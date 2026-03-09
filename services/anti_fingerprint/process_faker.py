"""Process faker — anti-fingerprint registry service.

Writes realistic Windows service registry entries and Run/RunOnce keys to
make the offline image look like a normally-running machine with background
processes.  Sourced from ``templates/registry/common_services.json``.

This module is a **pure operation builder** — it constructs
:class:`HiveOperation` lists and delegates execution to the injected
:class:`HiveWriter`.  No live process manipulation is performed.

Registry paths written
-----------------------
``SYSTEM`` hive:
* ``ControlSet001\\Services\\{service_name}``
    — Type (DWORD), Start (DWORD), ErrorControl (DWORD),
      ImagePath (REG_EXPAND_SZ), DisplayName (REG_SZ),
      Description (REG_SZ), ObjectName (REG_SZ)

``SOFTWARE`` hive:
* ``Microsoft\\Windows\\CurrentVersion\\Run``
    — Per-profile auto-start entries (e.g. OneDrive, Teams, Chrome updater)
* ``Microsoft\\Windows\\CurrentVersion\\RunOnce``
    — One-time deferred setup entries (empty by default, realistic to exist)

``NTUSER.DAT`` (per-user) hive:
* ``Software\\Microsoft\\Windows\\CurrentVersion\\Run``
    — User-level Run entries (profile-specific)

Profile-specific Run entries
-----------------------------
* home:      OneDrive, Spotify, Discord, Windows Defender tray icon
* office:    Teams, OneDrive, Outlook Notifier, Zoom, Edge updater
* developer: Docker Desktop, Slack, GitHubDesktop updater, VS Code CLI
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hive paths
# ---------------------------------------------------------------------------

_SYSTEM_HIVE: str = "Windows/System32/config/SYSTEM"
_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"
_NTUSER_HIVE: str = "Users/{username}/NTUSER.DAT"

# ---------------------------------------------------------------------------
# Registry key paths
# ---------------------------------------------------------------------------

_SERVICES_KEY: str = r"ControlSet001\Services"
_RUN_KEY: str = r"Microsoft\Windows\CurrentVersion\Run"
_RUNONCE_KEY: str = r"Microsoft\Windows\CurrentVersion\RunOnce"
_USER_RUN_KEY: str = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ---------------------------------------------------------------------------
# Template file
# ---------------------------------------------------------------------------

_SERVICES_TEMPLATE_FILE: str = "common_services.json"

# ---------------------------------------------------------------------------
# Profile-specific HKLM Run entries
# ---------------------------------------------------------------------------

_HOME_RUN_ENTRIES: Dict[str, str] = {
    "OneDrive": (
        r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background'
    ),
    "SecurityHealth": (
        r"%windir%\system32\SecurityHealthSystray.exe"
    ),
}

_OFFICE_RUN_ENTRIES: Dict[str, str] = {
    "OneDrive": (
        r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background'
    ),
    "SecurityHealth": (
        r"%windir%\system32\SecurityHealthSystray.exe"
    ),
    "MicrosoftEdgeAutoLaunch": (
        r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --no-startup-window'
    ),
}

_DEVELOPER_RUN_ENTRIES: Dict[str, str] = {
    "OneDrive": (
        r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background'
    ),
    "SecurityHealth": (
        r"%windir%\system32\SecurityHealthSystray.exe"
    ),
    "com.squirrel.GitHubDesktop.GitHubDesktop": (
        r'"C:\Users\{username}\AppData\Local\GitHubDesktop\Update.exe" --processStart GitHubDesktop.exe'
    ),
}

_PROFILE_RUN_ENTRIES: Dict[str, Dict[str, str]] = {
    "home": _HOME_RUN_ENTRIES,
    "office": _OFFICE_RUN_ENTRIES,
    "developer": _DEVELOPER_RUN_ENTRIES,
}

# ---------------------------------------------------------------------------
# Profile-specific NTUSER Run entries (user-level)
# ---------------------------------------------------------------------------

_HOME_USER_RUN: Dict[str, str] = {
    "Spotify": (
        r"C:\Users\{username}\AppData\Roaming\Spotify\Spotify.exe"
    ),
}

_OFFICE_USER_RUN: Dict[str, str] = {
    "com.squirrel.Teams.Teams": (
        r'"C:\Users\{username}\AppData\Local\Microsoft\Teams\Update.exe" --processStart Teams.exe'
    ),
}

_DEVELOPER_USER_RUN: Dict[str, str] = {
    "Docker Desktop": (
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe -Autostart"
    ),
    "com.squirrel.slack.slack": (
        r'"C:\Users\{username}\AppData\Local\slack\Update.exe" --processStart slack.exe'
    ),
}

_PROFILE_USER_RUN_ENTRIES: Dict[str, Dict[str, str]] = {
    "home": _HOME_USER_RUN,
    "office": _OFFICE_USER_RUN,
    "developer": _DEVELOPER_USER_RUN,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProcessFakerError(Exception):
    """Raised when process faker operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ProcessFaker(BaseService):
    """Writes Windows service entries and Run keys into offline registry hives.

    Populates ``ControlSet001\\Services`` with entries from
    ``common_services.json`` and adds profile-appropriate Run key entries to
    both HKLM and NTUSER.DAT.

    Args:
        hive_writer:     Low-level registry hive I/O service.
        audit_logger:    Shared audit logger for traceability.
        templates_dir:   Path to the ``templates/registry/`` directory
                         containing ``common_services.json``.
    """

    def __init__(
        self,
        hive_writer: HiveWriter,
        audit_logger: Any,
        templates_dir: Path,
    ) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger
        self._templates_dir = templates_dir
        self._services_data: List[Dict[str, Any]] = self._load_services()

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "ProcessFaker"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type:  str — ``"home"``, ``"office"``, or ``"developer"``.
            username:      str — Windows username for NTUSER.DAT path and
                           path template substitution.

        Raises:
            ProcessFakerError: On missing context keys or write failure.
        """
        profile_type = context.get("profile_type")
        if not profile_type:
            raise ProcessFakerError(
                "Missing required 'profile_type' in context"
            )
        username = context.get("username")
        if not username:
            raise ProcessFakerError(
                "Missing required 'username' in context"
            )
        self.fake_processes(profile_type, username)

    # -- public API ---------------------------------------------------------

    def fake_processes(self, profile_type: str, username: str) -> None:
        """Build and execute all process-faking registry operations.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
            username:     Windows username for NTUSER.DAT path resolution.

        Raises:
            ProcessFakerError: On write failure.
        """
        operations = self.build_operations(profile_type, username)
        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise ProcessFakerError(
                f"Process faker registry write failed: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "fake_processes",
            "profile_type": profile_type,
            "username": username,
            "operations_count": len(operations),
        })
        logger.info(
            "Process faker complete: %d operations for %s (%s)",
            len(operations), username, profile_type,
        )

    def build_operations(
        self, profile_type: str, username: str
    ) -> List[HiveOperation]:
        """Build all process-faking operations without writing.

        Pure function — suitable for isolated testing.

        Args:
            profile_type: Profile type string.
            username:     Windows username.

        Returns:
            List of :class:`HiveOperation`.
        """
        ops: List[HiveOperation] = []

        # 1. Service entries (SYSTEM hive)
        ops.extend(self._build_service_ops())

        # 2. HKLM Run entries (SOFTWARE hive)
        ops.extend(self._build_hklm_run_ops(profile_type, username))

        # 3. HKLM RunOnce — exists but empty (realistic)
        ops.extend(self._build_runonce_ops())

        # 4. NTUSER Run entries
        ops.extend(self._build_ntuser_run_ops(profile_type, username))

        return ops

    # -- operation builders -------------------------------------------------

    def _build_service_ops(self) -> List[HiveOperation]:
        """Build service registry operations from common_services.json.

        Returns:
            List of :class:`HiveOperation` for the SYSTEM hive.
        """
        ops: List[HiveOperation] = []
        for svc in self._services_data:
            svc_key = rf"{_SERVICES_KEY}\{svc['name']}"
            ops.extend([
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="Type",
                    value_data=svc.get("service_type", 32),
                    value_type=RegistryValueType.REG_DWORD,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="Start",
                    value_data=svc.get("start_type", 3),
                    value_type=RegistryValueType.REG_DWORD,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="ErrorControl",
                    value_data=1,
                    value_type=RegistryValueType.REG_DWORD,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="ImagePath",
                    value_data=svc.get("image_path", ""),
                    value_type=RegistryValueType.REG_EXPAND_SZ,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="DisplayName",
                    value_data=svc.get("display_name", svc["name"]),
                    value_type=RegistryValueType.REG_SZ,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="Description",
                    value_data=svc.get("description", ""),
                    value_type=RegistryValueType.REG_SZ,
                ),
                HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=svc_key,
                    value_name="ObjectName",
                    value_data=svc.get("object_name", "LocalSystem"),
                    value_type=RegistryValueType.REG_SZ,
                ),
            ])
        return ops

    def _build_hklm_run_ops(
        self, profile_type: str, username: str
    ) -> List[HiveOperation]:
        """Build HKLM\\...\\Run operations for the given profile.

        Args:
            profile_type: Profile type string.
            username:     Used for ``{username}`` template substitution.

        Returns:
            List of :class:`HiveOperation` for the SOFTWARE hive.
        """
        run_map = _PROFILE_RUN_ENTRIES.get(profile_type, _HOME_RUN_ENTRIES)
        ops: List[HiveOperation] = []
        for value_name, cmd_template in run_map.items():
            cmd = cmd_template.replace("{username}", username)
            ops.append(HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_RUN_KEY,
                value_name=value_name,
                value_data=cmd,
                value_type=RegistryValueType.REG_SZ,
            ))
        return ops

    def _build_runonce_ops(self) -> List[HiveOperation]:
        """Build a placeholder RunOnce key write (empty — key must exist).

        Returns:
            One :class:`HiveOperation` touching the RunOnce key.
        """
        # Writing an empty string to (default) ensures the key is present
        # while remaining functionally empty.
        return [
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_RUNONCE_KEY,
                value_name="(default)",
                value_data="",
                value_type=RegistryValueType.REG_SZ,
            )
        ]

    def _build_ntuser_run_ops(
        self, profile_type: str, username: str
    ) -> List[HiveOperation]:
        """Build NTUSER.DAT Software\\...\\Run operations.

        Args:
            profile_type: Profile type string.
            username:     Windows username (determines NTUSER.DAT hive path).

        Returns:
            List of :class:`HiveOperation` for the NTUSER.DAT hive.
        """
        ntuser_hive = _NTUSER_HIVE.format(username=username)
        user_run_map = _PROFILE_USER_RUN_ENTRIES.get(profile_type, {})
        ops: List[HiveOperation] = []
        for value_name, cmd_template in user_run_map.items():
            cmd = cmd_template.replace("{username}", username)
            ops.append(HiveOperation(
                hive_path=ntuser_hive,
                key_path=_USER_RUN_KEY,
                value_name=value_name,
                value_data=cmd,
                value_type=RegistryValueType.REG_SZ,
            ))
        return ops

    # -- helpers ------------------------------------------------------------

    def _load_services(self) -> List[Dict[str, Any]]:
        """Load common_services.json from the templates directory.

        Returns:
            List of service definition dicts.

        Raises:
            ProcessFakerError: If file is missing or malformed.
        """
        path = self._templates_dir / _SERVICES_TEMPLATE_FILE
        if not path.is_file():
            raise ProcessFakerError(
                f"Common services template not found: {path}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProcessFakerError(
                f"Failed to parse {_SERVICES_TEMPLATE_FILE}: {exc}"
            ) from exc
        if "services" not in data or not isinstance(data["services"], list):
            raise ProcessFakerError(
                f"{_SERVICES_TEMPLATE_FILE} must contain a 'services' list"
            )
        return data["services"]
