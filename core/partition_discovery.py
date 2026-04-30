"""Windows NTFS partition discovery — SAFE version.

Auto-discovery is RESTRICTED to loopback/virtual devices only.
Physical drives (/dev/sd*, /dev/nvme*) must be specified EXPLICITLY
via --partition AND --force-partition to prevent accidental writes
to real dual-boot Windows installations.

Background
----------
The previous version of this module silently discovered and mounted
the first NTFS partition found by lsblk.  On a dual-boot machine
this is the real Windows partition, and writing to it (especially
when Windows was hibernated or Fast Startup was active) corrupts
the BCD store and registry hives — producing error 0xc0000098.

Safe workflow
-------------
  # Option A — VM disk image (recommended for testing)
  qemu-img convert -f qcow2 -O raw windows11.qcow2 disk.img
  sudo losetup -f --show --partscan disk.img   # gives /dev/loop0
  python main.py --partition /dev/loop0p3

  # Option B — dual-boot real partition (explicit + confirmed)
  python main.py --partition /dev/nvme0n1p3 --force-partition
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Physical device prefixes that are BLOCKED from auto-discovery
_PHYSICAL_PREFIXES = ("sd", "hd", "nvme", "vd", "xvd", "mmcblk")


class PartitionDiscoveryError(Exception):
    """Raised when no suitable Windows partition is found."""


def find_windows_partition(*, allow_physical: bool = False) -> str:
    """Return the device path of a safe, unmounted NTFS partition.

    By default, ONLY loopback devices (/dev/loop*) are considered.
    Physical drives require ``allow_physical=True`` AND will be rejected
    here — callers must use the explicit ``--partition`` + ``--force-partition``
    path instead.

    Args:
        allow_physical: Reserved for internal use.  When False (default),
            physical drives are excluded from results.

    Returns:
        Device path string, e.g. ``"/dev/loop0p3"``.

    Raises:
        PartitionDiscoveryError: With a clear, actionable error message.
    """
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--output", "NAME,FSTYPE,MOUNTPOINT,SIZE,TYPE"],
            capture_output=True, text=True, check=True, timeout=15,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise PartitionDiscoveryError(f"lsblk failed: {exc}") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PartitionDiscoveryError(f"Could not parse lsblk output: {exc}") from exc

    all_ntfs: List[str] = []
    loop_ntfs: List[str] = []
    _walk(data.get("blockdevices", []), all_ntfs, loop_ntfs)

    # Physical drives found but auto-discovery blocked
    physical_found = [d for d in all_ntfs if d not in loop_ntfs]
    if physical_found and not allow_physical:
        phys_list = ", ".join(f"/dev/{d}" for d in physical_found)
        raise PartitionDiscoveryError(
            f"\n{'='*62}\n"
            f"  ⛔  AUTO-DISCOVERY BLOCKED — PHYSICAL DRIVES FOUND\n"
            f"{'='*62}\n"
            f"  Physical NTFS partitions detected: {phys_list}\n"
            f"\n"
            f"  Auto-discovery is DISABLED for physical drives to prevent\n"
            f"  accidental corruption of your dual-boot Windows installation.\n"
            f"\n"
            f"  ── Safe option (recommended for testing): ──────────────\n"
            f"  1. Get or create a Windows 11 disk image:\n"
            f"       qemu-img create -f raw disk.img 64G\n"
            f"       # or convert an existing QCOW2:\n"
            f"       qemu-img convert -f qcow2 -O raw win11.qcow2 disk.img\n"
            f"  2. Attach it as a loopback device:\n"
            f"       sudo losetup -f --show --partscan disk.img\n"
            f"  3. Run ARC:\n"
            f"       python main.py --partition /dev/loop0p3\n"
            f"\n"
            f"  ── Dual-boot option (advanced, requires confirmation): ──\n"
            f"  1. Fully shut down Windows (NOT hibernate / Fast Startup):\n"
            f"       Start → Power → Hold Shift → Shut down\n"
            f"  2. Run ARC with explicit device AND force flag:\n"
            f"       python main.py --partition {phys_list.split(',')[0].strip()} --force-partition\n"
            f"{'='*62}\n"
        )

    if not loop_ntfs:
        raise PartitionDiscoveryError(
            "No unmounted loopback NTFS partition found.\n"
            "Attach a disk image first:\n"
            "  sudo losetup -f --show --partscan disk.img\n"
            "Then retry, or use --partition /dev/loopX explicitly."
        )

    if len(loop_ntfs) > 1:
        logger.warning(
            "Multiple loopback NTFS partitions found: %s — using the first. "
            "Use --partition to choose explicitly.",
            ["/dev/" + d for d in loop_ntfs],
        )

    device = "/dev/" + loop_ntfs[0]
    logger.info("Auto-discovered loopback NTFS partition: %s", device)
    return device


def _walk(devices: list, all_ntfs: list, loop_ntfs: list) -> None:
    """Recursively walk lsblk device tree, separating physical from loopback."""
    for dev in devices:
        if dev.get("type") == "part" and dev.get("fstype") == "ntfs":
            if not dev.get("mountpoint"):
                name = dev["name"]
                all_ntfs.append(name)
                # Loopback partitions: loop0p1, loop0p2, etc.
                if name.startswith("loop"):
                    loop_ntfs.append(name)
        for child in dev.get("children", []):
            _walk([child], all_ntfs, loop_ntfs)
