# Arc — Anti-Sandbox Personalizer: Progress Report
_Last updated: March 2026_

---

## What This Project Does

Arc is a **Windows forensic artifact generation tool**. Given a mounted disk image (or a directory acting as one), it populates the image with realistic, profile-driven Windows artifacts — browser history, registry keys, downloaded files, event logs, application data — making a clean/sandbox VM look like a real, lived-in machine.

Three user personas are supported: `office_user`, `developer`, and `home_user`. Each persona drives different browsing categories, installed apps, work-hour patterns, and file types.

---

## Architecture Overview

```
main.py (CLI)
    └── core/orchestrator.py          ← wires and sequences all services
            ├── core/profile_engine.py        ← loads profile YAML → ProfileContext
            ├── core/identity_generator.py    ← fake name, email, machine GUID, HW strings
            ├── core/timestamp_service.py     ← master timeline (realistic time distribution)
            ├── core/mount_manager.py         ← path resolution, safety guards
            ├── core/audit_logger.py          ← structured JSON log of every write
            │
            ├── services/filesystem/…         ← directory scaffold, files, timestamps
            ├── services/registry/…           ← offline hive read/write (NTUSER, SOFTWARE, SYSTEM)
            ├── services/browser/…            ← Chrome/Edge SQLite history, downloads, bookmarks
            ├── services/eventlog/…           ← .evtx binary event logs
            ├── services/applications/…       ← Office, VS Code, Outlook, Teams traces
            └── services/anti_fingerprint/…   ← VM indicator removal
```

All services follow a common contract: they receive dependency-injected `mount_manager`, `timestamp_service`, and `audit_logger`, then expose `apply(context: dict)`. The `Orchestrator` calls them in dependency order.

---

## ✅ What Has Been Implemented

### Core Layer (`core/`)
| File | Status | Notes |
|---|---|---|
| `profile_engine.py` | ✅ Done | Loads `base.yaml` + profile YAML, merges them, returns a `ProfileContext` dataclass |
| `identity_generator.py` | ✅ Done | Generates fake name, email, org, machine GUID, BIOS serial, disk serial |
| `timestamp_service.py` | ✅ Done | Produces `created/modified/accessed` timestamps for given event types |
| `mount_manager.py` | ✅ Done | Resolves paths relative to mount root, path-escape checks |
| `audit_logger.py` | ✅ Done | Writes structured JSON audit entries for every service operation |
| `orchestrator.py` | ❌ **Empty** | The file exists but has no content — nothing wires the services together yet |

---

### Filesystem Service (`services/filesystem/`)
| File | Status | Notes |
|---|---|---|
| `cross_writer.py` | ✅ Done | Recursive dir/file writer; validates schema, applies timestamps & win32 attributes atomically |
| `user_directory.py` | 🔲 Stub | File exists, no implementation |
| `document_generator.py` | ❌ **Empty** | Meant to produce `.docx`, `.xlsx`, `.pdf`, `.txt` with metadata |
| `media_stub.py` | ❌ **Empty** | Meant to produce JPEG/PNG with EXIF data |
| `prefetch.py` | 🔲 Stub | Synthetic `.pf` Prefetch files for `C:\Windows\Prefetch` |
| `thumbnail_cache.py` | 🔲 Stub | `thumbcache_*.db` and `IconCache.db` |
| `recent_items.py` | 🔲 Stub | `.lnk` shortcut files and Jump Lists |
| `recycle_bin.py` | 🔲 Stub | `$Recycle.Bin` `$I`/`$R` paired artifacts |

---

