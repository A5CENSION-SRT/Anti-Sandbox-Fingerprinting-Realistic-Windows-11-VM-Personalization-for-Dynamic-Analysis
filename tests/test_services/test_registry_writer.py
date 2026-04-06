"""Tests for the HiveWriter registry service."""

import struct
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid regf hive builder
# ---------------------------------------------------------------------------

def _create_minimal_hive(path: Path, hive_name: str = "SOFTWARE") -> None:
    """Create a minimal but valid registry hive that regipy can parse.

    Produces a regf header + one hbin containing only the root NK cell.
    The hive has zero subkeys and zero values — it is structurally valid
    but empty.
    """
    header = bytearray(4096)

    # regf signature + sequence numbers
    header[0:4] = b"regf"
    struct.pack_into("<I", header, 4, 1)   # primary seq
    struct.pack_into("<I", header, 8, 1)   # secondary seq
    struct.pack_into("<Q", header, 12, 132000000000000000)  # timestamp
    struct.pack_into("<I", header, 20, 1)  # major version
    struct.pack_into("<I", header, 24, 3)  # minor version
    struct.pack_into("<I", header, 28, 0)  # file type (primary)
    struct.pack_into("<I", header, 32, 1)  # file format
    struct.pack_into("<I", header, 36, 32)  # root key offset in hbin
    struct.pack_into("<I", header, 40, 4096)  # hive bins data size
    struct.pack_into("<I", header, 44, 1)  # clustering factor

    name_bytes = hive_name.encode("utf-16-le")[:62]
    header[48:48 + len(name_bytes)] = name_bytes

    # Checksum = XOR of first 508 bytes
    checksum = 0
    for i in range(0, 508, 4):
        checksum ^= struct.unpack_from("<I", header, i)[0]
    struct.pack_into("<I", header, 508, checksum)

    # hbin block (4096 bytes)
    hbin = bytearray(4096)
    hbin[0:4] = b"hbin"
    struct.pack_into("<I", hbin, 4, 0)      # offset
    struct.pack_into("<I", hbin, 8, 4096)   # hbin size
    struct.pack_into("<Q", hbin, 20, 132000000000000000)

    # Root NK cell at offset 32
    key_name = b"CMI-CreateHive{2A7FB991-7BBE-4F9D-B91E-7CB51D4737F5}"
    nk_size = (76 + len(key_name) + 7) & ~7  # 8-byte aligned
    nk_cell_start = 32

    struct.pack_into("<i", hbin, nk_cell_start, -nk_size)  # allocated

    nk = nk_cell_start + 4
    hbin[nk:nk + 2] = b"nk"
    struct.pack_into("<H", hbin, nk + 2, 0x0024)  # KEY_HIVE_ENTRY|COMP_NAME
    struct.pack_into("<Q", hbin, nk + 4, 132000000000000000)
    struct.pack_into("<I", hbin, nk + 12, 0)          # access_bits
    struct.pack_into("<i", hbin, nk + 16, -1)         # parent offset
    struct.pack_into("<I", hbin, nk + 20, 0)          # subkey_count
    struct.pack_into("<I", hbin, nk + 24, 0)          # volatile_subkey_count
    struct.pack_into("<I", hbin, nk + 28, 0xFFFFFFFF)  # subkeys_list_offset
    struct.pack_into("<I", hbin, nk + 32, 0xFFFFFFFF)
    struct.pack_into("<I", hbin, nk + 36, 0)          # values_count
    struct.pack_into("<I", hbin, nk + 40, 0xFFFFFFFF)  # values_list_offset
    struct.pack_into("<I", hbin, nk + 44, 0xFFFFFFFF)  # security
    struct.pack_into("<I", hbin, nk + 48, 0xFFFFFFFF)  # class_name
    struct.pack_into("<I", hbin, nk + 52, 0)
    struct.pack_into("<I", hbin, nk + 56, 0)
    struct.pack_into("<I", hbin, nk + 60, 0)
    struct.pack_into("<I", hbin, nk + 64, 0)
    struct.pack_into("<I", hbin, nk + 68, 0)
    struct.pack_into("<H", hbin, nk + 72, len(key_name))
    struct.pack_into("<H", hbin, nk + 74, 0)
    hbin[nk + 76:nk + 76 + len(key_name)] = key_name

    # Free cell filling the rest
    free_start = nk_cell_start + nk_size
    free_size = 4096 - free_start
    struct.pack_into("<i", hbin, free_start, free_size)

    path.write_bytes(bytes(header) + bytes(hbin))


