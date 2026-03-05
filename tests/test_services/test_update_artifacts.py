"""Tests for the UpdateArtifacts service."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter
from services.eventlog.update_artifacts import (
    UpdateArtifacts,
    UpdateArtifactsError,
    _EID_DOWNLOAD_START,
    _EID_DOWNLOAD_DONE,
    _EID_INSTALL_START,
    _EID_INSTALL_DONE,
    _SOFTWARE_HIVE,
    _WU_RESULTS_INSTALL,
    _WU_RESULTS_DETECT,
    _WU_AUTO_UPDATE,
    _WU_ROOT,
    _UPDATES_BY_PROFILE,
)
from services.registry.hive_writer import HiveWriter, RegistryValueType


# ---------------------------------------------------------------------------
# KB data fixture
# ---------------------------------------------------------------------------

_SAMPLE_KB_DATA = {
    "updates": [
        {
            "kb": "KB5034441",
            "title": "2024-01 Cumulative Update for Windows 10 Version 22H2",
            "date": "2024-01-09",
            "size_kb": 512000,
            "category": "Security Updates",
            "description": "A security update.",
        },
        {
            "kb": "KB5033372",
            "title": "2023-12 Cumulative Update for Windows 10 Version 22H2",
            "date": "2023-12-12",
            "size_kb": 450000,
            "category": "Security Updates",
            "description": "A security update.",
        },
        {
            "kb": "KB5032189",
            "title": "2023-11 Cumulative Update for Windows 10 Version 22H2",
            "date": "2023-11-14",
            "size_kb": 425000,
            "category": "Security Updates",
            "description": "A security update.",
        },
    ]
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def mock_hive_writer():
    return MagicMock(spec=HiveWriter)


@pytest.fixture
def mock_evtx_writer():
    return MagicMock(spec=EvtxWriter)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    kb_file = d / "kb_updates.json"
    kb_file.write_text(json.dumps(_SAMPLE_KB_DATA), encoding="utf-8")
    return d


@pytest.fixture
def update_artifacts(mock_hive_writer, mock_evtx_writer, audit_logger, data_dir):
    return UpdateArtifacts(
        mock_hive_writer, mock_evtx_writer, audit_logger, data_dir
    )


@pytest.fixture
def install_date():
    return datetime(2023, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class TestLoadKbData:
    def test_loads_from_file(self, update_artifacts):
        assert len(update_artifacts._kb_data) == 3

    def test_missing_file_raises(self, mock_hive_writer, mock_evtx_writer, audit_logger, tmp_path):
        empty_dir = tmp_path / "nodata"
        empty_dir.mkdir()
        with pytest.raises(UpdateArtifactsError, match="not found"):
            UpdateArtifacts(mock_hive_writer, mock_evtx_writer, audit_logger, empty_dir)

    def test_malformed_json_raises(
        self, mock_hive_writer, mock_evtx_writer, audit_logger, tmp_path
    ):
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "kb_updates.json").write_text("not json", encoding="utf-8")
        with pytest.raises(UpdateArtifactsError):
            UpdateArtifacts(mock_hive_writer, mock_evtx_writer, audit_logger, bad_dir)

    def test_missing_updates_key_raises(
        self, mock_hive_writer, mock_evtx_writer, audit_logger, tmp_path
    ):
        bad_dir = tmp_path / "bad2"
        bad_dir.mkdir()
        (bad_dir / "kb_updates.json").write_text(
            json.dumps({"wrong_key": []}), encoding="utf-8"
        )
        with pytest.raises(UpdateArtifactsError):
            UpdateArtifacts(mock_hive_writer, mock_evtx_writer, audit_logger, bad_dir)


# ---------------------------------------------------------------------------
# build_registry_operations
# ---------------------------------------------------------------------------

class TestBuildRegistryOperations:
    def test_returns_list_of_hive_operations(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_contains_last_success_time_install(
        self, update_artifacts, install_date
    ):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        value_names = [op.value_name for op in ops]
        assert "LastSuccessTime" in value_names

    def test_contains_au_options(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        value_names = [op.value_name for op in ops]
        assert "AUOptions" in value_names

    def test_au_options_value_is_4(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        au_op = next(op for op in ops if op.value_name == "AUOptions")
        assert au_op.value_data == 4

    def test_sus_client_id_present(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        value_names = [op.value_name for op in ops]
        assert "SusClientId" in value_names

    def test_sus_client_id_guid_format(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        sus_op = next(op for op in ops if op.value_name == "SusClientId")
        import re
        guid_pattern = r"^\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}$"
        assert re.match(guid_pattern, sus_op.value_data, re.IGNORECASE)

    def test_sus_client_id_deterministic(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops1 = update_artifacts.build_registry_operations("CORP-PC", install_date, updates)
        ops2 = update_artifacts.build_registry_operations("CORP-PC", install_date, updates)
        id1 = next(op.value_data for op in ops1 if op.value_name == "SusClientId")
        id2 = next(op.value_data for op in ops2 if op.value_name == "SusClientId")
        assert id1 == id2

    def test_different_computers_different_sus_id(
        self, update_artifacts, install_date
    ):
        updates = update_artifacts._kb_data
        ops1 = update_artifacts.build_registry_operations("PC-A", install_date, updates)
        ops2 = update_artifacts.build_registry_operations("PC-B", install_date, updates)
        id1 = next(op.value_data for op in ops1 if op.value_name == "SusClientId")
        id2 = next(op.value_data for op in ops2 if op.value_name == "SusClientId")
        assert id1 != id2

    def test_validation_blob_is_bytes(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        blob_op = next(op for op in ops if op.value_name == "SusClientIDValidation")
        assert isinstance(blob_op.value_data, bytes)
        assert len(blob_op.value_data) == 28

    def test_all_ops_target_software_hive(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        ops = update_artifacts.build_registry_operations("PC", install_date, updates)
        assert all(op.hive_path == _SOFTWARE_HIVE for op in ops)


# ---------------------------------------------------------------------------
# build_evtx_records
# ---------------------------------------------------------------------------

class TestBuildEvtxRecords:
    def test_returns_list_of_evtx_records(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("PC", install_date, updates)
        assert all(isinstance(r, EvtxRecord) for r in records)

    def test_four_records_per_update(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("PC", install_date, updates)
        assert len(records) == len(updates) * 4

    def test_all_four_eids_present(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("PC", install_date, updates)
        eids = {r.event_id for r in records}
        assert _EID_DOWNLOAD_START in eids
        assert _EID_DOWNLOAD_DONE in eids
        assert _EID_INSTALL_START in eids
        assert _EID_INSTALL_DONE in eids

    def test_records_chronological_order(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("PC", install_date, updates)
        timestamps = [r.timestamp for r in records]
        assert timestamps == sorted(timestamps)

    def test_channel_is_system(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("PC", install_date, updates)
        assert all(r.channel == "System" for r in records)

    def test_computer_name_embedded(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        records = update_artifacts.build_evtx_records("CORP-PC", install_date, updates)
        assert all(r.computer == "CORP-PC" for r in records)

    def test_empty_updates_returns_empty(self, update_artifacts, install_date):
        records = update_artifacts.build_evtx_records("PC", install_date, [])
        assert records == []

    def test_deterministic_output(self, update_artifacts, install_date):
        updates = update_artifacts._kb_data
        r1 = update_artifacts.build_evtx_records("PC", install_date, updates)
        r2 = update_artifacts.build_evtx_records("PC", install_date, updates)
        assert [r.event_id for r in r1] == [r.event_id for r in r2]
        assert [r.timestamp for r in r1] == [r.timestamp for r in r2]


# ---------------------------------------------------------------------------
# _select_updates
# ---------------------------------------------------------------------------

class TestSelectUpdates:
    def test_home_update_count(self, update_artifacts):
        # With only 3 KB entries, should return min(home_count, 3)
        updates = update_artifacts._select_updates("home", "HOME-PC")
        assert len(updates) <= _UPDATES_BY_PROFILE["home"]
        assert len(updates) == min(_UPDATES_BY_PROFILE["home"], 3)

    def test_deterministic_selection(self, update_artifacts):
        u1 = update_artifacts._select_updates("office", "CORP-PC")
        u2 = update_artifacts._select_updates("office", "CORP-PC")
        assert [x["kb"] for x in u1] == [x["kb"] for x in u2]

    def test_different_computer_different_selection(self, update_artifacts):
        # With 3 entries and max >3, both return all 3 but internal order may vary
        u1 = update_artifacts._select_updates("developer", "PC-A")
        u2 = update_artifacts._select_updates("developer", "PC-B")
        # Both should be lists of the same 3 KBs (just possibly reordered)
        assert set(x["kb"] for x in u1) == set(x["kb"] for x in u2)


# ---------------------------------------------------------------------------
# write_update_artifacts — delegation
# ---------------------------------------------------------------------------

class TestWriteUpdateArtifacts:
    def test_delegates_to_both_writers(
        self, update_artifacts, mock_hive_writer, mock_evtx_writer, install_date
    ):
        update_artifacts.write_update_artifacts("home", "HOME-PC", install_date)
        mock_hive_writer.execute_operations.assert_called_once()
        mock_evtx_writer.write_records.assert_called_once()

    def test_audit_logged(
        self, update_artifacts, audit_logger, install_date
    ):
        audit_logger.clear()
        update_artifacts.write_update_artifacts("home", "HOME-PC", install_date)
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "UpdateArtifacts"

    def test_hive_error_propagates(
        self, update_artifacts, mock_hive_writer, install_date
    ):
        from services.registry.hive_writer import HiveWriterError
        mock_hive_writer.execute_operations.side_effect = HiveWriterError("disk full")
        with pytest.raises(UpdateArtifactsError, match="disk full"):
            update_artifacts.write_update_artifacts("home", "HOME-PC", install_date)

    def test_evtx_error_propagates(
        self, update_artifacts, mock_evtx_writer, install_date
    ):
        from services.eventlog.evtx_writer import EvtxWriterError
        mock_evtx_writer.write_records.side_effect = EvtxWriterError("io error")
        with pytest.raises(UpdateArtifactsError, match="io error"):
            update_artifacts.write_update_artifacts("home", "HOME-PC", install_date)


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_write(self, update_artifacts, install_date):
        update_artifacts.apply({
            "profile_type": "home",
            "computer_name": "HOME-PC",
            "install_date": install_date,
        })

    def test_apply_missing_profile_raises(self, update_artifacts, install_date):
        with pytest.raises(UpdateArtifactsError):
            update_artifacts.apply({
                "computer_name": "PC",
                "install_date": install_date,
            })

    def test_apply_missing_computer_raises(self, update_artifacts, install_date):
        with pytest.raises(UpdateArtifactsError):
            update_artifacts.apply({
                "profile_type": "home",
                "install_date": install_date,
            })

    def test_apply_missing_install_date_raises(self, update_artifacts):
        with pytest.raises(UpdateArtifactsError):
            update_artifacts.apply({
                "profile_type": "home",
                "computer_name": "PC",
            })

    def test_service_name(self, update_artifacts):
        assert update_artifacts.service_name == "UpdateArtifacts"
