#!/usr/bin/env python3
"""Unit tests for NTFS services.

Tests for MftTimestampPatcher, UsnJournalWriter, and LogfileWriter services.
"""

import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch, call

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.ntfs.mft_timestamp_patcher import (
    MftTimestampPatcher,
    _datetime_to_filetime,
    _pack_ntfs_times,
)
from services.ntfs.usn_journal_writer import (
    UsnJournalWriter,
    USN_RECORD_V3,
    UsnReasonFlags,
    _read_usn_max_header,
    _write_usn_max_header,
)
from services.ntfs.logfile_writer import (
    LogfileWriter,
    _make_rstr_page,
    _make_rcrd_page,
    _LOG_PAGE_SIZE,
    _RSTR_SIGNATURE,
    _RCRD_SIGNATURE,
)


# ============================================================================
# MftTimestampPatcher Tests
# ============================================================================

class TestMftTimestampPatcher:
    """Tests for MftTimestampPatcher service."""
    
    def test_datetime_to_filetime(self):
        """Test datetime to Windows FILETIME conversion."""
        # Test epoch (1970-01-01 00:00:00 UTC)
        epoch = datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        filetime = _datetime_to_filetime(epoch)
        
        # Windows epoch is 1601-01-01, so 1970 is 369 years later
        # 369 years * 365.25 days * 24 hours * 3600 seconds * 10,000,000 (100ns intervals)
        expected = 116444736000000000
        assert filetime == expected
    
    def test_datetime_to_filetime_recent(self):
        """Test datetime to FILETIME for recent date."""
        # Test 2024-01-15 12:00:00 UTC
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        filetime = _datetime_to_filetime(dt)
        
        # Should be a large positive number
        assert filetime > 130000000000000000  # After 2000
        assert filetime < 140000000000000000  # Before 2100
    
    def test_pack_ntfs_times(self):
        """Test packing 4 NTFS timestamps."""
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        packed = _pack_ntfs_times(dt, dt, dt, dt)
        
        # Should be 32 bytes (4 timestamps * 8 bytes each)
        assert len(packed) == 32
        
        # Unpack and verify
        times = struct.unpack("<4Q", packed)
        assert len(times) == 4
        assert all(t > 0 for t in times)
    
    def test_service_name(self):
        """Test service name property."""
        mock_mount = Mock()
        mock_audit = Mock()
        
        patcher = MftTimestampPatcher(mock_mount, mock_audit)
        assert patcher.service_name == "MftTimestampPatcher"
    
    @patch('subprocess.run')
    def test_apply_patches_timestamps(self, mock_run):
        """Test apply() patches timestamps for file events."""
        mock_mount = Mock()
        mock_mount.resolve.return_value = Path("/mnt/arc/Users/test/file.txt")
        mock_audit = Mock()
        
        # Create mock context
        mock_ctx = Mock()
        mock_event1 = Mock()
        mock_event1.event_type = "FILE_CREATE"
        mock_event1.path = "Users/test/file.txt"
        mock_event1.timestamp = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        mock_ctx.scheduler.events = [mock_event1]
        
        patcher = MftTimestampPatcher(mock_mount, mock_audit)
        patcher.apply(mock_ctx)
        
        # Verify setfattr was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "setfattr" in call_args
        assert "system.ntfs_times" in call_args
    
    def test_apply_handles_missing_file(self):
        """Test apply() handles missing files gracefully."""
        mock_mount = Mock()
        mock_mount.resolve.side_effect = FileNotFoundError("File not found")
        mock_audit = Mock()
        
        mock_ctx = Mock()
        mock_event = Mock()
        mock_event.event_type = "FILE_CREATE"
        mock_event.path = "Users/test/missing.txt"
        mock_event.timestamp = datetime.now(timezone.utc)
        
        mock_ctx.scheduler.events = [mock_event]
        
        patcher = MftTimestampPatcher(mock_mount, mock_audit)
        
        # Should not raise exception
        patcher.apply(mock_ctx)


# ============================================================================
# UsnJournalWriter Tests
# ============================================================================

