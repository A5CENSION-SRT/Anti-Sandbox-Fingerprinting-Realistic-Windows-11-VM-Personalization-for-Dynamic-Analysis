# ARC — Time Integrity and Cross-Domain Coherence

**Scope**: the complete model that governs how timestamps, event records, and filesystem metadata
stay coherent across seven domains when ARC rewrites a 360-day persona into a VHDX.

**Decisions recorded**: ADR-005, ADR-009, ADR-012.

---

## 1. Why coherence matters

A malware analyst or sandbox platform running NTFS Triforce analysis cross-validates:

```
$MFT SI creation time == $UsnJrnl FILE_CREATE timestamp?
$MFT SI mtime == $UsnJrnl DATA_EXTEND | CLOSE timestamp?
Chrome visits.visit_time == Security.evtx 4688 (browser launch) within ±1 min?
Prefetch last_run_times[0] == Security.evtx 4688 for same app within ±5 s?
UserAssist run count for App.exe == count of APP_LAUNCH events in scheduler?
```

If ARC does not coordinate timestamps, every service writes "now" and every cross-domain check
fails trivially. The detection surface is not exotic — standard tools (`MFTECmd`, `EvtxECmd`,
`PECmd`, `RECmd`) expose all of these in one report.

ARC's answer: a single `EventScheduler` emits events with committed timestamps; every service
consumes those events and writes exactly those timestamps. No service is allowed to invent time
or randomness independently.

---

## 2. The seven domains

A single user-visible action fans out to up to seven domains that must agree:

| Domain | On-disk location | Key timestamp fields | Coherence check |
|--------|-----------------|---------------------|----------------|
| **NTFS SI** | `$MFT` attribute 0x10 | creation, mtime, atime, change | must match scheduler event timestamp ±1 s |
| **NTFS FN** | `$MFT` attribute 0x30 | same four | set at create; diverges on rename (ADR-009) |
| **$UsnJrnl** | `\$Extend\$UsnJrnl:$J` | `TimeStamp` field in USN_RECORD | must match same event timestamp ±2 s |
| **EVTX** | `Windows\System32\winevt\Logs\*.evtx` | `<TimeCreated SystemTime="...">` | per-channel records timed to same event |
| **Registry MRU** | `NTUSER.DAT` MRU keys | hive key `LastWriteTime` | per-event MRU bump carries the timestamp |
| **Browser SQLite** | `Users\...\Chrome\User Data\Default\History` | `urls.last_visit_time`, `visits.visit_time` | Chrome timestamps in WebKit epoch |
| **App-specific files** | Prefetch `.pf`, Office MRU `.lnk`, Recent `.lnk` | last_run_times in PF header; LNK modified time | must match APP_LAUNCH / FILE_CREATE event |

---

## 3. Canonical event types and fan-out

`EventScheduler` emits `SyntheticEvent(kind, timestamp, payload)`. Every kind produces a precise
fan-out obligation across the seven domains.

### 3.1 `APP_LAUNCH(app: str, t: datetime)`

```
NTFS SI:    touch Prefetch/APP.EXE-XXXXXXXX.pf  →  mtime = t
$UsnJrnl:   USN_REASON_DATA_EXTEND | CLOSE for the .pf file at t
EVTX:       Security.evtx  event 4688 ("A new process has been created") at t
            - NewProcessName: full path of app executable
            - ParentProcessName: Explorer.exe / CMD / PowerShell depending on context
Registry:   HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist\
              {CEBFF5CD...}\Count\<ROT13(app.exe)>
              → REG_BINARY: count++ at offset 4; last_run_time FILETIME at offset 60
            HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\
              Compatibility Assistant\Persisted → add app path = DWORD 1 (if compatibility)
Prefetch:   .pf header: last_run_times[0] = t; rotate existing times down; run_count++
            → size varies: 10–80 KB based on trace-chain length
```

### 3.2 `FILE_CREATE(path: str, creator_app: str, t: datetime)`

```
NTFS SI+FN: ctime = mtime = atime = crtime = t  (FN only set at create — ADR-009)
$UsnJrnl:   USN_REASON_FILE_CREATE | DATA_EXTEND | CLOSE at t
EVTX:       Security.evtx event 4663 ("An attempt was made to access an object")
            only if SACL is enabled on parent directory (normal for Documents)
Registry:   HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs\
              .<ext> MRU key: prepend new entry, bump sequence
Shell LNK:  AppData\Roaming\Microsoft\Windows\Recent\<filename>.lnk
            → Windows Shell LNK with creation time = t
If Office doc:
  Registry: HKCU\Software\Microsoft\Office\16.0\<App>\User MRU\LiveId_XXX\File MRU
            → new entry with mtime = t
  File:     AppData\Roaming\Microsoft\Office\Recent\<basename>.lnk
If downloaded:
  ADS:      Zone.Identifier alternate data stream:
              [ZoneTransfer]
              ZoneId=3
              ReferrerUrl=<referrer>
              HostUrl=<url>
```

