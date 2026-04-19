# ARC — Windows 11 Artifact Density Baselines

**Scope**: real-world artifact densities on a Windows 11 machine with 6–18 months of active use.
These numbers drive Phase 4 density targets, `config.yaml` per-day rates, and acceptance gates
A10–A15.

**Sources**: SANS FOR500 course data, DFIR tooling output on volunteer test machines, Microsoft
documentation for default sizes, Belkasoft/Magnet forensics blog posts, Eric Zimmerman's tooling
output published in conference talks.

---

## 1. Registry hives

### 1.1 Physical sizes (6–18 months use)

| Hive | Typical size | Large/power-user size |
|------|-------------|----------------------|
| `SYSTEM` | 10–25 MB | 40 MB |
| `SOFTWARE` | 80–200 MB | 400 MB |
| `SAM` | 40–60 KB | 200 KB (many local accounts) |
| `SECURITY` | 200–400 KB | 600 KB |
| `DEFAULT` | 200–500 KB | 1 MB |
| `NTUSER.DAT` (per user, active) | 5–20 MB | 50 MB |
| `UsrClass.dat` (per user) | 1–10 MB | 30 MB |

Total registry footprint: **100–300 MB** for a typical developer or office user.

### 1.2 Key counts

| Location | Keys (baseline fresh) | Keys (1-year active user) | ARC target |
|----------|----------------------|--------------------------|-----------|
| NTUSER.DAT total | ~3 000 | ~15 000–25 000 | +5 000 new |
| SOFTWARE total | ~20 000 | ~50 000–80 000 | Unchanged (baseline has them) |
| SYSTEM total | ~8 000 | ~12 000 | +500 service keys |
| UserAssist entries | 0 | 200–500 entries | 50+ distinct apps |
| RecentDocs entries | 0 | 500–2 000 entries | Per-day rate × 360 |
| RunMRU | 0 | 20–50 entries | 15+ entries |
| TypedPaths (Explorer address bar) | 0 | 50–200 entries | 30+ entries |
| Network profiles | 0 | 3–15 networks | 5+ profiles |
| MUI cache | 0 | 100–500 entries | 50+ entries |

### 1.3 UserAssist format

UserAssist keys live at:
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist\
  {CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}\Count\   (apps)
  {F4E57C4B-2036-45F0-A9AB-443BCFE33D9F}\Count\   (links)
