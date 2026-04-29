# ARC — Architecture Decision Record log

**Status**: live. Every architectural change ships with an ADR entry. If any other document contradicts a decision here, **this file wins** and the other document is updated.

**Companion**: `docs/MASTER_PLAN.md` §4 (summary table). This file is the authoritative long form.

## Format

```
## ADR-NNN — <decision in one sentence>

- Status: {proposed | accepted | superseded-by ADR-XXX | deprecated}
- Date: YYYY-MM-DD
- Relates to: <risk tags, phase numbers, other ADRs>

### Context
<what was the problem; what constraints force the decision>

### Decision
<one declarative paragraph>

### Consequences
<what becomes easier / harder / locked-in>

### Alternatives considered
<option → why rejected>
```

---

## ADR-001 — Unify on `PersonaContext` (25 fields); delete `ProfileContext`

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §1, Phase 1, R11, bug #1

### Context

The repo currently carries two schemas. The old `ProfileContext` in `core/profile_engine.py` exposes 6 fields (username, profile_type, locale, installed_apps, browsing_categories, daily_avg_sites). The newer `PersonaContext` in `services/ai/schemas.py` exposes 25 fields including occupation, work_hours_start/end, active_days, personality traits, device posture, and structured seed bundles (DocumentsSeed, DownloadsSeed, BrowsingSeed, FilenamesSeed). `ProfileEngine._resolve_effective_fields()` at `core/profile_engine.py:127` silently filters every AI-produced persona down to the 6-field old schema before services see it:

```python
filtered = {k: v for k, v in resolved.items() if k in allowed_fields}
```

Every downstream service therefore runs on a stripped-down persona regardless of how rich the upstream AI output was. The AI-emitted `_ai_metadata` key is dropped entirely. Services that need persona depth (e.g. `process_faker.py`, `system_content_populator.py`) compensate by re-sampling from `data/*.json` with their own `Random(42)`, defeating both the AI output and determinism.

### Decision

Adopt `PersonaContext` as the single persona schema, extended with 7 additional fields that are currently derived inside `ProfileSynthesizer` (`profile_archetype`, `installed_apps`, `browsing_categories`, `daily_avg_sites`, `timeline_days`, `windows_build_hint`, `hostname_hints`). The canonical definition moves to `core/persona_context.py`. `ProfileContext` is deleted along with `profile_engine.py` and `profile_synthesizer.py`. Services receive a typed `ServiceContext` (ADR-006) that holds a `PersonaContext` directly — no filtering, no dict-shape. Extra fields on inbound YAML fail Pydantic validation loudly (`extra="forbid"`).

### Consequences

- No "lossy bridge" between AI output and services. A single migration removes ~1100 LoC (`profile_engine.py` 246 + `profile_synthesizer.py` 353 + `ai_orchestrator.py` 570 partial).
- Every service's `apply(ctx)` is retyped. Bulk grep-replace of `context["..."]` → `ctx.persona.<field>`. Non-trivial, but mechanical.
- Old `profiles/*.yaml` and `profiles/generated/*.yaml` are rewritten in PersonaContext shape under `profiles/presets/`.
- Tests that asserted the filter behaviour (`tests/test_core/test_profile_engine.py`, 476 LoC) are deleted.
- Determinism becomes achievable because persona state is no longer re-derived in the services.

### Alternatives considered

- **Keep both schemas, make the filter lossless**: still leaves two schemas, two places to mutate, and doesn't fix the "services re-sample with their own RNG" problem.
- **Make `ProfileContext` a view over `PersonaContext`**: adds an adapter layer without removing confusion; services would still receive a dict-shaped view.

---

## ADR-002 — Linux host only; ntfs-3g direct mount + hivex backend (superseded mount layer by ADR-017)

- Status: accepted (mount-layer implementation superseded by ADR-017; Linux-only decision stands)
- Date: 2026-04-19
- Relates to: MP §5, Phase 3, R8, R9, bug #4

### Context

`core/vm_manager.py` (278 LoC) implements VHDX mount via PowerShell: `Mount-DiskImage`, `Get-DiskImage`, `Get-Partition`, drive-letter assignment, Z: drive assumptions. `build_vm_image.py` and `mount_existing_vhd.py` call `ctypes.windll.shell32` and `diskpart`. `services/filesystem/cross_writer.py` imports `win32api`, `win32con`, `pywintypes`. The host development and target deployment environment is Linux (Ubuntu 24.04+); none of the above work there.

Linux has two native primitives that cover the full NTFS write surface:

- **ntfs-3g via FUSE** (direct mount on the real Windows partition): exposes all NTFS operations via standard POSIX file I/O, colon-path ADS syntax (`streams_interface=windows`), `setfattr system.ntfs_times`, `setfattr system.ntfs_attrib_be`. Replaces libguestfs entirely (see ADR-017).
- **hivex** (`hivex` Python binding): reads + writes Windows registry hive files (SOFTWARE, SYSTEM, NTUSER.DAT, SECURITY, DEFAULT). Preserves security descriptors. Handles all value types including REG_BINARY and REG_MULTI_SZ. Cannot handle values larger than ~1 MB (R26), which we accept.

### Decision

`core/linux_mount.py::LinuxMountBackend` wraps ntfs-3g direct mount + hivex. PowerShell mount is deleted. Every service reaches the Windows partition through `LinuxMountBackend` via the `MountManager` facade. The host dev and deploy platform is Ubuntu 24.04+; other Linux distros are best-effort but untested. libguestfs is not used (ADR-017 supersedes that part of the original ADR-002 decision).

