# Anti-Sandbox VM Personalization — Project Structure & Team Split

---

## Folder Structure

```
anti-sandbox-personalizer/
│
├── main.py                          # Entry point — CLI interface
├── config.yaml                      # Global config (mount path, log level, etc.)
├── requirements.txt
├── README.md
│
├── profiles/
│   ├── base.yaml                    # Shared defaults all profiles inherit
│   ├── office_user.yaml
│   ├── developer.yaml
│   └── home_user.yaml
│
├── core/
│   ├── __init__.py
│   ├── orchestrator.py              # Wires all services, runs in dependency order
│   ├── profile_engine.py            # Loads & resolves profile YAML into ProfileContext
│   ├── identity_generator.py        # Fake name, email, org, machine name, HW strings
│   ├── timestamp_service.py         # Master timeline, realistic time distribution
│   ├── mount_manager.py             # Mount point validation, path helpers, permissions
│   └── audit_logger.py              # Logs every write/modify across all services
│
├── services/
│   ├── __init__.py
│   ├── base_service.py              # Abstract base class all services inherit
│   │
│   ├── filesystem/
│   │   ├── __init__.py
│   │   ├── cross_writer.py          # Recursive dir/file writing to mounted drive
│   │   ├── user_directory.py        # C:\Users\<name> scaffold
│   │   ├── document_generator.py    # .docx, .xlsx, .pdf, .txt with metadata
│   │   ├── media_stub.py            # JPEG/PNG with EXIF, small media stubs
│   │   ├── prefetch.py              # Synthetic .pf files in C:\Windows\Prefetch
│   │   ├── thumbnail_cache.py       # thumbcache_*.db, IconCache.db
│   │   ├── recent_items.py          # .lnk files, Jump Lists
│   │   └── recycle_bin.py           # $Recycle.Bin artifacts ($I / $R pairs)
│   │
│   ├── registry/
│   │   ├── __init__.py
│   │   ├── hive_writer.py           # Core offline registry hive read/write
│   │   ├── mru_recentdocs.py        # RecentDocs, OpenSaveMRU, LastVisitedMRU
│   │   ├── userassist.py            # ROT13-encoded execution traces
│   │   ├── installed_programs.py    # Uninstall registry entries
│   │   ├── system_identity.py       # BIOS, disk, ProductId, machine GUID
│   │   └── network_profiles.py      # Saved Wi-Fi / network connection profiles
│   │
│   ├── browser/
│   │   ├── __init__.py
│   │   ├── browser_profile.py       # Chrome/Edge profile dir + config JSONs
│   │   ├── history.py               # SQLite History DB (URLs, visits, searches)
│   │   ├── cookies_cache.py         # Cookie DB + cache index stubs
│   │   ├── bookmarks.py             # Bookmarks JSON
│   │   └── downloads.py             # Downloads table (cross-ref with filesystem)
│   │
│   ├── eventlog/
│   │   ├── __init__.py
│   │   ├── evtx_writer.py           # Core .evtx binary construction/injection
│   │   ├── system_log.py            # System.evtx — boot, service, driver events
│   │   ├── security_log.py          # Security.evtx — logon/logoff events
│   │   ├── application_log.py       # Application.evtx — app errors, installs
│   │   └── update_artifacts.py      # SoftwareDistribution, CBS.log, KB traces
│   │
│   ├── applications/
│   │   ├── __init__.py
│   │   ├── office_artifacts.py      # Office MRU, temp/recovery files
│   │   ├── dev_environment.py       # VS Code, Git, Node/Python caches
│   │   ├── email_client.py          # Outlook .ost stub / Thunderbird prefs
│   │   └── comms_apps.py            # Teams/Slack/Discord/Zoom traces
│   │
│   └── anti_fingerprint/
│       ├── __init__.py
│       ├── vm_scrubber.py           # Remove VBox/VMware indicators
│       ├── hardware_normalizer.py   # Realistic SMBIOS/WMI/GPU strings
│       └── process_faker.py         # Service entries, SRUM stubs
│
├── templates/
│   ├── documents/                   # Template .docx/.xlsx/.pptx for doc generator
│   │   ├── meeting_notes.docx
│   │   ├── quarterly_report.xlsx
│   │   └── readme_template.txt
│   ├── browser/
│   │   ├── bookmarks_office.json
│   │   ├── bookmarks_developer.json
│   │   └── bookmarks_home.json
│   └── registry/
│       └── common_services.json     # Expected Windows service entries
│
├── data/
│   ├── wordlists/                   # For generating realistic content
│   │   ├── filenames.txt
│   │   ├── folder_names.txt
│   │   ├── search_terms.txt
│   │   └── urls_by_category.json
│   ├── hardware_models.json         # Realistic BIOS/disk/GPU model strings
│   └── kb_updates.json              # Real Windows KB numbers + dates
│
├── evaluation/
│   ├── __init__.py
│   ├── consistency_checker.py       # Do MRUs point to real files? Timeline sane?
│   ├── density_analyzer.py          # File/key/event counts vs reference baseline
│   ├── sandbox_signal_tester.py     # Checklist of common VM detection signals
│   └── report_generator.py          # Produces the final evaluation report
│
├── tests/
│   ├── __init__.py
│   ├── test_core/
│   │   ├── test_profile_engine.py
│   │   ├── test_timestamp_service.py
│   │   └── test_identity_generator.py
│   ├── test_services/
│   │   ├── test_cross_writer.py
│   │   ├── test_document_generator.py
│   │   ├── test_browser_history.py
│   │   ├── test_registry_writer.py
│   │   └── test_evtx_writer.py
│   └── test_evaluation/
│       └── test_consistency_checker.py
│
└── docs/
    ├── architecture.md              # High-level design & service dependency graph
    ├── profile_schema.md            # How to write/extend profile YAML files
    ├── change_log_format.md         # Audit log schema documentation
    └── evaluation_report.md         # Final deliverable report (template)
```

---
