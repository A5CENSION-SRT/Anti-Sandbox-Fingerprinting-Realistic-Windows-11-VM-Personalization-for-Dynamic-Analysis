# ARC — VM-Detection Evasion

**Scope**: the complete taxonomy of VM-detection methods, what ARC covers (guest registry scrub),
what is the operator's responsibility (hypervisor config), the authoritative scrub matrix, and
the validation methodology.

**Decisions recorded**: ADR-011.

---

## 1. MITRE ATT&CK mapping

Sandbox-evasion falls under **T1497 — Virtualization/Sandbox Evasion** with three subtechniques:

| Subtechnique | Description | ARC coverage |
|-------------|-------------|--------------|
| T1497.001 — System Checks | CPUID, registry strings, device names, file paths, running services, loaded drivers | **Partial** — ARC scrubs registry + drivers; cannot scrub CPUID/SMBIOS (operator responsibility) |
| T1497.002 — User Activity Based Checks | Mouse movement, clipboard content, recently opened documents, browser history, prefetch age | **Covered** — 360-day timeline, real document/browser/prefetch artifacts |
| T1497.003 — Time Based Evasion | Sleep loops, QueryPerformanceCounter deltas, RDTSC timing | **Not covered** — ARC writes offline; no runtime patching. Mitigated by host-side CPU mode |

ARC's primary scope is T1497.001 (registry/driver scrub) and T1497.002 (activity realism).
T1497.003 (timing evasion) requires hypervisor-level CPUID / HPET configuration.

---

## 2. Detection surface taxonomy

### 2.1 Hardware-level (hypervisor config; ARC cannot change)

These values are returned by the CPU or firmware at every boot. They cannot be patched in a VHDX.

#### CPUID

| Leaf | Register | VM indicator | Notes |
|------|----------|-------------|-------|
| 0x1 | ECX bit 31 | Hypervisor present bit | Set by VirtualBox, VMware, QEMU/KVM, Hyper-V |
| 0x40000000 | EBX:ECX:EDX | Hypervisor vendor string | "KVMKVMKVM\0\0\0", "VMwareVMware", "VBoxVBoxVBox", "Microsoft Hv" |
| 0x40000001 | EAX | Interface signature | QEMU: 0x4B4D4551; Hyper-V: "Hv#1" |
| 0x40000002 | EAX:EBX:ECX:EDX | Version info | Vendor-specific |
| 0x80000002-4 | all | Processor brand string | May say "QEMU Virtual CPU v..." if not masked |

**Mitigation (operator)**:
- QEMU: `-cpu host` or `-cpu host,+hv-relaxed,+hv-vapic,+hv-spinlocks=0x1fff`
- QEMU: `-machine q35,hpet=on,smm=on` with hypervisor CPUID leaf hidden
- libvirt: `<cpu mode='host-passthrough'><feature policy='disable' name='hypervisor'/></cpu>`

#### SMBIOS / DMI

| Type | String | VM indicator |
|------|--------|-------------|
| Type 0 (BIOS Info) | BIOSVendor | "QEMU", "SeaBIOS", "EDK II", "innotek GmbH", "American Megatrends" (on VM) |
| Type 1 (System) | Manufacturer, ProductName | "QEMU Standard PC", "VirtualBox", "VMware Virtual Platform", "Microsoft Corporation" (Hyper-V) |
| Type 2 (Baseboard) | Manufacturer | "Oracle Corporation", "VMware, Inc." |
| Type 11 (OEM Strings) | OEM string | Often contains hypervisor-specific IDs |

**Mitigation (operator)**:
```xml
<!-- libvirt domain XML -->
<sysinfo type='smbios'>
  <bios>
    <entry name='vendor'>American Megatrends Inc.</entry>
    <entry name='version'>A15</entry>
    <entry name='date'>12/01/2022</entry>
  </bios>
  <system>
    <entry name='manufacturer'>Dell Inc.</entry>
    <entry name='product'>OptiPlex 7090</entry>
    <entry name='version'>Not Specified</entry>
    <entry name='serial'>DLOPT7090SN001</entry>
    <entry name='uuid'>$(uuidgen)</entry>
    <entry name='sku'>OptiPlex7090</entry>
    <entry name='family'>OptiPlex</entry>
  </system>
  <baseBoard>
    <entry name='manufacturer'>Dell Inc.</entry>
    <entry name='product'>0WMJ54</entry>
    <entry name='version'>A00</entry>
  </baseBoard>
</sysinfo>
```

