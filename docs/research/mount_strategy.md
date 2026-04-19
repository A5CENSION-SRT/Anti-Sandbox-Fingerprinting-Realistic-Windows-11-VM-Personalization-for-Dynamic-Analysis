# ARC — Mount Strategy

**Scope**: how ARC opens a Windows 11 VHDX from a Linux host, what tools are used at each layer,
why the OOBE-first requirement exists, and the exact call sequence for each write type.

**Decisions recorded**: ADR-002, ADR-003, ADR-010.

---

## 1. Why OOBE must come first

Windows initialises several on-disk structures the first time its own kernel mounts a volume.
ARC cannot forge these correctly from scratch — doing so produces structures that Windows's repair
logic (`chkdsk`, registry recovery) treats as corrupt on next boot.

| Structure | Fresh ISO state | After first-boot | ARC can write offline? |
|-----------|----------------|-----------------|----------------------|
| `$Extend\$UsnJrnl:$J` | zero-sized or absent | initialised with valid `$Max` header | **Only by appending to an already-initialised journal** (see §4) |
| `$Extend\$UsnJrnl:$Max` | absent | 64-byte header with NextUsn, LowestValidUsn, AllocationDelta | Writable once it exists |
| `$LogFile` | ~1 MB placeholder | ~64 MB circular transaction log | Best-effort append only (R4) |
| Hive `.LOG1`/`.LOG2` | tiny placeholders | real transaction logs | Must be deleted after hivex write (ADR-010) |
| Hive `SOFTWARE` | minimal, ~5–10 MB | full, ~100 MB | Yes, via hivex |
| `Windows\System32\winevt\Logs\*.evtx` | empty channel files | real channels with valid chunk headers | Yes, by appending chunks |
| `Windows\Prefetch\*.pf` | none | 10–20 .pf files | Yes — but path-hash values depend on the runtime's hash routine |
| `C:\Users\<user>\AppData\*` | none | partial profile skeleton | Yes |

**The `$UsnJrnl` problem in detail**: the journal's sparse stream `$J` has an internal structure
established by the NTFS driver during volume initialisation. If ARC attempts to write `$J` header
bytes to a never-booted VHDX:

- The `$Max` header fields (`NextUsn`, `LowestValidUsn`) may be zero or uninitialised.
- `chkdsk` on next boot re-initialises the journal from scratch, wiping all injected records.
- The relationship between `$Max.UsnId` and the volume's sequence counter is set by the kernel,
  not stored in any file ARC can read.

**The hive-log problem**: a pre-first-boot hive's `.LOG1`/`.LOG2` may carry "dirty" page records
from the install process. If we write to the hive offline via hivex and don't clean these logs,
Windows replays the old dirty-page log over our changes on next boot — silently rolling back
everything we wrote.

**The EVTX problem**: empty channel files from a fresh ISO have no chunk headers; they are just
a 4 KB header with zero chunks. While we can write chunks, we have no reference template table
(channel/provider GUID → template binary layout), which is essential for BinaryXML encoding.
The template table for `Microsoft-Windows-Security-Auditing` is established on first login.

**Conclusion (ADR-003)**: the baseline VHDX must have completed at least one OOBE boot and
clean shutdown before ARC touches it.

---

## 2. Building the baseline VHDX

### 2.1 Automation via `scripts/build_baseline_vhdx.sh`

```bash
#!/bin/bash
set -euo pipefail
ISO=$1            # /path/to/Win11_23H2_x64.iso
UNATTEND=$2       # examples/unattend.xml
OUT=$3            # /path/to/baseline.vhdx
SIZE=${4:-80G}

qemu-img create -f vhdx "$OUT" "$SIZE"
virt-install \
    --name arc-baseline-build \
    --memory 4096 --vcpus 4 \
    --disk path="$OUT",format=vhdx,bus=virtio \
    --cdrom "$ISO" \
    --initrd-inject "$UNATTEND" \
    --extra-args "autounattend=/unattend.xml" \
    --os-variant win11 \
    --graphics spice \
    --wait -1 \
    --noautoconsole
virsh undefine arc-baseline-build
```

### 2.2 `examples/unattend.xml` key sections

The unattend file handles:
- **WindowsPE pass** — disk partitioning (`ModifyPartitions`), image selection.
- **specialize pass** — computer name from persona hostname; locale/keyboard.
- **oobeSystem pass** — skip OOBE wizard (`<HideEULAPage>`, `<SkipMachineOOBE>`);
  auto-login once; run `<FirstLogonCommands>`.

```xml
<FirstLogonCommands>
  <SynchronousCommand wcm:action="add">
    <Order>1</Order>
    <CommandLine>powershell.exe -Command "Start-Sleep -Seconds 180"</CommandLine>
    <Description>Wait for Windows to finish building journals and Prefetch</Description>
  </SynchronousCommand>
  <SynchronousCommand wcm:action="add">
    <Order>2</Order>
    <CommandLine>shutdown.exe /s /t 5</CommandLine>
    <Description>Clean shutdown</Description>
  </SynchronousCommand>
</FirstLogonCommands>
```

The 180-second idle is important: Windows writes the first `$UsnJrnl` records for profile
creation, the first Prefetch `.pf` files for Explorer/dwm/csrss, and the first EVTX chunks for
the Security channel. This gives ARC a real journal to append to.

### 2.3 Post-build validation

```bash
guestfish -a "$OUT" -i <<EOF
  ls /Windows/System32/config
  # Expected: SAM, SECURITY, SOFTWARE, SYSTEM, DEFAULT + *.LOG1 *.LOG2
  ls /Windows/Prefetch
  # Expected: at minimum EXPLORER.EXE-*.pf, SVCHOST.EXE-*.pf
  ls /Windows/System32/winevt/Logs
  # Expected: Application.evtx, Security.evtx, System.evtx with non-zero sizes
  cat /\$Extend/\$UsnJrnl:\$Max | hexdump -C | head
  # Expected: 8-byte MaximumSize, 8-byte AllocationDelta, 8-byte UsnId, 8-byte LowestUsn, 8-byte NextUsn
EOF
```

---

## 3. Tool stack

### 3.1 libguestfs

- **Python binding**: `import guestfs`  (via `python3-guestfs` system package)
- **What it does**: QEMU appliance-backed virtual filesystem API. Supports VHDX natively
  (`add_drive_opts(path, format="vhdx")`). Provides: `read_file`, `write`, `mkdir_p`, `stat`,
  `utimens`, `setxattr`, `getxattr`, inspection APIs.
- **What it does NOT do**: write NTFS alternate data streams via colon-path syntax (untested);
  expose `$MFT` entry numbers; expose per-stream sparse-file operations reliably.
- **Environment**: `export LIBGUESTFS_BACKEND=direct` skips libvirt overhead; faster for
  one-shot ARC runs.

### 3.2 hivex

- **Python binding**: `import hivex` (via `python3-hivex` system package)
- **API used**:
  - `h = hivex.Hivex(path, write=True)` — open hive file in write mode
  - `h.root()` → `int` node handle
  - `h.node_name(node)` → `str`
  - `h.node_get_child(node, name)` → `int` or `None`
  - `h.node_add_child(parent, name)` → `int` new node
  - `h.node_set_value(node, {"key": k, "t": REG_SZ, "value": v})`
  - `h.node_delete_child(node)` — deletes the node and all descendants
  - `h.commit(None)` — flush to the hive file
  - `h.close()`
- **Value types**: `hivex.VALUE_TYPE.REG_SZ = 1`, `REG_BINARY = 3`, `REG_DWORD = 4`,
  `REG_QWORD = 11`, `REG_MULTI_SZ = 7`, `REG_EXPAND_SZ = 2`.
- **Encoding**: REG_SZ and REG_EXPAND_SZ values must be passed as UTF-16-LE bytes
  (+ null terminator).
- **Limit**: values larger than ~1 MB are not supported (R26). Don't write `BCD`, `IconStreams`,
  or `ShellBag` large blobs.

### 3.3 ntfs-3g via guestmount (FUSE)

Used for two operations that libguestfs cannot do:

1. **`$STANDARD_INFORMATION` timestamp patching** — `setfattr -n system.ntfs_times -v ...`
2. **`$UsnJrnl:$J` raw-stream append** — `open("/mnt/arc/$Extend/$UsnJrnl:$J", "r+b")`

```bash
# Mount after guestfs unmount
guestmount \
    -a /path/to/run.vhdx \
    --rw \
    -m /dev/sda2 \
    -o allow_other \
    /mnt/arc

# ... Python FUSE operations ...

guestunmount /mnt/arc
```

`guestmount` uses ntfs-3g internally; it exposes all NTFS metadata APIs that ntfs-3g supports.
The `allow_other` option lets ARC's Python process (running as non-root but with CAP_DAC_OVERRIDE)
access mount paths.

---

