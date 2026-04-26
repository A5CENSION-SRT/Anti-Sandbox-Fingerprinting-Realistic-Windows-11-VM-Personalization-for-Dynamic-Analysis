#!/usr/bin/env bash
#
# validate_baseline.sh - Validate baseline VHDX quality for ARC
#
# This script validates that a baseline VHDX has all required structures
# initialized for ARC personalization. It checks for:
#   - $UsnJrnl:$Max header
#   - Prefetch files
#   - Non-empty event logs (System, Application, Security)
#   - Registry hives with .LOG files
#   - Proper NTFS filesystem structure
#
# Requirements:
#   - Ubuntu 24.04+ with libguestfs
#   - guestfish, virt-filesystems
#
# Usage:
#   ./validate_baseline.sh <VHDX_PATH>
#
# Example:
#   ./validate_baseline.sh baseline.vhdx
#
# Exit codes:
#   0 - Validation passed
#   1 - Invalid arguments
#   2 - Missing dependencies
#   3 - VHDX not found or invalid
#   4 - Validation failed (missing required structures)

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

readonly SCRIPT_NAME="$(basename "$0")"
readonly REPORT_FILE="/tmp/baseline_validation_$(date +%Y%m%d_%H%M%S).json"

# Validation thresholds
readonly MIN_PREFETCH_FILES=5
readonly MIN_EVTX_SIZE=65536  # 64 KB minimum
readonly REQUIRED_HIVES=(
    "Windows/System32/config/SYSTEM"
    "Windows/System32/config/SOFTWARE"
    "Windows/System32/config/SAM"
    "Windows/System32/config/SECURITY"
)

# Colors for output
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[1;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_RESET='\033[0m'

# Validation results
declare -A VALIDATION_RESULTS
declare -a VALIDATION_ERRORS
declare -a VALIDATION_WARNINGS

# ============================================================================
# Logging Functions
# ============================================================================

log_info() {
    echo -e "${COLOR_BLUE}[INFO]${COLOR_RESET} $*"
}

log_success() {
    echo -e "${COLOR_GREEN}[✓]${COLOR_RESET} $*"
}

log_warning() {
    echo -e "${COLOR_YELLOW}[⚠]${COLOR_RESET} $*"
}

log_error() {
    echo -e "${COLOR_RED}[✗]${COLOR_RESET} $*" >&2
}

# ============================================================================
# Validation Functions
# ============================================================================

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME <VHDX_PATH>

Arguments:
  VHDX_PATH  Path to baseline VHDX file to validate

Example:
  $SCRIPT_NAME baseline.vhdx

Requirements:
  - Ubuntu 24.04+ with libguestfs installed
  - guestfish, virt-filesystems commands available

Validation Checks:
  1. VHDX file exists and is readable
  2. NTFS filesystem detected
  3. \$UsnJrnl:\$Max header exists
  4. Prefetch files present (minimum $MIN_PREFETCH_FILES)
  5. Event logs initialized (System, Application, Security)
  6. Registry hives with .LOG files

Output:
  - Console: Human-readable validation results
  - JSON report: $REPORT_FILE

EOF
    exit 1
}

check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing_deps=()
    
    for cmd in guestfish virt-filesystems; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_error "Install with: sudo apt install libguestfs-tools"
        return 2
    fi
    
    log_success "All dependencies satisfied"
    return 0
}

validate_vhdx_exists() {
    local vhdx_path="$1"
    
    log_info "Validating VHDX file..."
    
    if [[ ! -f "$vhdx_path" ]]; then
        log_error "VHDX file not found: $vhdx_path"
        VALIDATION_ERRORS+=("VHDX file not found")
        return 3
    fi
    
    if [[ ! -r "$vhdx_path" ]]; then
        log_error "VHDX file not readable: $vhdx_path"
        VALIDATION_ERRORS+=("VHDX file not readable")
        return 3
    fi
    
    log_success "VHDX file exists and is readable"
    VALIDATION_RESULTS["vhdx_exists"]="true"
    return 0
}

validate_filesystem() {
    local vhdx_path="$1"
    
    log_info "Validating filesystem..."
    
    local filesystems
    if ! filesystems=$(virt-filesystems -a "$vhdx_path" 2>&1); then
        log_error "Failed to detect filesystems: $filesystems"
        VALIDATION_ERRORS+=("Failed to detect filesystems")
        return 3
    fi
    
    if ! echo "$filesystems" | grep -q "ntfs"; then
        log_error "NTFS filesystem not found"
        VALIDATION_ERRORS+=("NTFS filesystem not found")
        return 3
    fi
    
    log_success "NTFS filesystem detected"
    VALIDATION_RESULTS["filesystem"]="ntfs"
    return 0
}

