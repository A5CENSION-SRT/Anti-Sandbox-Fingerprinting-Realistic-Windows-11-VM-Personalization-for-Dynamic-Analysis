# ARC — NTFS Journal Research

**Scope**: structure of the NTFS change journal (`$UsnJrnl`), the `$MFT` timestamp attributes
(`$STANDARD_INFORMATION` vs `$FILE_NAME`), the `$LogFile` transaction log, and how ARC writes
each from a Linux host.

**Decisions recorded**: ADR-008, ADR-009.

---

## 1. Overview of NTFS forensic metadata

Windows NTFS maintains three separate structures that forensics tools use to establish a timeline:

| Structure | Location | What it tracks | User-writable? |
|-----------|----------|---------------|----------------|
| `$STANDARD_INFORMATION` (SI) | `$MFT` record, attribute 0x10 | ctime, atime, mtime, creation time | Yes — via `SetFileTime` / `setfattr system.ntfs_times` |
| `$FILE_NAME` (FN) | `$MFT` record, attribute 0x30 | same four times; set at file create, updated only on rename | **No — kernel-only** |
| `$UsnJrnl:$J` | `\$Extend\$UsnJrnl:$J` alternate data stream | log of every file/dir operation (create, modify, delete, rename, close) | Write via raw stream only |
| `$UsnJrnl:$Max` | `\$Extend\$UsnJrnl:$Max` | journal configuration + next USN pointer | Read/write |
| `$LogFile` | `\$LogFile` | circular NTFS transaction log (redo/undo LSN records) | Dangerous; best-effort only |

NTFS Triforce forensics (advocated by researchers at SANS DFIR) cross-validates all five:

```
SI mtime == FN mtime ?              → No: file was "timestomped" via SetFileTime
SI creation == FN creation ?        → No: file moved/renamed after creation (realistic)
$UsnJrnl: FILE_CREATE record for X exists? → No: file appeared without a journal trace
$LogFile: LSN of UsnJrnl write?     → Missing: $LogFile doesn't cover all $J writes
```

ARC's goal: produce SI timestamps that match the scheduler's event times, a `$UsnJrnl` with
coherent records for every scheduler-emitted file operation, and accept SI/FN divergence as a
realistic artifact of the "user had this file for a while and renamed it" pattern.

---

## 2. `$STANDARD_INFORMATION` (SI) — the writable timestamp quad

### 2.1 Field layout (attribute 0x10, 48-byte standard form)

All four timestamps are 64-bit Windows FILETIMEs, stored little-endian:

```
Offset  Size  Field
0x00    8     Creation time (ctime in Windows parlance — not change time)
0x08    8     Last modification time (mtime)
0x10    8     Last change time (ctime in POSIX sense — metadata change)
0x18    8     Last access time (atime)
0x20    4     File attributes (archive, hidden, system, …)
0x24    4     Maximum number of versions
0x28    4     Version number
0x2C    4     Class ID
--- extended form (> 72 bytes in newer NTFS) ---
0x30    8     Owner ID
0x38    8     Security ID
0x40    8     Quota charged
0x48    8     USN (last journal record for this file)
```

Windows FILETIME = 100-nanosecond intervals since 1601-01-01 UTC:

```python
def unix_to_filetime(unix_ts: float) -> int:
    EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 to 1970-01-01
    return int((unix_ts + EPOCH_OFFSET) * 10_000_000)
```

### 2.2 Patching via ntfs-3g `setfattr system.ntfs_times`

ntfs-3g exposes SI timestamps via the `system.ntfs_times` extended attribute. The value is
32 bytes: four 64-bit FILETIMEs in order **atime, mtime, ctime, crtime** (access, modified,
metadata-change, creation).

```python
import struct, subprocess

def patch_si_times(fuse_path: str, ts: datetime) -> None:
    ft = unix_to_filetime(ts.timestamp())
    # ntfs_times = atime, mtime, ctime, crtime (all same for simplicity)
    packed = struct.pack("<QQQQ", ft, ft, ft, ft)
    hex_val = "0x" + packed.hex()
    subprocess.run(
        ["setfattr", "-n", "system.ntfs_times", "-v", hex_val, fuse_path],
        check=True
    )
```