### 3.3 `FILE_MODIFY(path: str, t: datetime)`

```
NTFS SI:    mtime = change_time = t  (FN unchanged — ADR-009)
$UsnJrnl:   USN_REASON_DATA_OVERWRITE | CLOSE at t
NTFS SI:    parent directory — change_time = t  (parent dir mtime bumped on child change)
```

### 3.4 `FILE_DELETE(path: str, t: datetime, shift_delete: bool = False)`

```
NTFS SI:    entry marked deleted in $MFT (bit in record header); timestamps remain
$UsnJrnl:   USN_REASON_FILE_DELETE | CLOSE at t
Recycle:    if not shift_delete:
              $Recycle.Bin\S-1-5-21-...\$I<random6>.ext  (header with original path + deletion time)
              $Recycle.Bin\S-1-5-21-...\$R<random6>.ext  (copy of file data)
```

### 3.5 `URL_VISIT(url: str, t: datetime, with_download: bool = False)`

```
Chrome History DB:
  urls: INSERT OR REPLACE (url, last_visit_time=webkit_time(t), visit_count++)
  visits: INSERT (url_id, visit_time=webkit_time(t), from_visit, ...)
  keyword_search_terms: INSERT if URL contains search query pattern
  segments: touch or create for navigation segments
Chrome Cookies DB:
  cookies: last_access_time = webkit_time(t) for matching domain
If with_download:
  → synthesise FILE_CREATE(download_path, "chrome.exe", t) fan-out (see §3.2)
  Chrome History downloads: INSERT (url, current_path, total_bytes, end_time=webkit_time(t), ...)
```

**WebKit epoch**: Chrome stores timestamps as microseconds since 1601-01-01 (Windows FILETIME
units but in microseconds, not 100-ns intervals).

```python
def unix_to_webkit(unix_ts: float) -> int:
    WEBKIT_EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 to 1970-01-01
    return int((unix_ts + WEBKIT_EPOCH_OFFSET) * 1_000_000)
```

### 3.6 `LOGIN(t: datetime)` / `LOGOFF(t: datetime)`

```
EVTX:
  Security.evtx:
    4624  "An account was successfully logged on"  at t
          LogonType=2 (interactive), TargetUserName=persona.username
    4634  "An account was logged off"  at logoff_t
  System.evtx:
    6005  "The Event Log service was started"  (on LOGIN — first of the day)
    6006  "The Event Log service was stopped"  (on LOGOFF — last of the day)
    6013  "The system uptime is <N> seconds"  (on LOGIN; N = seconds since boot)
    1074  "The process ... has initiated the restart of computer ..."  (on LOGOFF)
Registry:
  NTUSER.DAT root key LastWriteTime = t
  HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist\
    {CEBFF5CD...}\Count  — root key LastWriteTime touched at t
```

### 3.7 `SYSTEM_UPDATE(kb: str, t: datetime)`

```
EVTX:
  System.evtx:
    19  "Installation Successful: Windows successfully installed the following update: <kb>"
    43  "Installation Started: ..."
Registry:
  HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\HotFix\<kb>
    InstallDate = DWORD (unix epoch // 86400 × 86400 expressed as YYYYMMDD int)
  HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages\
    Package_for_<kb>~...   LastInstallDate = DWORD
```

---

## 4. EventScheduler design contract

```python
class EventScheduler:
    def __init__(
        self,
        persona: PersonaContext,
        install_time: datetime,
        now: datetime,
        rng: random.Random,
    ): ...

    def emit(self) -> Iterable[SyntheticEvent]:
        """
        Deterministic stream of SyntheticEvent, sorted by timestamp ascending.
        Rules:
          - Only emit events on days in persona.active_days (ISO weekday ints, 1=Mon..7=Sun)
          - Only emit events during persona.work_hours_start..work_hours_end (in persona.locale TZ)
          - LOGIN/LOGOFF bracket each active day
          - App launches follow a Poisson process within the work window
          - File operations follow a sub-Poisson burst pattern
          - URL visits are more frequent than file ops (~83/day vs ~12/day for home_user)
          - System updates cluster ~monthly (KB schedule from data/kb_updates.json)
          - Long gaps: vacations (1–2 per year, 5–10 days each), holidays
        """

    def events_of(self, *kinds: str) -> list[SyntheticEvent]:
        """Return all emitted events of the given kinds, in timestamp order."""

    def child_rng(self, name: str) -> random.Random:
        """
        Deterministic child RNG seeded by hash(master_seed ^ hash(name)).
        Each service gets its own child_rng, so their draws don't interfere.
        """
```

### 4.1 RNG hierarchy

