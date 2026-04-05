"""Tests for the HardwareNormalizer anti-fingerprint service."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from core.identity_generator import HardwareIdentity, IdentityBundle, UserIdentity
from services.anti_fingerprint.hardware_normalizer import (
    HardwareNormalizer,
    HardwareNormalizerError,
    _SYSINFO_KEY,
    _DISK_ENUM_KEY,
    _SYSTEM_HIVE,
)
from services.registry.hive_writer import HiveWriter, HiveWriterError


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def _make_hardware(**overrides) -> HardwareIdentity:
    defaults = {
        "bios_vendor": "Dell Inc.",
        "bios_version": "2.18.0",
        "bios_release_date": date(2023, 6, 15),
        "motherboard_model": "Latitude 5540",
        "disk_model": "Samsung SSD 980 PRO 1TB",
        "disk_serial": "S6B2NJ0T12345",
        "gpu_model": "NVIDIA GeForce RTX 3060",
    }
    defaults.update(overrides)
    return HardwareIdentity(**defaults)


def _make_user(**overrides) -> UserIdentity:
    defaults = {
        "full_name": "Alice Smith",
        "username": "alice",
        "email": "alice@corp.com",
        "organization": "Corp",
        "computer_name": "CORP-LT-042",
    }
    defaults.update(overrides)
    return UserIdentity(**defaults)


def _make_bundle(**hw_overrides) -> IdentityBundle:
    return IdentityBundle(user=_make_user(), hardware=_make_hardware(**hw_overrides))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_HW_DATA = {
    "system_vendors": [
        {
            "bios_vendor": "Dell Inc.",
            "motherboard_models": ["Latitude 5540"],
            "bios_versions": ["2.18.0"],
        }
    ],
    "disk_models": [
        {"model": "Samsung SSD 980 PRO 1TB", "vendor": "Samsung"}
    ],
    "gpu_models": ["NVIDIA GeForce RTX 3060"],
}


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
    return MagicMock(spec=HiveWriter)


@pytest.fixture
def normalizer(mock_hive_writer, audit_logger, data_dir):
    return HardwareNormalizer(mock_hive_writer, audit_logger, data_dir)


@pytest.fixture
def sample_bundle():
    return _make_bundle()


# ---------------------------------------------------------------------------
# _format_bios_date
# ---------------------------------------------------------------------------

class TestFormatBiosDate:
    def test_date_object_formats_mm_dd_yyyy(self):
        result = HardwareNormalizer._format_bios_date(date(2023, 6, 15))
        assert result == "06/15/2023"

    def test_iso_string_parses_correctly(self):
        result = HardwareNormalizer._format_bios_date("2022-01-07")
        assert result == "01/07/2022"

    def test_january_zero_padded(self):
        result = HardwareNormalizer._format_bios_date(date(2023, 1, 5))
        assert result == "01/05/2023"

    def test_invalid_string_returns_fallback(self):
        result = HardwareNormalizer._format_bios_date("not-a-date")
        assert "/" in result  # fallback still returns a date-like string


# ---------------------------------------------------------------------------
# build_operations — SystemInformation
# ---------------------------------------------------------------------------

class TestBuildOperationsSystemInfo:
    def test_returns_hive_operation_list(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_system_manufacturer_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "SystemManufacturer" in names

    def test_system_product_name_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "SystemProductName" in names

    def test_bios_vendor_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "BIOSVendor" in names

    def test_bios_version_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "BIOSVersion" in names

    def test_bios_release_date_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "BIOSReleaseDate" in names

    def test_bios_date_format(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        date_op = next(op for op in ops if op.value_name == "BIOSReleaseDate")
        import re
        assert re.match(r"^\d{2}/\d{2}/\d{4}$", date_op.value_data)

    def test_base_board_product_set(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops]
        assert "BaseBoardProduct" in names

    def test_all_sysinfo_ops_target_system_hive(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        sysinfo_ops = [op for op in ops if op.key_path == _SYSINFO_KEY]
        assert all(op.hive_path == _SYSTEM_HIVE for op in sysinfo_ops)

    def test_values_match_bundle(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        hw = sample_bundle.hardware
        mfr_op = next(op for op in ops if op.value_name == "SystemManufacturer")
        assert mfr_op.value_data == hw.bios_vendor

    def test_product_name_matches_motherboard(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        hw = sample_bundle.hardware
        prod_op = next(op for op in ops if op.value_name == "SystemProductName")
        assert prod_op.value_data == hw.motherboard_model


# ---------------------------------------------------------------------------
# build_operations — Disk enumeration
# ---------------------------------------------------------------------------

class TestBuildOperationsDisk:
    def test_disk_enum_0_present(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops if op.key_path == _DISK_ENUM_KEY]
        assert "0" in names

    def test_disk_count_present(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        names = [op.value_name for op in ops if op.key_path == _DISK_ENUM_KEY]
        assert "Count" in names

    def test_disk_enum_value_contains_serial(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        disk_op = next(
            op for op in ops
            if op.key_path == _DISK_ENUM_KEY and op.value_name == "0"
        )
        assert sample_bundle.hardware.disk_serial in disk_op.value_data

    def test_disk_enum_value_has_scsi_prefix(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        disk_op = next(
            op for op in ops
            if op.key_path == _DISK_ENUM_KEY and op.value_name == "0"
        )
        assert disk_op.value_data.startswith("SCSI\\")

    def test_count_value_is_1(self, normalizer, sample_bundle):
        ops = normalizer.build_operations(sample_bundle)
        count_op = next(
            op for op in ops
            if op.key_path == _DISK_ENUM_KEY and op.value_name == "Count"
        )
        assert count_op.value_data == 1


# ---------------------------------------------------------------------------
# normalize — delegation
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_delegates_to_hive_writer(
        self, normalizer, mock_hive_writer, sample_bundle
    ):
        normalizer.normalize(sample_bundle)
        mock_hive_writer.execute_operations.assert_called_once()

    def test_audit_logged(self, normalizer, audit_logger, sample_bundle):
        audit_logger.clear()
        normalizer.normalize(sample_bundle)
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "HardwareNormalizer"

    def test_hive_error_propagates(
        self, normalizer, mock_hive_writer, sample_bundle
    ):
        mock_hive_writer.execute_operations.side_effect = HiveWriterError("io")
        with pytest.raises(HardwareNormalizerError, match="io"):
            normalizer.normalize(sample_bundle)


# ---------------------------------------------------------------------------
# apply — context parsing
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_calls_normalize(self, normalizer, sample_bundle):
        normalizer.apply({"identity_bundle": sample_bundle})

    def test_apply_missing_bundle_raises(self, normalizer):
        with pytest.raises(HardwareNormalizerError):
            normalizer.apply({})

    def test_apply_wrong_type_raises(self, normalizer):
        with pytest.raises(HardwareNormalizerError):
            normalizer.apply({"identity_bundle": "not a bundle"})

    def test_service_name(self, normalizer):
        assert normalizer.service_name == "HardwareNormalizer"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class TestLoad:
    def test_missing_file_raises(self, mock_hive_writer, audit_logger, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(HardwareNormalizerError, match="not found"):
            HardwareNormalizer(mock_hive_writer, audit_logger, empty)

    def test_malformed_json_raises(
        self, mock_hive_writer, audit_logger, tmp_path
    ):
        d = tmp_path / "d"
        d.mkdir()
        (d / "hardware_models.json").write_text("bad json", encoding="utf-8")
        with pytest.raises(HardwareNormalizerError):
            HardwareNormalizer(mock_hive_writer, audit_logger, d)