#### MAC address

| OUI prefix | Assigned to | Used by |
|-----------|-------------|---------|
| `08:00:27` | PCS Systemtechnik GmbH | VirtualBox |
| `00:0C:29` / `00:50:56` | VMware | VMware ESXi / Workstation |
| `52:54:00` | QEMU/KVM default | QEMU virtio-net |
| `00:1B:21` | Intel Corp | Real Intel NICs |
| `00:E0:4C` | Realtek Semiconductor | Real Realtek NICs |

**Mitigation (operator)**:
```xml
<interface type='bridge'>
  <mac address='00:1b:21:aa:bb:cc'/>   <!-- Intel OUI + random lower 3 bytes -->
  <model type='e1000e'/>               <!-- Intel e1000e model, not virtio -->
</interface>
```

#### Disk serial number

`IOCTL_STORAGE_QUERY_PROPERTY` returns the disk serial. Virtual disks often return QEMU or
VirtualBox-generated serials.

**Mitigation (operator)**:
```xml
<disk type='file' device='disk'>
  <serial>S5GYNX0N712345Y</serial>     <!-- Samsung 970 EVO pattern -->
</disk>
```

---

### 2.2 Kernel-reflected (registry; ARC scrubs)

Windows reads SMBIOS and device information at boot and writes it into **volatile** registry keys
under `HKLM\HARDWARE\*`. Because they are volatile (deleted on each boot), ARC patching them
offline is pointless unless the operator has also set the correct SMBIOS strings at the hypervisor
level. However, ARC scrubs these anyway to handle the case where SMBIOS hints slip through.

These are different from `HKLM\SYSTEM\*` keys, which are persistent and *are* meaningfully
patched by ARC.

---

### 2.3 Driver / service level (ARC scrubs)

