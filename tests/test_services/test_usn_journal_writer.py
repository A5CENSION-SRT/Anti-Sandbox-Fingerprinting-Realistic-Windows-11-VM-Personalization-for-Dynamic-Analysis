"""Tests for the UsnJournalWriter NTFS service."""

import random
import struct
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle
from core.service_context import ServiceContext
from services.ntfs.usn_journal_writer import (
    UsnJournalWriter,
    _build_usn_record,
    _V2_HEADER_SIZE,
    _USN_FILE_CREATE,
    _USN_CLOSE,
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
        occupation="Analyst",
        age_range="30-40",
        interests=PersonaInterests(
            hobbies=["reading", "hiking", "cooking"],
            professional_topics=["data", "security"],
        ),
        work_style=PersonaWorkStyle(
            description="Office worker",
            typical_tools=["Excel", "Outlook"],
        ),
        project_names=["proj_x", "proj_y", "proj_z"],
        colleague_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
        profile_archetype="office_user",
        installed_apps=["excel", "outlook", "teams"],
        browsing_categories=["general", "business"],
        timeline_days=7,
    )


# ---------------------------------------------------------------------------
# Scheduler + event helpers
# ---------------------------------------------------------------------------

def _make_mock_scheduler(events=None):
    scheduler = MagicMock()
    scheduler.events_of.return_value = events or []
    scheduler.child_rng.return_value = random.Random(42)
    return scheduler


def _make_file_event(path: str, event_type: str = "FILE_CREATE") -> MagicMock:
    event = MagicMock()
    event.event_type = event_type
    event.timestamp = datetime(2024, 3, 10, 14, 0, 0, tzinfo=timezone.utc)
    event.payload = {"path": path}
    return event


# ---------------------------------------------------------------------------
# Fixtures
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
def writer(mount_manager, audit_logger):
    return UsnJournalWriter(mount_manager, audit_logger)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSkipWithoutScheduler:
    def test_skip_without_scheduler(
        self, writer, mount_manager, audit_logger, persona, identity_bundle, caplog
    ):
        """apply() with no scheduler logs a warning and exits cleanly."""
        import logging
        ctx = make_service_context(mount_manager, audit_logger, persona, identity_bundle)
        with caplog.at_level(logging.WARNING):
            writer.apply(ctx)
        assert any("no scheduler" in m.lower() or "skipping" in m.lower()
                   for m in caplog.messages)

    def test_no_exception_without_scheduler(
        self, writer, mount_manager, audit_logger, persona, identity_bundle
    ):
        ctx = make_service_context(mount_manager, audit_logger, persona, identity_bundle)
        writer.apply(ctx)  # must not raise


