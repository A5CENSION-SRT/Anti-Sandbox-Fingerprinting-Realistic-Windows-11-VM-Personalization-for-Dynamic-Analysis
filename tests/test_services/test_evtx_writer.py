"""Tests for the EvtxWriter service."""

import struct
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from services.eventlog.evtx_writer import (
    EvtxRecord,
    EvtxWriter,
    EvtxWriterError,
    _FILE_MAGIC,
    _CHUNK_MAGIC,
    _CHUNK_SIZE,
    _FILE_HEADER_SIZE,
)


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
def evtx_writer(mount_manager, audit_logger):
    return EvtxWriter(mount_manager, audit_logger)


@pytest.fixture
def sample_timestamp():
    return datetime(2024, 3, 15, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_record(sample_timestamp):
    return EvtxRecord(
        channel="System",
        event_id=6005,
        level=4,
        provider="Microsoft-Windows-EventLog",
        computer="DESKTOP-TEST01",
        timestamp=sample_timestamp,
        event_data={"Message": "The Event log service was started."},
    )


# ---------------------------------------------------------------------------
# EvtxRecord model validation
# ---------------------------------------------------------------------------

class TestEvtxRecord:
    def test_valid_record_construction(self, sample_record):
        assert sample_record.event_id == 6005
        assert sample_record.level == 4
        assert sample_record.channel == "System"

    def test_default_keywords(self, sample_record):
        assert sample_record.keywords == "0x8000000000000000"

    def test_default_task_opcode(self, sample_record):
        assert sample_record.task == 0
        assert sample_record.opcode == 0

    def test_event_id_zero_valid(self, sample_timestamp):
        r = EvtxRecord(
            channel="Application",
            event_id=0,
            provider="TestProvider",
            computer="PC",
            timestamp=sample_timestamp,
        )
        assert r.event_id == 0

    def test_event_id_max_valid(self, sample_timestamp):
        r = EvtxRecord(
            channel="System",
            event_id=65535,
            provider="P",
            computer="C",
            timestamp=sample_timestamp,
        )
        assert r.event_id == 65535

    def test_event_id_out_of_range(self, sample_timestamp):
        with pytest.raises(Exception):
            EvtxRecord(
                channel="System",
                event_id=65536,
                provider="P",
                computer="C",
                timestamp=sample_timestamp,
            )

    def test_event_id_negative(self, sample_timestamp):
        with pytest.raises(Exception):
            EvtxRecord(
                channel="System",
                event_id=-1,
                provider="P",
                computer="C",
                timestamp=sample_timestamp,
            )

    def test_level_invalid(self, sample_timestamp):
        with pytest.raises(Exception):
            EvtxRecord(
                channel="System",
                event_id=1,
                level=99,
                provider="P",
                computer="C",
                timestamp=sample_timestamp,
            )

    def test_frozen_model(self, sample_record):
        with pytest.raises(Exception):
            sample_record.event_id = 999

    def test_extra_fields_forbidden(self, sample_timestamp):
        with pytest.raises(Exception):
            EvtxRecord(
                channel="System",
                event_id=1,
                provider="P",
                computer="C",
                timestamp=sample_timestamp,
                unknown_field="x",
            )

    def test_event_data_dict(self, sample_timestamp):
        r = EvtxRecord(
            channel="System",
            event_id=7036,
            provider="SCM",
            computer="PC",
            timestamp=sample_timestamp,
            event_data={"param1": "Spooler", "param2": "running"},
        )
        assert r.event_data["param1"] == "Spooler"


# ---------------------------------------------------------------------------
# EvtxWriter: write_records → produces valid EVTX file
# ---------------------------------------------------------------------------

class TestEvtxWriter:
    @pytest.fixture(autouse=True)
    def _inject_service_ctx(self, service_ctx):
        self.service_ctx = service_ctx

    def test_write_creates_file(self, evtx_writer, mount_dir, sample_record):
        evtx_path = "Windows/System32/winevt/Logs/System.evtx"
        evtx_writer.write_records([sample_record], evtx_path)
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        assert dest.exists()

    def test_file_magic(self, evtx_writer, mount_dir, sample_record):
        evtx_path = "Windows/System32/winevt/Logs/System.evtx"
        evtx_writer.write_records([sample_record], evtx_path)
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        data = dest.read_bytes()
        assert data[:8] == _FILE_MAGIC

    def test_file_header_size(self, evtx_writer, mount_dir, sample_record):
        evtx_writer.write_records(
            [sample_record], "Windows/System32/winevt/Logs/System.evtx"
        )
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        data = dest.read_bytes()
        assert len(data) >= _FILE_HEADER_SIZE

    def test_chunk_magic(self, evtx_writer, mount_dir, sample_record):
        evtx_writer.write_records(
            [sample_record], "Windows/System32/winevt/Logs/System.evtx"
        )
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        data = dest.read_bytes()
        assert data[_FILE_HEADER_SIZE:_FILE_HEADER_SIZE + 8] == _CHUNK_MAGIC

    def test_empty_records_creates_empty_log(self, evtx_writer, mount_dir):
        evtx_writer.write_records([], "Windows/System32/winevt/Logs/System.evtx")
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        assert dest.exists()
        assert len(dest.read_bytes()) >= _FILE_HEADER_SIZE

    def test_multiple_records(self, evtx_writer, mount_dir, sample_timestamp):
        records = [
            EvtxRecord(
                channel="System",
                event_id=6005 + i,
                provider="TestProvider",
                computer="PC",
                timestamp=sample_timestamp,
            )
            for i in range(5)
        ]
        evtx_writer.write_records(records, "Windows/System32/winevt/Logs/System.evtx")
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        assert dest.exists()

    def test_backup_created_on_overwrite(self, evtx_writer, mount_dir, sample_record):
        path = "Windows/System32/winevt/Logs/System.evtx"
        evtx_writer.write_records([sample_record], path)
        evtx_writer.write_records([sample_record], path)
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        backup = dest.with_suffix(".evtx.bak")
        assert backup.exists()

    def test_audit_logged(self, evtx_writer, audit_logger, sample_record):
        audit_logger.clear()
        evtx_writer.write_records(
            [sample_record], "Windows/System32/winevt/Logs/System.evtx"
        )
        entries = audit_logger.entries
        assert len(entries) == 1
        assert entries[0]["service"] == "EvtxWriter"

    def test_missing_evtx_path_is_noop(self, evtx_writer):
        # When evtx_path is absent, apply() is a no-op because
        # individual log services call write_records() directly.
        evtx_writer.apply(self.service_ctx)  # should not raise

    def test_path_escape_raises(self, evtx_writer, sample_record):
        with pytest.raises(EvtxWriterError):
            evtx_writer.write_records([sample_record], "../../../etc/passwd.evtx")

    def test_service_name(self, evtx_writer):
        assert evtx_writer.service_name == "EvtxWriter"

    def test_many_records_multi_chunk(self, evtx_writer, mount_dir, sample_timestamp):
        """Writing 250 records should produce a multi-chunk EVTX file."""
        records = [
            EvtxRecord(
                channel="System",
                event_id=7036,
                provider="SCM",
                computer="BULK-PC",
                timestamp=sample_timestamp,
                event_data={"param1": f"Service{i}", "param2": "running"},
            )
            for i in range(250)
        ]
        evtx_writer.write_records(records, "Windows/System32/winevt/Logs/System.evtx")
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "System.evtx"
        data = dest.read_bytes()
        # At least 2 chunks means file is > FILE_HEADER_SIZE + CHUNK_SIZE
        assert len(data) > _FILE_HEADER_SIZE + _CHUNK_SIZE


class TestEvtxWriterMissingPath:
    @pytest.fixture(autouse=True)
    def _inject_service_ctx(self, service_ctx):
        self.service_ctx = service_ctx

    def test_apply_no_evtx_path_is_noop(self, evtx_writer, audit_logger):
        """When evtx_path is missing, apply() should be a no-op (not raise)."""
        # This is intentional: individual log services call write_records() directly
        evtx_writer.apply(self.service_ctx)
        # No audit entries should be created for no-op
        assert len(audit_logger.entries) == 0


# ---------------------------------------------------------------------------
# A12 density gate — >=10 MB per log, >=5000 records
# ---------------------------------------------------------------------------

class TestA12Density:
    """Gate A12: EVTX per log >=10 MB and >=5000 records."""

    @pytest.fixture(autouse=True)
    def _inject_service_ctx(self, service_ctx):
        self.service_ctx = service_ctx

    def _make_records(self, count: int, ts: datetime) -> list:
        return [
            EvtxRecord(
                channel="Security",
                event_id=4688,
                provider="Microsoft-Windows-Security-Auditing",
                computer="CORP-WS01",
                timestamp=ts,
                event_data={
                    "SubjectUserName": f"user{i % 50}",
                    "NewProcessName": rf"C:\Windows\System32\cmd{i}.exe",
                    "CommandLine": f"cmd /c echo {i}",
                },
            )
            for i in range(count)
        ]

    def test_file_size_exceeds_10mb(self, evtx_writer, mount_dir):
        # 10 MB requires ~14 000 records at ~770 bytes/record
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        records = self._make_records(14_000, ts)
        evtx_path = "Windows/System32/winevt/Logs/Security.evtx"
        evtx_writer.write_records(records, evtx_path)
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "Security.evtx"
        size_mb = dest.stat().st_size / (1024 * 1024)
        assert size_mb >= 10.0, f"A12 fail: Security.evtx is {size_mb:.1f} MB (need >=10)"

    def test_record_count_exceeds_5000(self, evtx_writer, mount_dir):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        records = self._make_records(5_500, ts)
        evtx_path = "Windows/System32/winevt/Logs/Security.evtx"
        evtx_writer.write_records(records, evtx_path)
        dest = mount_dir / "Windows" / "System32" / "winevt" / "Logs" / "Security.evtx"
        data = dest.read_bytes()
        # File header has last_record_num at offset 24 (little-endian uint64)
        last_record = struct.unpack_from("<Q", data, 24)[0]
        assert last_record >= 5_000, f"A12 fail: only {last_record} records"
