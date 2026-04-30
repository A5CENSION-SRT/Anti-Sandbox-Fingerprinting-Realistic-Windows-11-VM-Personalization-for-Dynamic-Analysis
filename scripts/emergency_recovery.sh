#!/usr/bin/env bash
# =============================================================================
# ARC Emergency Recovery Script
# Fixes Windows boot error 0xc0000098 caused by corrupted BCD / registry hives
#
# ⚠️  RUN THIS FROM THE WINDOWS RECOVERY ENVIRONMENT COMMAND PROMPT
#     (Boot from Windows USB → Repair your computer → Command Prompt)
#     NOT from Linux! These are Windows CMD commands.
#
# Usage: Copy & paste each section into the Recovery Command Prompt
# =============================================================================

set -euo pipefail

echo "============================================================"
echo "  ARC Emergency Recovery Helper"
echo "  Target: Windows Boot Error 0xc0000098"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# This script is a REFERENCE — paste the commands into Windows Recovery CMD.
# It is written as a bash script for readability and to live in the repo,
# but the actual commands must run in Windows Recovery CMD.
# ---------------------------------------------------------------------------

cat << 'WINDOWS_CMD'
============================================================
 STEP 1: Open Recovery CMD
============================================================
1. Insert Windows 11 installation USB
2. Restart → press F12 for boot menu → select USB
3. Click "Repair your computer" (NOT Install now)
4. Troubleshoot → Advanced options → Command Prompt

============================================================
 STEP 2: Identify your Windows drive letter
============================================================
diskpart
list volume
exit

:: Note the Windows partition letter (usually C: or D:)
:: Note the EFI/System partition (100-500MB FAT32)

============================================================
 STEP 3: Basic BCD repair (try this first)
============================================================
bootrec /fixmbr
bootrec /fixboot
bootrec /scanos
bootrec /rebuildbcd

:: When asked "Add installation to boot list?" type: A

============================================================
 STEP 4: If Step 3 found 0 installations — manual BCD rebuild
============================================================
:: Assign a letter to the EFI partition (replace '1' with your partition number)
diskpart
list disk
select disk 0
list partition
select partition 1
assign letter=Z
exit

:: Rebuild boot files
bcdboot C:\Windows /s Z: /f UEFI

:: Verify
bcdedit /store Z:\EFI\Microsoft\Boot\BCD /enum all

:: Remove temp letter
diskpart
select partition 1
remove letter=Z
exit

============================================================
 STEP 5: Restore registry hives from RegBack
============================================================
cd /d C:\Windows\System32\config
mkdir corrupted_backup 2>nul
copy SYSTEM corrupted_backup\SYSTEM.bak
copy SOFTWARE corrupted_backup\SOFTWARE.bak
copy SAM corrupted_backup\SAM.bak
copy SECURITY corrupted_backup\SECURITY.bak

:: Check if RegBack files are non-empty (if 0 bytes, skip to Step 6)
dir RegBack\

:: Restore (only if RegBack files are >0 bytes)
copy /y RegBack\SYSTEM SYSTEM
copy /y RegBack\SOFTWARE SOFTWARE
copy /y RegBack\SAM SAM
copy /y RegBack\SECURITY SECURITY

============================================================
 STEP 6: Verify boot configuration
============================================================
bcdedit /enum all
:: Should show Windows Boot Manager and Windows Boot Loader entries

============================================================
 STEP 7: Restart
============================================================
:: Remove USB, then:
shutdown /r /t 0

============================================================
 If Windows still fails — use In-Place Upgrade Repair
============================================================
:: Boot from USB → Install now → accept license → 
:: Select UPGRADE (NOT Custom) → keeps your files and apps

WINDOWS_CMD

echo ""
echo "============================================================"
echo " Linux-side commands (run from Ubuntu Live USB if needed)"
echo "============================================================"
echo ""

# Detect Linux root and EFI partitions
echo "Scanning for Linux and EFI partitions..."
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,LABEL 2>/dev/null || true

echo ""
cat << 'EOF'
# If GRUB was also broken, run these from Ubuntu Live USB:

# 1. Find your Linux root partition (ext4) and EFI partition (vfat)
lsblk -o NAME,FSTYPE,SIZE,LABEL

# 2. Mount them (adjust partition names)
sudo mount /dev/nvme0n1p5 /mnt          # Linux root (ext4)
sudo mount /dev/nvme0n1p1 /mnt/boot/efi  # EFI partition (vfat, ~100-500MB)
sudo mount --bind /dev /mnt/dev
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys

# 3. Reinstall GRUB
sudo chroot /mnt grub-install --target=x86_64-efi \
    --efi-directory=/boot/efi \
    --bootloader-id=ubuntu \
    --recheck

# 4. Update GRUB (this will detect Windows too)
sudo chroot /mnt update-grub

# 5. Unmount and reboot
sudo umount -R /mnt
sudo reboot

EOF

echo ""
echo "============================================================"
echo " Prevention — run AFTER recovery"
echo "============================================================"
echo ""
cat << 'EOF'
After Windows boots successfully:

1. Disable Fast Startup in Windows:
   Control Panel → Power Options → 
   "Choose what the power buttons do" → 
   Uncheck "Turn on fast startup" → Save

2. Always use FULL shutdown before ARC from Linux:
   Start → Power → Hold Shift → Click "Shut down"

3. Use VM disk images for testing, not real partitions:
   qemu-img create -f raw windows11_test.img 64G
   python main.py --image windows11_test.img --preset developer

EOF
