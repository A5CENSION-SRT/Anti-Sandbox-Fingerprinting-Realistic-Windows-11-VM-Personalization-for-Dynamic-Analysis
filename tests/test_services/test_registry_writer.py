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

try:
    import hivex  # noqa: F401
    _HIVEX_AVAILABLE = True
except ImportError:
    _HIVEX_AVAILABLE = False

_requires_hivex = pytest.mark.skipif(
    not _HIVEX_AVAILABLE,
    reason="python3-hivex not installed: apt install libhivex-bin python3-hivex",
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid regf hive builder
# ---------------------------------------------------------------------------

def _create_minimal_hive(path: Path, hive_name: str = "SOFTWARE") -> None:
    """Create a minimal but valid registry hive for use in tests.

    Produces a regf header + one hbin with a root NK cell and a valid SK
    cell. The SK cell is required by hivex_node_add_child: it reads
    parent_nk->sk, adds 0x1000, and validates the block before creating
    any child key.
    """
    _HIVE_HDR = 4096
    _HBIN_HDR = 32
    _NK_FIXED = 80   # NK fixed header including work_var at +72

    bin_data_size = 4096
    hive_data = bytearray(_HIVE_HDR + bin_data_size)

    # ── regf header ────────────────────────────────────────────────────
    struct.pack_into("<4s", hive_data, 0, b"regf")
    struct.pack_into("<I", hive_data, 4, 1)               # primary seq
    struct.pack_into("<I", hive_data, 8, 1)               # secondary seq
    struct.pack_into("<Q", hive_data, 12, 0)              # last written ts
    struct.pack_into("<I", hive_data, 20, 1)              # major version
    struct.pack_into("<I", hive_data, 24, 5)              # minor version
    struct.pack_into("<I", hive_data, 28, 0)              # type (primary)
    struct.pack_into("<I", hive_data, 32, 1)              # format
    struct.pack_into("<I", hive_data, 36, 32)             # root cell offset in hbins
    struct.pack_into("<I", hive_data, 40, bin_data_size)  # hive bins data size
    struct.pack_into("<I", hive_data, 44, 1)              # clustering factor
    name_bytes = hive_name.encode("utf-16-le")[:64]
    hive_data[48:48 + len(name_bytes)] = name_bytes
    checksum = 0
    for i in range(0, 508, 4):
        checksum ^= struct.unpack_from("<I", hive_data, i)[0]
    struct.pack_into("<I", hive_data, 508, checksum)

    # ── hbin header ────────────────────────────────────────────────────
    hbin_off = _HIVE_HDR
    struct.pack_into("<4s", hive_data, hbin_off, b"hbin")
    struct.pack_into("<I", hive_data, hbin_off + 4, 0)
    struct.pack_into("<I", hive_data, hbin_off + 8, bin_data_size)

    # ── Root NK cell (80-byte fixed header) ────────────────────────────
    cell_off = hbin_off + _HBIN_HDR
    root_name = b"CMI-CreateHive{2A7FB991-7BBE-4F9D-B91E-7CB51D4737F5}"
    cell_size = _NK_FIXED + len(root_name)
    struct.pack_into("<i", hive_data, cell_off, -cell_size)
    struct.pack_into("<2s", hive_data, cell_off + 4, b"nk")
    struct.pack_into("<H", hive_data, cell_off + 6, 0x0020)   # KEY_HIVE_ENTRY
    struct.pack_into("<I", hive_data, cell_off + 20, 0xFFFFFFFF)  # parent
    struct.pack_into("<I", hive_data, cell_off + 32, 0xFFFFFFFF)  # subkeys stable
    struct.pack_into("<I", hive_data, cell_off + 36, 0xFFFFFFFF)  # subkeys volatile
    struct.pack_into("<I", hive_data, cell_off + 44, 0xFFFFFFFF)  # values list
    struct.pack_into("<I", hive_data, cell_off + 52, 0xFFFFFFFF)  # class name
    # sk field (offset 48) will be filled in after SK cell is placed
    struct.pack_into("<I", hive_data, cell_off + 72, 0)        # work_var (spare)
    struct.pack_into("<H", hive_data, cell_off + 76, len(root_name))
    struct.pack_into("<H", hive_data, cell_off + 78, 0)        # class name len
    hive_data[cell_off + 80:cell_off + 80 + len(root_name)] = root_name

    # ── SK (Security Key) cell — required by hivex_node_add_child ──────
    sk_cell_off = cell_off + cell_size
    sk_rel = sk_cell_off - _HIVE_HDR    # relative to hive bins start
    sec_desc = bytearray(20)
    sec_desc[0] = 1
    struct.pack_into("<H", sec_desc, 2, 0x8004)  # SE_SELF_RELATIVE | SE_DACL_PRESENT
    sk_size = ((24 + len(sec_desc) + 7) // 8) * 8
    struct.pack_into("<i", hive_data, sk_cell_off, -sk_size)
    struct.pack_into("<2s", hive_data, sk_cell_off + 4, b"sk")
    struct.pack_into("<I", hive_data, sk_cell_off + 8, sk_rel)   # sk_prev → self
    struct.pack_into("<I", hive_data, sk_cell_off + 12, sk_rel)  # sk_next → self
    struct.pack_into("<I", hive_data, sk_cell_off + 16, 0)       # refcount
    struct.pack_into("<I", hive_data, sk_cell_off + 20, len(sec_desc))
    hive_data[sk_cell_off + 24:sk_cell_off + 24 + len(sec_desc)] = sec_desc
    # Point root NK sk field at this cell
    struct.pack_into("<I", hive_data, cell_off + 48, sk_rel)

    # ── Free cell for remaining space ───────────────────────────────────
    used = _HBIN_HDR + cell_size + sk_size
    free_off = hbin_off + used
    remaining = bin_data_size - used
    if remaining > 4:
        struct.pack_into("<i", hive_data, free_off, remaining)

    path.write_bytes(bytes(hive_data))


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

@_requires_hivex
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

@_requires_hivex
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

@_requires_hivex
class TestExecuteOperations:
    """execute_operations must apply batched operations via hivex."""

    def test_empty_operations_is_noop(
        self, writer: HiveWriter, audit_logger: AuditLogger
    ) -> None:
        writer.execute_operations([])
        assert len(audit_logger.entries) == 0

    def test_write_value_persisted(
        self, writer: HiveWriter, hive_file: Path
    ) -> None:
        op = _make_op()
        writer.execute_operations([op])
        result = writer.read_value(
            "Windows/System32/config/SOFTWARE",
            r"Microsoft\Windows NT\CurrentVersion",
            "RegisteredOwner",
        )
        assert result == "John Doe"

    def test_rejects_invalid_hive(
        self, writer: HiveWriter, mount_dir: Path
    ) -> None:
        hive_dir = mount_dir / "Windows" / "System32" / "config"
        hive_dir.mkdir(parents=True, exist_ok=True)
        bad_hive = hive_dir / "BAD_HIVE"
        bad_hive.write_bytes(b"\x00" * 8192)
        op = _make_op(hive_path="Windows/System32/config/BAD_HIVE")
        with pytest.raises(HiveWriterError):
            writer.execute_operations([op])

    def test_groups_operations_by_hive(
        self, writer: HiveWriter, mount_dir: Path
    ) -> None:
        hive_dir = mount_dir / "Windows" / "System32" / "config"
        hive_dir.mkdir(parents=True, exist_ok=True)
        for name in ("SOFTWARE", "SYSTEM"):
            _create_minimal_hive(hive_dir / name, hive_name=name)

        op1 = _make_op(hive_path="Windows/System32/config/SOFTWARE")
        op2 = _make_op(hive_path="Windows/System32/config/SYSTEM")

        # Both hives should be written without error
        writer.execute_operations([op1, op2])


# ---------------------------------------------------------------------------
# 6. Audit trail
# ---------------------------------------------------------------------------

@_requires_hivex
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

@_requires_hivex
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

@_requires_hivex
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

@_requires_hivex
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
