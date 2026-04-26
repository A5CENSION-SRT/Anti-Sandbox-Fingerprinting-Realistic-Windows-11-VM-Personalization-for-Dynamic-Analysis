# ARC — System Architecture

## Overview

ARC (Artifact Reality Composer) takes a post-OOBE Windows 11 VHDX and rewrites it offline to
look like a machine with ~360 days of genuine user activity. The host runs Linux (Ubuntu 24.04+).
All I/O to the image goes through the libguestfs / hivex / ntfs-3g stack — no PowerShell, no
Windows dependency, no Z: drive.

```
core/persona_context.py    — unified PersonaContext schema (single source of truth)
core/persona_loader.py     — YAML → PersonaContext; preset or AI-generated path
core/service_context.py    — typed ServiceContext dataclass threaded through all services
core/event_scheduler.py    — cross-domain deterministic event stream
core/linux_mount.py        — LinuxMountBackend: libguestfs + hivex + guestmount
core/orchestrator.py       — phase runner; owns execution order

services/expansion/        — ExpansionOrchestrator → ExpansionBundle
services/registry/         — 5 domain services; hivex-backed HiveWriter
services/browser/          — history, downloads, bookmarks, cookies, CDP logs
services/filesystem/       — Prefetch, cross_writer, documents, office_mru, ps_history
services/eventlog/         — evtx_writer + 4 channel services
services/applications/     — scheduler-aware app-activity services
services/ntfs/             — mft_timestamp_patcher, usn_journal_writer, logfile_writer
services/anti_fingerprint/ — VmScrubber, HardwareNormalizer, MacHygiene, ProcessFaker

profiles/presets/          — developer.yaml / office_user.yaml / home_user.yaml
```

---

## Unified PersonaContext Schema

`core/persona_context.py` is the single persona schema. It is a frozen Pydantic `BaseModel`
with `extra="forbid"`. Every field that any service needs is defined here; there is no secondary
schema and no lossy filter.

`PersonaLoader` (`core/persona_loader.py`) loads a preset YAML or accepts an AI-generated
`PersonaContext` directly. Validation errors are loud — unknown fields raise `ValidationError`
immediately. The old `ProfileContext` (6-field) and its silent `allowed_fields` filter are
deleted (ADR-001).

---

## ServiceContext

`core/service_context.py` defines the frozen dataclass threaded through every
`BaseService.apply()`. Key fields: `persona` (PersonaContext), `mount` (MountManager),
`rng` (Random), `audit` (AuditLogger), `timestamp_service`, `install_time`, `boot_time`,
`identity_bundle`, `scheduler` (Optional — populated in SCHEDULING phase), `expansion`
(Optional — populated in EXPANSION phase).

No service receives `context: dict`. `ctx.persona.<field>` is the only legal field-access
pattern (CI grep-gate A2). `frozen=True` prevents services from mutating shared state.

---

## Execution Phases

```
INFRASTRUCTURE → EXPANSION → SCHEDULING → FILESYSTEM → REGISTRY → BROWSER
    → APPLICATIONS → EVENTLOG → ANTI_FINGERPRINT → NTFS → EVALUATION
```

INFRASTRUCTURE builds identity. EXPANSION runs `ExpansionOrchestrator` and produces the
`ExpansionBundle`. SCHEDULING constructs `EventScheduler` and pre-emits the full event stream.
FILESYSTEM through EVENTLOG are the artifact-writing phases — each consumes `ctx.scheduler`
events of their relevant kinds. ANTI_FINGERPRINT scrubs VM-detection markers. NTFS patches
`$STANDARD_INFORMATION` timestamps and appends `$UsnJrnl:$J` records for all scheduler
file-touch events. EVALUATION runs `TemporalCoherenceCheck` and density assertions.

---

## LinuxMountBackend

`core/linux_mount.py::LinuxMountBackend` provides three I/O surfaces:

### libguestfs (general file I/O)

The primary mount path. `mount()` launches the guestfs appliance, inspects the image with
`inspect_os()`, and mounts all partitions. Services call `read_bytes()`, `write_bytes()`,
`mkdir_p()`, `utimens()`, `set_ntfs_attributes()`. This covers registry hives, EVTX files, and
all filesystem artifacts.

```python
with LinuxMountBackend(Path("windows.vhdx")) as backend:
    backend.write_bytes("/Users/alexj/Documents/notes.txt", b"content")
```

### hivex (registry hive writes)

`open_hive(guest_path)` is a context manager that pulls the hive from the image to a host
tempfile, opens it with `hivex.Hivex(write=True)`, yields a `HivexHandle`, then commits and
writes the modified hive back. After commit, `.LOG1` and `.LOG2` are deleted so Windows
does not replay the old transaction log and silently roll back ARC's writes (ADR-010).

```python
with backend.open_hive("/Windows/System32/config/SOFTWARE") as h:
    ms = h.h.node_get_child(h.h.root(), "Microsoft")
```

### guestmount / ntfs-3g FUSE (raw NTFS stream access)

`host_fuse_mount()` runs `guestmount --rw --inspector` and returns a host `Path`. The NTFS
phase uses this for two operations that libguestfs cannot do cleanly:

- `setfattr -n system.ntfs_times` to patch `$STANDARD_INFORMATION` timestamps.
- Colon-path stream access for `$Extend\$UsnJrnl:$J` raw appending (ADR-008).

guestfs must be unmounted before the FUSE mount is raised (one writer at a time). The orchestrator
handles this sequencing between phases 8 and 9.

---

## EventScheduler

`core/event_scheduler.py::EventScheduler` is the single source of time and randomness for all
services. It walks `[install_time, now]` day by day, emitting `SyntheticEvent` objects for each
active day (respecting `persona.active_days` and `persona.work_hours_*`). Event counts per
session are Poisson-distributed.

```python
@dataclass(frozen=True)
class SyntheticEvent:
    kind: Literal["APP_LAUNCH","FILE_CREATE","FILE_MODIFY","FILE_DELETE",
                  "URL_VISIT","URL_DOWNLOAD","LOGIN","LOGOFF","SYSTEM_UPDATE"]
    timestamp: datetime  # always UTC
    payload: dict[str, Any]
```

Services consume events by kind:

```python
for event in ctx.scheduler.events_of("APP_LAUNCH"):
    # write Prefetch entry, EVTX 4688, UserAssist bump
```

Each service gets a deterministic child RNG:

```python
rng = ctx.scheduler.child_rng("registry.userassist")
```

Child RNGs are seeded from `hash(master_seed ^ hash(name))`. Adding or removing a service does
not affect other services' sequences. Direct `datetime.now()` or `Random(N)` in services is
banned (CI grep-gate A1, ADR-005, ADR-012).

---

## ExpansionBundle

`services/expansion/` runs in the EXPANSION phase before any artifact services. It reads
per-day rates from `config.yaml::artifact_scale` and materialises artifact descriptors:

```
per_day × persona.timeline_days × (1 ± jitter) = target count
```

The resulting `ExpansionBundle` holds lists of `DocumentDescriptor`, `DownloadDescriptor`,
`MediaDescriptor`, and `BrowsingDescriptor`. Downstream services read from `ctx.expansion`
instead of their own `Random(42)` pools. Seed generators (`RegistrySeedGenerator`,
`EvtxSeedGenerator`, `PrefetchSeedGenerator`) populate the registry, EVTX, and Prefetch
seeds from the same expansion phase.

---

## Cross-Domain Forensic Coherence

A single scheduler event fans out to multiple domains. A missed fan-out is detectable by
cross-domain forensics tools (NTFS Triforce, Eric Zimmerman's suite).

- `APP_LAUNCH(app, t)` → Prefetch `last_run_times[0]=t`; EVTX 4688; UserAssist ROT13 counter bump; RecentApps MRU
- `FILE_CREATE(path, t)` → $MFT SI ctime=t; $UsnJrnl FILE_CREATE; EVTX 4663 (SACL); RecentDocs MRU; shell .lnk; Office MRU
- `FILE_MODIFY(path, t)` → $MFT SI mtime=t; $UsnJrnl DATA_OVERWRITE; parent dir ctime bumped
- `URL_VISIT(url, t)` → Chrome History urls + visits rows; Cookies last_accessed
- `URL_DOWNLOAD(url, t)` → FILE_CREATE fan-out + Chrome downloads row + Zone.Identifier ADS
- `LOGIN(t)` / `LOGOFF(t)` → EVTX 4624/4634; System 6005/6006/6013; NTUSER.DAT LastWriteTime

`evaluation/consistency_checker.py::TemporalCoherenceCheck` validates after each run:
`APP_LAUNCH(app, t)` must have a matching EVTX 4688 within ±2 s and a `.pf`
`last_run_times[0]` within ±5 s (acceptance gate A9).

---

## VM-Detection Evasion

ARC covers the guest-side (registry) surface. The hypervisor-side (SMBIOS, CPUID, MAC OUI,
disk serial) is the operator's responsibility via `examples/libvirt-profile-template.xml`
(ADR-011).

| Service | Mechanism |
|---|---|
| `VmScrubber` | Deletes service keys: VBoxService, VBoxSF, VBoxGuest, VBoxVideo, VBoxMouse, vmtools, vmmouse, vmci, vmhgfs, vmxnet, vioscsi, viostor, qemu-ga, kvm*; deletes VBox/VMware Uninstall entries |
| `HardwareNormalizer` | Writes Dell/HP/Lenovo vendor strings to `HKLM\HARDWARE\Description\System` (SystemBiosVersion, VideoBiosVersion); patches `HKLM\HARDWARE\DEVICEMAP\Scsi\...\Identifier` (QEMU HARDDISK → Samsung SSD 970 EVO Plus) |
| `MacHygiene` | Walks `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-*}\000X`; sets `NetworkAddress` to Intel OUI `00:1b:21:xx:xx:xx` or Realtek `00:e0:4c:xx:xx:xx` |
| `ProcessFaker` | Populates 37 real Windows service keys from `templates/registry/common_services.json`; sets profile-specific `Run` keys |

`identity_generator.py::_VM_STRINGS` is the single canonical list of VM indicator strings;
`VmScrubber` imports and extends it (includes `qemu-ga`, `kvm`, `KVMKVMKVM\0\0\0`).