```

Each value name is ROT13-encoded. Each value is a 72-byte REG_BINARY blob:

```
Offset  Size  Description
0x00    4     Sessions count (little-endian DWORD)
0x04    4     Run count (little-endian DWORD)
0x08    4     Focus count
0x0C    4     Focus time (milliseconds)
0x10    44    Padding (all zeros in practice)
0x3C    8     Last run time as Windows FILETIME
0x44    4     (unknown)
```

ARC writes: `count = rng.randint(3, 50)` for apps from `persona.installed_apps`; `last_run_time`
= last APP_LAUNCH event timestamp for that app from the scheduler.

### 1.4 RecentDocs MRU format

Under `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs\`:
- Root key: last 150 files regardless of extension
- Per-extension subkeys (`\.docx`, `\.xlsx`, `\.pdf`, …): up to 20 per extension

Value format: `REG_BINARY` blob containing:
- UTF-16LE null-terminated filename (varies)
- Null padding to 2 bytes
- Shell Item ID List (PIDL) suffix (~44–100 bytes, variable)

In practice ARC can simplify: write just the UTF-16LE filename + 2 null bytes + a 44-byte zero
PIDL stub. Windows re-resolves the full PIDL on next access.

---

## 2. NTFS $UsnJrnl

### 2.1 Default and typical sizes

| Setting | Value |
|---------|-------|
| `$Max.MaximumSize` default (desktop) | 32 MB |
| `$Max.MaximumSize` default (server) | 256 MB |
| `$Max.AllocationDelta` | 1–4 MB |
| Actual sparse `$J` size (6-month desktop) | 32 MB (wrapping) |
| Actual sparse `$J` size (1-year busy desktop) | 64–256 MB (journal wraps, oldest records purged) |
| Server/workstation with 2+ years | 512 MB – 3 GB |

### 2.2 Record count per time period

| Period | Records (light user) | Records (active user) |
|--------|---------------------|----------------------|
| Per hour | 50–200 | 500–5 000 |
| Per day | 500–2 000 | 5 000–50 000 |
| 360 days | 180 000–720 000 | 1 800 000–18 000 000 |

ARC target: **540 000 records over 360 days** (~1 500/day, light-to-moderate user).
This is ~62 MB at ~120 bytes/record — well within the 512 MB `MaximumSize` ARC sets.

### 2.3 Per-event record multiplier

Each user-visible action typically generates multiple USN records:

| Action | Records generated |
|--------|-----------------|
| Save a file | FILE_CREATE + DATA_EXTEND + CLOSE = 3 records (or DATA_OVERWRITE + CLOSE = 2) |
| Open a document (MRU update) | DATA_OVERWRITE + CLOSE on NTUSER.DAT MRU key = 2 |
| Download a file | FILE_CREATE + DATA_EXTEND + DATA_EXTEND + CLOSE = 4 |
| App launch (Prefetch update) | DATA_OVERWRITE + CLOSE on .pf file = 2 |
| Browser page visit (cache) | 5–20 records on various AppData files |

So 1 500 user-visible events/day → ~6 000–15 000 USN records/day. ARC can batch-generate
records for background activities (Prefetch, MRU, temp file cleanup) to reach realistic counts.

---

## 3. Prefetch files

### 3.1 File count

| Machine type | Prefetch file count |
|-------------|-------------------|
| Fresh install (post-OOBE) | 5–15 files |
| 1-month use | 30–50 files |
| 6-month use | 50–100 files |
| Prefetch max (registry limit) | 128 files (Win8+) |

ARC target: **≥ 30 files**.

### 3.2 File sizes

| Content | Size range |
|---------|-----------|
| Simple utility (e.g., NOTEPAD.EXE) | 10–20 KB |
| Medium complexity (e.g., CHROME.EXE launch) | 30–60 KB |
| Complex app (DEVENV.EXE, WORD.EXE) | 60–100 KB |

ARC target: **mean ≥ 15 KB** per file.

### 3.3 Key fields in .pf header (v30 format — MAM-compressed)

Win11 uses version 30 (or 31 for recent builds). v30 is well-documented:

```
Offset  Size  Description
0x00    4     Version (0x1E = v30)
0x04    4     Signature "SCCA"
0x08    4     Unknown
0x0C    4     Prefetch file size (compressed)
0x10    60    Executable name (UTF-16LE, zero-padded)
0x4C    4     Prefetch file hash (FNV-like, path-dependent)
0x50    4     Unknown flags
```

After decompression (MAM = LZ + Huffman used in v26+):

```
Section A: file metrics array (each entry = 32 bytes, one per referenced module)
Section B: trace chains array
Section C: filename strings (UTF-16LE paths of all referenced files)
Section D: volume information (2–4 volumes, each 104 bytes)
```

`last_run_times`: 8 × 64-bit FILETIMEs at a fixed offset in the decompressed data
(Section D of the header array). First entry = most recent run. ARC populates these from
APP_LAUNCH events.

`run_count`: DWORD in Section D header. Matches the number of APP_LAUNCH events for this app
plus a small random offset (the app ran before the tracked window too).

### 3.4 Prefetch hash algorithm

The filename component of a .pf file is `EXECNAME.EXE-XXXXXXXX.pf` where the 8-hex suffix is
a path-hash. The hash function is an FNV variant applied to the full path of the executable,
uppercased. Incorrect hashes mean Windows ignores the .pf file.

```python
def prefetch_hash_xp(path: str) -> int:
    """FNV-1a variant used by Windows XP/Vista."""
    path = path.upper()
    h = 0
    for c in path:
        h = ((h * 37 + ord(c)) & 0xFFFFFFFF)
    return h

