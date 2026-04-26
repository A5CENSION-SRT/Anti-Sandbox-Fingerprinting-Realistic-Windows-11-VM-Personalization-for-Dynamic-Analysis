# Requirements Document: Linux Host Support for ARC

## 1. Functional Requirements

### FR-1: VHDX Mount and Access
**Priority**: Critical  
**Description**: The system shall provide read/write access to Windows 11 VHDX/VHD images from a Linux host.

**Acceptance Criteria**:
- FR-1.1: System shall mount VHDX images using libguestfs
- FR-1.2: System shall support both VHDX and VHD formats
- FR-1.3: System shall auto-detect Windows partition via inspection API
- FR-1.4: System shall handle multi-partition layouts (EFI, MSR, Windows, Recovery)
- FR-1.5: System shall gracefully handle mount failures with clear error messages

### FR-2: Registry Hive Manipulation
**Priority**: Critical  
**Description**: The system shall read and write Windows registry hives offline using hivex.

**Acceptance Criteria**:
- FR-2.1: System shall open registry hives (SOFTWARE, SYSTEM, SAM, SECURITY, DEFAULT, NTUSER.DAT) for editing
- FR-2.2: System shall support all registry value types (REG_SZ, REG_DWORD, REG_QWORD, REG_BINARY, REG_MULTI_SZ)
- FR-2.3: System shall commit hive changes atomically
- FR-2.4: System shall delete .LOG1 and .LOG2 files after every hive commit (ADR-010)
- FR-2.5: System shall perform pre-flight checks on hive log files before write operations
- FR-2.6: System shall handle hive write failures gracefully without aborting entire run

### FR-3: Filesystem Operations
**Priority**: Critical  
**Description**: The system shall create, modify, and delete files within the mounted VHDX.

**Acceptance Criteria**:
- FR-3.1: System shall write binary files (documents, images, executables)
- FR-3.2: System shall write text files with specified encoding
- FR-3.3: System shall create directory structures recursively
- FR-3.4: System shall set basic timestamps (atime, mtime) via libguestfs
- FR-3.5: System shall list directory contents
- FR-3.6: System shall check file existence
- FR-3.7: System shall delete files

### FR-4: NTFS Metadata Manipulation
**Priority**: High  
**Description**: The system shall set NTFS-specific metadata including $STANDARD_INFORMATION timestamps and file attributes.

**Acceptance Criteria**:
- FR-4.1: System shall mount VHDX via ntfs-3g FUSE for metadata access
- FR-4.2: System shall set $STANDARD_INFORMATION timestamps (ctime, atime, mtime, crtime) via setfattr
- FR-4.3: System shall set NTFS file attributes (hidden, system, archive) via xattr
- FR-4.4: System shall leave $FILE_NAME timestamps at create-time (SI/FN divergence per ADR-009)
- FR-4.5: System shall handle FUSE mount/unmount lifecycle correctly

### FR-5: USN Journal Manipulation
**Priority**: High  
**Description**: The system shall append records to the NTFS Update Sequence Number Journal ($UsnJrnl:$J).

**Acceptance Criteria**:
- FR-5.1: System shall read $UsnJrnl:$Max header to get current NextUsn
- FR-5.2: System shall construct valid USN_RECORD_V3 structures
- FR-5.3: System shall append records to $UsnJrnl:$J via FUSE mount
- FR-5.4: System shall update $UsnJrnl:$Max header with new NextUsn
- FR-5.5: System shall support all USN reason flags (FILE_CREATE, DATA_EXTEND, FILE_DELETE, etc.)
- FR-5.6: System shall validate baseline VHDX has initialized journal before attempting writes

### FR-6: Two-Phase Mount Strategy
**Priority**: Critical  
**Description**: The system shall execute operations in two sequential phases to avoid mount conflicts.

**Acceptance Criteria**:
- FR-6.1: Phase A shall use libguestfs for registry and bulk filesystem operations
- FR-6.2: System shall unmount libguestfs before Phase B
- FR-6.3: Phase B shall use ntfs-3g FUSE for NTFS metadata operations
- FR-6.4: System shall unmount FUSE after Phase B completes
- FR-6.5: System shall never have both mounts active simultaneously

### FR-7: Service Layer Compatibility
**Priority**: Critical  
**Description**: The system shall maintain API compatibility with existing service layer.

**Acceptance Criteria**:
- FR-7.1: Services shall continue using MountManager API without changes
- FR-7.2: MountManager shall transparently delegate to LinuxMountBackend
- FR-7.3: All existing services shall work without modification (except pywin32 removal)
- FR-7.4: ServiceContext dataclass shall remain unchanged

### FR-8: Baseline VHDX Validation
**Priority**: High  
**Description**: The system shall validate that baseline VHDX has completed Windows OOBE before processing.

