#!/usr/bin/env python3
"""Arc — Anti-Sandbox Personalizer CLI.

Usage:
    python main.py --mount <path> [--profile <name>] [--timeline-days N] [--dry-run] [-v]

Examples:
    python main.py --mount D:\\mounted_image --profile office_user
    python main.py -m ./test_mount -p developer --timeline-days 30 -v
    python main.py -m ./test_mount --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="arc",
        description="Arc — Anti-Sandbox Personalizer: populates a mounted "
                    "disk image with realistic Windows forensic artifacts.",
    )
    parser.add_argument(
        "--mount", "-m",
        required=True,
        help="Path to the mount root (target directory or drive letter).",
    )
    parser.add_argument(
        "--profile", "-p",
        default="office_user",
        choices=["office_user", "developer", "home_user"],
        help="Profile persona to apply (default: office_user).",
    )
    parser.add_argument(
        "--timeline-days",
        type=int,
        default=90,
        help="Number of days of history to generate (default: 90).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned operations without writing any files.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug-level logging.",
    )

    args = parser.parse_args()

    # ── Logging setup ───────────────────────────────────────────
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Validate mount path ─────────────────────────────────────
    mount_path = Path(args.mount)
    if not mount_path.exists():
        logging.info("Mount path does not exist, creating: %s", mount_path)
        mount_path.mkdir(parents=True, exist_ok=True)

    # ── Run orchestrator ────────────────────────────────────────
    # Add the project root to sys.path so imports work from anywhere
    project_root = Path(__file__).resolve().parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from core.orchestrator import Orchestrator

    try:
        orch = Orchestrator(
            mount_root=str(mount_path),
            profile_name=args.profile,
            timeline_days=args.timeline_days,
            dry_run=args.dry_run,
            project_root=project_root,
        )
        result = orch.run()

        if result.get("dry_run"):
            logging.getLogger(__name__).info("Dry run completed.")
        else:
            logging.getLogger(__name__).info(
                "Done — %d services, %d audit entries.",
                result.get("services_run", 0),
                result.get("audit_entries", 0),
            )
        return 0

    except FileNotFoundError as exc:
        logging.getLogger(__name__).error("File not found: %s", exc)
        return 1
    except Exception as exc:
        logging.getLogger(__name__).exception("Fatal error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
