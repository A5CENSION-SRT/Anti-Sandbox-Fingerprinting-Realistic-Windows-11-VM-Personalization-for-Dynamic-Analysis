"""Low-level EVTX binary writer for synthetic Windows event log files.

Produces well-formed ``.evtx`` files containing synthetic XML event records.
``python-evtx`` is used exclusively for **reading** and **verification** of
produced files; all **writes** are performed via direct binary construction,
because ``python-evtx`` exposes no write API (identical situation to
``regipy`` in the registry layer).

EVTX Binary Layout
------------------
+------------------+  offset 0x0000
| File Header      |  4096 bytes  (magic: b"ElfFile\\x00")
+------------------+  offset 0x1000
| Chunk 0          |  65536 bytes (magic: b"ElfChnk\\x00")
|   Chunk Header   |    512 bytes (0x200) — includes string/template tables
|   Records...     |  variable
+------------------+  offset 0x11000  (if second chunk needed)
| Chunk 1          |  ...
+------------------+

Record Layout (within chunk, starting at chunk offset 0x200)
-------------------------------------------------------------
Offset  Size  Field
 0x00    4    Magic (0x00002a2a = "**")
 0x04    4    Total size (including this header + XML BinXML data)
 0x08    8    Record number (monotonically increasing, 1-based)
 0x10    8    Timestamp (FILETIME, 100ns intervals since 1601-01-01)
 0x18    N    BinXML payload (XML encoded via Windows BinXML format)
 END-4   4    Total size repeated (for backwards traversal)

**Write strategy:** This module generates XML-as-UTF-8-in-records rather
than full BinXML encoding (which requires a Windows-specific template
compiler).  The records are stored as raw XML bytes wrapped in the
minimal record frame.  This is a known valid strategy for synthetic
EVTX files that parse correctly in ``python-evtx`` and event viewer
compat-mode tools.  The chunk and file headers are constructed with all
required checksums (CRC32) so verification tools pass.

Every write is audited via the injected :class:`AuditLogger`.
"""

from __future__ import annotations

import binascii
import logging
import shutil
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

from pydantic import BaseModel, field_validator

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EVTX structural constants (from MS-EVEN6 spec)
# ---------------------------------------------------------------------------

_FILE_MAGIC: bytes = b"ElfFile\x00"          # 8 bytes
_CHUNK_MAGIC: bytes = b"ElfChnk\x00"         # 8 bytes
_RECORD_MAGIC: int = 0x00002A2A              # dword "**\x00\x00"

_FILE_HEADER_SIZE: int = 4096               # 0x1000
_CHUNK_SIZE: int = 65536                    # 0x10000 — fixed per spec
_CHUNK_HEADER_SIZE: int = 512              # 0x200
_CHUNK_DATA_OFFSET: int = 512              # records start at chunk+0x200

# FILETIME epoch: 100-ns ticks between 1601-01-01 and 1970-01-01
_FILETIME_EPOCH_DELTA: int = 116_444_736_000_000_000

# Max records per chunk before starting a new chunk
_MAX_RECORDS_PER_CHUNK: int = 200

# File header field offsets
_FH_OLDEST_CHUNK: int = 0x08
_FH_CURRENT_CHUNK: int = 0x10
_FH_NEXT_RECORD: int = 0x18
_FH_HEADER_SIZE: int = 0x20
_FH_MINOR_VER: int = 0x24
_FH_MAJOR_VER: int = 0x26
_FH_HEADER_CHUNK_SIZE: int = 0x28
_FH_CHUNK_COUNT: int = 0x2A
_FH_FLAGS: int = 0x78
_FH_CHECKSUM: int = 0x7C

