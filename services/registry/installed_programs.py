"""Registry service for installed-programs (Uninstall) entries.

Creates realistic ``Uninstall`` registry keys under both the 64-bit and
32-bit SOFTWARE hive paths.  Each installed application listed in the
profile's ``installed_apps`` is mapped to a well-known program definition
(display name, publisher, version, install location, etc.) and written as
a set of registry values under its own subkey.

This module is a **pure operation builder** — it constructs
:class:`HiveOperation` lists and delegates execution to :class:`HiveWriter`.

Target hive paths
-----------------
* ``SOFTWARE``
    * ``Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_key}``
        — DisplayName, DisplayVersion, Publisher, InstallLocation,
          InstallDate, UninstallString, EstimatedSize, NoModify,
          NoRepair
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"

_UNINSTALL_KEY: str = (
    r"Microsoft\Windows\CurrentVersion\Uninstall"
)

# ---------------------------------------------------------------------------
# Program catalog — maps profile app names to realistic registry metadata
# ---------------------------------------------------------------------------

_PROGRAM_CATALOG: Dict[str, Dict[str, Any]] = {
    # Office / productivity
    "outlook": {
        "DisplayName": "Microsoft Outlook",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "16.0.17328.20162",
        "InstallLocation": r"C:\Program Files\Microsoft Office\root\Office16",
        "EstimatedSize": 524288,
    },
    "teams": {
        "DisplayName": "Microsoft Teams",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "24004.1309.2689.2246",
        "InstallLocation": r"C:\Program Files\WindowsApps\MSTeams",
        "EstimatedSize": 409600,
    },
    "excel": {
        "DisplayName": "Microsoft Excel",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "16.0.17328.20162",
        "InstallLocation": r"C:\Program Files\Microsoft Office\root\Office16",
        "EstimatedSize": 524288,
    },
    "word": {
        "DisplayName": "Microsoft Word",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "16.0.17328.20162",
        "InstallLocation": r"C:\Program Files\Microsoft Office\root\Office16",
        "EstimatedSize": 524288,
    },
    # Developer tools
    "vscode": {
        "DisplayName": "Microsoft Visual Studio Code",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "1.87.2",
        "InstallLocation": r"C:\Users\{username}\AppData\Local\Programs\Microsoft VS Code",
        "EstimatedSize": 348160,
    },
    "docker": {
        "DisplayName": "Docker Desktop",
        "Publisher": "Docker Inc.",
        "DisplayVersion": "4.28.0",
        "InstallLocation": r"C:\Program Files\Docker\Docker",
        "EstimatedSize": 819200,
    },
    "git": {
        "DisplayName": "Git",
        "Publisher": "The Git Development Community",
        "DisplayVersion": "2.44.0",
        "InstallLocation": r"C:\Program Files\Git",
        "EstimatedSize": 327680,
    },
    "terminal": {
        "DisplayName": "Windows Terminal",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "1.19.10573.0",
        "InstallLocation": r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal",
        "EstimatedSize": 61440,
        "_system_component": True,
    },
    # Home / media
    "spotify": {
        "DisplayName": "Spotify",
        "Publisher": "Spotify AB",
        "DisplayVersion": "1.2.31.1205",
        "InstallLocation": r"C:\Users\{username}\AppData\Roaming\Spotify",
        "EstimatedSize": 307200,
    },
    "vlc": {
        "DisplayName": "VLC media player",
        "Publisher": "VideoLAN",
        "DisplayVersion": "3.0.20",
        "InstallLocation": r"C:\Program Files\VideoLAN\VLC",
        "EstimatedSize": 163840,
    },
    "chrome": {
        "DisplayName": "Google Chrome",
        "Publisher": "Google LLC",
        "DisplayVersion": "122.0.6261.112",
        "InstallLocation": r"C:\Program Files\Google\Chrome\Application",
        "EstimatedSize": 204800,
    },
    # Common utilities — system/Store apps use realistic uninstall paths
    "notepad": {
        "DisplayName": "Notepad",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "11.2310.17.0",
        "InstallLocation": r"C:\Windows\System32",
        "EstimatedSize": 1024,
        "_system_component": True,
    },
    "calculator": {
        "DisplayName": "Windows Calculator",
        "Publisher": "Microsoft Corporation",
        "DisplayVersion": "11.2311.0.0",
        "InstallLocation": r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator",
        "EstimatedSize": 20480,
        "_system_component": True,
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InstalledProgramsError(Exception):
    """Raised when installed-programs operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class InstalledPrograms(BaseService):
    """Writes Uninstall registry entries for profile-defined applications.

    This service is a **pure operation builder** — it reads the list of
    installed apps from the profile context, maps each to a program
    definition from the catalog, and produces :class:`HiveOperation`
    lists for the :class:`HiveWriter`.

    Dependencies (injected):
        hive_writer: Low-level hive read/write service.
        audit_logger: Shared audit logger for traceability.

    Args:
        hive_writer: ``HiveWriter`` instance for offline hive I/O.
        audit_logger: ``AuditLogger`` instance for recording operations.
    """

    def __init__(self, hive_writer: HiveWriter, audit_logger: Any) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "InstalledPrograms"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            installed_apps: list[str] — app names from the profile.
            username: str — the profile username (for path templates).

        Raises:
            InstalledProgramsError: If required keys are missing.
        """
        installed_apps = context.get("installed_apps")
        if installed_apps is None:
            raise InstalledProgramsError(
                "Missing required 'installed_apps' in context"
            )
        username = context.get("username", "User")
        self.write_programs(installed_apps, username)

    # -- public API ---------------------------------------------------------

    def write_programs(
        self,
        installed_apps: List[str],
        username: str = "User",
    ) -> None:
        """Build and execute Uninstall registry operations.

        Args:
            installed_apps: Application names matching catalog keys.
            username: Profile username for path template substitution.

        Raises:
            InstalledProgramsError: On write failure.
        """
        operations = self.build_operations(installed_apps, username)
        if not operations:
            logger.info("No known programs to register — skipping")
            return

        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise InstalledProgramsError(
                f"Failed to write installed programs: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_programs_complete",
            "programs_count": len(installed_apps),
            "operations_count": len(operations),
        })
        logger.info(
            "Registered %d programs (%d operations)",
            len(installed_apps),
            len(operations),
        )

    def build_operations(
        self,
        installed_apps: List[str],
        username: str = "User",
    ) -> List[HiveOperation]:
        """Build Uninstall registry operations for all known apps.

        Unknown app names (not in the catalog) are silently skipped with
        a warning log.  This is intentional — profiles may list apps that
        other services handle differently.

        Args:
            installed_apps: Application names from the profile.
            username: For ``{username}`` template substitution in paths.

        Returns:
            List of :class:`HiveOperation` ready for execution.
        """
        ops: List[HiveOperation] = []

        for app_name in installed_apps:
            app_key = app_name.lower().strip()
            program = _PROGRAM_CATALOG.get(app_key)
            if program is None:
                logger.warning(
                    "App '%s' not in program catalog — skipping", app_name
                )
                continue
            ops.extend(
                self._build_app_operations(app_key, program, username)
            )

        return ops

    # -- operation builders -------------------------------------------------

    def _build_app_operations(
        self,
        app_key: str,
        program: Dict[str, Any],
        username: str,
    ) -> List[HiveOperation]:
        """Build operations for a single application.

        Args:
            app_key: Normalised application key.
            program: Program metadata from the catalog.
            username: For path template substitution.

        Returns:
            List of registry operations for this application.
        """
        subkey = self._derive_subkey(app_key)
        key_path = rf"{_UNINSTALL_KEY}\{subkey}"
        install_location = program["InstallLocation"].replace(
            "{username}", username
        )
        install_date = self._derive_install_date(app_key)
        is_system = program.get("_system_component", False)

        ops: List[HiveOperation] = [
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="DisplayName",
                value_data=program["DisplayName"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="DisplayVersion",
                value_data=program["DisplayVersion"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Publisher",
                value_data=program["Publisher"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="InstallLocation",
                value_data=install_location,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="InstallDate",
                value_data=install_date,
                value_type=RegistryValueType.REG_SZ,
            ),
        ]

        if is_system:
            # System/Store apps use SystemComponent=1 (hidden from Programs)
            ops.append(HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="SystemComponent",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ))
        else:
            # Regular apps have an uninstall string
            ops.append(HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="UninstallString",
                value_data=rf'"{install_location}\uninstall.exe"',
                value_type=RegistryValueType.REG_SZ,
            ))

        ops.extend([
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="EstimatedSize",
                value_data=program["EstimatedSize"],
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="NoModify",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="NoRepair",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ),
        ])

        return ops

    # -- deterministic derivation helpers -----------------------------------

    @staticmethod
    def _derive_subkey(app_key: str) -> str:
        """Derive a deterministic Uninstall subkey name.

        Produces a GUID-like ``{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}``
        string so the same app always gets the same registry subkey.

        Args:
            app_key: Normalised application key.

        Returns:
            GUID-formatted subkey string.
        """
        digest = hashlib.sha256(
            app_key.encode("utf-8")
        ).hexdigest()
        return (
            f"{{{digest[:8]}-{digest[8:12]}-{digest[12:16]}"
            f"-{digest[16:20]}-{digest[20:32]}}}"
        )

    @staticmethod
    def _derive_install_date(app_key: str) -> str:
        """Derive a deterministic install date string (YYYYMMDD format).

        Produces a date in the range [2021-01-01, 2024-12-31].

        Args:
            app_key: Normalised application key.

        Returns:
            Date string, e.g. ``"20230815"``.
        """
        digest = hashlib.sha256(
            (app_key + ":install_date").encode("utf-8")
        ).hexdigest()
        # 2021-01-01 to 2024-12-31 → 4 years × 365 = 1460 day offsets
        day_offset = int(digest[:8], 16) % 1460
        year = 2021 + day_offset // 365
        remainder = day_offset % 365
        month = (remainder // 30) + 1
        day = (remainder % 30) + 1
        # Clamp to valid calendar ranges
        month = min(month, 12)
        day = min(day, 28)
        return f"{year:04d}{month:02d}{day:02d}"
