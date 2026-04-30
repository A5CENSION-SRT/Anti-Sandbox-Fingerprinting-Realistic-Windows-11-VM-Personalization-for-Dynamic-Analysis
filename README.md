# ARC — Artifact Reality Composer

> **Realistic Windows 11 VM Personalization for Dynamic Analysis / Anti-Sandbox Fingerprinting**

ARC transforms a clean Windows 11 VM image into one that looks like a machine used by a real person for months. It generates thousands of coherent, internally-consistent forensic artifacts — browser history, registry entries, documents, event logs, prefetch files, and more — all tied to a configurable user persona.

---

## Table of Contents

1. [What ARC Does](#what-arc-does)
2. [Quick Start (Any OS)](#quick-start-any-os)
3. [Linux Host Setup (Full Mode)](#linux-host-setup-full-mode)
4. [Usage Modes](#usage-modes)
5. [CLI Reference](#cli-reference)
6. [Persona Profiles](#persona-profiles)
7. [Configuration (config.yaml)](#configuration-configyaml)
8. [Service Pipeline](#service-pipeline)
9. [Safety System](#safety-system)
10. [AI-Powered Generation](#ai-powered-generation)
11. [Realism Verification](#realism-verification)
12. [Project Structure](#project-structure)
13. [Troubleshooting](#troubleshooting)

---

## What ARC Does

Given a persona profile (e.g., "home user", "developer", "office worker"), ARC populates a Windows filesystem with:

| Artifact Category | Examples |
|---|---|
| **Filesystem** | 3,000–4,000 documents, media stubs, prefetch files, thumbnails, recycle bin, shell LNK shortcuts |
| **Registry** | UserAssist run counts, TypedURLs, MRU lists, network profiles, installed programs, computer name/SID |
| **Browser** | Chrome History SQLite (5,000+ URLs, 10,000+ visits), bookmarks, cookies, downloads with Zone.Identifier |
| **Event Logs** | Security.evtx (4624/4634/4688), System.evtx (6005/6006), Application.evtx — realistic timestamps |
| **Applications** | VS Code settings, .gitconfig, SSH keys, Outlook PST stubs, Teams/Slack/Discord dirs |
| **Anti-Fingerprint** | Scrubs VM strings (VirtualBox/VMware/QEMU) from registry; normalizes BIOS/NIC vendor strings |
| **NTFS** (Linux only) | MFT `$STANDARD_INFORMATION` timestamps via xattr, `$UsnJrnl:$J` change journal records |

All artifacts share a **coherent timeline** — timestamps, file dates, browser visits, event log entries, and prefetch records all align to a single simulated history anchored at install time.

---

## Quick Start (Any OS)

Works on **Windows, Linux, macOS** — no VM mounting required in this mode.

```bash
# 1. Clone
git clone <repo-url>
cd Anti-Sandbox-Fingerprinting-Realistic-Windows-11-VM-Personalization-for-Dynamic-Analysis

# 2. Create virtualenv and install Python deps
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Dry-run test (no files written)
python main.py --preset developer --dry-run -v

# 5. Full generation into ./output
python main.py --preset home_user --output ./output
```

**Expected output:**
```
[PASS] UserDirectoryService      |    55ms
[PASS] DocumentGenerator         | 14201ms
[PASS] BrowserHistory            |  3756ms
...
[PASS] SystemContentPopulator    |   463ms
============================================================
41/41 services succeeded  |  ~30s total
```

Output directory will contain `3,000–4,000 files` across a realistic Windows directory tree with `0 empty directories`.

---

## Linux Host Setup (Full Mode)

On Linux you get **full functionality**: real registry hive writes, MFT timestamp patching, and USN journal population. These require system packages not available on Windows/macOS.

### Step 1 — System packages

```bash
sudo apt update
sudo apt install -y \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 attr \
    libguestfs-tools libguestfs-dev python3-guestfs guestmount \
    qemu-utils sleuthkit
```

Or use the provided installer:

```bash
chmod +x install_deps.sh
./install_deps.sh
```

### Step 2 — Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Optional: Gemini API key (for AI persona generation)

```bash
cp .env.example .env
# Edit .env and set:
GEMINI_API_KEY=your_key_here
```

### Step 4 — Verify installation

```bash
python main.py --preset developer --dry-run -v
```

You should see **41/41** services registered. On Linux with hivex installed, registry services will actually write to hive binaries.

---

## Usage Modes

ARC supports three distinct modes, in order of safety:

### Mode 1 — Output Directory (Safest, Works Everywhere)

No mounting. Generates the full Windows directory tree into any folder.

```bash
python main.py --output ./my_output --preset developer
```

Use this for:
- Development and testing on any OS
- Generating artifacts to copy into a VM manually
- CI pipelines

---

### Mode 2 — VM Disk Image (Recommended for Linux)

ARC mounts a `.qcow2`, `.img`, `.vhd`, or `.raw` disk image via loopback, writes directly into it, then detaches cleanly.

```bash
# Create a blank image first (if you don't have one)
qemu-img create -f qcow2 win11_vm.qcow2 60G

# Run ARC against it
python main.py --image ./win11_vm.qcow2 --preset office_user

# With a specific partition index inside the image
python main.py --image ./win11.img --image-partition 3 --preset developer
```

ARC handles loopback setup (`kpartx`/`losetup`) and teardown automatically. **This is the recommended mode for Linux.**

---

### Mode 3 — Physical Dual-Boot Partition (Advanced, Linux Only)

For writing directly into a real Windows partition on a dual-boot machine. **Multiple safety layers protect against corruption.**

```bash
python main.py \
    --partition /dev/nvme0n1p3 \
    --force-partition \
    --preset home_user
```

**What happens:**
1. SafetyGuard classifies the device (physical vs. loopback)
2. NTFS dirty-bit probe runs (`ntfsfix --no-action`) — **aborts if dirty/hibernated**
3. You must interactively type `YES` to confirm
4. ARC mounts read-write only after all checks pass
5. Mount is cleaned up in the `finally` block even on crash

> ⚠️ **Windows must be fully shut down** — not hibernated, not Fast Startup.  
> Go to: Start → Shut down (hold Shift if using Fast Startup).

---

## CLI Reference

```
python main.py [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--output PATH` | `./output` | Output directory (Mode 1) |
| `--image PATH` | — | VM disk image path (Mode 2) |
| `--image-partition N` | auto | Partition index inside image |
| `--partition DEVICE` | — | Physical block device (Mode 3) |
| `--force-partition` | off | Required flag to enable Mode 3 |
| `--preset NAME` | `home_user` | Built-in persona: `home_user`, `office_user`, `developer` |
| `--profile PATH` | — | Path to a custom persona YAML file |
| `--timeline-days N` | `360` | Days of history to simulate |
| `--random-seed N` | — | Fixed RNG seed for reproducible output |
| `--override-username NAME` | — | Force a specific Windows username |
| `--override-hostname NAME` | — | Force a specific computer name |
| `--categories LIST` | all | Run only specific service categories |
| `--skip-anti-fingerprint` | off | Skip VM string scrubbing |
| `--dry-run` | off | Simulate without writing files |
| `-v / --verbose` | off | Enable DEBUG logging |

### Examples

```bash
# Reproducible developer image (same seed = identical output)
python main.py --image win11.qcow2 --preset developer --random-seed 42

# Custom username to match existing VM account
python main.py --output ./out --preset home_user --override-username john.doe

# Only generate browser + filesystem artifacts (skip registry, eventlog)
python main.py --output ./out --preset office_user --categories filesystem browser

# Full verbose dry-run to see what would be written
python main.py --preset developer --dry-run -v 2>&1 | head -100
```

---

## Persona Profiles

Profiles define who the simulated user is. They control what apps are installed, what sites are browsed, what documents are created, work hours, and more.

### Built-in presets

| Preset | Persona | Apps | Files generated |
|---|---|---|---|
| `home_user` | Alex Johnson — Freelance Graphic Designer | Photoshop, Spotify, Discord, Steam, Firefox | ~3,500 |
| `office_user` | Corporate worker | Outlook, Excel, Word, Teams, Edge | ~3,750 |
| `developer` | Software developer | VS Code, Git, Docker, Terminal, Chrome | ~4,000 |

### Preset files location

```
profiles/presets/
  home_user.yaml
  office_user.yaml
  developer.yaml
```

### Creating a custom profile

Copy any preset and edit:

```yaml
# profiles/presets/my_user.yaml
full_name: "Sarah Chen"
username: "schen"
email: "sarah.chen@company.com"
organization: "Acme Corp"
occupation: "Data Analyst"

tech_proficiency: intermediate
interests:
  hobbies: [reading, yoga, cooking]
  professional_topics: [data visualization, SQL, Python]

installed_apps:
  - chrome.exe
  - excel.exe
  - python.exe
  - tableau.exe

browsing_categories:
  - data_science
  - news
  - shopping

work_hours_start: 9
work_hours_end: 17
active_days: [1, 2, 3, 4, 5]
timeline_days: 365
profile_archetype: office_user
hostname_hints:
  - SCHEN-LAPTOP
```

Run with:

```bash
python main.py --profile profiles/presets/my_user.yaml --output ./out
```

---

## Configuration (config.yaml)

`config.yaml` controls global settings. Key sections:

```yaml
# Output path when no --image or --partition is given
mount_path: "./output"

# Default profile
profiles_dir: "profiles/presets"
profile_name: "home_user"

# Simulate N days of user history
timeline_days: 360

# Artifact density (files per day)
artifact_scale:
  documents:
    per_day: 12.5
    jitter: 0.3
  browser_history:
    per_day: 83.0
    jitter: 0.5

# AI generation (Gemini)
ai:
  enabled: true
  provider: "gemini"
  gemini:
    model: "gemini-3.1-flash-lite-preview"
    temperature: 0.7

# Safety — never disable
require_explicit_partition: true
```

---

## Service Pipeline

ARC runs **41 services** in 10 ordered phases:

```
INFRASTRUCTURE → EXPANSION → FILESYSTEM → REGISTRY → BROWSER
    → APPLICATIONS → EVENTLOG → ANTI_FINGERPRINT → NTFS → EVALUATION
```

| Phase | Key Services | What they produce |
|---|---|---|
| INFRASTRUCTURE | UserDirectoryService | `C:\Users\<name>\` shell folder skeleton |
| EXPANSION | ExpansionOrchestrator | AI-seeded document/browse/media descriptor bundles |
| FILESYSTEM | DocumentGenerator, PrefetchService, MediaStubService | 500+ files, 30+ `.pf` prefetch records |
| REGISTRY | HiveWriter, SystemIdentity, UserAssist, MruRecentDocs | Binary hive writes (Linux) or graceful skip (Windows) |
| BROWSER | BrowserHistory, BookmarksService, CookiesCacheService | Chrome SQLite DBs with 5,000+ URLs |
| APPLICATIONS | DevEnvironment, EmailClient, CommsApps | `.gitconfig`, SSH keys, PST stubs, Teams dirs |
| EVENTLOG | SecurityLog, SystemLog, ApplicationLog | EVTX binaries with realistic 4624/4688 events |
| ANTI_FINGERPRINT | VmScrubber, HardwareNormalizer, MacHygiene | Removes VBox/VMware strings, normalizes vendors |
| NTFS | MftTimestampPatcher, UsnJournalWriter | SI timestamps via xattr, USN V2 records (**Linux only**) |
| EVALUATION | SystemContentPopulator | Fills any remaining empty directories |

### Platform capability matrix

| Feature | Windows (dev) | Linux (no hivex) | Linux (full) |
|---|---|---|---|
| Filesystem artifacts | ✅ | ✅ | ✅ |
| Browser SQLite | ✅ | ✅ | ✅ |
| Event logs (EVTX) | ✅ | ✅ | ✅ |
| Registry hive writes | ⚠️ skipped | ⚠️ skipped | ✅ real writes |
| MFT timestamp patching | ⚠️ skipped | ⚠️ skipped | ✅ via xattr |
| USN journal | ⚠️ skipped | ⚠️ skipped | ✅ V2 records |
| VM image mounting | ❌ | ✅ | ✅ |

---

## Safety System

The safety system prevents accidental corruption of live Windows partitions.

### Three-layer protection for physical partitions

**Layer 1 — Classification**
`core/safety_guard.py` inspects every device path. Physical block devices (`/dev/nvme*`, `/dev/sd*`) are flagged; loopback devices and image files are always allowed.

**Layer 2 — Pre-flight NTFS probe**
Before any mount, ARC runs `ntfsfix --no-action` (read-only) to detect:
- **Dirty bit set** → Windows crashed or was not shut down cleanly → **ABORT**
- **Hibernated** (`hiberfil.sys` active) → Fast Startup / hibernate → **ABORT**

**Layer 3 — Interactive confirmation**
Even after passing the probe, `--force-partition` alone is not enough. ARC prints a detailed warning and waits for you to type `YES`.

### What ARC never does

| Action | Why |
|---|---|
| `ntfsfix -d` | Would clear the dirty bit, masking corruption |
| `remove_hiberfile` (ntfs-3g option) | Destroys `hiberfil.sys` → BCD error `0xc0000098` |
| Auto-discover physical drives | Could silently mount wrong partition |

### Recovery resources

If your Windows boot is broken:
- `docs/recovery_guide.md` — step-by-step BCD repair
- `scripts/emergency_recovery.sh` — automated repair script

---

## AI-Powered Generation

ARC can use Gemini to generate richer, more varied persona content.

### Enable AI generation

```bash
# Set your API key
export GEMINI_API_KEY=your_key_here

# Run with AI-generated persona
python main.py --output ./out --preset developer
# (AI is used automatically when key is present and ai.enabled=true in config.yaml)
```

AI generates:
- Realistic document names and content topics
- Varied browsing patterns for the persona's interests
- Coherent project names, colleague names, email subjects

Responses are **cached in `.cache/gemini/`** (TTL: 1 week) to avoid repeated API calls.

To disable AI and use static profiles only:
```yaml
# config.yaml
ai:
  enabled: false
```

---

## Realism Verification

After a full run, verify the output meets sandbox-evasion quality thresholds:

```bash
python verify_realism.py --output ./output
```

Key checks performed:

| Check | Threshold |
|---|---|
| Total files | ≥ 500 |
| Documents (DOCX/PDF/TXT) | ≥ 200 |
| Prefetch files | ≥ 30, mean size ≥ 15 KB |
| Browser history URLs | ≥ 5,000 |
| Browser history visits | ≥ 10,000 |
| Security.evtx size | ≥ 10 MB |
| Empty directories | 0 |
| Timestamp coherence | APP_LAUNCH ↔ EVTX 4688 within ±2s |

---

## Project Structure

```
.
├── main.py                   # CLI entry point
├── arc_wizard.py             # Interactive setup wizard
├── config.yaml               # Global configuration
├── requirements.txt          # Python dependencies
├── install_deps.sh           # Linux system package installer
├── verify_realism.py         # Post-run quality checker
│
├── core/
│   ├── orchestrator.py       # Service phase ordering & execution
│   ├── safety_guard.py       # Pre-flight NTFS safety checks
│   ├── linux_mount.py        # Image / partition / FUSE mount backend
│   ├── partition_discovery.py# Loopback-only device discovery
│   ├── identity_generator.py # Deterministic name/SID/GUID generation
│   ├── event_scheduler.py    # Coherent timeline event generation
│   ├── persona_loader.py     # YAML profile loader
│   └── audit_logger.py       # Structured audit trail
│
├── services/
│   ├── filesystem/           # Document, prefetch, media, LNK generators
│   ├── registry/             # HiveWriter + typed operation builders
│   ├── browser/              # Chrome History/Bookmarks/Cookies SQLite
│   ├── eventlog/             # EVTX binary writers
│   ├── applications/         # Dev env, Office, Email, Comms stubs
│   ├── anti_fingerprint/     # VM string scrubber, hardware normalizer
│   └── ntfs/                 # MFT patcher, USN journal (Linux only)
│
├── profiles/
│   └── presets/
│       ├── home_user.yaml
│       ├── office_user.yaml
│       └── developer.yaml
│
├── data/                     # hardware_models.json, wordlists, URL seeds
├── docs/                     # recovery_guide.md, architecture docs
├── scripts/                  # emergency_recovery.sh
└── tests/                    # pytest test suite + safety_audit.py
```

---

## Troubleshooting

### `hivex not available` warnings

```
WARNING HiveWriter: hivex not available (platform: win32). Registry writes will be silently skipped.
```
**Normal on Windows.** Registry services degrade gracefully. On Linux: `sudo apt install libhivex-bin python3-hivex`.

### `os.setxattr not available` (MFT patcher skipped)

Normal on Windows/macOS. MFT timestamp patching requires `ntfs-3g` FUSE on Linux. All other services still run.

### `UsnJournalWriter: ADS paths require Linux`

Normal on Windows. USN journal requires ntfs-3g with `streams_interface=windows`. All other services still run.

### Mount fails: `MOUNT ABORTED — PARTITION IS DIRTY / HIBERNATED`

Windows was not fully shut down. On Windows: hold Shift + click Shut Down (bypasses Fast Startup). Do not use Restart. Then retry.

### Permission denied on `/dev/nvme*`

```bash
sudo python main.py --partition /dev/nvme0n1p3 --force-partition --preset home_user
```

Or add your user to the `disk` group: `sudo usermod -aG disk $USER` (re-login required).

### `guestmount` not found

```bash
sudo apt install libguestfs-tools guestmount
# If permission errors with guestfs:
sudo chmod +r /boot/vmlinuz-$(uname -r)
```

### Gemini API errors

- Check `GEMINI_API_KEY` is set: `echo $GEMINI_API_KEY`
- AI is optional — disable with `ai.enabled: false` in `config.yaml`
- Cached responses in `.cache/gemini/` are reused for 1 week

### Running tests

```bash
pytest tests/ -v

# Safety audit (checks for dangerous subprocess calls)
python tests/safety_audit.py
```