**What this patches**: only `$STANDARD_INFORMATION`. `$FILE_NAME` timestamps remain unchanged.
This is the ADR-009 trade-off: SI is in the past (per the scheduler), FN is at "ARC run time"
(when libguestfs wrote the file). The divergence is acceptable — a real user whose file was saved
years ago would show the same divergence if they renamed or moved it since.

### 2.3 `$FILE_NAME` (FN) — why we don't patch it

`$FILE_NAME` timestamps are updated by the NTFS kernel driver on:
- File creation (ctime = mtime = atime = crtime = now)
- Rename / move (crtime unchanged; mtime and ctime bumped)

There is no public API to set FN timestamps on a live or FUSE-mounted volume. Options:
- **Direct MFT manipulation**: parse `$MFT`, locate the FN attribute, overwrite bytes. Risk:
  MFT checksum mismatch crashes chkdsk; MFT entry reuse complicates attribute lookup. Deferred.
- **Double rename**: rename to a temp name and back. Forces the kernel to update FN at rename
  time — but FN's new timestamps would be "now" (ARC run time), not the scheduler's past time.
  No improvement over leaving it alone.
- **Accept divergence**: chosen (ADR-009). Forensics sees "SI says 2024-07-15, FN says 2026-04-21"
  for every ARC-created file — consistent with a cohort of files that were written two years ago
  and recently synced to this machine.

---

## 3. `$UsnJrnl` — the change journal

### 3.1 Structure

The USN journal lives in the hidden system file `\$Extend\$UsnJrnl`, which has two data streams:

- `$Max` (always 64 bytes) — configuration header
- `$J` (sparse, typically 32 MB max-size on default installs, grows to 512 MB–3 GB on busy servers)

The `$J` stream is sparse: the beginning is a "hole" (unallocated clusters); only the recent tail
is actually allocated. Windows reads from `$Max.LowestValidUsn` onward.

#### `$Max` header (64 bytes, little-endian):

```
Offset  Size  Field
0x00    8     MaximumSize       (default 0x2000000 = 32 MB; server default 0x10000000 = 256 MB)
0x08    8     AllocationDelta   (amount to grow $J when full; default 0x100000 = 1 MB)
0x10    8     UsnId             (volume-specific ID; random 64-bit; set at format time)
0x18    8     LowestValidUsn    (USN of the oldest readable record in $J)
0x20    8     NextUsn           (USN of the next record to be written)
0x28    8     (reserved)
0x30    8     (reserved)
```

ARC reads `$Max` before appending records, uses `NextUsn` as the base for new record USNs,
and writes back an updated `$Max` with the new `NextUsn` after the append.

### 3.2 USN_RECORD_V2 format (standard)

**Version 2** is the baseline format, used on volumes formatted before Windows 8.1 or with NTFS
version < 3.0. Still accepted by Win11.

```
Offset  Size  Field
0x00    4     RecordLength      (total, including filename; 8-byte aligned)
0x04    2     MajorVersion      (2)
0x06    2     MinorVersion      (0)
0x08    8     FileReferenceNumber  (48-bit MFT entry index + 16-bit sequence number)
0x10    8     ParentFileReferenceNumber
0x18    8     Usn               (unique sequence number; monotonically increasing)
0x20    8     TimeStamp         (FILETIME)
0x28    4     Reason            (bitmask — see §3.4)
0x2C    4     SourceInfo        (usually 0)
0x30    4     SecurityId        (usually 0)
0x34    4     FileAttributes    (FILE_ATTRIBUTE_* values)
0x38    2     FileNameLength    (bytes; not chars)
0x3A    2     FileNameOffset    (offset from start of record; usually 0x3C)
0x3C    var   FileName          (UTF-16LE, no null terminator)
--- padding to 8-byte boundary ---
```

Fixed-size header: **60 bytes** (0x3C). Total record size: `((60 + FileNameLength + 7) & ~7)`.