validate_usn_journal() {
    local vhdx_path="$1"
    
    log_info "Validating \$UsnJrnl:\$Max header..."
    
    local usn_max_exists
    usn_max_exists=$(guestfish --ro -a "$vhdx_path" -i <<'EOF'
is-file /\$Extend/\$UsnJrnl:\$Max
EOF
)
    
    if [[ "$usn_max_exists" != "true" ]]; then
        log_error "\$UsnJrnl:\$Max not found"
        VALIDATION_ERRORS+=("\$UsnJrnl:\$Max not found")
        VALIDATION_RESULTS["usn_journal"]="false"
        return 4
    fi
    
    # Check size
    local usn_max_size
    usn_max_size=$(guestfish --ro -a "$vhdx_path" -i <<'EOF'
filesize /\$Extend/\$UsnJrnl:\$Max
EOF
)
    
    if [[ "$usn_max_size" -lt 32 ]]; then
        log_warning "\$UsnJrnl:\$Max size too small: $usn_max_size bytes"
        VALIDATION_WARNINGS+=("\$UsnJrnl:\$Max size too small")
    fi
    
    log_success "\$UsnJrnl:\$Max exists (${usn_max_size} bytes)"
    VALIDATION_RESULTS["usn_journal"]="true"
    VALIDATION_RESULTS["usn_max_size"]="$usn_max_size"
    return 0
}

