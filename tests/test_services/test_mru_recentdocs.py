"""Tests for the MruRecentDocs registry service."""

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
from services.registry.mru_recentdocs import (
    MruRecentDocs,
    MruRecentDocsError,
    _HOME_RECENT_DOCS,
    _OFFICE_RECENT_DOCS,
    _DEVELOPER_RECENT_DOCS,
    _RECENTDOCS_KEY,
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
) -> MruRecentDocs:
    """MruRecentDocs wired to mock HiveWriter and real AuditLogger."""
    return MruRecentDocs(mock_hive_writer, audit_logger)


# ---------------------------------------------------------------------------
# 1. Construction & BaseService interface
# ---------------------------------------------------------------------------

class TestMruRecentDocsInit:
    """MruRecentDocs must satisfy the BaseService contract."""

    def test_service_name(self, service: MruRecentDocs) -> None:
        assert service.service_name == "MruRecentDocs"

    def test_service_name_is_string(self, service: MruRecentDocs) -> None:
        assert isinstance(service.service_name, str)


# ---------------------------------------------------------------------------
# 2. apply() context validation
# ---------------------------------------------------------------------------

class TestApplyContextValidation:
    """apply() must validate the context before delegating."""

    def test_missing_profile_type_raises(
        self, service: MruRecentDocs
    ) -> None:
        with pytest.raises(MruRecentDocsError, match="profile_type"):
            service.apply({"username": "test"})

    def test_missing_username_raises(
        self, service: MruRecentDocs
    ) -> None:
        with pytest.raises(MruRecentDocsError, match="username"):
            service.apply({"profile_type": "home"})

    def test_invalid_profile_type_raises(
        self, service: MruRecentDocs
    ) -> None:
        with pytest.raises(MruRecentDocsError, match="Unknown profile"):
            service.apply({"profile_type": "gamer", "username": "test"})

    def test_valid_context_accepted(
        self, service: MruRecentDocs
    ) -> None:
        service.apply({"profile_type": "home", "username": "test"})


# ---------------------------------------------------------------------------
# 3. MRUListEx encoding
# ---------------------------------------------------------------------------

class TestMruListExEncoding:
    """encode_mrulistex must produce correct binary format."""

    def test_single_entry(self) -> None:
        result = MruRecentDocs.encode_mrulistex(1)
        assert result == struct.pack("<I", 0) + struct.pack("<I", 0xFFFFFFFF)

    def test_three_entries(self) -> None:
        result = MruRecentDocs.encode_mrulistex(3)
        expected = (
            struct.pack("<I", 0)
            + struct.pack("<I", 1)
            + struct.pack("<I", 2)
            + struct.pack("<I", 0xFFFFFFFF)
        )
        assert result == expected

    def test_zero_entries(self) -> None:
        result = MruRecentDocs.encode_mrulistex(0)
        assert result == struct.pack("<I", 0xFFFFFFFF)

    def test_length_formula(self) -> None:
        for n in (0, 1, 5, 10):
            result = MruRecentDocs.encode_mrulistex(n)
            assert len(result) == (n + 1) * 4


# ---------------------------------------------------------------------------
# 4. RecentDocs entry encoding
# ---------------------------------------------------------------------------

class TestRecentDocsEntryEncoding:
    """encode_recentdocs_entry must produce correct binary format."""

    def test_contains_utf16le_filename(self) -> None:
        result = MruRecentDocs.encode_recentdocs_entry("test.docx")
        name_part = "test.docx".encode("utf-16-le") + b"\x00\x00"
        assert result[:len(name_part)] == name_part

    def test_has_8_byte_padding(self) -> None:
        result = MruRecentDocs.encode_recentdocs_entry("file.txt")
        name_len = len("file.txt".encode("utf-16-le")) + 2  # + null term
        padding = result[name_len:]
        assert padding == b"\x00" * 8

    def test_total_length(self) -> None:
        filename = "report.pdf"
        result = MruRecentDocs.encode_recentdocs_entry(filename)
        expected_len = len(filename.encode("utf-16-le")) + 2 + 8
        assert len(result) == expected_len


# ---------------------------------------------------------------------------
# 5. Operation building
# ---------------------------------------------------------------------------

