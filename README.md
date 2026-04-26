# ARC - Artifact Reality Composer

**Anti-Sandbox Fingerprinting through Realistic Windows 11 VM Personalization**

ARC is a Python application that personalizes freshly installed Windows 11 VHDX images to resist VM-detection heuristics used by malware and sandbox-aware software. It generates realistic, coherent artifacts across registry, filesystem, browser data, event logs, and NTFS metadata to make analysis VMs appear as genuine user systems.

## 🎯 Overview

Modern malware employs sophisticated VM-detection techniques to evade dynamic analysis. ARC addresses this by:

- **Generating realistic user artifacts** across multiple Windows subsystems
- **Maintaining internal consistency** in timelines, paths, and user behavior
- **Operating offline** on mounted VHDX images without booting the VM
- **Supporting multiple personas** (office user, developer, home user, etc.)
- **Providing full auditability** of all modifications

## ✨ Key Features

### Comprehensive Artifact Generation

- **Registry**: User profiles, MRU lists, UserAssist, application settings
- **Filesystem**: Documents, downloads, recent items, recycle bin, thumbnails
- **Browser**: History, bookmarks, cookies, cache, downloads (Chrome/Edge)
- **Event Logs**: System, Application, Security events with realistic patterns
- **NTFS Metadata**: $MFT timestamps, $UsnJrnl records, $LogFile stubs
- **Prefetch**: Application execution traces
- **Windows Update**: KB update artifacts and registry entries

### AI-Powered Content Generation

- **LLM Integration**: Uses Gemini API for persona-driven content generation
- **Deterministic Output**: Seeded RNG ensures reproducible results
- **Contextual Coherence**: All artifacts align with chosen persona profile

### Linux Host Support

- **libguestfs**: Offline VHDX mounting and filesystem operations
- **hivex**: Direct registry hive manipulation
- **ntfs-3g FUSE**: NTFS metadata patching ($STANDARD_INFORMATION, $UsnJrnl)
- **Two-Phase Strategy**: Separate libguestfs and FUSE mount phases

## 📋 Requirements

### System Requirements

- **OS**: Ubuntu 24.04 LTS or later
- **CPU**: 2+ cores recommended
- **RAM**: 4 GB minimum, 8 GB recommended
- **Disk**: 100 GB+ free space for VHDX images
- **Virtualization**: KVM support (for baseline VHDX creation)

### System Dependencies

```bash
# Core dependencies
sudo apt update
sudo apt install -y \
    libguestfs-tools \
    python3-guestfs \
    libhivex-bin \
    python3-hivex \
    ntfs-3g \
    fuse3 \
    guestmount

# For baseline VHDX creation (optional)
sudo apt install -y \
    qemu-kvm \
    libvirt-daemon-system \
    virt-manager \
    qemu-utils \
    virt-install
```

### Python Dependencies

```bash
# Install Python 3.12+
sudo apt install -y python3 python3-pip python3-venv

# Install ARC dependencies
pip3 install -r requirements.txt
```

## 🚀 Quick Start

### 1. Create Baseline VHDX

First, create a post-OOBE Windows 11 baseline image:

```bash
# Download Windows 11 ISO from Microsoft
# https://www.microsoft.com/software-download/windows11

# Create baseline VHDX (requires sudo for KVM)
sudo bash scripts/build_baseline_vhdx.sh \
    Win11_23H2_x64.iso \
    examples/unattend.xml \
    baseline.vhdx \
    64
```

This creates a 64 GB VHDX with:
- Windows 11 Pro installed
- OOBE completed
- $UsnJrnl, Prefetch, and event logs initialized
- Windows Update and Defender disabled

### 2. Validate Baseline

Verify the baseline has all required structures:

```bash
bash scripts/validate_baseline.sh baseline.vhdx
```

Expected output:
```
✓ VHDX file exists and is readable
✓ NTFS filesystem detected
✓ $UsnJrnl:$Max exists
✓ Prefetch files found: 12
✓ Event logs valid
✓ Registry hives valid
```

### 3. Run ARC Personalization

Personalize the baseline with a user profile:

```bash
python3 main.py \
    --vhdx baseline.vhdx \
    --profile office_user \
    --timeline 360 \
    --output personalized.vhdx
```

Parameters:
- `--vhdx`: Path to baseline VHDX
- `--profile`: User persona (office_user, developer, home_user, gamer)
- `--timeline`: Days of simulated activity (default: 360)
- `--output`: Output VHDX path (optional, modifies in-place if omitted)

### 4. Boot and Test

Boot the personalized VHDX in QEMU/KVM:

```bash
qemu-system-x86_64 \
    -enable-kvm \
    -m 4096 \
    -smp 2 \
    -drive file=personalized.vhdx,format=vhdx \
    -bios /usr/share/ovmf/OVMF.fd \
    -net nic -net user
```

## 📖 Usage

### Basic Usage

```bash
# Personalize with default settings
python3 main.py --vhdx baseline.vhdx --profile office_user

# Specify timeline and output
python3 main.py \
    --vhdx baseline.vhdx \
    --profile developer \
    --timeline 180 \
    --output dev_vm.vhdx

# Use custom profile
python3 main.py \
    --vhdx baseline.vhdx \
    --profile-file custom_profile.yaml
```

### Available Profiles

| Profile | Description | Key Characteristics |
|---------|-------------|---------------------|
| `office_user` | Corporate office worker | Office 365, Teams, Outlook, business documents |
| `developer` | Software developer | VS Code, Git, Python, Node.js, Stack Overflow |
| `home_user` | Casual home user | Social media, streaming, shopping, email |
| `gamer` | PC gamer | Steam, Discord, game launchers, gaming sites |