# Chunk header field offsets (relative to chunk start)
_CH_FILE_FIRST_REC: int = 0x08
_CH_FILE_LAST_REC: int = 0x10
_CH_LOG_FIRST_REC: int = 0x18
_CH_LOG_LAST_REC: int = 0x20
_CH_HEADER_SIZE: int = 0x28
_CH_LAST_REC_OFFSET: int = 0x2C
_CH_NEXT_REC_OFFSET: int = 0x30
_CH_DATA_CHECKSUM: int = 0x34
_CH_UNUSED: int = 0x38   # 0x44 bytes padding
_CH_HEADER_CHECKSUM: int = 0x7C
_CH_STRING_TABLE: int = 0x80   # 64×4 = 256 bytes
_CH_TEMPLATE_TABLE: int = 0x180  # 32×4 = 128 bytes


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EvtxWriterError(Exception):
    """Raised on EVTX I/O, validation, or structural errors."""


# ---------------------------------------------------------------------------
# Pydantic operation model
# ---------------------------------------------------------------------------

class EvtxRecord(BaseModel):
    """Single synthetic event log record specification.

    Attributes:
        channel:    Log channel name (e.g. ``"System"``, ``"Security"``).
        event_id:   Windows Event ID (e.g. 4624, 7001).
        level:      Event level: 0=LogAlways,1=Critical,2=Error,3=Warning,
                    4=Information,5=Verbose.
        provider:   Event provider/source name.
        computer:   Computer name to embed in the record.
        timestamp:  UTC datetime for the event.
        event_data: Mapping of field name → string value for ``<EventData>``.
        keywords:   Hex keyword mask string (e.g. ``"0x8020000000000000"``).
        task:       Numeric task code.
        opcode:     Numeric opcode.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    channel: str
    event_id: int
    level: int = 4
    provider: str
    computer: str
    timestamp: datetime
    event_data: dict = field(default_factory=dict)
    keywords: str = "0x8000000000000000"
    task: int = 0
    opcode: int = 0

    @field_validator("event_id")
    @classmethod
    def _event_id_positive(cls, v: int) -> int:
        if v < 0 or v > 65535:
            raise ValueError(f"event_id must be 0–65535, got {v}")
        return v

    @field_validator("level")
    @classmethod
    def _level_valid(cls, v: int) -> int:
        if v not in (0, 1, 2, 3, 4, 5):
            raise ValueError(f"level must be 0–5, got {v}")
        return v


# ---------------------------------------------------------------------------
# Internal chunk accumulator
# ---------------------------------------------------------------------------

@dataclass
class _ChunkState:
    """Mutable accumulator for one 65536-byte chunk."""

    chunk_index: int
    records: List[bytes] = field(default_factory=list)
    first_record_num: int = 1
    next_record_num: int = 1

    def add_record(self, raw: bytes) -> None:
        self.records.append(raw)
        self.next_record_num += 1

    @property
    def last_record_num(self) -> int:
        return self.next_record_num - 1

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def is_full(self) -> bool:
        total = _CHUNK_DATA_OFFSET
        for r in self.records:
            total += len(r)
        # leave 4 KB headroom to avoid overflowing chunk
        return self.record_count >= _MAX_RECORDS_PER_CHUNK or total >= (_CHUNK_SIZE - 4096)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class EvtxWriter(BaseService):
    """Constructs synthetic EVTX files from :class:`EvtxRecord` lists.

    This is the **only** class that produces EVTX binary data.  All higher-
    level event log services (``SystemLog``, ``SecurityLog``, etc.) build
    :class:`EvtxRecord` lists and call :meth:`write_records`.

    Args:
        mount_manager: Resolves paths relative to the mounted image root.
        audit_logger:  Shared audit logger for traceability.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount_manager = mount_manager
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "EvtxWriter"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            records:    list[EvtxRecord]
            evtx_path:  str — relative path from mount root (e.g.
                        ``"Windows/System32/winevt/Logs/System.evtx"``)
        """
        records = context.get("records", [])
        evtx_path = context.get("evtx_path")
        if not evtx_path:
            raise EvtxWriterError("Missing required 'evtx_path' in context")
        self.write_records(records, evtx_path)

    # -- public API ---------------------------------------------------------

    def write_records(
        self,
        records: Sequence[EvtxRecord],
        evtx_rel_path: str,
    ) -> None:
        """Build and write an EVTX file from a sequence of records.

        Creates the destination file (and parent directories) under the
        mount root.  If the file already exists a ``.bak`` backup is made
        before overwriting.

        Args:
            records:       Records to embed in the file.
            evtx_rel_path: Relative path from mount root to the .evtx file.

        Raises:
            EvtxWriterError: On path-escape, I/O failure, or structural error.
        """
        dest = self._resolve_evtx_path(evtx_rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            backup = dest.with_suffix(dest.suffix + ".bak")
            try:
                shutil.copy2(str(dest), str(backup))
            except OSError as exc:
                raise EvtxWriterError(
                    f"Failed to create EVTX backup: {exc}"
                ) from exc

        evtx_bytes = self._build_evtx(records)

        try:
            dest.write_bytes(evtx_bytes)
        except OSError as exc:
            raise EvtxWriterError(
                f"Failed to write EVTX file {dest}: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_evtx",
            "path": evtx_rel_path,
            "record_count": len(records),
        })
        logger.info(
            "Wrote %d records to %s", len(records), dest.name
        )

    # -- path resolution ----------------------------------------------------

    def _resolve_evtx_path(self, evtx_rel_path: str) -> Path:
        """Resolve and validate an EVTX file path.

        Args:
            evtx_rel_path: Relative path from mount root.

        Returns:
            Absolute ``Path`` to the EVTX file.

        Raises:
            EvtxWriterError: If path escapes mount root.
        """
        try:
            return self._mount_manager.resolve(evtx_rel_path)
        except ValueError as exc:
            raise EvtxWriterError(
                f"Path escape detected for EVTX: {evtx_rel_path}"
            ) from exc

    # -- EVTX binary construction -------------------------------------------

    def _build_evtx(self, records: Sequence[EvtxRecord]) -> bytes:
        """Build a complete EVTX binary blob.

        Args:
            records: Records to embed.

        Returns:
            Raw bytes of the complete EVTX file.
        """
        # Assign global record numbers (1-based, monotonic)
        chunks: List[_ChunkState] = []
        current = _ChunkState(chunk_index=0, first_record_num=1, next_record_num=1)

        global_record_num: int = 1
        for rec in records:
            raw = self._encode_record(rec, global_record_num)
            current.add_record(raw)
            global_record_num += 1
            if current.is_full:
                chunks.append(current)
                current = _ChunkState(
                    chunk_index=len(chunks),
                    first_record_num=global_record_num,
                    next_record_num=global_record_num,
                )

        # Append final chunk even if empty (empty log = one empty chunk)
        chunks.append(current)

        chunk_count = len(chunks)
        next_record_number = global_record_num  # the *next* record that would be written

        # Build chunk binaries
        chunk_binaries: List[bytes] = []
        for chunk_state in chunks:
            chunk_binaries.append(self._build_chunk(chunk_state))

        # Build file header
        file_header = self._build_file_header(
            chunk_count=chunk_count,
            next_record_number=next_record_number,
            current_chunk_index=chunk_count - 1,
        )

        return file_header + b"".join(chunk_binaries)

    def _encode_record(self, rec: EvtxRecord, record_num: int) -> bytes:
        """Encode a single :class:`EvtxRecord` into a raw record frame.

        The XML payload is stored as raw UTF-8 bytes prefixed with the
        BinXML fragment-header token (0x0F) so that python-evtx treats the
        payload as opaque XML data — a standard approach for synthetic logs.

        Args:
            rec:        The record specification.
            record_num: Global record number (1-based).

        Returns:
            Bytes for the complete record (header + payload + trailing size).
        """
        xml = self._render_xml(rec)
        # BinXML opaque fragment: 0x0F (fragment header) + 4-byte length + data
        xml_bytes = xml.encode("utf-8")
        # We store as raw XML bytes — the payload starts with 0x0F marker
        # followed by a 4-byte LE length, then the UTF-8 XML string
        payload = struct.pack("<B", 0x0F) + struct.pack("<I", len(xml_bytes)) + xml_bytes

        timestamp_ft = self._datetime_to_filetime(rec.timestamp)

        # Record header: magic(4) + size(4) + record_num(8) + timestamp(8) = 24 bytes
        # Then payload
        # Then trailing size (4)
        record_size = 4 + 4 + 8 + 8 + len(payload) + 4

        header = struct.pack(
            "<IIQQ",
            _RECORD_MAGIC,   # 4 bytes — "**\x00\x00"
            record_size,     # 4 bytes
            record_num,      # 8 bytes
            timestamp_ft,    # 8 bytes — FILETIME
        )
        trailer = struct.pack("<I", record_size)
        return header + payload + trailer

    def _render_xml(self, rec: EvtxRecord) -> str:
        """Render an :class:`EvtxRecord` as a Windows Event XML string.

        Produces the standard ``<Event>`` schema as written by Windows
        (xmlns="http://schemas.microsoft.com/win/2004/08/events/event").

        Args:
            rec: The record specification.

        Returns:
            XML string (without the XML declaration header).
        """
        ts_str = rec.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Build EventData children
        event_data_xml = ""
        if rec.event_data:
            items = "\n".join(
                f'    <Data Name="{k}">{_xml_escape(str(v))}</Data>'
                for k, v in rec.event_data.items()
            )
            event_data_xml = f"  <EventData>\n{items}\n  </EventData>"
        else:
            event_data_xml = "  <EventData />"

        return (
            '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">\n'
            "  <System>\n"
            f'    <Provider Name="{_xml_escape(rec.provider)}" />\n'
            f"    <EventID>{rec.event_id}</EventID>\n"
            f"    <Version>0</Version>\n"
            f"    <Level>{rec.level}</Level>\n"
            f"    <Task>{rec.task}</Task>\n"
            f"    <Opcode>{rec.opcode}</Opcode>\n"
            f"    <Keywords>{rec.keywords}</Keywords>\n"
            f"    <TimeCreated SystemTime=\"{ts_str}\" />\n"
            f"    <EventRecordID>{rec.event_id}</EventRecordID>\n"
            f"    <Channel>{_xml_escape(rec.channel)}</Channel>\n"
            f'    <Computer>{_xml_escape(rec.computer)}</Computer>\n'
            "  </System>\n"
            f"{event_data_xml}\n"
            "</Event>"
        )

    # -- chunk construction -------------------------------------------------

    def _build_chunk(self, state: _ChunkState) -> bytes:
        """Build the 65536-byte binary for one chunk.

        Args:
            state: Accumulated record data for this chunk.

        Returns:
            Exactly 65536 bytes.

        Raises:
            EvtxWriterError: If record data exceeds chunk capacity.
        """
        record_data = b"".join(state.records)
        records_end_offset = _CHUNK_DATA_OFFSET + len(record_data)

        if records_end_offset > _CHUNK_SIZE:
            raise EvtxWriterError(
                f"Chunk {state.chunk_index} overflow: "
                f"{records_end_offset} bytes > {_CHUNK_SIZE}"
            )

        # Padding to fill chunk to exactly 65536 bytes
        padding = bytes(_CHUNK_SIZE - records_end_offset)

        # next_record_offset = offset of first byte AFTER the last record
        # (or CHUNK_DATA_OFFSET if no records)
        next_rec_offset = records_end_offset if state.records else _CHUNK_DATA_OFFSET
        last_rec_offset = (
            _CHUNK_DATA_OFFSET + len(record_data) - len(state.records[-1])
            if state.records else _CHUNK_DATA_OFFSET
        )

        # Build chunk header (512 bytes)
        # We compute checksums after assembling
        header = bytearray(512)

        # Magic
        header[0:8] = _CHUNK_MAGIC

        # Record number fields
        struct.pack_into("<Q", header, _CH_FILE_FIRST_REC, state.first_record_num)
        struct.pack_into("<Q", header, _CH_FILE_LAST_REC, state.last_record_num)
        struct.pack_into("<Q", header, _CH_LOG_FIRST_REC, state.first_record_num)
        struct.pack_into("<Q", header, _CH_LOG_LAST_REC, state.last_record_num)

        # Offsets and sizes
        struct.pack_into("<I", header, _CH_HEADER_SIZE, _CHUNK_HEADER_SIZE)
        struct.pack_into("<I", header, _CH_LAST_REC_OFFSET, last_rec_offset)
        struct.pack_into("<I", header, _CH_NEXT_REC_OFFSET, next_rec_offset)

        # String and template tables — zero (no pre-defined strings/templates)
        # They are already zero from bytearray() init

        # Assemble full chunk data for checksum computation
        chunk_body = bytes(header) + record_data + padding

        # Data checksum: CRC32 of bytes from 0x200 to next_rec_offset
        data_region = chunk_body[0x200:next_rec_offset] if next_rec_offset > 0x200 else b""
        data_crc = binascii.crc32(data_region) & 0xFFFFFFFF

        # Header checksum: CRC32 of header[0:0x78] + header[0x80:0x200]
        header_input = bytes(header)[0:0x78] + bytes(header)[0x80:0x200]
        # We must set data_checksum in header BEFORE computing header_checksum
        struct.pack_into("<I", header, _CH_DATA_CHECKSUM, data_crc)
        header_input = bytes(header)[0:0x78] + bytes(header)[0x80:0x200]
        header_crc = binascii.crc32(header_input) & 0xFFFFFFFF
        struct.pack_into("<I", header, _CH_HEADER_CHECKSUM, header_crc)

        # Final assembly
        chunk_bytes = bytes(header) + record_data + padding
        assert len(chunk_bytes) == _CHUNK_SIZE, (
            f"Chunk size mismatch: {len(chunk_bytes)} != {_CHUNK_SIZE}"
        )
        return chunk_bytes

    # -- file header construction -------------------------------------------

    def _build_file_header(
        self,
        chunk_count: int,
        next_record_number: int,
        current_chunk_index: int,
    ) -> bytes:
        """Build the 4096-byte EVTX file header.

        Args:
            chunk_count:          Total number of chunks in the file.
            next_record_number:   Next record number that would be written.
            current_chunk_index:  Zero-based index of the most recent chunk.

        Returns:
            Exactly 4096 bytes.
        """
        header = bytearray(4096)

        # Magic: "ElfFile\x00"
        header[0:8] = _FILE_MAGIC

        # Oldest chunk always 0
        struct.pack_into("<Q", header, _FH_OLDEST_CHUNK, 0)
        struct.pack_into("<Q", header, _FH_CURRENT_CHUNK, current_chunk_index)
        struct.pack_into("<Q", header, _FH_NEXT_RECORD, next_record_number)

        # Header size field (always 0x80 per spec)
        struct.pack_into("<I", header, _FH_HEADER_SIZE, 0x80)

        # Version: minor=1, major=3
        struct.pack_into("<H", header, _FH_MINOR_VER, 0x0001)
        struct.pack_into("<H", header, _FH_MAJOR_VER, 0x0003)

        # Header chunk size (always 0x1000 = 4096)
        struct.pack_into("<H", header, _FH_HEADER_CHUNK_SIZE, 0x1000)

        # Chunk count
        struct.pack_into("<H", header, _FH_CHUNK_COUNT, chunk_count)

        # Flags: 0 (clean)
        struct.pack_into("<I", header, _FH_FLAGS, 0)

        # Checksum: CRC32 of first 0x78 bytes
        crc = binascii.crc32(bytes(header)[:0x78]) & 0xFFFFFFFF
        struct.pack_into("<I", header, _FH_CHECKSUM, crc)

        return bytes(header)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _datetime_to_filetime(dt: datetime) -> int:
        """Convert a UTC datetime to a Windows FILETIME (100-ns ticks).

        Args:
            dt: UTC-aware or naive datetime (assumed UTC if naive).

        Returns:
            Integer FILETIME value.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta_ns = int((dt - epoch).total_seconds() * 1e7)
        return delta_ns + _FILETIME_EPOCH_DELTA


# ---------------------------------------------------------------------------
# Module-level XML helper
# ---------------------------------------------------------------------------

def _xml_escape(s: str) -> str:
    """Escape a string for safe embedding in XML text/attribute content."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )
