# Graph Report - .  (2026-04-18)

## Corpus Check
- 169 files · ~129,790 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3314 nodes · 11349 edges · 56 communities detected
- Extraction: 34% EXTRACTED · 66% INFERRED · 0% AMBIGUOUS · INFERRED: 7464 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Core Service Framework|Core Service Framework]]
- [[_COMMUNITY_Anti-Fingerprint and Hardware Normalization|Anti-Fingerprint and Hardware Normalization]]
- [[_COMMUNITY_AI Generation Pipeline|AI Generation Pipeline]]
- [[_COMMUNITY_Core Orchestration|Core Orchestration]]
- [[_COMMUNITY_Event Log Services|Event Log Services]]
- [[_COMMUNITY_Anti-Fingerprint Test Suite|Anti-Fingerprint Test Suite]]
- [[_COMMUNITY_LLM Browser Seed Generation|LLM Browser Seed Generation]]
- [[_COMMUNITY_Profile and Identity Engine|Profile and Identity Engine]]
- [[_COMMUNITY_Wizard Interface|Wizard Interface]]
- [[_COMMUNITY_Filesystem Writer|Filesystem Writer]]
- [[_COMMUNITY_Evaluation and Density Analysis|Evaluation and Density Analysis]]
- [[_COMMUNITY_Registry Binary IO|Registry Binary I/O]]
- [[_COMMUNITY_Browser Pipeline and Docs|Browser Pipeline and Docs]]
- [[_COMMUNITY_Download Generation|Download Generation]]
- [[_COMMUNITY_Architecture Design Decisions|Architecture Design Decisions]]
- [[_COMMUNITY_VM Image Build Utilities|VM Image Build Utilities]]
- [[_COMMUNITY_UserAssist Entry Tests|UserAssist Entry Tests]]
- [[_COMMUNITY_Determinism and Reproducibility|Determinism and Reproducibility]]
- [[_COMMUNITY_Chrome Timestamp Utilities|Chrome Timestamp Utilities]]
- [[_COMMUNITY_Search Term Generator|Search Term Generator]]
- [[_COMMUNITY_Browser Visit Generation|Browser Visit Generation]]
- [[_COMMUNITY_Registry MRU and Recent Docs|Registry MRU and Recent Docs]]
- [[_COMMUNITY_Security Event Log|Security Event Log]]
- [[_COMMUNITY_System Event Log|System Event Log]]
- [[_COMMUNITY_Network Profiles|Network Profiles]]
- [[_COMMUNITY_Installed Programs Registry|Installed Programs Registry]]
- [[_COMMUNITY_Office Application Artifacts|Office Application Artifacts]]
- [[_COMMUNITY_Recycle Bin Service|Recycle Bin Service]]
- [[_COMMUNITY_Thumbnail Cache|Thumbnail Cache]]
- [[_COMMUNITY_Prefetch Service|Prefetch Service]]
- [[_COMMUNITY_Browser Bookmarks|Browser Bookmarks]]
- [[_COMMUNITY_Browser Cookies|Browser Cookies]]
- [[_COMMUNITY_Browser History SQLite|Browser History SQLite]]
- [[_COMMUNITY_Media Stub Generation|Media Stub Generation]]
- [[_COMMUNITY_Document Generator|Document Generator]]
- [[_COMMUNITY_User Directory Scaffold|User Directory Scaffold]]
- [[_COMMUNITY_Dev Environment Traces|Dev Environment Traces]]
- [[_COMMUNITY_Email Client Artifacts|Email Client Artifacts]]
- [[_COMMUNITY_Comms App Traces|Comms App Traces]]
- [[_COMMUNITY_System Identity Registry|System Identity Registry]]
- [[_COMMUNITY_VM Manager|VM Manager]]
- [[_COMMUNITY_Mount Manager|Mount Manager]]
- [[_COMMUNITY_Timestamp Service|Timestamp Service]]
- [[_COMMUNITY_Consistency Checker|Consistency Checker]]
- [[_COMMUNITY_Sandbox Signal Tester|Sandbox Signal Tester]]
- [[_COMMUNITY_Report Generator|Report Generator]]
- [[_COMMUNITY_Profile Schema|Profile Schema]]
- [[_COMMUNITY_Base Profile YAML|Base Profile YAML]]
- [[_COMMUNITY_Update Artifacts|Update Artifacts]]
- [[_COMMUNITY_Application Log|Application Log]]
- [[_COMMUNITY_Test Utilities|Test Utilities]]
- [[_COMMUNITY_URL Loader|URL Loader]]
- [[_COMMUNITY_Config Generator|Config Generator]]
- [[_COMMUNITY_Bookmark Enricher|Bookmark Enricher]]
- [[_COMMUNITY_Recent Items|Recent Items]]
- [[_COMMUNITY_Cross-Cutting Utilities|Cross-Cutting Utilities]]