### Registry Service (`services/registry/`)
| File | Status | Notes |
|---|---|---|
| `hive_writer.py` | ✅ Done | Core offline hive binary patcher; reads with `regipy`, writes via in-place binary patching. Supports `REG_SZ`, `REG_EXPAND_SZ`, `REG_DWORD`, `REG_QWORD`, `REG_BINARY`, `REG_MULTI_SZ`. `delete_value` and `delete_key` operations are stubbed (logged but skipped). Cannot create **new** keys — only patches existing values in pre-installed hive templates. |
| `installed_programs.py` | ✅ Done | Writes Uninstall entries for 12 programs (Office suite, VS Code, Docker, Git, Chrome, Spotify, VLC, etc.) — deterministic GUIDs and install dates from SHA-256 |
| `mru_recentdocs.py` | ✅ Done | Writes `RecentDocs`, `OpenSaveMRU`, `LastVisitedMRU` keys |
| `userassist.py` | ✅ Done | ROT-13-encoded execution trace entries in `UserAssist` |
| `system_identity.py` | ✅ Done | BIOS, disk serial, `ProductId`, machine GUID |
| `network_profiles.py` | ✅ Done | Saved Wi-Fi and LAN network connection profile keys |

> **Key limitation of `hive_writer.py`:** It can only overwrite existing values (in-place binary patch). It cannot allocate new hive cells to create new keys. This means the target disk image must have the relevant registry keys pre-existing (normal for a freshly installed Windows image). True key creation would require a full hive re-serialization library or using tools like `hivex`/`python-registry-tools` with write support.

---

### Browser Service (`services/browser/`)
| File | Status | Notes |
|---|---|---|
| `browser_profile.py` | ✅ Done | Creates Chrome/Edge profile directory structure and `Preferences` JSON |
| `history.py` | ✅ Done | Creates `History` SQLite DB with correct Chrome schema; populates `urls`, `visits`, `keyword_search_terms` tables |
| `downloads.py` | ✅ Done | Populates `downloads` and `downloads_url_chains` tables in the existing History DB; creates zero-byte placeholder files in `Downloads/` folder |
| `bookmarks.py` | 🔲 Stub | File exists, no implementation |
| `cookies_cache.py` | ❌ **Empty** | Cookie DB + Cache index stubs |
| `generators/schema.py` | ✅ Done | Chrome History SQL schema, version constants |
| `generators/visit_generator.py` | ✅ Done | Assigns per-URL visit counts, session grouping, hour-bias, day-of-week weighting |
| `generators/search_term_generator.py` | ✅ Done | Inserts `keyword_search_terms` rows for realistic-looking searches |
| `generators/download_generator.py` | ✅ Done | Download catalogue loader, profile-aware selection, placeholder file creator |
| `generators/bookmark_enricher.py` | ✅ Done | Bookmark metadata enricher |
| `generators/config_generator.py` | ✅ Done | Chrome Preferences JSON generator |
| `utils/` | ✅ Done | `chrome_timestamps.py`, `constants.py`, `url_loader.py` |

---

### Event Log Service (`services/eventlog/`)
| File | Status | Notes |
|---|---|---|
| `evtx_writer.py` | ❌ **Empty** | Core `.evtx` binary writer — nothing implemented |
| `system_log.py` | ❌ **Empty** | `System.evtx` boot/service/driver events |
| `security_log.py` | ❌ **Empty** | `Security.evtx` logon/logoff events |
| `application_log.py` | ❌ **Empty** | `Application.evtx` app errors and install records |
| `update_artifacts.py` | ❌ **Empty** | `SoftwareDistribution`, `CBS.log`, KB traces |

---

### Applications Service (`services/applications/`)
| File | Status | Notes |
|---|---|---|
| `office_artifacts.py` | ❌ **Empty** | Office MRU + temp/recovery files |
| `dev_environment.py` | ❌ **Empty** | VS Code settings, Git config, Python/Node caches |
| `email_client.py` | ❌ **Empty** | Outlook `.ost` stub / Thunderbird `prefs.js` |
| `comms_apps.py` | ❌ **Empty** | Teams / Slack / Discord / Zoom trace files |

---

### Anti-Fingerprint Service (`services/anti_fingerprint/`)
| File | Status | Notes |
|---|---|---|
| `vm_scrubber.py` | ❌ **Empty** | Remove VirtualBox/VMware indicators |
| `hardware_normalizer.py` | ❌ **Empty** | Realistic SMBIOS/WMI/GPU strings |
| `process_faker.py` | ❌ **Empty** | Service entries, SRUM database stubs |

---

