# ARC Setup Guide

This guide covers the full setup flow from cloning the repo to running ARC with a VHDX target on Windows.

## 1. Prerequisites

- Windows 11
- Python 3.10+ (3.11 recommended)
- Git
- PowerShell
- Local admin rights (needed for VHDX mount/dismount operations)

## 2. Clone The Repository

```powershell
git clone <your-repo-url>
cd arc
```

If your project layout is nested, enter the folder that contains `main.py`.

## 3. Create And Activate A Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by execution policy, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. Install Dependencies

Option A (recommended):

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

Option B (project helper script):

```powershell
install_deps.bat
```

## 5. Optional AI Configuration

AI profile generation uses `GEMINI_API_KEY`.

PowerShell (session only):

```powershell
$env:GEMINI_API_KEY="your-key"
```

Or create a `.env` file in project root:

```env
GEMINI_API_KEY=your-key
```

## 6. Quick Verification

Run a small validation pass before using VHDX mode:

```powershell
python -m pytest tests/test_core/test_vm_manager.py
python -m pytest tests/test_core/test_orchestrator_profile_variant.py
```

## 7. VHDX Setup In Windows Disk Management

ARC can write to a VHDX only when the VHDX contains a usable partition and drive letter.

1. Open Disk Management

- Win+X -> Disk Management

2. Create VHDX file

- Action -> Create VHD
- Choose file path (example: `C:\path\to\images\test.vhdx`)
- Format: VHDX
- Type: Dynamically expanding
- Size: 20 GB minimum (40 GB is practical)

3. Initialize disk

- In the bottom pane, right-click the new disk label (`Unknown`, `Not Initialized`)
- Select Initialize Disk
- Choose GPT

4. Create volume

- Right-click `Unallocated` space
- New Simple Volume
- Assign drive letter
- Format as NTFS (Quick Format is fine)

5. Verify

- Drive appears in File Explorer
- You can create and delete a test file

If you skip initialization or partitioning, ARC will fail to discover a writable mounted path.

## 8. Dry-Run vs Live Run

- Dry-run (`--dry-run` or Wizard dry-run = Yes): no artifact files are written.
- Live run (dry-run disabled): ARC writes to the mounted drive path when VHDX mode is used.

## 9. Run ARC With The Wizard

```powershell
python arc_wizard.py
```

Recommended wizard flow:

1. Manual Workflow
2. Mount a new VHD/VHDX -> Yes
3. Enter VHDX path
4. Choose profile (AI or static)
5. Set dry-run as needed
6. Run generation
7. Dismount when done

## 10. Run ARC With CLI

Live run to VHDX:

```powershell
python main.py --vhdx-path "C:\path\to\images\test.vhdx" --profile home_user
```

Dry-run using same VHDX mount flow:

```powershell
python main.py --vhdx-path "C:\path\to\images\test.vhdx" --profile home_user --dry-run
```

## 11. Common Errors And Fixes

### Error: Disk image is attached but uses RAW partition style

Cause:

- Disk exists but is not initialized/partitioned/formatted.

Fix:

- Initialize disk (GPT), create NTFS volume, assign drive letter.

### Error: Disk image already attached but no drive in Explorer

Cause:

- Attached at storage level but no visible volume/letter.

Fix:

- Check Disk Management for missing partition or letter.
- Assign a drive letter if needed.

### Error: Failed to locate Windows partition on mounted VHDX

Cause:

- No usable partition path was found.

Fix:

- Ensure at least one formatted NTFS partition exists with drive letter.
- Dismount and mount again.

### Error: The process cannot access the file because it is being used by another process

Cause:

- The VHDX is locked or being attached by another process.

Fix:

- Close tools that may hold the image.
- Dismount and retry.

## 12. Useful PowerShell Commands

Check VHDX attachment status:

```powershell
Get-DiskImage -ImagePath "C:\path\to\images\test.vhdx" |
  Select-Object ImagePath, Attached, DevicePath, Number
```

Check disk partition style:

```powershell
Get-Disk | Format-List Number, Path, PartitionStyle, OperationalStatus, Size
```

Dismount VHDX:

```powershell
Dismount-DiskImage -ImagePath "C:\path\to\images\test.vhdx"
```

## 13. First Successful Run Checklist

- Repo cloned and dependencies installed
- Virtual environment active
- VHDX initialized and formatted (NTFS)
- Drive letter assigned and visible
- ARC run completed with no failed services
- VHDX cleanly dismounted after completion