validate_prefetch() {
    local vhdx_path="$1"
    
    log_info "Validating Prefetch files..."
    
    local prefetch_files
    prefetch_files=$(guestfish --ro -a "$vhdx_path" -i <<'EOF'
glob-expand /Windows/Prefetch/*.pf
EOF
)
    
    local prefetch_count
    prefetch_count=$(echo "$prefetch_files" | wc -l)
    
    if [[ "$prefetch_count" -lt "$MIN_PREFETCH_FILES" ]]; then
        log_warning "Insufficient Prefetch files: $prefetch_count (minimum $MIN_PREFETCH_FILES)"
        VALIDATION_WARNINGS+=("Insufficient Prefetch files: $prefetch_count")
    else
        log_success "Prefetch files found: $prefetch_count"
    fi
    
    VALIDATION_RESULTS["prefetch_count"]="$prefetch_count"
    return 0
}

validate_event_logs() {
    local vhdx_path="$1"
    
    log_info "Validating event logs..."
    
    local evtx_files=(
        "Windows/System32/winevt/Logs/System.evtx"
        "Windows/System32/winevt/Logs/Application.evtx"
        "Windows/System32/winevt/Logs/Security.evtx"
    )
    
    local evtx_valid=0
    
    for evtx in "${evtx_files[@]}"; do
        local evtx_size
        evtx_size=$(guestfish --ro -a "$vhdx_path" -i <<EOF
filesize /$evtx
EOF
)
        
        if [[ "$evtx_size" -lt "$MIN_EVTX_SIZE" ]]; then
            log_warning "Event log too small: $evtx ($evtx_size bytes)"
            VALIDATION_WARNINGS+=("Event log too small: $evtx")
        else
            log_success "Event log valid: $evtx ($evtx_size bytes)"
            evtx_valid=$((evtx_valid + 1))
        fi
        
        VALIDATION_RESULTS["evtx_$(basename "$evtx" .evtx)_size"]="$evtx_size"
    done
    
    if [[ "$evtx_valid" -eq 0 ]]; then
        log_error "No valid event logs found"
        VALIDATION_ERRORS+=("No valid event logs found")
        return 4
    fi
    
    VALIDATION_RESULTS["evtx_valid_count"]="$evtx_valid"
    return 0
}

validate_registry_hives() {
    local vhdx_path="$1"
    
    log_info "Validating registry hives..."
    
    local hives_valid=0
    
    for hive in "${REQUIRED_HIVES[@]}"; do
        # Check hive exists
        local hive_exists
        hive_exists=$(guestfish --ro -a "$vhdx_path" -i <<EOF
is-file /$hive
EOF
)
        
        if [[ "$hive_exists" != "true" ]]; then
            log_error "Registry hive not found: $hive"
            VALIDATION_ERRORS+=("Registry hive not found: $hive")
            continue
        fi
        
        # Check for .LOG files
        local log1_exists
        log1_exists=$(guestfish --ro -a "$vhdx_path" -i <<EOF
is-file /${hive}.LOG1
EOF
)
        
        local log2_exists
        log2_exists=$(guestfish --ro -a "$vhdx_path" -i <<EOF
is-file /${hive}.LOG2
EOF
)
        
        if [[ "$log1_exists" == "true" ]] || [[ "$log2_exists" == "true" ]]; then
            log_success "Registry hive valid: $hive (with .LOG files)"
            hives_valid=$((hives_valid + 1))
        else
            log_warning "Registry hive missing .LOG files: $hive"
            VALIDATION_WARNINGS+=("Registry hive missing .LOG files: $hive")
        fi
    done
    
    if [[ "$hives_valid" -eq 0 ]]; then
        log_error "No valid registry hives found"
        VALIDATION_ERRORS+=("No valid registry hives found")
        return 4
    fi
    
    VALIDATION_RESULTS["hives_valid_count"]="$hives_valid"
    return 0
}

# ============================================================================
# Report Generation
# ============================================================================

generate_report() {
    local vhdx_path="$1"
    local exit_code="$2"
    
    log_info "Generating validation report..."
    
    # Build JSON report
    cat > "$REPORT_FILE" <<EOF
{
  "vhdx_path": "$vhdx_path",
  "validation_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "exit_code": $exit_code,
  "status": "$([ "$exit_code" -eq 0 ] && echo "PASSED" || echo "FAILED")",
  "results": {
EOF
    
    # Add validation results
    local first=true
    for key in "${!VALIDATION_RESULTS[@]}"; do
        if [[ "$first" == true ]]; then
            first=false
        else
            echo "," >> "$REPORT_FILE"
        fi
        echo -n "    \"$key\": \"${VALIDATION_RESULTS[$key]}\"" >> "$REPORT_FILE"
    done
    
    cat >> "$REPORT_FILE" <<EOF

  },
  "errors": [
EOF
    
    # Add errors
    first=true
    for error in "${VALIDATION_ERRORS[@]}"; do
        if [[ "$first" == true ]]; then
            first=false
        else
            echo "," >> "$REPORT_FILE"
        fi
        echo -n "    \"$error\"" >> "$REPORT_FILE"
    done
    
    cat >> "$REPORT_FILE" <<EOF

  ],
  "warnings": [
EOF
    
    # Add warnings
    first=true
    for warning in "${VALIDATION_WARNINGS[@]}"; do
        if [[ "$first" == true ]]; then
            first=false
        else
            echo "," >> "$REPORT_FILE"
        fi
        echo -n "    \"$warning\"" >> "$REPORT_FILE"
    done
    
    cat >> "$REPORT_FILE" <<EOF

  ]
}
EOF
    
    log_success "Report generated: $REPORT_FILE"
}

# ============================================================================
# Main Function
# ============================================================================

main() {
    log_info "=== ARC Baseline VHDX Validator ==="
    
    # Validate arguments
    if [[ $# -ne 1 ]]; then
        log_error "Invalid number of arguments"
        usage
    fi
    
    local vhdx_path="$1"
    
    # Check dependencies
    if ! check_dependencies; then
        exit 2
    fi
    
    # Run validations
    local validation_failed=false
    
    validate_vhdx_exists "$vhdx_path" || validation_failed=true
    validate_filesystem "$vhdx_path" || validation_failed=true
    validate_usn_journal "$vhdx_path" || validation_failed=true
    validate_prefetch "$vhdx_path" || validation_failed=true
    validate_event_logs "$vhdx_path" || validation_failed=true
    validate_registry_hives "$vhdx_path" || validation_failed=true
    
    # Summary
    echo ""
    log_info "=== Validation Summary ==="
    
    if [[ "$validation_failed" == true ]]; then
        log_error "Validation FAILED"
        log_error "Errors: ${#VALIDATION_ERRORS[@]}"
        for error in "${VALIDATION_ERRORS[@]}"; do
            log_error "  - $error"
        done
    else
        log_success "Validation PASSED"
    fi
    
    if [[ ${#VALIDATION_WARNINGS[@]} -gt 0 ]]; then
        log_warning "Warnings: ${#VALIDATION_WARNINGS[@]}"
        for warning in "${VALIDATION_WARNINGS[@]}"; do
            log_warning "  - $warning"
        done
    fi
    
    # Generate report
    local exit_code=0
    if [[ "$validation_failed" == true ]]; then
        exit_code=4
    fi
    
    generate_report "$vhdx_path" "$exit_code"
    
    log_info "Report: $REPORT_FILE"
    
    return "$exit_code"
}

# ============================================================================
# Entry Point
# ============================================================================

# Trap errors
trap 'log_error "Script failed at line $LINENO"' ERR

# Run main
main "$@"
