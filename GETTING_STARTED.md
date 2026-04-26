# ARC — Getting Started

**ARC** (*Artifact Reality Composer*) takes a Windows 11 VHDX image and injects
360 days of coherent user activity — registry, filesystem, NTFS journal, event logs,
browser history — so the VM no longer reads as a sterile sandbox to malware.

> This file replaces `START_HERE.md`, `WIZARD_QUICKSTART.md`, `SETUP.md`, and
> `ENV_SETUP.md`.  Those files are archived under `docs/archive/`.

---

## 1. Prerequisites

### 1.1 Linux host (Ubuntu 24.04+)

```bash
sudo apt install -y \
    libguestfs-tools libguestfs-dev python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit
```

### 1.2 Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.3 Environment variables

```bash
export GEMINI_API_KEY=<your-key>        # optional — AI persona generation
export LIBGUESTFS_BACKEND=direct        # faster than default libvirt backend
```

Or add them to a `.env` file in the project root.

---

## 2. One-time: build baseline VHDX

You need a Windows 11 ISO and a post-OOBE VHDX as the starting point.

```bash
./scripts/build_baseline_vhdx.sh \
    ~/isos/Win11_23H2_English_x64.iso \
    ./examples/unattend.xml \
    ~/vms/baseline.vhdx \
    80G
```

The script uses `virt-install` for an unattended install that shuts down after
first boot.  `virsh undefine arc-baseline` cleans up the transient domain.

---

## 3. Per-analyst run

```bash
# 1. Copy baseline (never write to it directly)
cp ~/vms/baseline.vhdx ~/vms/run-042.vhdx

# 2. Inject artifacts with a preset persona
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --preset office_user \
    --timeline-days 360 \
    --random-seed 4242 \
    --audit-log ~/vms/run-042.audit.jsonl

# 3. Inject artifacts with AI-generated persona (requires GEMINI_API_KEY)
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --ai-generate \
    --occupation "Software Engineer" \
    --interests gaming open-source \
    --timeline-days 360 \
    --random-seed 4242
```

### CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--vhdx PATH` | — | Path to VHDX to inject (must be a copy) |
| `--preset NAME` | — | `developer`, `office_user`, or `home_user` |
| `--profile PATH` | — | Path to a custom PersonaContext YAML |
| `--ai-generate` | off | Generate persona via Gemini |
| `--occupation STR` | — | Required with `--ai-generate` |
| `--timeline-days N` | 360 | Days of history to inject (30–730) |
| `--random-seed N` | — | Fixed seed for reproducible runs |
| `--override-username STR` | — | Force a Windows username (must match existing VM user) |
| `--override-hostname STR` | — | Force computer name |
| `--skip-anti-fingerprint` | off | Skip VM-marker scrubbing (baseline testing) |
| `--dry-run` | off | Simulate without writing files |
| `--categories ...` | all | Limit to specific service categories |
| `-v` | off | Verbose (DEBUG) logging |

---

## 4. Boot the modified VM

Use the libvirt domain template so SMBIOS, MAC address, and disk serial are
also spoofed at the hypervisor level:

```bash
# Edit examples/libvirt-profile-template.xml with your run VHDX path, then:
virsh define ./examples/libvirt-profile-template.xml
virsh start arc-run-042
```

See `examples/libvirt-profile-template.xml` for the full SMBIOS/MAC/CPU spoofing
configuration (ADR-011).

---

## 5. Interactive wizard

For a guided menu-driven flow (AI generation + optional VHDX injection):

```bash
python arc_wizard.py
```

---

## 6. Verifying realism

After injection, run the ARC verifier against the mounted image:

```bash
python verify_realism.py ~/vms/run-042.vhdx
```

Inside the booted VM, `pafish` and `Al-Khaser` should flag ≤ 10 indicators
(baseline unmodified ~50+).

---

## 7. Dry-run (no VHDX needed)

```bash
python main.py --preset developer --dry-run -v
```

This validates all services load correctly and logs what would be written,
without touching any file.

---

## 8. Project structure

```
main.py                    CLI entry point
arc_wizard.py              Interactive menu wizard
verify_realism.py          Post-injection realism checker
config.yaml                Default configuration

core/
  persona_context.py       Canonical 25-field PersonaContext schema
  persona_loader.py        YAML → PersonaContext loader
  service_context.py       Typed ServiceContext passed to every service
  event_scheduler.py       Cross-domain deterministic event stream
  linux_mount.py           libguestfs + hivex + ntfs-3g backend
  orchestrator.py          Service execution pipeline

services/
  registry/                Hive writers (5 domain services + TypingHistory)
  filesystem/              Document, Prefetch, CrossWriter, ...
  browser/                 Chrome/Edge History, Bookmarks, Downloads, Cookies
  eventlog/                EVTX writer + Security/System/Application/Update logs
  anti_fingerprint/        VM-marker scrubbing (VmScrubber, HardwareNormalizer, ...)
  ntfs/                    $UsnJrnl, $MFT SI timestamp patching
  expansion/               Bulk artifact expansion pipeline
  applications/            Dev environment, Office, Email, Comms artifacts
  ai/                      Gemini-based PersonaContext generation

profiles/presets/           developer.yaml, office_user.yaml, home_user.yaml
docs/                       MASTER_PLAN.md, research/, design/decisions.md
examples/                   unattend.xml, libvirt-profile-template.xml
scripts/                    build_baseline_vhdx.sh
```

See `docs/MASTER_PLAN.md` for the full architectural specification.
