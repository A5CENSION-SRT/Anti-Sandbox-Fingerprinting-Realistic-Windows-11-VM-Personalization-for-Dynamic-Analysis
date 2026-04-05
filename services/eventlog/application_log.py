"""Application event log service.

Generates synthetic Windows Application event log entries and writes them to
``Windows/System32/winevt/Logs/Application.evtx`` via :class:`EvtxWriter`.

This module is a **pure operation builder** — it builds :class:`EvtxRecord`
lists and delegates all binary I/O to the injected :class:`EvtxWriter`.

Synthetic events generated
--------------------------
* **1000** — Application error (crash report — occasional, realistic noise)
* **1001** — Application fault (WER bucket follow-up for 1000)
* **11707** — MSI: Product installed successfully
* **11724** — MSI: Product removed/uninstalled
* **0**    — Custom application-level informational events (profile-specific)

Provider reference
------------------
* 1000/1001 → ``Application Error``
* 11707/11724 → ``MsiInstaller``
* Custom → application name (e.g. ``"Google Chrome"``)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Tuple

from services.base_service import BaseService
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter, EvtxWriterError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APPLICATION_EVTX: str = "Windows/System32/winevt/Logs/Application.evtx"
_CHANNEL: str = "Application"

_PROVIDER_APP_ERROR: str = "Application Error"
_PROVIDER_MSI: str = "MsiInstaller"

_EID_APP_CRASH: int = 1000
_EID_APP_FAULT: int = 1001
_EID_MSI_INSTALL: int = 11707
_EID_MSI_REMOVE: int = 11724

# ---------------------------------------------------------------------------
# MSI install entries — (display_name, version) per profile type
# ---------------------------------------------------------------------------

_HOME_MSI_INSTALLS: List[Tuple[str, str]] = [
    ("VLC media player", "3.0.20.0"),
    ("Google Chrome", "122.0.6261.129"),
    ("Spotify", "1.2.26.1187"),
    ("7-Zip 23.01 (x64)", "23.01.00.0"),
]

_OFFICE_MSI_INSTALLS: List[Tuple[str, str]] = [
    ("Microsoft Office Professional Plus 2021", "16.0.17328.20162"),
    ("Microsoft Teams", "24004.1309.2689.2246"),
    ("Google Chrome", "122.0.6261.129"),
    ("Adobe Acrobat Reader DC", "23.8.20555.0"),
    ("Zoom", "5.17.11.24247"),
]

_DEVELOPER_MSI_INSTALLS: List[Tuple[str, str]] = [
    ("Git version 2.44.0", "2.44.0.0"),
    ("Python 3.12.2 (64-bit)", "3.12.2150.0"),
    ("Microsoft Visual Studio Code", "1.87.2"),
    ("Docker Desktop", "4.28.0"),
    ("Node.js", "20.11.1"),
    ("Postman", "10.22.10"),
]

_PROFILE_INSTALLS: Dict[str, List[Tuple[str, str]]] = {
    "home": _HOME_MSI_INSTALLS,
    "office": _OFFICE_MSI_INSTALLS,
    "developer": _DEVELOPER_MSI_INSTALLS,
}

# ---------------------------------------------------------------------------
# Application crash candidates per profile type
# ---------------------------------------------------------------------------

_HOME_CRASH_APPS: List[Dict[str, str]] = [
    {
        "app_name": "chrome.exe",
        "app_version": "122.0.6261.129",
        "module": "ntdll.dll",
        "exception": "0xc0000005",
    },
]

_OFFICE_CRASH_APPS: List[Dict[str, str]] = [
    {
        "app_name": "WINWORD.EXE",
        "app_version": "16.0.17328.20162",
        "module": "mso30win32client.dll",
        "exception": "0xc0000374",
    },
]

_DEVELOPER_CRASH_APPS: List[Dict[str, str]] = [
    {
        "app_name": "Code.exe",
        "app_version": "1.87.2",
        "module": "node.dll",
        "exception": "0x80000003",
    },
]

_PROFILE_CRASHES: Dict[str, List[Dict[str, str]]] = {
    "home": _HOME_CRASH_APPS,
    "office": _OFFICE_CRASH_APPS,
    "developer": _DEVELOPER_CRASH_APPS,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ApplicationLogError(Exception):
    """Raised when application log operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ApplicationLog(BaseService):
    """Writes synthetic Application event log entries to Application.evtx.

    Produces MSI install records, occasional app crashes, and profile-specific
    informational events.

    Args:
        evtx_writer: Core EVTX binary writer service.
        audit_logger: Shared audit logger for traceability.
    """

    def __init__(self, evtx_writer: EvtxWriter, audit_logger: Any) -> None:
        self._evtx_writer = evtx_writer
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "ApplicationLog"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type:  str — ``"home"``, ``"office"``, or ``"developer"``.
            computer_name: str — VM computer name.
            username:      str — Windows username.
            install_time:  datetime — UTC reference time for install events.

        Raises:
            ApplicationLogError: On missing context keys or write failure.
        """
        for key in ("profile_type", "computer_name", "username", "install_time"):
            if not context.get(key):
                raise ApplicationLogError(
                    f"Missing required '{key}' in context"
                )
        self.write_application_log(
            profile_type=context["profile_type"],
            computer_name=context["computer_name"],
            username=context["username"],
            install_time=context["install_time"],
        )

    # -- public API ---------------------------------------------------------

    def write_application_log(
        self,
        profile_type: str,
        computer_name: str,
        username: str,
        install_time: datetime,
    ) -> None:
        """Build and write the Application.evtx file.

        Args:
            profile_type:  One of ``"home"``, ``"office"``, ``"developer"``.
            computer_name: VM computer name.
            username:      Windows username.
            install_time:  UTC reference time for MSI install events.

        Raises:
            ApplicationLogError: On write failure.
        """
        records = self.build_records(
            profile_type, computer_name, username, install_time
        )
        try:
            self._evtx_writer.write_records(records, _APPLICATION_EVTX)
        except EvtxWriterError as exc:
            raise ApplicationLogError(
                f"Failed to write Application event log: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_application_log",
            "profile_type": profile_type,
            "computer_name": computer_name,
            "record_count": len(records),
        })
        logger.info(
            "Application log written: %d records for %s (%s)",
            len(records), computer_name, profile_type,
        )

    def build_records(
        self,
        profile_type: str,
        computer_name: str,
        username: str,
        install_time: datetime,
    ) -> List[EvtxRecord]:
        """Build all Application log records without writing.

        Pure function — suitable for isolated testing.

        Args:
            profile_type:  Profile type string.
            computer_name: VM computer name.
            username:      Windows username.
            install_time:  UTC reference time for MSI events.

        Returns:
            Ordered list of :class:`EvtxRecord`.
        """
        if install_time.tzinfo is None:
            install_time = install_time.replace(tzinfo=timezone.utc)

        rng = Random(hash(computer_name + username + profile_type))
        records: List[EvtxRecord] = []
        cursor = install_time

        # 1. MSI install events (spread over install_time + a few days)
        installs = _PROFILE_INSTALLS.get(profile_type, _HOME_MSI_INSTALLS)
        for display_name, version in installs:
            records.append(
                self._make_msi_install(computer_name, display_name, version, cursor)
            )
            cursor += timedelta(
                minutes=rng.randint(10, 120),
                seconds=rng.randint(0, 59),
            )

        # 2. One or two app crashes (realistic noise — not every machine is perfect)
        crash_list = _PROFILE_CRASHES.get(profile_type, [])
        if crash_list and rng.random() < 0.7:  # 70% chance of at least one crash entry
            crash_info = rng.choice(crash_list)
            crash_ts = cursor + timedelta(days=rng.randint(1, 7))
            records.append(
                self._make_app_crash(computer_name, crash_info, crash_ts)
            )
            fault_ts = crash_ts + timedelta(milliseconds=rng.randint(100, 800))
            records.append(
                self._make_app_fault(computer_name, crash_info, fault_ts)
            )

        return records

    # -- record builders ----------------------------------------------------

    def _make_msi_install(
        self,
        computer: str,
        display_name: str,
        version: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 11707 — Product installed successfully."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_MSI_INSTALL,
            level=4,
            provider=_PROVIDER_MSI,
            computer=computer,
            timestamp=ts,
            event_data={
                "param1": f"Product: {display_name} -- Installation completed successfully.",
                "ProductName": display_name,
                "ProductVersion": version,
                "ProductCode": f"{{{_fake_guid(display_name)}}}",
            },
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )

    def _make_app_crash(
        self,
        computer: str,
        crash_info: Dict[str, str],
        ts: datetime,
    ) -> EvtxRecord:
        """EID 1000 — Application error (crash)."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_APP_CRASH,
            level=2,
            provider=_PROVIDER_APP_ERROR,
            computer=computer,
            timestamp=ts,
            event_data={
                "AppName": crash_info["app_name"],
                "AppVersion": crash_info["app_version"],
                "AppTimeStamp": "65f2a3b4",
                "ModName": crash_info["module"],
                "ModVersion": "10.0.19041.4474",
                "ModTimeStamp": "9db0b36e",
                "ExceptionCode": crash_info["exception"],
                "FaultOffset": "0x0000000000042760",
                "ProcessId": str(4096 + abs(hash(computer)) % 60000),
            },
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )

    def _make_app_fault(
        self,
        computer: str,
        crash_info: Dict[str, str],
        ts: datetime,
    ) -> EvtxRecord:
        """EID 1001 — WER bucket entry following a 1000 crash."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_APP_FAULT,
            level=4,
            provider="Windows Error Reporting",
            computer=computer,
            timestamp=ts,
            event_data={
                "EventName": "APPCRASH",
                "ApplicationName": crash_info["app_name"],
                "ApplicationVersion": crash_info["app_version"],
                "FaultModule": crash_info["module"],
                "ExceptionCode": crash_info["exception"],
                "ReportId": f"{{{_fake_guid(computer + crash_info['app_name'])}}}",
            },
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _fake_guid(seed: str) -> str:
    """Derive a deterministic GUID string from a seed string."""
    import hashlib
    d = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return f"{d[:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}"
