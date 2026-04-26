#!/usr/bin/env bash
#
# build_baseline_vhdx.sh - Automated Windows 11 baseline VHDX creation
#
# This script creates a post-OOBE Windows 11 VHDX suitable for ARC personalization.
# It uses virt-install to perform an unattended Windows installation with QEMU/KVM.
#
# Requirements:
#   - Ubuntu 24.04+ with KVM support
#   - libvirt, qemu-kvm, virt-install, qemu-utils
#   - Windows 11 ISO
#   - unattend.xml template
#
# Usage:
#   ./build_baseline_vhdx.sh <ISO_PATH> <UNATTEND_PATH> <OUTPUT_PATH> <SIZE_GB>
#
# Example:
#   ./build_baseline_vhdx.sh Win11_23H2.iso unattend.xml baseline.vhdx 64
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Missing dependencies
#   3 - VHDX creation failed
#   4 - VM installation failed
#   5 - Cleanup failed

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

readonly SCRIPT_NAME="$(basename "$0")"
readonly LOG_FILE="/tmp/build_baseline_vhdx_$(date +%Y%m%d_%H%M%S).log"

# VM configuration
readonly VM_NAME="arc-baseline-builder"
readonly VM_MEMORY=4096  # MB
readonly VM_VCPUS=2
readonly VM_DISK_BUS="sata"  # SATA for Windows compatibility
readonly VM_NETWORK="default"
readonly VM_GRAPHICS="none"  # Headless
readonly VM_TIMEOUT=3600  # 1 hour max installation time

# Colors for output
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[1;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_RESET='\033[0m'

# ============================================================================
# Logging Functions
# ============================================================================

log_info() {
    echo -e "${COLOR_BLUE}[INFO]${COLOR_RESET} $*" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $*" | tee -a "$LOG_FILE"
}

log_warning() {
    echo -e "${COLOR_YELLOW}[WARNING]${COLOR_RESET} $*" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${COLOR_RED}[ERROR]${COLOR_RESET} $*" | tee -a "$LOG_FILE" >&2
}

# ============================================================================
# Validation Functions
# ============================================================================

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME <ISO_PATH> <UNATTEND_PATH> <OUTPUT_PATH> <SIZE_GB>

Arguments:
  ISO_PATH       Path to Windows 11 ISO file
  UNATTEND_PATH  Path to unattend.xml template
  OUTPUT_PATH    Path for output VHDX file
  SIZE_GB        VHDX size in GB (minimum 64)

Example:
  $SCRIPT_NAME Win11_23H2.iso unattend.xml baseline.vhdx 64

Requirements:
  - Ubuntu 24.04+ with KVM support
  - libvirt, qemu-kvm, virt-install, qemu-utils installed
  - Windows 11 ISO (22H2 or later)
  - Valid unattend.xml for Windows 11

EOF
    exit 1
}

check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing_deps=()
    
    for cmd in virsh qemu-img virt-install qemu-system-x86_64; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_error "Install with: sudo apt install qemu-kvm libvirt-daemon-system virt-manager qemu-utils"
        return 2
    fi
    
    # Check KVM support
    if [[ ! -e /dev/kvm ]]; then
        log_error "/dev/kvm not found. KVM virtualization not available."
        log_error "Enable virtualization in BIOS or run on a physical machine."
        return 2
    fi
    
    # Check libvirt is running
    if ! systemctl is-active --quiet libvirtd; then
        log_error "libvirtd service is not running"
        log_error "Start with: sudo systemctl start libvirtd"
        return 2
    fi
    
    log_success "All dependencies satisfied"
    return 0
}

