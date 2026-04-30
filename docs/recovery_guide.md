# Windows Boot Recovery Guide
## Error 0xc0000098 — `\windows\system32\winload.efi` Missing or Corrupt

This guide covers recovery from the **exact failure mode** that occurred: the
ARC tool accidentally mounted and partially wrote to a live dual-boot Windows
partition while Windows was hibernated, corrupting the BCD store and boot files.

---

## What Happened

| Component corrupted | Effect |
|---|---|
| `hiberfil.sys` deleted | Windows cannot resume from hibernation |
| BCD store (`EFI\Microsoft\Boot\BCD`) overwritten | Bootloader cannot find `winload.efi` |
| Registry hives (SYSTEM, SOFTWARE, SAM) modified | Windows boots into recovery loop |

Error `0xc0000098` = Boot Configuration Data does not contain a valid OS entry.

---

## Step 0 — What You Need

- A **Windows 11 installation USB/DVD** (or Ventoy USB with a Windows ISO)
- Or: the **Windows recovery partition** (press F11/F8 at boot)

If you don't have installation media:
```bash
# On another Linux machine — create bootable Windows USB
sudo apt install wimtools genisoimage
# Download Windows 11 ISO from microsoft.com, then:
sudo dd if=Win11.iso of=/dev/sdX bs=4M status=progress
```

---

## Step 1 — Boot into Windows Recovery Environment

**Method A — USB:**
1. Insert Windows installation USB
2. Restart → press F12 (or Del/F2) for boot menu → select USB
3. Click **Repair your computer** (bottom-left, NOT Install now)

**Method B — Recovery partition:**
1. Restart → press **F8** repeatedly at boot
2. Select **Troubleshoot** → **Advanced options** → **Command Prompt**

---

## Step 2 — Rebuild BCD (Boot Configuration Data)

In the Recovery Command Prompt, run these commands in order:

```cmd
:: Step 2a — Find your Windows partition letter
diskpart
list volume
:: Look for the NTFS volume with "Windows" label — note its letter (e.g. C:)
exit

:: Step 2b — Fix the boot sector
bootrec /fixmbr
bootrec /fixboot

:: Step 2c — Rebuild BCD from scratch
bootrec /scanos
bootrec /rebuildbcd
:: When prompted "Add installation to boot list? (Yes/No/All)" — type: A

:: Step 2d — Verify BCD was created
bcdedit /enum all
```

If `bootrec /rebuildbcd` finds 0 installations, continue to Step 3.

---

## Step 3 — Manual BCD Reconstruction (if Step 2 found 0 installs)

```cmd
:: Identify your EFI partition (usually 100-500MB FAT32, System partition)
diskpart
list disk
select disk 0          :: your main disk
list partition
:: Note the EFI partition number (usually partition 1 or 2, type "System")
select partition 1     :: adjust to your EFI partition number
assign letter=Z        :: temporarily assign drive letter
exit

:: Recreate BCD store on EFI partition
bcdboot C:\Windows /s Z: /f UEFI

:: Rebuild identifiers
bcdedit /store Z:\EFI\Microsoft\Boot\BCD /set {bootmgr} device partition=Z:
bcdedit /store Z:\EFI\Microsoft\Boot\BCD /default {current}

:: Remove temporary drive letter
diskpart
select partition 1
remove letter=Z
exit
```

---

## Step 4 — Restore from Registry Backup (if Windows still won't boot)

Windows keeps automatic registry backups. Restore them:

```cmd
:: Go to the Windows drive (adjust C: if needed)
cd /d C:\Windows\System32\config

:: Back up the corrupted hives
mkdir corrupted_backup
copy SYSTEM corrupted_backup\
copy SOFTWARE corrupted_backup\
copy SAM corrupted_backup\
copy SECURITY corrupted_backup\

:: Restore from Windows automatic backup (RegBack)
copy C:\Windows\System32\config\RegBack\SYSTEM C:\Windows\System32\config\SYSTEM
copy C:\Windows\System32\config\RegBack\SOFTWARE C:\Windows\System32\config\SOFTWARE
copy C:\Windows\System32\config\RegBack\SAM C:\Windows\System32\config\SAM
copy C:\Windows\System32\config\RegBack\SECURITY C:\Windows\System32\config\SECURITY
```

> **Note:** RegBack files may be empty on Windows 10/11 (Microsoft disabled
> automatic backups in 1803+). If they're 0 bytes, use a System Restore point
> instead (Step 5).

---

## Step 5 — System Restore Point

In Recovery Environment:
1. **Advanced options** → **System Restore**
2. Choose a restore point from **before** the incident
3. Let it complete (takes 5–30 minutes)

---

## Step 6 — Startup Repair (Last Resort Before Reinstall)

In Recovery Environment:
1. **Advanced options** → **Startup Repair**
2. This runs automatically and can fix many boot issues
3. May need to run it **twice**

---

## Step 7 — Nuclear Option (Data-Safe Reinstall)

If nothing above works, do an **in-place upgrade repair**:

1. Boot from Windows USB → Click **Install now**
2. Accept license → Select **Upgrade** (NOT Custom/Clean install)
3. This reinstalls Windows while keeping your files, apps, and settings

---

## After Recovery — Prevent Recurrence

Once Windows boots again, do these immediately:

### Disable Fast Startup (most important!)
```
Control Panel → Power Options → 
  "Choose what the power buttons do" →
  "Turn on fast startup" → UNCHECK → Save changes
```

### Always shut down fully before using ARC from Linux
```
Start → Power → Hold Shift → Click "Shut down"
```

### Use VM images for testing, not your real partition
```bash
# Create a test disk image
qemu-img create -f raw windows11_test.img 64G

# Or convert an existing VM
qemu-img convert -f qcow2 -O raw windows11.qcow2 windows11_test.img

# Run ARC safely on the image
python main.py --image windows11_test.img --image-partition 3 --preset developer
```

---

## Quick Reference Card

```
ERROR 0xc0000098
      ↓
Boot Windows USB → Repair your computer → Command Prompt
      ↓
bootrec /fixmbr && bootrec /fixboot && bootrec /rebuildbcd
      ↓
Works? → Disable Fast Startup in Windows → Done ✓
      ↓ (No)
bcdboot C:\Windows /s Z: /f UEFI  (with EFI on Z:)
      ↓
Works? → Done ✓
      ↓ (No)
Restore RegBack hives or use System Restore
      ↓
Works? → Done ✓
      ↓ (No)
In-place upgrade repair from Windows USB
```

---

## For Linux Recovery (if GRUB also broken)

If your Linux GRUB was also affected:

```bash
# Boot from Ubuntu Live USB, then:
sudo mount /dev/nvme0n1p2 /mnt          # your Linux root partition
sudo mount /dev/nvme0n1p1 /mnt/boot/efi  # your EFI partition
sudo grub-install --target=x86_64-efi --efi-directory=/mnt/boot/efi --bootloader-id=ubuntu
sudo update-grub
```

---

*This guide was created as part of the ARC safety fix documentation.*
*See also: `scripts/emergency_recovery.sh` for automated recovery commands.*
