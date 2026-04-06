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

# Load .env files early
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent / "services" / "ai" / ".env")
except ImportError:
    pass  # dotenv not installed, rely on environment variables

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
        ("services.filesystem.installed_apps_stub", "InstalledAppsStub"),
        ("services.filesystem.document_generator", "DocumentGenerator"),
        ("services.filesystem.media_stub", "MediaStubService"),
        ("services.filesystem.prefetch", "PrefetchService"),
        ("services.filesystem.thumbnail_cache", "ThumbnailCacheService"),
        ("services.filesystem.recent_items", "RecentItemsService"),
        ("services.filesystem.recycle_bin", "RecycleBinService"),
    ],
    "registry": [
        ("services.registry.hive_writer", "HiveWriter"),
        ("services.registry.installed_programs", "InstalledPrograms"),
        ("services.registry.mru_recentdocs", "MruRecentDocs"),
        ("services.registry.network_profiles", "NetworkProfiles"),
        ("services.registry.system_identity", "SystemIdentity"),
        ("services.registry.userassist", "UserAssist"),
    ],
    "browser": [
        ("services.browser.browser_profile", "BrowserProfileService"),
        ("services.browser.bookmarks", "BookmarksService"),
        ("services.browser.history", "BrowserHistoryService"),
        ("services.browser.cookies_cache", "CookiesCacheService"),
        ("services.browser.downloads", "BrowserDownloadService"),
    ],
    "applications": [
        ("services.applications.dev_environment", "DevEnvironment"),
        ("services.applications.office_artifacts", "OfficeArtifacts"),
        ("services.applications.email_client", "EmailClient"),
        ("services.applications.comms_apps", "CommsApps"),
    ],
    "eventlog": [
        ("services.eventlog.evtx_writer", "EvtxWriter"),
        ("services.eventlog.application_log", "ApplicationLog"),
        ("services.eventlog.security_log", "SecurityLog"),
        ("services.eventlog.system_log", "SystemLog"),
        ("services.eventlog.update_artifacts", "UpdateArtifacts"),
    ],
    "anti_fingerprint": [
        ("services.anti_fingerprint.hardware_normalizer", "HardwareNormalizer"),
        ("services.anti_fingerprint.process_faker", "ProcessFaker"),
        ("services.anti_fingerprint.vm_scrubber", "VmScrubber"),
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

    if args.override_username:
        merged["override_username"] = args.override_username

    if args.override_hostname:
        merged["override_hostname"] = args.override_hostname

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
    """Parse command-line arguments."""
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
        "--override-username",
        type=str,
        default=None,
        help="Force a specific Windows username (CRITICAL for matching existing VM users)",
    )

    parser.add_argument(
        "--override-hostname",
        type=str,
        default=None,
        help="Force a specific Windows computer name",
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

    # AI Generation options
    parser.add_argument(
        "--ai-generate",
        action="store_true",
        help="Use AI (Gemini) to generate personalized profile instead of static templates",
    )

    parser.add_argument(
        "--occupation",
        type=str,
        default=None,
        help="Occupation for AI profile generation (e.g., 'Software Engineer')",
    )

    parser.add_argument(
        "--interests",
        type=str,
        nargs="+",
        default=None,
        help="Interests/hobbies for AI profile generation",
    )

    parser.add_argument(
        "--location",
        type=str,
        default=None,
        help="Location hint for AI profile generation",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Interactive Wizard
# ---------------------------------------------------------------------------

def run_interactive_wizard(args: argparse.Namespace) -> argparse.Namespace:
    """Run an interactive console wizard to configure ARC."""
    print("=" * 60)
    print(" ARC - Artifact Reality Composer (Interactive Setup)")
    print("=" * 60)
    
    # 1. Profile
    print("\n[1/5] Select an Artifact Profile:")
    profiles = ["developer", "office_user", "home_user", "base"]
    for i, p in enumerate(profiles, 1):
        print(f"  {i}. {p}")
    while True:
        choice = input("Select profile (1-4) [1]: ").strip() or "1"
        if choice in ("1", "2", "3", "4"):
            args.profile = profiles[int(choice) - 1]
            break
        print("Invalid choice.")

    # 2. Target Mode
    print("\n[2/5] Select Target Environment:")
    print("  1. Infect a Dormant Windows VM (VHD/VHDX)")
    print("  2. Generate locally to a test folder")
    while True:
        choice = input("Select target (1-2) [1]: ").strip() or "1"
        if choice in ("1", "2"):
            is_vm = (choice == "1")
            break
        print("Invalid choice.")

    # 3. Path & Overrides
    if is_vm:
        print("\n[3/5] VM Details:")
        while True:
            vhdx = input("  Path to VHD/VHDX image file: ").strip()
            if vhdx and Path(vhdx).exists():
                args.vhdx_path = Path(vhdx)
                break
            print("  Error: File does not exist. Please enter a valid path.")
        
        print("\n  CRITICAL: To ensure the VM recognizes the artifacts,")
        print("  you must provide the EXACT existing Windows username.")
        while True:
            uname = input("  Target VM Username: ").strip()
            if uname:
                args.override_username = uname
                break
            print("  Username is required for VM injection.")
            
        hname = input("  Target VM Computer Name (optional, ENTER to randomize): ").strip()
        if hname:
            args.override_hostname = hname
    else:
        print("\n[3/5] Local Output:")
        out = input("  Output directory path [./output]: ").strip() or "./output"
        args.output = Path(out)
        
        print("\n(Optional) Override Identifiers - press ENTER to use random generator.")
        uname = input("  Force Username: ").strip()
        if uname: args.override_username = uname

    # 4. Timeline
    print("\n[4/6] Timeline:")
    while True:
        days = input("  Days of history to generate [90]: ").strip() or "90"
        if days.isdigit() and int(days) > 0:
            args.timeline_days = int(days)
            break
        print("Please enter a valid positive number.")

    # 5. Logging
    print("\n[5/6] Logging:")
    verbose_choice = input("  Enable verbose (debugging) logging? (y/N): ").strip().lower()
    if verbose_choice == 'y':
        args.verbose = True

    print("\n[6/6] Setup Complete!")
    print("=" * 60)
    print(f" Profile :   {args.profile}")
    print(f" Target  :   {'VM Image (' + str(args.vhdx_path) + ')' if is_vm else 'Local (' + str(args.output) + ')'}")
    print(f" Username:   {args.override_username or '<Randomly Generated>'}")
    print(f" Timeline:   {args.timeline_days} days")
    print(f" Logging :   {'Verbose' if getattr(args, 'verbose', False) else 'Standard'}")
    print("=" * 60)
    
    confirm = input("\nProceed with generation? (Y/n): ").strip().lower()
    if confirm == 'n':
        print("Aborted.")
        sys.exit(0)

    return args


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point for ARC.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    args = parse_args()
    
    # If no arguments provided at all (other than script name), run the wizard
    if len(sys.argv) == 1:
        args = run_interactive_wizard(args)
        
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger(__name__)
    logger.info("ARC - Artifact Reality Composer starting...")

    vm_manager = None
    
    try:
        # Load configuration
        logger.debug("Loading config from: %s", args.config)
        config = load_config(args.config)
        config = merge_cli_args(config, args)
        
        # ---------------------------------------------------------------------------
        # AI Profile Generation Mode
        # ---------------------------------------------------------------------------
        if getattr(args, 'ai_generate', False):
            logger.info("AI Profile Generation Mode enabled")
            
            occupation = getattr(args, 'occupation', None)
            if not occupation:
                logger.error("--occupation is required when using --ai-generate")
                print("\nError: --occupation is required for AI generation.")
                print("Example: python main.py --ai-generate --occupation 'Software Engineer'")
                return 2
            
            try:
                from services.ai.ai_orchestrator import AIOrchestrator
                
                # Create AI orchestrator
                ai_output_dir = Path(config.get("profiles_dir", "profiles/generated"))
                ai_orchestrator = AIOrchestrator.from_config(config, output_dir=ai_output_dir)
                
                print(f"\n{'='*60}")
                print(" AI Profile Generation")
                print(f"{'='*60}")
                print(f" Occupation: {occupation}")
                if args.location:
                    print(f" Location:   {args.location}")
                if args.interests:
                    print(f" Interests:  {', '.join(args.interests)}")
                print(f"{'='*60}\n")
                
                # Generate profile
                result = ai_orchestrator.generate_profile(
                    occupation=occupation,
                    location=getattr(args, 'location', None),
                    interests=getattr(args, 'interests', None),
                )
                
                if result.success:
                    print(f"\n✓ Generated persona: {result.persona.full_name}")
                    print(f"  Username:     {result.persona.username}")
                    print(f"  Organization: {result.persona.organization}")
                    
                    if result.profile_path:
                        print(f"\n✓ Profile saved to: {result.profile_path}")
                        
                        # Update config to use the generated profile
                        config["profile_path"] = str(result.profile_path)
                        
                        # Also override username if not already set
                        if not args.override_username:
                            config["override_username"] = result.persona.username
                    
                    if result.used_fallback:
                        print(f"\n  ⚠ Used fallback generators (API unavailable)")
                    
                    print(f"\n  Generation time: {result.generation_time_ms:.1f}ms")
                    print(f"\n{'='*60}")
                    print(" Continuing with artifact generation using AI profile...")
                    print(f"{'='*60}\n")
                else:
                    logger.error("AI profile generation failed: %s", result.errors)
                    print(f"\n✗ AI generation failed:")
                    for err in result.errors:
                        print(f"  - {err}")
                    return 1
                    
            except ImportError as e:
                logger.error("AI modules not available: %s", e)
                print(f"\nError: AI modules not available. Install dependencies:")
                print("  pip install google-generativeai pydantic jinja2")
                return 2
        
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
        def make_progress_callback():
            def callback(current_index: int, total_services: int, service_name: str):
                if total_services == 0:
                    return
                percentage = int(current_index / total_services * 100)
                bar_length = 30
                filled_length = int(bar_length * current_index // total_services)
                bar = '=' * filled_length + '-' * (bar_length - filled_length)
                sys.stdout.write(f"\r[{bar}] {percentage}% | {service_name.ljust(30)}")
                sys.stdout.flush()
                if current_index == total_services:
                    sys.stdout.write("\n")
            return callback

        print("\nStarting generation...")
        result = orchestrator.run(progress_callback=make_progress_callback())
        
        # Power up if everything succeeded and VM was provided
        if result.success and vm_manager and args.vm_name:
            vm_manager.dismount_vhdx()
            vm_manager.start_vm(args.vm_name)

        # Report results
        print("\n" + "=" * 60)
        print(" GENERATION SUMMARY")
        print("=" * 60)
        
        for svc_result in result.results:
            status = "PASS" if svc_result.success else "FAIL"
            time_str = f"{svc_result.duration_ms:.1f}ms"
            print(f"[{status}] {svc_result.service_name[:25].ljust(25)} | {time_str:>10}")
            if not svc_result.success and svc_result.error:
                print(f"       -> ERROR: {svc_result.error}")
                print(f"       -> TRACE: Check arc.log for full details.")

        print("=" * 60)

        if result.success:
            logger.info(
                "SUCCESS: Generated artifacts in %.2f seconds",
                result.total_duration_ms / 1000,
            )
            print(f"\nSUCCESS: All {result.services_executed} services completed in {result.total_duration_ms / 1000:.2f} seconds.")
            return 0
        else:
            logger.error(
                "FAILED: %d services failed",
                result.services_failed,
            )
            print(f"\nFAILED: {result.services_failed} out of {result.services_executed + result.services_failed} services failed.")
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
