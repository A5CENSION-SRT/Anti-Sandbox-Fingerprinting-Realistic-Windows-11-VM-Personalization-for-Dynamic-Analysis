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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = "config.yaml"
_DEFAULT_OUTPUT = "./output"

# Service imports - lazy loaded to avoid circular imports
_SERVICE_MODULES = {
    "expansion": [
        ("services.expansion.orchestrator", "ExpansionOrchestrator"),
    ],
    "filesystem": [
        ("services.filesystem.user_directory", "UserDirectoryService"),
        ("services.filesystem.installed_apps_stub", "InstalledAppsStub"),
        ("services.filesystem.document_generator", "DocumentGenerator"),
        ("services.filesystem.media_stub", "MediaStubService"),
        ("services.filesystem.powershell_history", "PowerShellHistoryService"),
        ("services.filesystem.cdp_logs", "CdpLogsService"),
        ("services.filesystem.prefetch", "PrefetchService"),
        ("services.filesystem.thumbnail_cache", "ThumbnailCacheService"),
        ("services.filesystem.recent_items", "RecentItemsService"),
        ("services.filesystem.recycle_bin", "RecycleBinService"),
    ],
    "registry": [
        ("services.registry.hive_writer", "HiveWriter"),
        ("services.filesystem.office_mru", "OfficeMruService"),
        ("services.registry.installed_programs", "InstalledPrograms"),
        ("services.registry.mru_recentdocs", "MruRecentDocs"),
        ("services.registry.network_profiles", "NetworkProfiles"),
        ("services.registry.system_identity", "SystemIdentity"),
        ("services.registry.userassist", "UserAssist"),
        ("services.registry.typing_history", "TypingHistory"),
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
        ("services.anti_fingerprint.mac_hygiene", "MacHygiene"),
    ],
    "ntfs": [
        ("services.ntfs.mft_timestamp_patcher", "MftTimestampPatcher"),
        ("services.ntfs.usn_journal_writer", "UsnJournalWriter"),
        ("services.ntfs.logfile_writer", "LogfileWriter"),
    ],
    "evaluation": [
        ("services.filesystem.system_content_populator", "SystemContentPopulator"),
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

    # --preset overrides an explicit --profile
    if getattr(args, "preset", None):
        preset_path = Path("profiles") / "presets" / f"{args.preset}.yaml"
        merged["profile_path"] = str(preset_path)
        merged["profiles_dir"] = "profiles/presets"
    elif args.profile:
        merged["profile_path"] = str(args.profile)

    if args.timeline_days:
        merged["timeline_days"] = args.timeline_days

    if getattr(args, "random_seed", None) is not None:
        merged["random_seed"] = args.random_seed

    if args.override_username:
        merged["override_username"] = args.override_username

    if args.override_hostname:
        merged["override_hostname"] = args.override_hostname

    if getattr(args, "skip_anti_fingerprint", False):
        merged["skip_anti_fingerprint"] = True

    return merged


# ---------------------------------------------------------------------------
# Service Registration
# ---------------------------------------------------------------------------

def register_services(
    orchestrator: Orchestrator,
    categories: Optional[list] = None,
    config: Optional[Dict[str, Any]] = None,
) -> int:
    """Dynamically import and register services with the orchestrator.

    Args:
        orchestrator: Orchestrator instance.
        categories: List of service categories to load. If None, load all.
        config: Merged configuration dict (used to check feature flags).

    Returns:
        Number of services registered.
    """
    import importlib

    logger = logging.getLogger(__name__)
    registered = 0
    config = config or {}

    categories = categories or list(_SERVICE_MODULES.keys())

    if config.get("skip_anti_fingerprint"):
        categories = [c for c in categories if c != "anti_fingerprint"]

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
        type=Path,
        default=None,
        help="Path to an explicit persona YAML file",
    )

    parser.add_argument(
        "--preset",
        type=str,
        choices=["developer", "office_user", "home_user"],
        default=None,
        help="Load a built-in preset persona (profiles/presets/)",
    )

    parser.add_argument(
        "--timeline-days",
        type=int,
        default=None,
        help="Number of days of artifact history to generate (default: 360)",
    )

    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Fixed RNG seed for byte-identical reproducibility (ADR-012)",
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
        "--skip-anti-fingerprint",
        action="store_true",
        help="Skip VM anti-fingerprint registry scrubbing (for baseline testing)",
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
        help="Service categories to execute (default: all); 'ntfs' requires ntfs-3g FUSE mount",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    parser.add_argument(
        "--partition",
        type=str,
        default=None,
        metavar="DEVICE",
        help=(
            "Block device of the NTFS partition to inject into, "
            "e.g. /dev/loop0p3 (VM image) or /dev/nvme0n1p3 (dual-boot, requires --force-partition).  "
            "Overrides config.yaml::windows_partition."
        ),
    )

    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Path to a raw Windows disk image file (.img / .raw) to inject into.  "
            "ARC will attach it via losetup automatically.  "
            "For QCOW2: convert first with 'qemu-img convert -f qcow2 -O raw in.qcow2 out.img'.  "
            "This is the RECOMMENDED safe mode for testing."
        ),
    )

    parser.add_argument(
        "--image-partition",
        type=int,
        default=3,
        metavar="N",
        help="Partition number inside the disk image to use (default: 3, typical Windows data partition).",
    )

    parser.add_argument(
        "--force-partition",
        action="store_true",
        help=(
            "Allow writing to a PHYSICAL block device (dual-boot mode).  "
            "REQUIRED when --partition points to a real drive (/dev/sd*, /dev/nvme*).  "
            "Without this flag, ARC will refuse to touch physical drives.  "
            "You will be asked for explicit confirmation before any writes."
        ),
    )

    parser.add_argument(
        "--mount-point",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Host directory to mount the Windows partition or image at "
            "(default: /mnt/arc_windows or config.yaml::windows_mount_point)."
        ),
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

    # 1. Persona via AI
    print("\n[1/4] Persona (AI-generated via Gemini):")
    while True:
        occ = input("  Occupation [Software Engineer]: ").strip() or "Software Engineer"
        if occ:
            args.occupation = occ
            args.ai_generate = True
            break

    loc = input("  Location [United States]: ").strip() or "United States"
    args.location = loc

    interests_raw = input("  Interests, comma-separated [coding,gaming]: ").strip() or "coding,gaming"
    args.interests = [i.strip() for i in interests_raw.split(",")]

    # 2. Output
    print("\n[2/4] Output directory:")
    out = input("  Output path [./output]: ").strip() or "./output"
    args.output = Path(out)

    # 3. Overrides
    print("\n[3/4] Identity overrides (press ENTER to let AI decide):")
    uname = input("  Force username: ").strip()
    if uname:
        args.override_username = uname
    hname = input("  Force hostname: ").strip()
    if hname:
        args.override_hostname = hname

    # 4. Logging
    print("\n[4/4] Logging:")
    if input("  Verbose logging? (y/N): ").strip().lower() == "y":
        args.verbose = True

    print("\n" + "=" * 60)
    print(f" Occupation: {args.occupation}")
    print(f" Location:   {args.location}")
    print(f" Output:     {args.output}")
    print("=" * 60)

    if input("\nProceed? (Y/n): ").strip().lower() == "n":
        print("Aborted.")
        sys.exit(0)

    return args


