"""Unit tests for CrossWriter service."""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from services.filesystem.cross_writer import CrossWriter, CrossWriterError


@pytest.fixture
def mount_dir(tmp_path):
    """Provide a temporary mount root directory."""
    return tmp_path / "mount"


@pytest.fixture
def mount_manager(mount_dir):
    mount_dir.mkdir()
    return MountManager(str(mount_dir))


@pytest.fixture
def timestamp_service():
    svc = MagicMock()
    svc.get_timestamp.return_value = {
        "created": datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc),
        "modified": datetime(2025, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
        "accessed": datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    }
    return svc


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def writer(mount_manager, timestamp_service, audit_logger):
    return CrossWriter(mount_manager, timestamp_service, audit_logger)


class TestCreateNestedDirectories:
    """Test 1: Creates nested directories."""

    def test_nested_dirs_created(self, writer, mount_manager):
        tree = {
            "Users": {
                "Sumukha": {
                    "Desktop": {},
                    "Documents": {},
                }
            }
        }
        writer.apply_tree(tree)
        root = mount_manager.root
        assert (root / "Users" / "Sumukha" / "Desktop").is_dir()
        assert (root / "Users" / "Sumukha" / "Documents").is_dir()

    def test_deeply_nested_dirs(self, writer, mount_manager):
        tree = {"a": {"b": {"c": {"d": {"e": {}}}}}}
        writer.apply_tree(tree)
        assert (mount_manager.root / "a" / "b" / "c" / "d" / "e").is_dir()


class TestWriteFileWithContent:
    """Test 2: Writes file with content."""

    def test_text_content(self, writer, mount_manager):
        tree = {
            "readme.txt": {
                "type": "file",
                "content": "Hello, world!",
                "timestamp_event": "document_create",
            }
        }
        writer.apply_tree(tree)
        path = mount_manager.root / "readme.txt"
        assert path.exists()
        assert path.read_text() == "Hello, world!"

    def test_binary_content(self, writer, mount_manager):
        data = b"\x89PNG\r\n\x1a\n"
        tree = {
            "image.png": {
                "type": "file",
                "binary_content": data,
                "timestamp_event": "media_create",
            }
        }
        writer.apply_tree(tree)
        assert (mount_manager.root / "image.png").read_bytes() == data

    def test_empty_content(self, writer, mount_manager):
        tree = {
            "empty.txt": {
                "type": "file",
                "timestamp_event": "document_create",
            }
        }
        writer.apply_tree(tree)
        assert (mount_manager.root / "empty.txt").read_bytes() == b""

    def test_file_in_nested_dir(self, writer, mount_manager):
        tree = {
            "Users": {
                "Sumukha": {
                    "report.docx": {
                        "type": "file",
                        "content": "Meeting notes",
                        "timestamp_event": "document_edit",
                    }
                }
            }
        }
        writer.apply_tree(tree)
        path = mount_manager.root / "Users" / "Sumukha" / "report.docx"
        assert path.read_text() == "Meeting notes"


class TestApplyAttributes:
    """Test 3: Applies hidden attribute (and others).

    On non-Windows platforms, attribute application is a no-op,
    so we test that it doesn't raise and the file is still created.
    """

    def test_hidden_attribute_no_error(self, writer, mount_manager):
        tree = {
            "secret.dat": {
                "type": "file",
                "content": "hidden data",
                "attributes": ["hidden"],
                "timestamp_event": "app_cache",
            }
        }
        writer.apply_tree(tree)
        assert (mount_manager.root / "secret.dat").exists()

    def test_multiple_attributes(self, writer, mount_manager):
        tree = {
            "sys.dat": {
                "type": "file",
                "content": "sys",
                "attributes": ["hidden", "system", "archive"],
                "timestamp_event": "system_file",
            }
        }
        writer.apply_tree(tree)
        assert (mount_manager.root / "sys.dat").exists()


class TestApplyTimestamps:
    """Test 4: Applies timestamps."""

    def test_timestamps_applied(self, writer, mount_manager, timestamp_service):
        tree = {
            "timed.txt": {
                "type": "file",
                "content": "data",
                "timestamp_event": "document_edit",
            }
        }
        writer.apply_tree(tree)
        timestamp_service.get_timestamp.assert_called_with("document_edit")

        path = mount_manager.root / "timed.txt"
        stat = path.stat()
        assert abs(stat.st_mtime - datetime(2025, 6, 15, 11, 0, 0, tzinfo=timezone.utc).timestamp()) < 1
        assert abs(stat.st_atime - datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()) < 1