**Acceptance Criteria**:
- FR-8.1: System shall check for presence of $UsnJrnl:$Max header
- FR-8.2: System shall check for initialized Prefetch directory
- FR-8.3: System shall check for non-empty EVTX files
- FR-8.4: System shall abort with clear error if validation fails
- FR-8.5: System shall provide guidance on creating valid baseline VHDX

### FR-9: CLI Interface
**Priority**: High  
**Description**: The system shall provide command-line interface for Linux host operation.

**Acceptance Criteria**:
- FR-9.1: System shall accept --vhdx-path argument for VHDX file location
- FR-9.2: System shall support --profile argument for persona selection
- FR-9.3: System shall support --timeline-days argument (default 360)
- FR-9.4: System shall support --categories argument for selective service execution
- FR-9.5: System shall support --verbose flag for debug logging
- FR-9.6: System shall remove Windows-specific arguments (--vm-name)

### FR-10: Error Handling and Recovery
**Priority**: High  
**Description**: The system shall handle errors gracefully and provide recovery guidance.

**Acceptance Criteria**:
- FR-10.1: System shall detect stale mounts and provide cleanup commands
- FR-10.2: System shall handle VHDX access conflicts with clear error messages
- FR-10.3: System shall validate ntfs-3g version and report if outdated
- FR-10.4: System shall clean up temporary files in all exit paths
- FR-10.5: System shall unmount VHDX in finally blocks to prevent leaks

## 2. Non-Functional Requirements

### NFR-1: Performance
**Priority**: Medium  
**Description**: The system shall complete artifact generation within acceptable time limits.

**Acceptance Criteria**:
- NFR-1.1: libguestfs appliance launch shall complete within 15 seconds
- NFR-1.2: Phase A (registry + filesystem) shall complete within 60 seconds for 360-day timeline
- NFR-1.3: Phase B (NTFS metadata) shall complete within 20 seconds for 360-day timeline
- NFR-1.4: Total execution time shall not exceed 2 minutes for full artifact set

### NFR-2: Reliability
**Priority**: High  
**Description**: The system shall operate reliably without data corruption.

**Acceptance Criteria**:
- NFR-2.1: System shall never leave VHDX in inconsistent state
- NFR-2.2: System shall use context managers for automatic cleanup
- NFR-2.3: System shall validate all writes before commit
- NFR-2.4: Modified VHDX shall boot successfully in QEMU/KVM
- NFR-2.5: System shall maintain 99% success rate across diverse VHDX images

### NFR-3: Security
**Priority**: High  
**Description**: The system shall handle sensitive data securely.

**Acceptance Criteria**:
- NFR-3.1: Temporary hive files shall have restrictive permissions (0600)
- NFR-3.2: System shall never log sensitive data (SAM hashes, passwords)
- NFR-3.3: FUSE mounts shall use restrictive permissions
- NFR-3.4: System shall clean up all temporary files on exit
- NFR-3.5: System shall recommend VHDX snapshots before operation

### NFR-4: Maintainability
**Priority**: Medium  
**Description**: The system shall be maintainable and extensible.

**Acceptance Criteria**:
- NFR-4.1: Code shall follow PEP 8 style guidelines
- NFR-4.2: All public APIs shall have comprehensive docstrings
- NFR-4.3: Type hints shall be used throughout
- NFR-4.4: Unit test coverage shall exceed 80%
- NFR-4.5: Integration tests shall cover end-to-end workflows

### NFR-5: Portability
**Priority**: Medium  
**Description**: The system shall run on standard Linux distributions.

**Acceptance Criteria**:
- NFR-5.1: System shall run on Ubuntu 24.04 LTS
- NFR-5.2: System shall run on Debian 12+
- NFR-5.3: System shall run on Fedora 39+
- NFR-5.4: System shall provide Docker container for portability
- NFR-5.5: System shall document all system dependencies

### NFR-6: Usability
**Priority**: Medium  
**Description**: The system shall be easy to use and understand.

**Acceptance Criteria**:
- NFR-6.1: System shall provide clear progress indicators
- NFR-6.2: Error messages shall include actionable recovery steps
- NFR-6.3: Documentation shall include quick-start guide
- NFR-6.4: System shall provide example commands for common use cases
- NFR-6.5: Logs shall be human-readable and structured (JSON Lines)

## 3. System Requirements

### SR-1: Operating System
**Requirement**: Ubuntu 24.04 LTS or compatible Linux distribution  
**Justification**: libguestfs and hivex require Linux kernel features