### Custom Profiles

Create custom profiles in YAML format:

```yaml
# custom_profile.yaml
persona:
  name: "John Doe"
  age: 35
  occupation: "Data Analyst"
  interests:
    - data science
    - machine learning
    - visualization

browsing:
  categories:
    - technology
    - data science
    - news
  daily_visits: 50

applications:
  - name: "Python"
    frequency: "daily"
  - name: "Jupyter Notebook"
    frequency: "daily"
  - name: "Excel"
    frequency: "weekly"
```

## 🏗️ Architecture

### Two-Phase Mount Strategy

ARC uses a two-phase approach to handle different NTFS operations:

**Phase A: libguestfs (Bulk Operations)**
- Registry hive writes (via hivex)
- File creation and modification
- Event log generation
- Prefetch file creation

**Phase B: FUSE Mount (NTFS Metadata)**
- $STANDARD_INFORMATION timestamp patching
- $UsnJrnl record appending
- $LogFile stub generation

This separation is necessary because libguestfs and FUSE mounts cannot overlap.

### Component Overview

```
ARC/
├── core/
│   ├── linux_mount.py          # LinuxMountBackend (libguestfs + FUSE)
│   ├── mount_manager.py        # Path resolution and delegation
│   ├── orchestrator.py         # Two-phase execution coordinator
│   ├── event_scheduler.py      # Timeline event generation
│   └── persona_loader.py       # Profile loading and validation
├── services/
│   ├── registry/               # Registry hive writers
│   ├── filesystem/             # File and directory creation
│   ├── browser/                # Browser artifact generation
│   ├── eventlog/               # Event log synthesis
│   └── ntfs/                   # NTFS metadata services
│       ├── mft_timestamp_patcher.py
│       ├── usn_journal_writer.py
│       └── logfile_writer.py
├── scripts/
│   ├── build_baseline_vhdx.sh  # Automated baseline creation
│   └── validate_baseline.sh    # Baseline validation
└── examples/
    └── unattend.xml            # Windows 11 unattended install
```

## 🧪 Testing

### Run Unit Tests

```bash
# Install test dependencies
pip3 install pytest pytest-mock pytest-cov

# Run all tests
bash tests/run_tests.sh

# Run with coverage
bash tests/run_tests.sh --coverage

# Run specific test file
pytest tests/test_ntfs_services.py -v
```

### Test Coverage

Current test coverage:
- NTFS Services: 85%+
- LinuxMountBackend: 80%+
- Overall: 80%+

### Integration Testing

```bash
# Requires real VHDX and dependencies
pytest tests/test_integration.py --requires-vhdx
```

## 📊 Validation

### Forensic Tool Validation

Validate artifacts with industry-standard forensic tools:

```bash
# MFT Timeline
MFTECmd.exe -f personalized.vhdx --csv output

# Prefetch Analysis
PECmd.exe -d personalized.vhdx/Windows/Prefetch --csv output

# Registry Analysis
RECmd.exe -d personalized.vhdx/Windows/System32/config --csv output

# Event Log Analysis
EvtxECmd.exe -d personalized.vhdx/Windows/System32/winevt/Logs --csv output
```

### VM Detection Testing

Test against common VM detection tools:

```bash
# Boot personalized VHDX and run:
- pafish.exe
- Al-Khaser.exe
- InviZzzible

# Target: <10% detection rate
```

## 🔧 Troubleshooting

### libguestfs Issues

**Problem**: `libguestfs: error: could not find kernel`

**Solution**:
```bash
sudo update-guestfs-appliance
```

**Problem**: `Permission denied` when mounting VHDX

**Solution**:
```bash
# Add user to kvm group
sudo usermod -a -G kvm $USER
# Re-login for changes to take effect
```

### FUSE Mount Issues

**Problem**: `fusermount: user has no write access to mountpoint`

**Solution**:
```bash
# Add user to fuse group
sudo usermod -a -G fuse $USER
# Re-login
```

### Hivex Issues

**Problem**: `hivex: failed to open hive`

**Solution**:
- Ensure .LOG1 and .LOG2 files are deleted after hive commits
- ARC handles this automatically via ADR-010

## 📚 Documentation

- [Design Document](docs/design/design.md) - Technical architecture
- [Requirements](docs/design/requirements.md) - Functional requirements
- [Implementation Tasks](docs/design/tasks.md) - Development roadmap
- [Testing Guide](tests/TESTING_GUIDE.md) - Comprehensive testing procedures
- [Progress Tracker](docs/design/PROGRESS.md) - Current implementation status

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. **Code Style**: Follow PEP 8, use type hints, write docstrings
2. **Testing**: Maintain >80% test coverage for new code
3. **Documentation**: Update relevant docs with changes
4. **Commits**: Use conventional commit messages

## 📄 License

This project is for educational and research purposes only. Use responsibly and in accordance with applicable laws and regulations.

## 🙏 Acknowledgments

- **libguestfs** - Offline VM disk access
- **hivex** - Registry hive manipulation
- **ntfs-3g** - NTFS filesystem support
- **Gemini API** - AI-powered content generation

## 📞 Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Check the [Testing Guide](tests/TESTING_GUIDE.md)
- Review [Troubleshooting](#-troubleshooting) section

---

**Status**: 🟢 Active Development (65% Complete)  
**Last Updated**: 2024-01-15  
**Version**: 0.9.0-alpha
