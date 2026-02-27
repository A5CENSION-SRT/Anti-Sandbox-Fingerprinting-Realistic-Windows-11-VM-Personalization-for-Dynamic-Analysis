
## Team Split (3 Members)

### Sumukha — Core Infrastructure + Filesystem
**Why:** Everything else depends on this layer. Gets the foundation working first.

| Week | Tasks |
|------|-------|
| **W1** | `core/` — profile_engine, identity_generator, timestamp_service, mount_manager, audit_logger |
| **W1** | `profiles/` — base.yaml + all 3 profile definitions |
| **W1** | `services/base_service.py` — abstract base class contract |
| **W2** | `services/filesystem/` — cross_writer, user_directory, document_generator, media_stub |
| **W3** | `services/filesystem/` — prefetch, thumbnail_cache, recent_items, recycle_bin |
| **W3** | `data/` — wordlists, filenames, folder names |
| **W4** | `main.py` + `orchestrator.py` — CLI + wiring all services together |
| **W4** | Integration testing with B and C's services |

**Owns:** `core/`, `services/filesystem/`, `profiles/`, `data/`, `main.py`

---

### Snehal — Registry + Anti-Fingerprint + Event Logs
**Why:** These are the deepest Windows internals — one person should own the binary format knowledge.

| Week | Tasks |
|------|-------|
| **W1** | Research: `python-registry` / `regipy` for offline hive editing, EVTX format |
| **W1** | `services/registry/hive_writer.py` — core read/write on NTUSER.DAT, SOFTWARE, SYSTEM |
| **W2** | `services/registry/` — system_identity, installed_programs, network_profiles |
| **W2** | `services/registry/` — mru_recentdocs, userassist (ROT13 encoding) |
| **W3** | `services/eventlog/` — evtx_writer, system_log, security_log, application_log |
| **W3** | `services/eventlog/update_artifacts.py` |
| **W4** | `services/anti_fingerprint/` — vm_scrubber, hardware_normalizer, process_faker |
| **W4** | `templates/registry/`, `data/hardware_models.json`, `data/kb_updates.json` |

**Owns:** `services/registry/`, `services/eventlog/`, `services/anti_fingerprint/`

---

### Raghottam — Browser + Applications + Evaluation
**Why:** Browser artifacts are SQLite-heavy and self-contained. Evaluation ties everything together at the end.

| Week | Tasks |
|------|-------|
| **W1** | Research: Chrome/Edge SQLite schemas (History, Cookies, Login Data) |
| **W1** | `services/browser/browser_profile.py` — profile dir structure + config JSONs |
| **W2** | `services/browser/` — history, cookies_cache, bookmarks, downloads |
| **W2** | `templates/browser/` — bookmark templates per profile |
| **W2** | `data/urls_by_category.json`, `data/search_terms.txt` |
| **W3** | `services/applications/` — office_artifacts, dev_environment, email_client, comms_apps |
| **W3** | `templates/documents/` — document templates |
| **W4** | `evaluation/` — consistency_checker, density_analyzer, sandbox_signal_tester, report_generator |
| **W4** | `docs/evaluation_report.md` — final deliverable report |

**Owns:** `services/browser/`, `services/applications/`, `evaluation/`, `templates/`

---

## Shared Responsibilities (All 3)

| Task | Who |
|------|-----|
| `tests/` | Each member writes tests for their own services |
| `docs/architecture.md` | Member A drafts, all review |
| `docs/profile_schema.md` | Member A writes |
| `docs/change_log_format.md` | Member A writes |
| `docs/evaluation_report.md` | Member C writes, all contribute findings |
| Code reviews | Rotate — each PR reviewed by one other member |
| Integration testing | Week 4 — all three together |

---

## Week-by-Week Timeline

```
Week 1 ──  A: core infrastructure + base service contract
           B: registry research + hive_writer foundation
           C: browser research + browser_profile setup
           MILESTONE: Can load a profile, generate identity, write to mount

Week 2 ──  A: filesystem artifacts (docs, media, user dirs)
           B: registry services (identity, programs, MRU, UserAssist)
           C: browser artifacts (history, cookies, bookmarks, downloads)
           MILESTONE: Each layer independently generates artifacts

Week 3 ──  A: remaining filesystem (prefetch, thumbnails, recent items)
           B: event logs + update artifacts
           C: application artifacts (Office, dev tools, email, comms)
           MILESTONE: Full artifact coverage for one profile

Week 4 ──  A: orchestrator + CLI + integration wiring
           B: anti-fingerprint services + final registry polish
           C: evaluation suite + report
           ALL: integration testing, cross-review, documentation
           MILESTONE: End-to-end run, evaluation report complete
```

---

## Dependency Rules

- **B and C can start in Week 1** using hardcoded test data while A builds the core
- **By end of Week 1**, A delivers `ProfileContext`, `TimestampService`, and `MountManager` so B and C can integrate
- **All services inherit from `base_service.py`** and follow the same interface:
  ```python
  class BaseService:
      def __init__(self, profile, timestamp_svc, mount_mgr, audit_log): ...
      def generate(self) -> None: ...
      def get_dependencies(self) -> list[str]: ...
  ```
- **No service writes directly to disk** — always through `MountManager` + `CrossWriter`
