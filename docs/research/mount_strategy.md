# ARC — Mount Strategy (Dual-Boot NTFS Direct Mount)

**Scope**: how ARC opens a Windows 11 NTFS partition from Ubuntu in a dual-boot setup,
what tools are used at each layer, why Windows must be cleanly shut down before ARC runs,
and the exact call sequence for each write type.

**Decisions recorded**: ADR-002, ADR-003, ADR-010, ADR-017.

**Supersedes**: the previous VHDX + libguestfs approach. libguestfs is no longer used.

---

## 1. Architecture shift — why dual-boot direct mount

The original plan mounted a VHDX image file via libguestfs (a QEMU-backed appliance). The new
approach targets a dual-boot machine where Windows 11 and Ubuntu co-exist on the same physical
disk (or separate disks). Ubuntu mounts the Windows NTFS partition directly using ntfs-3g and
writes to it offline.

| Dimension           | Old approach (VHDX + libguestfs)                  | New approach (dual-boot + ntfs-3g) |
|---------------------|---------------------------------------------------|------------------------------------|
| Target medium       | VHDX image file on disk                           | Raw NTFS partition (`/dev/sdXY`)   |
| Mount tool          | libguestfs QEMU appliance                         | ntfs-3g FUSE (direct)              |
| FUSE phase          | Separate `guestmount` step after guestfs unmount  | Unified — single ntfs-3g mount serves everything |
| Phase A + B split   | Required (guestfs can't do ADS/xattr fully)       | Collapsed — one mount phase        |
| Startup overhead    | ~10–30 s QEMU appliance boot per run              | ~1 s FUSE mount                    |
| Registry hives      | Pull bytes via guestfs, write to /tmp, push back  | Open directly from mount path (still copy to /tmp for crash-safety) |
| `$UsnJrnl:$J`       | ntfs-3g FUSE via `guestmount` (second phase)      | Same ntfs-3g mount, colon-path access |
| SI timestamp patch  | `setfattr` via ntfs-3g FUSE (second phase)        | Same ntfs-3g mount                 |
| Key constraint      | Image file must be VHDX; can test offline         | Windows partition must be present; need real hardware or VM with accessible disk |

**ADR-017 rationale**: the dual-boot direct mount removes libguestfs entirely, collapses the two-phase
mount sequence into one, and reduces startup overhead from ~30 s to ~1 s. The trade-off is that the
operator must supply a real partition path and ensure Windows is powered off — but that constraint
already existed (the VHDX baseline had to come from a booted-then-shut-down Windows install).

---

## 2. Why Windows must be powered off (not hibernated)

Windows Fast Startup ("hybrid shutdown") does not fully unmount the NTFS volume. Instead, it writes
the kernel session to `hiberfil.sys` and marks the volume as "dirty" in the NTFS boot sector.

**What ntfs-3g sees**: attempting `mount -t ntfs-3g /dev/sdXY /mnt/windows -o rw` against a
hibernated volume produces:

```
ntfs-3g: Windows is hibernated, refused to mount.
```

**What the in-kernel ntfs3 driver sees**: similar refusal; `dmesg` shows
`volume is dirty and requires repair on next Windows boot`.

This means the operator must ensure Windows performed a **full shutdown** (not Fast Startup) before
ARC runs. The same requirement applies to the $UsnJrnl, registry hives, and EVTX channel files:

| Structure              | State after clean shutdown | State after Fast Startup |
|------------------------|---------------------------|--------------------------|
| NTFS volume dirty bit  | clear                     | set (ntfs-3g refuses rw) |
| `$Extend/$UsnJrnl:$J` | flushed                   | partial cache flush      |
| Hive `.LOG1`/`.LOG2`  | flushed or empty           | may have unwritten pages |
| `hiberfil.sys`         | absent or zero             | present, nonzero         |

### 2.1 Permanent fix: disable Fast Startup in Windows

From an elevated PowerShell or Command Prompt in Windows:

```
powercfg.exe /hibernate off
```

This disables hibernation entirely, which also disables Fast Startup (Fast Startup depends on
`hiberfil.sys`). After this, every Windows shutdown is a full shutdown. This is the recommended
operator configuration — do it once when setting up the dual-boot machine.

Alternative via GUI: Control Panel → Power Options → Choose what the power buttons do →
uncheck "Turn on fast startup (recommended)".

### 2.2 Recovery: clearing the dirty flag without destroying hibernation data

If the operator forgot to fully shut down Windows, `ntfsfix` can clear the dirty flag:

```bash
sudo ntfsfix --clear-dirty /dev/nvme0n1p3
```

This clears the dirty/hibernation flag in the NTFS boot sector without touching `hiberfil.sys`.
Windows will cold-boot next time (losing the hibernated session) but the partition can be safely
mounted read-write by ARC immediately.

**Do not use `mount -o remove_hiberfile`** — that option deletes `hiberfil.sys` outright, which
destroys the hibernated session and can leave Windows in an inconsistent state.

### 2.3 Pre-flight check in ARC

`LinuxMountBackend.mount()` performs a pre-flight before mounting:

```python
result = subprocess.run(
    ["ntfsinfo", str(self._partition), "--force"],
    capture_output=True, text=True
)
if "Volume is dirty" in result.stdout or result.returncode != 0:
    raise LinuxMountBackendError(
        "Windows partition is dirty (hibernated?). "
        "Run `sudo ntfsfix --clear-dirty <partition>` or ensure Windows did a full shutdown."
    )
```

---

## 3. Tool stack

### 3.1 ntfs-3g (primary — replaces libguestfs)

**Package**: `ntfs-3g` (system package; on Ubuntu 22.04+: `apt install ntfs-3g fuse3`)

**What it does**: FUSE-based NTFS driver with full read-write support. For ARC's use cases:

- **File I/O**: standard POSIX `open()`, `os.makedirs()`, `os.stat()`, `os.unlink()`.
- **Alternate Data Streams** (ADS): colon-path syntax with `streams_interface=windows` mount option.
  `open('/mnt/windows/$Extend/$UsnJrnl:$J', 'r+b')` works directly in Python.
- **NTFS timestamp patching**: `setfattr -n system.ntfs_times -v 0x<hex>` sets all four
  `$STANDARD_INFORMATION` timestamps.
- **NTFS attribute flags**: `setfattr -n system.ntfs_attrib_be -v 0x<hex>` sets file attribute bits
  (hidden, system, archive).

**What it does NOT do**: provide `$FILE_NAME` (FN) timestamp writes — FN is kernel-only per ADR-009.

**Why not in-kernel ntfs3 driver (Linux 5.15+)**: ntfs3 does not expose the `streams_interface=windows`
mount option, so colon-path ADS access (`$UsnJrnl:$J`) is not available via the in-kernel driver.
ntfs-3g is required. (ntfs3 supports `system.ntfs_attrib` but not `system.ntfs_times` — confirmed
against Linux kernel docs. For our NTFS_TIMES xattr writes, ntfs-3g is the only option.)

### 3.2 hivex

Same as before — unchanged. `hivex.Hivex(path, write=True)` opens a hive file on-disk. With the
direct mount, the hive path is a real host filesystem path (e.g. `/mnt/windows/Windows/System32/config/SOFTWARE`)
rather than bytes pulled via guestfs. ARC still copies to `/tmp` before opening with hivex for
crash-safety (so the original hive is untouched if the Python process crashes mid-write).

### 3.3 setfattr / getfattr

From `attr` package: `apt install attr`. Used to set ntfs-3g extended attributes:

- `system.ntfs_times` (4 × 64-bit FILETIME, little-endian) — patches SI timestamps
- `system.ntfs_attrib_be` (4-byte big-endian flags) — patches file attribute bits

Both are invoked via `subprocess.run(["setfattr", ...])` from Python (no Python binding needed).

---

## 4. Partition discovery

In a dual-boot setup, the Windows NTFS partition must be identified before mounting. ARC does
not hardcode a device path.

### 4.1 Automatic discovery

```python
import subprocess, json

def find_windows_partition() -> str:
    """Return the device path of the Windows system NTFS partition."""
    result = subprocess.run(
        ["lsblk", "--json", "-o", "NAME,FSTYPE,SIZE,LABEL,MOUNTPOINT"],
        capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)

    def walk(devices):
        for dev in devices:
            if (dev.get("fstype") == "ntfs"
                    and dev.get("mountpoint") is None
                    and "Windows" not in (dev.get("label") or "")  # skip WinRE
                    and "Recovery" not in (dev.get("label") or "")):
                path = "/dev/" + dev["name"]
                # Confirm it's a Windows system partition by checking for key files
                try:
                    probe = subprocess.run(
                        ["sudo", "ntfsls", path, "-p", "--", "Windows"],
                        capture_output=True, text=True, timeout=10
                    )
                    if probe.returncode == 0:
                        return path
                except Exception:
                    pass
            for child in dev.get("children", []):
                result = walk([child])
                if result:
                    return result
        return None

    return walk(data.get("blockdevices", []))
```

### 4.2 Manual configuration (preferred for production)

Add to `config.yaml`:

```yaml
# Dual-boot Windows NTFS partition device path.
# Run `sudo blkid -t TYPE=ntfs` to find it, then set here.
# Example: /dev/sda3 or /dev/nvme0n1p3
windows_partition: "/dev/nvme0n1p3"

# Mount point ARC uses (created if absent; must exist on reuse)
windows_mount_point: "/mnt/arc_windows"
```

`LinuxMountBackend` reads from `config.yaml` by default; `--partition` CLI flag overrides it.

### 4.3 Typical dual-boot partition layout

On a machine where Windows was installed first, then Ubuntu alongside:

```
/dev/nvme0n1p1   100 MB    EFI System Partition (FAT32) — shared by both OSes
/dev/nvme0n1p2   16 MB     Microsoft Reserved Partition
/dev/nvme0n1p3   ~150 GB   Windows (NTFS) ← ARC target
/dev/nvme0n1p4   ~700 MB   Windows Recovery Environment (NTFS, label "WinRE")
/dev/nvme0n1p5   ~2 GB     Ubuntu swap
/dev/nvme0n1p6   ~(rest)   Ubuntu root (ext4)
```

ARC mounts only `/dev/nvme0n1p3`. The EFI, WinRE, and Ubuntu partitions are never touched.

Use UUID instead of device path in `/etc/fstab` — UUIDs are stable across reboots and disk
reordering:

```bash
sudo blkid /dev/nvme0n1p3
# → /dev/nvme0n1p3: LABEL="Windows" UUID="F656C6B256C67455" TYPE="ntfs"
```

---

## 5. Mount command

```bash
sudo mount -t ntfs-3g \
    -o uid=$(id -u),gid=$(id -g),streams_interface=windows,allow_other \
    /dev/nvme0n1p3 \
    /mnt/arc_windows
```

**Option breakdown**:

| Option | Purpose |
|--------|---------|
| `uid=$(id -u),gid=$(id -g)` | All files appear owned by the ARC operator user. File writes from the user-level Python process succeed without further permission gymnastics. |
| `streams_interface=windows` | **Required for ADS access.** Exposes colon-path syntax: `open('/mnt/arc_windows/$Extend/$UsnJrnl:$J')` works. Without this, ADS are hidden. |
| `allow_other` | FUSE option: allows non-root processes to access the mount. Required because `mount` runs as root but ARC's Python runs as the operator user. |
| `remove_hiberfile` | **Do not use.** Destroys hiberfil.sys. Use `ntfsfix --clear-dirty` instead (§2.2). |

**Python implementation** in `LinuxMountBackend.mount()`:

```python
import subprocess, os
from pathlib import Path

self._mount_point = Path(self._config.windows_mount_point)
self._mount_point.mkdir(parents=True, exist_ok=True)

uid = os.getuid()
gid = os.getgid()

subprocess.run(
    [
        "sudo", "mount", "-t", "ntfs-3g",
        "-o", f"uid={uid},gid={gid},streams_interface=windows,allow_other",
        str(self._partition),
        str(self._mount_point),
    ],
    check=True,
    timeout=30,
)
```

### 5.1 sudo without password prompt

For automated ARC runs, add a sudoers rule:

```
# /etc/sudoers.d/arc — edit via visudo
Cmnd_Alias ARC_MOUNT = /usr/bin/mount -t ntfs-3g *, /usr/bin/umount /mnt/arc_windows
%arc-operators ALL=(ALL) NOPASSWD: ARC_MOUNT
```

Or use a startup-time manual mount and keep the partition mounted for the duration of the ARC run.

---

## 6. Collapsed mount sequence — single phase

Under the old VHDX approach there were two sequential phases:

- **Phase A**: libguestfs for registry + bulk filesystem writes (no ADS/xattr support)
- **Phase B**: `guestmount` (ntfs-3g FUSE) for SI timestamp patches + `$UsnJrnl:$J` appends

With direct ntfs-3g mount, **Phase A and Phase B collapse into one**. The single ntfs-3g mount
handles everything:

```
ARC run:
  1. mount (§5) — ntfs-3g FUSE up
  2. pre-flight hive log check (§8)
  3. all registry writes (hivex on /mnt/arc_windows/Windows/System32/config/...)
  4. all filesystem writes (standard Python file I/O on /mnt/arc_windows/Users/...)
  5. all SI timestamp patches (setfattr system.ntfs_times on /mnt/arc_windows/...)
  6. all $UsnJrnl:$J appends (open colon-path, write USN_RECORD_V3)
  7. sync + unmount (§10)
```

`LinuxMountBackend.host_fuse_mount()` now returns `self._mount_point` immediately (no subprocess
call needed — the mount is already active). `host_fuse_unmount()` is a no-op (the main `unmount()`
handles teardown).

---

## 7. File I/O through the mount

All file reads and writes are standard Python I/O on paths under the mount point.

**Reading**:
```python
# In LinuxMountBackend.read_bytes()
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
return real_path.read_bytes()
```

**Writing**:
```python
# In LinuxMountBackend.write_bytes()
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
real_path.parent.mkdir(parents=True, exist_ok=True)
real_path.write_bytes(data)
```

**Directory creation**:
```python
import os
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
os.makedirs(real_path, exist_ok=True)
```

**Existence check**:
```python
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
return real_path.exists()
```

**File removal**:
```python
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
real_path.unlink()
```

**Directory listing**:
```python
real_path = self._mount_point / path.lstrip("/\\").replace("\\", "/")
return [e.name for e in real_path.iterdir()]
```

---

## 8. Registry hive editing (hivex)

Hive files are accessible at real host paths once the partition is mounted. ARC copies each hive
to `/tmp` before opening with hivex (crash-safety: if Python dies mid-write, the original hive
on the partition is untouched).

```python
import shutil, tempfile, hivex

# 1. Locate hive on the mounted partition
hive_real_path = self._mount_point / "Windows/System32/config/SOFTWARE"

# 2. Copy to /tmp for crash-safe editing
with tempfile.NamedTemporaryFile(delete=False, suffix=".hive", prefix="arc_hive_") as tmp:
    tmp_path = tmp.name
shutil.copy2(hive_real_path, tmp_path)

# 3. Open, write, commit
h = hivex.Hivex(tmp_path, write=True)
# ... apply HiveOperations ...
h.commit(None)
h.close()

# 4. Write modified hive back to partition
shutil.copy2(tmp_path, hive_real_path)
os.unlink(tmp_path)

# 5. Delete .LOG1 / .LOG2 (ADR-010)
for suffix in (".LOG1", ".LOG2"):
    log = hive_real_path.parent / (hive_real_path.name + suffix)
    try:
        log.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not delete hive log %s: %s — rollback risk", log, e)
```

**Hive paths** (all under `self._mount_point`):

| Hive | Path |
|------|------|
| `SOFTWARE` | `Windows/System32/config/SOFTWARE` |
| `SYSTEM` | `Windows/System32/config/SYSTEM` |
| `SAM` | `Windows/System32/config/SAM` |
| `SECURITY` | `Windows/System32/config/SECURITY` |
| `DEFAULT` | `Windows/System32/config/DEFAULT` |
| `NTUSER.DAT` | `Users/<username>/NTUSER.DAT` |
| `UsrClass.dat` | `Users/<username>/AppData/Local/Microsoft/Windows/UsrClass.dat` |

**Pre-flight check** (R28): before any hivex writes, ARC verifies each hive's `.LOG1`/`.LOG2`
are present, readable, and writable. Failure is a WARN (skip that hive) not an abort.

```python
def _preflight_hive_logs(hive_real_path: Path) -> bool:
    for suffix in (".LOG1", ".LOG2"):
        log = hive_real_path.parent / (hive_real_path.name + suffix)
        if not log.exists():
            return False
        try:
            log.write_bytes(log.read_bytes())  # round-trip write test
        except OSError:
            return False
    return True
```

---

## 9. $UsnJrnl:$J access via ADS colon-path

With `streams_interface=windows` mount option, ntfs-3g exposes NTFS Alternate Data Streams
via colon-path syntax at the OS level.

```python
# $Extend and $UsnJrnl are NTFS metadata files whose names start with $
# Dollar signs must be escaped in shell but are literal in Python strings

j_path  = self._mount_point / "$Extend" / "$UsnJrnl:$J"
max_path = self._mount_point / "$Extend" / "$UsnJrnl:$Max"

# Read $Max header
with open(max_path, "rb") as f:
    max_data = f.read(72)  # 8+8+8+8+8+8+... see ntfs_journal.md §3

# Append USN_RECORD_V3 records to $J
with open(j_path, "r+b") as f:
    f.seek(0, 2)          # seek to end
    current_next_usn = ...
    for record in records:
        f.write(pack_usn_record_v3(record))
    f.flush()

# Update $Max.NextUsn
# ... see services/ntfs/usn_journal_writer.py
```

**Shell note**: `$` characters need quoting/escaping in bash. In Python they are literal:
```python
path = self._mount_point / "$Extend" / "$UsnJrnl:$J"  # works; no escaping needed
```

---

## 10. SI timestamp patching (setfattr system.ntfs_times)

After writing files to the mount, ARC patches `$STANDARD_INFORMATION` timestamps via setfattr.
This is the same technique as the old Phase B; it now runs during the same mount phase as the
file writes.

```python
import subprocess, struct

def patch_si_timestamps(fuse_path: Path, dt: datetime) -> None:
    """Patch all four NTFS SI timestamps to dt (UTC)."""
    def to_filetime(d: datetime) -> int:
        return int((d.timestamp() + 11644473600) * 10_000_000)

    ft = to_filetime(dt)
    # system.ntfs_times = 4 × 64-bit LE FILETIME: atime, mtime, ctime, crtime
    packed = struct.pack("<QQQQ", ft, ft, ft, ft)
    hex_val = "0x" + packed.hex()

    subprocess.run(
        ["setfattr", "-n", "system.ntfs_times", "-v", hex_val, str(fuse_path)],
        check=True,
        timeout=5,
    )
```

**Windows FILETIME formula**: `FILETIME = (unix_timestamp_seconds + 11644473600) × 10_000_000`
The constant 11644473600 is the delta in seconds between the Windows epoch (1601-01-01) and the
Unix epoch (1970-01-01).

---

## 11. NTFS attribute flags (setfattr system.ntfs_attrib_be)

```python
def set_ntfs_attributes(
    fuse_path: Path,
    *,
    hidden: bool = False,
    system: bool = False,
    archive: bool = True,
) -> None:
    flags = 0
    if hidden:
        flags |= 0x0002   # FILE_ATTRIBUTE_HIDDEN
    if system:
        flags |= 0x0004   # FILE_ATTRIBUTE_SYSTEM
    if archive:
        flags |= 0x0020   # FILE_ATTRIBUTE_ARCHIVE

    packed = struct.pack(">I", flags)  # big-endian 4 bytes
    hex_val = "0x" + packed.hex()

    subprocess.run(
        ["setfattr", "-n", "system.ntfs_attrib_be", "-v", hex_val, str(fuse_path)],
        check=True,
        timeout=5,
    )
```

---

## 12. Unmount sequence

```python
def unmount(self) -> None:
    if self._mount_point is None:
        return
    # 1. Flush kernel write buffers
    subprocess.run(["sync"], check=True, timeout=30)
    # 2. Unmount
    subprocess.run(
        ["sudo", "umount", str(self._mount_point)],
        check=True,
        timeout=60,
    )
    self._mount_point = None
    logger.info("Unmounted Windows partition")
```

**If umount hangs** (processes still holding the mount):
```bash
sudo fuser -km /mnt/arc_windows    # kill processes
sudo umount /mnt/arc_windows       # retry
```

**After unmount, verify**:
```bash
mount | grep arc_windows    # should return nothing
```

---

## 13. Hive log cleanup (ADR-010)

After every hivex commit on any hive, delete `.LOG1` and `.LOG2`. See §8 for the implementation.
This is unchanged from the VHDX approach — the paths just now live on the real mount rather than
being addressed through guestfs.

---

## 14. Pre-OOBE requirement

The same constraint from ADR-003 applies: the Windows partition must have completed at least one
OOBE boot and clean shutdown before ARC runs. Key structures that only initialise after first boot:

| Structure              | Pre-OOBE state        | Post-OOBE state         |
|------------------------|-----------------------|-------------------------|
| `$Extend/$UsnJrnl:$J` | zero-sized or absent  | initialised, appendable |
| Hive `.LOG1`/`.LOG2`  | minimal               | real, deletable         |
| `SOFTWARE` hive        | 5–10 MB               | ~100 MB                 |
| `Security.evtx`        | empty channel         | real channel with chunks|
| `Windows/Prefetch/`    | empty                 | 10–20 .pf files present |

For the dual-boot setup, this means: set up the dual-boot, boot into Windows once and let it
complete OOBE, then shut down fully (with `powercfg /h off` already applied). After that, the
partition is ARC-ready. There is no need to build a VHDX or use virt-install.

---

## 15. Failure modes and recovery

| Failure | Cause | Recovery |
|---------|-------|----------|
| `ntfs-3g: Windows is hibernated, refused to mount` | Fast Startup on | Boot Windows, disable Fast Startup (`powercfg /h off`), shut down fully; or `ntfsfix --clear-dirty <dev>` |
| `setfattr: No such attribute: system.ntfs_times` | ntfs-3g version too old | `apt install ntfs-3g` — need version ≥ 2017.x |
| `setfattr: Permission denied` | mount lacks `allow_other` or `uid` mismatch | Remount with `allow_other,uid=$(id -u)` |
| ADS open fails: `$UsnJrnl:$J` not found | Missing `streams_interface=windows` option | Remount with that option; or volume not fully initialised (boot Windows once) |
| hivex fails with `HivexException` | Hive partially written or corrupt | Restore from `/tmp` backup; re-run from hive pre-flight |
| umount hangs | Python process still has file open | `sudo fuser -km /mnt/arc_windows`; then retry umount |
| `mount: /dev/nvme0n1p3 is already mounted` | Previous ARC run left mount up | `sudo umount /mnt/arc_windows` or `sudo fuser -km` then umount |

---

## 16. References

- ntfs-3g manual: `man ntfs-3g`; Arch Wiki: NTFS-3G
- ntfs-3g extended attributes wiki: github.com/tuxera/ntfs-3g/wiki/Using-Extended-Attributes
- NTFS3 kernel driver docs: docs.kernel.org/filesystems/ntfs3.html
- ntfsfix: `man ntfsfix` — `--clear-dirty` flag
- ADR-002, ADR-003, ADR-010, ADR-017 — `docs/design/decisions.md`
- Risk register R1, R3, R7, R8, R28 — `docs/MASTER_PLAN.md` §9