class TestUsnJournalWriter:
    """Tests for UsnJournalWriter service."""
    
    def test_usn_record_v3_structure(self):
        """Test USN_RECORD_V3 structure."""
        record = USN_RECORD_V3(
            record_length=80,
            major_version=3,
            minor_version=0,
            file_reference_number=0x1234567890ABCDEF,
            parent_file_reference_number=0xFEDCBA0987654321,
            usn=1000,
            timestamp=132000000000000000,
            reason=UsnReasonFlags.DATA_OVERWRITE | UsnReasonFlags.CLOSE,
            source_info=0,
            security_id=0,
            file_attributes=0x20,  # FILE_ATTRIBUTE_ARCHIVE
            file_name_length=16,
            file_name_offset=60,
            file_name="test.txt"
        )
        
        packed = record.to_bytes()
        
        # Verify structure
        assert len(packed) >= 60  # Minimum size
        assert packed[0:4] == struct.pack("<I", 80)  # RecordLength
        assert packed[4:6] == struct.pack("<H", 3)   # MajorVersion
    
    def test_usn_reason_flags(self):
        """Test USN reason flags."""
        # Test individual flags
        assert UsnReasonFlags.DATA_OVERWRITE == 0x00000001
        assert UsnReasonFlags.DATA_EXTEND == 0x00000002
        assert UsnReasonFlags.FILE_CREATE == 0x00000100
        assert UsnReasonFlags.FILE_DELETE == 0x00000200
        assert UsnReasonFlags.RENAME_NEW_NAME == 0x00002000
        
        # Test flag combination
        combined = UsnReasonFlags.DATA_OVERWRITE | UsnReasonFlags.CLOSE
        assert combined == 0x80000001
    
    def test_read_usn_max_header(self):
        """Test reading $UsnJrnl:$Max header."""
        # Create mock header (48 bytes)
        header_data = struct.pack(
            "<QQQQQQ",
            1000,  # MaxUsn
            2000,  # AllocationDelta
            3000,  # NextUsn
            4000,  # LowestValidUsn
            5000,  # MaximumSize
            6000   # AllocationSize
        )
        
        mock_file = mock_open(read_data=header_data)()
        
        max_usn, next_usn = _read_usn_max_header(mock_file)
        
        assert max_usn == 1000
        assert next_usn == 3000
    
    def test_write_usn_max_header(self):
        """Test writing $UsnJrnl:$Max header."""
        mock_file = MagicMock()
        
        _write_usn_max_header(mock_file, max_usn=5000, next_usn=6000)
        
        # Verify seek to beginning
        mock_file.seek.assert_called_with(0)
        
        # Verify write was called
        assert mock_file.write.called
        written_data = mock_file.write.call_args[0][0]
        assert len(written_data) == 48  # Header size
    
    def test_service_name(self):
        """Test service name property."""
        mock_mount = Mock()
        mock_audit = Mock()
        
        writer = UsnJournalWriter(mock_mount, mock_audit)
        assert writer.service_name == "UsnJournalWriter"
    
    @patch('builtins.open', new_callable=mock_open)
    def test_apply_writes_usn_records(self, mock_file):
        """Test apply() writes USN records."""
        mock_mount = Mock()
        mock_mount.resolve.return_value = Path("/mnt/arc/$Extend/$UsnJrnl:$Max")
        mock_audit = Mock()
        
        # Mock $Max header read
        header_data = struct.pack("<QQQQQQ", 1000, 2000, 3000, 4000, 5000, 6000)
        mock_file.return_value.read.return_value = header_data
        
        # Create mock context
        mock_ctx = Mock()
        mock_event = Mock()
        mock_event.event_type = "FILE_CREATE"
        mock_event.path = "Users/test/file.txt"
        mock_event.timestamp = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        mock_ctx.scheduler.events = [mock_event]
        
        writer = UsnJournalWriter(mock_mount, mock_audit)
        writer.apply(mock_ctx)
        
        # Verify files were opened
        assert mock_file.called


# ============================================================================
# LogfileWriter Tests
# ============================================================================

