# ARC — Service Agent Reference

Each service in ARC is a `BaseService` subclass with a single `apply(ctx: ServiceContext)` method.
This file maps service names to what they produce and what context fields they consume.

---

## Infrastructure Services (INFRASTRUCTURE phase)

These run before any artifact service and populate identity + mount state.

| Service | Module | Produces |
|---|---|---|
| `UserDirectoryService` | `services/filesystem/user_directory.py` | `C:\Users\<username>\` skeleton |
| `SystemIdentity` | `services/registry/system_identity.py` | Computer name, SID, MachineGUID |

---

## Expansion (EXPANSION phase)

| Service | Module | Produces |
|---|---|---|
| `ExpansionOrchestrator` | `services/expansion/orchestrator.py` | `ctx.expansion: ExpansionBundle` with DocumentDescriptor/DownloadDescriptor/MediaDescriptor/BrowsingDescriptor lists and seed objects |

---

## Registry Services (REGISTRY phase)

All write to NTUSER.DAT or SYSTEM/SOFTWARE hives via `HiveWriter`.

| Service | Module | Produces |
|---|---|---|
| `HiveWriter` | `services/registry/hive_writer.py` | Base hivex write infrastructure |
| `InstalledPrograms` | `services/registry/installed_programs.py` | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*` |
| `MruRecentDocs` | `services/registry/mru_recentdocs.py` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs\*` |
| `NetworkProfiles` | `services/registry/network_profiles.py` | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles\*` |
| `UserAssist` | `services/registry/userassist.py` | ROT13-encoded `HKCU\...\UserAssist\*` run counts |
| `TypingHistory` | `services/registry/typing_history.py` | TypedURLs, TypedPaths, WordWheelQuery (A10) |

---

## Filesystem Services (FILESYSTEM phase)

| Service | Module | Produces |
|---|---|---|
| `DocumentGenerator` | `services/filesystem/document_generator.py` | ≥500 DOCX/XLSX/PDF/txt/md/py/json files under `Users/<user>/` (A14) |
| `PrefetchService` | `services/filesystem/prefetch.py` | ≥30 SCCA v30 `.pf` files in `Windows/Prefetch/`, mean ≥15 KB (A11) |
| `OfficeMruService` | `services/filesystem/office_mru.py` | Office MRU registry keys + file stubs |
| `PowerShellHistoryService` | `services/filesystem/powershell_history.py` | `ConsoleHost_history.txt` |
| `CdpLogsService` | `services/filesystem/cdp_logs.py` | ConnectedDevicesPlatform log dirs |
| `ThumbnailCacheService` | `services/filesystem/thumbnail_cache.py` | `thumbcache_*.db` stubs |
| `RecentItemsService` | `services/filesystem/recent_items.py` | Shell Recent `.lnk` files |
| `RecycleBinService` | `services/filesystem/recycle_bin.py` | `$Recycle.Bin` stubs |
| `InstalledAppsStub` | `services/filesystem/installed_apps_stub.py` | Executable stubs for `persona.installed_apps` |
| `MediaStubService` | `services/filesystem/media_stub.py` | `.mp3`/`.mp4`/`.jpg` stubs |

---

## Browser Services (BROWSER phase)

| Service | Module | Produces |
|---|---|---|
| `BrowserProfileService` | `services/browser/browser_profile.py` | Chrome/Edge `User Data/Default/` skeleton |
| `BrowserHistoryService` | `services/browser/history.py` | Chrome `History` SQLite: ≥5 000 urls, ≥10 000 visits (A13) |
| `BrowserDownloadService` | `services/browser/downloads.py` | Download files + `Zone.Identifier` ADS stubs (R27) |
| `BookmarksService` | `services/browser/bookmarks.py` | Chrome `Bookmarks` JSON |
| `CookiesCacheService` | `services/browser/cookies_cache.py` | `Cookies` SQLite + cache stubs |

---

## Application Services (APPLICATIONS phase)

| Service | Module | Produces |
|---|---|---|
| `DevEnvironment` | `services/applications/dev_environment.py` | `.gitconfig`, SSH keys, VS Code settings, Docker dirs |
| `OfficeArtifacts` | `services/applications/office_artifacts.py` | Office document stubs + LNK shortcuts |
| `EmailClient` | `services/applications/email_client.py` | Outlook profile XML + `.pst` stubs |
| `CommsApps` | `services/applications/comms_apps.py` | Teams, Slack, Discord, Zoom application dirs |

---

## Event Log Services (EVENTLOG phase)

| Service | Module | Produces |
|---|---|---|
| `EvtxWriter` | `services/eventlog/evtx_writer.py` | Multi-chunk binary EVTX files via `write_records()` |
| `SecurityLog` | `services/eventlog/security_log.py` | `Security.evtx`: 4624/4634/4688/4689/4648/4672/4769 — ≥10 MB (A12) |
| `SystemLog` | `services/eventlog/system_log.py` | `System.evtx`: 6005/6006/7036 + service events |
| `ApplicationLog` | `services/eventlog/application_log.py` | `Application.evtx`: MSI installs, app crashes |
| `UpdateArtifacts` | `services/eventlog/update_artifacts.py` | Windows Update history + CBS.log stubs |

---

## Anti-Fingerprint Services (ANTI_FINGERPRINT phase)

| Service | Module | Scrubs / Replaces |
|---|---|---|
| `VmScrubber` | `services/anti_fingerprint/vm_scrubber.py` | VBox/VMware/QEMU/KVM service keys and Uninstall entries |
| `HardwareNormalizer` | `services/anti_fingerprint/hardware_normalizer.py` | BIOS/SCSI/GPU vendor strings → Dell/HP/Lenovo |
| `MacHygiene` | `services/anti_fingerprint/mac_hygiene.py` | NIC `NetworkAddress` → Intel or Realtek OUI |
| `ProcessFaker` | `services/anti_fingerprint/process_faker.py` | 37 real Windows service registry keys; persona-specific Run keys |

---

## NTFS Services (NTFS phase)

Require guestmount FUSE mount (sequential with libguestfs phases).

| Service | Module | Produces |
|---|---|---|
| `MftTimestampPatcher` | `services/ntfs/mft_timestamp_patcher.py` | `$STANDARD_INFORMATION` SI time patches via `setxattr` |
| `UsnJournalWriter` | `services/ntfs/usn_journal_writer.py` | `$UsnJrnl:$J` USN_RECORD_V2 entries |
| `LogfileWriter` | `services/ntfs/logfile_writer.py` | `$LogFile` stub (best-effort) |

---

## Evaluation Services (EVALUATION phase)

| Service | Module | Validates |
|---|---|---|
| `TemporalCoherenceCheck` | `evaluation/consistency_checker.py` | APP_LAUNCH → EVTX 4688 within ±2 s; APP_LAUNCH → Prefetch within ±5 s (A9) |

---

## Writing a new service

```python
from services.base_service import BaseService
from core.service_context import ServiceContext

class MyService(BaseService):
    @property
    def service_name(self) -> str:
        return "MyService"

    def apply(self, ctx: ServiceContext) -> None:
        rng = ctx.scheduler.child_rng("my_service")
        # write artifacts using ctx.mount, ctx.persona, ctx.scheduler ...
        self.audit_logger.log(self.service_name, {"files_written": n})
```

Rules enforced by CI:
- No `datetime.now()` (gate A1) — use `ctx.timestamp_service` or scheduler events.
- No `ctx["key"]` access (gate A4) — use `ctx.persona.<field>`.
- No `import win32*` (gate A2).
- No PowerShell subprocess calls (gate A3).