### Evaluation (`evaluation/`)
| File | Status | Notes |
|---|---|---|
| `consistency_checker.py` | 🔲 Stub | MRU → file cross-reference checks, timeline sanity |
| `density_analyzer.py` | 🔲 Stub | File/key/event count vs reference baselines |
| `sandbox_signal_tester.py` | 🔲 Stub | Common VM detection signal checklist |
| `report_generator.py` | 🔲 Stub | Produces final evaluation report |

---

### Tests (`tests/`)
| Test File | Coverage |
|---|---|
| `test_browser_history.py` | ✅ Comprehensive — schema, visits, search terms, multi-browser |
| `test_cross_writer.py` | ✅ Comprehensive — schema validation, atomicity, path escape, attributes |
| `test_registry_writer.py` | ✅ Comprehensive — all value types, read/write round-trips |
| `test_system_identity.py` | ✅ Comprehensive |
| `test_installed_programs.py` | ✅ Comprehensive — catalog, GUID derivation, operations |
| `test_userassist.py` | ✅ Comprehensive |
| `test_mru_recentdocs.py` | ✅ Comprehensive |
| `test_network_profiles.py` | ✅ Comprehensive |
| `test_download_catalogue.py` | ✅ Done — catalogue loading and selection |
| `test_download_service.py` | ✅ Done — DB insertion, filesystem stubs |
| `test_browser_downloads.py` | 🔲 Minimal (placeholder) |
| `test_document_generator.py` | ❌ Empty |
| `test_evtx_writer.py` | ❌ Empty |
| `test_core/` | ✅ Done — profile engine, timestamp service, identity generator |
| `test_evaluation/` | 🔲 Placeholder |

---

## ❌ What Needs to Be Implemented

### Priority 1 — Core Wiring (Blocker)

**`core/orchestrator.py`** (currently empty)

This is the most critical missing piece. It must:
1. Accept CLI arguments (`--mount`, `--profile`, `--dry-run`)
2. Instantiate `MountManager`, `TimestampService`, `AuditLogger`, `IdentityGenerator`, `ProfileEngine`
3. Resolve the `ProfileContext` for the chosen persona
4. Instantiate all services in dependency order (e.g. `CrossWriter` → `BrowserHistoryService` → `BrowserDownloadService`)
5. Call each service's `apply(context)` with a merged context dict
6. Log a summary and exit cleanly

Without this, the tool cannot be run end-to-end.

---

### Priority 2 — Event Logs (High value for realism)

**`services/eventlog/evtx_writer.py`** and all log services are empty.