## God Nodes (most connected - your core abstractions)
1. `BaseService` - 466 edges
2. `AuditLogger` - 455 edges
3. `HiveWriter` - 317 edges
4. `HiveWriterError` - 312 edges
5. `HiveOperation` - 284 edges
6. `RegistryValueType` - 280 edges
7. `MountManager` - 195 edges
8. `PersonaContext` - 169 edges
9. `IdentityGenerator` - 144 edges
10. `Orchestrator` - 137 edges

## Surprising Connections (you probably didn't know these)
- `Browser SQLite State Synthesis` --semantically_similar_to--> `Browser History Generation Pipeline`  [INFERRED] [semantically similar]
  report.md → services/browser/browser_history.md
- `checker()` --calls--> `ConsistencyChecker`  [INFERRED]
  tests/test_evaluation/test_consistency_checker.py → evaluation/consistency_checker.py
- `audit_logger()` --calls--> `AuditLogger`  [INFERRED]
  tests/test_evaluation/test_density_analyzer.py → core/audit_logger.py
- `audit_logger()` --calls--> `AuditLogger`  [INFERRED]
  tests/test_evaluation/test_sandbox_signal_tester.py → core/audit_logger.py
- `audit_logger()` --calls--> `AuditLogger`  [INFERRED]
  tests/test_services/test_vm_scrubber.py → core/audit_logger.py

## Hyperedges (group relationships)
- **ARC Core Artifact Generation Pipeline** — report_arc_framework, report_spatio_temporal_consistency, report_hivewriter_binary_patching, report_evtxwriter_binary, report_browser_sqlite_synthesis, report_absolute_determinism [EXTRACTED 0.95]
- **Browser Artifact Synthesis Flow** — browser_history_pipeline, browser_history_diurnal_model, browser_history_session_chaining, browser_history_powerlaw_selection, browser_history_deterministic_seed [EXTRACTED 0.92]
- **Anti-Sandbox Fingerprint Defense Triad** — report_wearandtear_artifacts, report_sterile_sandbox_problem, report_drs_metric, report_artifact_density [EXTRACTED 0.88]
- **ARC Full Artifact Generation Pipeline (binary write adapters)** — architecture_hivewriter_adapter, architecture_evtxwriter_adapter, architecture_operation_list_pattern, architecture_constructor_injection, architecture_audit_logger_sideeffect [EXTRACTED 0.93]
- **AI Prompt Seed Expansion Pattern (documents/downloads)** — documents_prompt_seed_pattern, downloads_prompt_seed_pattern, wizard_guide_gemini_fallback [INFERRED 0.80]
- **VHD Mounted Image Demonstrating Full Artifact Coverage** — vhd_listing_registry_hives, vhd_listing_browser_artifacts, vhd_listing_prefetch_entries, vhd_listing_recycle_bin, vhd_listing_evtx_logs, vhd_listing_thumbcache [EXTRACTED 0.92]

## Communities

### Community 0 - "Core Service Framework"
Cohesion: 0.01
Nodes (373): ABC, Record an audit log entry with automatic timestamp., BaseService, Abstract base class for all services., Base class that all services must inherit from., BaseService, _empty_bookmarks(), _enrich_children() (+365 more)

### Community 1 - "Anti-Fingerprint and Hardware Normalization"
Cohesion: 0.01
Nodes (337): _format_bios_date(), HardwareNormalizer, HardwareNormalizerError, Hardware normalizer — anti-fingerprint registry service.  Replaces VM-generated, Return the unique service name., Execute from orchestrator context.          Expects context keys:             id, Build and execute all hardware normalization operations.          Args:, Build all hardware normalization operations without writing.          Pure funct (+329 more)