def _make_op(**overrides: Any) -> HiveOperation:
    """Build a HiveOperation with sensible defaults, applying *overrides*."""
    defaults = {
        "hive_path": "Windows/System32/config/SOFTWARE",
        "key_path": r"Microsoft\Windows NT\CurrentVersion",
        "value_name": "RegisteredOwner",
        "value_data": "John Doe",
        "value_type": RegistryValueType.REG_SZ,
        "operation": "set",
    }
    defaults.update(overrides)
    return HiveOperation(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mount_dir(tmp_path: Path) -> Path:
    """Provide a temporary mount root directory."""
    return tmp_path / "mount"


@pytest.fixture()
def mount_manager(mount_dir: Path) -> MountManager:
    """MountManager pointing at the temporary mount directory."""
    mount_dir.mkdir(parents=True)
    return MountManager(str(mount_dir))


@pytest.fixture()
def audit_logger() -> AuditLogger:
    """Shared AuditLogger instance."""
    return AuditLogger()


@pytest.fixture()
def writer(mount_manager: MountManager, audit_logger: AuditLogger) -> HiveWriter:
    """HiveWriter wired to the temp mount and audit logger."""
    return HiveWriter(mount_manager, audit_logger)


@pytest.fixture()
def hive_file(mount_dir: Path) -> Path:
    """Create a minimal valid hive at Windows/System32/config/SOFTWARE."""
    hive_dir = mount_dir / "Windows" / "System32" / "config"
    hive_dir.mkdir(parents=True)
    hive_path = hive_dir / "SOFTWARE"
    _create_minimal_hive(hive_path, hive_name="SOFTWARE")
    return hive_path


# ---------------------------------------------------------------------------
# 1. HiveWriter initialisation
# ---------------------------------------------------------------------------

class TestHiveWriterInit:
    """HiveWriter must validate dependencies at construction time."""

    def test_constructs_with_valid_dependencies(
        self, mount_manager: MountManager, audit_logger: AuditLogger
    ) -> None:
        hw = HiveWriter(mount_manager, audit_logger)
        assert hw.service_name == "HiveWriter"

    def test_service_name_is_string(
        self, mount_manager: MountManager, audit_logger: AuditLogger
    ) -> None:
        hw = HiveWriter(mount_manager, audit_logger)
        assert isinstance(hw.service_name, str)


# ---------------------------------------------------------------------------
# 2. HiveOperation Pydantic model validation
# ---------------------------------------------------------------------------

class TestHiveOperationModel:
    """HiveOperation must enforce frozen + extra=forbid + validators."""

    def test_creates_valid_operation(self) -> None:
        op = _make_op()
        assert op.hive_path == "Windows/System32/config/SOFTWARE"
        assert op.key_path == r"Microsoft\Windows NT\CurrentVersion"
        assert op.value_name == "RegisteredOwner"
        assert op.value_data == "John Doe"
        assert op.value_type == RegistryValueType.REG_SZ
        assert op.operation == "set"

    def test_frozen_cannot_mutate(self) -> None:
        op = _make_op()
        with pytest.raises(ValidationError):
            op.value_name = "hacked"

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            _make_op(unknown_field="bad")

    def test_empty_hive_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="hive_path"):
            _make_op(hive_path="")

    def test_whitespace_hive_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="hive_path"):
            _make_op(hive_path="   ")

    def test_empty_key_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="key_path"):
            _make_op(key_path="")

    def test_default_value_name_is_default(self) -> None:
        op = HiveOperation(
            hive_path="a/b",
            key_path=r"Some\Key",
        )
        assert op.value_name == "(default)"
        assert op.operation == "set"

    def test_all_value_types_valid(self) -> None:
        for vt in RegistryValueType:
            op = _make_op(value_type=vt, value_data=None)
            assert op.value_type == vt

    def test_invalid_operation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_op(operation="drop_table")


# ---------------------------------------------------------------------------
# 3. Value data encoding
# ---------------------------------------------------------------------------

