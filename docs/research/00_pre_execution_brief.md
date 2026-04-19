# ARC — Pre-Execution Design Brief

**Status**: design, pre-implementation
**Author**: refactor working set, iteration 3
**Last updated**: 2026-04-19
**Companion files**: `~/.claude/plans/typed-toasting-pelican.md` (full plan, Parts A / B / C)

> This is the single on-disk document an operator must read before running or refactoring ARC. It captures: (a) how the software is meant to be operated end-to-end, (b) how data is actually injected into a Windows 11 VHDX from a Linux host, (c) the cross-domain forensic coherence model every service must honour, (d) every known loophole / failure mode that has been identified, and (e) the authoritative list of files scheduled for deletion.
>
> The commits that rewrite ARC must not diverge from this document without first updating it.

---

## 0. Why this file exists

1. Conversation context compacts; research cannot live in chat.
2. The plan is split into three parts (A original, B iteration-2 additions, C gap audit). Together they are ~450 lines of structured markdown but they live outside the repo. This brief mirrors the operationally-relevant slice inside the repo so it is versioned with the code.
3. Several decisions (libguestfs vs ntfs-3g FUSE, SMBIOS spoofing boundary, journal-write approach) are load-bearing and need to be reviewable in PR diffs.

---

## 1. What ARC is

> **"A Python app that takes a mounted Windows 11 image and applies reproducible modifications so it no longer looks like a sterile analysis VM."**

- **Input**: a Windows 11 VHDX that has been through first-boot OOBE at least once.
- **Output**: the same VHDX, modified in-place, such that:
  - Timeline spans 360 days of realistic user activity (was 90 days — extended per user requirement).
  - Registry hives (SOFTWARE, SYSTEM, NTUSER.DAT) contain 5 000+ new keys coherent with the persona.
  - NTFS journal ($UsnJrnl:$J), $MFT timestamps ($STANDARD_INFORMATION + $FILE_NAME), Event Log (.evtx), registry MRUs, and browser SQLite histories all tell the same story with consistent timestamps.
  - VM-detection registry markers (VBox / VMware / QEMU strings) are scrubbed.
  - Host-side hypervisor configuration (SMBIOS, MAC, disk serial) is provided as reference XML the operator applies to their libvirt / QEMU VM.

ARC is **not** a VM. It does not create, boot, snapshot, or live-manage VMs. It is a one-shot offline disk rewriter plus a documentation set of hypervisor-config recipes the operator runs by hand.

---

## 2. Operator workflow (end-to-end)

Target: the operator is a malware analyst on a Linux host (Ubuntu 24.04 assumed). They want a Win11 VM that looks 360 days lived-in and is not trivially VM-detectable.

### Step A — one-time: host setup

```bash
sudo apt install libguestfs-tools libguestfs-dev python3-guestfs \
                 python3-hivex libhivex-bin ntfs-3g guestmount \
                 virtinst qemu-system-x86 libvirt-daemon-system
pip install -r requirements.txt
export GEMINI_API_KEY=...
```

### Step B — one-time per Windows SKU: build a baseline VHDX

```bash
./scripts/build_baseline_vhdx.sh \
    --iso ~/isos/Win11_23H2_x64.iso \
    --unattend ./examples/unattend.xml \
    --out ~/vms/baseline-win11-23h2.vhdx \
    --size 80G
```

`build_baseline_vhdx.sh` runs `virt-install` unattended. `unattend.xml` auto-logs in, runs a `<FirstLogonCommands>` sequence that sits idle 3 minutes (so Windows finishes building `$UsnJrnl`, registry transaction logs, Prefetch trace chains, Application.evtx, etc.), then calls `shutdown /s /t 0`. End state: a clean ~15 GB VHDX with fully-initialised NTFS and registry.

Archive this baseline. Reuse for every subsequent ARC run.

### Step C — per analyst run: inject artifacts

```bash
cp ~/vms/baseline-win11-23h2.vhdx ~/vms/analyst-run-042.vhdx
python -m arc run \
    --vhdx ~/vms/analyst-run-042.vhdx \
    --ai-generate \
    --occupation "Software Engineer" \
    --interests "gaming,open-source,photography" \
    --timeline-days 360 \
    --random-seed 4242 \
    --audit-log ~/vms/analyst-run-042.audit.jsonl
```

ARC's pipeline:

1. **Phase 0**: load / generate `PersonaContext`.
2. **Phase 1**: expand persona into `ExpansionBundle` (7 500 URL visits, 4 500 documents, 1 500 downloads, etc., rates scaled by `timeline_days`).
3. **Phase 2**: open VHDX via libguestfs (`guestfs.GuestFS`); mount filesystems; open hives via hivex.
4. **Phase 3**: `EventScheduler` produces a deterministic stream of timestamped `SyntheticEvent`s over the 360-day window respecting work hours, active weekdays, and Poisson intra-session activity.
5. **Phase 4**: run the 33 services. Every service consumes scheduler events and writes to: filesystem (files), registry (hive keys), evtx (log records), browser SQLite, NTFS metadata streams.
6. **Phase 5**: scrub VM-detection registry markers.
7. **Phase 6**: close hives cleanly; handle hive `.LOG1` / `.LOG2` transaction logs; unmount guestfs; shutdown.
8. **Phase 7**: write audit log JSONL; run `evaluation/consistency_checker.py` for the temporal-coherence gate.

### Step D — boot and analyse

```bash
virsh define ./examples/libvirt-profile-template.xml
virsh start arc-analyst-run-042
```

The libvirt template includes: SMBIOS strings matching a Dell OptiPlex, MAC with Intel OUI, disk serial, GPU emulation overrides. Sample is loaded. Observe.