class TestLogfileWriter:
    """Tests for LogfileWriter service."""
    
    def test_make_rstr_page(self):
        """Test RSTR page creation."""
        page = _make_rstr_page(seq=1)
        
        # Verify size
        assert len(page) == _LOG_PAGE_SIZE
        
        # Verify signature
        assert page[0:4] == _RSTR_SIGNATURE
        
        # Verify sequence number
        seq = struct.unpack("<Q", page[8:16])[0]
        assert seq == 1
    
    def test_make_rcrd_page(self):
        """Test RCRD page creation."""
        from random import Random
        rng = Random(42)
        
        page = _make_rcrd_page(lsn=1000, rng=rng)
        
        # Verify size
        assert len(page) == _LOG_PAGE_SIZE
        
        # Verify signature
        assert page[0:4] == _RCRD_SIGNATURE
        
        # Verify LSN
        lsn = struct.unpack("<Q", page[8:16])[0]
        assert lsn == 1000
        
        # Verify entropy (should not be all zeros)
        assert page[16:] != b'\x00' * (_LOG_PAGE_SIZE - 16)
    
    def test_service_name(self):
        """Test service name property."""
        mock_mount = Mock()
        mock_audit = Mock()
        
        writer = LogfileWriter(mock_mount, mock_audit)
        assert writer.service_name == "LogfileWriter"
    
    @patch('builtins.open', new_callable=mock_open)
    def test_apply_writes_logfile(self, mock_file):
        """Test apply() writes $LogFile stub."""
        mock_mount = Mock()
        mock_mount.resolve.return_value = Path("/mnt/arc/$LogFile")
        mock_audit = Mock()
        
        # Create mock context
        mock_ctx = Mock()
        mock_ctx.identity_bundle.user.username = "testuser"
        mock_ctx.scheduler = None  # No scheduler
        
        writer = LogfileWriter(mock_mount, mock_audit)
        writer.apply(mock_ctx)
        
        # Verify file was opened for writing
        mock_file.assert_called_with(Path("/mnt/arc/$LogFile"), "wb")
        
        # Verify write was called multiple times (RSTR + RCRD pages)
        assert mock_file.return_value.__enter__().write.call_count >= 3
        
        # Verify audit log
        assert mock_audit.log.called
        log_data = mock_audit.log.call_args[0][0]
        assert log_data["service"] == "LogfileWriter"
        assert log_data["operation"] == "write_logfile_stub"
    
    def test_apply_handles_permission_error(self):
        """Test apply() handles permission errors gracefully."""
        mock_mount = Mock()
        mock_mount.resolve.return_value = Path("/mnt/arc/$LogFile")
        mock_audit = Mock()
        
        mock_ctx = Mock()
        mock_ctx.identity_bundle.user.username = "testuser"
        mock_ctx.scheduler = None
        
        writer = LogfileWriter(mock_mount, mock_audit)
        
        # Mock open to raise PermissionError
        with patch('builtins.open', side_effect=PermissionError("Access denied")):
            # Should not raise exception
            writer.apply(mock_ctx)
    
    def test_apply_handles_resolve_error(self):
        """Test apply() handles resolve errors gracefully."""
        mock_mount = Mock()
        mock_mount.resolve.side_effect = Exception("Cannot resolve path")
        mock_audit = Mock()
        
        mock_ctx = Mock()
        mock_ctx.identity_bundle.user.username = "testuser"
        mock_ctx.scheduler = None
        
        writer = LogfileWriter(mock_mount, mock_audit)
        
        # Should not raise exception
        writer.apply(mock_ctx)


# ============================================================================
# Integration Tests
# ============================================================================

class TestNTFSServicesIntegration:
    """Integration tests for NTFS services."""
    
    def test_all_services_instantiate(self):
        """Test that all NTFS services can be instantiated."""
        mock_mount = Mock()
        mock_audit = Mock()
        
        patcher = MftTimestampPatcher(mock_mount, mock_audit)
        usn_writer = UsnJournalWriter(mock_mount, mock_audit)
        log_writer = LogfileWriter(mock_mount, mock_audit)
        
        assert patcher.service_name == "MftTimestampPatcher"
        assert usn_writer.service_name == "UsnJournalWriter"
        assert log_writer.service_name == "LogfileWriter"
    
    def test_services_have_apply_method(self):
        """Test that all services have apply() method."""
        mock_mount = Mock()
        mock_audit = Mock()
        
        services = [
            MftTimestampPatcher(mock_mount, mock_audit),
            UsnJournalWriter(mock_mount, mock_audit),
            LogfileWriter(mock_mount, mock_audit),
        ]
        
        for service in services:
            assert hasattr(service, 'apply')
            assert callable(service.apply)


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