The `.evtx` format is a custom binary XML (WEVT) format. Options:
- Use the `python-evtx` library for reading; for writing, `python-evtx` has no write API.
- Use **`libevtx-python` (libevtx)** which is read-only.
- Use **`evtx_dump` + manual struct packing** — complex.
- Recommended approach: use **`winevt-logger`** or write events by constructing raw binary `EVTX_CHUNK` / `EVTX_RECORD` structs (there's a known serialization format). Alternatively, generate XML event records and forward them to a real Windows evtlog API when running on Windows.

Each log service needs to generate:
- `system_log.py`: Boot events (Event ID 6013, 6005), service starts, driver loads
- `security_log.py`: Logon (4624), Logoff (4634), failed logon (4625)
- `application_log.py`: App install records, crash reports

---

### Priority 3 — Application Traces (High value for profiles)

**`services/applications/`** — all 4 files are empty:

- `office_artifacts.py`: Create Office MRU registry entries + `~$` temp file stubs in `%APPDATA%\Microsoft\Office\Recent`
- `dev_environment.py`: Write `.gitconfig`, VS Code `settings.json`, `extensions/`, `%APPDATA%\npm`, Python `pip` cache, etc.
- `email_client.py`: Stub an Outlook `.ost` file (zero-byte or minimal header) in `%LOCALAPPDATA%\Microsoft\Outlook\`; Thunderbird `prefs.js` with realistic account config
- `comms_apps.py`: Drop Teams `settings.json`, Slack workspace IDs, Discord `settings.json`, Zoom `zoomus.conf`

---

### Priority 4 — Filesystem Generators

**`services/filesystem/document_generator.py`**: Generate realistic `.docx`/`.xlsx`/`.pdf`/`.txt` files with embedded metadata (author, company, last-modified date). Use `python-docx` for DOCX, `openpyxl` for XLSX, `reportlab` for PDF.

**`services/filesystem/media_stub.py`**: Generate JPEG/PNG files with realistic EXIF tags (camera model, GPS, date). Use `Pillow` + `piexif`.

**`services/filesystem/prefetch.py`**: Synthetic `.pf` files in `C:\Windows\Prefetch`. The Prefetch format is documented; minimal headers with correct checksums can be crafted.

**`services/filesystem/recent_items.py`**: `.lnk` shortcut files using `pylnk3` or `shlobj` COM objects. Jump-list `.automaticDestinations` files.

**`services/filesystem/recycle_bin.py`**: `$I` (index) + `$R` (data) file pairs in `$Recycle.Bin\{user-SID}\`.

---

### Priority 5 — Anti-Fingerprint

**`services/anti_fingerprint/vm_scrubber.py`**: Search the hive for known VM-related strings (VBox, VMware, VBOX_HARDDISK, etc.) and overwrite them via `HiveWriter`. Also check device paths in SYSTEM hive.

**`services/anti_fingerprint/hardware_normalizer.py`**: Replace SMBIOS strings (Manufacturer, ProductName, SystemFamily) with realistic vendor values from `data/hardware_models.json`.

**`services/anti_fingerprint/process_faker.py`**: Write SRUM database stubs and fake service entries to make process history look plausible.

---

### Priority 6 — Known HiveWriter Limitation

The current `HiveWriter` can only **patch existing values**, not create new keys. This works for freshly installed base images, but becomes a blocker when:
- The target key doesn't exist yet in the hive
- A value was never written during Windows Setup

**Solution options:**
1. Ship a **base hive template** (a minimal but valid `NTUSER.DAT` / `SOFTWARE` with all target keys pre-seeded as empty defaults) — simplest approach.
2. Implement `delete_value` / `delete_key` / key-creation in `HiveWriter` using full hive cell allocation (complex but complete).
3. Integrate `hivex` (C library with Python bindings, full read/write support).

---

### Priority 7 — Browser Bookmarks and Cookies

**`services/browser/bookmarks.py`**: Write the `Bookmarks` JSON file using template files in `templates/browser/`. This is straightforward since bookmarks are plain JSON in Chrome/Edge.

**`services/browser/cookies_cache.py`**: Chrome `Cookies` SQLite DB + `Cache/` directory index stubs. The Cookies DB schema is well-documented.

---

### Priority 8 — Evaluation Suite

Once artifact generation is complete, the evaluation modules need implementation:
- `consistency_checker.py`: Verify MRU registry entries point to real file paths; verify download DB entries have matching filesystem files; check timeline monotonicity.
- `density_analyzer.py`: Compare artifact counts against reference baselines for a "real" Windows 11 install.
- `sandbox_signal_tester.py`: Check for remaining VM-indicator strings in hives, missing registry subtrees, suspicious file absence.
- `report_generator.py`: Produce a human-readable HTML/Markdown summary of all checks.

---

## Summary Table

| Component | Done | Partial | Stub/Empty |
|---|---|---|---|
| Core (profile, identity, timestamps, mount, audit) | 5 | — | 1 (orchestrator) |
| Filesystem | 1 | — | 7 |
| Registry | 5 | 1 (hive create-key) | — |
| Browser | 6 | — | 2 (bookmarks, cookies) |
| Event Logs | — | — | 5 |
| Applications | — | — | 4 |
| Anti-Fingerprint | — | — | 3 |
| Evaluation | — | — | 4 |
| Tests | 11 | — | 4 empty |

**Rough remaining effort (estimate):** Orchestrator (1 day), Event Logs (3–5 days), Applications (2–3 days), Filesystem generators (2 days), Anti-fingerprint (1–2 days), Browser completion (1 day), Evaluation (2 days).