def prefetch_hash_win8plus(path: str) -> int:
    """Hash used by Windows 8+, 10, 11."""
    # A different scrambler; exact algorithm reverse-engineered by Joachim Metz
    path_upper = path.upper()
    h = 314159265
    for c in path_upper:
        h = (h * 37 + ord(c)) & 0xFFFFFFFF
    return h
```

Source `services/filesystem/installed_apps_stub.py` must be the single source of truth for
app paths — Prefetch uses the same paths for hashing (R21).

---

## 4. Windows Event Log (EVTX)

### 4.1 File sizes on a 1-year active Windows 11 installation

| Channel | Default max size | Typical populated size (1 year) | Records |
|---------|-----------------|--------------------------------|---------|
| `Application.evtx` | 20 MB | 5–15 MB | 10 000–30 000 |
| `Security.evtx` | 128 MB | 10–50 MB | 20 000–100 000 |
| `System.evtx` | 20 MB | 5–10 MB | 10 000–20 000 |
| `Microsoft-Windows-PowerShell/Operational.evtx` | 15 MB | 1–5 MB | 2 000–10 000 |
| `Microsoft-Windows-TaskScheduler/Operational.evtx` | 4 MB | 1–4 MB | 5 000–20 000 |
| `Microsoft-Windows-Windows Defender/Operational.evtx` | 4 MB | 100 KB–1 MB | 100–500 |

ARC target (per MASTER_PLAN acceptance gate A12): **each primary log ≥ 10 MB, ≥ 5 000 records**.

### 4.2 EVTX file structure

```
[4 KB ELF file header]
[64 KB Chunk 0]
[64 KB Chunk 1]
...
[64 KB Chunk N]
[optional: 4 KB EOF record]
```

**ELF Header (4 096 bytes)**:

```
0x00  8    Signature "ElfFile\x00"
0x08  8    FirstChunkNumber (0)
0x10  8    LastChunkNumber  (N)
0x18  8    NextRecordIdentifier (next event record ID)
0x20  4    HeaderSize (0x80)
0x24  2    MinorVersion (1)
0x26  2    MajorVersion (3)
0x28  2    HeaderBlockSize (0x1000 = 4096)
0x2A  2    NumberOfChunks
0x2C  76   Padding
0x78  4    FileFlags (0 = normal)
0x7C  4    CRC32 of first 0x78 bytes
```

**Chunk header (512 bytes within each 64 KB chunk)**:

```
0x00  8    Signature "ElfChnk\x00"
0x08  8    LogFileFirstRecordNumber
0x10  8    LogFileLastRecordNumber
0x18  8    LogFileFirstRecordIdentifier
0x20  8    LogFileLastRecordIdentifier
0x28  4    HeaderSize (0x80)
0x2C  4    LastEventRecordDataOffset (offset within chunk)
0x30  4    FreeSpaceOffset
0x34  4    EventRecordsChecksum (CRC32 of all records in chunk)
0x38  64   CommonStringArray (provider GUIDs, channel identifiers)
0x78  4    Unknown
0x7C  4    ChunkHeaderCRC32
[512 bytes template table follows]
[records ...]
```

**Record format**:

```
0x00  4    Signature 0x2a2a (** = "**")
0x04  4    RecordSize (total including signature)
0x08  8    EventRecordID
0x10  8    TimeCreated (FILETIME)
[BinaryXML encoded EventData follows]
[4    RecordSize repeated at end]
```

### 4.3 BinaryXML

Records use a binary encoding of XML based on WinEvt's "BinaryXML" format. Key tokens:

| Token byte | Meaning |
|-----------|---------|
| 0x00 | EndOfFragment |
| 0x01 | OpenStartElement |
| 0x02 | CloseStartElement |
| 0x03 | CloseEmptyElement |
| 0x04 | EndElement |
| 0x05 | Value (followed by type byte + value) |
| 0x06 | Attribute |
| 0x0D | BinXmlName (string from name table) |
| 0x0F | TemplateReference |
| 0x13 | NormalSubstitution |
| 0x14 | OptionalSubstitution |

For ARC's approach: extract a reference template table from a real Win11 baseline EVTX, ship it
as `templates/evtx/reference_templates.bin`, and reuse provider GUIDs + template IDs.

Each event record = reference to a provider template + substituted values (timestamps, process
IDs, usernames, privilege lists). This avoids re-implementing the full BinaryXML encoder.

### 4.4 Key event IDs for ARC

| Channel | Event ID | Trigger | ARC generates |
|---------|----------|---------|--------------|
| Security | 4624 | Logon | Yes (LOGIN event) |
| Security | 4634 | Logoff | Yes (LOGOFF event) |
| Security | 4688 | Process created | Yes (APP_LAUNCH) |
| Security | 4663 | Object access | Selective (FILE_CREATE in audited dir) |
| Security | 4776 | Credential validation | Yes (LOGIN, NTLM) |
| Security | 4798 | User account queried | Occasional |
| System | 6005 | Event log started | Daily (LOGIN) |
| System | 6006 | Event log stopped | Daily (LOGOFF) |
| System | 6013 | System uptime | Daily |
| System | 1074 | Shutdown initiated | Daily (LOGOFF) |
| System | 19, 43 | Windows Update | Monthly (SYSTEM_UPDATE) |
| Application | 1000 | App crash (occasional) | Rare (1–3 per month) |
| Application | 1026 | .NET runtime error (occasional) | Rare |

---

## 5. Chrome History SQLite

### 5.1 Database sizes

| Table | Rows (6-month active user) | Size |
|-------|--------------------------|------|
| `urls` | 5 000–20 000 | 2–8 MB |
| `visits` | 15 000–80 000 | 5–30 MB |
| `keyword_search_terms` | 500–5 000 | 0.5–2 MB |
| `downloads` | 100–1 000 | 0.5–2 MB |
| `segments` | 200–2 000 | 0.2–1 MB |

ARC target (A13): **≥ 5 000 `urls` rows, ≥ 10 000 `visits` rows**.

### 5.2 visit_time encoding

Chrome stores times as **microseconds since 1601-01-01 00:00:00 UTC** (Windows FILETIME in
microseconds instead of 100-nanosecond units):

```python
def to_chrome_time(dt: datetime) -> int:
    epoch_offset = 11644473600  # seconds between 1601-01-01 and 1970-01-01
    return int((dt.timestamp() + epoch_offset) * 1_000_000)