VM guest additions install Windows services and drivers that persist in the registry under
`HKLM\SYSTEM\CurrentControlSet\Services\` and `HKLM\SYSTEM\CurrentControlSet\Enum\`.

---

## 3. Authoritative scrub matrix

### 3.1 Service keys to delete

Under `HKLM\SYSTEM\CurrentControlSet\Services\`:

| Key name | Hypervisor | Current ARC coverage |
|----------|-----------|---------------------|
| `VBoxService` | VirtualBox | ❌ Add Phase 7 |
| `VBoxSF` | VirtualBox | ✅ vm_scrubber.py |
| `VBoxGuest` | VirtualBox | ❌ Add Phase 7 |
| `VBoxMouse` | VirtualBox | ✅ vm_scrubber.py |
| `VBoxVideo` | VirtualBox | ❌ Add Phase 7 |
| `VBoxNetAdp` | VirtualBox | ❌ Add Phase 7 |
| `VBoxNetFlt` | VirtualBox | ❌ Add Phase 7 |
| `vmtools` | VMware | ✅ vm_scrubber.py |
| `vmmouse` | VMware | ✅ vm_scrubber.py |
| `vmci` | VMware | ✅ vm_scrubber.py |
| `vmhgfs` | VMware | ✅ vm_scrubber.py |
| `vmxnet` | VMware | ✅ vm_scrubber.py |
| `vmrawdsk` | VMware | ✅ vm_scrubber.py |
| `vmusbmouse` | VMware | ✅ vm_scrubber.py |
| `vioscsi` | QEMU virtio | ❌ Add Phase 7 |
| `viostor` | QEMU virtio | ❌ Add Phase 7 |
| `qemu-ga` | QEMU Guest Agent | ❌ Add Phase 7 |
| `kvm` | KVM | ❌ Add Phase 7 |
| `kvmb` | KVM balloon | ❌ Add Phase 7 |
| `netkvm` | KVM virtio-net | ❌ Add Phase 7 |
| `balloon` | QEMU memory balloon | ❌ Add Phase 7 |
| `pvpanic` | QEMU panic device | ❌ Add Phase 7 |
| `spice-vdagent` | SPICE agent | ❌ Add Phase 7 |

### 3.2 ACPI / Enum keys to replace

Under `HKLM\SYSTEM\CurrentControlSet\Enum\ACPI\`:

| Key | VM indicator | Action |
|-----|-------------|--------|
| `VBOX0001` | VirtualBox ACPI | delete or replace with generic |
| `VMW0001` | VMware ACPI | delete or replace |
| `QEMU0002` | QEMU ACPI | delete |

✅ Covered by `vm_scrubber.py`.

Under `HKLM\SYSTEM\CurrentControlSet\Enum\IDE\` and `\SCSI\`:

| Key pattern | VM indicator | Action |
|------------|-------------|--------|
| `DiskQEMU_HARDDISK___` | QEMU disk | delete |
| `DiskVBOX_HARDDISK___` | VirtualBox disk | delete |
| `DiskVMware_Virtual_` | VMware disk | delete |

Replace with pattern matching a real Samsung / WD / Seagate disk:
```
DiskSamsung_SSD_970_EVO_Plus_1TB_<serial>
```

### 3.3 Hardware description keys

Under `HKLM\HARDWARE\DESCRIPTION\System\` (volatile but ARC writes anyway for defence-in-depth):

| Value | VM indicator | Replacement |
|-------|-------------|-------------|
| `SystemBiosVersion` | "QEMU", "VirtualBox", "VBOX", "VMware" | "DELL - 1072009\0A15" |
| `VideoBiosVersion` | "BOCHS", "BIOS" stub versions | "Nvidia Corp..." or "Intel Corp..." |
| `SystemBiosDate` | Abnormally old dates | Match SMBIOS date from operator config |

Under `HKLM\HARDWARE\DEVICEMAP\Scsi\Scsi Port 0\Scsi Bus 0\Target Id 0\Logical Unit Id 0\`:

| Value | VM indicator | Replacement |
|-------|-------------|-------------|
| `Identifier` | "QEMU HARDDISK", "VBOX HARDDISK" | "Samsung SSD 970 EVO Plus 1000GB" |
| `Type` | "DirectAccess" | Keep — correct |

### 3.4 Software installation keys

Under `HKLM\SOFTWARE\`:

| Key | VM indicator | Action |
|-----|-------------|--------|
| `Oracle\VirtualBox Guest Additions` | VirtualBox | delete |
| `VMware, Inc.\VMware Tools` | VMware | delete |
| `QEMU` | QEMU guest tools | delete (if present) |

Under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\`:

| Key pattern | VM indicator | Action |
|------------|-------------|--------|
| `{VBox*}` | VirtualBox | delete |
| `{VMware*}` | VMware | delete |
| `Oracle VM VirtualBox Guest Additions` | VirtualBox | delete |

❌ Not yet covered; add in Phase 7.

### 3.5 Hyper-V guest parameters (already covered)

`HKLM\SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters` — delete entire key.
✅ Covered by `vm_scrubber.py`.

### 3.6 NIC NetworkAddress override

Under `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}\`:

For each adapter subkey (0000, 0001, ...):
- Check `DriverDesc` to identify the adapter type
- Set `NetworkAddress` = 12-hex-digit MAC with Intel OUI (`001B21XXXXXX`)
  or Realtek OUI (`00E04CXXXXXX`)

❌ New service: `services/anti_fingerprint/mac_hygiene.py`. Add Phase 7.

### 3.7 SystemManufacturer / SystemProductName (already covered)

`HKLM\SYSTEM\CurrentControlSet\Control\SystemInformation`:
- `SystemManufacturer` → "Dell Inc."
- `SystemProductName` → "OptiPlex 7090"
- `SystemVersion` → "Not Specified"

✅ Covered by `hardware_normalizer.py`. Must be consistent with persona's hostname format
(a "Dell" hostname like `OPTIPLEX-ALEX` or `DESKTOP-7F3K2` is plausible; avoid `WIN-XXXX`).

### 3.8 BIOS keys (already covered)

`HKLM\HARDWARE\DESCRIPTION\System\BIOS`:
- `BIOSVendor` → "American Megatrends Inc."
- `BIOSVersion` → "A15"
- `BIOSReleaseDate` → "12/01/2022"
- `SystemManufacturer` → "Dell Inc."
- `SystemProductName` → "OptiPlex 7090"

✅ Covered by `hardware_normalizer.py`.

---

## 4. File-system markers (service presence on disk)

Beyond registry keys, many detection tools look for VM-guest files on disk:

| Path | VM indicator | Action |
|------|-------------|--------|
| `C:\Program Files\Oracle\VirtualBox Guest Additions\` | VirtualBox | Delete directory (if present in baseline) |
| `C:\Program Files\VMware\VMware Tools\` | VMware | Delete directory |
| `C:\Windows\System32\drivers\VBox*.sys` | VirtualBox | Delete files |
| `C:\Windows\System32\drivers\vmci.sys`, `vmmouse.sys`, `vmhgfs.sys` | VMware | Delete files |
| `C:\Windows\System32\VBoxService.exe`, `VBoxTray.exe` | VirtualBox | Delete |
| `C:\Windows\System32\vmtoolsd.exe`, `vmwaretray.exe` | VMware | Delete |

ARC uses libguestfs `g.rm_rf()` / `g.rm()` for file deletions during Phase 7.

Note: in QEMU + virtio mode without guest additions, most of these files won't be present in
the baseline VHDX. The registry keys (service entries from Windows's own detection) are the more
persistent concern.

---

## 5. Process and device checks (runtime; partially out of ARC scope)

Some evasion detectors do runtime process enumeration and device enumeration. These cannot be
fully addressed by offline VHDX modification, but ARC's service scrubbing removes the persistence
that causes these detections.

| Runtime check | What it looks for | ARC mitigation |
|--------------|------------------|---------------|
| Running processes | `VBoxService.exe`, `vmtoolsd.exe` | Deleting service keys prevents auto-start |
| Loaded drivers | VirtualBox/VMware driver names in `\Device\` namespace | Service key deletion prevents loading |
| `\\.\VBoxGuest` / `\\.\VMCIHostDev` device objects | Guest additions device | Service key deletion |
| `GetSystemMetrics(SM_REMOTESESSION)` | Is this an RDP session? | Not a VM indicator per se; accept |
| `HKLM\SYSTEM\...\Control\Terminal Server\WinStations\RDP-Tcp\` | RDP terminal server config | Leave alone; standard on all Windows |

---

## 6. Host-side hypervisor configuration recipe

### 6.1 libvirt / QEMU (the recommended path)

`examples/libvirt-profile-template.xml` includes:

```xml
<domain type='kvm'>
  <name>arc-analyst-run-042</name>
  <memory unit='GiB'>8</memory>
  <vcpu>4</vcpu>

  <!-- CPU: host-passthrough hides hypervisor CPUID leaf -->
  <cpu mode='host-passthrough'>
    <feature policy='disable' name='hypervisor'/>
    <!-- Enable Hyper-V enlightenments for KVM performance, but mask identification -->
    <feature policy='require' name='hv-relaxed'/>
    <feature policy='require' name='hv-vapic'/>
  </cpu>

  <!-- SMBIOS: Dell OptiPlex 7090 strings -->
  <sysinfo type='smbios'>
    <bios>
      <entry name='vendor'>American Megatrends Inc.</entry>
      <entry name='version'>A15</entry>
      <entry name='date'>12/01/2022</entry>
    </bios>
    <system>
      <entry name='manufacturer'>Dell Inc.</entry>
      <entry name='product'>OptiPlex 7090</entry>
      <entry name='serial'>DLOPT7090SN001</entry>
    </system>
  </sysinfo>

  <!-- Intel NIC (not virtio) with Intel OUI -->
  <devices>
    <interface type='bridge'>
      <mac address='00:1b:21:a7:b3:c9'/>
      <model type='e1000e'/>
    </interface>

    <!-- Disk with plausible Samsung serial -->
    <disk type='file' device='disk'>
      <driver name='qemu' type='vhdx'/>
      <source file='/path/to/run-042.vhdx'/>
      <target dev='sda' bus='sata'/>
      <serial>S5GYNX0N712345Y</serial>
    </disk>
  </devices>

  <!-- HPET timer (present on real hardware) -->
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='yes'/>
  </clock>
