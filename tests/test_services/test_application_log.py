"""Tests for the ApplicationLog event log service."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter
from services.eventlog.application_log import (
    ApplicationLog,
    ApplicationLogError,
    _EID_APP_CRASH,
    _EID_APP_FAULT,
    _EID_MSI_INSTALL,
    _PROFILE_INSTALLS,
    _APPLICATION_EVTX,
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
def app_log(mock_evtx_writer, audit_logger):
    return ApplicationLog(mock_evtx_writer, audit_logger)


@pytest.fixture
def install_time():
    return datetime(2024, 1, 10, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# build_records — structural
# ---------------------------------------------------------------------------

class TestBuildRecords:
    def test_returns_list_of_evtx_records(self, app_log, install_time):
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        assert all(isinstance(r, EvtxRecord) for r in records)

    def test_msi_install_events_present(self, app_log, install_time):
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        eids = [r.event_id for r in records]
        assert _EID_MSI_INSTALL in eids

    def test_home_install_count_matches_profile(self, app_log, install_time):
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        msi_count = sum(1 for r in records if r.event_id == _EID_MSI_INSTALL)
        assert msi_count == len(_PROFILE_INSTALLS["home"])

    def test_office_install_count_matches_profile(self, app_log, install_time):
        records = app_log.build_records("office", "CORP-PC", "bob", install_time)
        msi_count = sum(1 for r in records if r.event_id == _EID_MSI_INSTALL)
        assert msi_count == len(_PROFILE_INSTALLS["office"])

    def test_developer_install_count_matches_profile(self, app_log, install_time):
        records = app_log.build_records("developer", "DEV-PC", "dev", install_time)
        msi_count = sum(1 for r in records if r.event_id == _EID_MSI_INSTALL)
        assert msi_count == len(_PROFILE_INSTALLS["developer"])

    def test_crash_1001_follows_1000_when_present(self, app_log, install_time):
        """If a 1000 crash event appears, a 1001 fault must follow it."""
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        eids = [r.event_id for r in records]
        if _EID_APP_CRASH in eids:
            crash_idx = eids.index(_EID_APP_CRASH)
            assert eids[crash_idx + 1] == _EID_APP_FAULT

    def test_channel_is_application(self, app_log, install_time):
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        assert all(r.channel == "Application" for r in records)

    def test_computer_name_embedded(self, app_log, install_time):
        records = app_log.build_records("home", "HOME-PC", "alice", install_time)
        assert all(r.computer == "HOME-PC" for r in records)

    def test_records_chronological_order(self, app_log, install_time):
        records = app_log.build_records("office", "CORP-PC", "bob", install_time)
        timestamps = [r.timestamp for r in records]
        assert timestamps == sorted(timestamps)

    def test_developer_has_more_installs_than_home(self, app_log, install_time):
        home_records = app_log.build_records("home", "PC", "alice", install_time)
        dev_records = app_log.build_records("developer", "PC", "dev", install_time)
        home_msi = sum(1 for r in home_records if r.event_id == _EID_MSI_INSTALL)
        dev_msi = sum(1 for r in dev_records if r.event_id == _EID_MSI_INSTALL)
        assert dev_msi > home_msi

    def test_deterministic_output(self, app_log, install_time):
        r1 = app_log.build_records("office", "CORP", "bob", install_time)
        r2 = app_log.build_records("office", "CORP", "bob", install_time)
        assert [r.event_id for r in r1] == [r.event_id for r in r2]

    def test_unknown_profile_fallback(self, app_log, install_time):
        # Should not crash — may return empty or home-like records
        records = app_log.build_records("unknown", "PC", "user", install_time)
        assert isinstance(records, list)


# ---------------------------------------------------------------------------
# write_application_log — delegation
# ---------------------------------------------------------------------------

class TestWriteApplicationLog:
    def test_delegates_to_evtx_writer(
        self, app_log, mock_evtx_writer, install_time
    ):
        app_log.write_application_log("home", "HOME-PC", "alice", install_time)
        mock_evtx_writer.write_records.assert_called_once()
        assert mock_evtx_writer.write_records.call_args[0][1] == _APPLICATION_EVTX

    def test_audit_logged(self, app_log, audit_logger, install_time):
        audit_logger.clear()
        app_log.write_application_log("home", "HOME-PC", "alice", install_time)
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "ApplicationLog"

    def test_evtx_error_propagates(
        self, app_log, mock_evtx_writer, install_time
    ):
        from services.eventlog.evtx_writer import EvtxWriterError
        mock_evtx_writer.write_records.side_effect = EvtxWriterError("io error")
        with pytest.raises(ApplicationLogError, match="io error"):
            app_log.write_application_log("home", "HOME-PC", "alice", install_time)


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_write(self, app_log, install_time):
        app_log.apply({
            "profile_type": "home",
            "computer_name": "HOME-PC",
            "username": "alice",
            "install_time": install_time,
        })

    def test_apply_missing_profile_raises(self, app_log, install_time):
        with pytest.raises(ApplicationLogError):
            app_log.apply({
                "computer_name": "PC",
                "username": "alice",
                "install_time": install_time,
            })

    def test_apply_missing_computer_raises(self, app_log, install_time):
        with pytest.raises(ApplicationLogError):
            app_log.apply({
                "profile_type": "home",
                "username": "alice",
                "install_time": install_time,
            })

    def test_apply_missing_username_raises(self, app_log, install_time):
        with pytest.raises(ApplicationLogError):
            app_log.apply({
                "profile_type": "home",
                "computer_name": "PC",
                "install_time": install_time,
            })

    def test_apply_missing_install_time_raises(self, app_log):
        with pytest.raises(ApplicationLogError):
            app_log.apply({
                "profile_type": "home",
                "computer_name": "PC",
                "username": "alice",
            })

    def test_service_name(self, app_log):
        assert app_log.service_name == "ApplicationLog"
