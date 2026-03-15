#!/usr/bin/env python3
"""Build a populated VM disk image (VHD).

Creates a VHD file, mounts it via diskpart, populates it with Arc
artifacts for a given profile, then detaches it.  The resulting VHD
can be attached to Hyper-V, VirtualBox, or VMware as a secondary disk.

⚠  REQUIRES: Administrator privileges (diskpart needs elevation).

Usage:
    python build_vm_image.py --output arc_image.vhd --profile office_user
    python build_vm_image.py -o arc_image.vhd -p developer --size 2048 --days 90
"""

import argparse
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("build_vm_image")

# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """Check if the current process has Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_and_rerun():
    """Re-launch this script with UAC elevation."""
    script = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    logger.info("Requesting Administrator privileges...")
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# Diskpart helpers
# ---------------------------------------------------------------------------

def run_diskpart(commands: list[str]) -> str:
    """Write commands to a temp script and run diskpart /s."""
    script_content = "\n".join(commands) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="diskpart_"
    ) as f:
        f.write(script_content)
        script_path = f.name

    logger.debug("diskpart script (%s):\n%s", script_path, script_content)
    try:
        result = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True, text=True, timeout=120,
        )
        logger.debug("diskpart stdout:\n%s", result.stdout)
        if result.returncode != 0:
            logger.error("diskpart stderr:\n%s", result.stderr)
            raise RuntimeError(f"diskpart failed (exit {result.returncode}): {result.stderr}")
        return result.stdout
    finally:
        os.unlink(script_path)


def find_free_drive_letter() -> str:
    """Find an unused drive letter (Z → D)."""
    import string
    used = set()
    for letter in string.ascii_uppercase:
        if os.path.exists(f"{letter}:\\"):
            used.add(letter)
    # Try from Z backwards to avoid common letters
    for letter in reversed(string.ascii_uppercase[3:]):  # D onwards
        if letter not in used:
            return letter
    raise RuntimeError("No free drive letters available")


# ---------------------------------------------------------------------------
# Windows directory skeleton
# ---------------------------------------------------------------------------

def create_windows_skeleton(mount_root: Path, username: str):
    """Create a minimal Windows-like directory tree.

    This gives the mounted VHD the appearance of a real Windows
    installation, providing the paths that Arc services expect.
    """
    dirs = [
        # Windows system directories
        "Windows/System32/config",
        "Windows/System32/drivers/etc",
        "Windows/System32/winevt/Logs",
        "Windows/Prefetch",
        "Windows/Temp",
        "Windows/SoftwareDistribution/Download",
        "Windows/Logs/CBS",
        # ProgramData
        "ProgramData/Microsoft/Windows/Start Menu/Programs",
        "ProgramData/Microsoft/Windows/Start Menu/Programs/Startup",
        # Program Files
        "Program Files/Common Files",
        "Program Files/Google/Chrome/Application",
        "Program Files/Microsoft Office/root/Office16",
        "Program Files/VideoLAN/VLC",
        "Program Files (x86)/Common Files",
        # User profile
        f"Users/{username}",
        f"Users/{username}/Desktop",
        f"Users/{username}/Documents",
        f"Users/{username}/Downloads",
        f"Users/{username}/Music",
        f"Users/{username}/Pictures",
        f"Users/{username}/Videos",
        f"Users/{username}/AppData/Local",
        f"Users/{username}/AppData/Local/Temp",
        f"Users/{username}/AppData/Local/Microsoft",
        f"Users/{username}/AppData/Roaming/Microsoft/Windows/Recent",
        f"Users/{username}/AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
        # Recycle Bin placeholder
        "$Recycle.Bin",
    ]
    for d in dirs:
        (mount_root / d).mkdir(parents=True, exist_ok=True)

    # Create some basic system files
    hosts_file = mount_root / "Windows/System32/drivers/etc/hosts"
    if not hosts_file.exists():
        hosts_file.write_text(
            "# Copyright (c) 1993-2009 Microsoft Corp.\n"
            "#\n"
            "# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.\n"
            "#\n"
            "# localhost name resolution is handled within DNS itself.\n"
            "#\n"
            "127.0.0.1       localhost\n"
            "::1             localhost\n",
            encoding="utf-8",
        )

    # Create a desktop.ini in user's Desktop (like Windows does)
    desktop_ini = mount_root / f"Users/{username}/Desktop/desktop.ini"
    if not desktop_ini.exists():
        desktop_ini.write_text(
            "[.ShellClassInfo]\n"
            "LocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-21769\n",
            encoding="utf-8",
        )

    logger.info("Windows directory skeleton created (%d directories)", len(dirs))


# ---------------------------------------------------------------------------
# Main build pipeline
# ---------------------------------------------------------------------------