```
master_rng = Random(--random-seed)
  child_rng("event_scheduler") → drives event timing
    child_rng("event_scheduler.app_launch") → Poisson arrival times for app launches
    child_rng("event_scheduler.file_ops") → file operation clustering
    child_rng("event_scheduler.url_visits") → URL visit burstiness
  child_rng("registry.userassist") → per-app count jitter
  child_rng("registry.recentdocs") → MRU ordering
  child_rng("browser.history") → visit duration, from_visit chains
  child_rng("filesystem.prefetch") → trace-chain length per app
  child_rng("eventlog.security") → logon ID randomness
  ... (one per service)
```

Same `--random-seed` + same `PersonaContext` → identical master_rng → identical child seeds →
identical event stream → identical audit log (ADR-012, A16).

### 4.2 Day-level schedule

```
For each day D in [install_time, now] (360 days total):
  weekday = D.isoweekday()              # 1=Mon..7=Sun
  if weekday not in persona.active_days:
    → emit no events for D
    continue
  if D is in persona.locale's public holiday calendar:
    → maybe emit no events (probability 0.7 no activity on holidays)
  if D is in a "vacation block" (1–2 per year, length from child_rng):
    → emit no events for D
    continue

  # Active day
  session_start = work_hours_start + rng.gauss(0, 15 min) clamped to +/- 45 min
  session_end   = work_hours_end + rng.gauss(0, 20 min)

  emit LOGIN(timestamp = session_start)
  # emit System.evtx events for boot if this is first session of week
  emit_poisson_app_launches(rate=persona.apps_per_session, window=[session_start, session_end])
  emit_poisson_url_visits(rate=persona.urls_per_session, window=[session_start, session_end])
  emit_file_operations(rate=persona.file_ops_per_session)
  emit LOGOFF(timestamp = session_end)
```

### 4.3 Intra-session structure

Sessions are not purely random. A realistic user:
- Launches an app (APP_LAUNCH) → creates files in that app (FILE_CREATE × N) → modifies them
  (FILE_MODIFY × M) → visits URLs while working (URL_VISIT bursts) → closes the app → launches
  next app.

This "app slot" pattern produces correlated events. EventScheduler implements it:

```python
# App slot: app is active for duration drawn from child_rng
for slot in app_slots:
    yield APP_LAUNCH(slot.app, slot.start)
    for i in range(slot.file_create_count):
        t = slot.start + rng.uniform(0, slot.duration)
        yield FILE_CREATE(slot.typical_path / filename, slot.app, t)
    for url in slot.associated_urls:
        t = slot.start + rng.uniform(0, slot.duration)
        yield URL_VISIT(url, t)
```

---

## 5. Timezone handling

`PersonaContext.locale` specifies an IANA timezone string (e.g., `"America/Chicago"`).

**Rule**: all scheduler events are UTC internally. Each service converts to the persona's local
time only at write time, for values that Windows stores in local time.

| Storage format | Examples | Conversion |
|----------------|---------|-----------|
| UTC (universal) | `$MFT` SI timestamps, `$UsnJrnl` TimeStamp, EVTX `<TimeCreated SystemTime>` | No conversion; write UTC FILETIME directly |
| Local time | Registry `LastWriteTime` (FILETIME, but set by Windows to local) | Convert `utc_event.timestamp()` → local FILETIME via `persona.locale` IANA → UTC offset |
| WebKit epoch | Chrome SQLite timestamps | `unix_to_webkit(event.timestamp.timestamp())` (UTC-based) |
| YYYYMMDD integer | Some registry date fields | `event.timestamp.astimezone(local_tz).strftime("%Y%m%d")` |

```python
from zoneinfo import ZoneInfo
from datetime import timezone

def to_local_filetime(utc_dt: datetime, iana_tz: str) -> int:
    local_tz = ZoneInfo(iana_tz)
    local_dt = utc_dt.astimezone(local_tz)
    # Windows hive LastWriteTime is stored as UTC even though it looks local in RegEdit
    # Actually Windows stores NTUSER.DAT LastWriteTime as UTC FILETIME — just use utc_dt
    return unix_to_filetime(utc_dt.timestamp())
```

Note: Windows registry hive `LastWriteTime` is stored as UTC FILETIME despite regional display.
The timezone conversion affects only fields that Windows explicitly expresses in local time
(e.g., `Date` values in certain Software keys, Office MRU timestamps in YYYYMMDD format).

---

## 6. `$STANDARD_INFORMATION` vs `$FILE_NAME` — when divergence is realistic

The SI/FN divergence question from ADR-009 comes up for every ARC-generated file.

**FN stamps set at create time** (kernel-only):
- `crtime` = when the MFT entry was first allocated = "ARC run time"
- `mtime`, `atime`, `ctime` = same

**SI stamps set by ARC via `setfattr system.ntfs_times`**:
- All four = the scheduler event's timestamp (a past date in the 360-day window)