### Community 2 - "AI Generation Pipeline"
Cohesion: 0.02
Nodes (278): AIGenerationConfig, AIGenerationResult, AIOrchestrator, from_config(), generate_ai_profile(), AI Orchestrator for coordinating AI-powered profile and artifact generation.  Th, Result of AI-powered generation., Check if generation was successful. (+270 more)

### Community 3 - "Core Orchestration"
Cohesion: 0.02
Nodes (280): success(), AuditLogger, Audit logging for all write/modify operations across services., Logs every write/modify operation for audit trail., AuditLogger, BrowserProfileService, Browser profile directory scaffolding service.  Orchestrates the creation of Chr, Creates Chrome and Edge browser profile directory trees.      Generates the comp (+272 more)

### Community 4 - "Event Log Services"
Cohesion: 0.02
Nodes (122): ApplicationLog, ApplicationLogError, _fake_guid(), Application event log service.  Generates synthetic Windows Application event lo, Raised when application log operations fail., Writes synthetic Application event log entries to Application.evtx.      Produce, Return the unique service name., Execute from orchestrator context.          Expects context keys:             pr (+114 more)

### Community 5 - "Anti-Fingerprint Test Suite"
Cohesion: 0.02
Nodes (33): audit_logger(), data_dir(), _make_bundle(), _make_hardware(), _make_user(), normalizer(), sample_bundle(), TestBuildOperationsDisk (+25 more)

### Community 6 - "LLM Browser Seed Generation"
Cohesion: 0.03
Nodes (60): _build_base(), build_bookmark_titles(), build_document_content(), build_email_subject(), build_event_log_message(), build_file_names(), build_pre_validation_prompt(), build_search_terms() (+52 more)

### Community 7 - "Profile and Identity Engine"
Cohesion: 0.05
Nodes (58): Identity Generation Service for creating consistent fake identities.  Generates, Generates deterministic, coherent identity bundles from profile context.      Ar, Generate a complete, deterministic identity bundle.          Args:             o, Create Faker and RNG instances with deterministic seed., Load and validate hardware_models.json.          Raises:             FileNotFoun, Generate the human-facing identity., Derive username from full name as ``firstname.lastname``.          Returns:, Normalize organization name to an email domain.          Strips corporate suffix (+50 more)

### Community 8 - "Wizard Interface"
Cohesion: 0.05
Nodes (70): automated_test_workflow(), check_drive_mounted(), clear_screen(), dismount_drive(), get_choice(), get_input(), get_yes_no(), main_menu() (+62 more)

### Community 9 - "Filesystem Writer"
Cohesion: 0.06
Nodes (34): CrossWriter, CrossWriterError, Recursive directory/file writing service for mounted drives.  CrossWriter is a p, Validate a file node's fields., Recursively process a tree node, creating dirs and files., Create a directory and log the operation., Ensure resolved path is within the mount root., Raised on schema validation, path escape, or write failures. (+26 more)

### Community 10 - "Evaluation and Density Analysis"
Cohesion: 0.07
Nodes (19): CategoryDensity, DensityAnalyzer, Artifact density analyzer.  Compares the number and distribution of artifacts pr, Compares artifact counts against reference baselines.      Args:         audit_l, Compute per-category density metrics.          Args:             context: Option, Return a 0.0–1.0 overall density score.          The score is the average densit, Return a human-readable density summary.          Returns:             Multi-lin, Count audit entries per artifact category. (+11 more)

### Community 11 - "Registry Binary I/O"
Cohesion: 0.07
Nodes (20): _add_subkey_to_parent(), _align8(), _create_nk_cell(), _create_or_replace_value(), _encode_value_data(), _find_data_offset(), _find_next_free(), _HiveAllocator (+12 more)

### Community 12 - "Browser Pipeline and Docs"
Cohesion: 0.07
Nodes (36): Production-Ready AI Agent Coding Standards (SOLID/DRY/KISS), Chromium History SQLite Schema v46, Deterministic Seeded Random (seed=42) for Reproducibility, Diurnal Browsing Model (Circadian Activity Pattern), Dual Download Artifacts (SQLite + Filesystem Stubs), Browser History Generation Pipeline, Power-Law URL Selection (expovariate 0.05), Session Chaining via from_visit Links (+28 more)

