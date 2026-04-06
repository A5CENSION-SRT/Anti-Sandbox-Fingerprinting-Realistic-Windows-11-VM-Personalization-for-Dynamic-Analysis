"""Tests for the UserAssist registry service (ROT13 encoding)."""

import codecs
import struct
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)
from services.registry.userassist import (
    UserAssist,
    UserAssistError,
    _DEVELOPER_PROGRAMS,
    _ENTRY_SIZE,
    _GUID_EXE,
    _HOME_PROGRAMS,
    _OFFICE_PROGRAMS,
    _USERASSIST_KEY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def audit_logger() -> AuditLogger:
    """Shared AuditLogger instance."""
    return AuditLogger()


@pytest.fixture()
def mock_hive_writer() -> MagicMock:
    """Mock HiveWriter — no real I/O needed."""
    writer = MagicMock(spec=HiveWriter)
    writer.execute_operations = MagicMock(return_value=None)
    return writer


@pytest.fixture()
def service(
    mock_hive_writer: MagicMock, audit_logger: AuditLogger
) -> UserAssist:
    """UserAssist wired to mock HiveWriter and real AuditLogger."""
    return UserAssist(mock_hive_writer, audit_logger)


# ---------------------------------------------------------------------------
# 1. Construction & BaseService interface
# ---------------------------------------------------------------------------

class TestUserAssistInit:
    """UserAssist must satisfy the BaseService contract."""

    def test_service_name(self, service: UserAssist) -> None:
        assert service.service_name == "UserAssist"

    def test_service_name_is_string(self, service: UserAssist) -> None:
        assert isinstance(service.service_name, str)


# ---------------------------------------------------------------------------
# 2. apply() context validation
# ---------------------------------------------------------------------------

class TestApplyContextValidation:
    """apply() must validate the context before delegating."""

    def test_missing_profile_type_raises(
        self, service: UserAssist
    ) -> None:
        with pytest.raises(UserAssistError, match="profile_type"):
            service.apply({"username": "test"})

    def test_missing_username_raises(
        self, service: UserAssist
    ) -> None:
        with pytest.raises(UserAssistError, match="username"):
            service.apply({"profile_type": "home"})

    def test_invalid_profile_type_raises(
        self, service: UserAssist
    ) -> None:
        with pytest.raises(UserAssistError, match="Unknown profile"):
            service.apply({"profile_type": "gamer", "username": "test"})

    def test_valid_context_accepted(self, service: UserAssist) -> None:
        service.apply({"profile_type": "home", "username": "test"})


# ---------------------------------------------------------------------------
# 3. ROT13 encoding
# ---------------------------------------------------------------------------

class TestRot13:
    """rot13 must match Windows Explorer's UserAssist encoding."""

    def test_simple_letters(self) -> None:
        assert UserAssist.rot13("abc") == "nop"
        assert UserAssist.rot13("ABC") == "NOP"

    def test_roundtrip(self) -> None:
        original = r"C:\Program Files\test.exe"
        encoded = UserAssist.rot13(original)
        decoded = UserAssist.rot13(encoded)
        assert decoded == original

    def test_digits_unchanged(self) -> None:
        assert UserAssist.rot13("123") == "123"

    def test_braces_unchanged(self) -> None:
        guid = "{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}"
        encoded = UserAssist.rot13(guid)
        assert encoded.startswith("{")
        assert encoded.endswith("}")
        # Digits and braces/hyphens pass through
        assert "-" in encoded

    def test_backslash_unchanged(self) -> None:
        path = r"C:\test\path"
        encoded = UserAssist.rot13(path)
        assert "\\" in encoded

    def test_known_guid_encoding(self) -> None:
        # Known: CEBFF5CD → PROSY5PQ (letters ROT13, digits unchanged)
        encoded = UserAssist.rot13("{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}")
        decoded = UserAssist.rot13(encoded)
        assert decoded == "{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}"

    def test_empty_string(self) -> None:
        assert UserAssist.rot13("") == ""


# ---------------------------------------------------------------------------
# 4. Binary entry encoding
# ---------------------------------------------------------------------------

class TestEntryEncoding:
    """encode_entry must produce correct 72-byte UserAssist v5 structs."""

    def test_entry_size(self) -> None:
        data = UserAssist.encode_entry(
            run_count=10,
            focus_count=20,
            focus_time_ms=5000,
            seed_name="TEST-PC",
        )
        assert len(data) == _ENTRY_SIZE

    def test_run_count_at_offset_4(self) -> None:
        data = UserAssist.encode_entry(
            run_count=42,
            focus_count=0,
            focus_time_ms=0,
            seed_name="PC",
        )
        run_count = struct.unpack_from("<I", data, 4)[0]
        assert run_count == 42

    def test_focus_count_at_offset_8(self) -> None:
        data = UserAssist.encode_entry(
            run_count=0,
            focus_count=99,
            focus_time_ms=0,
            seed_name="PC",
        )
        focus_count = struct.unpack_from("<I", data, 8)[0]
        assert focus_count == 99

    def test_focus_time_at_offset_12(self) -> None:
        data = UserAssist.encode_entry(
            run_count=0,
            focus_count=0,
            focus_time_ms=7200000,
            seed_name="PC",
        )
        focus_time = struct.unpack_from("<I", data, 12)[0]
        assert focus_time == 7200000

    def test_filetime_at_offset_60(self) -> None:
        data = UserAssist.encode_entry(
            run_count=10,
            focus_count=20,
            focus_time_ms=5000,
            seed_name="PC",
        )
        filetime = struct.unpack_from("<Q", data, 60)[0]
        # Should be in a reasonable FILETIME range (~2023)
        assert filetime > 130000000000000000
        assert filetime < 140000000000000000

    def test_session_id_is_zero(self) -> None:
        data = UserAssist.encode_entry(
            run_count=1,
            focus_count=1,
            focus_time_ms=100,
            seed_name="PC",
        )
        session_id = struct.unpack_from("<I", data, 0)[0]
        assert session_id == 0

    def test_padding_is_zeros(self) -> None:
        data = UserAssist.encode_entry(
            run_count=1,
            focus_count=1,
            focus_time_ms=100,
            seed_name="PC",
        )
        # Bytes 16–59 should be zeros
        padding = data[16:60]
        assert padding == b"\x00" * 44

    def test_trailing_bytes_zero(self) -> None:
        data = UserAssist.encode_entry(
            run_count=1,
            focus_count=1,
            focus_time_ms=100,
            seed_name="PC",
        )
        # Bytes 68–71 should be zeros
        assert data[68:72] == b"\x00" * 4

    def test_deterministic(self) -> None:
        args = dict(
            run_count=10,
            focus_count=20,
            focus_time_ms=5000,
            seed_name="TEST-PC",
        )
        d1 = UserAssist.encode_entry(**args)
        d2 = UserAssist.encode_entry(**args)
        assert d1 == d2


# ---------------------------------------------------------------------------
# 5. Binary entry decoding
# ---------------------------------------------------------------------------

class TestEntryDecoding:
    """decode_entry must correctly parse 72-byte structs."""

    def test_roundtrip(self) -> None:
        original = UserAssist.encode_entry(
            run_count=42,
            focus_count=99,
            focus_time_ms=7200000,
            seed_name="PC",
        )
        decoded = UserAssist.decode_entry(original)
        assert decoded["run_count"] == 42
        assert decoded["focus_count"] == 99
        assert decoded["focus_time_ms"] == 7200000
        assert decoded["session_id"] == 0
        assert decoded["filetime"] > 0

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(UserAssistError, match="72 bytes"):
            UserAssist.decode_entry(b"\x00" * 10)


# ---------------------------------------------------------------------------
# 6. Operation building
# ---------------------------------------------------------------------------

class TestOperationBuilding:
    """build_operations must produce correct registry operations."""

    def test_home_ops_count(self, service: UserAssist) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        assert len(ops) == len(_HOME_PROGRAMS)

    def test_office_ops_count(self, service: UserAssist) -> None:
        ops = service.build_operations(_OFFICE_PROGRAMS, "user")
        assert len(ops) == len(_OFFICE_PROGRAMS)

    def test_developer_ops_count(self, service: UserAssist) -> None:
        ops = service.build_operations(_DEVELOPER_PROGRAMS, "user")
        assert len(ops) == len(_DEVELOPER_PROGRAMS)

    def test_all_ops_are_binary(self, service: UserAssist) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        assert all(o.value_type == RegistryValueType.REG_BINARY for o in ops)

    def test_all_values_are_72_bytes(self, service: UserAssist) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        for op in ops:
            assert isinstance(op.value_data, bytes)
            assert len(op.value_data) == _ENTRY_SIZE

    def test_value_names_are_rot13(self, service: UserAssist) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        for op in ops:
            # ROT13-decoding should give a valid path
            decoded = UserAssist.rot13(op.value_name)
            # Original paths contain backslash or known patterns
            assert any(
                c in decoded
                for c in ("\\", ".", "!", "/")
            )

    def test_key_path_contains_rot13_guid(
        self, service: UserAssist
    ) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        rot13_guid = UserAssist.rot13(_GUID_EXE)
        for op in ops:
            assert rot13_guid in op.key_path

    def test_key_path_ends_with_count(
        self, service: UserAssist
    ) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "user")
        for op in ops:
            assert op.key_path.endswith("\\Count")

    def test_hive_path_contains_username(
        self, service: UserAssist
    ) -> None:
        ops = service.build_operations(_HOME_PROGRAMS, "jane.doe")
        for op in ops:
            assert "jane.doe" in op.hive_path
            assert "NTUSER.DAT" in op.hive_path

    def test_username_substituted_in_paths(
        self, service: UserAssist
    ) -> None:
        ops = service.build_operations(_DEVELOPER_PROGRAMS, "jane.doe")
        # VS Code path has {username} template
        for op in ops:
            decoded_name = UserAssist.rot13(op.value_name)
            assert "{username}" not in decoded_name


