"""Security event log service.

Generates synthetic Windows Security event log entries and writes them to
``Windows/System32/winevt/Logs/Security.evtx`` via :class:`EvtxWriter`.

This module is a **pure operation builder** — it builds :class:`EvtxRecord`
lists and delegates all binary I/O to the injected :class:`EvtxWriter`.

Synthetic events generated
--------------------------
* **4608** — Windows is starting up
* **4624** — Successful logon (interactive + network)
* **4634** — Logoff
* **4648** — Logon attempt using explicit credentials
* **4672** — Special privileges assigned to new logon
* **4769** — Kerberos service ticket was requested (office profile only)
* **4907** — Auditing settings changed (boot-time policy load)

Logon type reference
--------------------
* 2  = Interactive (local console)
* 3  = Network
* 7  = Unlock (screen saver dismissal)
* 10 = RemoteInteractive (RDP)
* 11 = CachedInteractive (domain cached credentials)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Optional, Tuple

from services.base_service import BaseService
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter, EvtxWriterError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECURITY_EVTX: str = "Windows/System32/winevt/Logs/Security.evtx"
_CHANNEL: str = "Security"
_PROVIDER: str = "Microsoft-Windows-Security-Auditing"

# Event IDs
_EID_STARTUP: int = 4608
_EID_LOGON: int = 4624
_EID_LOGOFF: int = 4634
_EID_EXPLICIT_CREDS: int = 4648
_EID_SPECIAL_PRIVS: int = 4672
_EID_PROCESS_CREATE: int = 4688   # §7.3 fan-out: APP_LAUNCH → Security 4688
_EID_PROCESS_EXIT: int = 4689
_EID_KERBEROS_SVC: int = 4769
_EID_AUDIT_POLICY: int = 4907

# Keywords for security audit events
_KW_AUDIT_SUCCESS: str = "0x8020000000000000"
_KW_AUDIT_FAILURE: str = "0x8010000000000000"

# Privilege set written for 4672 (typical admin session)
_SPECIAL_PRIVS: str = (
    "SeSecurityPrivilege\t\t\tSeTakeOwnershipPrivilege\t\t\t"
    "SeLoadDriverPrivilege\t\t\tSeBackupPrivilege\t\t\t"
    "SeRestorePrivilege\t\t\tSeDebugPrivilege\t\t\t"
    "SeSystemEnvironmentPrivilege\t\t\tSeImpersonatePrivilege\t\t\t"
    "SeDelegateSessionUserImpersonatePrivilege"
)

# Logon type → sessions to simulate per profile
_PROFILE_LOGON_SESSIONS: Dict[str, int] = {
    "home": 3,
    "office": 5,
    "developer": 6,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SecurityLogError(Exception):
    """Raised when security log operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SecurityLog(BaseService):
    """Writes synthetic Security event log entries to Security.evtx.

    Produces a realistic authentication trace: boot → logon → activity →
    logoff cycles, including privilege escalation markers.

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
        return "SecurityLog"

    def apply(self, ctx: "ServiceContext") -> None:
        """Execute from orchestrator context.

        Drives record generation from the EventScheduler LOGIN/LOGOFF events
        so each day in the timeline produces a full logon→activity→logoff
        sequence.  Falls back to a single session when no scheduler is present.

        Raises:
            SecurityLogError: On write failure.
        """
        profile_type = ctx.persona.profile_archetype
        username = ctx.identity_bundle.user.username
        computer_name = ctx.identity_bundle.user.computer_name
        domain = computer_name

        base_rng = (ctx.scheduler.child_rng("SecurityLog")
                    if ctx.scheduler is not None else None)

        if ctx.scheduler is not None:
            login_events = ctx.scheduler.events_of("LOGIN")
            logoff_events = ctx.scheduler.events_of("LOGOFF")
            logoff_map: dict = {}
            for ev in logoff_events:
                logoff_map.setdefault(ev.timestamp.date(), []).append(ev.timestamp)

            all_records: List[EvtxRecord] = []
            all_records.append(self._make_startup(computer_name, ctx.boot_time))
            all_records.append(self._make_audit_policy(computer_name, ctx.boot_time))

            for login_ev in login_events:
                boot_ts = login_ev.timestamp
                day_key = boot_ts.date()
                logoff_list = logoff_map.get(day_key, [])
                logoff_ts = logoff_list[0] if logoff_list else None
                all_records.extend(
                    self.build_records(
                        profile_type, username, computer_name, domain,
                        boot_ts, logoff_ts, rng=base_rng,
                    )
                )

            # §7.3 APP_LAUNCH fan-out: every APP_LAUNCH → 4688 + 4689 pair (A12)
            app_launch_events = ctx.scheduler.events_of("APP_LAUNCH")
            for ev in app_launch_events:
                app_name = ev.payload.get("app", "unknown.exe")
                pid = ev.payload.get("pid", 4096)
                all_records.append(
                    self._make_process_create(computer_name, username, app_name, pid, ev.timestamp)
                )
                # Process exit ~5-300 seconds after launch
                if base_rng:
                    dur = base_rng.randint(5, 300)
                else:
                    dur = 30
                exit_ts = ev.timestamp + timedelta(seconds=dur)
                all_records.append(
                    self._make_process_exit(computer_name, username, app_name, pid, exit_ts)
                )
        else:
            all_records = self.build_records(
                profile_type, username, computer_name, domain,
                ctx.boot_time, None,
            )

        try:
            self._evtx_writer.write_records(all_records, _SECURITY_EVTX)
        except EvtxWriterError as exc:
            raise SecurityLogError(f"Failed to write Security event log: {exc}") from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_security_log",
            "profile_type": profile_type,
            "username": username,
            "computer_name": computer_name,
            "record_count": len(all_records),
        })
        logger.info(
            "Security log written: %d records for %s@%s (%s)",
            len(all_records), username, computer_name, profile_type,
        )

    # -- public API ---------------------------------------------------------

    def build_records(
        self,
        profile_type: str,
        username: str,
        computer_name: str,
        domain: str,
        boot_time: datetime,
        logoff_time: "Optional[datetime]" = None,
        rng: "Optional[Random]" = None,
    ) -> List[EvtxRecord]:
        """Build Security log records for one logon→activity→logoff cycle.

        Pure function — suitable for isolated testing or direct calls.

        Args:
            profile_type:  Profile type string.
            username:      Windows username.
            computer_name: VM computer name.
            domain:        Domain or workgroup name.
            boot_time:     UTC logon timestamp.
            logoff_time:   UTC logoff timestamp.  If ``None`` a random
                           session length is used.
            rng:           Base RNG from the scheduler; mixed with day_seed
                           for per-day entropy.

        Returns:
            Ordered list of :class:`EvtxRecord`.
        """
        if boot_time.tzinfo is None:
            boot_time = boot_time.replace(tzinfo=timezone.utc)

        day_seed = int(boot_time.timestamp()) & 0xFFFFFFFF
        if rng is None:
            rng = Random(hash(computer_name + username + profile_type) ^ day_seed)
        else:
            rng = Random(rng.randint(0, 2**32 - 1) ^ day_seed)
        records: List[EvtxRecord] = []
        cursor = boot_time

        # Primary interactive logon
        logon_id = rng.randint(0x10000, 0xFFFFFF)
        records.append(
            self._make_logon(computer_name, username, domain, logon_id, 2, cursor)
        )
        cursor += timedelta(milliseconds=rng.randint(50, 400))
        records.append(
            self._make_special_privs(computer_name, username, domain, logon_id, cursor)
        )
        cursor += timedelta(seconds=rng.randint(1, 5))

        # Explicit-credential events during the session (scheduled tasks, runas)
        explicit_count = rng.randint(1, 4)
        session_len = (
            int((logoff_time - boot_time).total_seconds())
            if logoff_time
            else rng.randint(3600, 36000)
        )
        for _ in range(explicit_count):
            event_ts = boot_time + timedelta(seconds=rng.randint(300, max(301, session_len - 60)))
            records.append(self._make_explicit_creds(computer_name, username, domain, event_ts))

        # Kerberos tickets (office/developer profiles)
        if profile_type in ("office", "developer"):
            kerberos_count = rng.randint(2, 6)
            for _ in range(kerberos_count):
                event_ts = boot_time + timedelta(
                    seconds=rng.randint(60, max(61, session_len - 60))
                )
                records.append(
                    self._make_kerberos_svc(computer_name, username, domain, event_ts)
                )

        # SYSTEM account background logons (services, scheduled tasks)
        system_logon_count = rng.randint(2, 5)
        for _ in range(system_logon_count):
            event_ts = boot_time + timedelta(
                seconds=rng.randint(60, max(61, session_len - 60))
            )
            sys_id = rng.randint(0x3E6, 0x3E7)
            records.append(
                self._make_logon(
                    computer_name, "SYSTEM", "NT AUTHORITY",
                    sys_id, 5, event_ts,
                )
            )

        # Logoff
        logoff_ts = logoff_time or (boot_time + timedelta(seconds=session_len))
        records.append(
            self._make_logoff(computer_name, username, domain, logon_id, logoff_ts)
        )

        return records

    # -- record builders ----------------------------------------------------

    def _make_startup(self, computer: str, ts: datetime) -> EvtxRecord:
        """EID 4608 — Windows is starting up."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_STARTUP,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={},
            keywords=_KW_AUDIT_SUCCESS,
            task=12288,
            opcode=0,
        )

    def _make_audit_policy(self, computer: str, ts: datetime) -> EvtxRecord:
        """EID 4907 — Auditing settings on object were changed."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_AUDIT_POLICY,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": "S-1-5-18",
                "SubjectUserName": "SYSTEM",
                "SubjectDomainName": "NT AUTHORITY",
                "ObjectServer": "Security",
                "ObjectType": "File",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=13568,
            opcode=0,
        )

    def _make_logon(
        self,
        computer: str,
        username: str,
        domain: str,
        logon_id: int,
        logon_type: int,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4624 — An account was successfully logged on."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_LOGON,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": "S-1-0-0",
                "SubjectUserName": "-",
                "SubjectDomainName": "-",
                "SubjectLogonId": "0x0",
                "TargetUserSid": f"S-1-5-21-{abs(hash(username)) % 2**31}-1001",
                "TargetUserName": username,
                "TargetDomainName": domain,
                "TargetLogonId": hex(logon_id),
                "LogonType": str(logon_type),
                "LogonProcessName": "User32",
                "AuthenticationPackageName": "Negotiate",
                "WorkstationName": computer,
                "ProcessName": r"C:\Windows\System32\winlogon.exe",
                "IpAddress": "-",
                "IpPort": "-",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=12544,
            opcode=0,
        )

    def _make_logoff(
        self,
        computer: str,
        username: str,
        domain: str,
        logon_id: int,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4634 — An account was logged off."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_LOGOFF,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "TargetUserSid": f"S-1-5-21-{abs(hash(username)) % 2**31}-1001",
                "TargetUserName": username,
                "TargetDomainName": domain,
                "TargetLogonId": hex(logon_id),
                "LogonType": "2",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=12545,
            opcode=0,
        )

    def _make_special_privs(
        self,
        computer: str,
        username: str,
        domain: str,
        logon_id: int,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4672 — Special privileges assigned to new logon."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_SPECIAL_PRIVS,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": f"S-1-5-21-{abs(hash(username)) % 2**31}-1001",
                "SubjectUserName": username,
                "SubjectDomainName": domain,
                "SubjectLogonId": hex(logon_id),
                "PrivilegeList": _SPECIAL_PRIVS,
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=12548,
            opcode=0,
        )

    def _make_explicit_creds(
        self,
        computer: str,
        username: str,
        domain: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4648 — A logon was attempted using explicit credentials."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_EXPLICIT_CREDS,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": f"S-1-5-21-{abs(hash(username)) % 2**31}-1001",
                "SubjectUserName": username,
                "SubjectDomainName": domain,
                "SubjectLogonId": hex(0x3E7),
                "LogonGuid": "{00000000-0000-0000-0000-000000000000}",
                "TargetUserName": username,
                "TargetDomainName": domain,
                "TargetLogonGuid": "{00000000-0000-0000-0000-000000000000}",
                "ProcessId": hex(4),
                "ProcessName": r"C:\Windows\System32\svchost.exe",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=12544,
            opcode=0,
        )

    def _make_kerberos_svc(
        self,
        computer: str,
        username: str,
        domain: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4769 — A Kerberos service ticket was requested."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_KERBEROS_SVC,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "TargetUserName": f"{username}@{domain.upper()}",
                "TargetDomainName": domain.upper(),
                "ServiceName": f"host/{computer}",
                "ServiceSid": "S-1-0-0",
                "TicketOptions": "0x40810000",
                "TicketEncryptionType": "0x12",
                "IpAddress": "::1",
                "IpPort": "0",
                "Status": "0x0",
                "LogonGuid": "{00000000-0000-0000-0000-000000000000}",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=14337,
            opcode=0,
        )

    def _make_process_create(
        self,
        computer: str,
        username: str,
        app: str,
        pid: int,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4688 — A new process has been created (§7.3 APP_LAUNCH fan-out)."""
        exe = app if app.lower().endswith(".exe") else f"{app}.exe"
        exe_path = rf"C:\Windows\System32\{exe}" if "\\" not in exe else exe
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_PROCESS_CREATE,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": f"S-1-5-21-{pid}-500",
                "SubjectUserName": username,
                "SubjectDomainName": computer,
                "SubjectLogonId": f"0x{pid:x}",
                "NewProcessId": f"0x{pid:x}",
                "NewProcessName": exe_path,
                "TokenElevationType": "%%1938",
                "ProcessId": f"0x{(pid // 2):x}",
                "CommandLine": f'"{exe_path}" ',
                "TargetUserSid": "S-1-0-0",
                "TargetUserName": "-",
                "TargetDomainName": "-",
                "TargetLogonId": "0x0",
                "ParentProcessName": r"C:\Windows\System32\svchost.exe",
                "MandatoryLabel": "S-1-16-8192",
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=13312,
            opcode=0,
        )

    def _make_process_exit(
        self,
        computer: str,
        username: str,
        app: str,
        pid: int,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 4689 — A process has exited."""
        exe = app if app.lower().endswith(".exe") else f"{app}.exe"
        exe_path = rf"C:\Windows\System32\{exe}" if "\\" not in exe else exe
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_PROCESS_EXIT,
            level=4,
            provider=_PROVIDER,
            computer=computer,
            timestamp=ts,
            event_data={
                "SubjectUserSid": f"S-1-5-21-{pid}-500",
                "SubjectUserName": username,
                "SubjectDomainName": computer,
                "SubjectLogonId": f"0x{pid:x}",
                "Status": "0x0",
                "ProcessId": f"0x{pid:x}",
                "ProcessName": exe_path,
            },
            keywords=_KW_AUDIT_SUCCESS,
            task=13313,
            opcode=0,
        )