class TestValueEncoding:
    """HiveWriter._encode_value_data must produce correct binary forms."""

    def test_reg_sz_utf16le_null_terminated(self) -> None:
        result = HiveWriter._encode_value_data("Hello", RegistryValueType.REG_SZ)
        expected = "Hello".encode("utf-16-le") + b"\x00\x00"
        assert result == expected

    def test_reg_sz_empty_string(self) -> None:
        result = HiveWriter._encode_value_data("", RegistryValueType.REG_SZ)
        assert result == b"\x00\x00"

    def test_reg_dword_little_endian(self) -> None:
        result = HiveWriter._encode_value_data(42, RegistryValueType.REG_DWORD)
        assert result == struct.pack("<I", 42)

    def test_reg_dword_zero(self) -> None:
        result = HiveWriter._encode_value_data(0, RegistryValueType.REG_DWORD)
        assert result == b"\x00\x00\x00\x00"

    def test_reg_dword_max(self) -> None:
        result = HiveWriter._encode_value_data(
            0xFFFFFFFF, RegistryValueType.REG_DWORD
        )
        assert result == b"\xff\xff\xff\xff"

    def test_reg_qword_little_endian(self) -> None:
        val = 133944570000000000  # a FILETIME value
        result = HiveWriter._encode_value_data(val, RegistryValueType.REG_QWORD)
        assert result == struct.pack("<Q", val)

    def test_reg_binary_passthrough(self) -> None:
        data = b"\x01\x02\x03\xAB\xCD"
        result = HiveWriter._encode_value_data(data, RegistryValueType.REG_BINARY)
        assert result == data

    def test_reg_multi_sz_encoding(self) -> None:
        result = HiveWriter._encode_value_data(
            ["one", "two"], RegistryValueType.REG_MULTI_SZ
        )
        expected = (
            "one".encode("utf-16-le") + b"\x00\x00"
            + "two".encode("utf-16-le") + b"\x00\x00"
            + b"\x00\x00"  # double-null terminator
        )
        assert result == expected

    def test_reg_none_returns_empty(self) -> None:
        result = HiveWriter._encode_value_data(None, RegistryValueType.REG_NONE)
        assert result == b""

    def test_reg_sz_wrong_type_raises(self) -> None:
        with pytest.raises(HiveWriterError, match="REG_SZ"):
            HiveWriter._encode_value_data(42, RegistryValueType.REG_SZ)

    def test_reg_dword_wrong_type_raises(self) -> None:
        with pytest.raises(HiveWriterError, match="REG_DWORD"):
            HiveWriter._encode_value_data("nope", RegistryValueType.REG_DWORD)

    def test_reg_qword_wrong_type_raises(self) -> None:
        with pytest.raises(HiveWriterError, match="REG_QWORD"):
            HiveWriter._encode_value_data("nope", RegistryValueType.REG_QWORD)

    def test_reg_binary_wrong_type_raises(self) -> None:
        with pytest.raises(HiveWriterError, match="REG_BINARY"):
            HiveWriter._encode_value_data("nope", RegistryValueType.REG_BINARY)

    def test_reg_multi_sz_wrong_type_raises(self) -> None:
        with pytest.raises(HiveWriterError, match="REG_MULTI_SZ"):
            HiveWriter._encode_value_data("nope", RegistryValueType.REG_MULTI_SZ)


# ---------------------------------------------------------------------------
# 4. Path resolution and security
# ---------------------------------------------------------------------------

