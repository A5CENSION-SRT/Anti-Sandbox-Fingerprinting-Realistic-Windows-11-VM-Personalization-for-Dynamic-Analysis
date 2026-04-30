# Dual-Boot Setup Checklist (ARC — ADR-017)

One-time setup steps to prepare the dual-boot machine before running ARC.

---

## 1. Install Windows 11

Install Windows 11 on the target machine alongside Ubuntu (dual-boot).

Complete OOBE: create a local user account, skip optional setup steps.
Note the username you created — ARC uses it as the persona target.

---

## 2. Disable Fast Startup and Hibernate (Windows side)

ARC writes to the partition while Windows is offline.  If Fast Startup or
hibernation is enabled, the NTFS partition will be marked dirty and
ntfs-3g will refuse to mount it read-write.

In Windows, open an elevated PowerShell and run:

```powershell
powercfg.exe /hibernate off
```

Confirm there is no `C:\hiberfil.sys` after the next reboot.

Then **shut down fully** (Start → Shut Down, not Restart, not Sleep).

---

## 3. Boot into Ubuntu

Verify Windows is powered off (not suspended to RAM).

---

## 4. Find the Windows partition

```bash
sudo blkid -t TYPE=ntfs
```

Example output:
```
/dev/nvme0n1p3: LABEL="OS" UUID="XXXX-XXXX" TYPE="ntfs"
```

Note the device path (e.g. `/dev/nvme0n1p3`).

---

## 5. Configure ARC

Edit `config.yaml`:

```yaml
windows_partition: "/dev/nvme0n1p3"   # your device from step 4
windows_mount_point: "/mnt/arc_windows"
```

Or pass `--partition /dev/nvme0n1p3` on the command line.

---

## 6. Install system dependencies

```bash
sudo apt install -y \
    ntfs-3g fuse3 attr \
    libhivex-bin python3-hivex \
    sleuthkit
```

---

## 7. (Optional) Passwordless sudo for mount commands

To avoid sudo prompts during ARC runs, add a sudoers entry:

```
# /etc/sudoers.d/arc  (create with visudo -f /etc/sudoers.d/arc)
Cmnd_Alias ARC_MOUNT = \
    /usr/bin/mount -t ntfs-3g *, \
    /usr/bin/umount /mnt/arc_windows, \
    /usr/bin/ntfsfix *
%sudo ALL=(ALL) NOPASSWD: ARC_MOUNT
```

---

## 8. Verify ARC can mount

```bash
python main.py --preset home_user --dry-run
```

Then test a live mount manually:

```bash
sudo mount -t ntfs-3g -o uid=$(id -u),gid=$(id -g),streams_interface=windows,allow_other \
    /dev/nvme0n1p3 /mnt/arc_windows
ls /mnt/arc_windows/Windows/System32/config/
sudo umount /mnt/arc_windows
```

---

## 9. Run ARC

```bash
python main.py \
    --ai-generate --occupation "Software Engineer" \
    --interests gaming open-source \
    --partition /dev/nvme0n1p3 \
    --random-seed 4242
```

ARC will:
1. Mount the Windows partition via ntfs-3g.
2. Inject 360 days of coherent user artifacts.
3. Unmount cleanly.

---

## 10. Boot Windows and verify

Reboot into Windows.  It should reach the desktop with no chkdsk repair
triggered and no bluescreen.  You should see the injected artifacts in
`C:\Users\<persona>\Documents`, browser history, etc.

---

## 11. (Optional) Snapshot for isolated malware analysis

```bash
sudo dd if=/dev/nvme0n1p3 of=~/snapshots/run-042.img bs=4M status=progress
virsh define ./examples/libvirt-profile-template.xml
virsh start arc-run-042
```

See `examples/libvirt-profile-template.xml` for SMBIOS/MAC spoofing config.