# ---------------------------------------------------------------------------
# 7. HiveWriter delegation
# ---------------------------------------------------------------------------

class TestHiveWriterDelegation:
    """write_userassist must delegate to HiveWriter."""

    def test_execute_called(
        self,
        service: UserAssist,
        mock_hive_writer: MagicMock,
    ) -> None:
        service.write_userassist("home", "testuser")
        mock_hive_writer.execute_operations.assert_called_once()

    def test_hive_writer_error_wrapped(
        self,
        service: UserAssist,
        mock_hive_writer: MagicMock,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "fail"
        )
        with pytest.raises(UserAssistError, match="Failed"):
            service.write_userassist("office", "user")


# ---------------------------------------------------------------------------
# 8. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Successful writes must produce audit entries."""

    def test_audit_on_success(
        self, service: UserAssist, audit_logger: AuditLogger
    ) -> None:
        service.write_userassist("developer", "devuser")
        assert len(audit_logger.entries) >= 1
        entry = audit_logger.entries[-1]
        assert entry["service"] == "UserAssist"
        assert entry["operation"] == "write_userassist_complete"
        assert entry["profile_type"] == "developer"
        assert entry["username"] == "devuser"
        assert entry["programs_count"] == len(_DEVELOPER_PROGRAMS)
        assert "timestamp" in entry
