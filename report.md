# Anti-Sandbox Fingerprinting: Realistic Windows 11 VM Personalization for Dynamic Analysis

**Authors:** Raghottam, Sumukha, Snehal
**Date:** April 2026

---

## Abstract

Automated malware analysis sandboxes face a persistent adversarial arms race: modern evasive malware detects sterile virtual environments by scanning for the absence of genuine user activity and rejecting execution. ARC (Artifact Reality Composer) is a Python framework that transforms a pristine Windows 11 VHDX offline into a convincing lived-in workstation without booting the VM. It injects coherent, profile-driven artifacts across registry, filesystem, NTFS journal, event logs, and browser history — spanning a configurable history window of up to 730 days. The generated artifacts satisfy strict cross-domain temporal coherence (an APP_LAUNCH event fans out to Prefetch, EVTX 4688, UserAssist, and RecentApps within ±2–5 seconds). Against pafish and Al-Khaser, ARC reduces flagged indicators from ~52 (sterile baseline) to ≤10, achieving a Detection Resistance Score of 0.92. All generation is deterministic given a fixed seed, enabling reproducible analyst hand-offs.

---

## 1. Introduction

Dynamic analysis (detonating malware in a VM and observing behavior) is a cornerstone of modern threat intelligence. Its effectiveness is undermined by environment-aware malware that inspects the host for tell-tale signs of a sandbox: empty Recent Documents folders, zero browser history, timestamp clustering where thousands of files share the same creation second, and registry keys from hypervisor guest addition drivers.

Existing defenses fall into two categories. Hypervisor-level transparency patches (CPUID masking, RDTSC normalization) address hardware timing leaks but leave the data layer untouched. Manual "golden snapshot" preparation — an analyst spending hours creating files and browsing the web — does not scale and cannot reproduce a convincing 360-day activity history.

ARC addresses the data layer. It operates entirely offline on a mounted VHDX, requires no Windows boot, and produces artifacts that are statistically indistinguishable from genuine use across the subsystems that evasive malware probes most heavily.

### 1.1 Problem Statement

A sterile sandbox exhibits four detectable anomalies (MITRE T1497.001):
- **T2 — Artifact absence**: no installed programs, empty MRU lists, empty browser history.
- **T3 — Temporal clustering**: bulk file creation timestamps within seconds of each other.
- **T4 — Behavioral entropy**: browser cookies with uniform expiry, event logs with minimal records.
- **T1 — Hardware strings**: BIOS/SCSI/NIC identifiers identifying VMware, VirtualBox, QEMU, or KVM.

ARC targets T2, T3, T4 directly. T1 (BIOS/SCSI/NIC strings) is addressed at both the registry layer (HardwareNormalizer, MacHygiene) and the hypervisor layer via the bundled libvirt domain template.

### 1.2 Contributions

1. A deterministic, service-oriented artifact pipeline that generates 360 days of coherent activity without booting the target VM.
2. A formal temporal coherence model: a single scheduler event fans out to all relevant domains within tight ±2–5 s windows.
3. An offline registry manipulation stack (hivex) that rewrites Windows NT binary hives without hypervisor overhead, including post-write transaction log deletion to prevent Windows rollback.
4. An empirical evaluation against pafish, Al-Khaser, and a custom density/coherence suite, with all acceptance gate thresholds published and tested.

---

## 2. Background

### 2.1 Sandbox Architecture

A typical sandbox (Cuckoo, CAPE, Any.run) instantiates a VM from a golden snapshot, places a malicious binary, runs it for 2–5 minutes under a monitoring agent, then reverts. The monitoring agent is either a user-land API hook or a kernel driver. The VM is never used for anything other than analysis.

### 2.2 Adversary Model

We assume a malware author with the following capabilities:
- Query WMI for SMBIOS strings, CPU topology, and MAC address OUI.
- Enumerate the filesystem and registry for installed application footprints and MRU history.
- Inspect NTFS metadata ($STANDARD_INFORMATION timestamps, $UsnJrnl) for temporal patterns.
- Check browser SQLite databases (Chrome History, Cookies) for record counts and timestamp distribution.
- Read Windows Event Log binary files (.evtx) for record density and event ID diversity.

Our goal is to defeat all data-layer checks. Hardware timing attacks (RDTSC) are out of scope; they require VMM-level mitigation.

### 2.3 Prior Work

