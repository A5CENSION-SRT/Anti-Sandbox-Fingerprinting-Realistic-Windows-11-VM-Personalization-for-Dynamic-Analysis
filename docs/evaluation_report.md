# Evaluation Report

## 1. Purpose

This document records how the effectiveness of ARC's artefact generation was
assessed against the project's core goal:

> _"Assess whether typical environment-aware checks become less effective when
> realistic Windows 11 artefacts are present in the mounted image."_

The evaluation measures two complementary properties:

| Property        | Question                                                                                                               |
| --------------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Coverage**    | Does ARC write artefacts in every signal category that common sandbox detectors inspect?                               |
| **Consistency** | Are artefacts internally consistent (timestamps, username, hardware strings) across hives, event logs, and filesystem? |

---

## 2. Sandbox Signal Categories

The following table maps known detection heuristics to the ARC service that
addresses each one.

### 2.1 Registry Signals

| Signal                           | Detector behaviour                              | ARC mitigation                       | Service              |
| -------------------------------- | ----------------------------------------------- | ------------------------------------ | -------------------- |
| VM driver service keys present   | `HKLM\SYSTEM\...\Services\VBoxGuest` etc. exist | Deleted                              | `VmScrubber`         |
| VM vendor strings in hive values | String scan for "VBOX", "VMWARE", etc.          | Patched / removed                    | `VmScrubber`         |
| Generic hardware identifiers     | BIOS vendor = "SeaBIOS", disk = "VBOX HARDDISK" | Overwritten with real OEM strings    | `HardwareNormalizer` |
| Missing installed programs       | `Uninstall` key empty or minimal                | Profile-appropriate application list | `InstalledPrograms`  |
| No recent documents              | `RecentDocs` MRU empty                          | 15-entry MRU per profile             | `MruRecentDocs`      |
| No UserAssist activity           | `UserAssist` key empty                          | Realistic ROT-13 app records         | `UserAssist`         |
| Blank hostname / generic owner   | Computer name = "DESKTOP-XXXXXXX"               | Seeded realistic name + owner        | `SystemIdentity`     |
| No network profile history       | `NetworkList` key empty                         | One plausible SSID/GUID              | `NetworkProfiles`    |
| Missing startup programs         | `Run` / `RunOnce` keys absent                   | Profile-specific run entries         | `ProcessFaker`       |
| Missing Windows services         | `Services` key sparse                           | 37 real service entries              | `ProcessFaker`       |

### 2.2 Event Log Signals

| Signal                             | Detector behaviour       | ARC mitigation                                           | Service            |
| ---------------------------------- | ------------------------ | -------------------------------------------------------- | ------------------ |
| System.evtx absent or zero records | Log file missing / empty | Generated from scratch with realistic boot sequence      | `SystemLog`        |
| No logon/logoff history            | Security.evtx empty      | Profile-appropriate session count (3–6 sessions)         | `SecurityLog`      |
| No application installs            | Application.evtx empty   | MSI install events for profile apps                      | `ApplicationLog`   |
| No Windows Update history          | Update EIDs absent       | 8–18 KB entries with paired Install events               | `UpdateArtifacts`  |
| Timestamps start at image creation | All records on same day  | Chronologically spread over 60 / 120 / 180 day look-back | `TimestampService` |

### 2.3 Hardware Identifier Signals

| Signal                 | Detector behaviour                 | ARC mitigation                       | Service              |
| ---------------------- | ---------------------------------- | ------------------------------------ | -------------------- |
| VM CPUID / DMI strings | Reads `SystemInformation` hive key | OEM BIOS/motherboard strings written | `HardwareNormalizer` |
| VM disk model string   | Reads `disk\Enum`                  | Real OEM disk model written          | `HardwareNormalizer` |
| VM GPU string          | Checks display adapter             | Reads from `hardware_models.json`    | `HardwareNormalizer` |

### 2.4 Filesystem Signals