# ---------------------------------------------------------------------------
# AI persona builder (single path — replaces dead AIOrchestrator block)
# ---------------------------------------------------------------------------

def _build_ai_persona(
    args: argparse.Namespace,
    config: Dict[str, Any],
    logger: logging.Logger,
) -> int:
    """Generate a PersonaContext via Gemini and wire the path into config.

    Returns 0 on success, non-zero on failure.
    """
    import os
    import time as _time

    occupation = getattr(args, "occupation", None)
    if not occupation:
        print("\nError: --occupation is required for AI generation.")
        print("Example: python main.py --ai-generate --occupation 'Software Engineer'")
        return 2

    api_key = os.environ.get("GEMINI_API_KEY") or config.get("ai", {}).get("gemini", {}).get("api_key")
    if not api_key:
        print("\nError: GEMINI_API_KEY not set. Export it or add to config.yaml.")
        return 2

    try:
        from services.ai.gemini_client import GeminiClient
        from services.ai.persona_generator import PersonaGenerator

        gemini_cfg = config.get("ai", {}).get("gemini", {})
        client = GeminiClient(
            api_key=api_key,
            model=gemini_cfg.get("model", "gemini-3.1-flash-lite-preview"),
            temperature=gemini_cfg.get("temperature", 0.7),
        )
        generator = PersonaGenerator(client)

        hints_parts = []
        if getattr(args, "location", None):
            hints_parts.append(f"location: {args.location}")
        if getattr(args, "interests", None):
            hints_parts.append(f"interests: {', '.join(args.interests)}")

        print(f"\n{'='*60}")
        print(" AI Persona Generation (Gemini)")
        print(f"{'='*60}")
        print(f" Occupation : {occupation}")
        if hints_parts:
            print(f" Hints      : {'; '.join(hints_parts)}")
        print(f"{'='*60}\n")

        t0 = _time.perf_counter()
        persona = generator.generate(
            occupation=occupation,
            location=getattr(args, "location", "United States"),
            hints="; ".join(hints_parts) if hints_parts else occupation,
        )
        elapsed_ms = (_time.perf_counter() - t0) * 1000

        print(f" Generated  : {persona.full_name} ({persona.username})")
        print(f" Org        : {persona.organization}")
        print(f" Archetype  : {persona.profile_archetype}")
        print(f" Time       : {elapsed_ms:.1f}ms\n")

        # Persist persona so Orchestrator.initialize() can load it
        out_dir = Path(config.get("profiles_dir", "profiles/generated"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{persona.username}.yaml"

        import yaml as _yaml
        with out_path.open("w", encoding="utf-8") as fh:
            _yaml.safe_dump(persona.model_dump(mode="json"), fh, allow_unicode=True, sort_keys=False)

        config["profile_path"] = str(out_path)
        if not args.override_username:
            config["override_username"] = persona.username

        logger.info("AI persona saved: %s", out_path)
        return 0

    except Exception as exc:
        logger.error("AI persona generation failed: %s", exc)
        print(f"\nAI generation failed: {exc}")
        return 1


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

    try:
        # Load configuration
        logger.debug("Loading config from: %s", args.config)
        config = load_config(args.config)
        config = merge_cli_args(config, args)
        
        # ---------------------------------------------------------------------------
        # Persona build — AI-generate or preset/explicit YAML
        # ---------------------------------------------------------------------------
        if getattr(args, "ai_generate", False):
            rc = _build_ai_persona(args, config, logger)
            if rc != 0:
                return rc

        # ---------------------------------------------------------------------------
        # SAFE MOUNT BLOCK (ADR-017 revised)
        #
        # Three supported modes, in priority order:
        #
        #   Mode A — VM disk image  (--image disk.img)
        #     Safest. ARC attaches the image via losetup, runs SafetyGuard,
        #     then mounts via ntfs-3g.  No risk to physical drives.
        #
        #   Mode B — Explicit partition (--partition /dev/X [--force-partition])
        #     Physical drives REQUIRE --force-partition + interactive confirmation.
        #     SafetyGuard blocks hibernated/dirty partitions unconditionally.
        #
        #   Mode C — Output directory  (no --image, no --partition)
        #     Default safe mode.  All artifacts written to --output dir locally.
        #     NO partition is touched.  Suitable for dry-runs, CI, development.
        #
        # Auto-discovery of physical drives has been REMOVED — it was the root
        # cause of dual-boot corruption (silently mounted a hibernated Windows
        # partition and called remove_hiberfile, destroying hiberfil.sys + BCD).
        # ---------------------------------------------------------------------------

        _image_arg   = getattr(args, "image", None)
        _partition_arg = getattr(args, "partition", None) or config.get("windows_partition")
        _force        = getattr(args, "force_partition", False)
        _mp_arg       = getattr(args, "mount_point", None)
        _loop_device: str | None = None  # track for cleanup in finally

        if not args.dry_run:
            from pathlib import Path as _Path

            mp: _Path = (
                _mp_arg
                or (_Path(config["windows_mount_point"]) if config.get("windows_mount_point") else None)
                or _Path("/mnt/arc_windows")
            )

            # ── Mode A: VM disk image ──────────────────────────────────────────
            if _image_arg is not None:
                image_path = _Path(_image_arg)
                logger.info("Image mode: attaching %s via losetup …", image_path)
                try:
                    from core.safety_guard import attach_image_as_loopback, preflight_check
                    from core.linux_mount import LinuxMountBackend

                    part_idx = getattr(args, "image_partition", 3)
                    _loop_device = attach_image_as_loopback(image_path, partition_index=part_idx)
                    config["_loop_device"] = _loop_device
                    logger.info("Loopback device: %s", _loop_device)

                    # Safety check (always non-interactive for loopback images)
                    check = preflight_check(
                        _loop_device, mp,
                        force_partition=False,
                        non_interactive=True,
                    )
                    if not check.safe_to_proceed:
                        for err in check.errors:
                            print(err, file=sys.stderr)
                        return 2

                    backend = LinuxMountBackend(_loop_device, mp)
                    backend.mount()
                    config["mount_path"] = str(mp)
                    config["_ntfs_backend"] = backend
                    logger.info("Image %s mounted at %s via %s", image_path, mp, _loop_device)

                except Exception as exc:
                    logger.error("Image mode failed: %s", exc)
                    print(f"\n{exc}", file=sys.stderr)
                    return 2

            # ── Mode B: Explicit partition (dual-boot or explicit loopback) ────
            elif _partition_arg is not None:
                logger.info("Partition mode: target=%s, force=%s", _partition_arg, _force)
                try:
                    from core.safety_guard import preflight_check
                    from core.linux_mount import LinuxMountBackend

                    # SafetyGuard: classifies device, checks hibernation, prompts if physical
                    check = preflight_check(
                        _partition_arg, mp,
                        force_partition=_force,
                        non_interactive=False,
                    )
                    if not check.safe_to_proceed:
                        for err in check.errors:
                            print(err, file=sys.stderr)
                        return 2

                    for warning in check.warnings:
                        logger.warning("SafetyGuard: %s", warning)

                    backend = LinuxMountBackend(_partition_arg, mp)
                    backend.mount()
                    config["mount_path"] = str(mp)
                    config["_ntfs_backend"] = backend
                    logger.info("Partition %s mounted at %s", _partition_arg, mp)

                except Exception as exc:
                    logger.error("Partition mount failed: %s", exc)
                    print(f"\n{exc}", file=sys.stderr)
                    return 2

            # ── Mode C: Output directory (no image, no partition) ──────────────
            else:
                # Safe default: write to local output directory, no mounting.
                logger.info(
                    "Output-directory mode: writing artifacts to %s (no partition mounted)",
                    config.get("mount_path", "./output"),
                )
                # Auto-discovery of physical partitions intentionally REMOVED.
                # See docs/recovery_guide.md for the incident that motivated this.

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
        num_services = register_services(orchestrator, args.categories, config=config)
        logger.info("Registered %d services", num_services)

        if num_services == 0:
            logger.warning("No services registered. Nothing to do.")
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
        if "orchestrator" in locals():
            orchestrator.cleanup()
        # Detach loopback device created by --image mode
        _ld = locals().get("_loop_device") or (config.get("_loop_device") if "config" in locals() else None)
        if _ld:
            try:
                from core.safety_guard import detach_loopback
                detach_loopback(_ld)
            except Exception as _exc:
                logger.warning("Could not detach loopback %s: %s", _ld, _exc)


if __name__ == "__main__":
    sys.exit(main())
