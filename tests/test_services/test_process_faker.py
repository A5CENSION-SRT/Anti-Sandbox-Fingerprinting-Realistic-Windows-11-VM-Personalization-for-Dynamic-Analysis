"""Tests for the ProcessFaker anti-fingerprint service."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.anti_fingerprint.process_faker import (
    ProcessFaker,
    ProcessFakerError,
    _SYSTEM_HIVE,
    _SOFTWARE_HIVE,
    _NTUSER_HIVE,
    _SERVICES_KEY,
    _RUN_KEY,
    _RUNONCE_KEY,
    _USER_RUN_KEY,
    _PROFILE_RUN_ENTRIES,
    _PROFILE_USER_RUN_ENTRIES,
)
from services.registry.hive_writer import HiveWriter, HiveWriterError, RegistryValueType


# ---------------------------------------------------------------------------
# Service catalog fixture (minimal)
# ---------------------------------------------------------------------------

_SAMPLE_SERVICES = {
    "services": [
        {
            "name": "Dnscache",
            "display_name": "DNS Client",
            "description": "Resolves and caches DNS names.",
            "image_path": r"%SystemRoot%\system32\svchost.exe -k NetworkService -p",
            "start_type": 2,
            "service_type": 32,
            "object_name": "NT AUTHORITY\\NetworkService",
        },
        {
            "name": "EventLog",
            "display_name": "Windows Event Log",
            "description": "Manages events and event logs.",
            "image_path": r"%SystemRoot%\system32\svchost.exe -k LocalServiceNetworkRestricted -p",
            "start_type": 2,
            "service_type": 32,
            "object_name": "NT AUTHORITY\\LocalService",
        },
    ]
}

# 7 values per service
_VALUES_PER_SERVICE: int = 7


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
def templates_dir(tmp_path):
    d = tmp_path / "templates" / "registry"
    d.mkdir(parents=True)
    (d / "common_services.json").write_text(
        json.dumps(_SAMPLE_SERVICES), encoding="utf-8"
    )
    return tmp_path / "templates"


@pytest.fixture
def process_faker(mock_hive_writer, audit_logger, templates_dir):
    return ProcessFaker(mock_hive_writer, audit_logger, templates_dir)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class TestLoadServices:
    def test_loads_services(self, process_faker):
        assert len(process_faker._services_data) == 2

    def test_missing_file_raises(self, mock_hive_writer, audit_logger, tmp_path):
        empty = tmp_path / "templates" / "registry"
        empty.mkdir(parents=True)
        with pytest.raises(ProcessFakerError, match="not found"):
            ProcessFaker(mock_hive_writer, audit_logger, empty)

    def test_malformed_json_raises(self, mock_hive_writer, audit_logger, tmp_path):
        d = tmp_path / "templates" / "registry"
        d.mkdir(parents=True)
        (d / "common_services.json").write_text("bad json", encoding="utf-8")
        with pytest.raises(ProcessFakerError):
            ProcessFaker(mock_hive_writer, audit_logger, d)

    def test_missing_services_key_raises(
        self, mock_hive_writer, audit_logger, tmp_path
    ):
        d = tmp_path / "templates" / "registry"
        d.mkdir(parents=True)
        (d / "common_services.json").write_text(
            json.dumps({"wrong": []}), encoding="utf-8"
        )
        with pytest.raises(ProcessFakerError):
            ProcessFaker(mock_hive_writer, audit_logger, d)


# ---------------------------------------------------------------------------
# build_operations — service entries
# ---------------------------------------------------------------------------

class TestBuildOperationsServices:
    def test_returns_list(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        assert isinstance(ops, list)

    def test_service_entry_count(self, process_faker):
        """7 value ops per service for 2 services = 14 service ops."""
        ops = process_faker.build_operations("home", "alice")
        service_ops = [
            op for op in ops
            if op.hive_path == _SYSTEM_HIVE
            and op.key_path.startswith(_SERVICES_KEY)
        ]
        assert len(service_ops) == len(_SAMPLE_SERVICES["services"]) * _VALUES_PER_SERVICE

    def test_image_path_value_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        names = [op.value_name for op in ops if op.hive_path == _SYSTEM_HIVE]
        assert "ImagePath" in names

    def test_display_name_value_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        names = [op.value_name for op in ops if op.hive_path == _SYSTEM_HIVE]
        assert "DisplayName" in names

    def test_type_value_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        names = [op.value_name for op in ops if op.hive_path == _SYSTEM_HIVE]
        assert "Type" in names

    def test_start_value_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        names = [op.value_name for op in ops if op.hive_path == _SYSTEM_HIVE]
        assert "Start" in names

    def test_error_control_value_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        names = [op.value_name for op in ops if op.hive_path == _SYSTEM_HIVE]
        assert "ErrorControl" in names

    def test_service_key_path_format(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        svc_ops = [
            op for op in ops
            if op.hive_path == _SYSTEM_HIVE
            and op.key_path.startswith(_SERVICES_KEY)
        ]
        for op in svc_ops:
            # Must be SERVICES_KEY\<ServiceName>
            assert op.key_path.count("\\") >= 1


# ---------------------------------------------------------------------------
# build_operations — HKLM Run entries
# ---------------------------------------------------------------------------

class TestBuildOperationsHklmRun:
    def test_hklm_run_ops_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        run_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUN_KEY
        ]
        assert len(run_ops) > 0

    def test_home_run_entries_count(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        run_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUN_KEY
        ]
        expected = len(_PROFILE_RUN_ENTRIES["home"])
        assert len(run_ops) == expected

    def test_office_run_entries_count(self, process_faker):
        ops = process_faker.build_operations("office", "bob")
        run_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUN_KEY
        ]
        expected = len(_PROFILE_RUN_ENTRIES["office"])
        assert len(run_ops) == expected

    def test_developer_run_entries_count(self, process_faker):
        ops = process_faker.build_operations("developer", "dev")
        run_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUN_KEY
        ]
        expected = len(_PROFILE_RUN_ENTRIES["developer"])
        assert len(run_ops) == expected

    def test_username_substituted_in_run_values(self, process_faker):
        ops = process_faker.build_operations("developer", "carol")
        run_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUN_KEY
        ]
        for op in run_ops:
            assert "{username}" not in str(op.value_data)


# ---------------------------------------------------------------------------
# build_operations — RunOnce
# ---------------------------------------------------------------------------

class TestBuildOperationsRunOnce:
    def test_runonce_key_op_present(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        runonce_ops = [
            op for op in ops
            if op.hive_path == _SOFTWARE_HIVE
            and op.key_path == _RUNONCE_KEY
        ]
        assert len(runonce_ops) == 1


# ---------------------------------------------------------------------------
# build_operations — NTUSER Run entries
# ---------------------------------------------------------------------------

class TestBuildOperationsNtuserRun:
    def test_ntuser_run_ops_present_for_home(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        ntuser_hive = _NTUSER_HIVE.format(username="alice")
        ntuser_ops = [op for op in ops if op.hive_path == ntuser_hive]
        assert len(ntuser_ops) == len(_PROFILE_USER_RUN_ENTRIES["home"])

    def test_ntuser_run_ops_for_office(self, process_faker):
        ops = process_faker.build_operations("office", "bob")
        ntuser_hive = _NTUSER_HIVE.format(username="bob")
        ntuser_ops = [op for op in ops if op.hive_path == ntuser_hive]
        assert len(ntuser_ops) == len(_PROFILE_USER_RUN_ENTRIES["office"])

    def test_ntuser_run_ops_for_developer(self, process_faker):
        ops = process_faker.build_operations("developer", "dev")
        ntuser_hive = _NTUSER_HIVE.format(username="dev")
        ntuser_ops = [op for op in ops if op.hive_path == ntuser_hive]
        assert len(ntuser_ops) == len(_PROFILE_USER_RUN_ENTRIES["developer"])

    def test_ntuser_key_path_is_user_run(self, process_faker):
        ops = process_faker.build_operations("home", "alice")
        ntuser_hive = _NTUSER_HIVE.format(username="alice")
        ntuser_ops = [op for op in ops if op.hive_path == ntuser_hive]
        for op in ntuser_ops:
            assert op.key_path == _USER_RUN_KEY

    def test_username_substituted_in_ntuser_values(self, process_faker):
        ops = process_faker.build_operations("developer", "carol")
        ntuser_hive = _NTUSER_HIVE.format(username="carol")
        ntuser_ops = [op for op in ops if op.hive_path == ntuser_hive]
        for op in ntuser_ops:
            assert "{username}" not in str(op.value_data)


# ---------------------------------------------------------------------------
# fake_processes — delegation
# ---------------------------------------------------------------------------

class TestFakeProcesses:
    def test_delegates_to_hive_writer(
        self, process_faker, mock_hive_writer
    ):
        process_faker.fake_processes("home", "alice")
        mock_hive_writer.execute_operations.assert_called_once()

    def test_audit_logged(self, process_faker, audit_logger):
        audit_logger.clear()
        process_faker.fake_processes("home", "alice")
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "ProcessFaker"

    def test_hive_error_propagates(self, process_faker, mock_hive_writer):
        mock_hive_writer.execute_operations.side_effect = HiveWriterError("io")
        with pytest.raises(ProcessFakerError, match="io"):
            process_faker.fake_processes("home", "alice")


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_fake_processes(self, process_faker):
        process_faker.apply({"profile_type": "home", "username": "alice"})

    def test_apply_missing_profile_raises(self, process_faker):
        with pytest.raises(ProcessFakerError):
            process_faker.apply({"username": "alice"})

    def test_apply_missing_username_raises(self, process_faker):
        with pytest.raises(ProcessFakerError):
            process_faker.apply({"profile_type": "home"})

    def test_service_name(self, process_faker):
        assert process_faker.service_name == "ProcessFaker"