### Consequences

- Drops the Windows-host dependency entirely. No more Z: drive, no `diskpart`, no `ctypes.windll`.
- ~620 LoC deleted outright (`vm_manager.py` 278 + `build_vm_image.py` ~250 + `mount_existing_vhd.py` 97 + .bat scripts).
- `cross_writer.py` loses its pywin32 imports (Phase 3a); NTFS attribute writes go through `MountManager.set_ntfs_attributes(...)` which dispatches to `setfattr system.ntfs_attrib_be` via ntfs-3g.
- `requirements.txt` drops `pywin32`; adds nothing new at pip level (ntfs-3g/hivex are system packages; libguestfs removed).
- The two-phase mount sequence (guestfs Phase A + guestmount Phase B) collapses into a single ntfs-3g mount (ADR-017). `host_fuse_mount()` returns the existing mount point immediately.
- CI grep-gates (A3, A4) enforce no regressions: `import win32`, `from win32`, `powershell`, `Mount-DiskImage`, `Get-DiskImage` must stay at zero.

### Alternatives considered

- **Keep PowerShell, run via WSL**: drags a Windows host dependency back in, or requires WSL2 which doesn't expose block devices cleanly.
- **libguestfs + ntfs-3g** (original plan): adds QEMU appliance overhead (~30 s startup) and a two-phase mount complexity with no benefit now that we target a real partition. Replaced by ADR-017.

---

## ADR-003 — Windows partition must be post-OOBE and fully shut down; ARC is offline-inject only

- Status: accepted (updated for dual-boot per ADR-017; VHDX build scripts removed)
- Date: 2026-04-19 (updated 2026-04-27)
- Relates to: MP §7.3, Phase 8, R1, ADR-017

### Context

Several NTFS and hive structures only come into existence — with correct internal headers — after
Windows's own NTFS driver and registry manager see them at least once:

| Structure                      | Never-booted state    | Post-OOBE state |
| ------------------------------ | --------------------- | --------------- |
| `$Extend\$UsnJrnl:$J` + `$Max` | zero-sized or absent  | initialised     |
| `$LogFile`                     | tiny                  | ~64 MB          |
| Hive `.LOG1` / `.LOG2`         | minimal               | real            |
| `Prefetch\*.pf`                | none                  | ~10 .pf files   |
| `Windows\System32\winevt\Logs` | empty channels        | real channels   |
| Hive SOFTWARE                  | minimal, ~5-10 MB     | ~100 MB         |

Additionally, Windows Fast Startup (hybrid shutdown) leaves the NTFS volume dirty. ntfs-3g refuses
to mount a dirty volume read-write (see ADR-017, `docs/research/mount_strategy.md` §2).

### Decision

ARC runs on a Windows 11 partition in a dual-boot setup that has: (1) completed OOBE, (2) has
Fast Startup disabled (`powercfg /h off`), and (3) was fully shut down before the Ubuntu session
where ARC runs. ARC never creates, boots, snapshots, or live-manages VMs or partitions. There is
no baseline VHDX — the real dual-boot partition is the ARC target.

### Consequences

- ARC's input is always "a dual-boot Windows install that completed OOBE and is powered off".
- `scripts/build_baseline_vhdx.sh` and `examples/unattend.xml` are removed (no VM to build).
- Phase 8 delivers a dual-boot setup checklist instead of virt-install automation.
- No per-SKU baseline storage overhead. When Windows updates: boot Windows, let it update, shut down.
- We avoid NTFS/registry header-forgery bugs that would require reverse-engineering on-first-boot repair logic.

### Alternatives considered

- **Maintain a VHDX library per SKU**: requires virt-install automation, libguestfs overhead, image management. Removed by ADR-017.
- **ARC runs during first-boot via `FirstLogonCommands`**: defeats the offline-only design.
- **ARC forges all headers offline**: months of reverse-engineering work; fragile against Windows updates.

---

## ADR-004 — Default timeline = 360 days (was 90); validate `30 ≤ timeline_days ≤ 730`

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 4c, MP §1, ADR-013

### Context

The original plan used a 90-day timeline, encoded in several places: `config.yaml::permutation.date_spread_days`, `verify_realism.py::TIMELINE_DAYS`, and literal `90` integers inside individual services. The user's stated requirement is "360 days" because sandbox operators looking at registry MRU ages and `$UsnJrnl` breadth expect a year-scale lived-in image. 90 days is visibly shallow — first visit on every MRU list lands three months ago at most, which reads as synthetic.

### Decision