```

### 5.3 Visit transition types

`visits.transition` field is a bitmask. Common values:

```
0x01000000  LINK (followed a hyperlink)
0x02000000  TYPED (user typed URL)
0x03000000  AUTO_BOOKMARK
0x04000000  AUTO_SUBFRAME
0x07000000  RELOAD
0x08000000  KEYWORD (triggered by a keyword)
```

Most visits are `LINK (0x01000001)` with `QUALIFIER_CHAIN_START` bit 0x10000000 for first in
session. Typed navigation: `0x12000002` (TYPED + CHAIN_START + qualifiers).

---

## 6. Documents and downloads

### 6.1 Document count and types

| Persona type | Documents (1 year) | Typical formats |
|-------------|-------------------|----------------|
| home_user | 200–800 | .docx, .xlsx, .pdf, .txt, .jpg |
| office_user | 500–2 000 | .docx, .xlsx, .pptx, .pdf, email attachments |
| developer | 300–1 500 | .py, .md, .txt, .json, .pdf, .docx |

ARC target (A14): **≥ 500 documents, all openable**.

### 6.2 Document density by directory

| Directory | Files (1-year user) |
|-----------|-------------------|
| `Users\<user>\Documents\` (recursive) | 100–1 000 |
| `Users\<user>\Desktop\` | 5–30 |
| `Users\<user>\Downloads\` | 50–500 |
| `Users\<user>\Pictures\` | 50–500 |
| `Users\<user>\Videos\` | 5–50 |
| `Users\<user>\Music\` | 10–500 |
| `Users\<user>\AppData\Roaming\Microsoft\Windows\Recent\` | 100–500 (.lnk files) |

### 6.3 Download size distribution

Real download files span:

| Category | Count/year | Typical size |
|----------|-----------|-------------|
| PDF documents | 50–200 | 50 KB–5 MB |
| Installer .exe/.msi (kept) | 5–20 | 1–100 MB |
| Archives (.zip, .7z) | 20–100 | 100 KB–500 MB |
| Images (.png, .jpg) | 20–100 | 50 KB–5 MB |
| Office documents | 50–300 | 10 KB–10 MB |

ARC writes file stubs at realistic sizes using python-docx (real parseable DOCX), openpyxl
(real parseable XLSX), reportlab (real parseable PDF), and random-bytes for archives/images.

---

## 7. Thumbnail cache

### 7.1 Database files

Windows maintains thumbnail caches in:
`Users\<user>\AppData\Local\Microsoft\Windows\Explorer\`

| File | Purpose | Typical size (1-year) |
|------|---------|----------------------|
| `thumbcache_16.db` | 16×16 thumbnails | 500 KB–2 MB |
| `thumbcache_32.db` | 32×32 | 1–5 MB |
| `thumbcache_96.db` | 96×96 | 5–20 MB |
| `thumbcache_256.db` | 256×256 | 10–50 MB |
| `thumbcache_1024.db` | 1024×1024 | 20–100 MB |
| `thumbcache_2560.db` | Retina / 4K | 10–200 MB (only on high-DPI) |
| `thumbcache_idx.db` | Index | 500 KB–5 MB |

ARC target for `home_user` (many photos): `thumbcache_256.db` ≥ 10 MB. Full 100 MB is optional
and drives VHDX delta size.

### 7.2 Format

Each `.db` is a CMMM-format database:

```
[4 KB header — "CMMM" signature, version, cache ID]
[Records: each has 80-byte header + JPEG/PNG thumbnail data + padding]
```

ARC can write valid CMMM records using appropriately-sized thumbnail blobs derived from
expansion `media` seeds.

---

## 8. Shell artefacts (Recent + Jump Lists)

### 8.1 Recent folder (`.lnk` files)

`Users\<user>\AppData\Roaming\Microsoft\Windows\Recent\`:

| Item count | 1-month user | 1-year user |
|------------|-------------|------------|
| .lnk files | 20–50 | 100–500 |

LNK files have a complex binary format (Shell Link Binary specification MS-SHLLINK). Key fields:
- Header (76 bytes): `CLSID`, `LinkFlags`, `FileAttributes`, `CreationTime`, `WriteTime`,
  `AccessTime`, `FileSize`, `IconIndex`, `ShowCommand`, `HotKey`
- LinkTargetIDList: series of `ItemID` blobs (Shell ItemID List / PIDL)
- StringData: relative and/or absolute path strings

ARC writes simplified LNK stubs using the `struct` pack approach or the `pylnk` library.

### 8.2 Automatic Destinations (Jump Lists)

`Users\<user>\AppData\Roaming\Microsoft\Windows\Recent\AutomaticDestinations\`:

Files named `<AppId>.automaticDestinations-ms` (OLE compound document / multi-stream format).
Each file is a list of recently-opened items per application.

A realistic 1-year machine has 10–30 `.automaticDestinations-ms` files.

ARC Phase 4 writes minimal versions using `olefile` (Python library for OLE compound docs).

---

## 9. VHDX delta budget

### 9.1 What ARC adds

| Category | Target size |
|---------|------------|
| Registry hives (new keys) | 50–200 MB |
| Documents + downloads | 500 MB–2 GB |
| Browser History + Cookies | 50–200 MB |
| EVTX logs | 50–150 MB |
| Prefetch files | 1–5 MB |
| Thumbnail caches | 10–200 MB |
| $UsnJrnl `$J` records | 50–100 MB |
| LNK + Jump Lists | 5–20 MB |
| PowerShell history | < 1 MB |
| cdp_logs | 1–10 MB |
| Office MRU | < 1 MB |
| **Total** | **~700 MB – 3 GB** |

ARC acceptance gate A15: **VHDX delta ≥ 500 MB** (the realistic minimum for a `home_user` with
moderate documents).

### 9.2 Baseline VHDX sizes

| Component | Size in baseline (post-OOBE) |
|-----------|---------------------------|
| Windows system files | ~12 GB |
| Empty user profile | ~200 MB |
| Pre-installed apps | ~500 MB |
| **Total baseline** | **~13 GB** |

Post-ARC: **~14–16 GB** for typical runs.

---

## 10. Evaluation baselines (fantasy vs real)

The current `evaluation/density_analyzer.py` uses fantasy baselines:

```python
# WRONG (current code):
REGISTRY_BASELINE = 150      # should be 50 000
FILESYSTEM_BASELINE = 80     # should be 10 000
BROWSER_BASELINE = 30        # should be 500 visits minimum
```

**Correct values for Phase 4 rewrite**:

```python
# Real baselines (Phase 4 target)
REGISTRY_NEW_KEYS_TARGET = 5_000       # keys ARC adds
FILESYSTEM_FILES_TARGET = 500          # documents ARC creates
BROWSER_URLS_TARGET = 5_000            # Chrome urls table rows
BROWSER_VISITS_TARGET = 10_000         # Chrome visits table rows
PREFETCH_COUNT_TARGET = 30             # .pf files
PREFETCH_MEAN_SIZE_KB = 15             # average .pf size in KB
EVTX_SIZE_MB_PER_LOG = 10             # per primary log channel
EVTX_RECORDS_PER_LOG = 5_000          # per primary log channel
VHDX_DELTA_MB = 500                   # minimum added bytes
```

---

## 11. Per-day rate derivation (config.yaml artifact_scale)

From SANS FOR500 and personal test-machine measurements:

| Artifact category | Observed events/day (active 8-hour workday) | `per_day` in config.yaml |
|------------------|--------------------------------------------|-----------------------|
| URL visits | 50–150 | 83 |
| Search queries | 10–25 | 16.7 |
| Documents created/modified | 8–20 | 12.5 |
| Downloads | 2–8 | 4.2 |
| Photos viewed/added | 1–4 | 2.1 |
| New bookmarks | 0.3–1.0 | 0.56 |

These rates are applied only to `persona.active_days` — typically 5 days/week = 260 active days
in 365. At 260 active days in 360-day window × 83 URL visits/day = **21 580 URL visits** from
scheduled activity. Additional burst events (weekends, evenings at reduced rate) bring the total
to ~30 000 for a `home_user`.

---

## 12. References

- SANS FOR500: Windows Forensic Analysis (course materials 2023–2024)
- Eric Zimmerman tooling output blog posts: https://ericzimmerman.github.io/
- Belkasoft Evidence Center documentation on Chrome / EVTX / Prefetch sizes
- Microsoft Docs: Event Log channel configuration, `EVTX` format specification (MS-EVEN6)
- LibScca documentation: https://github.com/libyal/libscca/blob/main/documentation/
- Windows Shellbag / LNK format: MS-SHLLINK specification
- ChromeDB schema: Chromium source `//components/history/core/browser/history_database.cc`
- ADR-004, ADR-013 — `docs/design/decisions.md`
- Phase 4 acceptance gates A10–A15 — `docs/MASTER_PLAN.md` §11