### Community 13 - "Download Generation"
Cohesion: 0.13
Nodes (12): create_placeholder_file(), generate_download_time(), load_download_catalogue(), Generates download records in the Chrome History SQLite database and creates pla, Load downloads_by_profile.json from data/wordlists/., Pick ``count`` download entries for this profile.      Falls back to ``home_user, Random timestamp inside the profile's active window., Write a zero-byte stub file so the path exists on the filesystem.      We do not (+4 more)

### Community 14 - "Architecture Design Decisions"
Cohesion: 0.09
Nodes (23): Constructor Injection Pattern (HiveWriter/EvtxWriter at init), EvtxWriter Binary I/O Adapter (from scratch, CRC32), HiveWriter Binary I/O Adapter (regipy + direct binary patch), Operation-List Pattern (build then execute, dry-run support), Single _VM_STRINGS Source in identity_generator.py, Document Seed Expansion Pattern (Jinja2 template + variables), Download Seed Expansion Pattern (filename/url/mime templates), Cross-Service Consistency Checker (+15 more)

### Community 15 - "VM Image Build Utilities"
Cohesion: 0.11
Nodes (19): build_image(), elevate_and_rerun(), find_free_drive_letter(), is_admin(), main(), Re-launch this script with UAC elevation, preserving all args., Create VHD → format NTFS → run main.py to populate → detach → save., run_diskpart() (+11 more)

### Community 16 - "UserAssist Entry Tests"
Cohesion: 0.33
Nodes (2): TestEntryEncoding, encode_entry()

### Community 17 - "Determinism and Reproducibility"
Cohesion: 0.33
Nodes (6): Deterministic RNG Seeded on (computer_name, profile_type), Frozen Pydantic Models for Config Error Detection, Audit Log Replay and Reproducibility Guarantee, Profile Coverage Metrics (home/office/developer), Profile YAML Inheritance Chain (base → home/office/developer), ProfileContext Pydantic Validation (frozen=True, extra=forbid)

### Community 18 - "Chrome Timestamp Utilities"
Cohesion: 0.5
Nodes (3): chrome_to_unix_seconds(), Chrome/WebKit timestamp conversion utilities.  Chrome and Chromium-based browser, Convert a Chrome timestamp back to Unix epoch seconds.      Useful for debugging

### Community 19 - "Search Term Generator"
Cohesion: 0.5
Nodes (3): populate_search_terms(), Inserts keyword_search_terms rows into the History DB.  Links randomly selected, Insert search-term entries linked to search-engine URLs.      Args:         conn

### Community 20 - "Browser Visit Generation"
Cohesion: 1.0
Nodes (1): Shared constants for browser artifact generation.  Centralises browser paths, Ch

### Community 21 - "Registry MRU and Recent Docs"
Cohesion: 1.0
Nodes (1): Chrome History SQLite database schema.  Contains the full CREATE TABLE / CREATE

### Community 22 - "Security Event Log"
Cohesion: 1.0
Nodes (2): AuditLogger Append-Only List (zero side-effects in testing), AuditLogger Structured Log Schema (timestamp/service/operation)

### Community 23 - "System Event Log"
Cohesion: 1.0
Nodes (2): Graceful Fallback to Static Profiles if AI Unavailable, ARC Interactive Wizard Menu Interface

### Community 24 - "Network Profiles"
Cohesion: 1.0
Nodes (0): 

### Community 25 - "Installed Programs Registry"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "Office Application Artifacts"
Cohesion: 1.0
Nodes (1): Whether the LLM client is active.

### Community 27 - "Recycle Bin Service"
Cohesion: 1.0
Nodes (1): Return usage statistics.

### Community 28 - "Thumbnail Cache"
Cohesion: 1.0
Nodes (1): Extract the generated text from various LLM response formats.          Supports:

### Community 29 - "Prefetch Service"
Cohesion: 1.0
Nodes (1): Produce a deterministic cache key from a prompt string.

### Community 30 - "Browser Bookmarks"
Cohesion: 1.0
Nodes (1): Return the resolved mount root path.

### Community 31 - "Browser Cookies"
Cohesion: 1.0
Nodes (1): Load, resolve, validate and cache a profile by name.          Args:

### Community 32 - "Browser History SQLite"
Cohesion: 1.0
Nodes (1): Return all recorded audit entries.

### Community 33 - "Media Stub Generation"
Cohesion: 1.0
Nodes (1): Return PowerShell-safe image path wrapped for single quotes.

### Community 34 - "Document Generator"
Cohesion: 1.0
Nodes (1): Convert datetime to Chrome/Chromium timestamp (microseconds since 1601).

### Community 35 - "User Directory Scaffold"
Cohesion: 1.0
Nodes (1): Convert Chrome timestamp to datetime.          Args:             chrome_ts: Micr

### Community 36 - "Dev Environment Traces"
Cohesion: 1.0
Nodes (1): Convert datetime to Windows FILETIME (100-nanosecond intervals since 1601).

### Community 37 - "Email Client Artifacts"
Cohesion: 1.0
Nodes (1): Convert Windows FILETIME to datetime.          Args:             filetime: 100-n

### Community 38 - "Comms App Traces"
Cohesion: 1.0
Nodes (0): 

### Community 39 - "System Identity Registry"
Cohesion: 1.0
Nodes (1): Return the unique name of this service.

### Community 40 - "VM Manager"
Cohesion: 1.0
Nodes (1): Execute the service's primary operation.

### Community 41 - "Mount Manager"
Cohesion: 1.0
Nodes (0): 

### Community 42 - "Timestamp Service"
Cohesion: 1.0
Nodes (1): Convert mixed input into a clean list of non-empty strings.

### Community 43 - "Consistency Checker"
Cohesion: 1.0
Nodes (1): Return case-insensitive de-duplicated values preserving order.

### Community 44 - "Sandbox Signal Tester"
Cohesion: 1.0
Nodes (1): Derive a schema-compatible username from full name.

### Community 45 - "Report Generator"
Cohesion: 1.0
Nodes (1): Build a plausible email domain from organization name.

### Community 46 - "Profile Schema"
Cohesion: 1.0
Nodes (1): Map free-form proficiency values to schema enum values.

### Community 47 - "Base Profile YAML"
Cohesion: 1.0
Nodes (1): Return the model name.

### Community 48 - "Update Artifacts"
Cohesion: 1.0
Nodes (1): Return True if API key is configured.

### Community 49 - "Application Log"
Cohesion: 1.0
Nodes (0): 

### Community 50 - "Test Utilities"
Cohesion: 1.0
Nodes (0): 

### Community 51 - "URL Loader"
Cohesion: 1.0
Nodes (0): 

### Community 52 - "Config Generator"
Cohesion: 1.0
Nodes (0): 

### Community 53 - "Bookmark Enricher"
Cohesion: 1.0
Nodes (0): 

### Community 54 - "Recent Items"
Cohesion: 1.0
Nodes (0): 

### Community 55 - "Cross-Cutting Utilities"
Cohesion: 1.0
Nodes (1): 4-Month Development Timeline and Milestones

