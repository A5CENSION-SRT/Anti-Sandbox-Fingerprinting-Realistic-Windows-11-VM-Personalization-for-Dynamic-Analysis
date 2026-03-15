#!/usr/bin/env python3
"""Build a populated VM disk image (VHD/VHDX).

Creates a fresh VHD, formats it with NTFS, then delegates to main.py
(which already registers ALL services and uses the Orchestrator) to
populate it with realistic Windows artifacts.

⚠  REQUIRES: Administrator privileges (diskpart needs elevation).

Usage:
    python build_vm_image.py
    python build_vm_image.py -p developer
    python build_vm_image.py -p home_user -o custom.vhd --size 2048
"""

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("build_vm_image")


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_and_rerun():
    """Re-launch this script with UAC elevation, preserving all args."""
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
    script_content = "\n".join(commands) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="diskpart_"
    ) as f:
        f.write(script_content)
        script_path = f.name

    logger.debug("diskpart script:\n%s", script_content)
    try:
        result = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"diskpart failed: {result.stderr}")
        return result.stdout
    finally:
        os.unlink(script_path)


def find_free_drive_letter() -> str:
    import string
    used = {letter for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")}
    for letter in reversed(string.ascii_uppercase[3:]):
        if letter not in used:
            return letter
    raise RuntimeError("No free drive letters")


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
    """Create VHD → format NTFS → run main.py to populate → detach → save."""

    vhd_path = Path(output_path).resolve()
    if vhd_path.suffix.lower() not in (".vhd", ".vhdx"):
        vhd_path = vhd_path.with_suffix(".vhd")

    project_root = Path(__file__).resolve().parent
    drive_letter = find_free_drive_letter()

    logger.info("=" * 60)
    logger.info("Arc VM Image Builder")
    logger.info("=" * 60)
    logger.info("Output     : %s", vhd_path)
    logger.info("Profile    : %s", profile)
    logger.info("VHD size   : %d MB", size_mb)
    logger.info("Drive      : %s:\\", drive_letter)
    logger.info("=" * 60)

    try:
        # ── Step 1: Create and mount VHD ────────────────────────────
        logger.info("Step 1/3: Creating VHD (%d MB)...", size_mb)

        if vhd_path.exists():
            logger.warning("Removing existing VHD: %s", vhd_path)
            vhd_path.unlink()

        run_diskpart([
            f'create vdisk file="{vhd_path}" maximum={size_mb} type=expandable',
            f'select vdisk file="{vhd_path}"',
            "attach vdisk",
            "create partition primary",
            'format fs=ntfs label="ArcImage" quick',
            f"assign letter={drive_letter}",
        ])

        mount_root = Path(f"{drive_letter}:\\")
        for i in range(30):
            if mount_root.exists():
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Drive {drive_letter}:\\ not ready after 30s")

        logger.info("Drive %s:\\ ready", drive_letter)

        # ── Step 2: Run main.py (calls ALL services) ───────────────
        logger.info("Step 2/3: Running Arc pipeline (all services)...")

        cmd = [
            sys.executable, str(project_root / "main.py"),
            "--output", str(mount_root),
            "--profile", profile,
            "--timeline-days", str(timeline_days),
        ]
        if verbose:
            cmd.append("--verbose")

        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            timeout=300,
        )

        if result.returncode != 0:
            logger.warning("main.py exited with code %d (some services may have failed)", result.returncode)
        else:
            logger.info("Arc pipeline completed successfully")

    finally:
        # ── Step 3: Detach VHD ──────────────────────────────────────
        logger.info("Step 3/3: Detaching VHD...")
        try:
            run_diskpart([
                f'select vdisk file="{vhd_path}"',
                "detach vdisk",
            ])
            logger.info("VHD detached")
        except Exception as exc:
            logger.error("Failed to detach: %s", exc)

    # ── Report ──────────────────────────────────────────────────
    if vhd_path.exists():
        size = vhd_path.stat().st_size
        logger.info("=" * 60)
        logger.info("VM image created: %s (%.1f MB)", vhd_path.name, size / (1024 * 1024))
        logger.info("=" * 60)
    else:
        logger.error("VHD not found: %s", vhd_path)

    return vhd_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="build_vm_image",
        description="Build a populated VHD disk image with Arc artifacts.",
    )
    parser.add_argument("--output", "-o", default=None,
                        help="Output VHD path (default: images/arc_{profile}.vhd)")
    parser.add_argument("--profile", "-p", default="office_user",
                        choices=["office_user", "developer", "home_user"],
                        help="Profile persona (default: office_user)")
    parser.add_argument("--size", "-s", type=int, default=1024,
                        help="VHD max size in MB (default: 1024, dynamic expansion)")
    parser.add_argument("--timeline-days", type=int, default=90,
                        help="Days of history (default: 90)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug logging")

    args = parser.parse_args()

    # Resolve default output path
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
        logger.warning("Requesting Administrator privileges (diskpart needs elevation)...")
        elevate_and_rerun()
        return 0

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