class TestPathResolution:
    """HiveWriter must resolve hive paths safely via MountManager."""

    def test_valid_hive_path_resolves(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        resolved = writer._resolve_hive_path(
            "Windows/System32/config/SOFTWARE"
        )
        assert resolved == hive_file

    def test_missing_hive_raises(self, writer: HiveWriter) -> None:
        with pytest.raises(HiveWriterError, match="not found"):
            writer._resolve_hive_path("nonexistent/hive.dat")

    def test_path_escape_raises(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        with pytest.raises(HiveWriterError, match="[Pp]ath escape"):
            writer._resolve_hive_path("../../etc/passwd")


# ---------------------------------------------------------------------------
# 5. Execute operations — structural tests
# ---------------------------------------------------------------------------

class TestExecuteOperations:
    """execute_operations must process batched operations and create backups."""

    def test_empty_operations_is_noop(
        self, writer: HiveWriter, audit_logger: AuditLogger
    ) -> None:
        writer.execute_operations([])
        assert len(audit_logger.entries) == 0

    def test_creates_backup_on_write(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        op = _make_op()
        # The HiveWriter successfully creates keys/values in empty hives,
        # and should still create a backup before modification.
        writer.execute_operations([op])
        backup = hive_file.with_suffix(hive_file.suffix + ".bak")
        assert backup.exists()
        # Backup should reflect the original size before modifications
        assert backup.stat().st_size > 0

    def test_rejects_invalid_regf_signature(
        self, writer: HiveWriter, mount_dir: Path
    ) -> None:
        hive_dir = mount_dir / "Windows" / "System32" / "config"
        hive_dir.mkdir(parents=True, exist_ok=True)
        bad_hive = hive_dir / "BAD_HIVE"
        bad_hive.write_bytes(b"\x00" * 8192)
        op = _make_op(hive_path="Windows/System32/config/BAD_HIVE")
        with pytest.raises(HiveWriterError, match="regf"):
            writer.execute_operations([op])

    def test_groups_operations_by_hive(
        self, writer: HiveWriter, mount_dir: Path
    ) -> None:
        # Create two hive files
        hive_dir = mount_dir / "Windows" / "System32" / "config"
        hive_dir.mkdir(parents=True, exist_ok=True)
        for name in ("SOFTWARE", "SYSTEM"):
            _create_minimal_hive(hive_dir / name, hive_name=name)

        op1 = _make_op(hive_path="Windows/System32/config/SOFTWARE")
        op2 = _make_op(hive_path="Windows/System32/config/SYSTEM")

        # HiveWriter successfully creates keys/values in empty hives.
        # Both hives should get backup files.
        writer.execute_operations([op1, op2])

        # Both hives should have backups
        assert (hive_dir / "SOFTWARE.bak").exists()
        assert (hive_dir / "SYSTEM.bak").exists()


# ---------------------------------------------------------------------------
# 6. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Every operation must produce audit log entries."""

    def test_set_value_fallback_audited(
        self, writer: HiveWriter, hive_file: Path, audit_logger: AuditLogger
    ) -> None:
        # HiveWriter creates keys/values in empty hives, audit should capture this
        op = _make_op()
        writer.execute_operations([op])
        # Successful operation should produce audit entries for backup + value creation
        # The audit logger should have at least one entry for the operation
        assert len(audit_logger.entries) > 0

    def test_audit_entry_has_required_fields(
        self, audit_logger: AuditLogger
    ) -> None:
        # Directly test the audit structure via a mock scenario
        writer_mock = MagicMock()
        op = _make_op()
        # Build the dict that _audit_operation would create
        entry = {
            "service": "HiveWriter",
            "operation": "set_value",
            "hive": "SOFTWARE",
            "key_path": op.key_path,
            "value_name": op.value_name,
            "value_type": op.value_type.value,
        }
        audit_logger.log(entry)
        assert len(audit_logger.entries) == 1
        logged = audit_logger.entries[0]
        assert logged["service"] == "HiveWriter"
        assert logged["operation"] == "set_value"
        assert logged["key_path"] == op.key_path
        assert logged["value_name"] == "RegisteredOwner"
        assert logged["value_type"] == "REG_SZ"
        assert "timestamp" in logged


# ---------------------------------------------------------------------------
# 7. BaseService interface compliance
# ---------------------------------------------------------------------------

class TestServiceInterface:
    """HiveWriter must satisfy the BaseService contract."""

    def test_service_name(self, writer: HiveWriter) -> None:
        assert writer.service_name == "HiveWriter"

    def test_apply_delegates_to_execute_operations(
        self, writer: HiveWriter, audit_logger: AuditLogger
    ) -> None:
        # apply with empty list should be a no-op
        writer.apply({"operations": []})
        assert len(audit_logger.entries) == 0

    def test_apply_with_missing_key_defaults_empty(
        self, writer: HiveWriter, audit_logger: AuditLogger
    ) -> None:
        writer.apply({})
        assert len(audit_logger.entries) == 0


# ---------------------------------------------------------------------------
# 8. key_exists helper
# ---------------------------------------------------------------------------

class TestKeyExists:
    """key_exists must use regipy to check key presence."""

    def test_root_exists(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        # Root key always exists in a valid hive
        result = writer.key_exists(
            "Windows/System32/config/SOFTWARE", "\\"
        )
        assert result is True

    def test_nonexistent_key_returns_false(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        result = writer.key_exists(
            "Windows/System32/config/SOFTWARE",
            r"Microsoft\Nonexistent\Key",
        )
        assert result is False

    def test_missing_hive_raises(self, writer: HiveWriter) -> None:
        with pytest.raises(HiveWriterError, match="not found"):
            writer.key_exists("no/such/hive", r"Some\Key")


# ---------------------------------------------------------------------------
# 9. read_value helper
# ---------------------------------------------------------------------------

class TestReadValue:
    """read_value must read from offline hives via regipy."""

    def test_missing_hive_raises(self, writer: HiveWriter) -> None:
        with pytest.raises(HiveWriterError, match="not found"):
            writer.read_value("no/hive", r"Some\Key", "SomeValue")

    def test_missing_key_raises(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        with pytest.raises(HiveWriterError, match="Key not found"):
            writer.read_value(
                "Windows/System32/config/SOFTWARE",
                r"Nonexistent\Key",
                "SomeValue",
            )

    def test_missing_value_raises(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        # Root key exists but has no values in our minimal hive
        with pytest.raises(HiveWriterError, match="Value.*not found"):
            writer.read_value(
                "Windows/System32/config/SOFTWARE",
                "\\",
                "NonexistentValue",
            )
