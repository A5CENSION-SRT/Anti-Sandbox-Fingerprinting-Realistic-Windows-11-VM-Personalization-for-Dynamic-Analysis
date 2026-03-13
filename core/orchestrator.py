"""Central orchestrator for artifact generation pipeline.

The Orchestrator coordinates all services in the correct dependency order,
manages the execution context, and provides dry-run capability for safe
testing without filesystem modifications.

It handles:
- Service dependency resolution and ordering
- Context propagation to each service
- Dry-run mode simulation
- Progress tracking and error handling
- Audit trail aggregation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type

from core.audit_logger import AuditLogger
from core.identity_generator import IdentityGenerator
from core.mount_manager import MountManager
from core.profile_engine import ProfileEngine
from core.timestamp_service import TimestampService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Constants
# ---------------------------------------------------------------------------

class ExecutionPhase(Enum):
    """Execution phases for service ordering."""
    INFRASTRUCTURE = 1  # Core setup (directories, identity)
    FILESYSTEM = 2      # File artifacts
    REGISTRY = 3        # Registry hives
    BROWSER = 4         # Browser profiles
    APPLICATIONS = 5    # Application artifacts
    EVENTLOG = 6        # Event logs
    ANTI_FINGERPRINT = 7  # Anti-fingerprint measures
    EVALUATION = 8      # Final validation


# Service -> Phase mapping
_SERVICE_PHASES: Dict[str, ExecutionPhase] = {
    # Phase 1: Infrastructure
    "UserDirectoryService": ExecutionPhase.INFRASTRUCTURE,
    # Phase 2: Filesystem
    "DocumentGenerator": ExecutionPhase.FILESYSTEM,
    "MediaStubService": ExecutionPhase.FILESYSTEM,
    "PrefetchService": ExecutionPhase.FILESYSTEM,
    "ThumbnailCacheService": ExecutionPhase.FILESYSTEM,
    "RecentItemsService": ExecutionPhase.FILESYSTEM,
    "RecycleBinService": ExecutionPhase.FILESYSTEM,
    # Phase 3: Registry
    "HiveWriter": ExecutionPhase.REGISTRY,
    "InstalledPrograms": ExecutionPhase.REGISTRY,
    "MruRecentDocs": ExecutionPhase.REGISTRY,
    "NetworkProfiles": ExecutionPhase.REGISTRY,
    "SystemIdentity": ExecutionPhase.REGISTRY,
    "UserAssist": ExecutionPhase.REGISTRY,
    # Phase 4: Browser
    "BrowserProfileService": ExecutionPhase.BROWSER,
    "BookmarksService": ExecutionPhase.BROWSER,
    "BrowserHistoryService": ExecutionPhase.BROWSER,
    "CookiesCacheService": ExecutionPhase.BROWSER,
    "BrowserDownloadService": ExecutionPhase.BROWSER,
    # Phase 5: Applications
    "DevEnvironment": ExecutionPhase.APPLICATIONS,
    "OfficeArtifacts": ExecutionPhase.APPLICATIONS,
    "EmailClient": ExecutionPhase.APPLICATIONS,
    "CommsApps": ExecutionPhase.APPLICATIONS,
    # Phase 6: Event logs
    "EvtxWriter": ExecutionPhase.EVENTLOG,
    "ApplicationLog": ExecutionPhase.EVENTLOG,
    "SecurityLog": ExecutionPhase.EVENTLOG,
    "SystemLog": ExecutionPhase.EVENTLOG,
    "UpdateArtifacts": ExecutionPhase.EVENTLOG,
    # Phase 7: Anti-fingerprint
    "HardwareNormalizer": ExecutionPhase.ANTI_FINGERPRINT,
    "ProcessFaker": ExecutionPhase.ANTI_FINGERPRINT,
    "VmScrubber": ExecutionPhase.ANTI_FINGERPRINT,
}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ServiceResult:
    """Result of a single service execution."""
    service_name: str
    success: bool
    duration_ms: float = 0.0
    error: Optional[str] = None
    artifacts_created: int = 0


@dataclass
class OrchestrationResult:
    """Result of the full orchestration run."""
    success: bool
    dry_run: bool
    services_executed: int = 0
    services_failed: int = 0
    results: List[ServiceResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OrchestrationError(Exception):
    """Raised when orchestration fails."""


class ServiceRegistrationError(Exception):
    """Raised when service registration fails."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Coordinates all artifact generation services.

    The Orchestrator is the central coordinator that:
    1. Registers and orders services by dependency
    2. Builds the execution context from config and identity
    3. Runs services in the correct order
    4. Handles errors and provides audit trails

    Args:
        config: Configuration dictionary from config.yaml.
        audit_logger: Structured audit logger instance.
        dry_run: If True, simulates execution without writing files.

    Example::

        orchestrator = Orchestrator(config, audit_logger, dry_run=False)
        orchestrator.register_service(UserDirectoryService)
        orchestrator.register_service(DocumentGenerator)
        result = orchestrator.run()
    """

    def __init__(
        self,
        config: Dict[str, Any],
        audit_logger: AuditLogger,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._audit = audit_logger
        self._dry_run = dry_run

        # Core dependencies
        self._mount_manager: Optional[MountManager] = None
        self._timestamp_service: Optional[TimestampService] = None
        self._identity_generator: Optional[IdentityGenerator] = None
        self._profile_engine: Optional[ProfileEngine] = None

        # Service registry: name -> (class, instance)
        self._services: Dict[str, tuple] = {}
        self._service_order: List[str] = []

        # Execution context
        self._context: Dict[str, Any] = {}

    def initialize(self) -> None:
        """Initialize core dependencies and build context.

        This must be called before run() to set up:
        - MountManager for filesystem access
        - TimestampService for artifact timestamps
        - IdentityGenerator for user/machine identity
        - ProfileEngine for loading profile configurations

        Raises:
            OrchestrationError: If initialization fails.
        """
        try:
            # Initialize mount manager
            mount_path = Path(self._config.get("mount_path", "./output"))
            mount_path.mkdir(parents=True, exist_ok=True)
            self._mount_manager = MountManager(str(mount_path))

            # Load profile engine and profile context
            profiles_dir = Path(self._config.get("profiles_dir", "profiles"))
            self._profile_engine = ProfileEngine(profiles_dir)

            profile_name = self._config.get("profile_name", "base")
            profile_context = self._profile_engine.load_profile(profile_name)

            # Generate identity
            data_dir = Path(self._config.get("data_dir", "data"))
            self._identity_generator = IdentityGenerator(profile_context, data_dir)
            identity_bundle = self._identity_generator.generate(
                override_username=self._config.get("override_username"),
                override_hostname=self._config.get("override_hostname"),
            )

            # Initialize timestamp service with seed from identity
            username = identity_bundle.user.username
            computer_name = identity_bundle.user.computer_name
            seed = f"{username}-{computer_name}"

            self._timestamp_service = TimestampService(
                seed=seed,
                timeline_days=self._config.get("timeline_days", 90),
                work_hours={
                    "start": profile_context.work_hours.start,
                    "end": profile_context.work_hours.end,
                    "active_days": list(profile_context.work_hours.active_days),
                },
            )

            # Build execution context
            profile = {
                "username": profile_context.username,
                "organization": profile_context.organization,
                "locale": profile_context.locale,
                "profile_type": profile_name,
                "installed_apps": list(profile_context.installed_apps),
            }
            identity = {
                "username": identity_bundle.user.username,
                "full_name": identity_bundle.user.full_name,
                "email": identity_bundle.user.email,
                "computer_name": identity_bundle.user.computer_name,
                "organization": identity_bundle.user.organization,
            }

            # Build execution context
            self._context = {
                **identity,
                **profile,
                "config": self._config,
                "dry_run": self._dry_run,
                "timeline_days": self._config.get("timeline_days", 90),
            }

            self._audit.log({
                "operation": "orchestrator_init",
                "mount_path": str(mount_path),
                "username": identity.get("username"),
                "computer_name": identity.get("computer_name"),
                "profile_type": profile.get("profile_type"),
                "dry_run": self._dry_run,
            })

            logger.info(
                "Orchestrator initialized: user=%s, machine=%s, profile=%s",
                identity.get("username"),
                identity.get("computer_name"),
                profile.get("profile_type"),
            )

        except Exception as exc:
            logger.error("Failed to initialize orchestrator: %s", exc)
            raise OrchestrationError(f"Initialization failed: {exc}") from exc

    def register_service(self, service_class: Type) -> None:
        """Register a service for execution.

        Args:
            service_class: Service class (must have `service_name` property).

        Raises:
            ServiceRegistrationError: If registration fails.
        """
        try:
            import inspect

            # Setup available dependencies for dependency injection
            available_deps = {
                "mount_manager": self._mount_manager,
                "timestamp_service": self._timestamp_service,
                "audit_logger": self._audit,
                "data_dir": Path(self._config.get("data_dir", "data")),
                "templates_dir": Path(self._config.get("templates_dir", "templates")),
                "profile_config": self._context,  # passing context in case they ask for profile_config
                "username": self._context.get("username", "default_user"),
            }

            # Map already registered services by class name in case another service depends on them
            for name, (cls, inst) in self._services.items():
                # lower_snake_case of class name as key (e.g., 'HiveWriter' -> 'hive_writer')
                import re
                key = re.sub(r'(?<!^)(?=[A-Z])', '_', cls.__name__).lower()
                available_deps[key] = inst

            sig = inspect.signature(service_class.__init__)
            kwargs = {}
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                if param_name in available_deps:
                    kwargs[param_name] = available_deps[param_name]
                else:
                    # Provide None if it's optional, else fallback to something or let it fail
                    if param.default is not inspect.Parameter.empty:
                        kwargs[param_name] = param.default
                    else:
                        logger.warning("Unsatisfied dependency '%s' for %s", param_name, service_class.__name__)
                        kwargs[param_name] = None

            # Instantiate service with dynamic kwargs
            instance = service_class(**kwargs)

            service_name = getattr(instance, "service_name", service_class.__name__)
            self._services[service_name] = (service_class, instance)

            logger.debug("Registered service: %s", service_name)

        except Exception as exc:
            logger.error(
                "Failed to register service %s: %s",
                service_class.__name__, exc,
            )
            raise ServiceRegistrationError(
                f"Failed to register {service_class.__name__}: {exc}"
            ) from exc

    def _order_services(self) -> List[str]:
        """Order services by execution phase.

        Returns:
            List of service names in execution order.
        """
        def get_phase(name: str) -> int:
            phase = _SERVICE_PHASES.get(name, ExecutionPhase.FILESYSTEM)
            return phase.value

        return sorted(self._services.keys(), key=get_phase)

    def run(self, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> OrchestrationResult:
        """Execute all registered services in order.

        Args:
            progress_callback: Optional callback receiving (current_index, total_services, current_service_name).

        Returns:
            OrchestrationResult with execution details.

        Raises:
            OrchestrationError: If critical error occurs.
        """
        import time

        start_time = time.perf_counter()
        result = OrchestrationResult(success=True, dry_run=self._dry_run)

        # Order services
        self._service_order = self._order_services()

        self._audit.log({
            "operation": "orchestration_start",
            "services": self._service_order,
            "dry_run": self._dry_run,
        })

        logger.info(
            "Starting orchestration with %d services (dry_run=%s)",
            len(self._service_order), self._dry_run,
        )

        total_services = len(self._service_order)
        for i, service_name in enumerate(self._service_order):
            if progress_callback:
                progress_callback(i, total_services, service_name)

            service_start = time.perf_counter()
            _, instance = self._services[service_name]

            service_result = ServiceResult(
                service_name=service_name,
                success=False,
            )

            try:
                if self._dry_run:
                    logger.debug("[DRY RUN] Would execute: %s", service_name)
                else:
                    instance.apply(self._context)

                service_result.success = True
                result.services_executed += 1

                logger.debug("Executed service: %s", service_name)

            except Exception as exc:
                service_result.error = str(exc)
                result.services_failed += 1
                result.success = False

                logger.error("Service %s failed: %s", service_name, exc)

                # Check if we should abort
                if self._config.get("abort_on_failure", False):
                    raise OrchestrationError(
                        f"Aborted due to service failure: {service_name}"
                    ) from exc

            finally:
                service_result.duration_ms = (
                    time.perf_counter() - service_start
                ) * 1000
                result.results.append(service_result)

        if progress_callback:
            progress_callback(total_services, total_services, "Complete")

        result.total_duration_ms = (time.perf_counter() - start_time) * 1000

        self._audit.log({
            "operation": "orchestration_complete",
            "success": result.success,
            "services_executed": result.services_executed,
            "services_failed": result.services_failed,
            "total_duration_ms": result.total_duration_ms,
        })

        logger.info(
            "Orchestration complete: %d/%d services succeeded in %.2fms",
            result.services_executed,
            len(self._service_order),
            result.total_duration_ms,
        )

        return result

    def cleanup(self) -> None:
        """Clean up resources after orchestration."""
        if self._mount_manager and not self._dry_run:
            try:
                if hasattr(self._mount_manager, 'unmount'):
                    self._mount_manager.unmount()
            except Exception as exc:
                logger.warning("Failed to unmount: %s", exc)

    @property
    def context(self) -> Dict[str, Any]:
        """Get the current execution context."""
        return self._context.copy()

    @property
    def registered_services(self) -> List[str]:
        """Get list of registered service names."""
        return list(self._services.keys())