</domain>
```

### 6.2 VirtualBox (v1.1 scope — documented here for future)

VirtualBox uses `VBoxManage modifyvm` and `.vbox` XML:

```bash
VBoxManage modifyvm "arc-run-042" \
    --cpuidset 00000001 000306a9 02100800 7fbae3ff bfebfbff  # Ivy Bridge i5
VBoxManage setextradata "arc-run-042" \
    "VBoxInternal/Devices/pcbios/0/Config/DmiSystemVendor" "Dell Inc."
VBoxManage setextradata "arc-run-042" \
    "VBoxInternal/Devices/pcbios/0/Config/DmiSystemProduct" "OptiPlex 7090"
VBoxManage modifyvm "arc-run-042" \
    --macaddress1 001B21A7B3C9   # Intel OUI
```

Full VBoxManage recipe: deferred to v1.1.

---

## 7. Validation methodology

### 7.1 Test tools

| Tool | What it checks | Run location |
|------|---------------|-------------|
| `pafish` | CPUID, registry, process, file, timing, GPU checks | Inside the booted modified VM |
| `Al-Khaser` | Comprehensive evasion test suite (100+ checks) | Inside the booted modified VM |
| `InviZzzible` | Focused CPUID + registry checks | Inside the booted modified VM |
| Eric Zimmerman's `RECmd` | Registry forensics — scan for VM string markers | From Linux host pre-boot, via hivex or registry-parse |
| `MFTECmd` | $MFT analysis — check timestamp realism | From Linux host, on the VHDX |
| `PECmd` | Prefetch parser — check .pf file validity and times | From Linux host or inside VM |

### 7.2 Acceptance gate A8

**Target**: pafish + Al-Khaser flag ≤ 10 checks combined (baseline unmodified VirtualBox: ~50+).

**Expected remaining flags after ARC + correct hypervisor config**:
- CPUID timing-delta check (T1497.003) — can't fix without kernel patches
- GPU check — absent vGPU driver may still flag
- Screen resolution check (too small) — configure VM display appropriately
- Running time (uptime too short) — first boot after ARC will have short uptime; accept

**Pre-boot registry validation** (can be done without booting the VM):

```python
# Quick scan for VM strings in SOFTWARE hive
h = hivex.Hivex("SOFTWARE", write=False)