### 3.3 USN_RECORD_V3 format (Win8+, Win11 default for large volumes)

**Version 3** adds 128-bit file references (for file systems > 2^48 files, not common in practice,
but Win11 defaults to V3 on volumes formatted with the latest NTFS driver).

```
Offset  Size  Field
0x00    4     RecordLength
0x04    2     MajorVersion      (3)
0x06    2     MinorVersion      (0)
0x08    16    FileReferenceNumber  (128-bit)
0x18    16    ParentFileReferenceNumber  (128-bit)
0x28    8     Usn
0x30    8     TimeStamp
0x38    4     Reason
0x3C    4     SourceInfo
0x40    4     SecurityId
0x44    4     FileAttributes
0x48    2     FileNameLength
0x4A    2     FileNameOffset    (usually 0x4C)
0x4C    var   FileName          (UTF-16LE)
--- padding to 8-byte boundary ---
```

Fixed-size header: **76 bytes** (0x4C). ARC uses V3 (ADR-008) to match Win11 default output.

```python
import struct

def pack_usn_record_v3(
    file_ref: int,          # lower 48 bits = MFT index, upper 16 = seq num (pack into 128-bit)
    parent_ref: int,
    usn: int,
    timestamp: datetime,
    reason: int,            # USN_REASON_* bitmask
    file_attributes: int,   # FILE_ATTRIBUTE_*
    filename: str,
) -> bytes:
    fn_utf16 = filename.encode("utf-16-le")
    fn_len = len(fn_utf16)
    # Total = 0x4C (header) + fn_len, rounded up to 8-byte boundary
    total = (0x4C + fn_len + 7) & ~7
    hdr = struct.pack(
        "<IHH",
        total,       # RecordLength
        3,           # MajorVersion
        0,           # MinorVersion
    )
    # 128-bit file references: pack 64-bit MFT ref into lower 8 bytes, zero upper 8
    file_ref_128 = struct.pack("<QQ", file_ref, 0)
    parent_ref_128 = struct.pack("<QQ", parent_ref, 0)
    usn_ts = struct.pack(
        "<QQ",
        usn,
        unix_to_filetime(timestamp.timestamp()),
    )
    flags = struct.pack(
        "<IIIIHH",
        reason,
        0,                  # SourceInfo
        0,                  # SecurityId
        file_attributes,
        fn_len,
        0x4C,              # FileNameOffset
    )
    record = hdr + file_ref_128 + parent_ref_128 + usn_ts + flags + fn_utf16
    record += b'\x00' * (total - len(record))   # pad
    return record
```

### 3.4 Reason flags (USN_REASON_*)

```
0x00000001  DATA_OVERWRITE       — existing data overwritten
0x00000002  DATA_EXTEND          — file data extended (size increased)
0x00000004  DATA_TRUNCATION      — file data truncated
0x00000010  NAMED_DATA_OVERWRITE — ADS overwritten
0x00000020  NAMED_DATA_EXTEND    — ADS extended
0x00000040  NAMED_DATA_TRUNCATION
0x00000100  FILE_CREATE          — file newly created
0x00000200  FILE_DELETE          — file deleted
0x00000400  EA_CHANGE            — Extended attribute changed
0x00000800  SECURITY_CHANGE      — ACL changed
0x00001000  RENAME_OLD_NAME      — old name in a rename
0x00002000  RENAME_NEW_NAME      — new name in a rename
0x00004000  INDEXABLE_CHANGE
0x00008000  BASIC_INFO_CHANGE    — attribute flags or timestamp changed
0x00010000  HARD_LINK_CHANGE
0x00020000  COMPRESSION_CHANGE
0x00040000  ENCRYPTION_CHANGE
0x00080000  OBJECT_ID_CHANGE
0x00100000  REPARSE_POINT_CHANGE
0x00200000  STREAM_CHANGE        — named data stream created/deleted/renamed
0x00400000  TRANSACTED_CHANGE
0x80000000  CLOSE                — handle to file closed
```

