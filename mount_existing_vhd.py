#!/usr/bin/env python3
"""Mount an existing Windows VHD/VHDX and keep it attached.

This helper is intentionally small: it reuses :class:`core.vm_manager.VMManager`
to mount an existing offline image, prints the assigned drive letter, and exits
without dismounting so the mounted volume can be inspected in File Explorer.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
from pathlib import Path

from core.vm_manager import VMManager, VMManagerError


logger = logging.getLogger("mount_existing_vhd")


def is_admin() -> bool:
    """Return True when the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_and_rerun() -> None:
    """Re-launch this script with elevated privileges via UAC."""
    script = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    logger.info("Requesting Administrator privileges...")
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        f'"{script}" {params}',
        None,
        1,
    )
    sys.exit(0)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    project_root = Path(__file__).resolve().parent
    default_image = project_root / "images" / "arc_home_user_build.vhd"

    parser = argparse.ArgumentParser(
        prog="mount_existing_vhd",
        description="Mount an existing VHD/VHDX and keep it attached.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=default_image,
        help=f"Path to the VHD/VHDX image (default: {default_image})",
    )
    return parser.parse_args()


def main() -> int:
    """Mount the requested image and leave it attached."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)-22s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args = parse_args()
    image_path = args.image.resolve()

    if not image_path.exists():
        logger.error("Image not found: %s", image_path)
        return 2

    if not is_admin():
        elevate_and_rerun()
        return 0

    try:
        vm_manager = VMManager(str(image_path))
        drive = vm_manager.mount_vhdx()
        logger.info("Mounted image at %s", drive)
        print(drive)
        return 0
    except (FileNotFoundError, VMManagerError) as exc:
        logger.exception("Failed to mount image: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())