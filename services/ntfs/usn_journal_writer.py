"""USN Change Journal ($UsnJrnl:$J) writer.

Appends synthetic USN_RECORD_V2 entries to the NTFS change journal stream so
that forensic tools (MFTECmd, Velociraptor) see a realistic audit trail of
file system operations.

Background
----------
Windows maintains a USN (Update Sequence Number) journal at
``\\$Extend\\$UsnJrnl:$J``.  Each record describes one file-system event
(create, write, rename, delete, close).  On NTFS volumes with no journal
activity the stream is conspicuously empty — a sandbox tell.

This service appends V2 records derived from ``EventScheduler`` events.
The ``$J`` stream is opened for append via the FUSE-mounted path; if the
path is inaccessible the service degrades gracefully with a warning.

USN_RECORD_V2 layout (60-byte fixed header + variable filename)
---------------------------------------------------------------
::

    DWORD  RecordLength          (includes filename + pad to 8-byte boundary)
    WORD   MajorVersion = 2
    WORD   MinorVersion = 0
    QWORD  FileReferenceNumber
    QWORD  ParentFileReferenceNumber
    LONGLONG Usn
    LARGE_INTEGER TimeStamp      (FILETIME)
    DWORD  Reason
    DWORD  SourceInfo = 0
    DWORD  SecurityId = 0
    DWORD  FileAttributes
    WORD   FileNameLength        (bytes, not chars)
    WORD   FileNameOffset = 60
    WCHAR  FileName[...]

Reason flags (selected)
-----------------------
- 0x00000001  USN_REASON_DATA_OVERWRITE
- 0x00000002  USN_REASON_DATA_EXTEND
- 0x00000100  USN_REASON_FILE_CREATE
- 0x00000200  USN_REASON_FILE_DELETE
- 0x00001000  USN_REASON_RENAME_NEW_NAME
- 0x00002000  USN_REASON_RENAME_OLD_NAME
- 0x80000000  USN_REASON_CLOSE
"""

from __future__ import annotations

import logging
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)

_FILETIME_EPOCH: int = 116_444_736_000_000_000

# USN reason flags
_USN_FILE_CREATE: int = 0x00000100
_USN_FILE_DELETE: int = 0x00000200
_USN_DATA_EXTEND: int = 0x00000002
_USN_DATA_OVERWRITE: int = 0x00000001
_USN_CLOSE: int = 0x80000000

# Fixed header size for V2 (before filename)
_V2_HEADER_SIZE: int = 60

# Journal stream path relative to NTFS root (FUSE layout uses $-prefixed names)
_USN_JOURNAL_REL: str = "$Extend/$UsnJrnl/$J"

# Maximum records to append (keeps the stream size realistic)
_MAX_RECORDS: int = 50_000


class UsnJournalWriterError(Exception):
    """Raised when USN journal write fails."""


def _to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ticks = int((dt - epoch).total_seconds() * 1e7)
    return ticks + _FILETIME_EPOCH


def _build_usn_record(
    usn: int,
    filename: str,
    file_ref: int,
    parent_ref: int,
    timestamp: datetime,
    reason: int,
    file_attrs: int,
) -> bytes:
    """Pack one USN_RECORD_V2 into bytes, padded to 8-byte boundary."""
    fname_bytes = filename.encode("utf-16-le")
    fname_len = len(fname_bytes)
    record_len_raw = _V2_HEADER_SIZE + fname_len
    # Pad to 8-byte boundary
    record_len = (record_len_raw + 7) & ~7

    buf = bytearray(record_len)
    struct.pack_into(
        "<IHHQQqQIIIIHH",
        buf, 0,
        record_len,         # RecordLength
        2,                  # MajorVersion
        0,                  # MinorVersion
        file_ref,           # FileReferenceNumber
        parent_ref,         # ParentFileReferenceNumber
        usn,                # Usn (LONGLONG)
        _to_filetime(timestamp),  # TimeStamp
        reason,             # Reason
        0,                  # SourceInfo
        0,                  # SecurityId
        file_attrs,         # FileAttributes
        fname_len,          # FileNameLength
        _V2_HEADER_SIZE,    # FileNameOffset
    )
    buf[_V2_HEADER_SIZE:_V2_HEADER_SIZE + fname_len] = fname_bytes
    return bytes(buf)


