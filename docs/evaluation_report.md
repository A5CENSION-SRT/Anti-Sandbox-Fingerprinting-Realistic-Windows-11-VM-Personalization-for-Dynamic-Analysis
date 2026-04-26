# ARC — Evaluation Report

Density baselines and acceptance gate thresholds as measured against Phase 4 outputs.

---

## Acceptance Gates

| Gate | Metric | Threshold | Measured (developer preset, 360d) |
|------|--------|-----------|-----------------------------------|
| A9  | TemporalCoherenceCheck: APP_LAUNCH → EVTX 4688 within ±2 s | 100% | pass |
| A10 | Registry typed-path operations | ≥5 000 | ~5 200 |
| A11 | Prefetch `.pf` files | ≥30 files, mean ≥15 KB | 42 files, ~18 KB mean |
| A12 | EVTX per log (Security.evtx) | ≥10 MB, ≥5 000 records | ~11 MB, ~14 000 records |
| A13 | Chrome History `urls` table | ≥5 000 URLs | ~5 400 |
| A13 | Chrome History `visits` table | ≥10 000 visits | ~10 800 |
| A14 | Openable documents (.docx/.xlsx/.pdf/.txt/.md/.py/.json) | ≥500 | ~540 |
| A15 | VHDX delta from baseline | ≥500 MB | ~580 MB (VM-dependent) |
| A17 | Research docs + ADR log committed | present | pass |

---

## Real Windows 11 Baselines (from `docs/research/windows_artifact_baselines.md`)

These are the reference densities from an actively used Windows 11 23H2 workstation used to
calibrate ARC targets.

| Subsystem | Real system range |
|---|---|
| Registry keys total | 50 000 – 80 000 |
| EVTX Security.evtx | 10–20 MB, 8 000–30 000 records |
| EVTX System.evtx | 5–15 MB |
| USN journal ($UsnJrnl:$J) | 0.5–3 GB |
| Prefetch files | 30–100 files × 10–80 KB each |
| Thumbcache total | 100–500 MB |
| Chrome History urls | 5 000–30 000 URLs |
| Chrome History visits | 10 000–80 000 visits |
| Documents (user home) | 500–5 000 files |

---

## Density per Persona Archetype

### developer (360-day, seed 4242)

| Domain | Count / Size |
|---|---|
| Prefetch .pf files | 42 |
| Registry UserAssist entries | ~185 |
| Registry TypedPaths / TypedURLs | ~220 |
| EVTX Security records (4688 only) | ~8 400 |
| EVTX System records | ~2 100 |
| Chrome History URLs | ~5 400 |
| Chrome Downloads | ~1 200 |
| Documents (all types) | ~540 |
| PowerShell history entries | ~1 800 |
| USN journal entries | ~45 000 |

### office_user (360-day, seed 4242)

| Domain | Count / Size |
|---|---|
| Prefetch .pf files | 38 |
| EVTX Security records | ~7 200 |
| Chrome History URLs | ~5 100 |
| Documents (docx/xlsx dominant) | ~520 |

### home_user (360-day, seed 4242)

| Domain | Count / Size |
|---|---|
| Prefetch .pf files | 31 |
| EVTX Security records | ~6 800 |
| Chrome History URLs | ~5 000 |
| Documents | ~505 |

---

## Coherence Score

`evaluation/consistency_checker.py::TemporalCoherenceCheck` validates cross-domain event
fan-out after each run. Over 50 automated runs (random seeds 1–50):

- Mean coherence score: **98.4%**
- Failures: 0 runs failed the ±2 s APP_LAUNCH → EVTX 4688 check
- Failures: 0 runs failed the ±5 s APP_LAUNCH → Prefetch last_run_times[0] check

---

## Sandbox Signal Testing

Reference tools: `pafish` (v0.6), `Al-Khaser` (latest main branch).

| Check category | Sterile baseline | ARC output |
|---|---|---|
| BIOS vendor strings | flagged | clean |
| Disk/SCSI identifier | flagged | clean |
| NIC OUI (VM vendor) | flagged | clean |
| VBox/VMware service keys | flagged | clean |
| Empty browser history | flagged | clean |
| Empty Recent Documents | flagged | clean |
| Temporal clustering | flagged | clean |
| CPUID / RDTSC timing | flagged | flagged (hypervisor-layer; out of scope) |

Total flags on sterile baseline: ~52. Total flags after ARC: ≤10 (timing/CPUID only, which
require hypervisor-level mitigation via `examples/libvirt-profile-template.xml`).

---

## Running the Evaluation Suite

```bash
# Post-injection, against a mounted image
python verify_realism.py ~/vms/run-042.vhdx

# Unit-level density assertions (no VHDX needed)
python -m pytest tests/ -x -q --ignore=tests/test_core/test_linux_mount.py
```

The pytest suite includes density gate tests A10–A14. Gates A6/A7/A8/A15/A20 require a
booted VM and cannot be automated via pytest.
