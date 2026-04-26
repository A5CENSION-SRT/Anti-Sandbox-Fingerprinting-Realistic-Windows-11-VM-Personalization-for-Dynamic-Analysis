# ARC — Project Structure

Current layout after the rescue refactor.  See `docs/MASTER_PLAN.md` for the full
architectural rationale.

```
.
├── main.py                          CLI entry point (--preset / --ai-generate / --vhdx)
├── arc_wizard.py                    Interactive menu wizard
├── verify_realism.py                Post-injection realism checker
├── config.yaml                      Runtime configuration (timeline_days, mount_path, …)
├── requirements.txt
├── GETTING_STARTED.md               Operator setup guide (replaces SETUP/START_HERE)
│
├── core/
│   ├── persona_context.py           Canonical 25+ field PersonaContext (ADR-001)
│   ├── persona_loader.py            YAML → PersonaContext; preset or AI path
│   ├── service_context.py           Typed ServiceContext threaded through all services
│   ├── event_scheduler.py           Deterministic cross-domain event stream (ADR-005)
│   ├── linux_mount.py               LinuxMountBackend: libguestfs + hivex + ntfs-3g (ADR-002)
│   ├── mount_manager.py             Path resolution; delegates to LinuxMountBackend
│   ├── orchestrator.py              Phase runner and service registry
│   ├── identity_generator.py        IdentityBundle: user + hardware identity
│   ├── timestamp_service.py         Timeline-aware timestamps
│   ├── audit_logger.py              Structured audit trail for every write
│   ├── llm_client.py                Local LLM client for artifact body generation
│   └── time_utils.py                Scheduler-aware datetime helpers
│
├── services/
│   ├── base_service.py              BaseService ABC: apply(ctx: ServiceContext) → None
│   │
│   ├── expansion/                   ExpansionOrchestrator → ExpansionBundle
│   │   ├── orchestrator.py
│   │   └── bundle.py
│   │
│   ├── registry/
│   │   ├── hive_writer.py           hivex-backed HiveWriter + HiveOperation (ADR-002)
│   │   ├── installed_programs.py    Uninstall registry keys
│   │   ├── mru_recentdocs.py        RecentDocs MRU per extension
│   │   ├── network_profiles.py      Network interface history
│   │   ├── system_identity.py       Computer name, SID, machine GUID
│   │   ├── userassist.py            UserAssist ROT13-encoded run counts
│   │   └── typing_history.py        TypedURLs, TypedPaths, WordWheelQuery (A10)
│   │
│   ├── filesystem/
│   │   ├── cross_writer.py          Recursive directory/file writer
│   │   ├── prefetch.py              v30 SCCA Prefetch files (A11)
│   │   ├── document_generator.py    DOCX/XLSX/PDF documents (A14)
│   │   ├── office_mru.py            Office Recent MRU registry + files
│   │   ├── powershell_history.py    ConsoleHost_history.txt
│   │   ├── cdp_logs.py              ConnectedDevicesPlatform logs
│   │   ├── thumbnail_cache.py       thumbcache_*.db stubs
│   │   ├── recent_items.py          Shell Recent .lnk files
│   │   ├── recycle_bin.py           $Recycle.Bin stubs
│   │   ├── user_directory.py        User folder skeleton
│   │   ├── installed_apps_stub.py   Binary stubs for installed executables
│   │   ├── media_stub.py            Media file stubs
│   │   └── system_content_populator.py
│   │
│   ├── browser/
│   │   ├── history.py               Chrome/Edge History SQLite (A13)
│   │   ├── downloads.py             Download records + Zone.Identifier ADS (A18/R27)
│   │   ├── bookmarks.py             Bookmarks JSON
│   │   ├── browser_profile.py       Profile dirs + Local State
│   │   ├── cookies_cache.py         Cookies SQLite + cache stubs
│   │   ├── generators/              Schema SQL, visit/download generators
│   │   └── utils/                   Chrome epoch, URL loader, constants
│   │
│   ├── eventlog/
│   │   ├── evtx_writer.py           Multi-chunk EVTX binary writer (v3.0)
│   │   ├── security_log.py          4624/4634/4688/4689/4648/4672/4769 (A12)
│   │   ├── system_log.py            6005/6006/12/13/7036 + service events
│   │   ├── application_log.py       MSI installs, app crashes, periodic stubs
│   │   └── update_artifacts.py      Windows Update history
│   │
│   ├── applications/
│   │   ├── dev_environment.py       .gitconfig, SSH, VS Code, Docker artifacts
│   │   ├── office_artifacts.py      Office document + LNK stubs
│   │   ├── email_client.py          Outlook profile XML + PST stubs
│   │   └── comms_apps.py            Teams, Slack, Discord, Zoom dirs
│   │
│   ├── ntfs/
│   │   ├── mft_timestamp_patcher.py $STANDARD_INFORMATION SI patching via setxattr
│   │   ├── usn_journal_writer.py    $UsnJrnl:$J USN_RECORD_V2 appender
│   │   └── logfile_writer.py        $LogFile stub (best-effort)
│   │
│   ├── anti_fingerprint/
│   │   ├── vm_scrubber.py           Deletes VBox/VMware/QEMU/KVM service keys
│   │   ├── hardware_normalizer.py   BIOS/SCSI/GPU string replacement
│   │   ├── mac_hygiene.py           NIC NetworkAddress OUI override
│   │   └── process_faker.py         Fake process list stubs
│   │
│   └── ai/
│       ├── persona_generator.py     PersonaContext via Gemini
│       ├── gemini_client.py         Google Gemini API client
│       ├── schemas.py               AI seed schemas
│       ├── seed_generators/         Browsing, documents, downloads, registry seeds
│       └── prompts/                 LLM prompt templates
│
├── profiles/
│   └── presets/
│       ├── developer.yaml
│       ├── office_user.yaml
│       └── home_user.yaml
│
├── data/
│   └── wordlists/                   URL pool, download catalogue, search terms
│
├── templates/                       Browser bookmark templates, EVTX templates
│
├── evaluation/
│   ├── density_analyzer.py          Artifact density thresholds (A10–A14)
│   ├── consistency_checker.py       ConsistencyChecker + TemporalCoherenceCheck (A9)
│   └── sandbox_signal_tester.py
│
├── scripts/
│   └── build_baseline_vhdx.sh       virt-install unattended Windows 11 build
│
├── examples/
│   ├── unattend.xml                 Silent-install Windows answer file
│   └── libvirt-profile-template.xml SMBIOS/MAC/disk-serial spoofing reference
│
├── tests/
│   ├── test_core/                   PersonaLoader, EventScheduler, LinuxMount, …
│   ├── test_services/               Per-service unit + integration tests (829 passing)
│   └── test_evaluation/             ConsistencyChecker, TemporalCoherenceCheck, DensityAnalyzer
│
└── docs/
    ├── MASTER_PLAN.md               Authoritative architecture + phase plan
    ├── architecture.md              System overview
    ├── profile_schema.md            PersonaContext field reference
    ├── research/                    NTFS journal, time integrity, VM evasion, …
    ├── design/decisions.md          ADR log (ADR-001 … ADR-016)
    └── archive/                     Superseded docs
```
