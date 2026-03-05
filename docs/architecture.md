# ARC — System Architecture

## Overview

ARC (Artifact Replication & Calibration) is a Python tool that personalises a
**mounted Windows 11 image** to resist VM-detection heuristics. It does so by
writing realistic artefacts — registry keys, event-log records, filesystem
structures, browser data — that are consistent with a chosen usage profile.

```
┌─────────────────────────────────────────────────────────────────────┐
│  main.py                                                            │
│  Parses CLI args, loads config.yaml, constructs Orchestrator,      │
│  calls orchestrator.run(mount_path)                                 │
└────────────────────────────┬────────────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  core.Orchestrator          │
              │  – loads ProfileEngine      │
              │  – coordinates all services │
              │  – owns AuditLogger         │
              └───┬──────────────────┬──────┘
                  │                  │
     ┌────────────▼─────┐   ┌────────▼──────────────┐
     │  ProfileEngine   │   │  IdentityGenerator    │
     │  YAML deep-merge │   │  Deterministic names, │
     │  Pydantic valid. │   │  hardware, user data  │
     └────────────┬─────┘   └────────┬──────────────┘
                  │                  │
                  └────────┬─────────┘
                           │  ProfileContext + IdentityBundle
                           │
           ┌───────────────▼────────────────────────────────────────┐
           │  Services Layer                                         │
           │                                                         │
           │  ┌─────────────────────────────────────────────────┐   │
           │  │  services/registry/                              │   │
           │  │   SystemIdentity  InstalledPrograms              │   │
           │  │   NetworkProfiles MruRecentDocs  UserAssist      │   │
           │  └───────────────────────┬─────────────────────────┘   │
           │                          │ HiveOperation list           │
           │                  ┌───────▼────────┐                    │
           │                  │  HiveWriter    │ ── binary patch ──► │
           │                  └────────────────┘    NTUSER.DAT /    │
           │                                        SYSTEM / SAM    │
           │  ┌─────────────────────────────────────────────────┐   │
           │  │  services/eventlog/                              │   │
           │  │   SystemLog  SecurityLog  ApplicationLog         │   │
           │  │   UpdateArtifacts                                │   │
           │  └───────────────────────┬─────────────────────────┘   │
           │                          │ EvtxRecord list              │
           │                  ┌───────▼────────┐                    │
           │                  │  EvtxWriter    │ ── binary build ──► │
           │                  └────────────────┘    System.evtx /   │
           │                                        Security.evtx   │
           │  ┌─────────────────────────────────────────────────┐   │
           │  │  services/anti_fingerprint/                      │   │
           │  │   VmScrubber  HardwareNormalizer  ProcessFaker   │   │
           │  └───────────────────────┬─────────────────────────┘   │
           │                          │ HiveOperation list           │
           │                  ┌───────▼────────┐                    │
           │                  │  HiveWriter    │ ── binary patch ──► │
           │                  └────────────────┘    SYSTEM hive     │
           │                                                         │
           │  ┌─────────────────────────────────────────────────┐   │
           │  │  services/filesystem/  (Sumukha)                 │   │
           │  │  services/browser/     (Raghottam)               │   │
           │  │  services/applications/(Raghottam)               │   │
           │  └─────────────────────────────────────────────────┘   │
           └───────────────────────────────────────────────────────┘
                           │
           ┌───────────────▼───────────────────────────────────────┐
           │  Mounted Windows 11 Image                             │
           │  (e.g. /mnt/win11  or  Z:\)                           │
           └───────────────────────────────────────────────────────┘
```

---

## Layer Descriptions

### 1. Entry Point (`main.py`)

- Reads CLI arguments: `--mount` (image mount path), `--profile` (yaml path),
  `--config` (config.yaml override)
- Instantiates `Orchestrator` with the resolved config and calls `.run()`
- Owned by **Sumukha**

### 2. Core Layer (`core/`)

| Module | Role |
|--------|------|
| `orchestrator.py` | Wires all services; single entry point for a full run |
| `profile_engine.py` | Loads and deep-merges profile YAML files; validates via Pydantic |
| `identity_generator.py` | Generates deterministic `IdentityBundle` from a seed |
| `timestamp_service.py` | Produces realistic, chronologically-sorted timestamps |
| `audit_logger.py` | Append-only in-memory log; `entries` property returns list of dicts |

### 3. Services Layer (`services/`)

All services inherit from `services.base_service.BaseService` (ABC):

```
BaseService
 └── apply(context: dict) → None   # public entry point
```