In practice, most records combine `CLOSE` with one or more other flags. Common patterns:

```
File created:   FILE_CREATE | DATA_EXTEND | CLOSE
File modified:  DATA_OVERWRITE | CLOSE
File deleted:   FILE_DELETE | CLOSE
File renamed:   RENAME_OLD_NAME (old record) + RENAME_NEW_NAME (new record)
Timestamp set:  BASIC_INFO_CHANGE | CLOSE
```

### 3.5 Appending to `$J` via ntfs-3g

The `$J` stream is a sparse file. The canonical write approach:

```python
def append_usn_records(fuse_mount: str, records: list[bytes]) -> None:
    j_path = os.path.join(fuse_mount, "$Extend", "$UsnJrnl:$J")
    max_path = os.path.join(fuse_mount, "$Extend", "$UsnJrnl:$Max")

    # Read $Max to get NextUsn and LowestValidUsn
    with open(max_path, "rb") as f:
        max_data = f.read(64)
    (max_size, alloc_delta, usn_id,
     lowest_usn, next_usn, _, _) = struct.unpack("<QQQQQQQQ"[:7] + "x", max_data[:56] + b"\x00")
    # Note: $Max is exactly 64 bytes; fields at 0x00, 0x08, 0x10, 0x18, 0x20, 0x28, 0x30

    # Seek to current end ($J is sparse; seek to next_usn which is the write cursor)
    with open(j_path, "r+b") as f:
        f.seek(next_usn)
        new_usn = next_usn
        for record in records:
            # Patch the USN field inside the record (offset 0x28 in V3)
            record = record[:0x28] + struct.pack("<Q", new_usn) + record[0x36:]
            f.write(record)
            new_usn += len(record)

    # Update $Max.NextUsn
    updated_max = max_data[:0x20] + struct.pack("<Q", new_usn) + max_data[0x28:]
    with open(max_path, "r+b") as f:
        f.write(updated_max)
```

**Important**: the USN value at byte 0x28 of each V3 record must equal the file offset in `$J`
where that record begins. So we increment `new_usn` by `len(record)` after each write.

### 3.6 `$Max` read format (corrected)

```python
def read_max_header(max_path: str) -> dict:
    with open(max_path, "rb") as f:
        data = f.read(64)
    fmt = "<QQQQQQQQ"
    fields = struct.unpack(fmt, data)
    return {
        "maximum_size":     fields[0],
        "allocation_delta": fields[1],
        "usn_id":           fields[2],
        "lowest_valid_usn": fields[3],
        "next_usn":         fields[4],
        # fields[5], fields[6], fields[7] — reserved
    }
```

### 3.7 `FileReferenceNumber` — MFT entry to file reference mapping

USN_RECORD_V3 requires `FileReferenceNumber` and `ParentFileReferenceNumber`. These are the
MFT entry numbers of the file and its parent directory.

ARC obtains them by reading the `system.ntfs_file_id` extended attribute via ntfs-3g after
each file is created:

```python
import os

def get_mft_ref(fuse_path: str) -> int:
    # ntfs-3g exposes the MFT record number as system.ntfs_file_id (8 bytes, LE int64)
    raw = os.getxattr(fuse_path, "system.ntfs_file_id")
    return struct.unpack("<Q", raw)[0]
```

The 64-bit reference number encodes: lower 48 bits = MFT entry index, upper 16 bits = sequence
number (incremented each time the entry is reused after deletion).

---

## 4. `$LogFile` — the NTFS transaction log

### 4.1 What it is

`$LogFile` is a circular transaction log that records redo/undo LSN (Log Sequence Number) records
for every NTFS metadata operation. It is separate from `$UsnJrnl` — the journal records *what*
changed; `$LogFile` records *how to roll it back* if a crash occurs.

A forensics tool reading `$LogFile` can:
- Verify that every USN record has a corresponding LSN in `$LogFile`.
- Detect that entries were added offline without matching log entries (the LSN gap is detectable).

### 4.2 Why ARC does not write it (Phase 1 scope)