Default `PersonaContext.timeline_days = 360`. Pydantic bounds `Field(ge=30, le=730)` — 30 is the lower useful bound (anything shorter doesn't span a full salary cycle, paycheck URL visits, monthly updates); 730 is the upper sanity bound (two years; longer than that trips "persona was a Windows XP user" realism checks). Every service reads `ctx.persona.timeline_days` — no bare `90` integers, no literal `360` either. `config.yaml::timeline_days: 360` is the default that flows into preset personas; AI-generated personas may override within the Pydantic bounds.

### Consequences

- Per-day rates (ADR-013) replace absolute totals. `config.yaml::artifact_scale` becomes `{per_day: N, jitter: K}` — total = `per_day × timeline_days × (1 ± jitter)`.
- Phase 4 density targets scale with timeline: at 360 days, registry ≥ 5 000 new keys, `$UsnJrnl` ≥ 500 k records, browser history ≥ 30 000 urls.
- Storage cost per artifact category scales linearly. Phase-4 density report must show both per-day rate and totals.
- CI grep-gate: `grep -rn '\b90\b' services/ | grep -iE 'timeline|days|spread'` must return zero.

### Alternatives considered

- **Keep 90 as default, make 360 an opt-in preset**: surfaces the realism problem in every default run; analysts will reach for the opt-in anyway.
- **Parameterise at CLI only, no default**: violates the "sane zero-config" ergonomic — the tool's default should be a realistic persona.

---

## ADR-005 — Single `EventScheduler` owns time and RNG; no service calls `datetime.now()` / `Random(N)` directly

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §7, Phase 4a, ADR-012, A1, A16

### Context

Today every service rolls its own time and randomness. Grep:

```
services/filesystem/*.py        → 40+ datetime.now() calls
services/browser/*.py           → 25+ Random(42) calls
services/eventlog/*.py          → 15+ datetime.utcnow() calls
services/registry/*.py          → 20+ mixed
```

Every service gets a fresh now-ish timestamp inside its own `apply()` call. The result: every service's artifacts carry timestamps within a 1-second window of each other, centred on "when ARC ran", not on "when the persona lived". Cross-domain coherence (the scheduler's event T at 2025-06-12 14:33 UTC producing a 4688 event, a Prefetch bump, a UserAssist counter, a Chrome `visits` row, and a `$UsnJrnl` record all at the same T) is impossible under this model.

Determinism is also broken: multiple services call `Random(42)` separately, so changing one service's draw count shifts no other service's draws — but the aggregate artifact state is not reproducible because the global draw order depends on service execution order, which depends on orchestrator ordering, which can drift.

### Decision

`core/event_scheduler.py::EventScheduler` owns the single master RNG. It emits a deterministic, ordered stream of `SyntheticEvent(kind, timestamp, payload)` over the `[now - timeline_days, now]` window, respecting `persona.active_days`, `persona.work_hours_*`, Poisson intra-session intensity, and locale-aware holiday calendars. Services consume events by kind (`ctx.scheduler.events_of("APP_LAUNCH")`). Each service also receives a deterministic child RNG via `ctx.scheduler.child_rng("registry.userassist")` — child RNGs are seeded from `hash(master_seed ^ hash(name))`, so changing one service's name doesn't shift another service's draws. Direct `datetime.now()` / `datetime.utcnow()` / `Random(literal)` calls are banned in `services/**` and enforced by CI grep-gate (A1).

### Consequences

- Every service sees events that already carry timestamps. Cross-domain coherence becomes: "service A's write of event T must match service B's write of event T". TemporalCoherenceCheck enforces ±2 s / ±5 s tolerances.
- Determinism: same `--random-seed` ⇒ same event stream ⇒ same child RNG seeds ⇒ byte-identical audit log (ADR-012, A16).
- Service migration is the largest single bulk edit in Phase 4a. ~100 grep-matches across 33 services.
- Bootstrap order: orchestrator constructs `ServiceContext` without a scheduler (Phase 1), populates `.scheduler` in the `SCHEDULING` execution phase (Phase 4a), then runs scheduler-aware services.

### Alternatives considered

- **Per-service seeds passed by orchestrator**: doesn't solve "every service uses its own clock"; would still let services drift in time.
- **Single shared clock + per-service RNG passed explicitly**: effectively the same as this decision but without the event stream; services would still have to invent their own event timestamps, defeating coherence.

---

## ADR-006 — `ServiceContext` dataclass replaces `context: dict` in `BaseService.apply()`

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 1, ADR-001, ADR-005

### Context

Every service's signature today is `def apply(self, context: dict, mount: MountManager) -> None`. The dict carries strings keyed by convention (`"profile_type"`, `"installed_apps"`, `"locale"`, ...). Services either assume keys exist (KeyError on missing) or `.get(k, fallback)` with service-local fallbacks. This hides the schema, couples services to the filtered `ProfileContext` shape, and prevents type-checking. After ADR-001 lands, the persona is a typed object; the services should see that shape, plus the scheduler and expansion outputs, via a typed dataclass.

### Decision

```python
@dataclass(frozen=True)
class ServiceContext:
    persona: PersonaContext
    mount: MountManager
    rng: random.Random                       # master child RNG for legacy paths
    scheduler: EventScheduler | None         # None pre-Phase-4a, populated after
    expansion: ExpansionBundle | None        # None pre-Phase-2, populated after
    audit: AuditLogger
    timestamp_service: TimestampService
```

`BaseService.apply(self, ctx: ServiceContext) -> None`. `context["..."]` is banned (CI grep-gate A2). `ctx.scheduler` and `ctx.expansion` are `Optional` only during the bootstrap phases; after Phase 4a they are always populated when services run.

### Consequences

- Compile-time / tool-time type checking for every service field access.
- New services can be written against a documented surface without reading the old code.
- Migration is mechanical: grep `context["X"]` → `ctx.persona.X` / `ctx.mount` / `ctx.rng`.
- `frozen=True` prevents a service from mutating shared state; any per-service state goes into the service's own instance attributes.

### Alternatives considered

- **TypedDict**: gives type hints but not dataclass ergonomics (no frozen, no default factories).
- **Keep `dict`, add a schema validator at the boundary**: retains the runtime KeyError class and loses IDE autocomplete.

---

## ADR-007 — Keep `core/llm_client.py` (local LLM) and `services/ai/gemini_client.py` (Gemini); they have different roles

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §6 Keep list, R10

### Context

