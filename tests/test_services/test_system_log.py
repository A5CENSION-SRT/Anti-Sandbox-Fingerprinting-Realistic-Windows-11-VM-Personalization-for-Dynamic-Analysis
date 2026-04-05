"""Tests for the SystemLog event log service."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call

import pytest

from core.audit_logger import AuditLogger
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter
from services.eventlog.system_log import (
    SystemLog,
    SystemLogError,
    _EID_EVENTLOG_START,
    _EID_EVENTLOG_STOP,
    _EID_SERVICE_STARTED,
    _EID_SERVICE_STATE,
    _SYSTEM_EVTX,
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
def system_log(mock_evtx_writer, audit_logger):
    return SystemLog(mock_evtx_writer, audit_logger)


@pytest.fixture
def boot_time():
    return datetime(2024, 3, 15, 8, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# build_records — structural
# ---------------------------------------------------------------------------

class TestBuildRecords:
    def test_returns_list_of_evtx_records(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert all(isinstance(r, EvtxRecord) for r in records)

    def test_starts_with_eventlog_start(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert records[0].event_id == _EID_EVENTLOG_START

    def test_ends_with_eventlog_stop(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert records[-1].event_id == _EID_EVENTLOG_STOP

    def test_computer_name_embedded(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert all(r.computer == "HOME-PC01" for r in records)

    def test_records_chronological_order(self, system_log, boot_time):
        records = system_log.build_records("office", "CORP-LT-7", boot_time)
        timestamps = [r.timestamp for r in records]
        assert timestamps == sorted(timestamps)

    def test_boot_time_is_first_timestamp(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert records[0].timestamp == boot_time

    def test_shutdown_after_boot(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        stop = records[-1]
        assert stop.timestamp > boot_time

    def test_service_started_events_present(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        eids = [r.event_id for r in records]
        assert _EID_SERVICE_STARTED in eids

    def test_service_state_events_present(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        eids = [r.event_id for r in records]
        assert _EID_SERVICE_STATE in eids

    def test_channel_is_system(self, system_log, boot_time):
        records = system_log.build_records("home", "HOME-PC01", boot_time)
        assert all(r.channel == "System" for r in records)

    def test_developer_has_more_records_than_home(self, system_log, boot_time):
        home_records = system_log.build_records("home", "DEV-PC", boot_time)
        dev_records = system_log.build_records("developer", "DEV-PC", boot_time)
        assert len(dev_records) > len(home_records)

    def test_unknown_profile_falls_back(self, system_log, boot_time):
        # unknown profile should not crash — falls back to empty extras
        records = system_log.build_records("unknown", "ANON-PC", boot_time)
        assert len(records) >= 2

    def test_deterministic_output(self, system_log, boot_time):
        r1 = system_log.build_records("office", "CORP-PC", boot_time)
        r2 = system_log.build_records("office", "CORP-PC", boot_time)
        assert [r.event_id for r in r1] == [r.event_id for r in r2]


# ---------------------------------------------------------------------------
# write_system_log — delegation
# ---------------------------------------------------------------------------

class TestWriteSystemLog:
    def test_delegates_to_evtx_writer(self, system_log, mock_evtx_writer, boot_time):
        system_log.write_system_log("home", "HOME-PC", boot_time)
        mock_evtx_writer.write_records.assert_called_once()
        args = mock_evtx_writer.write_records.call_args
        assert args[0][1] == _SYSTEM_EVTX

    def test_audit_logged(self, system_log, audit_logger, boot_time):
        audit_logger.clear()
        system_log.write_system_log("home", "HOME-PC", boot_time)
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "SystemLog"

    def test_evtx_writer_error_propagates(self, system_log, mock_evtx_writer, boot_time):
        from services.eventlog.evtx_writer import EvtxWriterError
        mock_evtx_writer.write_records.side_effect = EvtxWriterError("disk full")
        with pytest.raises(SystemLogError, match="disk full"):
            system_log.write_system_log("home", "HOME-PC", boot_time)


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_write(self, system_log, boot_time):
        ctx = {
            "profile_type": "home",
            "computer_name": "HOME-PC",
            "boot_time": boot_time,
        }
        system_log.apply(ctx)

    def test_apply_missing_profile_raises(self, system_log, boot_time):
        with pytest.raises(SystemLogError):
            system_log.apply({"computer_name": "PC", "boot_time": boot_time})

    def test_apply_missing_computer_name_raises(self, system_log, boot_time):
        with pytest.raises(SystemLogError):
            system_log.apply({"profile_type": "home", "boot_time": boot_time})

    def test_apply_missing_boot_time_raises(self, system_log):
        with pytest.raises(SystemLogError):
            system_log.apply({"profile_type": "home", "computer_name": "PC"})

    def test_service_name(self, system_log):
        assert system_log.service_name == "SystemLog"