### SR-2: System Packages
**Required Packages**:
- libguestfs-tools (≥ 1.48)
- python3-guestfs (≥ 1.48)
- libhivex-bin (≥ 1.3.21)
- python3-hivex (≥ 1.3.21)
- ntfs-3g (≥ 2017.3.23)
- fuse3 (≥ 3.10)
- guestmount (part of libguestfs-tools)
- qemu-system-x86 (≥ 6.2)
- libvirt-daemon-system (≥ 8.0)

### SR-3: Python Version
**Requirement**: Python 3.10 or higher  
**Justification**: Type hints and dataclass features

### SR-4: Python Packages
**Required Packages**:
- pydantic (≥ 2.0)
- pyyaml (≥ 6.0)
- python-evtx (≥ 0.7.4)
- python-docx (≥ 0.8.11)
- openpyxl (≥ 3.1.0)
- reportlab (≥ 4.0.0)

**Removed Packages**:
- pywin32 (Windows-only)

### SR-5: Disk Space
**Requirement**: Minimum 100 GB free space  
**Justification**: VHDX images (80 GB) + temporary files (20 GB)

### SR-6: Memory
**Requirement**: Minimum 4 GB RAM  
**Justification**: libguestfs appliance requires 2 GB, Python process requires 1-2 GB

### SR-7: Permissions
**Requirement**: User must be in `fuse` group  
**Justification**: FUSE mount operations require group membership

## 4. Constraints

### C-1: Platform Constraint
**Constraint**: Linux-only host support  
**Rationale**: libguestfs and hivex are Linux-specific  
**Impact**: Windows users must use WSL2 or Docker

### C-2: VHDX Format Constraint
**Constraint**: VHDX must be post-OOBE baseline  
**Rationale**: Windows initializes critical structures on first boot  
**Impact**: Users must create baseline VHDX before running ARC

### C-3: Sequential Mount Constraint
**Constraint**: libguestfs and FUSE cannot mount simultaneously  
**Rationale**: Both hold exclusive locks on VHDX  
**Impact**: Two-phase execution required

### C-4: NTFS Version Constraint
**Constraint**: ntfs-3g version ≥ 2017.3.23 required  
**Rationale**: Older versions lack system.ntfs_times xattr support  
**Impact**: Users on older distributions must upgrade

### C-5: Offline Operation Constraint
**Constraint**: VHDX must not be mounted by running VM  
**Rationale**: Concurrent access causes corruption  
**Impact**: VM must be shut down before ARC execution

## 5. Assumptions

### A-1: Baseline VHDX Quality
**Assumption**: Baseline VHDX is created using provided automation script  
**Risk**: Manual creation may miss critical initialization steps  
**Mitigation**: Provide validation script to check baseline quality

### A-2: System Package Availability
**Assumption**: All required system packages are available in distribution repos  
**Risk**: Older distributions may have outdated packages  
**Mitigation**: Document minimum distribution versions

### A-3: VHDX Integrity
**Assumption**: Input VHDX is not corrupted  
**Risk**: Corrupted VHDX may cause unpredictable failures  
**Mitigation**: Run chkdsk validation before ARC execution

### A-4: Sufficient Permissions
**Assumption**: User has necessary permissions for FUSE operations  
**Risk**: Permission errors may cause FUSE mount failures  
**Mitigation**: Check permissions during initialization

### A-5: Network Availability (Optional)
**Assumption**: Network available for AI profile generation  
**Risk**: Offline operation requires pre-generated profiles  
**Mitigation**: Support both AI-generated and static profiles

## 6. Dependencies

### D-1: libguestfs
**Type**: External System Library  
**Version**: ≥ 1.48  
**Purpose**: VHDX mount and filesystem access  
**Criticality**: Critical

### D-2: hivex
**Type**: External System Library  
**Version**: ≥ 1.3.21  
**Purpose**: Offline registry hive editing  
**Criticality**: Critical

### D-3: ntfs-3g
**Type**: External System Library  
**Version**: ≥ 2017.3.23  
**Purpose**: NTFS metadata manipulation via FUSE  
**Criticality**: High

### D-4: QEMU/KVM
**Type**: External System Software  
**Version**: ≥ 6.2  
**Purpose**: Baseline VHDX creation and testing  
**Criticality**: Medium (build-time only)

### D-5: Existing ARC Services
**Type**: Internal Dependency  
**Version**: Current codebase  
**Purpose**: Artifact generation logic  
**Criticality**: Critical

## 7. Success Metrics

### M-1: Functional Completeness
**Metric**: 100% of existing services work on Linux host  
**Target**: All 33 services execute successfully  
**Measurement**: Integration test suite pass rate

### M-2: Artifact Quality
**Metric**: Modified VHDX passes forensic validation  
**Target**: 95% of artifacts validated by forensic tools  
**Measurement**: MFTECmd, PECmd, RECmd, EvtxECmd validation