Two LLM clients coexist in the repo. `core/llm_client.py` (552 LoC) wraps a local model (Ollama / llama.cpp style) used to generate artifact content bodies — document text, email-like string contents, search queries, small free-text strings. `services/ai/gemini_client.py` (808 LoC) wraps Google Gemini used by `PersonaGenerator` and the seed generators to emit structured JSON matching Pydantic schemas (PersonaContext, DocumentsSeed, BrowsingSeed, DownloadsSeed, FilenamesSeed, MediaSeed, RegistrySeed, EvtxSeed, PrefetchAppSeed).

The temptation is to consolidate into one. Doing so runs into: local LLMs do not reliably emit schema-valid JSON at the size and nesting depth we need (persona is a ~25-field nested object); Gemini is expensive for high-volume unstructured generation (every doc body would burn tokens); Gemini's latency budget is wrong for bulk body synthesis.

### Decision

Both clients stay. Clear division of labour:

- **Gemini (`services/ai/gemini_client.py`)**: structured output with schema validation. One call per persona (→ `PersonaContext`) plus one call per seed type (→ structured seed bundles). ~10 API calls per run.
- **Local LLM (`core/llm_client.py`)**: unstructured artifact content bodies (document prose, email drafts, search-engine query text). Called many times per run (hundreds for a 360-day timeline). Optional — if absent, bodies fall back to template-based synthesis from `templates/documents/*` + wordlist-driven generation.

No cross-dependency: deleting Gemini doesn't break local-LLM flows, and vice versa.

### Consequences

- Two dependencies to maintain, two API surfaces, two sets of prompts. Accepted overhead.
- Local LLM is optional: pure-preset + template-only runs work without either client (no API keys needed for smoke tests).
- Cost profile: Gemini tokens bounded (~10 K tokens per run); local LLM is host-resident and pays no per-call cost.

### Alternatives considered

- **One client using OpenAI's function-calling / JSON-mode + same model for bodies**: possible but locks us into one vendor and conflates the two workloads' latency profiles.
- **Drop local LLM, use Gemini for everything**: unacceptable token cost for 360-day artifact bodies.

---

## ADR-008 — NTFS `$UsnJrnl:$J` writes use ntfs-3g FUSE + USN_RECORD_V3 Python appender

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §7.4, Phase 4b, R1, A6

### Context

The USN Journal is Windows's per-volume change log. Its body lives in the sparse stream `\$Extend\$UsnJrnl:$J`; its header (NextUsn, MaximumSize, AllocationDelta, LowestValidUsn) lives in `\$Extend\$UsnJrnl:$Max`. A typical busy volume has 100 MB – 3 GB of `$J`. Forensics tools (NTFS Triforce, Eric Zimmerman's `MFTECmd`, Sleuth Kit's `usnjls`) cross-validate `$J` against `$MFT` SI times. Without it, ARC's timeline is detectable because there's no journal backing the file creates.

Four ways to write into `$J`:

- **Option A — via libguestfs**: `g.write("/\$Extend\$UsnJrnl:$J", data)` is untested; libguestfs's NTFS driver has known quirks with colon-suffixed stream paths.
- **Option B — via ntfs-3g FUSE**: `open("/mnt/arc/\$Extend\$UsnJrnl:$J", "r+b")` works with the standard ntfs-3g driver because colon-path is the canonical ADS/stream syntax. We append USN_RECORD_V3 records ourselves; we update `$Max.NextUsn` by a matching write.
- **Option C — skip offline, let Windows rebuild on next boot**: produces realistic headers but all records cluster at boot time, not across the 360-day window. Defeats the timeline.
- **Option D — third-party tool**: no mature OSS offline USN writer exists; we'd be writing one ourselves regardless.

### Decision

Option B. `services/ntfs/usn_journal_writer.py` implements a `USN_RECORD_V3` Python binary packer (80-byte fixed header + variable-length filename + 8-byte alignment padding) and appends records directly to `$J` via ntfs-3g. `$Max` is updated with matching `NextUsn`. The write phase runs after guestfs-based file writes have completed and guestfs is unmounted (the appliance must release the VHDX before the host FUSE mount can claim it). `LinuxMountBackend.host_fuse_mount()` / `host_fuse_unmount()` wrap `guestmount` / `guestunmount`.

USN_RECORD_V3 is preferred over V2 because Win11 defaults to V3 for volumes > 128 KB clusters and because V3 carries 128-bit `FileReferenceNumber` / `ParentFileReferenceNumber` fields, which we need to match the `$MFT` entry numbers from libguestfs's file-create.

### Consequences

- Acceptance gate A6: `fsutil usn readjournal C:` inside the booted modified VHDX shows ≥ 500 k records spanning ≥ 300 days.
- `services/ntfs/usn_journal_writer.py` must track the `FileReferenceNumber` of every file libguestfs creates. libguestfs doesn't surface MFT entry numbers in its high-level API; we read them via `tsk_gettimes`-equivalent (fls) on the unmounted image or via re-stat-in-FUSE.
- `$LogFile` remains unhandled in v1 (R4, ADR-009 addendum).
- Determinism holds: `UsnId` sequence is derived from scheduler event order; same seed → same record stream.

### Alternatives considered

Options A, C, D as above. Option A is a future optimisation if libguestfs improves colon-path support (saves the FUSE remount). Option C is a fallback if B proves unstable — gated behind an `--usnjrnl=defer-to-boot` CLI flag.

---

## ADR-009 — $MFT `$STANDARD_INFORMATION` patched via `setfattr system.ntfs_times`; `$FILE_NAME` left at create-time (SI-FN divergence is realistic)

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §7.4, Phase 4b, R2, A7