---

## 3. Baseline VHDX build — why OOBE first, not after inject

The user asked why the "boot Windows once, then inject offline" approach is necessary. Short answer:

| Structure                   | Exists in fresh install ISO? | Exists after first-boot? | Can ARC write offline? |
| --------------------------- | ---------------------------- | ------------------------ | ---------------------- |
| `$MFT`                      | Yes                          | Yes, populated more      | Yes (libguestfs)       |
| `$UsnJrnl:$J` + `$Max`      | **No, zero-sized**           | Yes, initialised         | **No** (see R1)        |
| `$LogFile` (circular)       | Yes, but tiny                | Yes, expanded to ~64 MB  | Partially (risky)      |
| Hives (SOFTWARE/SYSTEM/...) | Yes, minimal                 | Yes, 100+ MB SOFTWARE    | Yes (hivex)            |
| Hive `.LOG1` / `.LOG2`      | Minimal                      | Real logs                | Must clean-close       |
| `Windows/Prefetch/*.pf`     | No                           | Yes (~10 files)          | Yes                    |
| `Windows/System32/winevt`   | Empty channels               | Real channels            | Yes, but see R3        |
| Recycle Bin `$I*` / `$R*`   | No                           | No                       | Yes                    |
| Chrome History SQLite       | No                           | No (no user yet)         | Yes                    |

ARC cannot populate `$UsnJrnl` from scratch against a never-booted image; Windows's own NTFS driver sets up the journal's sparse streams with internally-consistent headers on first mount. If we forge those headers, chkdsk on next boot nukes them. So: let Windows initialise the journal, *then* back-fill its content.

**Decision**: baseline VHDX is always post-first-boot, pre-ARC.

---

## 4. Offline injection — the actual mechanics

Linux host, no PowerShell, no Windows host dependency.

### 4.1 Registry writes — hivex

- Open: `h = hivex.Hivex("/mnt/guestfs/Windows/System32/config/SOFTWARE", write=True)`
- Walk: `h.root()` → `h.node_get_child(node, name)` → ...
- Write: `h.node_set_value(node, {"key":..., "t":REG_SZ, "value":...})`
- Commit: `h.commit(None)` — writes back to the file. **Must close cleanly** or Windows replays `.LOG1`/`.LOG2` on next boot and rolls back our changes.

**Critical**: after ARC commits, delete the corresponding `.LOG1` and `.LOG2` files. Windows rebuilds them on next mount. Failure to delete = boot loop risk if log checksum mismatches.

### 4.2 Filesystem writes — libguestfs

- `g = guestfs.GuestFS(python_return_dict=True)`
- `g.add_drive_opts(vhdx_path, format="vhdx", readonly=False)`
- `g.launch()`
- `g.inspect_os()` → pick Windows root
- `g.mount("/dev/sda2", "/")` (or equivalent)
- `g.mkdir_p(...)`, `g.write(...)`, `g.utimens(path, atsecs, atnsecs, mtsecs, mtnsecs)` for atime/mtime

`g.utimens` sets atime and mtime but **does not set ctime** ($STANDARD_INFORMATION ChangeTime) and **does not touch $FILE_NAME timestamps** at all. See §6 for the work-around.

### 4.3 NTFS $MFT timestamp patching — ntfs-3g FUSE + setfattr

guestfs's NTFS write path does not expose SI/FN timestamps. Required detour:

```bash
# After libguestfs writes files:
guestfs> umount_all
guestfs> shutdown
# Mount via ntfs-3g (FUSE) on host:
guestmount -a ./vhdx --rw -m /dev/sda2 /mnt/arc --fuse-helper="/usr/bin/ntfs-3g"
# Patch timestamps:
setfattr -n system.ntfs_times -v "0x${hex_timestamps}" /mnt/arc/Users/alex/doc.txt
# Patch $FILE_NAME via ntfsinfo / ntfstruncate hacks, see R2.
guestunmount /mnt/arc
```

**R2 risk**: `setfattr system.ntfs_times` sets only $STANDARD_INFORMATION, not $FILE_NAME. The plan assumes we accept SI-only patching (FN stays at file-create-time) because divergence between SI and FN is itself realistic for legitimately-moved files. A Triforce-aware analyst would see coherent SI-FN mismatch patterns for some subset of files — this is a feature, not a bug.

### 4.4 NTFS $UsnJrnl:$J — the hard one

**R1 decision tree** (must be resolved before writing Phase-4b code):

- **Option A — raw-stream write via guestfs**: `g.read_file("/\$Extend/\$UsnJrnl:$J")` works for sparse streams, but `g.write` on a colon-suffixed path is untested. Risk: the filename-with-colon parsing breaks inside guestfs's NTFS driver.
- **Option B — ntfs-3g raw stream via host FUSE**: `cat new_records.bin > /mnt/arc/\$Extend/\$UsnJrnl:$J` — ntfs-3g accepts the colon syntax. Must maintain `$Max` header integrity (next-USN counter).
- **Option C — skip offline, rely on Windows**: ARC touches files, next Windows boot records entries itself. Produces realistic headers, but all records cluster at boot-time. Defeats the 360-day spread.
- **Option D — third-party tool**: no mature OSS offline journal writer exists.

**Recommended**: Option B, with a small Python helper that appends `USN_RECORD_V3` records at the sparse-stream end and updates `$Max`'s `NextUsn` field. Reference: Microsoft docs for `USN_RECORD_V3` (0x50-byte header + variable filename); Brian Carrier's *File System Forensic Analysis* chapter on NTFS logs.

### 4.5 $LogFile