## Knowledge Gaps
- **256 isolated node(s):** `Comprehensive realism verification script.  Checks every requirement from the pr`, `Re-launch this script with UAC elevation, preserving all args.`, `Create VHD → format NTFS → run main.py to populate → detach → save.`, `Sandbox signal tester.  Re-implements simplified versions of common static sandb`, `Result of one sandbox signal check.      Attributes:         signal_name: Short` (+251 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Browser Visit Generation`** (2 nodes): `Shared constants for browser artifact generation.  Centralises browser paths, Ch`, `constants.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Registry MRU and Recent Docs`** (2 nodes): `Chrome History SQLite database schema.  Contains the full CREATE TABLE / CREATE`, `schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Security Event Log`** (2 nodes): `AuditLogger Append-Only List (zero side-effects in testing)`, `AuditLogger Structured Log Schema (timestamp/service/operation)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `System Event Log`** (2 nodes): `Graceful Fallback to Static Profiles if AI Unavailable`, `ARC Interactive Wizard Menu Interface`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Network Profiles`** (1 nodes): `test_env.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Installed Programs Registry`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Office Application Artifacts`** (1 nodes): `Whether the LLM client is active.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Recycle Bin Service`** (1 nodes): `Return usage statistics.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Thumbnail Cache`** (1 nodes): `Extract the generated text from various LLM response formats.          Supports:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Prefetch Service`** (1 nodes): `Produce a deterministic cache key from a prompt string.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Browser Bookmarks`** (1 nodes): `Return the resolved mount root path.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Browser Cookies`** (1 nodes): `Load, resolve, validate and cache a profile by name.          Args:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Browser History SQLite`** (1 nodes): `Return all recorded audit entries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Media Stub Generation`** (1 nodes): `Return PowerShell-safe image path wrapped for single quotes.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Document Generator`** (1 nodes): `Convert datetime to Chrome/Chromium timestamp (microseconds since 1601).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `User Directory Scaffold`** (1 nodes): `Convert Chrome timestamp to datetime.          Args:             chrome_ts: Micr`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dev Environment Traces`** (1 nodes): `Convert datetime to Windows FILETIME (100-nanosecond intervals since 1601).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Email Client Artifacts`** (1 nodes): `Convert Windows FILETIME to datetime.          Args:             filetime: 100-n`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Comms App Traces`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `System Identity Registry`** (1 nodes): `Return the unique name of this service.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `VM Manager`** (1 nodes): `Execute the service's primary operation.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Mount Manager`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Timestamp Service`** (1 nodes): `Convert mixed input into a clean list of non-empty strings.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Consistency Checker`** (1 nodes): `Return case-insensitive de-duplicated values preserving order.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sandbox Signal Tester`** (1 nodes): `Derive a schema-compatible username from full name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Report Generator`** (1 nodes): `Build a plausible email domain from organization name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Profile Schema`** (1 nodes): `Map free-form proficiency values to schema enum values.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Base Profile YAML`** (1 nodes): `Return the model name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Update Artifacts`** (1 nodes): `Return True if API key is configured.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Application Log`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Test Utilities`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `URL Loader`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Config Generator`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Bookmark Enricher`** (1 nodes): `test_document_generator.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Recent Items`** (1 nodes): `test_browser_downloads.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Cross-Cutting Utilities`** (1 nodes): `4-Month Development Timeline and Milestones`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AuditLogger` connect `Core Orchestration` to `Core Service Framework`, `Anti-Fingerprint and Hardware Normalization`, `Event Log Services`, `Anti-Fingerprint Test Suite`, `LLM Browser Seed Generation`, `Wizard Interface`, `Filesystem Writer`, `Evaluation and Density Analysis`, `Registry Binary I/O`, `UserAssist Entry Tests`?**
  _High betweenness centrality (0.174) - this node is a cross-community bridge._
- **Why does `BaseService` connect `Core Service Framework` to `Anti-Fingerprint and Hardware Normalization`, `AI Generation Pipeline`, `Core Orchestration`, `Event Log Services`, `Anti-Fingerprint Test Suite`, `Filesystem Writer`, `Registry Binary I/O`?**
  _High betweenness centrality (0.161) - this node is a cross-community bridge._
- **Why does `Integration tests for the ARC system.` connect `AI Generation Pipeline` to `Core Service Framework`, `Anti-Fingerprint and Hardware Normalization`, `Core Orchestration`, `Event Log Services`, `Anti-Fingerprint Test Suite`, `Evaluation and Density Analysis`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Are the 463 inferred relationships involving `BaseService` (e.g. with `BrowserDownloadService` and `Download seed generator using Gemini API.  Generates 10-30 download seeds that w`) actually correct?**
  _`BaseService` has 463 INFERRED edges - model-reasoned connections that need verification._
- **Are the 450 inferred relationships involving `AuditLogger` (e.g. with `Configure logging for the application.      Args:         verbose: If True, s` and `Load configuration from YAML file.      Args:         config_path: Path to co`) actually correct?**
  _`AuditLogger` has 450 INFERRED edges - model-reasoned connections that need verification._
- **Are the 301 inferred relationships involving `HiveWriter` (e.g. with `Integration tests for the ARC system.` and `UserAssistError`) actually correct?**
  _`HiveWriter` has 301 INFERRED edges - model-reasoned connections that need verification._
- **Are the 304 inferred relationships involving `HiveWriterError` (e.g. with `Integration tests for the ARC system.` and `UserAssistError`) actually correct?**
  _`HiveWriterError` has 304 INFERRED edges - model-reasoned connections that need verification._