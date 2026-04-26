# ARC — Wizard and CLI Guide

Linux end-to-end guide for running ARC against a Windows 11 VHDX image.

---

## Prerequisites

### System packages (Ubuntu 24.04+)

```bash
sudo apt install -y \
    libguestfs-tools libguestfs-dev python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit
```

### Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

```bash
export GEMINI_API_KEY=<your-key>         # optional — AI persona generation
export LIBGUESTFS_BACKEND=direct         # faster than default libvirt backend
```

Add to a `.env` file in the project root to avoid re-exporting each session.

---

## Workflow A — CLI (recommended for automation)

### Step 1: Build baseline VHDX

Only required once. You need a Windows 11 ISO.

```bash
./scripts/build_baseline_vhdx.sh \
    ~/isos/Win11_23H2_English_x64.iso \
    ./examples/unattend.xml \
    ~/vms/baseline.vhdx \
    80G
```

The script runs `virt-install` for an unattended install and shuts down after first boot.
`virsh undefine arc-baseline` removes the transient domain afterwards.

### Step 2: Copy baseline (never write to the original)

```bash
cp ~/vms/baseline.vhdx ~/vms/run-042.vhdx
```

### Step 3: Inject artifacts

```bash
# Preset persona
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --preset office_user \
    --timeline-days 360 \
    --random-seed 4242 \
    --audit-log ~/vms/run-042.audit.jsonl

# AI-generated persona (requires GEMINI_API_KEY)
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --ai-generate \
    --occupation "Software Engineer" \
    --interests gaming open-source \
    --timeline-days 360 \
    --random-seed 4242
```

ARC mounts the VHDX via libguestfs, runs all service phases, then unmounts cleanly.
Typical runtime on a 80 GB VHDX: 8–15 minutes.

### Step 4: Boot the modified VM

Edit `examples/libvirt-profile-template.xml` to point to your VHDX, then:

```bash
virsh define ./examples/libvirt-profile-template.xml
virsh start arc-run-042
```

The template spoofs SMBIOS strings, MAC OUI, and disk serial at the hypervisor level (ADR-011).

### Step 5: Verify realism

From the host, against the mounted VHDX:

```bash
python verify_realism.py ~/vms/run-042.vhdx
```

Inside the booted VM, run `pafish` and `Al-Khaser`. Target: ≤10 flagged indicators
(sterile baseline typically flags 50+).

---

## Workflow B — Interactive wizard

```bash
source .venv/bin/activate
python arc_wizard.py
```

The wizard prompts for occupation, location, interests, output path, and username/hostname
overrides. It generates a Gemini persona, then runs the same injection pipeline.

---

## Workflow C — Dry run (no VHDX, no writes)

Validates that all services load and would produce correct output:

```bash
python main.py --preset developer --dry-run -v
```

Useful for CI, smoke testing, and checking a new preset YAML before committing.

---

## CLI Flag Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--vhdx PATH` | — | VHDX to inject (must be a copy of baseline) |
| `--preset NAME` | — | `developer`, `office_user`, or `home_user` |
| `--profile PATH` | — | Path to a custom PersonaContext YAML |
| `--ai-generate` | off | Generate persona via Gemini |
| `--occupation STR` | — | Required with `--ai-generate` |
| `--interests ...` | — | Hobby/interest hints for AI persona |
| `--timeline-days N` | 360 | Days of history to inject (30–730) |
| `--random-seed N` | — | Fixed seed for byte-identical reproducibility (ADR-012) |
| `--override-username STR` | — | Force Windows username (must match existing VM user) |
| `--override-hostname STR` | — | Force computer name |
| `--skip-anti-fingerprint` | off | Skip VM-marker scrubbing (for baseline testing) |
| `--categories ...` | all | Limit to specific service categories |
| `--dry-run` | off | Simulate without writing files |
| `-v` | off | Verbose (DEBUG) logging |

Available categories: `expansion`, `filesystem`, `registry`, `browser`, `applications`,
`eventlog`, `anti_fingerprint`, `ntfs`, `evaluation`.

---

## Reproducibility

The same `--preset` + `--random-seed` combination produces byte-identical audit logs across
runs (ADR-012). This is required for analyst hand-off and cross-team reproducibility:

```bash
python main.py --preset developer --random-seed 4242 --dry-run > run1.log
python main.py --preset developer --random-seed 4242 --dry-run > run2.log
diff run1.log run2.log  # empty diff
```

---

## Common issues

| Symptom | Fix |
|---|---|
| `libguestfs: error: inspect_os: ...` | VHDX is locked or already mounted. `virsh destroy` the domain first. |
| `hivex: error opening hive` | The hive path is wrong for this Windows version. Check `--preset` username matches the VM's user. |
| `guestmount: FUSE: ...` | FUSE device unavailable. Ensure `fuse3` is installed and `/dev/fuse` exists. |
| `GEMINI_API_KEY not set` | Set the key or use `--preset` instead of `--ai-generate`. |
| Services fail with `ctx["..."]` error | Old preset YAML uses dict-style ctx. Update to `ctx.persona.<field>`. |