class TestWritesRecordsToJournal:
    def test_writes_records_to_journal_file(
        self, writer, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """With events, USN records are written to the $UsnJrnl/$J file."""
        events = [
            _make_file_event("Users/testuser/Documents/report.docx", "FILE_CREATE"),
            _make_file_event("Users/testuser/Documents/notes.txt", "FILE_MODIFY"),
        ]
        scheduler = _make_mock_scheduler(events)
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        writer.apply(ctx)

        journal_path = mount_dir / "$Extend" / "$UsnJrnl" / "$J"
        assert journal_path.exists(), "Journal file should be created"
        assert journal_path.stat().st_size > 0, "Journal file should not be empty"

    def test_journal_file_contains_multiple_records(
        self, writer, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """Multiple events produce multiple records in the journal file."""
        events = [_make_file_event(f"Users/testuser/file_{i}.txt") for i in range(5)]
        scheduler = _make_mock_scheduler(events)
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        writer.apply(ctx)

        journal_path = mount_dir / "$Extend" / "$UsnJrnl" / "$J"
        content = journal_path.read_bytes()
        # Each record is at least _V2_HEADER_SIZE bytes
        assert len(content) >= _V2_HEADER_SIZE * 5

    def test_audit_log_records_write(
        self, writer, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """apply() emits an audit log entry with records_written count."""
        events = [_make_file_event("Users/testuser/doc.docx")]
        scheduler = _make_mock_scheduler(events)
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        audit_logger.clear()
        writer.apply(ctx)

        entries = [e for e in audit_logger.entries if e.get("service") == "UsnJournalWriter"]
        assert len(entries) == 1
        assert entries[0]["records_written"] >= 1

    def test_mount_manager_resolve_called(
        self, mount_dir, audit_logger, persona, identity_bundle
    ):
        """mount_manager.resolve() is invoked for the journal path."""
        mock_mount = MagicMock()
        journal_tmp = mount_dir / "$Extend" / "$UsnJrnl" / "$J"
        mock_mount.resolve.return_value = journal_tmp

        writer_obj = UsnJournalWriter(mock_mount, audit_logger)
        events = [_make_file_event("Users/testuser/file.txt")]
        scheduler = _make_mock_scheduler(events)
        static_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ts_service = MagicMock()
        ctx = ServiceContext(
            persona=persona,
            mount=mock_mount,
            rng=random.Random(0),
            audit=audit_logger,
            timestamp_service=ts_service,
            install_time=static_time,
            boot_time=static_time,
            identity_bundle=MagicMock(),
            scheduler=scheduler,
        )
        writer_obj.apply(ctx)
        mock_mount.resolve.assert_called()


class TestUsnRecordStructure:
    def test_record_size_is_multiple_of_8(self):
        """USN_RECORD_V2 must be padded to an 8-byte boundary."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = _build_usn_record(
            usn=0x1000,
            filename="test.docx",
            file_ref=0x0002_0000_0000_0001,
            parent_ref=0x0002_0000_0000_0005,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        assert len(record) % 8 == 0

    def test_record_length_field_matches_actual_size(self):
        """The RecordLength field (first DWORD) must equal len(record)."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = _build_usn_record(
            usn=0x2000,
            filename="report.pdf",
            file_ref=0x0003_0000_0000_0002,
            parent_ref=0x0003_0000_0000_0006,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        record_length = struct.unpack_from("<I", record, 0)[0]
        assert record_length == len(record)

    def test_major_version_is_2(self):
        """MajorVersion (bytes 4-5) must be 2 for V2 records."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = _build_usn_record(
            usn=0x3000,
            filename="notes.txt",
            file_ref=0x0002_ABCD_0000_0001,
            parent_ref=0x0002_ABCD_0000_0002,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        major_version = struct.unpack_from("<H", record, 4)[0]
        assert major_version == 2

    def test_minor_version_is_0(self):
        """MinorVersion (bytes 6-7) must be 0."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = _build_usn_record(
            usn=0x4000,
            filename="data.xlsx",
            file_ref=0x0002_0000_0000_0099,
            parent_ref=0x0002_0000_0000_0100,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        minor_version = struct.unpack_from("<H", record, 6)[0]
        assert minor_version == 0

    def test_filename_offset_is_60(self):
        """FileNameOffset (bytes 58-60) must always be 60 (fixed V2 header size)."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = _build_usn_record(
            usn=0x5000,
            filename="presentation.pptx",
            file_ref=0x0002_1111_0000_0001,
            parent_ref=0x0002_2222_0000_0001,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        fname_offset = struct.unpack_from("<H", record, 58)[0]
        assert fname_offset == _V2_HEADER_SIZE

    def test_filename_encoded_utf16le_in_record(self):
        """The filename bytes appear in the record starting at offset 60."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        filename = "myfile.docx"
        record = _build_usn_record(
            usn=0x6000,
            filename=filename,
            file_ref=0x0002_DEAD_0000_0001,
            parent_ref=0x0002_BEEF_0000_0001,
            timestamp=ts,
            reason=_USN_FILE_CREATE | _USN_CLOSE,
            file_attrs=0x20,
        )
        fname_bytes = filename.encode("utf-16-le")
        assert record[_V2_HEADER_SIZE:_V2_HEADER_SIZE + len(fname_bytes)] == fname_bytes

    def test_reason_field_matches(self):
        """The Reason field (bytes 40-44) must equal the value passed in."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        reason = _USN_FILE_CREATE | _USN_CLOSE
        record = _build_usn_record(
            usn=0x7000,
            filename="test.txt",
            file_ref=0x0002_0000_0000_0001,
            parent_ref=0x0002_0000_0000_0005,
            timestamp=ts,
            reason=reason,
            file_attrs=0x20,
        )
        stored_reason = struct.unpack_from("<I", record, 40)[0]
        assert stored_reason == reason


class TestHandlesMissingPathGracefully:
    def test_event_with_no_payload_path_no_error(
        self, writer, mount_manager, audit_logger, persona, identity_bundle
    ):
        """An event whose payload has no 'path' key is silently skipped."""
        event = MagicMock()
        event.event_type = "FILE_CREATE"
        event.timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)
        event.payload = {}  # no path key

        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        writer.apply(ctx)  # must not raise

    def test_event_with_none_payload_no_error(
        self, writer, mount_manager, audit_logger, persona, identity_bundle
    ):
        """An event with payload=None is silently skipped."""
        event = MagicMock()
        event.event_type = "FILE_CREATE"
        event.timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)
        event.payload = None

        scheduler = _make_mock_scheduler([event])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        writer.apply(ctx)  # must not raise

    def test_no_records_written_for_empty_event_list(
        self, writer, mount_dir, mount_manager, audit_logger, persona, identity_bundle
    ):
        """With zero events, no journal file is created."""
        scheduler = _make_mock_scheduler([])
        ctx = make_service_context(
            mount_manager, audit_logger, persona, identity_bundle, scheduler=scheduler
        )
        writer.apply(ctx)
        journal_path = mount_dir / "$Extend" / "$UsnJrnl" / "$J"
        assert not journal_path.exists()


class TestServiceName:
    def test_service_name(self, writer):
        assert writer.service_name == "UsnJournalWriter"