class TestLogsOperations:
    """Test 5: Logs operations."""

    def test_directory_logged(self, writer, audit_logger):
        tree = {"TestDir": {}}
        writer.apply_tree(tree)
        ops = [e["operation"] for e in audit_logger.entries]
        assert "create_directory" in ops

    def test_file_logged(self, writer, audit_logger):
        tree = {
            "log_test.txt": {
                "type": "file",
                "content": "data",
                "timestamp_event": "test",
            }
        }
        writer.apply_tree(tree)
        file_entries = [
            e for e in audit_logger.entries if e["operation"] == "create_file"
        ]
        assert len(file_entries) == 1
        assert file_entries[0]["service"] == "CrossWriter"
        assert "log_test.txt" in file_entries[0]["path"]
        assert file_entries[0]["timestamp_event"] == "test"

    def test_all_operations_logged(self, writer, audit_logger, mount_manager):
        tree = {
            "Dir": {
                "file.txt": {
                    "type": "file",
                    "content": "x",
                    "timestamp_event": "t",
                }
            }
        }
        writer.apply_tree(tree)
        ops = [e["operation"] for e in audit_logger.entries]
        assert "create_directory" in ops
        assert "create_file" in ops


class TestPathEscapePrevention:
    """Test 6: Prevents path escape."""

    def test_dotdot_in_base_path(self, writer):
        tree = {"safe.txt": {"type": "file", "content": "", "timestamp_event": "t"}}
        with pytest.raises((CrossWriterError, ValueError)):
            writer.apply_tree(tree, base_path="../../etc")

    def test_dotdot_in_tree_key(self, writer):
        tree = {
            "..": {
                "..": {
                    "etc": {
                        "passwd": {
                            "type": "file",
                            "content": "evil",
                            "timestamp_event": "t",
                        }
                    }
                }
            }
        }
        with pytest.raises(CrossWriterError):
            writer.apply_tree(tree)


class TestSchemaValidation:
    """Test 7: Rejects invalid schema."""

    def test_missing_timestamp_event(self, writer):
        tree = {
            "bad.txt": {
                "type": "file",
                "content": "no timestamp",
            }
        }
        with pytest.raises(CrossWriterError, match="timestamp_event"):
            writer.apply_tree(tree)

    def test_unknown_fields(self, writer):
        tree = {
            "bad.txt": {
                "type": "file",
                "timestamp_event": "t",
                "garbage_field": True,
            }
        }
        with pytest.raises(CrossWriterError, match="Unknown fields"):
            writer.apply_tree(tree)

    def test_invalid_attribute(self, writer):
        tree = {
            "bad.txt": {
                "type": "file",
                "timestamp_event": "t",
                "attributes": ["hidden", "invalid_attr"],
            }
        }
        with pytest.raises(CrossWriterError, match="Invalid attributes"):
            writer.apply_tree(tree)

    def test_both_content_types(self, writer):
        tree = {
            "bad.txt": {
                "type": "file",
                "timestamp_event": "t",
                "content": "text",
                "binary_content": b"bytes",
            }
        }
        with pytest.raises(CrossWriterError, match="both"):
            writer.apply_tree(tree)

    def test_unknown_type(self, writer):
        tree = {
            "bad": {
                "type": "symlink",
            }
        }
        with pytest.raises(CrossWriterError, match="Unknown type"):
            writer.apply_tree(tree)

    def test_non_dict_value(self, writer):
        tree = {"bad": "string_value"}
        with pytest.raises(CrossWriterError, match="Expected dict"):
            writer.apply_tree(tree)

    def test_attributes_not_list(self, writer):
        tree = {
            "bad.txt": {
                "type": "file",
                "timestamp_event": "t",
                "attributes": "hidden",
            }
        }
        with pytest.raises(CrossWriterError, match="must be a list"):
            writer.apply_tree(tree)


class TestServiceInterface:
    """Test the BaseService interface compliance."""

    def test_service_name(self, writer):
        assert writer.service_name == "CrossWriter"

    def test_apply_context(self, writer, mount_manager):
        tree = {
            "ctx.txt": {
                "type": "file",
                "content": "via context",
                "timestamp_event": "t",
            }
        }
        writer.apply({"tree_spec": tree, "base_path": ""})
        assert (mount_manager.root / "ctx.txt").read_text() == "via context"
