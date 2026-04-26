# ARC — Development Log

Chronological record of key implementation decisions and phase completions.

---

## Phase 6 — Documentation refresh (2026-04)

- Created `docs/wizard_guide.md`: Linux end-to-end CLI and wizard workflow guide.
- Created `docs/evaluation_report.md`: density baselines, coherence scores, sandbox signal results.
- Created `docs/change_log_format.md`: per-service changelog entries for Phases 0, 3, 4a.
- Rewrote `agents.md`: ARC service agent reference replacing generic AI coding prompt.
- Rewrote `report.md`: clean academic paper removing LLM filler artifacts.
- Updated `install_deps.sh`: added `apt` system package block.
- Created `log.md` (this file).
- `docs/architecture.md`, `docs/profile_schema.md`, `project_structure.md` verified accurate — no changes needed.

---

## Phase 4a — Scheduler migration + density gates (2025-xx)

- Migrated all services to `ctx.scheduler.child_rng()` (ADR-005, ADR-012).
- A1 CI gate: broadened pattern to catch `datetime.now(tz)` calls; excluded legitimate
  bootstrap files (`event_scheduler.py`, `orchestrator.py`, `audit_logger.py`).
- A3 CI gate: narrowed from broad PowerShell keyword match to subprocess-invocation patterns
  only (was falsely matching class names and seed data strings).
- Fixed `document_generator.py:563` A1 violation: removed `datetime.now()` fallback when
  both `install_time` and `now` are None.
- Fixed `hardware_normalizer.py` SyntaxWarning: escaped `\S` in docstring.
- Fixed `test_download_service.py` Zone.Identifier count: Linux ext4 creates
  `file.exe:Zone.Identifier` as a real filename; filter updated to `"Zone.Identifier" not in
  f.name`.
- A12 test: 5500 records only produced 4.1 MB; updated to 14 000 records (~770 bytes/record)
  to guarantee ≥10 MB.
- Full test suite: 848 passed, 99 skipped, 0 failed.
- New test files: `test_document_generator.py` (11 tests), `test_prefetch_service.py` (6 tests).
- Added TestA11Density, TestA12Density, TestA13Density, TestA14 density gate tests.

---

## Phase 4 — Preset profiles + random seed support (2025-xx)

- Added `profiles/presets/developer.yaml`, `office_user.yaml`, `home_user.yaml`.
- Added `--preset` and `--random-seed` CLI flags.
- `EventScheduler` seeds child RNGs via `hash(master_seed ^ hash(name))` so service
  sequences are independent; adding/removing a service does not shift other services' RNG.

---

## Phase 3 — Linux mount backend (2025-xx)

- Implemented `LinuxMountBackend` (libguestfs + hivex + guestmount/ntfs-3g).
- Replaced all Windows-only Z: drive paths.
- Unified `PersonaContext` schema (ADR-001): 25+ fields, frozen Pydantic, `extra="forbid"`.
- HiveWriter deletes `.LOG1`/`.LOG2` after hive commit to prevent Windows rollback (ADR-010).

---

## Phase 0 — Research archive (2025-xx)

- Created `docs/research/`: mount_strategy, ntfs_journal, time_integrity,
  vm_detection_evasion, windows_artifact_baselines.
- Created `docs/design/decisions.md`: ADR-001 through ADR-016.
