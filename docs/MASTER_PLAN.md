# ARC — Master Refactor & Implementation Plan

**Authoritative plan for the ARC rescue refactor. Self-contained.**

> A fresh Claude session (or any engineer) picking up this project should read this single file top-to-bottom before touching any code. It contains every decision, every file disposition, every phase, every risk. If this file contradicts any other doc, **this file wins** and the other doc is updated.

**Companion**: `docs/research/00_pre_execution_brief.md` (operator-facing walkthrough of the same material).

**Versioning rule**: every architectural change in this doc must come with an ADR entry in `docs/design/decisions.md`.

---

## 0. How to use this plan

1. Read §1–§4 first to understand *what* and *why*.
2. Read §5–§9 for the environment, file list, and the coherence/detection/risk models — these are reference material you will return to during every phase.
3. Execute phases in the order given in §10. Each phase is self-contained: goal, inputs, steps, tests, CI gates, acceptance.
4. Do not skip Phase 0 — it produces the committed research docs that prevent knowledge loss across context resets.
5. Match §11's acceptance matrix to mark the refactor done.
6. Resolve §12's open questions with the user before starting Phase 1.

---

## 1. Executive summary

**ARC** = *Artifact Reality Composer*. Given a Windows 11 VHDX, ARC rewrites it so it no longer looks like a sterile analysis VM — adding 360 days of coherent user activity across registry, filesystem, NTFS journal, event logs, browser history, and VM-detection-marker scrubbing.

**Current state** is broken in six independently critical ways:

1. **Dual profile schema**. A 6-field `ProfileContext` (old) and a 25-field AI-generated `PersonaContext` (new) coexist. The AI metadata is dropped by a silent filter at `core/profile_engine.py:127`. The 33 artifact services receive only the stripped-down old schema.
2. **Grep bridge**. `core/orchestrator.py:278 _normalize_profile_variant()` funnels every AI persona into 3 hard-coded preset names.
3. **Dead bulk expansion**. `services/generators/bulk_*.py` (1 445 LoC) is imported only by a dev CLI and tests. Never runs in the main pipeline.
4. **Windows-only mount**. `core/vm_manager.py` is 100 % PowerShell; host is now Linux.
5. **Stub artifacts**. Prefetch files 512 B, registry hives 8 KB, evtx 69 KB, docs 8 per user. VHDX delta ~70 MB.
6. **All files share the same timestamp**. No cross-domain temporal coherence; no NTFS $UsnJrnl synthesis; no SI/FN divergence.

**Target**: Linux-host, unified schema, wired expansion, real-density artifacts, coherent cross-domain timeline over 360 days, VM-detection keys scrubbed.

---

## 2. Current state — what's broken (with evidence)

| Symptom                                  | File / line                                       | Evidence                                                                 |
| ---------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------ |
| AI metadata silently dropped             | `core/profile_engine.py:127`                      | `filtered = {k: v for k, v in resolved.items() if k in allowed_fields}`  |
| Persona funneled to 3 names              | `core/orchestrator.py:278`                        | `_normalize_profile_variant()` alias map                                 |
| Bulk expansion orphaned                  | `services/generators/__init__.py`                 | Only referenced by `services/ai/cli.py` + `tests/test_ai_generation.py`  |
| PowerShell host dependency               | `core/vm_manager.py`                              | `subprocess.run(["powershell", ...])` everywhere                         |
| Prefetch 512 B stub                      | `services/filesystem/prefetch.py:243-291`         | Writes 84 + 128 + 104 = 316 B, pads to 512                               |
| 8 KB hives                               | `core/orchestrator.py:46-128 _create_minimal_hive()` | 4 KB base + 4 KB bin                                                  |
| Shared timestamps                        | every service that calls `datetime.now()`         | No central scheduler; each service rolls its own time                   |
| pywin32 in filesystem writer             | `services/filesystem/cross_writer.py:16-24`       | `import win32api; import win32con; import pywintypes`                    |
| Windows-only helper scripts              | `build_vm_image.py`, `mount_existing_vhd.py`      | `ctypes.windll.shell32`, `diskpart`                                      |
| Fantasy density baselines                | `evaluation/density_analyzer.py:36-53`            | registry=150, filesystem=80, browser=30 (real: 50 000 / 10 000 / 500)    |
| Lossy YAML synthesis                     | `services/ai/profile_synthesizer.py`              | Writes `_ai_metadata` keys that get dropped                              |

---

## 3. Target architecture

```
Linux host (Ubuntu 24.04+)
│
├── core/
│   ├── persona_context.py     (NEW — canonical 25+ field Pydantic schema)
│   ├── persona_loader.py      (NEW — replaces ProfileEngine)
│   ├── service_context.py     (NEW — typed context dataclass passed to services)
│   ├── event_scheduler.py     (NEW — cross-domain event stream)
│   ├── linux_mount.py         (NEW — libguestfs + hivex + ntfs-3g backend)
│   ├── mount_manager.py       (rewritten — delegates to linux_mount)
│   ├── orchestrator.py        (rewritten — unified persona → seeds → scheduler → services)
│   ├── timestamp_service.py   (kept — portable)
│   ├── audit_logger.py        (kept — portable)
│   ├── identity_generator.py  (kept — _VM_STRINGS authority)
│   ├── llm_client.py          (kept — local LLM for content bodies)
│
├── services/
│   ├── ai/
│   │   ├── persona_generator.py    (rewritten — emits PersonaContext directly)
│   │   ├── gemini_client.py        (kept — Google Gemini)
│   │   ├── schemas.py              (slimmed — seed schemas only; PersonaContext moves to core/)
│   │   ├── seed_generators/
│   │   │   ├── {downloads,documents,browsing,filenames}.py  (kept)
│   │   │   ├── media.py            (NEW — was missing)
│   │   │   ├── registry.py         (NEW — RegistrySeed)
│   │   │   ├── evtx.py             (NEW — EvtxSeed)
│   │   │   └── prefetch.py         (NEW — PrefetchAppSeed)
│   │   └── prompts/*.txt           (rewritten — Windows-artifact-specific)
│   │
│   ├── expansion/                  (RENAMED from services/generators/)
│   │   └── bulk_*.py               (wired into orchestrator; rate-driven)
│   │
│   ├── ntfs/                       (NEW)
│   │   ├── mft_timestamp_patcher.py
│   │   ├── usn_journal_writer.py
│   │   └── logfile_writer.py
│   │
│   ├── anti_fingerprint/
│   │   ├── vm_scrubber.py          (extended)
│   │   ├── hardware_normalizer.py  (extended)
│   │   ├── mac_hygiene.py          (NEW)
│   │   └── process_faker.py        (kept; reads PersonaContext)
│   │
│   ├── registry/*.py               (kept — 5 domain services)
│   ├── browser/{history,downloads,bookmarks,browser_profile,cookies_cache}.py  (scheduler-aware)
│   ├── browser/generators/*.py     (kept — Chromium SQLite infrastructure)
│   ├── filesystem/
│   │   ├── prefetch.py             (rewritten — v30/v31 real)
│   │   ├── cross_writer.py         (rewritten — no pywin32)
│   │   ├── document_generator.py   (expansion-bundle consumer)
│   │   ├── office_mru.py           (NEW)
│   │   ├── powershell_history.py   (NEW)
│   │   ├── cdp_logs.py             (NEW)
│   │   └── (rest kept, scheduler-aware)
│   ├── eventlog/
│   │   ├── evtx_writer.py          (rewritten — multi-chunk, BinaryXML templates)
│   │   └── {application,security,system,update}_log.py (scheduler-aware)
│   └── applications/*.py           (scheduler-aware)
│
├── profiles/presets/
│   └── {developer,office_user,home_user}.yaml  (NEW — PersonaContext-shaped)
│
├── scripts/
│   └── build_baseline_vhdx.sh      (NEW — virt-install automation)
│
├── examples/
│   ├── unattend.xml                (NEW — silent-install answer file)
│   └── libvirt-profile-template.xml (NEW — SMBIOS/MAC spoof reference)
│
├── docs/
│   ├── MASTER_PLAN.md              (this file)
│   ├── research/
│   │   ├── 00_pre_execution_brief.md
│   │   ├── mount_strategy.md
│   │   ├── ntfs_journal.md
│   │   ├── time_integrity.md
│   │   ├── vm_detection_evasion.md
│   │   └── windows_artifact_baselines.md
│   └── design/
│       └── decisions.md            (ADR log)
│
└── [deleted]:
    core/vm_manager.py
    core/profile_engine.py
    services/ai/profile_synthesizer.py
    services/ai/ai_orchestrator.py
    services/ai/cli.py
    build_vm_image.py
    mount_existing_vhd.py
    profiles/{base,developer,office_user,home_user}.yaml
    profiles/generated/*.yaml
    install_*.bat, quick_install.bat, run_local_model.bat
```

