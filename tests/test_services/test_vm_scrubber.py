"""Tests for the VmScrubber anti-fingerprint service."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.anti_fingerprint.vm_scrubber import (
    VmScrubber,
    VmScrubberError,
    _VM_STRINGS,
    _VM_SERVICE_KEYS,
    _VM_SOFTWARE_KEYS,
    _SYSTEM_HIVE,
    _SOFTWARE_HIVE,
)
from services.registry.hive_writer import HiveWriter, HiveWriterError


# ---------------------------------------------------------------------------
# Hardware data fixture (minimal)
# ---------------------------------------------------------------------------

_SAMPLE_HW_DATA = {
    "system_vendors": [
        {
            "bios_vendor": "Dell Inc.",
            "motherboard_models": ["Latitude 5540", "OptiPlex 7090"],
            "bios_versions": ["2.18.0", "2.15.1"],
        }
    ],
    "disk_models": [
        {"model": "Samsung SSD 980 PRO 1TB", "vendor": "Samsung"}
    ],
    "gpu_models": ["NVIDIA GeForce RTX 3060"],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "hardware_models.json").write_text(
        json.dumps(_SAMPLE_HW_DATA), encoding="utf-8"
    )
    return d


@pytest.fixture
def mock_hive_writer():
    hw = MagicMock(spec=HiveWriter)
    # By default: no VM keys exist, read_value raises HiveWriterError
    hw.key_exists.return_value = False
    hw.read_value.side_effect = HiveWriterError("not found")
    return hw


@pytest.fixture
def vm_scrubber(mock_hive_writer, audit_logger, data_dir):
    return VmScrubber(mock_hive_writer, audit_logger, data_dir)


# ---------------------------------------------------------------------------
# _contains_vm_string
# ---------------------------------------------------------------------------

class TestContainsVmString:
    def test_detects_vbox(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("VirtualBox")

    def test_detects_vmware(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("VMware Tools")

    def test_detects_virtual_machine(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("Virtual Machine")

    def test_detects_case_insensitive(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("VBOX_SERVICE")

    def test_bare_metal_not_detected(self, vm_scrubber):
        assert not VmScrubber._contains_vm_string("Dell Inc.")

    def test_realistic_bios_not_detected(self, vm_scrubber):
        assert not VmScrubber._contains_vm_string("BIOS Date: 06/15/2023")

    def test_qemu_detected(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("QEMU Standard PC")

    def test_xen_detected(self, vm_scrubber):
        assert VmScrubber._contains_vm_string("Xen HVM domU")

    def test_empty_string_not_detected(self, vm_scrubber):
        assert not VmScrubber._contains_vm_string("")


# ---------------------------------------------------------------------------
# build_operations — no VM keys present
# ---------------------------------------------------------------------------

class TestBuildOperationsClean:
    def test_returns_list(self, vm_scrubber):
        ops = vm_scrubber.build_operations("HOME-PC")
        assert isinstance(ops, list)

    def test_no_deletes_when_keys_absent(self, vm_scrubber):
        ops = vm_scrubber.build_operations("HOME-PC")
        delete_ops = [op for op in ops if op.operation == "delete_key"]
        assert len(delete_ops) == 0

    def test_no_patches_when_values_raise(self, vm_scrubber):
        ops = vm_scrubber.build_operations("HOME-PC")
        set_ops = [op for op in ops if op.operation != "delete_key"]
        assert len(set_ops) == 0

    def test_key_exists_called_for_vm_services(
        self, vm_scrubber, mock_hive_writer
    ):
        vm_scrubber.build_operations("HOME-PC")
        calls = [
            c for c in mock_hive_writer.key_exists.call_args_list
            if c[0][0] == _SYSTEM_HIVE
        ]
        assert len(calls) == len(_VM_SERVICE_KEYS)

    def test_key_exists_called_for_vm_software(
        self, vm_scrubber, mock_hive_writer
    ):
        vm_scrubber.build_operations("HOME-PC")
        calls = [
            c for c in mock_hive_writer.key_exists.call_args_list
            if c[0][0] == _SOFTWARE_HIVE
        ]
        assert len(calls) == len(_VM_SOFTWARE_KEYS)


# ---------------------------------------------------------------------------
# build_operations — VM keys present
# ---------------------------------------------------------------------------

class TestBuildOperationsVmPresent:
    def test_delete_ops_when_vm_keys_present(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = True
        mock_hive_writer.read_value.side_effect = HiveWriterError("not found")
        scrubber = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops = scrubber.build_operations("VM-PC")
        delete_ops = [op for op in ops if op.operation == "delete_key"]
        expected = len(_VM_SERVICE_KEYS) + len(_VM_SOFTWARE_KEYS)
        assert len(delete_ops) == expected

    def test_delete_ops_target_correct_hives(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = True
        mock_hive_writer.read_value.side_effect = HiveWriterError("not found")
        scrubber = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops = scrubber.build_operations("VM-PC")
        system_deletes = [
            op for op in ops
            if op.operation == "delete_key" and op.hive_path == _SYSTEM_HIVE
        ]
        software_deletes = [
            op for op in ops
            if op.operation == "delete_key" and op.hive_path == _SOFTWARE_HIVE
        ]
        assert len(system_deletes) == len(_VM_SERVICE_KEYS)
        assert len(software_deletes) == len(_VM_SOFTWARE_KEYS)

    def test_patch_op_when_vm_string_in_value(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = False
        mock_hive_writer.read_value.side_effect = None
        mock_hive_writer.read_value.return_value = "VMware, Inc."
        scrubber = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops = scrubber.build_operations("VM-PC")
        set_ops = [op for op in ops if op.operation == "set"]
        # 3 identity patches, all values contain "VMware"
        assert len(set_ops) == 3

    def test_no_patch_when_value_is_clean(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = False
        mock_hive_writer.read_value.side_effect = None
        mock_hive_writer.read_value.return_value = "Dell Inc."
        scrubber = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops = scrubber.build_operations("HOME-PC")
        set_ops = [op for op in ops if op.operation == "set"]
        assert len(set_ops) == 0

    def test_deterministic_replacements(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = False
        mock_hive_writer.read_value.side_effect = None
        mock_hive_writer.read_value.return_value = "VirtualBox"
        s1 = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        s2 = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops1 = s1.build_operations("CORP-PC")
        ops2 = s2.build_operations("CORP-PC")
        set1 = [op.value_data for op in ops1 if op.operation == "set"]
        set2 = [op.value_data for op in ops2 if op.operation == "set"]
        assert set1 == set2

    def test_replacement_not_a_vm_string(
        self, mock_hive_writer, audit_logger, data_dir
    ):
        mock_hive_writer.key_exists.return_value = False
        mock_hive_writer.read_value.side_effect = None
        mock_hive_writer.read_value.return_value = "VirtualBox"
        scrubber = VmScrubber(mock_hive_writer, audit_logger, data_dir)
        ops = scrubber.build_operations("PC")
        for op in ops:
            if op.operation == "set":
                assert not VmScrubber._contains_vm_string(str(op.value_data))


# ---------------------------------------------------------------------------
# scrub — delegation
# ---------------------------------------------------------------------------

class TestScrub:
    def test_delegates_to_hive_writer(self, vm_scrubber, mock_hive_writer):
        vm_scrubber.scrub("HOME-PC")
        mock_hive_writer.execute_operations.assert_called_once()

    def test_audit_logged(self, vm_scrubber, audit_logger):
        audit_logger.clear()
        vm_scrubber.scrub("HOME-PC")
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "VmScrubber"

    def test_hive_error_propagates(self, vm_scrubber, mock_hive_writer):
        mock_hive_writer.execute_operations.side_effect = HiveWriterError("io")
        with pytest.raises(VmScrubberError, match="io"):
            vm_scrubber.scrub("PC")


# ---------------------------------------------------------------------------
# apply — context
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_scrub(self, vm_scrubber):
        vm_scrubber.apply({"computer_name": "HOME-PC"})

    def test_apply_missing_computer_name_raises(self, vm_scrubber):
        with pytest.raises(VmScrubberError):
            vm_scrubber.apply({})

    def test_service_name(self, vm_scrubber):
        assert vm_scrubber.service_name == "VmScrubber"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class TestLoad:
    def test_missing_hw_file_raises(
        self, mock_hive_writer, audit_logger, tmp_path
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(VmScrubberError, match="not found"):
            VmScrubber(mock_hive_writer, audit_logger, empty)

    def test_malformed_json_raises(
        self, mock_hive_writer, audit_logger, tmp_path
    ):
        d = tmp_path / "d"
        d.mkdir()
        (d / "hardware_models.json").write_text("bad json", encoding="utf-8")
        with pytest.raises(VmScrubberError):
            VmScrubber(mock_hive_writer, audit_logger, d)

    def test_missing_system_vendors_key_raises(
        self, mock_hive_writer, audit_logger, tmp_path
    ):
        d = tmp_path / "d2"
        d.mkdir()
        (d / "hardware_models.json").write_text(
            json.dumps({"other": []}), encoding="utf-8"
        )
        with pytest.raises(VmScrubberError):
            VmScrubber(mock_hive_writer, audit_logger, d)
