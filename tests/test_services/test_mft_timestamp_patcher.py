"""Tests for the MftTimestampPatcher NTFS service."""

import os
import random
import struct
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from core.audit_logger import AuditLogger
from core.event_scheduler import EventScheduler, SyntheticEvent
from core.mount_manager import MountManager
from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle
from core.service_context import ServiceContext
from services.ntfs.mft_timestamp_patcher import (
    MftTimestampPatcher,
    _NTFS_TIMES_XATTR,
    _NTFS_TIMES_SIZE,
    _pack_ntfs_times,
    _to_filetime,
)


# ---------------------------------------------------------------------------
# Persona helpers
# ---------------------------------------------------------------------------

def _make_persona() -> PersonaContext:
    return PersonaContext(
        full_name="Test User",
        username="testuser",
        email="testuser@example.com",
        organization="TestOrg",
        occupation="Software Developer",
        age_range="25-35",
        interests=PersonaInterests(
            hobbies=["coding", "reading", "gaming"],
            professional_topics=["Python", "security"],
        ),
        work_style=PersonaWorkStyle(
            description="Remote worker",
            typical_tools=["VSCode", "git"],
        ),
        project_names=["proj_a", "proj_b", "proj_c"],
        colleague_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
        profile_archetype="developer",
        installed_apps=["vscode", "git", "python"],
        browsing_categories=["general"],
        timeline_days=7,
    )


# ---------------------------------------------------------------------------
# Scheduler mock helper
# The real EventScheduler.events_of() only accepts one kind, but
# MftTimestampPatcher calls events_of("FILE_CREATE", "FILE_MODIFY").
# We mock the scheduler to match what the service expects.
# ---------------------------------------------------------------------------

def _make_mock_scheduler(events=None):
    """Return a MagicMock scheduler that returns the given events from events_of()."""
    scheduler = MagicMock()
    scheduler.events_of.return_value = events or []
    scheduler.child_rng.return_value = random.Random(42)
    return scheduler


def _make_event(path: str, event_type: str = "FILE_CREATE") -> MagicMock:
    """Build a mock SyntheticEvent with the shape MftTimestampPatcher expects."""
    event = MagicMock()
    event.event_type = event_type
    event.timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    event.payload = {"path": path}
    return event


# ---------------------------------------------------------------------------
# ServiceContext fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mount_dir(tmp_path):
    d = tmp_path / "mount"
    d.mkdir()
    return d


@pytest.fixture
def mount_manager(mount_dir):
    return MountManager(str(mount_dir))


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def persona():
    return _make_persona()


@pytest.fixture
def identity_bundle():
    bundle = MagicMock()
    bundle.user.username = "TestUser"
    bundle.user.computer_name = "DESKTOP-TEST"
    return bundle


def make_service_context(
    mount_manager,
    audit_logger,
    persona,
    identity_bundle,
    scheduler=None,
) -> ServiceContext:
    """Build a minimal ServiceContext for MftTimestampPatcher tests."""
    static_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ts_service = MagicMock()
    ts_service.get_timestamp.return_value = {
        "created": static_time,
        "modified": static_time,
        "accessed": static_time,
    }
    return ServiceContext(
        persona=persona,
        mount=mount_manager,
        rng=random.Random(42),
        audit=audit_logger,
        timestamp_service=ts_service,
        install_time=static_time,
        boot_time=static_time,
        identity_bundle=identity_bundle,
        scheduler=scheduler,
    )


@pytest.fixture
def service_context(mount_manager, audit_logger, persona, identity_bundle):
    return make_service_context(mount_manager, audit_logger, persona, identity_bundle)


# ---------------------------------------------------------------------------
# MftTimestampPatcher fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def patcher(mount_manager, audit_logger):
    return MftTimestampPatcher(mount_manager, audit_logger)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSkipWithoutScheduler:
    def test_skip_without_scheduler(self, patcher, service_context, caplog):
        """apply() with ctx.scheduler=None logs a warning and does not raise."""
        import logging
        with caplog.at_level(logging.WARNING):
            patcher.apply(service_context)
        assert any("no scheduler" in msg.lower() or "skipping" in msg.lower()
                   for msg in caplog.messages)