## 4. Mount sequence — end-to-end

The ARC pipeline uses two sequential mount phases. They **cannot** overlap — guestfs and the
FUSE mount both hold the VHDX; only one can be active at a time.

### Phase A: guestfs mount (registry + bulk filesystem)

```python
import guestfs

g = guestfs.GuestFS(python_return_dict=True)
g.add_drive_opts(str(vhdx_path), format="vhdx", readonly=False)
g.set_backend("direct")          # faster than libvirt
g.launch()

roots = g.inspect_os()
assert len(roots) == 1, f"Unexpected OS count: {roots}"
root = roots[0]

# Find Windows + EFI partitions
mounts = g.inspect_get_mountpoints(root)
# mounts is typically {"/": "/dev/sda2", "/boot/efi": "/dev/sda1"}

for mp, dev in sorted(mounts.items(), key=lambda x: len(x[0])):
    g.mount(dev, mp)

# Now /Windows, /Users, etc. are accessible.
# hivex hive files are at:
#   /Windows/System32/config/SOFTWARE
#   /Windows/System32/config/SYSTEM
#   /Windows/System32/config/SAM
#   /Windows/System32/config/SECURITY
#   /Users/<username>/NTUSER.DAT
#   /Users/<username>/AppData/Local/Microsoft/Windows/UsrClass.dat
```

**Registry write sequence** per hive:

```python
# 1. Copy hive to host temp file (hivex needs a real host path)
hive_data = g.read_file("/Windows/System32/config/SOFTWARE")
with open("/tmp/arc_SOFTWARE", "wb") as f:
    f.write(hive_data)

# 2. Open, write, commit
h = hivex.Hivex("/tmp/arc_SOFTWARE", write=True)
# ... apply HiveOperations ...
h.commit(None)
h.close()

# 3. Write back to VHDX
with open("/tmp/arc_SOFTWARE", "rb") as f:
    g.write("/Windows/System32/config/SOFTWARE", f.read())

# 4. Delete .LOG1 / .LOG2 (ADR-010, R3)
for log in ["/Windows/System32/config/SOFTWARE.LOG1",
            "/Windows/System32/config/SOFTWARE.LOG2"]:
    try:
        g.rm(log)
    except guestfs.GuestFSException:
        audit.warn(f"Could not delete {log} — rollback risk")
```

**Filesystem writes**:

```python
g.mkdir_p("/Users/alex/Documents/Projects")
g.write("/Users/alex/Documents/report.docx", docx_bytes)
g.utimens("/Users/alex/Documents/report.docx",
          atsecs=int(atime.timestamp()), atnsecs=0,
          mtsecs=int(mtime.timestamp()), mtnsecs=0)
# Note: utimens sets atime + mtime only. ctime and FILETIME creation
# are NOT set — that requires the FUSE phase below.
```

**Unmount** (mandatory before FUSE phase):

```python
g.umount_all()
g.shutdown()
# g object is now unusable; VHDX is released
```

### Phase B: FUSE mount (SI timestamps + $UsnJrnl)

```python
import subprocess, os, struct, pathlib

subprocess.run([
    "guestmount",
    "-a", str(vhdx_path),
    "--rw",
    "-m", "/dev/sda2",
    "-o", "allow_other",
    "/mnt/arc"
], check=True)

# Patch SI timestamps
for event in scheduler.events_of("FILE_CREATE", "FILE_MODIFY"):
    fuse_path = pathlib.Path("/mnt/arc") / event.payload["path"].lstrip("/\\").replace("\\", "/")
    if not fuse_path.exists():
        continue
    # system.ntfs_times: 4 × 64-bit little-endian Windows FILETIMEs
    # FILETIME = (unix_timestamp + 11644473600) * 10_000_000
    def to_filetime(dt):
        return int((dt.timestamp() + 11644473600) * 10_000_000)
    atime_ft = to_filetime(event.timestamp)
    mtime_ft = to_filetime(event.timestamp)
    ctime_ft = to_filetime(event.timestamp)
    crtime_ft = to_filetime(event.timestamp)   # creation time
    packed = struct.pack("<QQQQ", atime_ft, mtime_ft, ctime_ft, crtime_ft)
    # setfattr requires hex string with 0x prefix
    hex_val = "0x" + packed.hex()
    subprocess.run(["setfattr", "-n", "system.ntfs_times", "-v", hex_val, str(fuse_path)], check=True)

# Append to $UsnJrnl (Phase 4b — see ntfs_journal.md for record format)
j_path = "/mnt/arc/$Extend/$UsnJrnl:$J"
max_path = "/mnt/arc/$Extend/$UsnJrnl:$Max"
# ... see services/ntfs/usn_journal_writer.py ...

subprocess.run(["guestunmount", "/mnt/arc"], check=True)
```