### M-3: VM Detection Evasion
**Metric**: VM detection tools fail to identify as VM  
**Target**: <10% detection rate by pafish/Al-Khaser  
**Measurement**: Detection tool test results

### M-4: Performance
**Metric**: Total execution time for 360-day timeline  
**Target**: <2 minutes  
**Measurement**: End-to-end execution time

### M-5: Reliability
**Metric**: Success rate across diverse VHDX images  
**Target**: ≥99% success rate  
**Measurement**: Automated test suite across 100+ VHDX variants

## 8. Out of Scope

### OS-1: Windows Host Support
**Rationale**: Maintaining dual platform support doubles maintenance burden  
**Alternative**: Users can use WSL2 on Windows

### OS-2: macOS Host Support
**Rationale**: libguestfs not well-supported on macOS  
**Alternative**: Users can use Docker or Linux VM

### OS-3: Real-time VM Modification
**Rationale**: Requires different architecture (agent-based)  
**Alternative**: Offline modification only

### OS-4: Automated VM Lifecycle Management
**Rationale**: Out of scope for artifact generation tool  
**Alternative**: Users manage VM lifecycle separately

### OS-5: Cloud-Native VHDX Storage
**Rationale**: Deferred to future enhancement  
**Alternative**: Users download VHDX locally before processing

## 9. Risks and Mitigations

### R-1: libguestfs Stability
**Risk**: libguestfs appliance crashes or hangs  
**Probability**: Low  
**Impact**: High  
**Mitigation**: Use LIBGUESTFS_BACKEND=direct, implement timeouts, provide debug logging

### R-2: VHDX Corruption
**Risk**: Incomplete writes leave VHDX in inconsistent state  
**Probability**: Medium  
**Impact**: Critical  
**Mitigation**: Use context managers, implement pre-flight checks, recommend snapshots

### R-3: Hive Log Rollback
**Risk**: Windows replays .LOG files over ARC writes  
**Probability**: High (if logs not deleted)  
**Impact**: Critical  
**Mitigation**: Mandatory .LOG cleanup after every commit (ADR-010)

### R-4: FUSE Mount Leaks
**Risk**: Stale FUSE mounts prevent subsequent runs  
**Probability**: Medium  
**Impact**: Medium  
**Mitigation**: Implement cleanup in finally blocks, provide diagnostic commands

### R-5: Version Incompatibility
**Risk**: Older ntfs-3g versions lack required features  
**Probability**: Medium  
**Impact**: High  
**Mitigation**: Version checks during initialization, clear error messages

### R-6: Performance Degradation
**Risk**: Execution time exceeds acceptable limits  
**Probability**: Low  
**Impact**: Medium  
**Mitigation**: Batch operations, use direct backend, profile and optimize

### R-7: Forensic Detection
**Risk**: Artifacts detectable as synthetic  
**Probability**: Medium  
**Impact**: High  
**Mitigation**: Implement temporal coherence checks, validate with forensic tools

## 10. Acceptance Criteria Summary

The Linux host support implementation shall be considered complete when:

1. ✅ All functional requirements (FR-1 through FR-10) are met
2. ✅ All non-functional requirements (NFR-1 through NFR-6) are met
3. ✅ Unit test coverage exceeds 80%
4. ✅ Integration tests pass for end-to-end workflows
5. ✅ Modified VHDX boots successfully in QEMU/KVM
6. ✅ Forensic tools validate 95%+ of artifacts
7. ✅ VM detection tools show <10% detection rate
8. ✅ Execution time <2 minutes for 360-day timeline
9. ✅ Documentation complete (README, API docs, troubleshooting guide)
10. ✅ Docker container builds and runs successfully

## 11. Traceability Matrix

| Requirement | Design Section | Test Coverage |
|-------------|----------------|---------------|
| FR-1 | LinuxMountBackend.mount() | test_mount_unmount_lifecycle() |
| FR-2 | LinuxMountBackend.open_hive() | test_hive_context_manager() |
| FR-3 | LinuxMountBackend file I/O | test_file_io_operations() |
| FR-4 | LinuxMountBackend.set_ntfs_attributes() | test_ntfs_attribute_setting() |
| FR-5 | services/ntfs/usn_journal_writer.py | test_usn_journal_append() |
| FR-6 | Orchestrator two-phase execution | test_phase_a_phase_b_transition() |
| FR-7 | MountManager delegation | test_backend_mode() |
| FR-8 | Baseline validation logic | test_baseline_validation() |
| FR-9 | main.py CLI parsing | test_cli_arguments() |
| FR-10 | Error handling throughout | test_error_recovery() |

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-15  
**Status**: Draft  
**Approved By**: [Pending]
