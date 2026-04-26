# ARC Setup Guide

ARC runs on **Linux** (Ubuntu 24.04+ recommended). There is no Windows host support — the
mount backend is libguestfs + hivex + ntfs-3g (ADR-002).

---

## 1. System Dependencies

Install the required system packages:

```bash
sudo apt install -y \
    libguestfs-tools python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit
```

`libguestfs-tools` provides `guestmount` and `guestunmount`. `sleuthkit` provides `fls` and
`istat` for post-run acceptance validation (optional but recommended).

---

## 2. Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` does not include `pywin32` — that dependency is removed. `guestfs` and
`hivex` are provided by the system packages installed above, not by pip.

---

## 3. Environment Variables

```bash
export LIBGUESTFS_BACKEND=direct        # faster than the default libvirt backend
export GEMINI_API_KEY=your-key-here     # required only for --ai-generate runs
```

Add these to `~/.bashrc` or `~/.zshrc` for persistence. A `.env` file in the project root
is also supported (loaded automatically by `python-dotenv`):

```ini
LIBGUESTFS_BACKEND=direct
GEMINI_API_KEY=your-key-here
```

---

## 4. Baseline VHDX

ARC requires a **post-OOBE** Windows 11 VHDX — a VHDX that has been booted at least once so
that Windows has initialised `$UsnJrnl`, event log channels, registry hive logs, and the
Prefetch directory (ADR-003).

Build a baseline automatically with the provided script:

```bash
./scripts/build_baseline_vhdx.sh \
    ~/isos/Win11_23H2.iso \
    ./examples/unattend.xml \
    ~/vms/baseline_23h2.vhdx \
    80G
```

This uses `virt-install` to run an unattended Windows install. The `unattend.xml` answer file
skips OOBE and issues `shutdown /s /t 0` after a 3-minute idle (giving Windows time to
initialise journals and hive logs). Archive the result; copy it per analyst run:

```bash
cp ~/vms/baseline_23h2.vhdx ~/vms/run-042.vhdx
```

---

## 5. Run ARC

### Preset run (no API key needed)

```bash
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --preset home_user \
    --random-seed 4242 \
    --audit-log ~/vms/run-042.audit.jsonl
```

### AI-generated persona (requires `GEMINI_API_KEY`)

```bash
python main.py \
    --vhdx ~/vms/run-042.vhdx \
    --ai-generate \
    --occupation "Software Engineer" \
    --interests "gaming,open-source" \
    --timeline-days 360 \
    --random-seed 4242 \
    --audit-log ~/vms/run-042.audit.jsonl
```

### Dry-run (no writes to VHDX)

```bash
python main.py --vhdx ~/vms/run-042.vhdx --preset developer --dry-run
```

---

## 6. Boot for Analysis

Define the VM using the libvirt template (SMBIOS / MAC / disk-serial spoofing — ADR-011):

```bash
# Edit examples/libvirt-profile-template.xml to set the VHDX path, then:
virsh define examples/libvirt-profile-template.xml
virsh start arc-run-042
```

The template sets Dell OptiPlex 7090 SMBIOS strings, an Intel OUI MAC address, and
`<cpu mode='host-passthrough'>` to hide the hypervisor CPUID leaf.

---

## 7. Verify Realism

```bash
python verify_realism.py --vhdx ~/vms/run-042.vhdx --audit ~/vms/run-042.audit.jsonl
```

Target acceptance gates: registry ≥ 5 000 new keys (A10), Prefetch ≥ 30 files mean ≥ 15 KB
(A11), EVTX per log ≥ 10 MB (A12), Chrome History ≥ 5 000 urls (A13), documents ≥ 500 (A14),
VHDX delta ≥ 500 MB (A15), TemporalCoherenceCheck passes (A9).

---

## 8. Smoke Test (no VHDX required)

```bash
python -m pytest tests/ -x -q
```

Import sanity check:

```bash
python -c "from core.linux_mount import LinuxMountBackend; print('OK')"
python -c "from core.persona_context import PersonaContext; print('OK')"
python -c "from core.event_scheduler import EventScheduler; print('OK')"
```

---

## 9. Troubleshooting

### `python3-guestfs not found`

Ensure the system package is installed and the active virtualenv can see it:

```bash
dpkg -l python3-guestfs
# If inside a venv, guestfs is a system-level C extension.
# Either install into the system Python or use --system-site-packages when creating the venv:
python3 -m venv --system-site-packages .venv
```

### `guestmount: failed to launch appliance`

```bash
export LIBGUESTFS_BACKEND=direct
# If still failing:
sudo chmod 644 /boot/vmlinuz-$(uname -r)   # guestfs needs to read the kernel
libguestfs-test-tool                        # diagnostic
```

### `hivex: permission denied`

The hive files inside the VHDX must not be marked read-only. Verify the VHDX is not
mounted read-only by guestfs. Check `LinuxMountBackend.mount()` — it passes `readonly=False`
to `add_drive_opts`.

### CI grep-gates

```bash
grep -rn 'datetime\.now\|Random(42)' services/        # must return 0 (A1)
grep -rn 'context\["' services/                        # must return 0 (A2)
grep -rn 'import win32\|from win32' .                  # must return 0 (A3)
grep -rn 'powershell\|Mount-DiskImage' core/ services/ # must return 0 (A4)
```