### Context

Every NTFS file has two timestamp quads in its MFT record: `$STANDARD_INFORMATION` (SI — user-settable, what `GetFileTime` returns) and `$FILE_NAME` (FN — kernel-only, set at file create and on rename; not updatable via any documented API). Forensics tooling (NTFS Triforce methodology, `MFTECmd`) flags mismatches between SI and FN as a "timestomp" signal: if SI was rewritten by SetFileTime, SI and FN diverge.

We need to patch SI so that files created during the ARC run appear to have been created over the 360-day window. FN is harder — `setfattr system.ntfs_times` only touches SI; writing FN requires either direct MFT manipulation (brittle) or renaming the file twice to force a kernel-side FN update (noisy in the journal).

The trick: **SI/FN divergence is normal for legitimately-moved files**. A real user saves `report.docx`, later renames it — SI mtime advances, FN ctime stays at original create. So some fraction of ARC-generated files should show divergence.

### Decision

Patch SI for every scheduler `FILE_CREATE`/`FILE_MODIFY` event using `setfattr -n system.ntfs_times -v 0x<hex>` via ntfs-3g FUSE. Accept that FN stays at the libguestfs-create time (which is "when ARC ran"). The resulting SI/FN divergence — SI in the past, FN at "now-ish" — mimics renamed/moved files. Documented as a known realism signal, not a bug.

For future enhancement: a subset of files can have FN patched by rename-roundtrip (rename to temp, rename back), forcing the kernel to update FN. Not in v1.

### Consequences

- Acceptance A7 reworded: `MFTECmd.exe` on modified VHDX shows matching SI times for ARC-generated files; FN divergence is acceptable and expected.
- The SI/FN pattern is: ARC-created file → SI in the past, FN = "ARC run time" (single cluster, one week before analyst reads). A discerning analyst would see "many files were renamed around the same week" — which itself is plausible (machine reimaging, user cleanup). Not a realism fail for malware-analysis scope.
- `setfattr system.ntfs_times` byte layout is four 64-bit Windows FILETIMEs (atime, mtime, ctime, creation) packed little-endian: 32 bytes prefixed with `0x`. See `docs/research/ntfs_journal.md` §4.3.

### Alternatives considered

- **Full FN rewrite via direct MFT manipulation**: requires parsing and re-emitting MFT entries, maintaining SI/FN consistency with `$UsnJrnl`, preserving name-collision data — months of effort, high-risk for boot damage.
- **Double-rename for FN update**: doubles the journal volume and still leaves FN at last-rename time, which is "ARC run time" — no improvement over the default.

---

## ADR-010 — Hive `.LOG1` / `.LOG2` deleted after hivex commit; Windows rebuilds on next mount

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §7.3, Phase 3, R3, R7, R28

### Context

Windows registry hives (`SOFTWARE`, `SYSTEM`, `NTUSER.DAT`, etc.) are logged: every in-memory write goes first to `HIVE.LOG1` or `HIVE.LOG2` (double-buffered), then flushed into the hive. If Windows is killed mid-write, on next mount the kernel replays unflushed pages from whichever `.LOG{1,2}` has the latest transaction sequence number (`Hvle` signature for newer format / `DIRT` for older), then clears the logs.

hivex writes directly to the hive file and does not produce matching log entries. If the prior `.LOG1`/`.LOG2` still carry an unflushed page that covers a bin we just rewrote, Windows replays the older page on next boot — **silently rolling back our hivex writes**.

Worse: if the log's checksum header is out of sync with the hive's sequence counter, Windows can refuse to mount the hive and bluescreen (R28). Observed in practice on Win10 22H2 when hivex-edited hives were booted without log cleanup.

### Decision

After every `h.commit(None)` + `h.close()` sequence, ARC deletes the corresponding `.LOG1` and `.LOG2` files. Windows rebuilds them (empty, matching checksums) on next mount. Pre-flight check in `LinuxMountBackend`: verify the log files are writable and deletable before any hivex writes; if not, abort with an operator-visible error.

### Consequences

- Eliminates the rollback class of bugs.
- Minor risk: if Windows crashed before the baseline VHDX was cleanly shut down, the logs might carry legitimate unflushed changes we'd be discarding. Mitigated by ADR-003's "baseline is post-clean-shutdown" invariant.
- `SAM`, `SECURITY`, `SOFTWARE`, `SYSTEM`, `DEFAULT`, `NTUSER.DAT` (per-user), `UsrClass.dat` (per-user) each have their own `.LOG1`/`.LOG2` pair. Phase 3's deletion routine walks all of them.
- Audit log records each deletion (or failure). A deletion failure is a WARN, not a FAIL — operator can still boot, at the cost of silent rollback of that hive's writes.

### Alternatives considered

- **Synthesise matching log entries**: requires full hive-log-format reverse engineering (sequence counter, per-page dirty-bit vector, checksum). Weeks of work for a rollback correctness fix.
- **Leave logs, accept rollback**: catastrophically defeats the entire refactor — hivex writes would be silently reverted.

---

## ADR-011 — SMBIOS / MAC / disk-serial spoofing is 100% hypervisor config; ARC only scrubs the registry reflection

- Status: accepted
- Date: 2026-04-19
- Relates to: MP §8, Phase 7 + 8, R5

### Context

VM-detection in malware and sandbox-evasion modules checks multiple layers:

- **Hardware-provided** (CPUID vendor leaf, SMBIOS / DMI strings at memory addresses 0xF0000-0xFFFFF or via `GetSystemFirmwareTable`, NIC MAC OUI, disk serial via `IOCTL_STORAGE_QUERY_PROPERTY`).
- **Kernel-reflected** (the same values mirrored into `HKLM\HARDWARE\DESCRIPTION\System\BIOS`, `HKLM\HARDWARE\DEVICEMAP\Scsi\...`, etc. — volatile keys built at each boot).
- **Driver-provided** (VBox / VMware / QEMU guest additions services, files in `C:\Program Files\Oracle`, `C:\Windows\System32\drivers\VBox*.sys`).

ARC can scrub driver-provided markers (delete service registry keys, remove files). ARC **cannot** change hardware-provided values — those are emitted by the hypervisor at boot; by the time Windows reads them and by the time ARC writes a hive, the hive values are overwritten on next boot because `HKLM\HARDWARE\*` is `HKLM\SYSTEM\CurrentControlSet\Services\mountmgr\...`-volatile. Writing them offline is useless.

### Decision

ARC scrubs the persistent registry surface (services, Uninstall keys, VBox/VMware tool keys, ACPI ID references). The hardware layer — SMBIOS strings, MAC OUI, disk serial, CPUID hypervisor bit — is the operator's hypervisor-config responsibility. ARC ships `examples/libvirt-profile-template.xml` with correct SMBIOS `<sysinfo>`, `<interface><mac>`, `<disk serial=...>`, and `<cpu mode='host-passthrough'>`. VirtualBox users get a README pointer (no `.vbox` template in v1 — documented in Q2, deferred to v1.1 if demand).

### Consequences

- Phase 7 delivers guest-side scrub (registry + file markers).
- Phase 8 delivers `examples/libvirt-profile-template.xml` as the host-side recipe.
- Acceptance A8 (`pafish`/`Al-Khaser` flags ≤ 10) is testable only with both phases applied to a booted VM. The plan's acceptance is: with guest scrubbing alone, expect ~20 flags (still down from ~50+ baseline); with both applied, ≤ 10.
- We document the "no VirtualBox template in v1" gap in the operator runbook.

### Alternatives considered

- **ARC also patches SMBIOS via hypervisor APIs at run time**: not possible offline — those are hypervisor-configured at VM definition / start time, not stored in the VHDX.
- **Ship a `VBoxManage setextradata` script**: possible for VBox but adds a Windows/Mac dep for VBox users; defer to v1.1.

---

## ADR-012 — Determinism is mandatory: same `--random-seed` ⇒ byte-identical audit log

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 4a, A16, Q5

### Context

Two kinds of determinism matter:

1. **Reproducibility**: re-running ARC with the same persona + seed should produce the same VHDX changes. This is tested via the audit log (a JSONL record of every write ARC performed); byte-identical audit logs across two runs prove reproducibility.
2. **Debuggability**: when a realism-check fails, the operator should be able to replay the exact run that produced the failure.

Non-determinism enters via: `datetime.now()`, `Random()` without a seed, iteration order over unordered dicts (Python 3.7+ is insertion-ordered so this is OK), filesystem enumeration order (`os.listdir` is order-dependent on underlying FS — can leak), dict → YAML dump ordering.

### Decision

Same `--random-seed N` + same `PersonaContext` (same preset YAML) must produce byte-identical audit log (`*.audit.jsonl`) across two runs on the same host. The VHDX itself may have non-byte-identical allocator state (block ordering inside NTFS) but all user-visible content (files, keys, records) must match. Enforced by: ADR-005 (scheduler owns time + RNG), child-RNG naming scheme deterministic (`hash(service_name) ^ master_seed`), audit log writes in event-stream order.

### Consequences

- Scheduler implementation is constrained: cannot use set iteration for event ordering.
- Any service that enumerates the filesystem (e.g., Prefetch walker) must sort results explicitly.
- CI gate A16: two runs with same seed → `diff a.audit.jsonl b.audit.jsonl` empty.
- Debugging flow: operator saves `*.audit.jsonl` + seed, passes to ARC team, we reproduce exactly.

### Alternatives considered

- **Non-deterministic, allow replay via audit log only**: loses the reproduction-from-seed path and makes bisection harder.
- **Byte-identical VHDX required**: would require fighting NTFS's own allocator, not achievable without owning the on-disk layout end-to-end.

---

## ADR-013 — `config.yaml::artifact_scale` becomes per-day rates × `timeline_days`, not absolute totals

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 2, Phase 4c, R12, ADR-004

### Context

Current `config.yaml`:

```yaml
artifact_scale:
  downloads: {target_total: 1500}
  documents: {target_total: 4500}
  browser_history: {target_total: 7500}
```

Hard-coded to the old 90-day timeline. When timeline_days becomes 360 (ADR-004), these numbers are silently 4× too low. Changing timeline without rescaling is a common footgun.

### Decision

Convert to per-day rates with jitter:

```yaml
artifact_scale:
  downloads:        {per_day: 4.2,  jitter: 0.3}
  documents:        {per_day: 12.5, jitter: 0.3}
  pictures:         {per_day: 2.1,  jitter: 0.4}
  browser_history:  {per_day: 83,   jitter: 0.5}
  search_terms:     {per_day: 16.7, jitter: 0.5}
  bookmarks:        {per_day: 0.56, jitter: 0.2}
```

Target count = `per_day × persona.timeline_days × (1 + rng.uniform(-jitter, +jitter))`. Rates are derived from SANS DFIR reference numbers and from running the baseline Win11 install for a week on a test user (documented in `docs/research/windows_artifact_baselines.md`).

