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
* **37**   — Time Provider NtpClient synced clock
* **12**   — Kernel-Power: system starts
* **13**   — Kernel-Power: system shutdown

Provider context
----------------
* 6005/6006  → ``Microsoft-Windows-EventLog``
* 7001/7036/7040 → ``Service Control Manager``
* 37 → ``Microsoft-Windows-Time-Service``
* 12/13 → ``Microsoft-Windows-Kernel-Power``

Density target: one full boot+runtime+shutdown sequence per scheduler
LOGIN event → 360 days × ~50 records ≈ 18,000 records ≈ 6-10 MB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
_EID_TIME_SYNC: int = 37
_EID_KERNEL_BOOT: int = 12
_EID_KERNEL_SHUTDOWN: int = 13

_PROVIDER_TIME: str = "Microsoft-Windows-Time-Service"
_PROVIDER_KERNEL_POWER: str = "Microsoft-Windows-Kernel-Power"

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

    def apply(self, ctx: "ServiceContext") -> None:
        """Execute from orchestrator context.

        Drives record generation from the EventScheduler LOGIN events so
        each day in the timeline produces a full boot→services→shutdown
        sequence.  Falls back to a single boot cycle when no scheduler is
        present.

        Raises:
            SystemLogError: On write failure.
        """
        profile_type = ctx.persona.profile_archetype
        computer_name = ctx.identity_bundle.user.computer_name

        base_rng = (ctx.scheduler.child_rng("SystemLog")
                    if ctx.scheduler is not None else None)

        if ctx.scheduler is not None:
            login_events = ctx.scheduler.events_of("LOGIN")
            logoff_events = ctx.scheduler.events_of("LOGOFF")
            logoff_by_day: dict = {}
            for ev in logoff_events:
                day_key = ev.timestamp.date()
                logoff_by_day.setdefault(day_key, []).append(ev.timestamp)

            all_records: List[EvtxRecord] = []
            for login_ev in login_events:
                boot_ts = login_ev.timestamp
                day_key = boot_ts.date()
                logoff_list = logoff_by_day.get(day_key, [])
                logoff_ts = logoff_list[0] if logoff_list else None
                all_records.extend(
                    self.build_records(profile_type, computer_name, boot_ts, logoff_ts,
                                       rng=base_rng)
                )
        else:
            all_records = self.build_records(
                profile_type, computer_name, ctx.boot_time, None
            )

        try:
            self._evtx_writer.write_records(all_records, _SYSTEM_EVTX)
        except EvtxWriterError as exc:
            raise SystemLogError(f"Failed to write System event log: {exc}") from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_system_log",
            "profile_type": profile_type,
            "computer_name": computer_name,
            "record_count": len(all_records),
        })
        logger.info(
            "System log written: %d records for %s (%s)",
            len(all_records), computer_name, profile_type,
        )

    # -- public API ---------------------------------------------------------

    def build_records(
        self,
        profile_type: str,
        computer_name: str,
        boot_time: datetime,
        logoff_time: "Optional[datetime]" = None,
        rng: "Optional[Random]" = None,
    ) -> List[EvtxRecord]:
        """Build System log records for one boot→runtime→shutdown cycle.

        Pure function — suitable for isolated testing or direct calls.

        Args:
            profile_type:  Profile type string.
            computer_name: VM computer name.
            boot_time:     UTC boot timestamp.
            logoff_time:   UTC logoff/shutdown timestamp.  If ``None`` a
                           random session length (4–10 h) is used.
            rng:           Base RNG from the scheduler; mixed with day_seed
                           for per-day entropy.

        Returns:
            Ordered list of :class:`EvtxRecord`.
        """
        if boot_time.tzinfo is None:
            boot_time = boot_time.replace(tzinfo=timezone.utc)

        day_seed = int(boot_time.timestamp()) & 0xFFFFFFFF
        if rng is None:
            rng = Random(hash(computer_name + profile_type) ^ day_seed)
        else:
            rng = Random(rng.randint(0, 2**32 - 1) ^ day_seed)
        records: List[EvtxRecord] = []
        cursor = boot_time

        # Kernel power: system starts
        records.append(self._make_kernel_power(computer_name, _EID_KERNEL_BOOT, cursor))
        cursor += timedelta(milliseconds=rng.randint(200, 800))

        # EventLog service starts
        records.append(self._make_eventlog_start(computer_name, cursor))
        cursor += timedelta(seconds=rng.randint(1, 3))

        # Core + profile services start
        services = list(_CORE_SERVICES)
        services.extend(_PROFILE_EXTRA_SERVICES.get(profile_type, []))

        for svc_name, svc_display in services:
            records.append(
                self._make_service_started(computer_name, svc_name, svc_display, cursor)
            )
            cursor += timedelta(seconds=rng.randint(1, 8))
            records.append(
                self._make_service_state(computer_name, svc_display, "running", cursor)
            )
            cursor += timedelta(seconds=rng.randint(1, 4))

        # NTP time sync shortly after boot
        cursor += timedelta(seconds=rng.randint(30, 120))
        records.append(self._make_time_sync(computer_name, cursor))

        # Mid-session service chatter — random service restarts during uptime
        if logoff_time is None:
            session_end = boot_time + timedelta(
                hours=rng.randint(4, 10), minutes=rng.randint(0, 59)
            )
        else:
            session_end = logoff_time

        mid_session_events = rng.randint(3, 8)
        for _ in range(mid_session_events):
            event_ts = boot_time + timedelta(
                seconds=rng.randint(
                    300,
                    max(301, int((session_end - boot_time).total_seconds()) - 60),
                )
            )
            svc_name, svc_display = rng.choice(services)
            records.append(
                self._make_service_state(computer_name, svc_display, "stopped", event_ts)
            )
            event_ts += timedelta(seconds=rng.randint(2, 15))
            records.append(
                self._make_service_state(computer_name, svc_display, "running", event_ts)
            )

        # Second NTP sync mid-session
        mid_sync_ts = boot_time + timedelta(
            seconds=rng.randint(
                900,
                max(901, int((session_end - boot_time).total_seconds()) // 2),
            )
        )
        records.append(self._make_time_sync(computer_name, mid_sync_ts))

        # Shutdown sequence
        shutdown_cursor = session_end
        for svc_name, svc_display in reversed(services):
            records.append(
                self._make_service_state(computer_name, svc_display, "stopped", shutdown_cursor)
            )
            shutdown_cursor += timedelta(seconds=rng.randint(1, 5))

        records.append(self._make_eventlog_stop(computer_name, shutdown_cursor))
        shutdown_cursor += timedelta(milliseconds=rng.randint(100, 500))
        records.append(
            self._make_kernel_power(computer_name, _EID_KERNEL_SHUTDOWN, shutdown_cursor)
        )

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

    def _make_time_sync(self, computer: str, ts: datetime) -> EvtxRecord:
        """EID 37 — NtpClient synced clock with NTP server."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_TIME_SYNC,
            level=4,
            provider=_PROVIDER_TIME,
            computer=computer,
            timestamp=ts,
            event_data={
                "TimeSource": "time.windows.com,0x9",
            },
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )

    def _make_kernel_power(
        self, computer: str, event_id: int, ts: datetime
    ) -> EvtxRecord:
        """EID 12 (boot) or 13 (shutdown) — Kernel-Power lifecycle."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=event_id,
            level=4,
            provider=_PROVIDER_KERNEL_POWER,
            computer=computer,
            timestamp=ts,
            event_data={},
            keywords="0x8000000000000000",
            task=0,
            opcode=0,
        )
