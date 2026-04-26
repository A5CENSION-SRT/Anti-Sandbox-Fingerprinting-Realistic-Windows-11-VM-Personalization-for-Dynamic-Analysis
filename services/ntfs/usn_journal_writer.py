"""NTFS Update Sequence Number (USN) Journal writer.

This service appends USN_RECORD_V3 structures to the NTFS $UsnJrnl:$J stream
based on file operation events from the scheduler. The USN Journal provides
a persistent log of all changes to files and directories on an NTFS volume.

The journal consists of two streams:
- $UsnJrnl:$Max - Header with metadata (NextUsn, LowestValidUsn, etc.)
- $UsnJrnl:$J - Sparse stream containing USN_RECORD_V3 structures

This service requires a FUSE-mounted NTFS filesystem to access the alternate
data streams via colon-path syntax ($UsnJrnl:$J).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from core.service_context import ServiceContext

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# Windows FILETIME epoch
_FILETIME_EPOCH_DELTA = 11644473600


# USN Reason Flags (from winioctl.h)
USN_REASON_DATA_OVERWRITE = 0x00000001
USN_REASON_DATA_EXTEND = 0x00000002
USN_REASON_DATA_TRUNCATION = 0x00000004
USN_REASON_NAMED_DATA_OVERWRITE = 0x00000010
USN_REASON_NAMED_DATA_EXTEND = 0x00000020
USN_REASON_NAMED_DATA_TRUNCATION = 0x00000040
USN_REASON_FILE_CREATE = 0x00000100
USN_REASON_FILE_DELETE = 0x00000200
USN_REASON_EA_CHANGE = 0x00000400
USN_REASON_SECURITY_CHANGE = 0x00000800
USN_REASON_RENAME_OLD_NAME = 0x00001000
USN_REASON_RENAME_NEW_NAME = 0x00002000
USN_REASON_INDEXABLE_CHANGE = 0x00004000
USN_REASON_BASIC_INFO_CHANGE = 0x00008000
USN_REASON_HARD_LINK_CHANGE = 0x00010000
USN_REASON_COMPRESSION_CHANGE = 0x00020000
USN_REASON_ENCRYPTION_CHANGE = 0x00040000
USN_REASON_OBJECT_ID_CHANGE = 0x00080000
USN_REASON_REPARSE_POINT_CHANGE = 0x00100000
USN_REASON_STREAM_CHANGE = 0x00200000
USN_REASON_TRANSACTED_CHANGE = 0x00400000
USN_REASON_CLOSE = 0x80000000


# File Attributes (from winnt.h)
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_TEMPORARY = 0x00000100
FILE_ATTRIBUTE_SPARSE_FILE = 0x00000200
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_COMPRESSED = 0x00000800
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x00002000
FILE_ATTRIBUTE_ENCRYPTED = 0x00004000


def _datetime_to_filetime(dt: datetime) -> int:
    """Convert Python datetime to Windows FILETIME."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    unix_timestamp = dt.timestamp()
    return int((unix_timestamp + _FILETIME_EPOCH_DELTA) * 10_000_000)