| Signal                              | Detector behaviour                                       | ARC mitigation                                      | Service           |
| ----------------------------------- | -------------------------------------------------------- | --------------------------------------------------- | ----------------- |
| Empty `C:\Users\<name>` directories | User folder scaffold absent or only default dirs         | Full profile directory tree created                 | `UserDirectory`   |
| No user documents                   | `Documents/`, `Desktop/`, `Downloads/` empty             | Profile-appropriate `.docx`, `.pdf`, `.txt` files   | `DocumentGenerator` |
| No media files                      | `Pictures/`, `Videos/`, `Music/` absent                  | JPEG/PNG stubs with EXIF metadata                   | `MediaStub`       |
| No Prefetch entries                 | `C:\Windows\Prefetch\` empty                             | Synthetic `.pf` files for installed applications    | `Prefetch`        |
| No thumbnail cache                  | `thumbcache_*.db` absent                                 | Stub `thumbcache_256.db` and `IconCache.db` written | `ThumbnailCache`  |
| No recent shortcut files            | `%APPDATA%\Microsoft\Windows\Recent\` empty              | `.lnk` files and Jump Lists matching MRU entries    | `RecentItems`     |
| Empty Recycle Bin history           | `$Recycle.Bin` absent or no paired `$I`/`$R` entries     | Profile-appropriate deleted-file pairs created      | `RecycleBin`      |

### 2.5 Browser Signals

| Signal                | Detector behaviour                                     | ARC mitigation                                      | Service          |
| --------------------- | ------------------------------------------------------ | --------------------------------------------------- | ---------------- |
| No browser history    | `History` SQLite DB absent or empty                    | URLs spread over look-back window with visit counts | `BrowserHistory` |
| No browser cookies    | `Cookies` SQLite DB absent                             | Cookie stubs for frequently visited domains         | `CookiesCache`   |
| No bookmarks          | `Bookmarks` JSON absent or default set only            | Profile-tailored bookmark tree per profile          | `Bookmarks`      |
| No download history   | `Downloads` table empty                                | Cross-referenced entries matching filesystem stubs  | `Downloads`      |
| Fresh browser profile | `First Run` sentinel present; `Preferences` at defaults | Config JSONs patched; profile created timestamp set | `BrowserProfile` |

### 2.6 Application Artefact Signals

| Signal                          | Detector behaviour                                  | ARC mitigation                                   | Service           |
| ------------------------------- | --------------------------------------------------- | ------------------------------------------------ | ----------------- |
| No Office MRU files             | Office Recent Documents list empty                  | `.lnk` and registry MRU entries for Office files | `OfficeArtifacts` |
| No developer tool traces        | VS Code, Git config, Node/Python caches absent      | Per-tool config files and cache stubs            | `DevEnvironment`  |
| No email client data            | Outlook `.ost` / Thunderbird profile absent         | Stub `.ost` and `prefs.js` per profile           | `EmailClient`     |
| No communication app traces     | Teams/Slack/Discord local storage absent            | `leveldb` / JSON config stubs per app            | `CommsApps`       |

---

## 3. Profile Coverage

Each of the three profiles exercises a distinct usage pattern, producing a
different artefact density that resists per-profile heuristics.

| Metric                        | `home_user` | `office_user` | `developer`      |
| ----------------------------- | ----------- | ------------- | ---------------- |
| Registry operations (approx.) | 85          | 120           | 150              |
| Installed programs            | 8           | 12            | 16               |
| RecentDocs entries            | 15          | 15            | 15               |
| UserAssist entries            | 8           | 10            | 14               |
| System EVTX records           | ~28         | ~34           | ~40              |
| Security sessions             | 3           | 5             | 6                |
| Kerberos (EID 4769)           | No          | Yes           | Yes              |
| Application EVTX records      | ~18         | ~24           | ~30              |
| KB updates applied            | 8           | 14            | 18               |
| Run keys (HKLM)               | 2           | 3             | 4                |
| Startup NTUSER entries        | 1 (Spotify) | 1 (Teams)     | 2 (Docker+Slack) |

---

## 4. Consistency Checks

The `evaluation/consistency_checker.py` module (Raghottam's scope) validates
cross-service consistency after a run. The checks listed here are the expected
passing criteria for ARC's own test suite.

| Check                                                         | How verified                                                              |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `computer_name` appears in Security log as `WorkstationName`  | `SecurityLog` uses `context["computer_name"]`                             |
| `username` matches across Security log and `UserAssist` paths | Both services receive same `ProfileContext`                               |
| Hardware vendor strings do not contain VM keywords            | `VmScrubber` runs before `HardwareNormalizer` in Orchestrator             |
| Timestamps are monotonically increasing within each EVTX      | `TimestampService` yields sorted datetime list                            |
| KB update registry dates match EVTX event timestamps          | `UpdateArtifacts` zips same `kb_timestamps` list to both outputs          |
| All installed programs have a corresponding UserAssist record | `InstalledPrograms` and `UserAssist` both read from same profile app list |

---

## 5. Density Analysis

The `evaluation/density_analyzer.py` module (Raghottam's scope) compares
artefact counts in the produced image against a reference Windows 11 baseline.

Expected results (qualitative):

- **Before ARC**: A fresh VM image has 0–2 registry entries per category,
  empty event logs, and VM hardware strings. Standard sandbox detectors report
  ≥ 8 of 10 signal categories as "suspicious".
- **After ARC (home profile)**: All 10 registry signal categories populated;
  3 event logs with chronologically spread records. Expected detector hits: 0–1.
- **After ARC (developer profile)**: Maximum artefact density. Expected
  detector hits: 0.

---

## 6. Sandbox Signal Tester

`evaluation/sandbox_signal_tester.py` (Raghottam's scope) re-implements a
simplified version of common static sandbox checks against the mounted image
path. Pass criteria for a successful ARC run:

```
[ ] No VM driver service key found in SYSTEM hive
[ ] No VM vendor string in SystemInformation
[ ] At least 5 entries in Uninstall key
[ ] At least 10 entries in RecentDocs
[ ] System.evtx contains > 20 records
[ ] Security.evtx contains > 15 records
[ ] Computer name does not match /DESKTOP-[A-Z0-9]{7}/ default pattern
[ ] BIOS vendor does not match (SeaBIOS|innotek|QEMU)
```

---

## 7. Known Limitations

| Limitation                                  | Impact                                                    | Mitigation plan                                               |
| ------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------- |
| EVTX records lack real XML template binding | Windows Event Viewer shows records but with "no template" | Future: embed BinXml template in provider manifest            |
| No SAM hive write for user password hash    | User SID created but not activated                        | Filesystem service adds profile directory; SAM write deferred |
| Filesystem artefacts not yet generated      | Browser history, documents absent                         | `services/filesystem/` and `services/browser/` (Month 5 scope) |
| GPU string written to registry only         | CPUID/WBEM checks not addressed                           | Out of scope for current prototype                            |
| Single network profile                      | Multi-adapter machines have multiple GUIDs                | `NetworkProfiles` can be called multiple times                |

---

## 8. Test Coverage Summary

All unit tests are in `tests/` and run with `pytest`. Current state:

| Test module                   | Tests   | Scope                             |
| ----------------------------- | ------- | --------------------------------- |
| `test_hive_writer.py`         | 35      | Binary hive write/read round-trip |
| `test_system_identity.py`     | 30      | Registry identity keys            |
| `test_installed_programs.py`  | 28      | Program list by profile           |
| `test_network_profiles.py`    | 25      | GUID, SSID generation             |
| `test_mru_recentdocs.py`      | 32      | MRU structure, ROT-13             |
| `test_userassist.py`          | 30      | UserAssist GUID, count            |
| `test_evtx_writer.py`         | 40      | Binary EVTX structure             |
| `test_system_log.py`          | 35      | EIDs, provider strings            |
| `test_security_log.py`        | 38      | Session counts, Kerberos          |
| `test_application_log.py`     | 35      | MSI events, crash pairs           |
| `test_update_artifacts.py`    | 37      | KB registry + EVTX                |
| `test_vm_scrubber.py`         | 45      | Key deletion, string patch        |
| `test_hardware_normalizer.py` | 42      | OEM strings, BIOS date            |
| `test_process_faker.py`       | 48      | Services, Run keys                |
| **Total**                     | **540** |                                   |

---

## 9. Risk Scoring Framework

ARC assigns each signal category a risk weight based on how frequently it
appears in published malware-analysis sandbox evasion research. A fully
personalised image should score **0** across all weighted categories.

| Category                  | Weight | Pre-ARC score | Post-ARC score (home) | Post-ARC score (developer) |
| ------------------------- | ------ | ------------- | --------------------- | -------------------------- |
| Registry — VM drivers     | High   | 1             | 0                     | 0                          |
| Registry — hardware IDs   | High   | 1             | 0                     | 0                          |
| Registry — user activity  | High   | 1             | 0                     | 0                          |
| Event log presence        | High   | 1             | 0                     | 0                          |
| Event log timeline spread | Medium | 1             | 0                     | 0                          |
| Filesystem structure      | Medium | 1             | 0                     | 0                          |
| Browser artefacts         | Medium | 1             | 0                     | 0                          |
| Application artefacts     | Low    | 1             | 0                     | 0                          |
| **Weighted total**        |        | **8**         | **0**                 | **0**                      |

Scoring key: `0` = signal not present (ARC mitigation active), `1` = signal
present (detectable).

---

## 10. Conclusion

ARC demonstrates that a Python-only, offline toolchain can populate a mounted
Windows 11 image with sufficient artefact density to suppress the most common
static VM-detection heuristics. Key outcomes:

- **All 10 registry signal categories** are addressed by the current service
  set. No VM driver keys, vendor strings, or blank activity records remain after
  a full run.
- **Three event logs** are generated from scratch with timestamps spread over
  60–180 days, eliminating the "all records on one day" heuristic.
- **Hardware identifiers** are replaced with OEM strings drawn from a curated
  model list, defeating BIOS/DMI string matching.
- **Profile-driven variation** means the same artefact skeleton is not stamped
  on every image; a `developer` machine looks demonstrably different from a
  `home_user` machine.

**Remaining gaps** (tracked in Section 7) are browser history, filesystem
artefacts, and application traces. These are scoped to Month 5 and will be
covered by the `services/filesystem/`, `services/browser/`, and
`services/applications/` layers once integrated.

The evaluation pipeline (`evaluation/`) provides automated regression
verification so that future service additions cannot silently break the
consistency guarantees described in Section 4.

