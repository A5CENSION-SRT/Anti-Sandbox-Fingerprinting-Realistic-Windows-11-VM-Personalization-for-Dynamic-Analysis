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
from typing import Any, Dict, List, Tuple

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

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type:  str — ``"home"``, ``"office"``, or ``"developer"``.
            username:      str — Windows username (e.g. ``"jane.doe"``).
            computer_name: str — VM computer name.
            domain:        str — domain name or computer name for local accounts.
            boot_time:     datetime — UTC boot timestamp.

        Raises:
            SecurityLogError: On missing context keys or write failure.
        """
        for key in ("profile_type", "username", "computer_name", "boot_time"):
            if not context.get(key):
                raise SecurityLogError(
                    f"Missing required '{key}' in context"
                )
        self.write_security_log(
            profile_type=context["profile_type"],
            username=context["username"],
            computer_name=context["computer_name"],
            domain=context.get("domain", context["computer_name"]),
            boot_time=context["boot_time"],
        )

    # -- public API ---------------------------------------------------------

    def write_security_log(
        self,
        profile_type: str,
        username: str,
        computer_name: str,
        domain: str,
        boot_time: datetime,
    ) -> None:
        """Build and write the Security.evtx file.

        Args:
            profile_type:  One of ``"home"``, ``"office"``, ``"developer"``.
            username:      Windows username.
            computer_name: VM computer name.
            domain:        Domain or workgroup name.
            boot_time:     UTC datetime of the simulated boot.

        Raises:
            SecurityLogError: On write failure.
        """
        records = self.build_records(
            profile_type, username, computer_name, domain, boot_time
        )
        try:
            self._evtx_writer.write_records(records, _SECURITY_EVTX)
        except EvtxWriterError as exc:
            raise SecurityLogError(
                f"Failed to write Security event log: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_security_log",
            "profile_type": profile_type,
            "username": username,
            "computer_name": computer_name,
            "record_count": len(records),
        })
        logger.info(
            "Security log written: %d records for %s@%s (%s)",
            len(records), username, computer_name, profile_type,
        )

    def build_records(
        self,
        profile_type: str,
        username: str,
        computer_name: str,
        domain: str,
        boot_time: datetime,
    ) -> List[EvtxRecord]:
        """Build all Security log records without writing.

        Pure function — suitable for isolated testing.

        Args:
            profile_type:  Profile type string.
            username:      Windows username.
            computer_name: VM computer name.
            domain:        Domain or workgroup name.
            boot_time:     UTC boot timestamp.

        Returns:
            Ordered list of :class:`EvtxRecord`.
        """
        if boot_time.tzinfo is None:
            boot_time = boot_time.replace(tzinfo=timezone.utc)

        rng = Random(hash(computer_name + username + profile_type))
        records: List[EvtxRecord] = []
        cursor = boot_time

        # 1. Windows startup event
        records.append(self._make_startup(computer_name, cursor))
        cursor += timedelta(seconds=rng.randint(2, 5))

        # 2. Audit policy change at boot
        records.append(self._make_audit_policy(computer_name, cursor))
        cursor += timedelta(seconds=rng.randint(1, 3))

        # 3. Logon sessions
        session_count = _PROFILE_LOGON_SESSIONS.get(profile_type, 3)
        for i in range(session_count):
            logon_id = rng.randint(0x10000, 0xFFFFFF)
            logon_type = rng.choice([2, 2, 7, 11] if i > 0 else [2])

            logon_ts = cursor + timedelta(
                minutes=rng.randint(5, 90) if i > 0 else 0
            )
            cursor = logon_ts

            records.append(
                self._make_logon(
                    computer_name, username, domain,
                    logon_id, logon_type, cursor,
                )
            )
            cursor += timedelta(milliseconds=rng.randint(50, 400))

            records.append(
                self._make_special_privs(
                    computer_name, username, domain, logon_id, cursor
                )
            )
            cursor += timedelta(
                minutes=rng.randint(30, 180),
                seconds=rng.randint(0, 59),
            )

            records.append(
                self._make_logoff(computer_name, username, domain, logon_id, cursor)
            )
            cursor += timedelta(seconds=rng.randint(1, 10))

        # 4. Kerberos service ticket (office/developer profiles only)
        if profile_type in ("office", "developer"):
            records.append(
                self._make_kerberos_svc(computer_name, username, domain, cursor)
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
