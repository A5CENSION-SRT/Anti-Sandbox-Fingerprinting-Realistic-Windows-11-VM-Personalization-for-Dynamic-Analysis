#!/usr/bin/env bash
# build_baseline_vhdx.sh — ARC Phase 8: create a post-OOBE Windows 11 baseline VHDX
#
# Usage:
#   ./scripts/build_baseline_vhdx.sh \
#       --iso ~/isos/Win11_23H2_x64.iso \
#       --unattend ./examples/unattend.xml \
#       --out ~/vms/baseline_win11_23h2.vhdx \
#       [--size 80G] [--name arc-baseline] [--ram 4096] [--vcpus 4]
#
# Prerequisites (install via apt):
#   sudo apt install -y virtinst qemu-system-x86 libvirt-daemon-system \
#                       libguestfs-tools qemu-img
#
# What this script does:
#   1. Creates an empty VHDX of the requested size with qemu-img.
#   2. Boots the Windows 11 ISO with virt-install using the unattend.xml
#      answer file for completely silent installation.
#   3. The unattend FirstLogonCommands shut the VM down after first-boot
#      completes (OOBE skipped, local account created, Windows ready).
#   4. Undefines the temporary libvirt domain — the VHDX remains on disk.
#   5. Optionally trims the VHDX with qemu-img convert to save space.
#
# The resulting VHDX is the "baseline" ARC injects into.  Copy it before
# each analyst run to preserve the clean state:
#   cp ~/vms/baseline_win11_23h2.vhdx ~/vms/run-$(date +%Y%m%d-%H%M).vhdx
#   python main.py --vhdx ~/vms/run-....vhdx --ai-generate ...

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
ISO=""
UNATTEND=""
OUT=""
SIZE="80G"
NAME="arc-baseline"
RAM="4096"
VCPUS="4"
TRIM="1"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $0 --iso <path> --unattend <path> --out <path.vhdx> [--size 80G] [--name arc-baseline] [--ram 4096] [--vcpus 4] [--no-trim]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)       ISO="$2";      shift 2 ;;
        --unattend)  UNATTEND="$2"; shift 2 ;;
        --out)       OUT="$2";      shift 2 ;;
        --size)      SIZE="$2";     shift 2 ;;
        --name)      NAME="$2";     shift 2 ;;
        --ram)       RAM="$2";      shift 2 ;;
        --vcpus)     VCPUS="$2";    shift 2 ;;
        --no-trim)   TRIM="0";      shift   ;;
        -h|--help)   usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

[[ -z "$ISO"      ]] && { echo "ERROR: --iso is required"; usage; }
[[ -z "$UNATTEND" ]] && { echo "ERROR: --unattend is required"; usage; }
[[ -z "$OUT"      ]] && { echo "ERROR: --out is required"; usage; }

[[ -f "$ISO"      ]] || { echo "ERROR: ISO not found: $ISO"; exit 1; }
[[ -f "$UNATTEND" ]] || { echo "ERROR: Unattend XML not found: $UNATTEND"; exit 1; }

OUT_DIR=$(dirname "$OUT")
mkdir -p "$OUT_DIR"

# ---------------------------------------------------------------------------
# Step 1: Create empty VHDX
# ---------------------------------------------------------------------------
echo "[1/4] Creating ${SIZE} VHDX at ${OUT} ..."
if [[ -f "$OUT" ]]; then
    echo "  WARNING: $OUT already exists. Overwriting."
    rm -f "$OUT"
fi
qemu-img create -f vhdx "$OUT" "$SIZE"

# ---------------------------------------------------------------------------
# Step 2: Inject unattend.xml into the ISO via a temporary floppy image
# ---------------------------------------------------------------------------
# Windows setup reads unattend.xml from a floppy (A:), CDROM, or EFI partition.
# We use a vfat floppy image since virt-install supports --disk path=...,device=floppy.
echo "[2/4] Preparing unattend floppy image ..."
FLOPPY=$(mktemp --suffix=.img)
mkdosfs -F 12 -n UNATTEND "$FLOPPY" 1440 2>/dev/null || truncate -s 1474560 "$FLOPPY"
mcopy -i "$FLOPPY" "$UNATTEND" ::autounattend.xml 2>/dev/null || {
    # fallback: mount and copy
    FLOPPY_MNT=$(mktemp -d)
    sudo mount -o loop "$FLOPPY" "$FLOPPY_MNT"
    sudo cp "$UNATTEND" "$FLOPPY_MNT/autounattend.xml"
    sudo umount "$FLOPPY_MNT"
    rmdir "$FLOPPY_MNT"
}

# ---------------------------------------------------------------------------
# Step 3: Install Windows via virt-install
# ---------------------------------------------------------------------------
echo "[3/4] Installing Windows 11 via virt-install (this takes 30–60 min) ..."
echo "  VM name: $NAME | RAM: ${RAM}MB | vCPUs: $VCPUS"
echo "  Log: /tmp/${NAME}-install.log"

# virt-install --wait -1 blocks until the VM shuts down (triggered by
# FirstLogonCommands in unattend.xml: shutdown /s /t 0 after first-boot)
virt-install \
    --name        "$NAME" \
    --memory      "$RAM" \
    --vcpus       "$VCPUS" \
    --os-variant  win11 \
    --machine     q35 \
    --features    smm=on \
    --loader      /usr/share/OVMF/OVMF_CODE_4M.ms.fd \
    --loader-secure yes \
    --nvram       "/var/lib/libvirt/qemu/nvram/${NAME}_VARS.fd" \
    --nvram-template /usr/share/OVMF/OVMF_VARS_4M.ms.fd \
    --disk        "path=${OUT},format=vhdx,bus=sata,cache=writeback" \
    --disk        "${FLOPPY},device=floppy" \
    --cdrom       "$ISO" \
    --network     network=default,model=e1000e \
    --cpu         host-passthrough \
    --graphics    spice,listen=127.0.0.1 \
    --video       vga \
    --channel     spicevmc \
    --console     pty,target_type=serial \
    --noautoconsole \
    --wait        -1 \
    2>&1 | tee "/tmp/${NAME}-install.log"

# ---------------------------------------------------------------------------
# Step 4: Clean up
# ---------------------------------------------------------------------------
echo "[4/4] Cleaning up ..."
rm -f "$FLOPPY"
virsh undefine "$NAME" --nvram 2>/dev/null || true

# Optionally trim the VHDX to remove zeroed blocks
if [[ "$TRIM" == "1" ]]; then
    TRIMMED="${OUT%.vhdx}_trimmed.vhdx"
    echo "  Trimming VHDX to ${TRIMMED} ..."
    qemu-img convert -O vhdx -c "$OUT" "$TRIMMED"
    mv "$TRIMMED" "$OUT"
fi

FINAL_SIZE=$(du -sh "$OUT" | cut -f1)
echo ""
echo "============================================================"
echo "  Baseline VHDX ready: $OUT  (${FINAL_SIZE})"
echo ""
echo "  Next step: copy before each analyst run:"
echo "    cp '$OUT' ~/vms/run-\$(date +%Y%m%d-%H%M).vhdx"
echo "    python main.py --vhdx ~/vms/run-....vhdx \\"
echo "        --ai-generate --occupation 'Software Engineer' \\"
echo "        --timeline-days 360 --random-seed 4242"
echo "============================================================"