class UsnJournalWriter(BaseService):
    """Appends synthetic USN_RECORD_V2 entries to the NTFS change journal.

    Args:
        mount_manager: Resolves paths against the FUSE-mounted NTFS root.
        audit_logger:  Shared audit logger.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "UsnJournalWriter"

    def apply(self, ctx: "ServiceContext") -> None:
        """Write USN records for all scheduler FILE_* events.

        Opens ``$Extend/$UsnJrnl/$J`` for append via the FUSE mount. If the
        path is inaccessible (non-FUSE host filesystem, or offline vhd), the
        service logs a warning and returns without error.

        Raises:
            UsnJournalWriterError: On fatal write error after open succeeds.
        """
        if ctx.scheduler is None:
            logger.warning("UsnJournalWriter: no scheduler; skipping")
            return

        try:
            journal_path = self._mount.resolve(_USN_JOURNAL_REL)
        except Exception as exc:
            logger.warning("UsnJournalWriter: cannot resolve journal path: %s", exc)
            return

        records = self._build_records(ctx)
        if not records:
            return

        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with journal_path.open("ab") as fh:
                for record in records:
                    fh.write(record)
        except PermissionError:
            logger.warning(
                "UsnJournalWriter: no write access to %s; "
                "ensure ntfs-3g FUSE mount is active",
                journal_path,
            )
            return
        except OSError as exc:
            raise UsnJournalWriterError(
                f"Failed to write USN journal: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_usn_journal",
            "records_written": len(records),
        })
        logger.info("USN journal: wrote %d records", len(records))

    def _build_records(self, ctx: "ServiceContext") -> List[bytes]:
        """Build USN_RECORD_V2 payloads from scheduler events."""
        rng = (
            ctx.scheduler.child_rng("UsnJournal")
            if ctx.scheduler
            else __import__("random").Random(0)
        )
        records: List[bytes] = []
        usn: int = rng.randint(0x0001_0000_0000, 0x000F_0000_0000)
        usn_step: int = 64  # typical journal step size

        # Root directory reference
        root_ref: int = 0x0005_0000_0000_0005

        event_types = {
            "FILE_CREATE": (_USN_FILE_CREATE | _USN_CLOSE, 0x20),     # archive
            "FILE_MODIFY": (_USN_DATA_EXTEND | _USN_CLOSE, 0x20),
            "FILE_DELETE": (_USN_FILE_DELETE | _USN_CLOSE, 0x20),
        }

        seen_events = 0
        for event in ctx.scheduler.events_of("FILE_CREATE", "FILE_MODIFY", "FILE_DELETE"):
            if seen_events >= _MAX_RECORDS:
                break

            rel_path: Optional[str] = (event.payload or {}).get("path")
            if not rel_path:
                continue

            event_kind = event.kind
            reason, attrs = event_types.get(event_kind, (_USN_DATA_OVERWRITE | _USN_CLOSE, 0x20))

            filename = Path(rel_path).name
            file_ref = rng.randint(0x0002_0000_0000_0000, 0x0004_FFFF_FFFF_FFFF)
            parent_ref = rng.randint(0x0002_0000_0000_0000, 0x0004_FFFF_FFFF_FFFF)

            record = _build_usn_record(
                usn=usn,
                filename=filename,
                file_ref=file_ref,
                parent_ref=parent_ref,
                timestamp=event.timestamp,
                reason=reason,
                file_attrs=attrs,
            )
            records.append(record)
            usn += usn_step + rng.randint(0, 32) * 8
            seen_events += 1

        return records