Result: SI says "modified 2024-07-15", FN says "created 2026-04-21".

**When is this realistic?**

| Scenario | SI | FN |
|----------|----|----|
| File created and never touched | SI creation ≈ FN creation | Matches |
| File created long ago, recently renamed | SI mtime = old, SI crtime = old, FN crtime = old, FN mtime = rename time | Diverges on mtime |
| File synced from cloud (new local path, old content) | SI crtime = sync time, SI mtime = original | FN = sync time | Diverges on crtime vs mtime |
| **ARC-generated file** | **SI all = past** | **FN all = ARC run time** | **Diverges on all four** |

The last row is anomalous compared to real file system activity. However, it is indistinguishable
from "files that were bulk-restored from a cloud backup or migration tool", which happens
routinely on machines that have been re-imaged or migrated. ARC's file cohort looks like a
"profile migration" event — which is plausible for a machine that was set up from a backup.

To improve: a future version can generate a subset of files via the rename-roundtrip method to
update FN crtime closer to SI crtime. Deferred to v2.

---

## 7. TemporalCoherenceCheck

`evaluation/consistency_checker.py::TemporalCoherenceCheck` runs post-run and enforces:

### 7.1 Rules

```
For every APP_LAUNCH(app, t) in scheduler.events_of("APP_LAUNCH"):
  1. Security.evtx must contain event 4688 with TimeCreated within ±2 s of t
     AND NewProcessName matches app executable
  2. Prefetch/<app_hash>.pf must exist
  3. pf.last_run_times[0] must be within ±5 s of t

For every FILE_CREATE(path, creator, t) in scheduler.events_of("FILE_CREATE"):
  4. File must exist at path (verified via libguestfs or FUSE mount)
  5. SI creation time (via setfattr read-back or stat) must be within ±1 s of t

For every URL_VISIT(url, t) in scheduler.events_of("URL_VISIT"):
  6. Chrome History DB must have a visits row with visit_time within ±3 s of t

Global:
  7. $UsnJrnl record count ≥ (FILE_CREATE + FILE_MODIFY + FILE_DELETE events) × 0.9
  8. No ARC-written file has SI ctime == mtime == atime (A18) — all three equal only
     if the file was never touched after creation, which should be very rare over 360 days
  9. Security.evtx 4624/4634 count == LOGIN/LOGOFF event count in scheduler (±2 per day jitter)
 10. Prefetch file count ≥ distinct APP_LAUNCH app count × 0.95
```

### 7.2 Tolerances

Tolerances exist because:
- `setfattr` precision is 100 ns but EVTX and SQLite are millisecond-precision.
- Some services do batch-write and have up to ±1 s of internal ordering jitter.
- Chrome writes `visit_time` as WebKit epoch microseconds but rounds to full seconds in some
  SQLite schemas.

### 7.3 Output

`TemporalCoherenceCheck` produces:
- A JSONL report (`*.coherence.jsonl`) with one line per failed check.
- A summary line: `PASSED (N checks, 0 failures)` or `FAILED (N checks, K failures)`.
- Acceptance gate A9: zero failures.

---

## 8. $LogFile limitations (accepted risk R4)

$LogFile has the following limitations for ARC:

1. **Format complexity**: restart area + circular 4 KB pages with LSN records using NTFS's
   internal redo/undo operation codes. Not publicly documented enough for reliable offline writes.

2. **Triforce detection**: a Triforce-aware analyst running `LogFileParser` (Joakim Schicht's
   tool) will see USN records in `$J` without matching LSN records in `$LogFile`. This creates
   a detectable gap.

3. **Scope for ARC v1**: accept the gap. Flag in `evaluation/density_analyzer.py` as a known
   limitation. Future work: Phase v2 — implement $LogFile LSN record construction.

4. **Practical impact**: for malware sandbox analysis (ARC's primary use case), $LogFile is
   rarely parsed. The gap is only visible to DFIR examiners specifically looking for offline
   injection. Malware itself does not check $LogFile. So the realism gap is narrow.

**Action taken**: `services/ntfs/logfile_writer.py` is a stub that logs intent and returns
empty op-list, preserving the extension point for v2.

---

## 9. References

- NTFS Triforce methodology: Harlan Carvey, "Windows Registry Forensics", 3rd ed.
- Windows FILETIME conversion: MSDN `FILETIME` structure
- WebKit epoch: Chromium source `//base/time/time.h::UnixEpochToWindowsEpoch`
- Poisson scheduling model: Kingman's Theorem applied to human task arrival processes
- NTFS $LogFile format: `libfsntfs` source `libfsntfs/libfsntfs_log_record.c`
- ADR-005, ADR-009, ADR-012 — `docs/design/decisions.md`
- Risk register R4, R6, R17 — `docs/MASTER_PLAN.md` §9