class TestOperationBuilding:
    """build_operations must produce correct registry operations."""

    def test_home_ops_count(self, service: MruRecentDocs) -> None:
        filenames = _HOME_RECENT_DOCS
        ops = service.build_operations(filenames, "testuser")
        # Main key: 1 MRUListEx + 5 entries = 6
        # Extensions: group by ext → each group gets 1 MRU + N entries
        # .jpg(1), .pdf(1), .xlsx(1), .mp4(1), .txt(1) → 5 groups × 2 = 10
        # Total: 6 + 10 = 16
        assert len(ops) == 16

    def test_all_ops_target_ntuser_hive(
        self, service: MruRecentDocs
    ) -> None:
        ops = service.build_operations(["test.txt"], "john")
        for op in ops:
            assert "NTUSER.DAT" in op.hive_path
            assert "john" in op.hive_path

    def test_mrulistex_is_binary(self, service: MruRecentDocs) -> None:
        ops = service.build_operations(["a.txt"], "user")
        mru_ops = [o for o in ops if o.value_name == "MRUListEx"]
        assert len(mru_ops) >= 1
        for op in mru_ops:
            assert op.value_type == RegistryValueType.REG_BINARY

    def test_numbered_entries_are_binary(
        self, service: MruRecentDocs
    ) -> None:
        ops = service.build_operations(["a.txt", "b.pdf"], "user")
        numbered = [o for o in ops if o.value_name.isdigit()]
        assert all(o.value_type == RegistryValueType.REG_BINARY for o in numbered)

    def test_main_key_path(self, service: MruRecentDocs) -> None:
        ops = service.build_operations(["a.txt"], "user")
        main_ops = [o for o in ops if o.key_path == _RECENTDOCS_KEY]
        assert len(main_ops) >= 1

    def test_extension_subkey_created(
        self, service: MruRecentDocs
    ) -> None:
        ops = service.build_operations(["report.docx"], "user")
        ext_ops = [
            o for o in ops
            if o.key_path == rf"{_RECENTDOCS_KEY}\.docx"
        ]
        # MRUListEx + 1 entry = 2
        assert len(ext_ops) == 2

    def test_multiple_same_extension_grouped(
        self, service: MruRecentDocs
    ) -> None:
        files = ["a.docx", "b.docx", "c.docx"]
        ops = service.build_operations(files, "user")
        ext_ops = [
            o for o in ops
            if o.key_path == rf"{_RECENTDOCS_KEY}\.docx"
        ]
        # MRUListEx + 3 entries = 4
        assert len(ext_ops) == 4


# ---------------------------------------------------------------------------
# 6. Extension grouping
# ---------------------------------------------------------------------------

class TestExtensionGrouping:
    """_group_by_extension must correctly group filenames."""

    def test_single_extension(self) -> None:
        result = MruRecentDocs._group_by_extension(["a.txt", "b.txt"])
        assert result == {".txt": ["a.txt", "b.txt"]}

    def test_multiple_extensions(self) -> None:
        result = MruRecentDocs._group_by_extension(
            ["a.txt", "b.pdf", "c.txt"]
        )
        assert set(result.keys()) == {".txt", ".pdf"}
        assert result[".txt"] == ["a.txt", "c.txt"]
        assert result[".pdf"] == ["b.pdf"]

    def test_no_extension(self) -> None:
        result = MruRecentDocs._group_by_extension(["README"])
        assert ".unknown" in result


# ---------------------------------------------------------------------------
# 7. HiveWriter delegation
# ---------------------------------------------------------------------------

class TestHiveWriterDelegation:
    """write_recent_docs must delegate to HiveWriter."""

    def test_execute_called(
        self,
        service: MruRecentDocs,
        mock_hive_writer: MagicMock,
    ) -> None:
        service.write_recent_docs("home", "testuser")
        mock_hive_writer.execute_operations.assert_called_once()

    def test_hive_writer_error_wrapped(
        self,
        service: MruRecentDocs,
        mock_hive_writer: MagicMock,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "fail"
        )
        with pytest.raises(MruRecentDocsError, match="Failed"):
            service.write_recent_docs("office", "user")


# ---------------------------------------------------------------------------
# 8. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Successful writes must produce audit entries."""

    def test_audit_on_success(
        self, service: MruRecentDocs, audit_logger: AuditLogger
    ) -> None:
        service.write_recent_docs("developer", "devuser")
        assert len(audit_logger.entries) >= 1
        entry = audit_logger.entries[-1]
        assert entry["service"] == "MruRecentDocs"
        assert entry["operation"] == "write_recent_docs_complete"
        assert entry["profile_type"] == "developer"
        assert entry["username"] == "devuser"
        assert "timestamp" in entry
