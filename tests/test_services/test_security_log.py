"""Tests for the SecurityLog event log service."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter
from services.eventlog.security_log import (
    SecurityLog,
    SecurityLogError,
    _EID_STARTUP,
    _EID_LOGON,
    _EID_LOGOFF,
    _EID_SPECIAL_PRIVS,
    _EID_KERBEROS_SVC,
    _EID_AUDIT_POLICY,
    _PROFILE_LOGON_SESSIONS,
    _SECURITY_EVTX,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def mock_evtx_writer():
    return MagicMock(spec=EvtxWriter)


@pytest.fixture
def security_log(mock_evtx_writer, audit_logger):
    return SecurityLog(mock_evtx_writer, audit_logger)


@pytest.fixture
def boot_time():
    return datetime(2024, 3, 15, 7, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# build_records — structure
# ---------------------------------------------------------------------------

class TestBuildRecords:
    def test_returns_list_of_evtx_records(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        assert all(isinstance(r, EvtxRecord) for r in records)

    def test_first_event_is_startup_4608(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        assert records[0].event_id == _EID_STARTUP

    def test_channel_is_security(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        assert all(r.channel == "Security" for r in records)

    def test_computer_name_embedded(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        assert all(r.computer == "HOME-PC" for r in records)

    def test_records_chronological_order(self, security_log, boot_time):
        records = security_log.build_records(
            "office", "bob", "CORP-PC", "CONTOSO", boot_time
        )
        timestamps = [r.timestamp for r in records]
        assert timestamps == sorted(timestamps)

    def test_logon_events_present(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        assert _EID_LOGON in eids

    def test_logoff_events_present(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        assert _EID_LOGOFF in eids

    def test_special_privs_present(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        assert _EID_SPECIAL_PRIVS in eids

    def test_audit_policy_event_present(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        assert _EID_AUDIT_POLICY in eids

    def test_logon_logoff_balanced(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        assert eids.count(_EID_LOGON) == eids.count(_EID_LOGOFF)

    def test_kerberos_only_for_office(self, security_log, boot_time):
        office_records = security_log.build_records(
            "office", "bob", "CORP-PC", "CONTOSO", boot_time
        )
        home_records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        office_eids = [r.event_id for r in office_records]
        home_eids = [r.event_id for r in home_records]
        assert _EID_KERBEROS_SVC in office_eids
        assert _EID_KERBEROS_SVC not in home_eids

    def test_kerberos_for_developer(self, security_log, boot_time):
        records = security_log.build_records(
            "developer", "dev", "DEV-PC", "CONTOSO", boot_time
        )
        eids = [r.event_id for r in records]
        assert _EID_KERBEROS_SVC in eids

    def test_home_logon_session_count(self, security_log, boot_time):
        records = security_log.build_records(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        eids = [r.event_id for r in records]
        logon_count = eids.count(_EID_LOGON)
        assert logon_count == _PROFILE_LOGON_SESSIONS["home"]

    def test_office_logon_session_count(self, security_log, boot_time):
        records = security_log.build_records(
            "office", "bob", "CORP-PC", "CONTOSO", boot_time
        )
        eids = [r.event_id for r in records]
        logon_count = eids.count(_EID_LOGON)
        assert logon_count == _PROFILE_LOGON_SESSIONS["office"]

    def test_developer_logon_session_count(self, security_log, boot_time):
        records = security_log.build_records(
            "developer", "dev", "DEV-PC", "CONTOSO", boot_time
        )
        eids = [r.event_id for r in records]
        logon_count = eids.count(_EID_LOGON)
        assert logon_count == _PROFILE_LOGON_SESSIONS["developer"]

    def test_developer_has_most_sessions(self, security_log, boot_time):
        home = security_log.build_records("home", "a", "PC", "WG", boot_time)
        office = security_log.build_records("office", "b", "PC", "WG", boot_time)
        dev = security_log.build_records("developer", "c", "PC", "WG", boot_time)
        assert len(dev) > len(office) > len(home)

    def test_deterministic_output(self, security_log, boot_time):
        r1 = security_log.build_records("office", "bob", "CORP", "DOM", boot_time)
        r2 = security_log.build_records("office", "bob", "CORP", "DOM", boot_time)
        assert [r.event_id for r in r1] == [r.event_id for r in r2]


# ---------------------------------------------------------------------------
# write_security_log — delegation
# ---------------------------------------------------------------------------

class TestWriteSecurityLog:
    def test_delegates_to_evtx_writer(self, security_log, mock_evtx_writer, boot_time):
        security_log.write_security_log(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        mock_evtx_writer.write_records.assert_called_once()
        assert mock_evtx_writer.write_records.call_args[0][1] == _SECURITY_EVTX

    def test_audit_logged(self, security_log, audit_logger, boot_time):
        audit_logger.clear()
        security_log.write_security_log(
            "home", "alice", "HOME-PC", "WORKGROUP", boot_time
        )
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "SecurityLog"

    def test_evtx_error_propagates(self, security_log, mock_evtx_writer, boot_time):
        from services.eventlog.evtx_writer import EvtxWriterError
        mock_evtx_writer.write_records.side_effect = EvtxWriterError("io error")
        with pytest.raises(SecurityLogError, match="io error"):
            security_log.write_security_log(
                "home", "alice", "HOME-PC", "WORKGROUP", boot_time
            )


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_write(self, security_log, boot_time):
        security_log.apply({
            "profile_type": "home",
            "username": "alice",
            "computer_name": "HOME-PC",
            "boot_time": boot_time,
        })

    def test_apply_uses_computer_name_as_domain_default(
        self, security_log, mock_evtx_writer, boot_time
    ):
        security_log.apply({
            "profile_type": "home",
            "username": "alice",
            "computer_name": "HOME-PC",
            "boot_time": boot_time,
        })
        mock_evtx_writer.write_records.assert_called_once()

    def test_apply_missing_profile_raises(self, security_log, boot_time):
        with pytest.raises(SecurityLogError):
            security_log.apply({
                "username": "alice",
                "computer_name": "PC",
                "boot_time": boot_time,
            })

    def test_apply_missing_username_raises(self, security_log, boot_time):
        with pytest.raises(SecurityLogError):
            security_log.apply({
                "profile_type": "home",
                "computer_name": "PC",
                "boot_time": boot_time,
            })

    def test_apply_missing_boot_time_raises(self, security_log):
        with pytest.raises(SecurityLogError):
            security_log.apply({
                "profile_type": "home",
                "username": "alice",
                "computer_name": "PC",
            })

    def test_service_name(self, security_log):
        assert security_log.service_name == "SecurityLog"
