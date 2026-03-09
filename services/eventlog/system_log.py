"""System event log service.

Generates synthetic Windows System event log entries and writes them to
``Windows/System32/winevt/Logs/System.evtx`` via :class:`EvtxWriter`.

This module is a **pure operation builder** — it builds :class:`EvtxRecord`
lists and delegates all binary I/O to the injected :class:`EvtxWriter`.

Synthetic events generated
--------------------------
* **6005** — Event Log Service started  (boot boundary marker)
* **7001** — Service Control Manager: service started  (critical services)
* **7036** — Service entered running/stopped state    (service chatter)
* **7040** — Service start type changed               (optional profile)
* **6006** — Event Log Service stopped  (clean-shutdown marker)

Provider context
----------------
* 6005/6006  → ``Microsoft-Windows-EventLog``
* 7001/7036/7040 → ``Service Control Manager``
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Sequence, Tuple

from services.base_service import BaseService
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter, EvtxWriterError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYSTEM_EVTX: str = "Windows/System32/winevt/Logs/System.evtx"
_CHANNEL: str = "System"

_PROVIDER_EVENTLOG: str = "Microsoft-Windows-EventLog"
_PROVIDER_SCM: str = "Service Control Manager"

_EID_EVENTLOG_START: int = 6005
_EID_EVENTLOG_STOP: int = 6006
_EID_SERVICE_STARTED: int = 7001
_EID_SERVICE_STOPPED: int = 7002
_EID_SERVICE_STATE: int = 7036
_EID_SERVICE_START_TYPE: int = 7040

# (service_name, display_name) pairs written on every profile
_CORE_SERVICES: List[Tuple[str, str]] = [
    ("LanmanWorkstation", "Workstation"),
    ("Dnscache", "DNS Client"),
    ("nsi", "Network Store Interface Service"),
    ("Winmgmt", "Windows Management Instrumentation"),
    ("Spooler", "Print Spooler"),
    ("EventLog", "Windows Event Log"),
    ("wuauserv", "Windows Update"),
    ("WSearch", "Windows Search"),
]

# Extra services added per profile type
_DEVELOPER_SERVICES: List[Tuple[str, str]] = [
    ("Docker Desktop Service", "Docker Desktop Service"),
    ("com.docker.service", "Docker Desktop Backend"),
    ("ssh-agent", "OpenSSH Authentication Agent"),
]

_HOME_SERVICES: List[Tuple[str, str]] = [
    ("OneDrive Updater Service", "Microsoft OneDrive Updater Service"),
    ("WMPNetworkSvc", "Windows Media Player Network Sharing Service"),
]

_OFFICE_SERVICES: List[Tuple[str, str]] = [
    ("ClickToRunSvc", "Microsoft Office Click-to-Run Service"),
    ("MozillaMaintenance", "Mozilla Maintenance Service"),
]

_PROFILE_EXTRA_SERVICES: Dict[str, List[Tuple[str, str]]] = {
    "developer": _DEVELOPER_SERVICES,
    "home": _HOME_SERVICES,
    "office": _OFFICE_SERVICES,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SystemLogError(Exception):
    """Raised when system log operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SystemLog(BaseService):
    """Writes synthetic System event log entries to System.evtx.

    Produces a realistic boot→services→shutdown event sequence.
    All binary I/O is delegated to the injected :class:`EvtxWriter`.

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
        return "SystemLog"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type:  str — ``"home"``, ``"office"``, or ``"developer"``.
            computer_name: str — the VM computer name.
            boot_time:     datetime — UTC boot timestamp.

        Raises:
            SystemLogError: On missing context keys or write failure.
        """
        profile_type = context.get("profile_type")
        if not profile_type:
            raise SystemLogError("Missing required 'profile_type' in context")
        computer_name = context.get("computer_name")
        if not computer_name:
            raise SystemLogError("Missing required 'computer_name' in context")
        boot_time = context.get("boot_time")
        if boot_time is None:
            raise SystemLogError("Missing required 'boot_time' in context")

        self.write_system_log(profile_type, computer_name, boot_time)

    # -- public API ---------------------------------------------------------

    def write_system_log(
        self,
        profile_type: str,
        computer_name: str,
        boot_time: datetime,
    ) -> None:
        """Build and write the System.evtx file.

        Args:
            profile_type:  One of ``"home"``, ``"office"``, ``"developer"``.
            computer_name: The VM computer name.
            boot_time:     UTC datetime of the simulated system boot.

        Raises:
            SystemLogError: On write failure.
        """
        records = self.build_records(profile_type, computer_name, boot_time)
        try:
            self._evtx_writer.write_records(records, _SYSTEM_EVTX)
        except EvtxWriterError as exc:
            raise SystemLogError(
                f"Failed to write System event log: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_system_log",
            "profile_type": profile_type,
            "computer_name": computer_name,
            "record_count": len(records),
        })
        logger.info(
            "System log written: %d records for %s (%s)",
            len(records), computer_name, profile_type,
        )

    def build_records(
        self,
        profile_type: str,
        computer_name: str,
        boot_time: datetime,
    ) -> List[EvtxRecord]:
        """Build all System log records without writing.

        This is a pure function — suitable for isolated testing.

        Args:
            profile_type:  Profile type string.
            computer_name: VM computer name.
            boot_time:     UTC boot timestamp.

        Returns:
            Ordered list of :class:`EvtxRecord` objects.
        """
        if boot_time.tzinfo is None:
            boot_time = boot_time.replace(tzinfo=timezone.utc)

        rng = Random(hash(computer_name + profile_type))
        records: List[EvtxRecord] = []
        cursor = boot_time

        # 1. EventLog service starts (marks boot start)
        records.append(self._make_eventlog_start(computer_name, cursor))
        cursor += timedelta(seconds=rng.randint(1, 3))

        # 2. Core services start up
        services = list(_CORE_SERVICES)
        extra = _PROFILE_EXTRA_SERVICES.get(profile_type, [])
        services.extend(extra)

        for svc_name, svc_display in services:
            records.append(
                self._make_service_started(computer_name, svc_name, svc_display, cursor)
            )
            cursor += timedelta(seconds=rng.randint(1, 8))
            records.append(
                self._make_service_state(
                    computer_name, svc_display, "running", cursor
                )
            )
            cursor += timedelta(seconds=rng.randint(1, 4))

        # 3. Simulate a clean shutdown sequence at cursor + ~8h runtime
        shutdown_time = boot_time + timedelta(
            hours=rng.randint(4, 10),
            minutes=rng.randint(0, 59),
        )
        shutdown_cursor = shutdown_time

        for svc_name, svc_display in reversed(services):
            records.append(
                self._make_service_state(
                    computer_name, svc_display, "stopped", shutdown_cursor
                )
            )
            shutdown_cursor += timedelta(seconds=rng.randint(1, 5))

        records.append(self._make_eventlog_stop(computer_name, shutdown_cursor))

        return records

    # -- record builders ----------------------------------------------------

    def _make_eventlog_start(
        self, computer: str, ts: datetime
    ) -> EvtxRecord:
        """EID 6005 — The Event log service was started."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_EVENTLOG_START,
            level=4,
            provider=_PROVIDER_EVENTLOG,
            computer=computer,
            timestamp=ts,
            event_data={},
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )

    def _make_eventlog_stop(
        self, computer: str, ts: datetime
    ) -> EvtxRecord:
        """EID 6006 — The Event log service was stopped."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_EVENTLOG_STOP,
            level=4,
            provider=_PROVIDER_EVENTLOG,
            computer=computer,
            timestamp=ts,
            event_data={},
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )

    def _make_service_started(
        self,
        computer: str,
        service_name: str,
        display_name: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 7001 — Service successfully started."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_SERVICE_STARTED,
            level=4,
            provider=_PROVIDER_SCM,
            computer=computer,
            timestamp=ts,
            event_data={
                "param1": display_name,
                "param2": service_name,
            },
            keywords="0x8080000000000000",
            task=0,
            opcode=0,
        )

    def _make_service_state(
        self,
        computer: str,
        display_name: str,
        state: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 7036 — Service entered the running/stopped state."""
        state_str = "running" if state == "running" else "stopped"
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_SERVICE_STATE,
            level=4,
            provider=_PROVIDER_SCM,
            computer=computer,
            timestamp=ts,
            event_data={
                "param1": display_name,
                "param2": state_str,
            },
            keywords="0x8080000000000000",
            task=0,
            opcode=0,
        )
