"""ARC Safety Guard — pre-flight validation before any partition write.

This module is the single gatekeeper that MUST be called before
LinuxMountBackend.mount() is invoked.  It:

1. Classifies the target device (VM image loopback vs real physical drive).
2. Detects Windows hibernation / Fast Startup dirty state.
3. Checks for a live BCD store (strong signal of a real install).
4. Requires explicit --force-partition for physical drives.
5. Shows an interactive confirmation screen for dual-boot targets.

All decision logic is kept here so linux_mount.py stays thin I/O-only.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    """Classified information about the target block device."""
    device: str                          # e.g. /dev/loop0, /dev/nvme0n1p3
    is_loopback: bool = False            # /dev/loop* — always safe
    is_physical: bool = False            # /dev/sd*, /dev/nvme*, /dev/hd*
    is_virtual_disk: bool = False        # backing file is a .img/.qcow2/.vhd
    backing_file: Optional[str] = None  # loop backing file path if loopback
    size_bytes: int = 0
    mountpoints: List[str] = field(default_factory=list)
    is_currently_mounted: bool = False


@dataclass
class SafetyCheckResult:
    """Result of the full pre-flight safety check."""
    safe_to_proceed: bool
    device_info: Optional[DeviceInfo]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    hibernated: bool = False
    has_bcd: bool = False              # BCD store present → real Windows install
    requires_force: bool = False       # True if --force-partition is needed


# ---------------------------------------------------------------------------
# Device Classification
# ---------------------------------------------------------------------------

def classify_device(device: str) -> DeviceInfo:
    """Inspect a block device and classify it.

    Uses lsblk JSON output for reliable metadata.

    Args:
        device: Block device path, e.g. ``/dev/loop0`` or ``/dev/nvme0n1p3``.

    Returns:
        :class:`DeviceInfo` describing the device.
    """
    info = DeviceInfo(device=device)

    # Normalize: accept bare names like "loop0" or "nvme0n1p3"
    if not device.startswith("/dev/"):
        device = "/dev/" + device
    info.device = device

    dev_name = Path(device).name

    # Classify by name prefix
    if dev_name.startswith("loop"):
        info.is_loopback = True
    elif any(dev_name.startswith(p) for p in ("sd", "hd", "nvme", "vd", "xvd")):
        info.is_physical = True

    # Query lsblk for extra metadata
    try:
        result = subprocess.run(
            [
                "lsblk", "--json", "--bytes",
                "--output", "NAME,SIZE,MOUNTPOINT,TYPE,PKNAME",
                device,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            devices = data.get("blockdevices", [])
            if devices:
                dev = devices[0]
                info.size_bytes = int(dev.get("size", 0) or 0)
                mp = dev.get("mountpoint")
                if mp:
                    info.mountpoints = [mp] if isinstance(mp, str) else list(mp)
                    info.is_currently_mounted = bool(mp)
    except Exception as exc:
        logger.debug("lsblk metadata query failed for %s: %s", device, exc)

    # For loopback: find backing file via losetup
    if info.is_loopback:
        try:
            lo_result = subprocess.run(
                ["losetup", "--json", "--list", device],
                capture_output=True, text=True, timeout=10,
            )
            if lo_result.returncode == 0:
                lo_data = json.loads(lo_result.stdout)
                lo_devices = lo_data.get("loopdevices", [])
                if lo_devices:
                    bf = lo_devices[0].get("back-file")
                    if bf:
                        info.backing_file = bf
                        # Check if backing file is a recognisable VM disk format
                        ext = Path(bf).suffix.lower()
                        if ext in (".img", ".qcow2", ".vhd", ".vhdx", ".vmdk", ".raw", ".iso"):
                            info.is_virtual_disk = True
        except Exception as exc:
            logger.debug("losetup query failed for %s: %s", device, exc)

    return info


# ---------------------------------------------------------------------------
# Hibernation / Dirty Bit Detection
# ---------------------------------------------------------------------------

def _check_hibernation(device: str) -> bool:
    """Return True if the NTFS partition has the dirty bit set (hibernated/unclean).

    Uses ``ntfsinfo`` (part of ntfs-3g-tools) to read the volume flags without
    touching the filesystem.  Falls back to a read-only ntfsfix check.

    We deliberately do NOT call ``ntfsfix -d`` to clear the dirty bit —
    that would bypass Windows' safety mechanism.
    """
    # Try ntfsinfo first (non-destructive read)
    try:
        result = subprocess.run(
            ["sudo", "ntfsinfo", "--force", device],
            capture_output=True, text=True, timeout=15,
        )
        output = (result.stdout + result.stderr).lower()
        # ntfsinfo prints "volume is dirty" or "dirty: 1" when hibernated
        if "dirty" in output and ("1" in output or "yes" in output or "true" in output):
            return True
        # Explicit hibernation mention
        if "hibernat" in output:
            return True
    except FileNotFoundError:
        logger.debug("ntfsinfo not found, trying ntfsfix probe")
    except Exception as exc:
        logger.debug("ntfsinfo failed: %s", exc)

    # Fallback: ntfsfix without -d (just diagnose, don't fix)
    try:
        result = subprocess.run(
            ["sudo", "ntfsfix", "--no-action", device],
            capture_output=True, text=True, timeout=15,
        )
        output = (result.stdout + result.stderr).lower()
        if "hibernat" in output or "dirty" in output:
            return True
    except FileNotFoundError:
        logger.debug("ntfsfix not found, cannot check dirty bit")
    except Exception as exc:
        logger.debug("ntfsfix probe failed: %s", exc)

    return False


def _check_bcd(mount_point: Path) -> bool:
    """Return True if a Windows BCD store is found at the mount point.

    The BCD store lives at ``EFI\\Microsoft\\Boot\\BCD`` or
    ``Boot\\BCD`` (legacy BIOS).  Its presence strongly indicates
    a real Windows installation (not a blank VM disk).
    """
    bcd_paths = [
        mount_point / "EFI" / "Microsoft" / "Boot" / "BCD",
        mount_point / "Boot" / "BCD",
    ]
    for p in bcd_paths:
        if p.exists():
            return True
    return False


# ---------------------------------------------------------------------------
# Main Pre-Flight Check
# ---------------------------------------------------------------------------

def preflight_check(
    device: str,
    mount_point: Path,
    *,
    force_partition: bool = False,
    non_interactive: bool = False,
) -> SafetyCheckResult:
    """Run all safety checks before mounting a partition for write.

    Args:
        device: Block device path (e.g. ``/dev/loop0``, ``/dev/nvme0n1p3``).
        mount_point: Directory where the partition will be mounted.
        force_partition: If True, physical drives are allowed (still requires
            interactive confirmation unless ``non_interactive`` is also True).
        non_interactive: If True, skip the interactive confirmation prompt.
            Use only in CI/automated testing with loopback devices.

    Returns:
        :class:`SafetyCheckResult`.  Check ``.safe_to_proceed`` before mounting.
    """
    result = SafetyCheckResult(safe_to_proceed=False, device_info=None)

    # 1. Classify the device
    logger.info("Safety guard: classifying device %s …", device)
    try:
        dev_info = classify_device(device)
    except Exception as exc:
        result.errors.append(f"Could not classify device {device}: {exc}")
        return result
    result.device_info = dev_info

    # 2. Block auto-discovered physical drives without --force-partition
    if dev_info.is_physical and not force_partition:
        result.errors.append(
            f"\n{'='*60}\n"
            f"  ⛔  SAFETY BLOCK — PHYSICAL DRIVE DETECTED\n"
            f"{'='*60}\n"
            f"  Device : {device}\n"
            f"  This appears to be a REAL physical disk, NOT a VM image.\n"
            f"  Writing to it could corrupt your Windows installation.\n\n"
            f"  To proceed, you MUST explicitly pass:\n"
            f"    --force-partition   (acknowledge you want to write to a physical drive)\n"
            f"  AND also specify the device explicitly:\n"
            f"    --partition {device}\n\n"
            f"  For safe testing, use a VM disk image instead:\n"
            f"    sudo losetup -f --show your_disk.img   # attach image\n"
            f"    python main.py --partition /dev/loopX\n"
            f"{'='*60}\n"
        )
        result.requires_force = True
        return result

    # 3. Check for hibernation / dirty bit
    logger.info("Safety guard: checking NTFS dirty bit on %s …", device)
    try:
        is_dirty = _check_hibernation(device)
    except Exception as exc:
        result.warnings.append(f"Could not determine dirty bit state: {exc}. Proceeding cautiously.")
        is_dirty = False

    if is_dirty:
        result.hibernated = True
        result.errors.append(
            f"\n{'='*60}\n"
            f"  ⛔  SAFETY BLOCK — WINDOWS IS HIBERNATED / DIRTY\n"
            f"{'='*60}\n"
            f"  Device : {device}\n"
            f"  The NTFS dirty bit is SET, meaning Windows did not shut down\n"
            f"  cleanly (hibernation, Fast Startup, or a crash).\n\n"
            f"  Writing to a hibernated partition CORRUPTS it.\n"
            f"  This is exactly what happened to your dual-boot system.\n\n"
            f"  FIX: Boot Windows normally, then FULLY SHUT DOWN:\n"
            f"    Start → Power → Hold Shift → Click 'Shut down'\n"
            f"  Then retry ARC from Linux.\n\n"
            f"  Do NOT use: ntfsfix -d   (it clears the flag but doesn't fix the data)\n"
            f"{'='*60}\n"
        )
        return result

    # 4. Warn if a BCD store is found (this is a real Windows install, not blank VM)
    try:
        # Try a read-only probe mount to check for BCD
        probe_mp = mount_point / ".arc_probe"
        probe_mp.mkdir(parents=True, exist_ok=True)
        probe = subprocess.run(
            ["sudo", "mount", "-t", "ntfs-3g", "-o", "ro", device, str(probe_mp)],
            capture_output=True, text=True, timeout=20,
        )
        if probe.returncode == 0:
            try:
                has_bcd = _check_bcd(probe_mp)
                result.has_bcd = has_bcd
            finally:
                subprocess.run(
                    ["sudo", "umount", str(probe_mp)],
                    capture_output=True, timeout=10,
                )
            try:
                probe_mp.rmdir()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("BCD probe mount failed: %s", exc)

    if result.has_bcd:
        result.warnings.append(
            f"BCD store found on {device} — this appears to be a REAL Windows installation.\n"
            f"Make sure Windows is fully shut down (no Fast Startup) before proceeding."
        )

    # 5. Interactive confirmation for physical drives (even with --force-partition)
    if dev_info.is_physical and force_partition and not non_interactive:
        print(f"\n{'='*60}")
        print(f"  ⚠️   DUAL-BOOT WRITE CONFIRMATION")
        print(f"{'='*60}")
        print(f"  Device     : {device}")
        print(f"  Size       : {dev_info.size_bytes // (1024**3):.1f} GB")
        print(f"  BCD found  : {'YES — real Windows install' if result.has_bcd else 'No'}")
        print(f"  Hibernated : {'NO (safe)' if not result.hibernated else 'YES (DANGER!)'}")
        if result.warnings:
            print(f"\n  Warnings:")
            for w in result.warnings:
                print(f"    ⚠  {w}")
        print(f"\n  ARC will write artifact files to the Windows NTFS partition.")
        print(f"  This CANNOT be undone automatically.")
        print(f"{'='*60}")
        answer = input("\n  Type 'YES' in capitals to confirm: ").strip()
        if answer != "YES":
            result.errors.append("User did not confirm. Aborting.")
            return result

    # 6. All checks passed
    result.safe_to_proceed = True
    logger.info(
        "Safety guard: all checks passed for %s (loopback=%s, physical=%s, hibernated=%s, bcd=%s)",
        device, dev_info.is_loopback, dev_info.is_physical,
        result.hibernated, result.has_bcd,
    )
    return result


# ---------------------------------------------------------------------------
# VM Image attachment helper
# ---------------------------------------------------------------------------

def attach_image_as_loopback(image_path: Path, partition_index: int = 1) -> str:
    """Attach a disk image file as a loopback device and return the partition device.

    Args:
        image_path: Path to ``.img``, ``.raw``, or ``.qcow2`` file.
            QCOW2 must be converted to raw first (use ``qemu-img convert``).
        partition_index: Partition number inside the image (1-based).

    Returns:
        Block device path, e.g. ``/dev/loop0p3``.

    Raises:
        RuntimeError: If attachment fails.
    """
    if not image_path.exists():
        raise RuntimeError(f"Image file not found: {image_path}")

    ext = image_path.suffix.lower()
    if ext == ".qcow2":
        raise RuntimeError(
            f"QCOW2 images cannot be directly losetup-attached.\n"
            f"Convert first:  qemu-img convert -f qcow2 -O raw {image_path} disk.img\n"
            f"Then use:       python main.py --image disk.img"
        )

    # losetup --partscan automatically creates /dev/loopXpN partition devices
    result = subprocess.run(
        ["sudo", "losetup", "--find", "--show", "--partscan", str(image_path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"losetup failed: {result.stderr.strip()}\n"
            f"Ensure you have: sudo apt install util-linux"
        )

    loop_dev = result.stdout.strip()   # e.g. /dev/loop0
    if not loop_dev:
        raise RuntimeError("losetup returned empty device path")

    partition_dev = f"{loop_dev}p{partition_index}"

    # Give udev a moment to create partition devices
    import time
    time.sleep(0.5)

    if not Path(partition_dev).exists():
        # Try without partition suffix (whole-disk image)
        if Path(loop_dev).exists():
            logger.warning(
                "Partition device %s not found — using whole-disk device %s",
                partition_dev, loop_dev,
            )
            partition_dev = loop_dev

    logger.info("Attached %s as %s (partition %s)", image_path, loop_dev, partition_dev)
    return partition_dev


def detach_loopback(loop_device: str) -> None:
    """Detach a loopback device created by :func:`attach_image_as_loopback`.

    Args:
        loop_device: Device path returned by :func:`attach_image_as_loopback`,
            e.g. ``/dev/loop0p3`` or ``/dev/loop0``.
    """
    # Normalise to the loop device (strip partition suffix)
    base = loop_device
    if "p" in Path(loop_device).name and not loop_device.endswith("loop0"):
        # e.g. /dev/loop0p3 → /dev/loop0
        import re
        base = re.sub(r"p\d+$", "", loop_device)

    result = subprocess.run(
        ["sudo", "losetup", "--detach", base],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        logger.warning("losetup --detach %s failed: %s", base, result.stderr.strip())
    else:
        logger.info("Detached loopback device %s", base)