---

## 5. Hive log-file cleanup (ADR-010)

After every hivex commit on any hive, ARC deletes the corresponding `.LOG1` and `.LOG2` files.
This is not optional — it prevents Windows from replaying old dirty pages over ARC's writes.

**Hives and their log pairs**:

| Hive path | LOG1 | LOG2 |
|-----------|------|------|
| `Windows\System32\config\SOFTWARE` | `SOFTWARE.LOG1` | `SOFTWARE.LOG2` |
| `Windows\System32\config\SYSTEM` | `SYSTEM.LOG1` | `SYSTEM.LOG2` |
| `Windows\System32\config\SAM` | `SAM.LOG1` | `SAM.LOG2` |
| `Windows\System32\config\SECURITY` | `SECURITY.LOG1` | `SECURITY.LOG2` |
| `Windows\System32\config\DEFAULT` | `DEFAULT.LOG1` | `DEFAULT.LOG2` |
| `Users\<user>\NTUSER.DAT` | `NTUSER.DAT.LOG1` | `NTUSER.DAT.LOG2` |
| `Users\<user>\AppData\Local\Microsoft\Windows\UsrClass.dat` | `UsrClass.dat.LOG1` | `UsrClass.dat.LOG2` |

**Pre-flight check** (R28): before any hivex write, ARC verifies the log files exist,
are writable, and are removable. If pre-flight fails on a hive, ARC skips writing that hive
and logs a WARN. Do not abort the entire run — scrubbing other hives is still better than none.

```python
def _preflight_hive_logs(g, hive_guest_path: str) -> bool:
    for suffix in [".LOG1", ".LOG2"]:
        log = hive_guest_path + suffix
        try:
            stat = g.stat(log)
            # Attempt a test write (overwrite with same content)
            data = g.read_file(log)
            g.write(log, data)
        except guestfs.GuestFSException as e:
            audit.warn(f"Pre-flight failed for {log}: {e}")
            return False
    return True
```

---

## 6. Partition layout discovery

Win11 VHDX from virt-install with default partitioning:

```
/dev/sda1  100 MB   EFI System Partition (FAT32)
/dev/sda2  16 MB    Microsoft Reserved Partition
/dev/sda3  ~79 GB   Windows (NTFS) — the root
/dev/sda4  ~700 MB  Windows Recovery Environment (NTFS)
```

`guestfs.inspect_os()` auto-discovers the Windows root and `inspect_get_mountpoints()` maps
device → mount point. ARC should not hardcode `/dev/sda3`; use the inspection API.

**NTFS partition**: ARC writes to `/dev/sda3` (the Windows volume). The EFI and WinRE partitions
are untouched.

---

## 7. Failure modes and recovery

| Failure | Cause | Recovery |
|---------|-------|----------|
| `guestfs.GuestFSException: access denied` | VHDX is already mounted by another process | `guestunmount` any stale mounts; kill orphaned `guestfsd` processes |
| `hivex.HivexException: cannot open` | hive file is locked by Windows (won't happen in offline mode) | N/A offline; check file permissions |
| `setfattr: No such attribute: system.ntfs_times` | ntfs-3g version < 2016.2.22 | `apt install ntfs-3g` ≥ 2017.x |
| `guestmount` hung at launch | guestfs appliance kernel panic | `export LIBGUESTFS_DEBUG=1 LIBGUESTFS_TRACE=1` and check log |
| `$UsnJrnl:$J` write fails with ENOENT | colon-path not supported in this ntfs-3g version | Upgrade ntfs-3g; fallback to Option C (defer journal to boot) |
| hive `.LOG1` delete fails | readonly filesystem segment | Pre-flight check caught it; skip that hive, log WARN |

---

## 8. References

- libguestfs documentation: https://libguestfs.org/guestfs-python.3.html
- hivex Python bindings: `pydoc hivex`
- ntfs-3g extended attribute support: ntfs-3g docs, `man ntfs-3g`, "Extended attributes" section
- `guestmount` man page: `man guestmount`
- ADR-002, ADR-003, ADR-010 — `docs/design/decisions.md`
- Risk register R1, R3, R7, R8, R9, R28 — `docs/MASTER_PLAN.md` §9
