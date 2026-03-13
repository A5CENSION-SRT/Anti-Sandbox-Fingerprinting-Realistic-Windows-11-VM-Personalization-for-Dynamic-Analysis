#!/usr/bin/env python3
"""ARC - Artifact Reality Composer.

Command-line entry point for generating realistic Windows filesystem
artifacts for analysis, research, and testing purposes.

Usage::

    # Basic execution with default config
    python main.py

    # Specify custom config file
    python main.py --config custom_config.yaml

    # Dry run mode (no files written)
    python main.py --dry-run

    # Specify profile directly
    python main.py --profile developer

    # Verbose output
    python main.py -v

Example::

    python main.py --config config.yaml --profile office_user --output ./vm_image
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from core.audit_logger import AuditLogger
from core.orchestrator import Orchestrator, OrchestrationError
from core.vm_manager import VMManager, VMManagerError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = "config.yaml"
_DEFAULT_OUTPUT = "./output"

# Service imports - lazy loaded to avoid circular imports
_SERVICE_MODULES = {
    "filesystem": [
        ("services.filesystem.user_directory", "UserDirectoryService"),
        ("services.filesystem.document_generator", "DocumentGenerator"),
        ("services.filesystem.media_stub", "MediaStubService"),
        ("services.filesystem.prefetch", "PrefetchService"),
        ("services.filesystem.thumbnail_cache", "ThumbnailCacheService"),
        ("services.filesystem.recent_items", "RecentItemsService"),
        ("services.filesystem.recycle_bin", "RecycleBinService"),
    ],
    "registry": [
        ("services.registry.hive_writer", "HiveWriter"),
        ("services.registry.installed_programs", "InstalledProgramsService"),
        ("services.registry.mru_recentdocs", "MRURecentDocsService"),
        ("services.registry.network_profiles", "NetworkProfilesService"),
        ("services.registry.system_identity", "SystemIdentityService"),
        ("services.registry.userassist", "UserAssistService"),
    ],
    "browser": [
        ("services.browser.browser_profile", "BrowserProfileService"),
        ("services.browser.bookmarks", "BookmarksService"),
        ("services.browser.history", "HistoryService"),
        ("services.browser.cookies_cache", "CookiesCacheService"),
        ("services.browser.downloads", "DownloadsService"),
    ],
    "applications": [
        ("services.applications.dev_environment", "DevEnvironmentService"),
        ("services.applications.office_artifacts", "OfficeArtifactsService"),
        ("services.applications.email_client", "EmailClientService"),
        ("services.applications.comms_apps", "CommsAppsService"),
    ],
    "eventlog": [
        ("services.eventlog.application_log", "ApplicationLogService"),
        ("services.eventlog.security_log", "SecurityLogService"),
        ("services.eventlog.system_log", "SystemLogService"),
        ("services.eventlog.update_artifacts", "UpdateArtifactsService"),
    ],
    "anti_fingerprint": [
        ("services.anti_fingerprint.hardware_normalizer", "HardwareNormalizer"),
        ("services.anti_fingerprint.process_faker", "ProcessFaker"),
        ("services.anti_fingerprint.vm_scrubber", "VMScrubber"),
    ],
}


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application.

    Args:
        verbose: If True, set DEBUG level; otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration Loading
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> Dict[str, Any]:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config.yaml file.

    Returns:
        Configuration dictionary.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        yaml.YAMLError: If config file is invalid.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config or {}


def merge_cli_args(config: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    """Merge CLI arguments into configuration.

    CLI arguments take precedence over config file values.

    Args:
        config: Configuration from file.
        args: Parsed CLI arguments.

    Returns:
        Merged configuration dictionary.
    """
    merged = config.copy()

    if args.output:
        merged["mount_path"] = str(args.output)

    if args.profile:
        merged["profile_name"] = args.profile

    if args.timeline_days:
        merged["timeline_days"] = args.timeline_days

    return merged


# ---------------------------------------------------------------------------
# Service Registration
# ---------------------------------------------------------------------------

def register_services(
    orchestrator: Orchestrator,
    categories: Optional[list] = None,
) -> int:
    """Dynamically import and register services with the orchestrator.

    Args:
        orchestrator: Orchestrator instance.
        categories: List of service categories to load. If None, load all.

    Returns:
        Number of services registered.
    """
    import importlib

    logger = logging.getLogger(__name__)
    registered = 0

    categories = categories or list(_SERVICE_MODULES.keys())

    for category in categories:
        if category not in _SERVICE_MODULES:
            logger.warning("Unknown service category: %s", category)
            continue

        for module_path, class_name in _SERVICE_MODULES[category]:
            try:
                module = importlib.import_module(module_path)
                service_class = getattr(module, class_name)
                orchestrator.register_service(service_class)
                registered += 1
                logger.debug("Registered: %s.%s", module_path, class_name)
            except ImportError as e:
                logger.warning(
                    "Could not import %s.%s: %s",
                    module_path, class_name, e,
                )
            except AttributeError as e:
                logger.warning(
                    "Class %s not found in %s: %s",
                    class_name, module_path, e,
                )
            except Exception as e:
                logger.error(
                    "Failed to register %s.%s: %s",
                    module_path, class_name, e,
                )

    return registered


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="arc",
        description="ARC - Artifact Reality Composer",
        epilog="Generate realistic Windows filesystem artifacts for analysis.",
    )

    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path(_DEFAULT_CONFIG),
        help=f"Path to configuration file (default: {_DEFAULT_CONFIG})",
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help=f"Output directory for generated artifacts (default: {_DEFAULT_OUTPUT})",
    )

    parser.add_argument(
        "-p", "--profile",
        type=str,
        choices=["base", "home_user", "office_user", "developer"],
        default=None,
        help="User profile type to generate",
    )

    parser.add_argument(
        "--timeline-days",
        type=int,
        default=None,
        help="Number of days of artifact history to generate (default: 90)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without writing files",
    )

    parser.add_argument(
        "--categories",
        type=str,
        nargs="+",
        choices=list(_SERVICE_MODULES.keys()),
        default=None,
        help="Service categories to execute (default: all)",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    parser.add_argument(
        "--vm-name",
        type=str,
        default=None,
        help="Target Hyper-V VM Name to stop and start automatically",
    )

    parser.add_argument(
        "--vhdx-path",
        type=Path,
        default=None,
        help="Path to Windows 11 VHDX to mount and infect",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point for ARC.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    args = parse_args()
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger(__name__)
    logger.info("ARC - Artifact Reality Composer starting...")

    vm_manager = None
    
    try:
        # Load configuration
        logger.debug("Loading config from: %s", args.config)
        config = load_config(args.config)
        config = merge_cli_args(config, args)
        
        # Determine if we are doing VM direct-injection
        if args.vhdx_path:
            logger.info("VM Direct Injection Mode enabled")
            vm_manager = VMManager(str(args.vhdx_path))
            
            # Optionally power down a linked VM first
            if args.vm_name:
                vm_manager.stop_vm(args.vm_name)
                
            # Mount and update Mount Path
            mounted_drive = vm_manager.mount_vhdx()
            config["mount_path"] = mounted_drive
            logger.info("Redirecting ARC output to %s", mounted_drive)

        # Initialize audit logger
        audit_path = Path(config.get("audit_log_path", "audit.log"))
        audit_logger = AuditLogger(audit_path)

        # Create and initialize orchestrator
        orchestrator = Orchestrator(
            config=config,
            audit_logger=audit_logger,
            dry_run=args.dry_run,
        )

        orchestrator.initialize()

        # Register services
        num_services = register_services(orchestrator, args.categories)
        logger.info("Registered %d services", num_services)

        if num_services == 0:
            logger.warning("No services registered. Nothing to do.")
            if vm_manager:
                vm_manager.dismount_vhdx()
            return 0

        # Execute orchestration
        result = orchestrator.run()
        
        # Power up if everything succeeded and VM was provided
        if result.success and vm_manager and args.vm_name:
            vm_manager.dismount_vhdx()
            vm_manager.start_vm(args.vm_name)

        # Report results
        if result.success:
            logger.info(
                "SUCCESS: Generated artifacts in %.2f seconds",
                result.total_duration_ms / 1000,
            )
            logger.info(
                "  Services executed: %d/%d",
                result.services_executed,
                result.services_executed + result.services_failed,
            )
            return 0
        else:
            logger.error(
                "FAILED: %d services failed",
                result.services_failed,
            )
            for svc_result in result.results:
                if not svc_result.success:
                    logger.error("  - %s: %s", svc_result.service_name, svc_result.error)
            return 1

    except FileNotFoundError as e:
        logger.error("Configuration error: %s", e)
        return 2

    except OrchestrationError as e:
        logger.error("Orchestration error: %s", e)
        return 3

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130

    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        return 1

    finally:
        if vm_manager:
            try:
                vm_manager.dismount_vhdx()
            except Exception as dismount_err:
                logger.error("Failed to dismount VHDX during cleanup: %s", dismount_err)
        if "orchestrator" in locals():
            orchestrator.cleanup()


if __name__ == "__main__":
    sys.exit(main())
