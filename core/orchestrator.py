"""Orchestrator — wires and sequences all Arc services.

Instantiates core dependencies and all implemented services, then calls
each service's ``apply(context)`` in dependency order.  Services that
require missing resources (e.g. registry hives not present in the mount)
are silently skipped with a warning log.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.audit_logger import AuditLogger
from core.identity_generator import IdentityGenerator, IdentityBundle
from core.mount_manager import MountManager
from core.profile_engine import ProfileContext, ProfileEngine
from core.timestamp_service import TimestampService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile-type detection (mirrors IdentityGenerator logic)
# ---------------------------------------------------------------------------

_DEVELOPER_APPS = frozenset({
    "vscode", "docker", "git", "terminal", "vim", "intellij",
    "sublime", "atom", "neovim", "emacs", "pycharm", "webstorm",
})

_HOME_CATEGORIES = frozenset({
    "social_media", "entertainment", "gaming", "shopping", "streaming",
})


def detect_profile_type(profile: ProfileContext) -> str:
    """Infer 'home', 'office', or 'developer' from a ProfileContext."""
    apps = frozenset(a.lower() for a in profile.installed_apps)
    if apps & _DEVELOPER_APPS:
        return "developer"
    categories = frozenset(c.lower() for c in profile.browsing.categories)
    if profile.organization.lower() == "personal" or categories & _HOME_CATEGORIES:
        return "home"
    return "office"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Sequences all Arc services against a mounted drive image.

    Args:
        mount_root: Path to the mounted drive / target directory.
        profile_name: Profile to load (e.g. ``"office_user"``).
        timeline_days: Number of days of history to generate.
        dry_run: If True, log planned operations without writing.
        project_root: Path to the Arc project root (auto-detected if None).
    """

    def __init__(
        self,
        mount_root: str,
        profile_name: str = "office_user",
        timeline_days: int = 90,
        dry_run: bool = False,
        project_root: Path | None = None,
    ) -> None:
        self._mount_root = mount_root
        self._profile_name = profile_name
        self._timeline_days = timeline_days
        self._dry_run = dry_run
        self._project_root = project_root or Path(__file__).resolve().parent.parent

        # Paths
        self._profiles_dir = self._project_root / "profiles"
        self._data_dir = self._project_root / "data"
        self._templates_dir = self._project_root / "templates"

    def run(self) -> Dict[str, Any]:
        """Execute the full artifact generation pipeline.

        Returns:
            Summary dict with counts of operations performed.
        """
        logger.info("=" * 60)
        logger.info("Arc — Anti-Sandbox Personalizer")
        logger.info("=" * 60)
        logger.info("Mount root : %s", self._mount_root)
        logger.info("Profile    : %s", self._profile_name)
        logger.info("Timeline   : %d days", self._timeline_days)
        logger.info("Dry run    : %s", self._dry_run)
        logger.info("=" * 60)

        # ── 1. Core dependencies ────────────────────────────────────
        mount = MountManager(self._mount_root)
        audit = AuditLogger()
        ts = TimestampService()
        engine = ProfileEngine(self._profiles_dir)

        profile: ProfileContext = engine.load_profile(self._profile_name)
        logger.info("Profile loaded: %s (org=%s)", profile.username, profile.organization)

        identity_gen = IdentityGenerator(profile, self._data_dir)
        identity: IdentityBundle = identity_gen.generate()
        logger.info(
            "Identity: %s <%s> @ %s",
            identity.user.full_name,
            identity.user.email,
            identity.user.computer_name,
        )

        profile_type = detect_profile_type(profile)
        username = identity.user.username
        logger.info("Detected profile type: %s", profile_type)
        logger.info("Using username: %s", username)

        # ── 2. Build shared context ─────────────────────────────────
        context: Dict[str, Any] = {
            # Profile data
            "profile_name": self._profile_name,
            "profile_type": profile_type,
            "profile_config": {
                "browsing": {
                    "categories": list(profile.browsing.categories),
                    "daily_avg_sites": profile.browsing.daily_avg_sites,
                },
                "work_hours": {
                    "start": profile.work_hours.start,
                    "end": profile.work_hours.end,
                    "active_days": list(profile.work_hours.active_days),
                },
            },
            "work_hours": {
                "start": profile.work_hours.start,
                "end": profile.work_hours.end,
                "active_days": list(profile.work_hours.active_days),
            },
            # Identity
            "username": username,
            "identity_bundle": identity,
            "installed_apps": list(profile.installed_apps),
            # Timeline
            "timeline_days": self._timeline_days,
        }

        if self._dry_run:
            logger.info("[DRY RUN] Would generate artifacts with context:")
            for k, v in context.items():
                logger.info("  %s = %s", k, v)
            logger.info("[DRY RUN] No files written.")
            return {"dry_run": True, "services_run": 0}

        # ── 3. Instantiate and run services ─────────────────────────
        services_run = 0

        # --- 3a. Browser services (always run — create new files) ---
        logger.info("-" * 40)
        logger.info("Phase: Browser artifacts")
        logger.info("-" * 40)

        browser_services = self._create_browser_services(
            mount, ts, audit, profile_type, username
        )
        for svc in browser_services:
            try:
                logger.info("Running service: %s", svc.service_name)
                svc.apply(context)
                services_run += 1
                logger.info("✓ %s completed", svc.service_name)
            except Exception:
                logger.exception("✗ %s failed", svc.service_name)

        # --- 3b. Registry services (skip if hives missing) ----------
        logger.info("-" * 40)
        logger.info("Phase: Registry artifacts")
        logger.info("-" * 40)

        try:
            registry_services = self._create_registry_services(
                mount, audit, username
            )
            for svc in registry_services:
                try:
                    logger.info("Running service: %s", svc.service_name)
                    svc.apply(context)
                    services_run += 1
                    logger.info("✓ %s completed", svc.service_name)
                except Exception:
                    logger.exception("✗ %s failed", svc.service_name)
        except FileNotFoundError as exc:
            logger.warning(
                "Registry services skipped (hive files not found): %s", exc
            )

        # ── 4. Summary ──────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("Arc completed: %d services executed", services_run)
        logger.info("Audit log: %d entries recorded", len(audit.entries))
        logger.info("=" * 60)

        return {
            "dry_run": False,
            "services_run": services_run,
            "audit_entries": len(audit.entries),
        }

    # ------------------------------------------------------------------
    # Service factory methods
    # ------------------------------------------------------------------

    def _create_browser_services(
        self,
        mount: MountManager,
        ts: TimestampService,
        audit: AuditLogger,
        profile_name: str,
        username: str,
    ) -> list:
        """Instantiate browser services in dependency order."""
        from services.browser.browser_profile import BrowserProfileService
        from services.browser.history import BrowserHistoryService
        from services.browser.downloads import BrowserDownloadService

        data_dir = str(self._data_dir / "wordlists")

        return [
            BrowserProfileService(
                mount, ts, audit,
                profile_name=profile_name,
                username=username,
                templates_dir=str(self._templates_dir / "browser"),
            ),
            BrowserHistoryService(
                mount, ts, audit,
                username=username,
                data_dir=data_dir,
            ),
            BrowserDownloadService(
                mount, ts, audit,
                profile_name=profile_name,
                username=username,
                data_dir=data_dir,
            ),
        ]

    def _create_registry_services(
        self,
        mount: MountManager,
        audit: AuditLogger,
        username: str,
    ) -> list:
        """Instantiate registry services.

        Raises FileNotFoundError if no hive files exist in the mount.
        """
        from services.registry.hive_writer import HiveWriter
        from services.registry.system_identity import SystemIdentity
        from services.registry.installed_programs import InstalledPrograms
        from services.registry.mru_recentdocs import MruRecentDocs
        from services.registry.userassist import UserAssist
        from services.registry.network_profiles import NetworkProfiles

        # Check that at least one hive file exists before creating HiveWriter
        software_hive = mount.root / "Windows" / "System32" / "config" / "SOFTWARE"
        ntuser_hive = mount.root / "Users" / username / "NTUSER.DAT"

        if not software_hive.exists() and not ntuser_hive.exists():
            raise FileNotFoundError(
                f"No registry hive files found at {software_hive} or {ntuser_hive}. "
                "Registry services require a Windows installation in the mount."
            )

        hive_writer = HiveWriter(mount, audit)

        return [
            SystemIdentity(hive_writer, audit),
            InstalledPrograms(hive_writer, audit),
            MruRecentDocs(hive_writer, audit),
            UserAssist(hive_writer, audit),
            NetworkProfiles(hive_writer, audit),
        ]