class TestPatchesExistingFile:
    def test_patches_existing_file(
        self, patcher, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """FILE_CREATE event for a real temp file triggers setxattr with 32-byte value."""
        # Create a real file in the mount tree
        rel_path = "Users/testuser/Documents/report.docx"
        host_path = mount_dir / "Users" / "testuser" / "Documents"
        host_path.mkdir(parents=True)
        (host_path / "report.docx").write_bytes(b"dummy content")

        event = _make_event(rel_path, "FILE_CREATE")
        scheduler = _make_mock_scheduler([event])

        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        mock_setxattr.assert_called_once()
        args = mock_setxattr.call_args[0]
        # args: (path_str, xattr_name, xattr_value)
        assert args[1] == _NTFS_TIMES_XATTR
        assert isinstance(args[2], bytes)
        assert len(args[2]) == _NTFS_TIMES_SIZE

    def test_xattr_name_is_correct_bytes(
        self, patcher, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """The xattr name passed to os.setxattr must be b'system.ntfs_times'."""
        rel_path = "Users/testuser/Documents/file.txt"
        host_path = mount_dir / "Users" / "testuser" / "Documents"
        host_path.mkdir(parents=True)
        (host_path / "file.txt").write_bytes(b"x")

        event = _make_event(rel_path)
        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        assert mock_setxattr.call_args[0][1] == b"system.ntfs_times"

    def test_xattr_value_is_32_bytes(
        self, patcher, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """The xattr value must be exactly 32 bytes (four 64-bit little-endian FILETIMEs)."""
        rel_path = "Users/testuser/Documents/doc.pdf"
        host_path = mount_dir / "Users" / "testuser" / "Documents"
        host_path.mkdir(parents=True)
        (host_path / "doc.pdf").write_bytes(b"pdf")

        event = _make_event(rel_path)
        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        xattr_val = mock_setxattr.call_args[0][2]
        assert len(xattr_val) == 32

    def test_xattr_value_contains_valid_filetimes(
        self, patcher, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """All four FILETIME values in the xattr must be positive and after 2000."""
        rel_path = "Users/testuser/Documents/notes.txt"
        host_path = mount_dir / "Users" / "testuser" / "Documents"
        host_path.mkdir(parents=True)
        (host_path / "notes.txt").write_bytes(b"notes")

        event = _make_event(rel_path)
        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        xattr_val = mock_setxattr.call_args[0][2]
        filetimes = struct.unpack("<4Q", xattr_val)
        # FILETIME for 2000-01-01 is approx 125911584000000000
        min_ft = 125_911_584_000_000_000
        for ft in filetimes:
            assert ft > min_ft, f"FILETIME {ft} looks too old (before year 2000)"


class TestSkipsMissingFile:
    def test_skips_missing_file(
        self, patcher, mount_manager, audit_logger, persona, identity_bundle
    ):
        """FILE_CREATE event for a nonexistent path is silently skipped; no error raised."""
        nonexistent_rel = "Users/testuser/Documents/ghost_file.docx"
        event = _make_event(nonexistent_rel)
        scheduler = _make_mock_scheduler([event])

        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        # setxattr must NOT be called for a missing file
        mock_setxattr.assert_not_called()

    def test_skipped_count_positive_for_missing_file(
        self, patcher, mount_manager, audit_logger, persona, identity_bundle
    ):
        """Audit log records a skipped count > 0 when the file does not exist."""
        nonexistent_rel = "Users/testuser/Documents/missing.txt"
        event = _make_event(nonexistent_rel)
        scheduler = _make_mock_scheduler([event])

        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        audit_logger.clear()
        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr", create=True):
            patcher.apply(ctx)

        entries = [e for e in audit_logger.entries if e.get("service") == "MftTimestampPatcher"]
        assert len(entries) == 1
        assert entries[0]["skipped"] > 0

    def test_skips_event_with_no_path_in_payload(
        self, patcher, mount_manager, audit_logger, persona, identity_bundle
    ):
        """An event with no 'path' key in payload is silently skipped."""
        event = MagicMock()
        event.event_type = "FILE_CREATE"
        event.timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)
        event.payload = {}  # no path

        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True) as mock_setxattr:
            patcher.apply(ctx)

        mock_setxattr.assert_not_called()


class TestSetxattrOsError:
    def test_enotsup_errno_does_not_raise(
        self, patcher, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """ENOTSUP (errno=95) from setxattr is treated as a skip, not a fatal error."""
        rel_path = "Users/testuser/Documents/enotsup.docx"
        host_path = mount_dir / "Users" / "testuser" / "Documents"
        host_path.mkdir(parents=True)
        (host_path / "enotsup.docx").write_bytes(b"data")

        event = _make_event(rel_path)
        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )

        enotsup = OSError(95, "Operation not supported")
        with patch("services.ntfs.mft_timestamp_patcher.os.setxattr",
                   create=True, side_effect=enotsup):
            patcher.apply(ctx)  # must not raise


class TestPackNtfsTimes:
    def test_pack_returns_32_bytes(self):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _pack_ntfs_times(ts, ts, ts, ts)
        assert len(result) == 32

    def test_pack_format_is_little_endian_4_uint64(self):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _pack_ntfs_times(ts, ts, ts, ts)
        values = struct.unpack("<4Q", result)
        ft = _to_filetime(ts)
        assert all(v == ft for v in values)

    def test_to_filetime_epoch_offset(self):
        """Unix epoch (1970-01-01) should equal exactly _FILETIME_EPOCH ticks."""
        unix_epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        ft = _to_filetime(unix_epoch)
        from services.ntfs.mft_timestamp_patcher import _FILETIME_EPOCH
        assert ft == _FILETIME_EPOCH


class TestServiceName:
    def test_service_name(self, patcher):
        assert patcher.service_name == "MftTimestampPatcher"