Correctly writing `$LogFile` requires:
- Parsing the circular buffer format (two 4 KB restart areas + 64 MB circular pages).
- Assigning LSN values that are monotonically increasing and consistent with the volume's
  restart LSN counter.
- Writing redo/undo operation records in NTFS's internal format (not public documentation).

This is substantially harder than `$UsnJrnl`. The `$LogFile` format is partially documented in
Brian Carrier's *File System Forensic Analysis* (chapter 13) and in the `libbfio` + `libfsntfs`
C libraries, but there is no Python library for constructing valid `$LogFile` records.

**Risk accepted (R4)**: a forensics tool doing full Triforce analysis (Harlan Carvey's methodology)
will see a `$LogFile` gap — USN records in `$J` without corresponding LSN records in `$LogFile`.
For malware sandbox dynamic analysis (ARC's target), this level of Triforce scrutiny is rare.
Flag as a known inconsistency in the realism report.

**Future work**: Phase 4b stub (`services/ntfs/logfile_writer.py`) documents the intent and
returns an empty op-list. Full $LogFile synthesis is deferred to v2.

---

## 5. Win11 NTFS journal in numbers

| Metric | Default value | Notes |
|--------|--------------|-------|
| `$Max.MaximumSize` | 32 MB (desktop), 256 MB (server) | Configurable via `fsutil usn createjournal maxsize=<N> allocationdelta=<D> <vol>` |
| `$Max.AllocationDelta` | 1 MB | Amount grown when journal wraps |
| Typical `$J` size on 1-year-old desktop | 512 MB – 2 GB | Sparse file; actual allocated clusters are ~10-100 MB |
| Records per day (active user) | 5 000 – 50 000 | ARC targets ~1 500 records/day = 540 000 over 360 days |
| Average record size (V3) | ~120 bytes | 76-byte header + avg 44-byte filename |
| 540 000 records at 120 bytes each | ~62 MB | Fits comfortably in a 512 MB journal |
| `NextUsn` advance per 360-day run | ~62 MB | Well below `MaximumSize` |

ARC sets `$Max.MaximumSize = 0x20000000` (512 MB, matching busy Win11 desktop defaults) before
appending, to avoid triggering Windows's journal-wrap reclaim on first boot.

---

## 6. Acceptance gate

**A6** (from `docs/MASTER_PLAN.md`):

After booting the modified VHDX in a VM:
```cmd
fsutil usn readjournal C:
```
Output must show:
- `Usn Journal Id:` — non-zero
- `First Usn:` — at least 300 days before the current date
- `Next Usn:` — a few bytes after the last ARC-written record
- `Lowest Valid Usn:` — matches `$Max.LowestValidUsn`
- `Max Usn:` — 0x7fffffffffffffff (unsigned max, normal)
- `Maximum Size:` — 0x20000000 (512 MB as set by ARC)
- `Allocation Delta:` — 0x100000 (1 MB)

Validate record count:
```powershell
$j = [System.IO.File]::Open("\\.\C:", [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
# ... or use EricZimmerman's MFTECmd: MFTECmd.exe -f C:\$MFT --csv out\
```
Or from Linux pre-boot:
```bash
python -m tools.usn_parser /mnt/arc/\$Extend/\$UsnJrnl:\$J | wc -l
# Target: ≥ 500 000
```

---

## 7. References

- Brian Carrier, *File System Forensic Analysis* (2005), Chapter 13: NTFS
- Microsoft Docs: `USN_RECORD_V2`, `USN_RECORD_V3`, `USN_JOURNAL_DATA_V2` structures
- ntfs-3g source: `ntfs-3g/include/ntfs-3g/ntfstypes.h`, `ntfs-3g/libntfs-3g/unistr.c`
- Eric Zimmerman's MFTECmd: `https://github.com/EricZimmerman/MFTECmd`
- Harlan Carvey, NTFS Triforce methodology: `https://www.kazamiya.net/ntfs-triforce`
- ADR-008, ADR-009 — `docs/design/decisions.md`
