# ARC — Service Changelog Format

Format for recording service additions, removals, and breaking changes.

---

## Entry Format

```
## vX.Y — YYYY-MM-DD

### Added
- `services/<category>/<module>.py` — <ServiceClass>: one-line description

### Changed
- `services/<category>/<module>.py` — <what changed and why>

### Removed
- `services/<category>/<module>.py` — <ServiceClass>: reason

### Breaking
- <what breaks and how to migrate>
```

---

## Changelog

### Phase 4a — 2025-xx-xx

**Added**
- `services/registry/typing_history.py` — TypingHistory: TypedURLs, TypedPaths,
  WordWheelQuery from scheduler events (A10 gate)
- `services/filesystem/prefetch.py` — PrefetchService: SCCA v30 binary .pf files, ≥30
  files at ≥15 KB each (A11 gate)
- `services/eventlog/evtx_writer.py` — EvtxWriter: multi-chunk binary EVTX writer with
  EvtxRecord model (A12 gate)
- `services/eventlog/security_log.py`, `system_log.py`, `application_log.py`,
  `update_artifacts.py` — per-channel EVTX services
- `services/browser/history.py` — BrowserHistoryService: Chrome History SQLite ≥5 000 URLs
  / ≥10 000 visits (A13 gate)
- `services/filesystem/document_generator.py` — DocumentGenerator: valid DOCX/XLSX/PDF +
  txt/md/py/json, ≥500 files at 360 days (A14 gate)
- `services/ntfs/mft_timestamp_patcher.py` — SI timestamp patching via setxattr
- `services/ntfs/usn_journal_writer.py` — $UsnJrnl:$J USN_RECORD_V2 appender
- `services/anti_fingerprint/vm_scrubber.py` — VBox/VMware/QEMU/KVM service key removal
- `services/anti_fingerprint/hardware_normalizer.py` — BIOS/SCSI/GPU string replacement
- `services/anti_fingerprint/mac_hygiene.py` — NIC OUI override
- `services/expansion/orchestrator.py` — ExpansionOrchestrator producing ExpansionBundle

**Changed**
- `core/event_scheduler.py` — single source of time; all services consume child_rng from
  scheduler instead of `Random(N)` (ADR-005, ADR-012)
- `core/service_context.py` — added `scheduler` and `expansion` optional fields
- `core/orchestrator.py` — added SCHEDULING and EXPANSION phases before artifact services
- All artifact services — migrated to `ctx.scheduler.child_rng("<service>")` pattern

**Breaking**
- Services may no longer call `datetime.now()` directly (CI gate A1). Use
  `ctx.timestamp_service` or consume scheduler events.
- Services may no longer access context fields as `ctx["key"]` (CI gate A4). Use
  `ctx.persona.<field>`.
- Preset YAMLs must validate as full `PersonaContext` (extra fields raise `ValidationError`).

---

### Phase 3 — 2025-xx-xx

**Added**
- `core/linux_mount.py` — LinuxMountBackend: libguestfs + hivex + guestmount/ntfs-3g (ADR-002)
- `core/persona_context.py` — unified 25+ field Pydantic PersonaContext replacing 6-field
  ProfileContext (ADR-001)
- `services/registry/hive_writer.py` — hivex-backed HiveWriter; deleted `.LOG1`/`.LOG2`
  to prevent rollback (ADR-010)

**Removed**
- Old Windows-only `Z:` drive mount path
- `ProfileContext` (6-field, `allowed_fields` silent filter)

---

### Phase 0 — 2025-xx-xx

**Added**
- `docs/research/` — five research docs: mount_strategy, ntfs_journal, time_integrity,
  vm_detection_evasion, windows_artifact_baselines
- `docs/design/decisions.md` — ADR-001 through ADR-016