---

## 4. Decisions (foundation — do not re-litigate)

| ID      | Decision                                                                                    | Recorded in           |
| ------- | ------------------------------------------------------------------------------------------- | --------------------- |
| ADR-001 | Unify on `PersonaContext` (25 fields). Delete `ProfileContext`.                             | §6, Phase 1           |
| ADR-002 | Linux host only. libguestfs + hivex + ntfs-3g. Delete PowerShell mounter.                   | §5, §6, Phase 3       |
| ADR-003 | Baseline VHDX = post-first-boot (OOBE complete). ARC is offline-inject only.                | §7.3, Phase 8         |
| ADR-004 | Default timeline = 360 days (was 90). Validated 30 ≤ timeline ≤ 730.                        | Phase 4c              |
| ADR-005 | Single `EventScheduler`; no service calls `datetime.now()` / `Random(N)` directly.          | §7, Phase 4a          |
| ADR-006 | `ServiceContext` dataclass replaces `context: dict` in `BaseService.apply()`.               | Phase 1               |
| ADR-007 | `core/llm_client.py` (local LLM, artifact content) and `services/ai/gemini_client.py` (Gemini, structured seeds) coexist intentionally. | §6  |
| ADR-008 | NTFS $UsnJrnl:$J writes use ntfs-3g FUSE + USN_RECORD_V3 Python appender (Option B for R1). | §7.4, Phase 4b        |
| ADR-009 | NTFS $MFT $STANDARD_INFORMATION patched via `setfattr system.ntfs_times`; $FILE_NAME left at create-time (SI-FN divergence is realistic). | §7.4, R2  |
| ADR-010 | Hive `.LOG1` / `.LOG2` deleted after hivex commit; Windows rebuilds on next mount.          | R3, R7, R28, Phase 3  |
| ADR-011 | SMBIOS / MAC / disk-serial spoofing is 100 % hypervisor-config; ARC only scrubs registry reflection. | §8, R5, Phase 7   |
| ADR-012 | Determinism is mandatory: same `--random-seed` ⇒ byte-identical audit log.                  | A16, Phase 4a         |
| ADR-013 | `config.yaml::artifact_scale` becomes per-day rates × `timeline_days`, not absolute totals. | R12, Phase 2 + 4c     |
| ADR-014 | `services/generators/` renamed to `services/expansion/` to avoid collision with `services/browser/generators/`. | Phase 2   |
| ADR-015 | Tests migrated in their own phase with CI grep-gates; not a "rerun" afterthought.           | Phase 9               |
| ADR-016 | Every research doc goes into git under `docs/research/`; `.gitignore` must not exclude `docs/`. | R24, Phase 0     |

---

## 5. Dependencies & environment

### 5.1 Linux host

```bash
apt install -y \
    libguestfs-tools libguestfs-dev python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit
```

### 5.2 Python (`requirements.txt`)

**Drop**: `pywin32`.
**Add**: `guestfs` (provided by system package), `hivex` (system package), `python-evtx` (read-only, for template extraction), `python-docx`, `openpyxl`, `reportlab` (already present).

### 5.3 Inside-VM validation tools (optional, for acceptance only)

`pafish`, `Al-Khaser`, `InviZzzible`, Eric Zimmerman's `MFTECmd` / `PECmd` / `RECmd` / `EvtxECmd`.

### 5.4 Environment variables

```bash
export GEMINI_API_KEY=...          # Phase 0 optional; presets work without
export LIBGUESTFS_BACKEND=direct    # faster than default libvirt backend
```

---

## 6. File disposition (authoritative)

### 6.1 Delete