def walk_values(node, path):
    for v in h.node_values(node):
        val_data = h.value_string(v) if h.value_type(v)[0] in (1,2) else ""
        if any(s in val_data.lower() for s in ["vbox", "vmware", "qemu", "virtual"]):
            print(f"FOUND: {path}\\{h.value_key(v)} = {val_data}")
    for child in h.node_children(node):
        walk_values(child, path + "\\" + h.node_name(child))

walk_values(h.root(), "SOFTWARE")
```

---

## 8. Strings that trigger detection

The following literal strings are checked by pafish, Al-Khaser, and most malware evasion code.
ARC must ensure none survive in the persistent registry (SYSTEM, SOFTWARE, NTUSER.DAT) after
Phase 7:

```
VBox, VirtualBox, VBoxGuest, VBoxService, VBoxTray, VBoxMRXNP
VBOX HARDDISK, VBOX CD-ROM, innotek
VMware, vmtoolsd, VMware Tools, VMware SCSI, VMware SVGA
QEMU, QEMU HARDDISK, QEMU DVD-ROM, virtio, vioscsi, viostor
KVM, KVMKVMKVM, qemu-ga
Microsoft Virtual, Hyper-V, VMMS
Bochs, Bochs BIOS, BOCHSCPU
Xen, XenService, XenBus
Parallels, prl_tools
```

Identity strings to deploy instead:
```
Dell Inc., OptiPlex 7090
Intel(R) Core(TM) i7-10700
Samsung SSD 970 EVO Plus 1000GB (or WD_BLACK SN850X 1000GB)
American Megatrends Inc., BIOS A15
Realtek PCIe GbE / Intel I219-V
```

---

## 9. References

- MITRE ATT&CK T1497: https://attack.mitre.org/techniques/T1497/
- pafish source: https://github.com/a0rtega/pafish
- Al-Khaser source: https://github.com/LordNoteworthy/al-khaser
- InviZzzible source: https://github.com/CheckPointSW/InviZzzible
- QEMU CPUID masking: https://www.qemu.org/docs/master/system/qemu-cpu-models.html
- libvirt SMBIOS: https://libvirt.org/formatdomain.html#sysinfo
- ADR-011 — `docs/design/decisions.md`