### Consequences

- Changing `timeline_days` scales artifact volumes linearly — no other edits needed.
- Jitter ensures two personas with the same archetype don't produce identical volumes (adds realism across a fleet).
- Any downstream code that assumed "total" as a fixed integer must be rewritten to resolve from `per_day × timeline_days`.

### Alternatives considered

- **Keep absolute totals, scale them via CLI multiplier**: re-introduces magic numbers and moves the realism logic out of config.
- **Rates as hard-coded in services**: opaque; operators want to tune these per engagement.

---

## ADR-014 — `services/generators/` renamed to `services/expansion/`

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 2

### Context

Two folders with similar names exist:

- `services/generators/` — bulk expansion modules (`bulk_documents.py`, `bulk_downloads.py`, `bulk_browsing.py`, …) that take seeds and produce large lists of artifacts. Not in the main pipeline today.
- `services/browser/generators/` — Chromium SQLite table writers (history, cookies, downloads) that already work.

Grep / IDE jumps to "generators" land in the wrong place half the time. Future readers will be confused.

### Decision

Rename `services/generators/` → `services/expansion/`. Update all imports (`from services.generators.X import Y` → `from services.expansion.X import Y`). Git-mv preserves history.

### Consequences

- One mechanical rename pass.
- `services/browser/generators/` stays as-is (it's a narrow Chromium-specific thing, name fits).
- No behavioural change.

### Alternatives considered

- **Rename `services/browser/generators/` instead**: touches more files, less semantically clean (Chromium generators *are* generators of SQLite state).

---

## ADR-015 — Tests migrated in their own phase with CI grep-gates; not a "rerun" afterthought

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 9, R13

### Context

~8 000 LoC of tests across `tests/test_core/`, `tests/test_services/`, `tests/test_evaluation/` consume the old `context["..."]` dict shape and construct `ProfileContext` directly. If we migrate code first and defer test migration, `pytest` is red for the entire refactor and loses its signal value. If we migrate tests first, we're testing code that doesn't exist yet.

Doing them service-by-service in Phase 1 is tempting but drags the pace — every service's migration PR would carry 2-5 test files.

### Decision

Dedicated Phase 9 at the end of the code-change sequence. It does:

1. Bulk-migrate `tests/**` to `ServiceContext`, `PersonaContext`, `EventScheduler` stubs.
2. Delete tests for deleted classes (`test_profile_engine.py`, `test_vm_manager.py`, `test_orchestrator_profile_variant.py`).
3. Add new tests for new components (`test_persona_loader.py`, `test_linux_mount.py`, `test_event_scheduler.py`, `test_temporal_coherence.py`, `test_mft_timestamp_patcher.py`, `test_usn_journal_writer.py`, `test_mac_hygiene.py`).
4. Wire CI grep-gates (A1, A2, A3, A4) + `pytest -x`.

Between Phase 1 and Phase 9, `pytest` is expected to be broken in parts; each intermediate phase's acceptance criteria are service-level, not test-suite-green.

### Consequences

- One concentrated test migration pass = easier review, clearer diffs.
- Risk: during Phases 1-8, regressions in existing-and-still-correct behaviour aren't caught by tests. Mitigation: every phase must include smoke-test CLI invocations in its acceptance (Phase 1's `python main.py --preset home_user --dry-run completes`, Phase 3's `python -c "from core.linux_mount import ...; mount/unmount"`, etc.).
- Phase 9 is likely the largest single test-file churn in project history. Budget accordingly.

### Alternatives considered

- **Test-first per phase**: idealistic; in practice each phase would stall on test framework churn.
- **Keep old tests passing via adapter shim**: adds code that exists only to support dying tests.

---

## ADR-016 — Every research doc goes into git under `docs/research/`; `.gitignore` must not exclude `docs/`

- Status: accepted
- Date: 2026-04-19
- Relates to: Phase 0, R24, A17

### Context

Claude / assistant context compacts. Research discussed in chat is lost. The MASTER_PLAN is terse by design (cross-reference style). The detailed reasoning — *why* the mount strategy is libguestfs + ntfs-3g, *what* the NTFS journal byte layout is, *which* Windows artifact densities are real — must live on disk or the next refactor re-derives it from scratch.

### Decision

All research is committed under `docs/research/*.md`. The ADR log is `docs/design/decisions.md`. `docs/MASTER_PLAN.md` is the terse authoritative plan; `docs/research/00_pre_execution_brief.md` is the operator-facing walkthrough. `.gitignore` must **not** exclude `docs/` (currently it doesn't, but is called out here as a permanent invariant). Research-doc edits land in normal PRs; no out-of-band markdown.

Phase 0 produces the initial set:

- `docs/research/mount_strategy.md`
- `docs/research/ntfs_journal.md`
- `docs/research/time_integrity.md`
- `docs/research/vm_detection_evasion.md`
- `docs/research/windows_artifact_baselines.md`
- `docs/design/decisions.md` (this file)

### Consequences

- New-engineer onboarding path: read `docs/MASTER_PLAN.md` → read `docs/research/00_pre_execution_brief.md` → read `docs/research/<topic>.md` as needed.
- Future refactors start from a written base, not from chat memory.
- Acceptance A17: these six files exist and are committed before Phase 1 starts.

### Alternatives considered

- **Wiki / Notion / external**: loses co-versioning with code; doc drift inevitable.
- **Inline everything in `docs/MASTER_PLAN.md`**: 5× the file length, harder to navigate, kills the "terse authoritative plan" design.

---

## ADR-017 — Dual-boot NTFS direct mount via ntfs-3g replaces VHDX + libguestfs

- Status: accepted
- Date: 2026-04-27
- Relates to: ADR-002 (supersedes mount layer), ADR-003 (updates baseline concept), Phase 3, Phase 8

### Context

The original ADR-002 specified libguestfs (QEMU appliance) for VHDX container handling, with a
second FUSE phase via `guestmount` for ADS/xattr writes. The deployment target has shifted from
a standalone VHDX image to a dual-boot machine where Windows 11 and Ubuntu coexist on the same
hardware. The Windows NTFS partition is directly accessible from Ubuntu as a block device
(`/dev/nvme0n1p3` or similar).

Key problems with the VHDX + libguestfs approach on this target:
- libguestfs adds ~30 s QEMU appliance startup per run; no benefit when a real partition is
  mounted in ~1 s.
- The two-phase sequence (guestfs Phase A + guestmount Phase B) was an artefact of guestfs's
  limitations with ADS/xattr. ntfs-3g mounted directly supports both in one phase.
- libguestfs requires additional system packages (~300 MB) and Python bindings that are
  awkward to install on Ubuntu 24.04.
- The VHDX image concept is unnecessary — the real Windows partition on the dual-boot machine
  IS the baseline; no copy/move needed.

### Decision

`LinuxMountBackend` mounts the Windows NTFS partition directly via ntfs-3g FUSE
(`mount -t ntfs-3g ... -o uid=...,streams_interface=windows,allow_other`). libguestfs is removed
entirely. The two-phase mount sequence collapses to a single mount. `host_fuse_mount()` returns
the existing mount point (no second subprocess call). The partition path is configured in
`config.yaml::windows_partition` and overrideable via `--partition` CLI flag.

### Consequences

- `LinuxMountBackend` constructor changes from `vhdx_path: Path` to `partition: str, mount_point: Path`.
- All file I/O methods (`read_bytes`, `write_bytes`, `mkdir_p`, etc.) use standard Python `Path`
  operations on the mount point instead of guestfs API calls.
- `hivex` still opens a `/tmp` copy of each hive for crash-safety; the copy source is now the
  mounted path directly (no `g.read_file()` call needed).
- `apt install libguestfs-tools python3-guestfs guestmount` removed from setup instructions.
- `scripts/build_baseline_vhdx.sh` and `examples/unattend.xml` removed (Phase 8 becomes a
  dual-boot setup checklist, not a VM-build script).
- Pre-flight: `LinuxMountBackend.mount()` calls `ntfsfix --clear-dirty` check before mounting;
  raises `LinuxMountBackendError` if the volume is dirty.
- Startup overhead: ~1 s (ntfs-3g mount) vs ~30 s (libguestfs QEMU appliance).

### Alternatives considered

- **Keep libguestfs, add direct-mount as fallback**: unnecessary complexity; the direct mount
  is strictly better on the new target.
- **Use in-kernel ntfs3 driver**: does not support `streams_interface=windows` for ADS colon-path
  access; `system.ntfs_times` xattr is undocumented in kernel ntfs3. ntfs-3g required.
- **qemu-nbd to expose VHDX then ntfs-3g**: fragile kernel module dependency; no benefit if we
  target a real partition.

---

## Index

| ADR | Title | Date | Status |
| --- | ----- | ---- | ------ |
| 001 | Unify on `PersonaContext`; delete `ProfileContext` | 2026-04-19 | accepted |
| 002 | Linux host only; ntfs-3g direct mount + hivex (mount layer superseded by ADR-017) | 2026-04-19 | accepted |
| 003 | Windows partition post-OOBE, fully shut down; offline-inject only | 2026-04-19 | accepted |
| 004 | Default timeline = 360 days; bounds 30..730 | 2026-04-19 | accepted |
| 005 | Single `EventScheduler` owns time + RNG | 2026-04-19 | accepted |
| 006 | `ServiceContext` dataclass replaces `context: dict` | 2026-04-19 | accepted |
| 007 | Keep local LLM + Gemini clients; different roles | 2026-04-19 | accepted |
| 008 | `$UsnJrnl:$J` via ntfs-3g FUSE + USN_RECORD_V3 appender | 2026-04-19 | accepted |
| 009 | SI patched; FN left at create-time (divergence is realistic) | 2026-04-19 | accepted |
| 010 | Hive `.LOG1`/`.LOG2` deleted after hivex commit | 2026-04-19 | accepted |
| 011 | SMBIOS/MAC/disk-serial is hypervisor config; ARC scrubs registry only | 2026-04-19 | accepted |
| 012 | Determinism mandatory: same seed ⇒ byte-identical audit log | 2026-04-19 | accepted |
| 013 | `artifact_scale` is per-day rates × timeline_days | 2026-04-19 | accepted |
| 014 | Rename `services/generators/` → `services/expansion/` | 2026-04-19 | accepted |
| 015 | Test migration is its own phase (Phase 9) with CI grep-gates | 2026-04-19 | accepted |
| 016 | Research committed under `docs/research/`; `.gitignore` keeps docs | 2026-04-19 | accepted |
| 017 | Dual-boot NTFS direct mount via ntfs-3g replaces VHDX + libguestfs | 2026-04-27 | accepted |

---

**Next ADR ID**: 018.

When adding: append the body, add an Index row, update `docs/MASTER_PLAN.md` §4 if the decision changes the refactor plan, and cross-link from the relevant research doc.