Miramirkhani et al. (2017) coined "wear-and-tear artifacts" and demonstrated that sandboxes are reliably identified by the absence of user-generated data. Vasenkov and Kholod (2022) formalized a taxonomy of sandbox evasion techniques confirming that artifact absence remains the most reliable detection vector. Bulazel and Yener (2017) surveyed automated dynamic analysis evasion and counter-evasion, noting that temporal clustering is trivially detectable. ARC is the first system to address all four detection vectors (T1–T4) at the data layer in a deterministic, scalable, Linux-native pipeline.

---

## 3. System Architecture

ARC is organized as an Orchestrator-driven phase pipeline. Each phase runs a set of `BaseService` subclasses that receive an immutable `ServiceContext` and produce artifacts to a mounted VHDX.

### 3.1 Execution Phases

```
INFRASTRUCTURE → EXPANSION → SCHEDULING → FILESYSTEM → REGISTRY → BROWSER
    → APPLICATIONS → EVENTLOG → ANTI_FINGERPRINT → NTFS → EVALUATION
```

**INFRASTRUCTURE** builds the identity bundle (username, hostname, SID, hardware profile).
**EXPANSION** runs `ExpansionOrchestrator` and produces an `ExpansionBundle` with target counts for all artifact types.
**SCHEDULING** constructs `EventScheduler` and pre-emits the full event stream across the configured timeline.
**FILESYSTEM through EVENTLOG** are the artifact-writing phases, each consuming scheduler events of their relevant kinds.
**ANTI_FINGERPRINT** scrubs VM-detection markers from registry hives.
**NTFS** patches `$STANDARD_INFORMATION` timestamps and appends `$UsnJrnl:$J` records.
**EVALUATION** runs `TemporalCoherenceCheck` and density assertions.

### 3.2 Determinism

All randomness flows from a single master seed through `EventScheduler`. Each service calls `ctx.scheduler.child_rng("service.name")`, which produces a deterministic `Random` seeded from `hash(master_seed ^ hash(name))`. Adding or removing a service does not perturb any other service's RNG sequence. Direct `datetime.now()` or `Random(N)` in services is banned by CI gate A1.

Given identical `--preset` and `--random-seed`, ARC produces byte-identical audit logs across runs (ADR-012).

### 3.3 LinuxMountBackend

Three I/O surfaces for the VHDX:

- **libguestfs**: primary path for all file read/write. Inspects partitions with `inspect_os()`, mounts them, exposes `write_bytes()`, `mkdir_p()`, `utimens()`.
- **hivex**: offline registry hive editor. `open_hive()` pulls the hive to a host tempfile, opens it with `hivex.Hivex(write=True)`, commits, writes back, then deletes `.LOG1`/`.LOG2` to prevent Windows from replaying the old transaction log and rolling back ARC's writes (ADR-010).
- **guestmount/ntfs-3g FUSE**: raw NTFS stream access for `$STANDARD_INFORMATION` `setxattr` patches and `$UsnJrnl:$J` colon-path appending (ADR-008).

### 3.4 EventScheduler

`EventScheduler` walks `[install_time, now]` day by day, emitting `SyntheticEvent` objects respecting `persona.active_days` and `persona.work_hours_*`. Event counts per session follow a Poisson distribution. Event kinds:

```
APP_LAUNCH, FILE_CREATE, FILE_MODIFY, FILE_DELETE,
URL_VISIT, URL_DOWNLOAD, LOGIN, LOGOFF, SYSTEM_UPDATE
```

A single event fans out to all relevant domains. `APP_LAUNCH(app, t)` causes: Prefetch `last_run_times[0]=t`, EVTX 4688, UserAssist counter bump, RecentApps MRU update.

### 3.5 Anti-Fingerprinting Services

| Service | Mechanism |
|---|---|
| VmScrubber | Deletes VBox/VMware/QEMU/KVM service keys and Uninstall entries |
| HardwareNormalizer | Overwrites BIOS/SCSI/GPU vendor strings with Dell/HP/Lenovo values |
| MacHygiene | Sets NIC `NetworkAddress` to Intel or Realtek OUI |
| ProcessFaker | Populates 37 real Windows service keys and persona-specific Run keys |

Hypervisor-level spoofing (SMBIOS, CPUID masking, disk serial) is provided separately via `examples/libvirt-profile-template.xml`.

---

## 4. Evaluation

### 4.1 Experimental Setup

Three preset profiles (developer, office_user, home_user) were run against a pristine Windows 11 23H2 VHDX baseline with `--timeline-days 360 --random-seed 4242`.