@dataclass
class UsnRecordV3:
    """NTFS Update Sequence Number record (version 3).
    
    This structure is appended to $UsnJrnl:$J for each file operation.
    """
    
    record_length: int
    major_version: int = 3
    minor_version: int = 0
    file_reference_number: int = 0  # MFT entry number (48-bit) + sequence (16-bit)
    parent_file_reference_number: int = 0
    usn: int = 0  # Update Sequence Number
    timestamp: int = 0  # Windows FILETIME
    reason: int = 0  # USN_REASON_* flags
    source_info: int = 0
    security_id: int = 0
    file_attributes: int = FILE_ATTRIBUTE_ARCHIVE
    file_name_length: int = 0  # Length in bytes (UTF-16LE)
    file_name_offset: int = 60  # Offset to file name within record
    file_name: str = ""
    
    def to_bytes(self) -> bytes:
        """Pack USN_RECORD_V3 to binary format for $UsnJrnl:$J.
        
        Returns:
            Binary representation of the USN record
        """
        # Encode filename as UTF-16LE
        file_name_bytes = self.file_name.encode("utf-16-le")
        file_name_length = len(file_name_bytes)
        
        # Calculate total record length (must be 8-byte aligned)
        base_length = 60 + file_name_length
        record_length = ((base_length + 7) // 8) * 8  # Round up to 8-byte boundary
        
        # Pack the fixed-size header (60 bytes)
        header = struct.pack(
            "<I"      # RecordLength (DWORD)
            "HH"      # MajorVersion, MinorVersion (WORD, WORD)
            "Q"       # FileReferenceNumber (DWORDLONG)
            "Q"       # ParentFileReferenceNumber (DWORDLONG)
            "Q"       # Usn (USN / LONGLONG)
            "Q"       # TimeStamp (LARGE_INTEGER)
            "I"       # Reason (DWORD)
            "I"       # SourceInfo (DWORD)
            "I"       # SecurityId (DWORD)
            "I"       # FileAttributes (DWORD)
            "H"       # FileNameLength (WORD)
            "H",      # FileNameOffset (WORD)
            record_length,
            self.major_version,
            self.minor_version,
            self.file_reference_number,
            self.parent_file_reference_number,
            self.usn,
            self.timestamp,
            self.reason,
            self.source_info,
            self.security_id,
            self.file_attributes,
            file_name_length,
            self.file_name_offset
        )
        
        # Append filename and padding
        padding_length = record_length - base_length
        padding = b'\x00' * padding_length
        
        return header + file_name_bytes + padding


class UsnJournalWriter(BaseService):
    """Appends USN records to NTFS $UsnJrnl:$J stream.
    
    This service processes file operation events from the scheduler and
    appends corresponding USN_RECORD_V3 structures to the USN Journal.
    
    The USN Journal must be initialized by Windows before this service can
    write to it (baseline VHDX must have completed OOBE).
    """
    
    service_name = "UsnJournalWriter"
    
    def __init__(self) -> None:
        """Initialize the USN Journal writer service."""
        super().__init__()
        self._next_usn = 0
        self._next_file_ref = 0x1000  # Start at a reasonable MFT entry number
    
    def apply(self, ctx: "ServiceContext") -> None:
        """Append USN records based on scheduler events.
        
        Args:
            ctx: Service execution context
        """
        logger.info("Starting NTFS $UsnJrnl:$J record appending")
        
        # Verify FUSE mount is available
        if ctx.mount.backend is None:
            logger.warning(
                "No backend available - skipping USN journal writes. "
                "This service requires FUSE mount (Phase B)."
            )
            return
        
        fuse_root = ctx.mount.root
        if not fuse_root.exists():
            logger.error("FUSE mount point does not exist: %s", fuse_root)
            return
        
        # Locate $UsnJrnl streams
        usnjrnl_max_path = fuse_root / "$Extend" / "$UsnJrnl:$Max"
        usnjrnl_j_path = fuse_root / "$Extend" / "$UsnJrnl:$J"
        
        # Validate journal exists
        if not usnjrnl_max_path.exists():
            logger.error(
                "$UsnJrnl:$Max not found. Baseline VHDX must complete OOBE first. "
                "Path: %s",
                usnjrnl_max_path
            )
            return
        
        if not usnjrnl_j_path.exists():
            logger.error(
                "$UsnJrnl:$J not found. Baseline VHDX must complete OOBE first. "
                "Path: %s",
                usnjrnl_j_path
            )
            return
        
        # Read current NextUsn from $Max header
        try:
            self._next_usn = self._read_next_usn(usnjrnl_max_path)
            logger.info("Current NextUsn: 0x%X", self._next_usn)
        except Exception as exc:
            logger.error("Failed to read $UsnJrnl:$Max header: %s", exc)
            return
        
        # Collect file operation events
        file_events = []
        for event in ctx.scheduler.emit():
            if event.kind in ("FILE_CREATE", "FILE_MODIFY", "FILE_DELETE", "FILE_RENAME"):
                file_events.append(event)
        
        logger.info("Processing %d file operation events", len(file_events))
        
        # Generate USN records
        usn_records = []
        for event in file_events:
            try:
                record = self._create_usn_record(event)
                usn_records.append(record)
            except Exception as exc:
                logger.warning(
                    "Failed to create USN record for %s: %s",
                    event.payload.get("path", "unknown"),
                    exc
                )
        
        # Append records to $UsnJrnl:$J
        try:
            self._append_usn_records(usnjrnl_j_path, usn_records)
            logger.info("Appended %d USN records to $UsnJrnl:$J", len(usn_records))
        except Exception as exc:
            logger.error("Failed to append USN records: %s", exc)
            return
        
        # Update $Max header with new NextUsn
        try:
            self._update_next_usn(usnjrnl_max_path, self._next_usn)
            logger.info("Updated NextUsn to 0x%X", self._next_usn)
        except Exception as exc:
            logger.error("Failed to update $UsnJrnl:$Max header: %s", exc)
            return
        
        ctx.audit.log({
            "operation": "usn_journal_append",
            "records_written": len(usn_records),
            "next_usn": self._next_usn,
        })
    
    def _read_next_usn(self, max_path: Path) -> int:
        """Read NextUsn from $UsnJrnl:$Max header.
        
        Args:
            max_path: Path to $UsnJrnl:$Max stream
        
        Returns:
            Current NextUsn value
        """
        with open(max_path, "rb") as f:
            data = f.read(64)  # $Max header is 64 bytes
        
        if len(data) < 64:
            raise ValueError(f"$Max header too small: {len(data)} bytes")
        
        # $Max structure:
        # +0x00: MaximumSize (DWORDLONG)
        # +0x08: AllocationDelta (DWORDLONG)
        # +0x10: UsnId (DWORDLONG)
        # +0x18: LowestValidUsn (USN)
        # +0x20: NextUsn (USN)
        
        next_usn = struct.unpack_from("<Q", data, 0x20)[0]
        return next_usn
    
    def _update_next_usn(self, max_path: Path, next_usn: int) -> None:
        """Update NextUsn in $UsnJrnl:$Max header.
        
        Args:
            max_path: Path to $UsnJrnl:$Max stream
            next_usn: New NextUsn value
        """
        with open(max_path, "r+b") as f:
            # Seek to NextUsn field (offset 0x20)
            f.seek(0x20)
            f.write(struct.pack("<Q", next_usn))
    
    def _create_usn_record(self, event) -> UsnRecordV3:
        """Create a USN_RECORD_V3 from a scheduler event.
        
        Args:
            event: Scheduler event (FILE_CREATE, FILE_MODIFY, etc.)
        
        Returns:
            USN_RECORD_V3 structure
        """
        # Extract file path and name
        path = event.payload.get("path", "")
        file_name = Path(path).name if path else "unknown"
        
        # Determine reason flags based on event kind
        reason = 0
        if event.kind == "FILE_CREATE":
            reason = USN_REASON_FILE_CREATE | USN_REASON_DATA_EXTEND | USN_REASON_CLOSE
        elif event.kind == "FILE_MODIFY":
            reason = USN_REASON_DATA_OVERWRITE | USN_REASON_CLOSE
        elif event.kind == "FILE_DELETE":
            reason = USN_REASON_FILE_DELETE | USN_REASON_CLOSE
        elif event.kind == "FILE_RENAME":
            reason = USN_REASON_RENAME_NEW_NAME | USN_REASON_CLOSE
        else:
            reason = USN_REASON_CLOSE
        
        # Create record
        record = UsnRecordV3(
            record_length=0,  # Will be calculated in to_bytes()
            file_reference_number=self._next_file_ref,
            parent_file_reference_number=self._next_file_ref - 1,  # Simplified
            usn=self._next_usn,
            timestamp=_datetime_to_filetime(event.timestamp),
            reason=reason,
            source_info=0,
            security_id=0,
            file_attributes=FILE_ATTRIBUTE_ARCHIVE,
            file_name=file_name
        )
        
        # Increment counters
        record_bytes = record.to_bytes()
        self._next_usn += len(record_bytes)
        self._next_file_ref += 1
        
        return record
    
    def _append_usn_records(self, j_path: Path, records: List[UsnRecordV3]) -> None:
        """Append USN records to $UsnJrnl:$J stream.
        
        Args:
            j_path: Path to $UsnJrnl:$J stream
            records: List of USN_RECORD_V3 structures to append
        """
        with open(j_path, "ab") as f:
            for record in records:
                record_bytes = record.to_bytes()
                f.write(record_bytes)