def build_image(
    output_path: str,
    profile: str = "office_user",
    size_mb: int = 1024,
    timeline_days: int = 90,
    verbose: bool = False,
) -> Path:
    """Create a VHD, mount it, populate with Arc, detach.

    Args:
        output_path: Where to save the final .vhd file.
        profile: Arc profile persona.
        size_mb: VHD size in megabytes.
        timeline_days: Days of artifact history.
        verbose: Enable debug logging.

    Returns:
        Path to the created VHD file.
    """
    vhd_path = Path(output_path).resolve()
    if vhd_path.suffix.lower() not in (".vhd", ".vhdx"):
        vhd_path = vhd_path.with_suffix(".vhd")

    project_root = Path(__file__).resolve().parent
    drive_letter = find_free_drive_letter()
    mount_root = Path(f"{drive_letter}:\\")

    logger.info("=" * 60)
    logger.info("Arc VM Image Builder")
    logger.info("=" * 60)
    logger.info("Output     : %s", vhd_path)
    logger.info("Profile    : %s", profile)
    logger.info("VHD size   : %d MB", size_mb)
    logger.info("Timeline   : %d days", timeline_days)
    logger.info("Drive      : %s:\\", drive_letter)
    logger.info("=" * 60)

    try:
        # ── Step 1: Create and mount VHD ────────────────────────────
        logger.info("Step 1/5: Creating VHD (%d MB)...", size_mb)

        # Remove existing VHD if present
        if vhd_path.exists():
            logger.warning("Removing existing VHD: %s", vhd_path)
            vhd_path.unlink()

        run_diskpart([
            f"create vdisk file=\"{vhd_path}\" maximum={size_mb} type=expandable",
            f"select vdisk file=\"{vhd_path}\"",
            "attach vdisk",
            "create partition primary",
            "format fs=ntfs label=\"ArcImage\" quick",
            f"assign letter={drive_letter}",
        ])

        # Wait for drive to be ready
        logger.info("Step 2/5: Waiting for drive %s:\\ ...", drive_letter)
        for i in range(30):
            if mount_root.exists():
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Drive {drive_letter}:\\ not ready after 30 seconds")

        logger.info("Drive %s:\\ is ready", drive_letter)

        # ── Step 2: Predict the username Arc will generate ──────────
        logger.info("Step 3/5: Pre-generating identity to build skeleton...")

        # We need to know the username Arc will use so we can create
        # the skeleton before Arc runs.
        sys.path.insert(0, str(project_root))
        from core.profile_engine import ProfileEngine
        from core.identity_generator import IdentityGenerator

        engine = ProfileEngine(project_root / "profiles")
        profile_ctx = engine.load_profile(profile)
        id_gen = IdentityGenerator(profile_ctx, project_root / "data")
        identity = id_gen.generate()
        username = identity.user.username

        logger.info("Generated identity: %s (%s)", identity.user.full_name, username)

        # ── Step 3: Create Windows skeleton ─────────────────────────
        logger.info("Step 4/5: Creating Windows directory skeleton...")
        create_windows_skeleton(mount_root, username)

        # ── Step 4: Run Arc ─────────────────────────────────────────
        logger.info("Step 5/5: Running Arc against %s:\\...", drive_letter)

        from core.orchestrator import Orchestrator

        orch = Orchestrator(
            mount_root=str(mount_root),
            profile_name=profile,
            timeline_days=timeline_days,
            project_root=project_root,
        )
        result = orch.run()

        logger.info(
            "Arc completed: %d services, %d audit entries",
            result.get("services_run", 0),
            result.get("audit_entries", 0),
        )

    finally:
        # ── Step 5: Detach VHD ──────────────────────────────────────
        logger.info("Detaching VHD...")
        try:
            run_diskpart([
                f"select vdisk file=\"{vhd_path}\"",
                "detach vdisk",
            ])
            logger.info("VHD detached successfully")
        except Exception as exc:
            logger.error("Failed to detach VHD: %s", exc)

    # ── Report ──────────────────────────────────────────────────
    if vhd_path.exists():
        size = vhd_path.stat().st_size
        logger.info("=" * 60)
        logger.info("✓ VM image created successfully!")
        logger.info("  File: %s", vhd_path)
        logger.info("  Size: %.1f MB", size / (1024 * 1024))
        logger.info("")
        logger.info("To use this image:")
        logger.info("  Hyper-V   : Settings → Hard Drive → Browse → %s", vhd_path.name)
        logger.info("  VirtualBox: Settings → Storage → Add Hard Disk → %s", vhd_path.name)
        logger.info("  VMware    : Convert with: qemu-img convert -f vpc -O vmdk %s image.vmdk", vhd_path.name)
        logger.info("=" * 60)
    else:
        logger.error("VHD file not found at %s", vhd_path)

    return vhd_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="build_vm_image",
        description="Build a populated VHD disk image with Arc artifacts.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output VHD file path (default: images/arc_{profile}.vhd).",
    )
    parser.add_argument(
        "--profile", "-p",
        default="office_user",
        choices=["office_user", "developer", "home_user"],
        help="Profile persona (default: office_user).",
    )
    parser.add_argument(
        "--size", "-s",
        type=int,
        default=1024,
        help="VHD maximum size in MB (default: 1024). Uses dynamic expansion.",
    )
    parser.add_argument(
        "--timeline-days",
        type=int,
        default=90,
        help="Days of artifact history (default: 90).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    # ── Resolve default output path ─────────────────────────────
    project_root = Path(__file__).resolve().parent
    if args.output is None:
        images_dir = project_root / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(images_dir / f"arc_{args.profile}.vhd")

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not is_admin():
        logger.warning("This script requires Administrator privileges.")
        logger.warning("Requesting UAC elevation...")
        elevate_and_rerun()
        return 0  # Won't reach here

    try:
        build_image(
            output_path=args.output,
            profile=args.profile,
            size_mb=args.size,
            timeline_days=args.timeline_days,
            verbose=args.verbose,
        )
        return 0
    except Exception as exc:
        logger.exception("Build failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