### 4.2 Artifact Density

| Subsystem | Sterile baseline | Real system | ARC output |
|---|---|---|---|
| Registry UserAssist entries | 3 | ~150 | 185 |
| Prefetch files | 0 | 30–100 | 42 |
| EVTX Security records | 142 | 8 000–30 000 | ~14 000 |
| Chrome History URLs | 0 | 5 000–30 000 | ~5 400 |
| Documents (user home) | 0 | 500–5 000 | ~540 |

All density gate thresholds (A10–A14) are met for all three presets.

### 4.3 Temporal Coherence

`TemporalCoherenceCheck` validated cross-domain fan-out over 50 automated runs (seeds 1–50).
Mean coherence score: **98.4%**. Zero runs failed the ±2 s APP_LAUNCH → EVTX 4688 check or the ±5 s APP_LAUNCH → Prefetch check.

### 4.4 Sandbox Signal Testing

Reference tools: pafish v0.6, Al-Khaser (latest main).

| Check | Sterile | ARC |
|---|---|---|
| BIOS/SCSI vendor strings | flagged | clean |
| VM NIC OUI | flagged | clean |
| VBox/VMware service keys | flagged | clean |
| Empty browser history | flagged | clean |
| Empty Recent Documents | flagged | clean |
| Temporal timestamp clustering | flagged | clean |
| CPUID/RDTSC timing | flagged | flagged (hypervisor scope) |

Total flags: sterile baseline ~52, ARC ≤10. Detection Resistance Score: **0.92** (up from 0.15 on the sterile baseline). The remaining flags are hardware timing leaks addressable only at the VMM layer.

---

## 5. Conclusion

ARC demonstrates that systematic, profile-driven VM personalization can reduce sandbox detectability from a DRS of 0.15 to 0.92 purely through data-layer artifact injection. The framework is deterministic, Linux-native, requires no VM boot, and scales to any number of analyst runs. All critical thresholds are codified as pytest acceptance gates (A1–A17) so regressions are caught in CI before deployment.

Remaining limitations: CPU timing leaks (RDTSC, CPUID) require VMM-level mitigation beyond ARC's scope. The $LogFile circular replay (R4) is accepted as a known gap documented in `docs/research/time_integrity.md`.

---

## References

1. Miramirkhani, N., Applegate, M. P., & Nikiforakis, N. (2017). Spotless sandboxes: Evading malware analysis systems using wear-and-tear artifacts. *IEEE S&P*, 1009–1024.
2. Bulazel, A., & Yener, B. (2017). A survey on automated dynamic malware analysis evasion and counter-evasion. *ROOTS*, 1–21.
3. Vasenkov, A., & Kholod, I. (2022). A taxonomy of sandbox evasion techniques. *ElConRus*, 442–446.
4. Zhang, X., Xiao, J., & Guo, Y. (2023). A survey of dynamic malware analysis evasion techniques and defenses. *IEEE Access*, 11, 23098–23115.
5. Al-Ghafari, M., & El-Khatib, K. (2024). Next-generation sandbox evasion: A taxonomy and defensive perspective. *IJIS*, 1–22.
6. Salem, A., Boshmaf, Y., & Al-Ibrahim, O. (2023). Advanced anti-analysis techniques in modern malware. *IEEE S&P*, 21(2), 55–64.
7. Ferrand, O. (2022). State of the art of malware evasion techniques. *JCVHT*, 18(1).
8. Kumar, R., Singh, P., & Sharma, K. (2022). Evading sandbox analysis: A comprehensive survey. *Computers & Security*, 114, 102581.
9. Pi, L., Chen, Y., & Zhao, Z. (2021). A deep learning approach to detect environment-aware malware. *JNCA*, 174, 102898.
10. Zimba, A., Wang, Z., & Chen, H. (2021). Modeling the evolution of malware evasion techniques. *Security and Communication Networks*.
11. Afianian, A., et al. (2019). Malware dynamic analysis evasion techniques: A survey. *ACM CSUR*, 52(6).
12. D'Elia, D. C., et al. (2020). Tackling environment-sensitive malware with automated reasoning. *EuroS&P*.
13. Yokoyama, A., et al. (2016). SandPrint: Fingerprinting malware sandboxes to provide intelligence for sandbox evasion. *RAID*.
14. Kirat, D., & Vigna, G. (2015). MalGene: Automatic extraction of malware analysis evasion signature. *CCS*, 769–780.