**R4 risk**: $LogFile is circular, internally checksummed, and replayed at mount. If we inject USN records without corresponding $LogFile LSN entries, a thorough forensics tool can detect the gap. Acceptable for Phase 1; flag as a known Triforce inconsistency in `docs/research/time_integrity.md`. Full $LogFile synthesis is deferred.

### 4.6 EVTX writes

Hand-roll binary format: 4 KB header + sequence of 64 KB chunks + records inside chunks. Each record is BinaryXML-encoded. Template IDs are per-channel and per-event (the `Microsoft-Windows-Security-Auditing` provider has its own template table).

**Shortcut**: read a reference chunk from a live Win11 Security.evtx (post-first-boot from §3), extract the template table, use that as a library. Append records with matching templates. Re-checksum the chunk (CRC32 over the 64 KB payload). Reference: `python-evtx` parser + `libevtx` C library source (we're writing, not reading, so we port BinaryXML *construction*, not parsing).

Target: Application.evtx ~15 MB, Security.evtx ~20 MB, System.evtx ~10 MB, with 30 k / 40 k / 20 k records respectively over 360 days.

### 4.7 Chrome History SQLite

Already implemented correctly (`services/browser/generators/schema.py` carries schema v46). Just needs to consume scheduler events instead of its own `Random(42)`. Target: 30 k+ `urls` rows, 80 k+ `visits` rows, 5 k+ `keyword_search_terms`, 1 k+ `downloads`.

### 4.8 Prefetch (.pf files)

Currently v30, 512-byte stubs. Rewrite to v31 (Win11 format, introduced 2024) with MAM-compressed trace chains. Target 50 files, 15–80 KB each. Reference: `libscca` C library + Eric Zimmerman's `PECmd` source (C#, readable).

---

## 5. Cross-domain forensic coherence

The user's key requirement: *"ntfs journal shows files open, files is write, file is saved, so that intervening loop should be there"*. This §5 is the authoritative model every service must implement.

### 5.1 Domains

A single user-visible action fans out to up to **7 domains** that must agree:

1. **NTFS $MFT $STANDARD_INFORMATION** — ctime, atime, mtime, ptime.
2. **NTFS $MFT $FILE_NAME** — same four timestamps at file-create (diverges only on rename/move).
3. **NTFS $UsnJrnl:$J** — USN_RECORD with reason flags.
4. **Windows Event Log** — per-channel (.evtx) records.
5. **Registry** — MRU keys, RunMRU, UserAssist counters, AppCompatCache.
6. **Browser SQLite** — urls/visits/downloads/keyword_search_terms tables.
7. **Application-specific files** — Chrome `Last Session`, Office `~$*.tmp` lockfiles, Prefetch `.pf`.

### 5.2 Canonical event types (emitted by EventScheduler)

Each produces a specific fan-out:

#### `APP_LAUNCH(app, t)`

- $MFT: touch `Prefetch/APP.EXE-XXXXXXXX.pf` mtime = t.
- $UsnJrnl: FILE_CREATE (first launch) or DATA_EXTEND (subsequent).
- Security.evtx: 4688 "process created" at t.
- Registry: UserAssist ROT13 counter bump; RecentApps MRU entry.
- If Office app: `HKCU\Software\Microsoft\Office\16.0\{App}\User MRU\LiveId_XXX\File MRU` — NOT YET, that's FILE_OPEN.
- Prefetch: `last_run_times[0] = t`, rotate old timestamps.

#### `FILE_CREATE(path, creator_app, t)`

- $MFT SI + FN: ctime = atime = mtime = t.
- $UsnJrnl: `USN_REASON_FILE_CREATE | DATA_EXTEND | CLOSE`.
- Security.evtx 4663 if SACL enabled on parent dir.
- Registry: `HKCU\...\RecentDocs\.<ext>` MRU entry for the file.
- If Office doc: Office MRU entry in `~/AppData/Roaming/Microsoft/Office/Recent/*.LNK` and in registry.
- Shell: `~/AppData/Roaming/Microsoft/Windows/Recent/*.lnk` shortcut created.

#### `FILE_MODIFY(path, t)`

- $MFT SI: mtime = t. (FN *not* updated — kernel-only.)
- $UsnJrnl: `DATA_OVERWRITE | CLOSE`.
- Parent-dir ctime bumped.

#### `FILE_DELETE(path, t)`

- $MFT: entry marked deleted (but not overwritten).
- $UsnJrnl: `FILE_DELETE | CLOSE`.
- Recycle Bin: `$I` header + `$R` copy, unless Shift-Delete.

#### `URL_VISIT(url, t)`

- Chrome History: new `urls` row (or `visit_count++`), new `visits` row with `visit_time = t`.
- Chrome Cookies: if domain matches cookie jar, `last_accessed = t`.
- Chrome Cache: new entry in `Cache/data_N` LevelDB.
- If downloaded file: synthesize FILE_CREATE of the downloaded file *AND* Chrome `downloads` row at same t.

#### `LOGIN(t)` / `LOGOFF(t)`

- Security.evtx: 4624 (logon) / 4634 (logoff).
- System.evtx: 1074 (startup reason), 6005/6006/6013.
- Registry: `HKCU\...\Explorer\UserAssist\...` touched.
- `NTUSER.DAT` LastWriteTime bumped.

### 5.3 Scheduler rules

```
EventScheduler(persona, start=install_time, end=now, rng)
  .emit_daily_pattern(weekday, work_hours)
  .emit_login_logoff_per_active_day()
  .emit_app_launches_poisson(app_rates_from_persona)
  .emit_file_operations(doc_seed_bundle, scheduler_clock)
  .emit_url_visits(browsing_seed, scheduler_clock)
  .emit_system_events(updates_kb_schedule)
  → stream of SyntheticEvent
```

Services consume the stream. No service is allowed to call `datetime.now()` or `random.Random(...)` with a fresh seed — they receive a child RNG from the scheduler.

CI gate: `grep -r "datetime.now\|datetime.utcnow\|Random(42)" services/` must return empty.

### 5.4 Coherence checker

`evaluation/consistency_checker.py::TemporalCoherenceCheck` enforces:

- For every `APP_LAUNCH(app, t)` in the scheduler's emitted list, a 4688 exists in Security.evtx within ±2 s of t, and the `.pf` `last_run_times[0]` is within ±5 s.
- For every `FILE_CREATE(path, t)`, the file exists on disk and its SI ctime is within ±1 s.
- For every `URL_VISIT(url, t)`, a matching `visits` row exists.
- `$UsnJrnl` record count matches the scheduler's file-touch count ± 10%.

---

## 6. Loopholes / known-unknowns / mitigations

Consolidated list. Every item has a tag the plan references.

| Tag   | Issue                                                                                         | Severity | Mitigation                                                                                                                                                       |
| ----- | --------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1    | libguestfs cannot cleanly write `$Extend\$UsnJrnl:$J`                                         | HIGH     | Use ntfs-3g FUSE + small Python USN_RECORD_V3 appender; update `$Max.NextUsn` header manually. Decision recorded in `docs/research/ntfs_journal.md`.             |
| R2    | guestfs `utimens` sets only $STANDARD_INFORMATION, not $FILE_NAME                             | MEDIUM   | Accept SI-only patching as a realistic signal; document the SI-FN divergence in `time_integrity.md`. Full FN patching deferred.                                  |
| R3    | Hive `.LOG1`/`.LOG2` replay on next Windows boot can roll back hivex writes                   | HIGH     | After hivex commit, delete `.LOG1` and `.LOG2`. Windows rebuilds on mount. Confirmed behaviour on Win10/11 in hivex-tools issue tracker.                         |
| R4    | `$LogFile` is circular + replayed; missing LSN entries for ARC writes are a Triforce tell     | LOW      | Accept. Phase-1 scope excludes $LogFile synthesis. Flag as known inconsistency in realism report.                                                                |
| R5    | SMBIOS / DMI / CPUID spoofing is 100% hypervisor-level; ARC cannot do it                      | MEDIUM   | Document as host-side prerequisite. Provide `examples/libvirt-profile-template.xml` with all spoofing flags. Phase-7 registry scrubbing is a *complementary* fix. |
| R6    | Deterministic reproducibility under multi-service scheduler is non-trivial                    | MEDIUM   | Scheduler owns the RNG; services receive child RNGs via `rng.spawn()` pattern. Audit log byte-identical for same seed.                                           |
| R7    | Hive companion transaction log checksum mismatch bricks next boot                             | HIGH     | (Same as R3.) Additionally, hivex `commit()` must be followed by verifying `h.close()` without error before deleting logs.                                       |
| R8    | `services/filesystem/cross_writer.py` hard-imports pywin32 — Linux migration blocker          | HIGH     | Phase-3a prerequisite: strip `import win32api / win32con / pywintypes`; replace with guestfs `set_xattr`-based NTFS attribute writes.                            |
| R9    | `build_vm_image.py` + `mount_existing_vhd.py` use `ctypes.windll.shell32` and `diskpart`      | HIGH     | Delete both (Linux migration). Replace with `scripts/build_baseline_vhdx.sh` (virt-install) + `scripts/mount_vhd.py` (guestfs) only if mount-and-hold is needed.  |
| R10   | `core/llm_client.py` (local LLM) and `services/ai/gemini_client.py` (Gemini) coexist          | LOW      | Keep both. Document the split: llm_client for artifact content, gemini_client for structured schema emission. Consolidation optional, not required.              |
| R11   | 6 pre-generated `profiles/generated/*.yaml` files use the lossy old schema                    | LOW      | Delete. User has no data tied to them.                                                                                                                           |
| R12   | `config.yaml::artifact_scale` already defines 1500/4500/7500/etc. — must not be double-coded  | LOW      | Phase 2 reads config.yaml targets; Phase 4c converts absolute totals to per-day rates.                                                                           |
| R13   | Tests (~8 000 LoC) consume old schema and `context["profile_type"]`                           | HIGH     | New Phase 9 — test migration with CI grep-gates.                                                                                                                 |
| R14   | Windows defender / SmartScreen may quarantine injected `.exe` / `.dll` artifacts on boot      | MEDIUM   | Avoid injecting executable payloads. Persona-tool launches are recorded in Prefetch / registry, not as binaries.                                                 |
| R15   | Prefetch v31 format (Win11 2024) is undocumented publicly; v30 format is public               | MEDIUM   | Use v30 initially (Win11 accepts it). Migrate to v31 once libscca adds support. `services/filesystem/prefetch.py` already uses v30.                              |
| R16   | EVTX per-provider template tables require per-provider reverse-engineering                    | HIGH     | Limit to a fixed set of 8–10 providers: Security-Auditing, Windows-Kernel-General, Application, Sysmon-Operational, PowerShell, TaskScheduler, BITS, WinRM.      |
| R17   | Time-zone drift: persona locale may imply non-UTC TZ; hives store local + UTC sometimes       | MEDIUM   | All scheduler timestamps stored as UTC; per-hive conversion done at write-time using `persona.locale` → IANA TZ mapping.                                         |
| R18   | $MFT entry reuse on file-delete means injected timestamps can collide with prior MFT entries  | LOW      | Accept collision; libguestfs allocates new MFT entries for new files; deletions are rare in scheduler output.                                                    |
| R19   | Linux host → Windows NTFS ACL semantics diverge; file ownership (SID) must be set via ntfs-3g | MEDIUM   | Use `ntfs-3g`'s `uid`/`gid` mount options + `setfattr system.ntfs_acl` to write the persona's user SID as owner of `Users/<username>/**`.                        |
| R20   | Chrome History schema version 46 may differ from the installed Chrome version in the VHDX    | LOW      | Detect Chrome version from `Application/<version>/` folder; write matching schema. `browser/generators/schema.py` must become version-aware.                     |
| R21   | Prefetch hashtable depends on exact executable path casing; a typo invalidates the filename   | MEDIUM   | Source paths from `services/filesystem/installed_apps_stub.py` — the single source of truth for installed-app paths.                                             |
| R22   | Registry hivex write invalidates Windows Registry Security Descriptors (SD) subtrees          | LOW      | hivex preserves SD blobs transparently. No action unless we write NEW keys with empty SD — then inherit parent SD explicitly.                                    |
| R23   | `persona.username` clashes with installed-hive username (hives key on SID, not string)        | MEDIUM   | PersonaLoader validates that `persona.username == C:\Users\<x>` resolvable in the baseline VHDX. If mismatch, Phase 2 renames directories and updates hives.     |
| R24   | User wants to preserve research against compaction — research docs must be **committed**      | CRITICAL | Every `docs/research/*.md` file goes into git. `docs/design/decisions.md` is the ADR log. `.gitignore` must NOT exclude `docs/`.                                 |
| R25   | $MFT record reuse / reparse points — if the baseline VHDX has symlinks/junctions, rewriting their targets silently breaks | MEDIUM | Before writing, enumerate reparse points via `guestfs.readlink` across all user directories; leave them alone. |
| R26   | hivex does not support "large values" (>1 MB) — certain keys like `BCD` or `IconStreams` exceed this | LOW | Don't write those keys. Only append keys ARC owns. Scrubbing is safe (we only delete or replace strings). |
| R27   | NTFS alternate data streams (ADS) — Zone.Identifier on downloaded files is expected by Windows | MEDIUM | For every scheduler `URL_DOWNLOAD` event, ARC writes the target file AND a `file:Zone.Identifier` ADS containing `[ZoneTransfer]\nZoneId=3` + referrer. ntfs-3g supports ADS via colon syntax. |
| R28   | If the operator doesn't delete `.LOG1`/`.LOG2`, Windows won't just roll back — it may bluescreen | HIGH | Phase 3 must WARN loudly in audit log if log-file deletion failed. Add a pre-flight check that log files are writable/deletable. |

---

## 7. VM-detection coverage matrix

Inside-guest registry surface ARC must scrub (complements host-side hypervisor config):

| Path                                                                                                | Value(s)                               | Scrub action                                                  | Current coverage                                                             |
| --------------------------------------------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `HKLM\HARDWARE\Description\System`                                                                  | SystemBiosVersion, VideoBiosVersion    | replace QEMU/Bochs/VirtualBox/VMware with realistic OEM       | **NOT covered** — add in Phase 7                                             |
| `HKLM\HARDWARE\DEVICEMAP\Scsi\Scsi Port 0\Scsi Bus 0\Target Id 0\Logical Unit Id 0\Identifier`      | "QEMU HARDDISK"/"VBOX HARDDISK"/etc.   | replace with Samsung/WD/Seagate model string                  | **NOT covered** — add in Phase 7                                             |
| `HKLM\SYSTEM\CurrentControlSet\Services\VBoxService`, VBoxSF, VBoxGuest, VBoxMouse, VBoxVideo       | entire key                             | delete                                                        | `vm_scrubber.py` covers VBoxSF + VBoxMouse; extend to Video/Guest/Service    |
| `HKLM\SYSTEM\CurrentControlSet\Services\vmtools`, vmmouse, vmci, vmhgfs, vmxnet                     | entire key                             | delete                                                        | `vm_scrubber.py` covers                                                      |
| `HKLM\SYSTEM\CurrentControlSet\Services\vioscsi`, viostor, qemu-ga, kvm*                            | entire key                             | delete                                                        | **NOT covered** — add in Phase 7                                             |
| `HKLM\SYSTEM\CurrentControlSet\Enum\ACPI`                                                           | VBOX0001/VMW0001 device IDs            | replace with generic ACPI IDs                                 | `vm_scrubber.py` covers                                                      |
| `HKLM\SYSTEM\CurrentControlSet\Control\SystemInformation`                                           | SystemManufacturer, SystemProductName  | replace with Dell/HP/Lenovo consistent with persona           | `hardware_normalizer.py` covers                                              |
| `HKLM\HARDWARE\DESCRIPTION\System\BIOS`                                                             | BIOSVendor, BIOSVersion, ReleaseDate   | replace                                                       | `hardware_normalizer.py` covers (writes even though HARDWARE is volatile)    |
| `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-*}\0000..\NetworkAddress`                    | MAC override                           | set to plausible Intel/Realtek OUI                            | **NOT covered** — add in Phase 7                                             |
| `HKLM\SOFTWARE\Oracle\VirtualBox Guest Additions`, `HKLM\SOFTWARE\VMware, Inc.\VMware Tools`        | entire key                             | delete                                                        | `vm_scrubber.py` covers                                                      |
| `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{VBox*}`, `{VMware*}`                     | entire key                             | delete                                                        | **NOT covered** — add in Phase 7                                             |
| `HKLM\SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters`                                          | values                                 | delete key                                                    | `vm_scrubber.py` covers (Hyper-V)                                            |

Host-side (documented, not executed by ARC):

| Hypervisor config location                              | Fix                                                                                             |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| libvirt `<domain>/<sysinfo>` or QEMU `-smbios type=0,1` | vendor="Dell Inc.", version="A15", manufacturer="Dell Inc.", product="OptiPlex 7090"            |
| libvirt `<interface><mac address=...>`                  | Intel OUI (`00:1b:21:...`) or Realtek (`00:e0:4c:...`); NOT VBox `08:00:27`, VMware `00:0c:29`  |
| libvirt `<disk ... serial=...>`                         | set to a plausible SSD serial, e.g. `S5GYNX0N712345Y` (Samsung pattern)                         |
| QEMU `-cpu host,+hv-*` + `-machine q35,hpet=on,...`     | expose the host CPU features; enable HPET; disable hypervisor CPUID leaf                        |
| QEMU `-device virtio-net-pci` → replace with `e1000e`   | Intel NIC model has no QEMU string                                                              |

Acceptance: `pafish`, `Al-Khaser`, `InviZzzible` flags ≤ 10 (baseline ~50+).

---

## 8. Files authoritatively scheduled for deletion

Rationale column is mandatory for every entry. If an operator disagrees with a deletion, the disagreement is settled in `docs/design/decisions.md` before the file is removed.

### 8.1 Delete

| Path                                           | LOC     | Rationale                                                                                                        |
| ---------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------- |
| `core/vm_manager.py`                           | 278     | 100% PowerShell (`Mount-DiskImage`, `Get-DiskImage`, ...). Incompatible with Linux host.                         |
| `core/profile_engine.py`                       | 246     | Source of the 6-field lossy filter (line 127). Canonical schema moves to `core/persona_context.py`.              |
| `services/ai/profile_synthesizer.py`           | 353     | Lossy YAML round-trip producing `_ai_metadata` keys that get dropped by ProfileEngine. Unneeded once unified.    |
| `services/ai/ai_orchestrator.py`               | 570     | Parallel orchestrator that never reaches the 33 filesystem services. Subsumed by `core/orchestrator.py`.         |
| `services/ai/cli.py`                           | 515     | Dev-only diagnostic CLI. `cmd_expand` moves into main pipeline.                                                  |
| `build_vm_image.py`                            | ~250    | Uses `ctypes.windll.shell32` + `diskpart`. Windows-only. Replaced by `scripts/build_baseline_vhdx.sh`.           |
| `mount_existing_vhd.py`                        | 97      | Uses `ctypes.windll.shell32` + `VMManager`. Windows-only.                                                        |
| `install_ai_deps.bat`, `install_deps.bat`, `quick_install.bat`, `run_local_model.bat` | — | Windows .bat files. Host is Linux.                                                       |
| `profiles/base.yaml`                           | —       | Old 6-field schema. Replaced by `profiles/presets/{developer,office_user,home_user}.yaml`.                       |
| `profiles/developer.yaml`                      | —       | Same.                                                                                                            |
| `profiles/office_user.yaml`                    | —       | Same.                                                                                                            |
| `profiles/home_user.yaml`                      | —       | Same.                                                                                                            |
| `profiles/generated/{alex_chen,alex_johnson,alex_mercer,arjun_mehta,jordan_chen,snehal}.yaml` | — | 6 pre-generated profiles in the lossy old shape. No user data attached.              |
| `tests/test_core/test_profile_engine.py`       | 476     | Tests the class being deleted. Replaced by `test_persona_loader.py`.                                             |
| `tests/test_core/test_vm_manager.py`           | 125     | Tests the PowerShell mounter. Replaced by `test_linux_mount.py`.                                                 |
| `tests/test_core/test_orchestrator_profile_variant.py` | 181 | Tests the `_normalize_profile_variant` bridge being deleted.                                                   |

**Total deleted**: ~3 500 LoC + 10 files + 6 YAMLs + 4 .bat.

### 8.2 Rename

| From                                    | To                              | Why                                             |
| --------------------------------------- | ------------------------------- | ----------------------------------------------- |
| `services/generators/`                  | `services/expansion/`           | Naming collision with `services/browser/generators/` (live Chromium code). |

### 8.3 Heavily rewrite (keep file, replace body)

| Path                                                                 | Rewrite scope                                                     |
| -------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `main.py`                                                            | Remove AI-branch YAML handoff; single `_build_persona()`.         |
| `core/orchestrator.py`                                               | Delete bridge; unify AI+preset; wire expansion + scheduler phases.|
| `core/mount_manager.py`                                              | Delegate to `LinuxMountBackend`.                                  |
| `services/registry/hive_writer.py`                                   | Replace hand-rolled binary writes with hivex API.                 |
| `services/filesystem/prefetch.py`                                    | v30 → v31 (or keep v30 correct); real trace chains.               |
| `services/eventlog/evtx_writer.py`                                   | Multi-chunk; BinaryXML templates; 10+ MB output.                  |
| `services/filesystem/cross_writer.py`                                | Remove pywin32; guestfs/ntfs-3g attribute writes.                 |
| `services/filesystem/document_generator.py`                          | Consume expansion bundle.                                         |
| `services/browser/history.py`, `downloads.py`, `bookmarks.py`        | Consume scheduler events; stop `Random(42)`.                      |
| `arc_wizard.py`                                                      | Linux flow; no Z: drive.                                          |
| `verify_realism.py`                                                  | 90 → 360 days; add `TemporalCoherenceCheck` hook.                 |

### 8.4 Create

| Path                                                                                          | Purpose                                              |
| --------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `core/persona_context.py`                                                                     | Canonical schema.                                    |
| `core/persona_loader.py`                                                                      | Replaces ProfileEngine.                              |
| `core/service_context.py`                                                                     | Typed `ServiceContext` dataclass.                    |
| `core/linux_mount.py`                                                                         | libguestfs + hivex backend.                          |
| `core/event_scheduler.py`                                                                     | Cross-domain event stream.                           |
| `services/expansion/__init__.py` (rename from generators/)                                    | Expansion facade.                                    |
| `services/ntfs/mft_timestamp_patcher.py`                                                      | SI timestamp patching.                               |
| `services/ntfs/usn_journal_writer.py`                                                         | `$UsnJrnl:$J` appender.                              |
| `services/ntfs/logfile_writer.py` (stub)                                                      | `$LogFile` best-effort.                              |
| `services/ai/seed_generators/media.py`                                                        | Missing media seed source.                           |
| `services/ai/seed_generators/registry.py`                                                     | LLM → RegistrySeed list.                             |
| `services/ai/seed_generators/evtx.py`                                                         | LLM → EvtxSeed list.                                 |
| `services/ai/seed_generators/prefetch.py`                                                     | LLM → PrefetchAppSeed list.                          |
| `services/filesystem/office_mru.py`                                                           | Office `~/AppData/Roaming/Microsoft/Office/Recent/`. |
| `services/filesystem/powershell_history.py`                                                   | `ConsoleHost_history.txt`.                           |
| `services/filesystem/cdp_logs.py`                                                             | ConnectedDevicesPlatform logs.                       |
| `services/anti_fingerprint/mac_hygiene.py`                                                    | NetworkAddress override writes.                      |
| `profiles/presets/{developer,office_user,home_user}.yaml`                                     | PersonaContext-shaped presets.                       |
| `docs/research/{ntfs_journal,vm_detection_evasion,mount_strategy,time_integrity,windows_artifact_baselines}.md` | Research archive.                          |
| `docs/design/decisions.md`                                                                    | ADR log.                                             |
| `scripts/build_baseline_vhdx.sh`                                                              | virt-install automation.                             |
| `examples/unattend.xml`                                                                       | Silent-install answer file.                          |
| `examples/libvirt-profile-template.xml`                                                       | SMBIOS/MAC/disk-serial spoofing.                     |
| `services/ai/prompts/{registry_artifacts,evtx_events,prefetch_apps}.txt`                      | New LLM prompts.                                     |

### 8.5 Keep, minor touches

| Path                                                | Why keep                                            |
| --------------------------------------------------- | --------------------------------------------------- |
| `core/timestamp_service.py` (344 L)                 | Portable; used by every service.                    |
| `core/audit_logger.py` (45 L)                       | Portable structured logging.                        |
| `core/llm_client.py` (552 L)                        | Local-LLM client for artifact content (R10).        |
| `services/ai/gemini_client.py` (808 L)              | Google Gemini for persona/seed generation (R10).    |
| `services/ai/persona_generator.py` (277 L)          | Generates PersonaContext; wire to new schema.       |
| `services/browser/generators/*` (488 L)             | Chromium SQLite infrastructure; add scheduler hook. |
| `services/anti_fingerprint/vm_scrubber.py` (398 L)  | Extend, don't replace.                              |
| `services/anti_fingerprint/hardware_normalizer.py` (360 L) | Extend with SystemBiosVersion/VideoBiosVersion. |
| `services/anti_fingerprint/process_faker.py` (439 L) | Consume PersonaContext for Run keys.               |
| `services/registry/{userassist,mru_recentdocs,network_profiles,system_identity,installed_programs}.py` | 5 domain services; preserve API. |
| `services/filesystem/installed_apps_stub.py` (499 L) | Source-of-truth for app paths; referenced by Prefetch + installed_programs. |
| `services/filesystem/system_content_populator.py` (956 L) | Audit; likely keep and consume expansion bundle. |
| `data/hardware_models.json`, `kb_updates.json`, `wordlists/*` | Vocabulary + update-history data.       |
| `templates/{browser,documents,registry}/*`          | Template inputs.                                    |
| `team.md`                                           | User confirmed this is the only accurate existing doc. |

---

## 9. Linux host dependency list

```bash
# System packages
apt install \
    libguestfs-tools libguestfs-dev python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g ntfs-3g-dev \
    fuse3 \
    guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit  # for fls, istat, fsstat in verification

# Python
pip install -r requirements.txt
# requirements.txt drops: pywin32
# requirements.txt adds: python-docx, openpyxl, reportlab (already there), 
#                        python-evtx (read for templates, we write), 
#                        python-registry (cross-validation against hivex writes)
```

For inside-VM validation only (optional):
```bash
# On a Windows 11 test VM:
pafish.exe, Al-Khaser.exe, InviZzzible.exe
MFTECmd.exe, PECmd.exe, RECmd.exe, EvtxECmd.exe (Eric Zimmerman's tools)
```

---

## 10. Revised execution order

```
Phase 0  — Research docs + ADR log (this file's companions)
Phase 1  — PersonaContext schema + ServiceContext + migrate 33 services
Phase 3a — pywin32 removal from cross_writer.py (Phase 3 blocker)
Phase 3  — LinuxMountBackend (guestfs + hivex) + log-file handling
Phase 2  — Expansion pipeline wired; config.yaml rate conversion
Phase 4c — timeline_days 90 → 360 (schema + all service call sites)
Phase 4a — EventScheduler + scheduler migration of every service's datetime/random usage
Phase 4  — Density targets per service, scheduler-aware
Phase 4b — NTFS $MFT SI patching + $UsnJrnl:$J appender (R1, R2 decisions)
Phase 7  — Extend vm_scrubber + hardware_normalizer; add mac_hygiene
Phase 8  — build_baseline_vhdx.sh + unattend.xml + libvirt template
Phase 5  — Rewrite AI prompts; add media/registry/evtx/prefetch seed generators
Phase 9  — Test migration + CI grep-gates
Phase 6  — Doc rewrite (all of docs/*.md except team.md; report.md; latex.tex)
```

---

## 11. Acceptance matrix

Rows are gates; every row must be green before calling the refactor done.

| #   | Gate                                                                                                           | Phase       |
| --- | -------------------------------------------------------------------------------------------------------------- | ----------- |
| A1  | `grep -r "datetime.now\|datetime.utcnow\|Random(42)" services/` returns zero lines                              | Phase 4a    |
| A2  | `grep -r "context\[.profile_type.\]\|context\[.installed_apps.\]\|context\[.locale.\]" services/` returns zero | Phase 1     |
| A3  | `grep -r "import win32\|from win32" .` returns zero (except explicit Windows-only test utilities)               | Phase 3a    |
| A4  | `grep -r "powershell\|Mount-DiskImage\|Get-DiskImage" core/ services/` returns zero                             | Phase 3     |
| A5  | All tests pass on Linux: `pytest -x`                                                                            | Phase 9     |
| A6  | `fsutil usn readjournal C:` inside booted modified VHDX returns ≥ 500 k records spanning ≥ 300 days            | Phase 4b    |
| A7  | `MFTECmd.exe` on modified VHDX shows matching SI/FN create times for ARC-generated files (SI-only patch ok)    | Phase 4b    |
| A8  | `pafish` / `Al-Khaser` flags ≤ 10 (baseline unmodified VBox ~50+)                                              | Phase 7 + 8 |
| A9  | `evaluation/consistency_checker.py::TemporalCoherenceCheck` passes                                             | Phase 4a    |
| A10 | Registry: ≥ 5 000 new keys across SOFTWARE + NTUSER + SYSTEM, verified via hivex count                          | Phase 4     |
| A11 | Prefetch: ≥ 30 `.pf` files, mean size ≥ 15 KB                                                                   | Phase 4     |
| A12 | EVTX: per primary log, ≥ 10 MB, ≥ 5 000 records (Application, Security, System)                                | Phase 4     |
| A13 | Browser History: ≥ 5 000 `urls`, ≥ 10 000 `visits` rows in Chrome DB                                            | Phase 4     |
| A14 | Documents: ≥ 500 files under `Users/<user>/Documents/**` that open in Word/Excel without errors                 | Phase 4     |
| A15 | VHDX delta (modified − baseline): ≥ 500 MB added                                                                | Phase 4     |
| A16 | Two ARC runs with same `--random-seed` produce byte-identical audit logs                                        | Phase 4a    |
| A17 | `docs/research/*.md` and `docs/design/decisions.md` exist and are committed                                     | Phase 0     |
| A18 | No ARC-written file has `ctime == mtime == atime` by default                                                    | Phase 4a    |
| A19 | Hive `.LOG1`/`.LOG2` deletion verified post-write; next Windows boot does not show "registry rolled back"       | Phase 3     |
| A20 | Host-side libvirt template (`examples/libvirt-profile-template.xml`) boots successfully via `virsh start`       | Phase 8     |

---

## 12. Open questions needing user input

Before Phase 1 lands:

- **Q1 (R1)**: confirm Option B (ntfs-3g FUSE + Python USN appender). Alternative: skip offline journal synthesis for v1, document as "first boot rebuilds journal". Impact: A6 gate would become "after first boot", not "after ARC run".
- **Q2 (R5)**: is it acceptable that SMBIOS/MAC/disk-serial spoofing is delivered as libvirt XML + documentation, not as ARC code? If the operator uses VirtualBox instead of libvirt, we need a parallel `examples/virtualbox.vbox-snippet.xml`.
- **Q3 (Phase 5 scope)**: are LLM prompts for registry/evtx/prefetch in scope for the initial cut, or is Phase 5 deferred to v2? The current plan includes them, but schema-accurate prompts are the slowest single item.
- **Q4 (Q-system)**: want a quickstart `docs/getting_started.md` in addition to `START_HERE.md` / `WIZARD_QUICKSTART.md`, or should we consolidate to a single operator guide?
- **Q5 (determinism)**: is byte-identical reproducibility (A16) mandatory or nice-to-have? It constrains scheduler design significantly.
- **Q6 (v1 scope)**: is it ok to ship Phase 1-4 (schema + Linux backend + expansion + density) as the first release, with Phase 4b (NTFS journal) + Phase 7 (VM-detection) as v1.1? Or must they all ship together?

---

## 13. Quick reference — where each concern lives

- **"How do I run it?"** → §2.
- **"How do I build the baseline VHDX?"** → §3 + §2 Step B; detailed recipe goes to `docs/research/mount_strategy.md`.
- **"How does ARC inject without booting Windows?"** → §4.
- **"How does time stay coherent across artifacts?"** → §5; detailed rules go to `docs/research/time_integrity.md`.
- **"What breaks / what are the risks?"** → §6.
- **"What about VM detection?"** → §7; detailed coverage goes to `docs/research/vm_detection_evasion.md`.
- **"What files are being deleted?"** → §8.
- **"What dependencies do I install?"** → §9.
- **"What's the order of work?"** → §10.
- **"When is it done?"** → §11.
- **"What still needs deciding?"** → §12.