validate_arguments() {
    if [[ $# -ne 4 ]]; then
        log_error "Invalid number of arguments"
        usage
    fi
    
    local iso_path="$1"
    local unattend_path="$2"
    local output_path="$3"
    local size_gb="$4"
    
    # Validate ISO exists
    if [[ ! -f "$iso_path" ]]; then
        log_error "ISO file not found: $iso_path"
        return 1
    fi
    
    # Validate unattend.xml exists
    if [[ ! -f "$unattend_path" ]]; then
        log_error "unattend.xml not found: $unattend_path"
        return 1
    fi
    
    # Validate size is numeric and >= 64
    if ! [[ "$size_gb" =~ ^[0-9]+$ ]] || [[ "$size_gb" -lt 64 ]]; then
        log_error "Size must be a number >= 64 GB"
        return 1
    fi
    
    # Check if output already exists
    if [[ -f "$output_path" ]]; then
        log_warning "Output file already exists: $output_path"
        read -rp "Overwrite? (y/N): " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            log_info "Aborted by user"
            exit 0
        fi
        rm -f "$output_path"
    fi
    
    log_success "Arguments validated"
    return 0
}

# ============================================================================
# VHDX Creation Functions
# ============================================================================

create_vhdx() {
    local output_path="$1"
    local size_gb="$2"
    
    log_info "Creating VHDX file: $output_path (${size_gb}G)"
    
    if ! qemu-img create -f vhdx "$output_path" "${size_gb}G" &>>"$LOG_FILE"; then
        log_error "Failed to create VHDX file"
        return 3
    fi
    
    log_success "VHDX created successfully"
    return 0
}

# ============================================================================
# VM Installation Functions
# ============================================================================

cleanup_vm() {
    local vm_name="$1"
    
    log_info "Cleaning up VM: $vm_name"
    
    # Check if VM exists
    if virsh list --all | grep -q "$vm_name"; then
        # Destroy if running
        if virsh list --state-running | grep -q "$vm_name"; then
            log_info "Destroying running VM..."
            virsh destroy "$vm_name" &>>"$LOG_FILE" || true
        fi
        
        # Undefine VM
        log_info "Undefining VM..."
        if ! virsh undefine "$vm_name" --nvram &>>"$LOG_FILE"; then
            log_warning "Failed to undefine VM (may not exist)"
        fi
    fi
    
    log_success "VM cleanup complete"
    return 0
}

install_windows() {
    local iso_path="$1"
    local unattend_path="$2"
    local vhdx_path="$3"
    
    log_info "Starting Windows 11 installation..."
    log_info "This may take 30-60 minutes. Check $LOG_FILE for details."
    
    # Cleanup any existing VM with same name
    cleanup_vm "$VM_NAME"
    
    # Convert paths to absolute
    iso_path="$(realpath "$iso_path")"
    unattend_path="$(realpath "$unattend_path")"
    vhdx_path="$(realpath "$vhdx_path")"
    
    log_info "ISO: $iso_path"
    log_info "Unattend: $unattend_path"
    log_info "VHDX: $vhdx_path"
    
    # Create virt-install command
    local virt_install_cmd=(
        virt-install
        --name "$VM_NAME"
        --memory "$VM_MEMORY"
        --vcpus "$VM_VCPUS"
        --disk "path=$vhdx_path,format=vhdx,bus=$VM_DISK_BUS"
        --cdrom "$iso_path"
        --os-variant win11
        --network "network=$VM_NETWORK"
        --graphics "$VM_GRAPHICS"
        --console pty,target_type=serial
        --boot uefi
        --initrd-inject "$unattend_path"
        --extra-args "console=ttyS0"
        --wait "$VM_TIMEOUT"
        --noautoconsole
    )
    
    log_info "Running virt-install..."
    log_info "Command: ${virt_install_cmd[*]}"
    
    # Run installation
    if ! "${virt_install_cmd[@]}" &>>"$LOG_FILE"; then
        log_error "virt-install failed"
        log_error "Check log file: $LOG_FILE"
        cleanup_vm "$VM_NAME"
        return 4
    fi
    
    log_success "Windows installation completed"
    
    # Wait for VM to shut down
    log_info "Waiting for VM to shut down..."
    local wait_count=0
    while virsh list --state-running | grep -q "$VM_NAME"; do
        sleep 5
        wait_count=$((wait_count + 1))
        if [[ $wait_count -gt 60 ]]; then  # 5 minutes max
            log_warning "VM did not shut down automatically, forcing shutdown..."
            virsh destroy "$VM_NAME" &>>"$LOG_FILE" || true
            break
        fi
    done
    
    log_success "VM shut down"
    
    # Cleanup VM definition
    cleanup_vm "$VM_NAME"
    
    return 0
}

# ============================================================================
# Main Function
# ============================================================================

main() {
    log_info "=== ARC Baseline VHDX Builder ==="
    log_info "Log file: $LOG_FILE"
    
    # Validate arguments
    if ! validate_arguments "$@"; then
        exit 1
    fi
    
    local iso_path="$1"
    local unattend_path="$2"
    local output_path="$3"
    local size_gb="$4"
    
    # Check dependencies
    if ! check_dependencies; then
        exit 2
    fi
    
    # Create VHDX
    if ! create_vhdx "$output_path" "$size_gb"; then
        exit 3
    fi
    
    # Install Windows
    if ! install_windows "$iso_path" "$unattend_path" "$output_path"; then
        log_error "Installation failed, cleaning up..."
        rm -f "$output_path"
        exit 4
    fi
    
    # Final validation
    if [[ ! -f "$output_path" ]]; then
        log_error "Output VHDX not found after installation"
        exit 4
    fi
    
    local vhdx_size
    vhdx_size="$(du -h "$output_path" | cut -f1)"
    
    log_success "=== Baseline VHDX created successfully ==="
    log_success "Output: $output_path"
    log_success "Size: $vhdx_size"
    log_success "Log: $LOG_FILE"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Validate baseline: ./scripts/validate_baseline.sh $output_path"
    log_info "  2. Run ARC: python main.py --vhdx $output_path --profile office_user"
    
    return 0
}

# ============================================================================
# Entry Point
# ============================================================================

# Trap errors and cleanup
trap 'log_error "Script failed at line $LINENO"' ERR

# Run main
main "$@"