| Path                                           | Reason                                                                                                     |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `core/vm_manager.py` (278 LoC)                 | PowerShell mounter; Linux incompatible (ADR-002).                                                          |
| `core/profile_engine.py` (246 LoC)             | 6-field ProfileContext + lossy filter (source of bug #1).                                                  |
| `services/ai/profile_synthesizer.py` (353 LoC) | Lossy YAML round-trip.                                                                                     |
| `services/ai/ai_orchestrator.py` (570 LoC)     | Parallel orchestrator; subsumed by unified `core/orchestrator.py`.                                         |
| `services/ai/cli.py` (515 LoC)                 | Dev-only diagnostic CLI.                                                                                   |
| `build_vm_image.py` (~250 LoC)                 | `ctypes.windll.shell32` + `diskpart`.                                                                      |
| `mount_existing_vhd.py` (97 LoC)               | `ctypes.windll.shell32` + uses VMManager.                                                                  |
| `install_ai_deps.bat`, `install_deps.bat`, `quick_install.bat`, `run_local_model.bat` | Windows .bat files.                                                 |
| `profiles/base.yaml`                           | Old schema.                                                                                                |
| `profiles/developer.yaml`                      | Old schema.                                                                                                |
| `profiles/office_user.yaml`                    | Old schema.                                                                                                |
| `profiles/home_user.yaml`                      | Old schema.                                                                                                |
| `profiles/generated/*.yaml` (6 files)          | AI output in old lossy shape.                                                                              |
| `tests/test_core/test_profile_engine.py` (476 LoC) | Tests deleted class.                                                                                   |
| `tests/test_core/test_vm_manager.py` (125 LoC) | Tests deleted class.                                                                                       |
| `tests/test_core/test_orchestrator_profile_variant.py` (181 LoC) | Tests deleted bridge.                                                                    |

### 6.2 Rename

| From                       | To                         |
| -------------------------- | -------------------------- |
| `services/generators/`     | `services/expansion/`      |

### 6.3 Heavily rewrite

| Path                                               | Scope                                                                                              |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `main.py`                                          | Remove AI-branch YAML handoff; single `_build_persona()` function.                                 |
| `core/orchestrator.py`                             | Delete bridge; unify AI + preset paths; wire expansion + scheduler phases.                         |
| `core/mount_manager.py`                            | Delegate to `LinuxMountBackend`.                                                                   |
| `services/registry/hive_writer.py` (956 LoC)       | Replace hand-rolled binary writes with hivex API; preserve `HiveOperation` / `HiveWriter` facade.  |
| `services/filesystem/prefetch.py` (291 LoC)        | Real v30/v31 format; 15–80 KB per file; consumes PrefetchAppSeed.                                  |
| `services/eventlog/evtx_writer.py` (603 LoC)       | Multi-chunk (64 KB each); reference templates extracted from a real Win11 Security.evtx.           |
| `services/filesystem/cross_writer.py` (279 LoC)    | Strip pywin32; guestfs/ntfs-3g attribute writes.                                                   |
| `services/filesystem/document_generator.py` (673 LoC) | Consume expansion bundle.                                                                       |
| `services/browser/{history,downloads,bookmarks}.py` | Consume scheduler events; stop `Random(42)`.                                                      |
| `arc_wizard.py` (25 KB)                            | Linux flow; no Z: drive; libguestfs paths.                                                         |
| `verify_realism.py` (21 KB)                        | 90 → 360 days; add `TemporalCoherenceCheck` hooks; cross-domain consistency checks.                |
| `evaluation/density_analyzer.py`                   | Real baselines (registry 50 000; filesystem 10 000; browser 500).                                  |

### 6.4 Extend (existing, add coverage)

| Path                                                 | New coverage                                                                                                  |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `services/anti_fingerprint/vm_scrubber.py` (398 LoC) | Add `vioscsi`, `viostor`, `qemu-ga`, kvm*, `VBoxVideo`, `VBoxGuest`, Uninstall-key entries.                   |
| `services/anti_fingerprint/hardware_normalizer.py` (360 LoC) | `HKLM\HARDWARE\Description\System` `SystemBiosVersion`, `VideoBiosVersion`; SCSI Identifier path.    |
| `core/identity_generator.py::_VM_STRINGS`            | Extend to include qemu-ga, kvm strings.                                                                       |

### 6.5 Create

| Path                                                                   | Purpose                                            |
| ---------------------------------------------------------------------- | -------------------------------------------------- |
| `core/persona_context.py`                                              | Canonical schema (migrated from `services/ai/schemas.py::PersonaContext`). |
| `core/persona_loader.py`                                               | YAML → PersonaContext; replaces ProfileEngine.     |
| `core/service_context.py`                                              | Typed `ServiceContext` dataclass.                  |
| `core/linux_mount.py`                                                  | libguestfs + hivex + ntfs-3g backend.              |
| `core/event_scheduler.py`                                              | Cross-domain event stream.                         |
| `services/expansion/__init__.py`                                       | Facade (after rename).                             |
| `services/ntfs/mft_timestamp_patcher.py`                               | SI patching via ntfs-3g.                           |
| `services/ntfs/usn_journal_writer.py`                                  | `\$Extend\$UsnJrnl:$J` appender.                   |
| `services/ntfs/logfile_writer.py`                                      | $LogFile best-effort stub.                         |
| `services/ai/seed_generators/media.py`                                 | Missing media seed.                                |
| `services/ai/seed_generators/registry.py`                              | RegistrySeed.                                      |
| `services/ai/seed_generators/evtx.py`                                  | EvtxSeed.                                          |
| `services/ai/seed_generators/prefetch.py`                              | PrefetchAppSeed.                                   |
| `services/filesystem/office_mru.py`                                    | Office Recent MRU.                                 |
| `services/filesystem/powershell_history.py`                            | ConsoleHost_history.txt.                           |
| `services/filesystem/cdp_logs.py`                                      | ConnectedDevicesPlatform logs.                     |
| `services/anti_fingerprint/mac_hygiene.py`                             | NIC NetworkAddress override.                       |
| `profiles/presets/developer.yaml`                                      | PersonaContext preset.                             |
| `profiles/presets/office_user.yaml`                                    | PersonaContext preset.                             |
| `profiles/presets/home_user.yaml`                                      | PersonaContext preset.                             |
| `scripts/build_baseline_vhdx.sh`                                       | virt-install automation.                           |
| `examples/unattend.xml`                                                | Silent-install answer file.                        |
| `examples/libvirt-profile-template.xml`                                | SMBIOS/MAC/disk-serial reference.                  |
| `docs/research/{mount_strategy,ntfs_journal,time_integrity,vm_detection_evasion,windows_artifact_baselines}.md` | Research archive. |
| `docs/design/decisions.md`                                             | ADR log.                                           |
| `services/ai/prompts/{registry_artifacts,evtx_events,prefetch_apps}.txt` | New LLM prompts.                                 |

### 6.6 Keep unchanged (or with trivial touches)

`core/timestamp_service.py`, `core/audit_logger.py`, `core/llm_client.py`, `services/ai/gemini_client.py`, `services/ai/persona_generator.py` (minor: emit PersonaContext directly), `services/browser/generators/*.py`, `services/registry/{userassist,mru_recentdocs,network_profiles,system_identity,installed_programs}.py`, `services/filesystem/installed_apps_stub.py`, `services/filesystem/system_content_populator.py` (after scheduler retrofit), `services/anti_fingerprint/process_faker.py` (after PersonaContext retrofit), `data/*`, `templates/*`, `team.md`, `config.yaml` (small edits — §10.4), `services/base_service.py` (small edit — §10.1).

---

## 7. Cross-domain forensic coherence model

### 7.1 Why this matters

A malware analyst running NTFS Triforce ($MFT + $LogFile + $UsnJrnl cross-validation) notices instantly when a file's $UsnJrnl record says it was created at T1 but $MFT SI says T2. ARC must produce coherent timelines or be trivially detected as synthetic.

### 7.2 Domains

A single user-visible action fans out to up to **7 domains**:

1. **NTFS $MFT $STANDARD_INFORMATION** (SI): ctime, atime, mtime, ptime. User-settable.
2. **NTFS $MFT $FILE_NAME** (FN): same four timestamps. Kernel-only; set at file-create, changed only on rename.
3. **NTFS $UsnJrnl:$J**: USN_RECORD with reason flags.
4. **Windows Event Log** (.evtx): per-channel, per-provider records.
5. **Registry**: MRU keys, RunMRU, UserAssist, AppCompatCache, Amcache.
6. **Browser SQLite**: urls / visits / downloads / keyword_search_terms tables (Chrome History).
7. **Application-specific files**: Office ~$tmp lockfiles, Prefetch .pf, Chrome Last Session, Recent *.lnk.

### 7.3 Canonical event types and fan-out

`EventScheduler` emits `SyntheticEvent(kind, timestamp, payload)`. Services consume by `kind`.

```
APP_LAUNCH(app, t)
  → Prefetch: last_run_times[0] = t
  → $MFT SI: touch Prefetch/APP.EXE-XXXXXXXX.pf mtime = t
  → $UsnJrnl: FILE_CREATE (first launch) | DATA_EXTEND (subsequent)
  → Security.evtx: 4688 "process created" at t
  → Registry: UserAssist ROT13 counter bump; RecentApps MRU

FILE_CREATE(path, creator_app, t)
  → $MFT SI + FN: ctime = atime = mtime = t (FN only at creation)
  → $UsnJrnl: USN_REASON_FILE_CREATE | DATA_EXTEND | CLOSE
  → Security.evtx 4663 (if SACL)
  → Registry: HKCU\...\RecentDocs\.<ext> MRU
  → Shell: ~/AppData/Roaming/Microsoft/Windows/Recent/<file>.lnk
  → Office: HKCU\Software\Microsoft\Office\16.0\{App}\User MRU (if Office)
  → File: Zone.Identifier ADS if downloaded

FILE_MODIFY(path, t)
  → $MFT SI: mtime = t  (FN unchanged — kernel-only)
  → $UsnJrnl: DATA_OVERWRITE | CLOSE
  → Parent dir: ctime bumped

FILE_DELETE(path, t)
  → $MFT: entry marked deleted (not overwritten)
  → $UsnJrnl: FILE_DELETE | CLOSE
  → Recycle Bin: $I header + $R copy (unless Shift-Delete)

URL_VISIT(url, t, with_download=False)
  → Chrome History: urls.last_visit_time = t; new visits row
  → Chrome Cookies: last_accessed = t (if domain match)
  → Chrome Cache: LevelDB entry
  → If with_download: FILE_CREATE of download + Chrome downloads row + Zone.Identifier ADS

LOGIN(t) / LOGOFF(t)
  → Security.evtx: 4624 / 4634
  → System.evtx: 6005/6006/6013/1074
  → Registry: NTUSER.DAT LastWriteTime
  → Explorer UserAssist touched
```

### 7.4 Scheduler contract

```python
# core/event_scheduler.py
class EventScheduler:
    def __init__(self, persona: PersonaContext, install_time: datetime,
                 now: datetime, rng: random.Random): ...

    def emit(self) -> Iterable[SyntheticEvent]:
        """
        Deterministic stream of events respecting:
          - persona.active_days (ISO weekdays)
          - persona.work_hours_start/end (UTC conversion via persona.locale)
          - Poisson intra-session activity
          - Long gaps: weekends, holidays (locale-aware), vacations
          - Sleep / off-hours silence
        """

    def child_rng(self, name: str) -> random.Random:
        """Deterministic child RNG per service consumer."""
```

### 7.5 Coherence checker

`evaluation/consistency_checker.py::TemporalCoherenceCheck` validates post-run:

- For every `APP_LAUNCH(app, t)`: matching 4688 in Security.evtx ±2 s; matching .pf `last_run_times[0]` ±5 s.
- For every `FILE_CREATE(path, t)`: file exists on disk; SI ctime ±1 s.
- For every `URL_VISIT(url, t)`: matching Chrome `visits` row.
- $UsnJrnl record count ≥ scheduler's file-touch count × 0.9.
- No artifact with `ctime == mtime == atime` (except for truly untouched installed binaries).

---

## 8. VM-detection evasion matrix

### 8.1 Inside-guest (ARC responsibility)

| Registry path                                                                                   | Action                                                               | Current coverage                |
| ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------- |
| `HKLM\HARDWARE\Description\System` (SystemBiosVersion, VideoBiosVersion)                        | replace vendor strings (QEMU/VBox/VMware → Dell/HP/Lenovo)           | **add in Phase 7**              |
| `HKLM\HARDWARE\DEVICEMAP\Scsi\...\Identifier`                                                   | replace "QEMU HARDDISK" → "Samsung SSD 970 EVO Plus"                 | **add in Phase 7**              |
| `HKLM\SYSTEM\CurrentControlSet\Services\{VBoxService,VBoxSF,VBoxGuest,VBoxVideo,VBoxMouse}`     | delete key                                                            | partial (SF+Mouse); extend      |
| `HKLM\SYSTEM\CurrentControlSet\Services\{vmtools,vmmouse,vmci,vmhgfs,vmxnet,vmrawdsk,vmusbmouse}` | delete key                                                          | covered                         |
| `HKLM\SYSTEM\CurrentControlSet\Services\{vioscsi,viostor,qemu-ga,kvm*}`                         | delete key                                                            | **add in Phase 7**              |
| `HKLM\SYSTEM\CurrentControlSet\Enum\ACPI\{VBOX0001,VMW0001}`                                    | replace IDs with generic                                              | covered                         |
| `HKLM\SYSTEM\CurrentControlSet\Control\SystemInformation` (SystemManufacturer, SystemProductName) | replace Dell/HP/Lenovo                                              | covered (hardware_normalizer)   |
| `HKLM\HARDWARE\DESCRIPTION\System\BIOS`                                                         | write BIOSVendor, BIOSVersion, ReleaseDate                            | covered (volatile; recorded)    |
| `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-*}\000X\NetworkAddress`                  | set plausible Intel/Realtek OUI                                       | **new: mac_hygiene.py**         |
| `HKLM\SOFTWARE\Oracle\VirtualBox Guest Additions`, `HKLM\SOFTWARE\VMware, Inc.\VMware Tools`    | delete key                                                            | covered                         |
| `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{VBox*,VMware*}`                      | delete key                                                            | **add in Phase 7**              |
| `HKLM\SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters`                                      | delete key                                                            | covered (Hyper-V)               |

### 8.2 Host-side (hypervisor config; ARC documents only)

| Config location                                                    | Fix                                                                                   |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| libvirt `<sysinfo type='smbios'>` or QEMU `-smbios type=0,1,2,3`   | vendor="Dell Inc.", version="A15", manufacturer="Dell Inc.", product="OptiPlex 7090"  |
| libvirt `<interface><mac address=...>`                             | Intel OUI `00:1b:21:...` or Realtek `00:e0:4c:...` (avoid VBox `08:00:27`, VMware `00:0c:29`, QEMU `52:54:00`) |
| libvirt `<disk ... serial=...>`                                    | Plausible SSD serial, e.g. `S5GYNX0N712345Y`                                          |
| QEMU `-cpu host,+hv-*` + `-machine q35,hpet=on`                    | Expose host CPU features; disable hypervisor CPUID leaf                               |
| QEMU `-device e1000e` (replace `virtio-net-pci`)                   | Intel NIC has no QEMU string                                                          |

Acceptance: `pafish` / `Al-Khaser` flags ≤ 10 (baseline unmodified VBox ~50+).

---

## 9. Risk register

| Tag | Issue | Sev | Mitigation |
| --- | --- | --- | --- |
| R1  | libguestfs cannot cleanly write `\$Extend\$UsnJrnl:$J` | H | ADR-008: ntfs-3g FUSE + USN_RECORD_V3 Python appender |
| R2  | guestfs `utimens` sets only $STANDARD_INFORMATION, not $FILE_NAME | M | ADR-009: accept SI-only; SI/FN divergence is realistic signal |
| R3  | Hive `.LOG1`/`.LOG2` replay rolls back hivex writes | H | ADR-010: delete logs after hivex commit |
| R4  | $LogFile is circular + replayed; missing LSN entries are a Triforce tell | L | Accept v1; document in `time_integrity.md` |
| R5  | SMBIOS / DMI / CPUID is 100% hypervisor; ARC cannot spoof | M | ADR-011: provide libvirt XML reference; registry reflection scrubbed |
| R6  | Deterministic reproducibility under scheduler non-trivial | M | Scheduler owns RNG; `child_rng(name)` per service |
| R7  | Hive companion log checksum mismatch → boot loop | H | Same as R3 + audit log warning on failed deletion |
| R8  | `cross_writer.py` imports pywin32 — Linux blocker | H | Phase 3a: strip; use guestfs attribute APIs |
| R9  | `build_vm_image.py` + `mount_existing_vhd.py` use Windows ctypes | H | Delete (§6.1) |
| R10 | `core/llm_client.py` vs `services/ai/gemini_client.py` | L | ADR-007: both kept, different roles |
| R11 | 6 pre-generated profiles in old schema | L | Delete (§6.1) |
| R12 | `config.yaml::artifact_scale` absolute totals vs per-day rates | L | ADR-013: convert to rates × timeline_days |
| R13 | ~8 000 LoC of tests on old schema | H | Phase 9: systematic migration with CI grep-gates |
| R14 | Windows Defender may quarantine injected .exe artifacts | M | Don't inject executable payloads; only artifact traces |
| R15 | Prefetch v31 format (Win11 2024) undocumented publicly | M | Use v30 initially (Win11 accepts it); migrate later |
| R16 | EVTX per-provider templates require reverse-engineering | H | Limit to 8–10 providers; extract templates from live Win11 VM |
| R17 | Persona-implied non-UTC TZ vs hive UTC storage | M | All scheduler UTC internally; convert at write-time |
| R18 | $MFT entry reuse on delete | L | Accept |
| R19 | Linux→Windows NTFS ACL / SID ownership | M | ntfs-3g `setfattr system.ntfs_acl` with persona's user SID |
| R20 | Chrome schema version drift (v46 vs installed Chrome) | L | Detect Chrome version from installation; write matching schema |
| R21 | Prefetch hashtable depends on exact path casing | M | Use `services/filesystem/installed_apps_stub.py` as single source |
| R22 | hivex preserves Security Descriptors on edit | L | No action for edits; new keys inherit parent SD |
| R23 | Persona username vs existing `C:\Users\<x>` in baseline | M | PersonaLoader validates; Phase 2 renames if mismatch |
| R24 | Research must survive context compaction | C | ADR-016: all research in git under `docs/research/` |
| R25 | Reparse points / junctions in baseline | M | Enumerate via `readlink`; leave alone |
| R26 | hivex no large-value (>1 MB) support | L | Don't write such keys (BCD, IconStreams) |
| R27 | Zone.Identifier ADS expected on downloaded files | M | Phase 4a: every URL_DOWNLOAD writes `file:Zone.Identifier` ADS |
| R28 | Failed log-file deletion → Windows bluescreen | H | Phase 3 pre-flight check; audit log warn-fail |

---

## 10. Phase-by-phase implementation

**Execution order** (strict):

```
Phase 0  → 1  → 3a → 3  → 2  → 4c → 4a → 4  → 4b → 7  → 8  → 5  → 9  → 6
```

### Phase 0 — Research archive + ADR log

**Goal**: every subsequent decision has a committed written source of truth.

**Steps**:
1. Create `docs/research/` and `docs/design/` (already done).
2. Write `docs/research/mount_strategy.md` — Sysprep + OOBE first-boot recipe; why baseline must be post-boot (§7.3 MP §3 brief).
3. Write `docs/research/ntfs_journal.md` — $UsnJrnl layout ($J sparse, $Max), USN_RECORD_V2/V3 binary format, ntfs-3g write approach (R1 option B).
4. Write `docs/research/time_integrity.md` — §7 coherence model; divergence rules (SI/FN realistic splits); $LogFile limitations.
5. Write `docs/research/vm_detection_evasion.md` — §8 matrix; MITRE T1497.001; host vs guest responsibilities.
6. Write `docs/research/windows_artifact_baselines.md` — real Win11 density numbers (registry 50 000 keys; evtx 10–20 MB per log; USN journal 0.5–3 GB; Prefetch 30–100 files × 10–80 KB; thumbcache 100+ MB; Chrome History 5–20 MB).
7. Write `docs/design/decisions.md` — seed with ADR-001 … ADR-016 from §4.
8. `git add docs/ && git commit -m "Phase 0: research archive + ADR log"`.

**Acceptance**: all six markdown files exist and are committed (A17).

---

### Phase 1 — Unified PersonaContext schema + ServiceContext

**Goal**: one schema, used end-to-end. No more lossy bridge. Services consume a typed context.

**Pre-reqs**: Phase 0 complete.

**Steps**:

1. **Create `core/persona_context.py`**. Migrate `services/ai/schemas.py::PersonaContext` verbatim, then add these fields (currently derived in `ProfileSynthesizer`):
   ```python
   class PersonaContext(BaseModel):
       model_config = {"frozen": True, "extra": "forbid"}

       # ... existing 25 fields ...

       profile_archetype: Literal["developer", "office_user", "home_user"]
       installed_apps: list[str]                  # derived-then-explicit
       browsing_categories: list[str]
       daily_avg_sites: int = Field(ge=1, le=200)
       timeline_days: int = Field(default=360, ge=30, le=730)
       windows_build_hint: Literal["22H2", "23H2", "24H2"] = "23H2"
       hostname_hints: list[str] = Field(min_length=1, max_length=5)
   ```
2. **Create `core/persona_loader.py`**:
   ```python
   class PersonaLoader:
       def __init__(self, presets_dir: Path): ...
       def load_preset(self, name: str) -> PersonaContext: ...
       def load_yaml(self, path: Path) -> PersonaContext: ...
       # No silent filtering. Pydantic validation; extra keys fail loudly.
   ```
3. **Create `core/service_context.py`**:
   ```python
   @dataclass(frozen=True)
   class ServiceContext:
       persona: PersonaContext
       mount: MountManager
       rng: random.Random
       scheduler: EventScheduler | None        # None during Phase 1; populated in Phase 4a
       expansion: ExpansionBundle | None       # None during Phase 1; populated in Phase 2
       audit: AuditLogger
       timestamp_service: TimestampService
   ```
4. **Edit `services/base_service.py`**:
   ```python
   class BaseService(ABC):
       @property
       @abstractmethod
       def service_name(self) -> str: ...

       @abstractmethod
       def apply(self, ctx: ServiceContext) -> None: ...
   ```
5. **Migrate every service** in `services/**/*.py`. Replace `context["profile_type"]` → `ctx.persona.profile_archetype`; `context["installed_apps"]` → `ctx.persona.installed_apps`; etc. Grep authoritatively:
   ```bash
   grep -rn 'context\["' services/ | wc -l  # target: 0
   ```
6. **Delete** `services/ai/profile_synthesizer.py`, `services/ai/ai_orchestrator.py`, `services/ai/cli.py`, `core/profile_engine.py`.
7. **Delete** `profiles/base.yaml`, `profiles/{developer,office_user,home_user}.yaml`, `profiles/generated/*.yaml`.
8. **Create** `profiles/presets/{developer,office_user,home_user}.yaml` in PersonaContext shape — port persona defaults from the deleted presets + sensible values for new fields.
9. **Rewrite `core/orchestrator.py`**:
   - Delete `_normalize_profile_variant()`, `_resolve_profile_variant()`.
   - Constructor takes `PersonaContext` directly.
   - `ExecutionPhase` enum stays; `INFRASTRUCTURE` phase is now AI-optional.
   - Services receive `ServiceContext`.
10. **Rewrite `main.py`**:
    - One `_build_persona()` that either loads preset (via `PersonaLoader`) or calls `PersonaGenerator.generate()` → `PersonaContext`.
    - Feed directly to Orchestrator. No YAML round-trip.
11. **Rewrite `services/ai/persona_generator.py`** to return `PersonaContext` directly (no YAML).
12. **Update `config.yaml`**:
    ```yaml
    profile_name: "home_user"        # now refers to profiles/presets/<name>.yaml
    timeline_days: 360               # was 90
    ```

**Tests**:
- Write `tests/test_core/test_persona_loader.py` — valid YAML → PersonaContext; invalid YAML → ValidationError; extra field → error.
- Delete `tests/test_core/test_profile_engine.py`, `test_orchestrator_profile_variant.py`.
- Smoke test: `python main.py --preset home_user --dry-run` completes.

**CI gates**:
- `grep -rn 'context\["' services/` returns 0 (A2).
- `grep -rn 'profile_type\|installed_apps\|browsing_categories' services/ --include='*.py'` — every hit must be `ctx.persona.<field>`.

**Acceptance**: single end-to-end run from `--preset` and from `--ai-generate` both produce a valid ServiceContext passed to all 33 services.

---

### Phase 3a — Remove pywin32 from cross_writer

**Goal**: unblock Linux migration (R8).

**Pre-reqs**: Phase 1 complete.

**Steps**:
1. `services/filesystem/cross_writer.py` — remove `import win32api`, `import win32con`, `import pywintypes`.
2. Replace `win32file.SetFileAttributesW(path, flags)` with a new abstraction in `core/mount_manager.py`:
   ```python
   class MountManager:
       def set_ntfs_attributes(self, relative_path: str, *,
                               hidden: bool = False, system: bool = False,
                               archive: bool = True) -> None: ...
   ```
   Implementation is platform-dispatched in Phase 3 (`LinuxMountBackend` uses guestfs `setxattr system.ntfs_attrib`).

**Tests**:
- `tests/test_services/test_cross_writer.py` — if it existed (check) — remove pywin32-specific assertions.

**CI gate**: `grep -rn 'import win32\|from win32' .` returns 0 (A3).

**Acceptance**: `python -c "import services.filesystem.cross_writer"` works on Linux.

---

### Phase 3 — Linux mount backend (libguestfs + hivex + ntfs-3g)

**Goal**: replace PowerShell mounter with a Linux-native backend.

**Pre-reqs**: Phase 3a complete.

**Steps**:

1. **Create `core/linux_mount.py`**:
   ```python
   class LinuxMountBackend:
       def __init__(self, vhdx_path: Path): ...
       def mount(self) -> None:
           """libguestfs add_drive + launch + inspect_os + mount('/dev/sdaN', '/')."""
       def unmount(self) -> None:
           """umount_all + shutdown; return clean."""

       def read_bytes(self, path: str) -> bytes: ...
       def write_bytes(self, path: str, data: bytes) -> None: ...
       def mkdir_p(self, path: str) -> None: ...
       def utimens(self, path: str, atime: datetime, mtime: datetime) -> None: ...
       def set_ntfs_attributes(self, path: str, *, hidden=False, system=False, archive=True) -> None: ...

       def open_hive(self, hive_path: str) -> HivexHandle:
           """hivex.Hivex(local_file_path, write=True)."""

       def commit_hive(self, handle: HivexHandle) -> None:
           """h.commit(None); then delete .LOG1/.LOG2 (R3, R7, R28)."""

       def host_fuse_mount(self) -> Path:
           """guestmount via ntfs-3g for raw stream work (Phase 4b)."""

       def host_fuse_unmount(self) -> None: ...
   ```
2. **Rewrite `core/mount_manager.py`** to delegate to `LinuxMountBackend` but preserve the existing `resolve(relative_path)` API — paths returned are still opaque handles the services pass around, but internally they're guestfs paths, not host paths.
3. **Rewrite `services/registry/hive_writer.py`** to use hivex API:
   ```python
   class HiveWriter:
       def __init__(self, backend: LinuxMountBackend): ...
       def apply_operations(self, hive_path: str, ops: list[HiveOperation]) -> None:
           with self._backend.open_hive(hive_path) as h:
               for op in ops:
                   op.execute(h)   # hivex calls: node_add_child, node_set_value, node_delete_child
               self._backend.commit_hive(h)
   ```
   Preserve the `HiveOperation` and `RegistryValueType` API surface so the 5 domain registry services and the anti-fingerprint scrubbers keep working.
4. **Delete `core/vm_manager.py`**.
5. **Delete root scripts**: `build_vm_image.py`, `mount_existing_vhd.py`.
6. **Delete** `install_*.bat`, `quick_install.bat`, `run_local_model.bat`.
7. **Update `requirements.txt`**: drop `pywin32`.
8. **Update `SETUP.md`, `ENV_SETUP.md`, `START_HERE.md`**: Linux install steps from §5.1.
9. **Rewrite `arc_wizard.py`** to use `LinuxMountBackend`; remove Z:-drive logic.

**Tests**:
- `tests/test_core/test_linux_mount.py` — mount a tiny reference VHDX fixture; verify `read_bytes(/Windows/System32/drivers/etc/hosts)` returns plausible content.
- Rewrite `tests/test_services/test_registry_writer.py` against hivex API.

**CI gate**:
- `grep -rn 'powershell\|Mount-DiskImage\|Get-DiskImage' core/ services/` returns 0 (A4).

**Acceptance**: `python -c "from core.linux_mount import LinuxMountBackend; b = LinuxMountBackend('fixture.vhdx'); b.mount(); print(b.read_bytes('/Windows/System32/drivers/etc/hosts')[:40]); b.unmount()"` succeeds on Linux host.

---

### Phase 2 — Wire expansion pipeline

**Goal**: seeds produced by AI (or preset fallback) actually land on disk.

**Pre-reqs**: Phase 3 complete.

**Steps**:

1. `git mv services/generators services/expansion` (ADR-014). Update imports everywhere.
2. Add `ExecutionPhase.EXPANSION = 1.5` in `core/orchestrator.py`. Expansion services run after infrastructure, before filesystem/registry/etc.
3. **Convert `config.yaml::artifact_scale` to per-day rates**:
   ```yaml
   artifact_scale:
     downloads: {per_day: 4.2, jitter: 0.3}           # was target_total: 1500 / 90d
     documents: {per_day: 12.5, jitter: 0.3}          # was 4500 / 90d
     pictures:  {per_day: 2.1, jitter: 0.4}           # was 750 / 90d
     browser_history: {per_day: 83, jitter: 0.5}      # was 7500 / 90d
     search_terms: {per_day: 16.7, jitter: 0.5}       # was 1500 / 90d
     bookmarks: {per_day: 0.56, jitter: 0.2}          # was 200 / 90d
   # target_count = per_day × persona.timeline_days × (1 + rng.uniform(-jitter, +jitter))
   ```
4. Wire expansion services:
   ```python
   class ExpansionOrchestrator:
       def run(self, ctx: ServiceContext) -> ExpansionBundle:
           return ExpansionBundle(
               documents = BulkDocumentsGenerator().expand(ctx, seeds.documents),
               downloads = BulkDownloadsGenerator().expand(ctx, seeds.downloads),
               media     = BulkMediaGenerator().expand(ctx, seeds.media),
               history   = BulkBrowsingGenerator().expand(ctx, seeds.browsing),
               ...
           )
   ```
5. Every downstream service (`services/filesystem/document_generator.py`, `services/browser/history.py`, etc.) reads from `ctx.expansion` instead of its own `data/*.json` / `Random(42)`.
6. Fallback path: when `ctx.persona` has no AI seeds (preset-only), `seed_generators/*.py::fallback_*` produce deterministic seeds from PersonaContext fields.

**Tests**:
- `tests/test_services/test_expansion_integration.py` — end-to-end: persona → seeds → expansion → filesystem count matches per_day × timeline_days × (1 ± jitter).

**Acceptance**: `python main.py --preset home_user --timeline-days 360` produces ≥ 4000 documents, ≥ 1200 downloads, ≥ 30 000 URL visits on disk.

---

### Phase 4c — Timeline 90 → 360

**Goal**: every `90` / `timeline_days` call site respects the new default.

**Steps**:
1. `config.yaml::timeline_days: 360`.
2. `verify_realism.py::TIMELINE_DAYS = 360`.
3. Grep every `= 90\b` in services; replace with `ctx.persona.timeline_days` where semantically timeline-related.
4. `permutation.date_spread_days` in config → removed; derived from persona.

**CI gate**: `grep -rn '\b90\b' services/ | grep -iE 'timeline|days|spread'` returns 0.

**Acceptance**: `PersonaContext.timeline_days` is the only timeline constant referenced.

---

### Phase 4a — EventScheduler + migration

**Goal**: deterministic cross-domain event stream; no service owns time or RNG.

**Pre-reqs**: Phase 2 + 4c complete.

**Steps**:

1. **Create `core/event_scheduler.py`** per §7.4 contract. Core implementation:
   - Walk `persona.timeline_days` back from `now`.
   - For each day: skip if `weekday not in persona.active_days`; otherwise emit a day-session.
   - Day-session = login-event, Poisson-distributed activity events respecting work_hours, logoff-event.
   - Activity events drawn from weighted pools: app_launches, file_ops, url_visits, system_events.
   - Seed: `Random(hash(persona.username + str(persona.timeline_days)) ^ random_seed)`.
2. **Define events** (`core/events.py` or nested in `event_scheduler.py`):
   ```python
   @dataclass(frozen=True)
   class SyntheticEvent:
       kind: Literal["APP_LAUNCH","FILE_CREATE","FILE_MODIFY","FILE_DELETE",
                     "URL_VISIT","LOGIN","LOGOFF","SYSTEM_UPDATE"]
       timestamp: datetime                       # UTC
       payload: dict[str, Any]                   # kind-specific
   ```
3. **Orchestrator integration**: `EXPANSION` phase creates expansion bundle; new `SCHEDULING` phase creates `EventScheduler` + pre-emits events; `ServiceContext.scheduler` populated.
4. **Migrate every service** from `datetime.now()` / `Random(42)` to `ctx.scheduler.events_of(kind)` / `ctx.scheduler.child_rng(service_name)`. This is a bulk edit — grep and fix systematically.
5. **TemporalCoherenceCheck**: new class in `evaluation/consistency_checker.py`. After a run, re-read scheduler's events and validate §7.5 rules against written artifacts.

**Tests**:
- `tests/test_core/test_event_scheduler.py` — determinism (same seed ⇒ same event list); work-hour respect; weekend silence; child_rng independence.
- `tests/test_evaluation/test_temporal_coherence.py` — synthetic coherent run passes; intentionally desynced run fails.

**CI gate**:
- `grep -rn 'datetime\.now\|datetime\.utcnow\|Random(42)' services/` returns 0 (A1).
- A16: `python main.py --random-seed 4242 --output a.audit.jsonl && ... && diff a b` is empty.

**Acceptance**: every scheduler-emitted event has a cross-domain fan-out verified by TemporalCoherenceCheck.

---

### Phase 4 — Density (scheduler-aware)

**Goal**: artifacts match real Win11 baselines.

**Pre-reqs**: Phase 4a complete.

**Steps**:

1. **Registry** — target 5 000+ new keys total. Each domain service writes scheduler-driven MRU bumps; UserAssist counts come from scheduler APP_LAUNCH count.
2. **Prefetch** (`services/filesystem/prefetch.py`): rewrite body.
   - v30 format (Win11 accepts v30; v31 per R15 deferred).
   - Real 84-byte header + file-metrics array + trace chains + filename strings + volume info.
   - Size 10–80 KB per file via trace-chain length tied to scheduler APP_LAUNCH count per app.
3. **EVTX** (`services/eventlog/evtx_writer.py`): rewrite body.
   - Multi-chunk: 4 KB header + N × 64 KB chunks + footer.
   - BinaryXML records per scheduler event.
   - Template table extracted from a reference Win11 Security.evtx (ship as `templates/evtx/reference_templates.bin`).
   - Target 10–20 MB per primary log.
4. **Thumbnail cache**: grow with expansion.media count; target 100 MB for thumbcache_2560.db on home_user.
5. **Documents**: 500+ files; python-docx/openpyxl/reportlab for real .docx/.xlsx/.pdf.
6. **Browser History**: 30 000+ urls rows; 80 000+ visits.
7. **New services**: `services/filesystem/{office_mru,powershell_history,cdp_logs}.py`. Each consumes scheduler events of its relevant kind.

**Tests**: per-service density asserts.

**Acceptance** (A10–A15):
- Registry ≥ 5 000 new keys.
- Prefetch ≥ 30 files, mean ≥ 15 KB.
- EVTX per log ≥ 10 MB, ≥ 5 000 records.
- Documents ≥ 500 files, openable.
- Browser ≥ 5 000 urls / ≥ 10 000 visits.
- VHDX delta ≥ 500 MB.

---

### Phase 4b — NTFS journal ($UsnJrnl + $MFT)

**Goal**: NTFS artifacts coherent with scheduler.

**Pre-reqs**: Phase 4a complete.

**Steps**:

1. **Create `services/ntfs/mft_timestamp_patcher.py`**:
   ```python
   class MftTimestampPatcher(BaseService):
       def apply(self, ctx: ServiceContext) -> None:
           # Precondition: guestfs unmounted; ntfs-3g FUSE mounted (backend.host_fuse_mount()).
           for event in ctx.scheduler.events_of("FILE_CREATE", "FILE_MODIFY"):
               patch_si_times(fuse_path / event.payload["path"], event.timestamp)
           # $FILE_NAME left at create-time (ADR-009).
   ```
   Use `os.setxattr(path, "system.ntfs_times", struct.pack(...))` via ntfs-3g.
2. **Create `services/ntfs/usn_journal_writer.py`**:
   ```python
   class UsnJournalWriter(BaseService):
       def apply(self, ctx: ServiceContext) -> None:
           # Read $Max header for NextUsn.
           # For each FILE_CREATE/MODIFY/DELETE/CLOSE event, pack USN_RECORD_V3 and append to $J stream.
           # Update $Max.NextUsn.
   ```
   ntfs-3g supports colon-syntax stream access: `open("/mnt/arc/\$Extend/\$UsnJrnl:$J", "r+b")`.
3. **Create `services/ntfs/logfile_writer.py`** — best-effort stub; Phase 1 returns empty op-list but logs intent (R4).
4. **Orchestrator phase `NTFS = 4.5`** — runs after filesystem/registry/browser/evtx so the events to journal already exist as scheduler records.
5. **Phase 3 extension**: `LinuxMountBackend.host_fuse_mount()` / `host_fuse_unmount()` wrapping `guestmount` / `guestunmount`.
6. **Pre-flight check** (R28): verify `.LOG1` / `.LOG2` are writable and deletable before any hivex writes.

**Tests**:
- `tests/test_services/test_mft_timestamp_patcher.py` — write file, patch, read back via `istat` (Sleuth Kit) to assert SI times.
- `tests/test_services/test_usn_journal_writer.py` — ingest fixture events, parse with `fls` / `tsk_usnjrnl`.

**Acceptance** (A6, A7):
- `fsutil usn readjournal C:` inside booted VM ≥ 500 k records spanning ≥ 300 days.
- `MFTECmd.exe` shows matching SI times.

---

### Phase 7 — VM key scrubbing

**Goal**: scrub registry VM-detection markers (§8.1).

**Pre-reqs**: Phase 3 complete.

**Steps**:

1. **Extend `services/anti_fingerprint/vm_scrubber.py`** to cover:
   - `VBoxVideo`, `VBoxGuest`, `VBoxService` (add to existing VBoxSF / VBoxMouse list).
   - `vioscsi`, `viostor`, `qemu-ga`, `kvm*`.
   - `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{VBox*,VMware*}`.
2. **Extend `services/anti_fingerprint/hardware_normalizer.py`** to cover:
   - `HKLM\HARDWARE\Description\System\SystemBiosVersion`, `VideoBiosVersion`.
   - `HKLM\HARDWARE\DEVICEMAP\Scsi\Scsi Port 0\Scsi Bus 0\Target Id 0\Logical Unit Id 0\Identifier`.
3. **Create `services/anti_fingerprint/mac_hygiene.py`**:
   ```python
   class MacHygiene(BaseService):
       def apply(self, ctx: ServiceContext) -> None:
           # Walk HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-*}\000X
           # For each adapter key, set NetworkAddress = Intel/Realtek OUI + random tail.
   ```
4. **Extend `core/identity_generator._VM_STRINGS`** to include `qemu-ga`, `kvm`, `KVMKVMKVM\0\0\0` CPUID strings.
5. **Add `--skip-anti-fingerprint` flag** to `main.py` for baseline testing.

**Tests**: extend `test_vm_scrubber.py`, `test_hardware_normalizer.py`; new `test_mac_hygiene.py`.

**Acceptance** (A8): `pafish` / `Al-Khaser` flags ≤ 10 inside booted modified VM.

---

### Phase 8 — Baseline build + libvirt spoofing templates

**Goal**: operator recipe end-to-end.

**Steps**:

1. **Create `scripts/build_baseline_vhdx.sh`**:
   ```bash
   #!/bin/bash
   set -euo pipefail
   ISO=$1; UNATTEND=$2; OUT=$3; SIZE=${4:-80G}
   qemu-img create -f vhdx "$OUT" "$SIZE"
   virt-install \
       --name arc-baseline --memory 4096 --vcpus 4 \
       --disk path="$OUT",format=vhdx \
       --cdrom "$ISO" --initrd-inject "$UNATTEND" \
       --os-variant win11 --graphics spice --wait -1
   # virt-install exits on shutdown initiated by unattend.xml FirstLogonCommands
   virsh undefine arc-baseline
   ```
2. **Create `examples/unattend.xml`** — answer file with:
   - `<AutoLogon>` single-user skip.
   - `<OOBE><SkipMachineOOBE>true</SkipMachineOOBE>`.
   - `<FirstLogonCommands>` that wait 3 minutes (powershell `Start-Sleep 180`) then `shutdown /s /t 0`.
3. **Create `examples/libvirt-profile-template.xml`** — domain XML with:
   - `<sysinfo type='smbios'>` Dell OptiPlex 7090 strings.
   - `<interface><mac address='00:1b:21:xx:xx:xx'>` Intel OUI.
   - `<disk ... serial='S5GYNX0N712345Y'>`.
   - `<cpu mode='host-passthrough'>`.
   - `<feature><hidden state='on'/></feature>` for hypervisor CPUID hiding.
4. **Update `docs/wizard_guide.md`** — Linux end-to-end with these scripts.

**Acceptance** (A20): `virsh define ./examples/libvirt-profile-template.xml && virsh start arc-test` boots cleanly.

---

### Phase 5 — AI prompts + missing seed generators

**Goal**: Gemini emits Windows-artifact-specific structured output.

**Steps**:

1. Rewrite `services/ai/prompts/persona.txt` — add required fields: `hostname_hints`, `typical_domain_names`, `local_timezone_hints`, `windows_build_hint`, `profile_archetype`.
2. Rewrite `services/ai/prompts/documents.txt` — emit per doc: `subfolder` (specific Windows path), `registry_mru_hint`.
3. Rewrite `services/ai/prompts/browsing.txt` — Chrome Default vs Profile 1, extension IDs.
4. **New prompts**:
   - `services/ai/prompts/registry_artifacts.txt` — emit list of realistic registry key/value tuples for this persona (RunMRU, TypedPaths, UserAssist ROT13).
   - `services/ai/prompts/evtx_events.txt` — persona-specific event-log stories.
   - `services/ai/prompts/prefetch_apps.txt` — installed exec list + run-count distribution.
5. **New seed generators**:
   - `services/ai/seed_generators/media.py` — MediaSeed production (missing R-add).
   - `services/ai/seed_generators/registry.py` — RegistrySeed.
   - `services/ai/seed_generators/evtx.py` — EvtxSeed.
   - `services/ai/seed_generators/prefetch.py` — PrefetchAppSeed.
6. **New seed schemas** in `core/persona_context.py`: `RegistrySeed`, `EvtxSeed`, `PrefetchAppSeed`, `MediaSeed`.

**Acceptance**: `python -m arc.ai.generate --occupation "X" --json` returns a schema-valid PersonaContext + full seed bundle.

---

### Phase 9 — Test migration + CI gates

**Goal**: tests pass; CI prevents regression.

**Steps**:

1. **Bulk-migrate `tests/test_services/test_*.py`**:
   - Replace `context = {"profile_type": ...}` with `ctx = ServiceContext(persona=..., scheduler=..., ...)`.
   - Replace any direct `datetime.now()` / `Random()` with scheduler stubs.
2. **New tests**:
   - `test_persona_loader.py` (Phase 1).
   - `test_linux_mount.py` (Phase 3).
   - `test_event_scheduler.py` (Phase 4a).
   - `test_temporal_coherence.py` (Phase 4a).
   - `test_mft_timestamp_patcher.py`, `test_usn_journal_writer.py` (Phase 4b).
   - `test_mac_hygiene.py` (Phase 7).
3. **Delete** `test_profile_engine.py`, `test_vm_manager.py`, `test_orchestrator_profile_variant.py`.
4. **CI config** (`.github/workflows/ci.yml` or equivalent):
   ```yaml
   - name: CI grep gates
     run: |
       ! grep -rn 'datetime\.now\|datetime\.utcnow\|Random(42)' services/
       ! grep -rn 'context\[.profile_type.\]\|context\[.installed_apps.\]' services/
       ! grep -rn 'import win32\|from win32' .
       ! grep -rn 'powershell\|Mount-DiskImage\|Get-DiskImage' core/ services/
   - name: pytest
     run: pytest -x
   ```

**Acceptance**: `pytest -x` passes on Linux; all 4 CI gates green (A1, A2, A3, A4, A5).

---

### Phase 6 — Documentation refresh

**Goal**: docs accurate; obsolete docs archived.

**Steps**:

1. Rewrite `docs/architecture.md` — unified schema, LinuxMountBackend, hivex, scheduler.
2. Rewrite `docs/profile_schema.md` — canonical PersonaContext fields + seed types.
3. Rewrite `docs/change_log_format.md` — new service list.
4. Rewrite `docs/evaluation_report.md` — new density baselines.
5. Rewrite `docs/wizard_guide.md` — Linux flow, no Z: drive.
6. Rewrite `report.md` — after Phase 4 regenerate metrics.
7. Rewrite `latex.tex` / regenerate `docs/project_report.pdf`.
8. Rewrite `SETUP.md`, `ENV_SETUP.md`, `START_HERE.md`, `WIZARD_QUICKSTART.md`, `install_deps.sh`, `agents.md`, `project_structure.md`, `log.md`.
9. Obsolete drafts → `docs/archive/`.
10. `team.md` untouched (already accurate).
11. **This file (`docs/MASTER_PLAN.md`)** — update only if architecture changes; otherwise leave.

**Acceptance**: no doc references deleted files; every code location cited in a doc exists.

---

## 11. Acceptance matrix

| #   | Gate                                                                                           | Phase    |
| --- | ---------------------------------------------------------------------------------------------- | -------- |
| A1  | `grep -rn 'datetime\.now\|Random(42)' services/` → 0                                           | 4a       |
| A2  | `grep -rn 'context\[.profile_type.\]' services/` → 0                                           | 1        |
| A3  | `grep -rn 'import win32\|from win32' .` → 0                                                    | 3a       |
| A4  | `grep -rn 'powershell\|Mount-DiskImage' core/ services/` → 0                                   | 3        |
| A5  | `pytest -x` passes on Linux                                                                    | 9        |
| A6  | `fsutil usn readjournal C:` in booted modified VHDX ≥ 500k records ≥ 300 days                  | 4b       |
| A7  | `MFTECmd.exe` SI times match scheduler events (FN divergence acceptable per ADR-009)           | 4b       |
| A8  | `pafish` / `Al-Khaser` flags ≤ 10                                                              | 7 + 8    |
| A9  | `TemporalCoherenceCheck` passes                                                                | 4a       |
| A10 | Registry: ≥ 5 000 new keys                                                                     | 4        |
| A11 | Prefetch: ≥ 30 files, mean ≥ 15 KB                                                             | 4        |
| A12 | EVTX per log ≥ 10 MB, ≥ 5 000 records                                                          | 4        |
| A13 | Chrome History ≥ 5 000 urls / ≥ 10 000 visits                                                  | 4        |
| A14 | ≥ 500 documents openable in Word/Excel/Adobe                                                   | 4        |
| A15 | VHDX delta ≥ 500 MB                                                                            | 4        |
| A16 | Same `--random-seed` ⇒ byte-identical audit log                                                | 4a       |
| A17 | `docs/research/*.md` + `docs/design/decisions.md` committed                                    | 0        |
| A18 | No ARC-written file has ctime == mtime == atime                                                | 4a       |
| A19 | Hive `.LOG1`/`.LOG2` deletion verified; no registry rollback on boot                           | 3        |
| A20 | `virsh define && virsh start` of libvirt template succeeds                                     | 8        |

---

## 12. Open decisions (resolve before Phase 1)

| Q   | Question                                                                                    | Default if silent                         |
| --- | ------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Q1  | Confirm R1 option B (ntfs-3g + USN appender)?                                               | Yes (ADR-008)                             |
| Q2  | SMBIOS spoofing libvirt-only; VirtualBox users get a README?                                | Yes (ADR-011)                             |
| Q3  | Phase 5 AI prompts in scope for v1, or defer?                                               | In scope                                  |
| Q4  | Consolidate `START_HERE.md`, `WIZARD_QUICKSTART.md`, `SETUP.md` → single `GETTING_STARTED.md`? | Consolidate                            |
| Q5  | A16 byte-identical determinism mandatory or nice-to-have?                                   | Mandatory (ADR-012)                       |
| Q6  | Phases 1–4 ship as v1; 4b + 7 as v1.1?                                                      | All ship together (single rescue release) |

---

## 13. Operator runbook (post-refactor)

```bash
# One-time host setup
sudo apt install libguestfs-tools libhivex-bin ntfs-3g guestmount virtinst qemu-system-x86 libvirt-daemon-system
pip install -r requirements.txt
export GEMINI_API_KEY=...

# One-time per Windows SKU: build baseline VHDX
./scripts/build_baseline_vhdx.sh \
    --iso ~/isos/Win11_23H2.iso \
    --unattend ./examples/unattend.xml \
    --out ~/vms/baseline.vhdx --size 80G

# Per analyst run: inject
cp ~/vms/baseline.vhdx ~/vms/run-042.vhdx
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --ai-generate --occupation "Software Engineer" \
    --interests gaming,open-source \
    --timeline-days 360 \
    --random-seed 4242 \
    --audit-log ~/vms/run-042.audit.jsonl

# Boot for analysis
virsh define ./examples/libvirt-profile-template.xml
virsh start arc-run-042
```

---

## 14. File tree — before vs after

**Before (bug state)**:
- 3 schemas (old ProfileContext + new PersonaContext + Chromium schema) — lossy bridge.
- 2 orchestrators (`core/orchestrator.py` + `services/ai/ai_orchestrator.py`).
- Windows-only mount (`core/vm_manager.py`).
- Dead bulk expansion (`services/generators/`).
- 512 B Prefetch stubs, 8 KB hive stubs, 69 KB evtx stubs.
- 70 MB VHDX delta.

**After (rescue)**:
- 1 schema (`core/persona_context.py`); Chromium schema is orthogonal (SQLite, not profile).
- 1 orchestrator with Phase 0 (persona build) + Phase 1.5 (expansion) + Phase 2 (scheduling) baked in.
- Linux mount (`core/linux_mount.py`).
- Wired expansion (`services/expansion/`).
- v30 Prefetch 10–80 KB, hives at baseline size (100 MB+ SOFTWARE), evtx 10–20 MB per log.
- ≥ 500 MB VHDX delta.

---

**End of master plan.** Proceed to Phase 0.