Constructor injection: every service receives `HiveWriter` **or** `EvtxWriter`
plus `AuditLogger` at construction time — never inside `apply()`. This keeps
services testable in isolation with `MagicMock(spec=HiveWriter)`.

#### `services/registry/`
Writes personalisation data to raw registry hives via `HiveWriter`.

| Service | Key area | Notable detail |
|---------|----------|----------------|
| `SystemIdentity` | `HKLM\SYSTEM\ControlSet001\Control\ComputerName` | Sets hostname + ProductId |
| `InstalledPrograms` | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall` | Profile-specific app list |
| `NetworkProfiles` | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList` | Unique GUID per adapter |
| `MruRecentDocs` | `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs` | 15-doc MRU by extension |
| `UserAssist` | `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\UserAssist` | ROT-13 encoded app paths |

#### `services/eventlog/`
Constructs binary-valid EVTX files from scratch via `EvtxWriter`.

| Service | Log file | EIDs |
|---------|----------|------|
| `SystemLog` | `System.evtx` | 6005, 6006, 7001, 7036 |
| `SecurityLog` | `Security.evtx` | 4608, 4624, 4634, 4672, 4769, 4907 |
| `ApplicationLog` | `Application.evtx` | 11707, 1000, 1001 |
| `UpdateArtifacts` | `System.evtx` + registry | EIDs 19, 20, 43, 44 per KB |

#### `services/anti_fingerprint/`
Removes VM-indicator strings and writes realistic hardware identifiers.

| Service | Mechanism |
|---------|-----------|
| `VmScrubber` | Deletes VM driver/service keys; patches VM strings in hive values |
| `HardwareNormalizer` | Writes real-vendor BIOS/motherboard/disk/GPU strings from `data/hardware_models.json` |
| `ProcessFaker` | Populates 37 real Windows services from `templates/registry/common_services.json`; sets profile-specific Run keys |

### 4. Data & Templates (`data/`, `templates/`)

| Resource | Used by |
|----------|---------|
| `data/hardware_models.json` | `HardwareNormalizer`, `VmScrubber` |
| `data/kb_updates.json` | `UpdateArtifacts` |
| `data/wordlists/` | Filesystem, browser services |
| `templates/registry/common_services.json` | `ProcessFaker` |
| `templates/browser/` | Browser services |
| `templates/documents/` | Filesystem services |

### 5. Profiles (`profiles/`)

```
profiles/base.yaml          ← merged first
profiles/home_user.yaml     ← or developer.yaml / office_user.yaml
```

Deep-merged by `ProfileEngine` using `deepmerge`, then validated by a frozen
Pydantic `ProfileContext` model. See `docs/profile_schema.md` for field
definitions.

---

## Binary I/O Adapters

### `HiveWriter`

- Reads the existing hive with **regipy** (read-only) to locate cell offsets.
- Writes new/modified values directly at the binary level (no regipy write API).
- All changes expressed as a list of `HiveOperation(BaseModel)` objects built by
  the calling service, then executed atomically.

### `EvtxWriter`

- Produces a binary-valid EVTX from scratch — no dependency on an existing log.
- Structure: 4096-byte file header (`ElfFile\0`), one or more 65536-byte chunks
  (`ElfChnk\0`), each containing variable-length records (`**` magic `0x2A2A`).
- CRC32 checksums computed over header and chunk regions per the EVTX spec.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| BaseService ABC + constructor injection | Enables full mock-based unit testing without mounting a real image |
| Deterministic RNG seeded on `(computer_name, profile_type)` | Reproducible runs; same seed → same artefacts |
| Frozen Pydantic models throughout | Catch config errors at load time, not mid-run |
| Single `_VM_STRINGS` source in `identity_generator.py` | One canonical set; `VmScrubber` imports and extends it |
| Operation-list pattern (build then execute) | Allows dry-run inspection; simplifies error recovery |
| AuditLogger as append-only list | Zero side-effects during testing; entries queryable after run |

---

## Data Flow Summary

```
config.yaml + profile YAML
        │
        ▼
ProfileEngine  ──▶  ProfileContext (frozen Pydantic model)
        │
        ▼
IdentityGenerator  ──▶  IdentityBundle
        │                  (computer_name, username, hardware, timestamps …)
        ▼
Per-service apply(context)
        │
        ├──▶  [HiveOperation, …]  ──▶  HiveWriter.execute_operations()
        │                                  └──▶  hive file on mounted image
        │
        └──▶  [EvtxRecord, …]    ──▶  EvtxWriter.write_records()
                                         └──▶  .evtx file on mounted image

All service calls emit entries to AuditLogger
        │
        ▼
audit_logger.entries  →  JSON-serialisable list (see change_log_format.md)
```
