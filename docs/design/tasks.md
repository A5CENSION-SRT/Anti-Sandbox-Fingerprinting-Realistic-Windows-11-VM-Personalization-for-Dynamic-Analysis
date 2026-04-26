# Implementation Tasks: Linux Host Support for ARC

## Task Overview

This document breaks down the Linux host support implementation into discrete, testable tasks. Tasks are organized by phase and include acceptance criteria, dependencies, and estimated effort.

**Total Estimated Effort**: 15-20 days  
**Priority**: Critical  
**Target Completion**: Q1 2024

---

## Phase 1: Core Infrastructure (5-7 days)

### Task 1.1: Implement LinuxMountBackend

**Description**: Create `core/linux_mount.py` with libguestfs and hivex integration.

**Subtasks**:
- [ ] 1.1.1: Implement `__init__()` and basic lifecycle (mount/unmount)
- [ ] 1.1.2: Implement file I/O operations (read_bytes, write_bytes, mkdir_p, ls, rm, exists)
- [ ] 1.1.3: Implement timestamp operations (utimens)
- [ ] 1.1.4: Implement NTFS attribute setting (set_ntfs_attributes via xattr)
- [ ] 1.1.5: Implement HivexHandle context manager
- [ ] 1.1.6: Implement open_hive() with .LOG cleanup
- [ ] 1.1.7: Implement FUSE mount/unmount (host_fuse_mount, host_fuse_unmount)
- [ ] 1.1.8: Add comprehensive error handling and logging

**Acceptance Criteria**:
- All methods have type hints and docstrings
- Unit tests achieve >90% coverage
- Can mount/unmount VHDX without errors
- Can read/write files via libguestfs
- Can edit registry hives via hivex
- .LOG1/.LOG2 deleted after hive commit
- FUSE mount/unmount works correctly

**Dependencies**: None

**Estimated Effort**: 3 days

---

### Task 1.2: Update MountManager for Backend Delegation

**Description**: Modify `core/mount_manager.py` to delegate to LinuxMountBackend.

**Subtasks**:
- [ ] 1.2.1: Add `backend` parameter to `__init__()`
- [ ] 1.2.2: Update `root` property to use backend mountpoint when available
- [ ] 1.2.3: Update `set_ntfs_attributes()` to delegate to backend
- [ ] 1.2.4: Update `utimens()` to delegate to backend
- [ ] 1.2.5: Remove Windows-specific path handling
- [ ] 1.2.6: Add unit tests for both standalone and backend modes

**Acceptance Criteria**:
- MountManager works in standalone mode (no backend)
- MountManager works in backend mode (with LinuxMountBackend)
- All NTFS operations delegate correctly
- Path resolution handles both Windows and POSIX paths
- Unit tests achieve >85% coverage

**Dependencies**: Task 1.1

**Estimated Effort**: 1 day

---

### Task 1.3: Update Orchestrator for Two-Phase Execution

**Description**: Modify `core/orchestrator.py` to support two-phase mount strategy.

**Subtasks**:
- [ ] 1.3.1: Add `--vhdx-path` CLI argument handling
- [ ] 1.3.2: Create LinuxMountBackend instance when VHDX path provided
- [ ] 1.3.3: Implement Phase A execution (all services except NTFS)
- [ ] 1.3.4: Implement libguestfs unmount between phases
- [ ] 1.3.5: Implement Phase B execution (NTFS services only)
- [ ] 1.3.6: Add cleanup in finally blocks
- [ ] 1.3.7: Update service ordering to separate NTFS services

**Acceptance Criteria**:
- Orchestrator creates backend when --vhdx-path provided
- Phase A completes before Phase B starts
- libguestfs unmounted before FUSE mount
- FUSE unmounted after Phase B
- Cleanup happens in all exit paths (success, error, interrupt)
- Integration test validates two-phase execution

**Dependencies**: Tasks 1.1, 1.2

**Estimated Effort**: 2 days

---

### Task 1.4: Remove Windows Dependencies

**Description**: Remove all Windows-specific code and dependencies.

**Subtasks**:
- [ ] 1.4.1: Remove `pywin32` from requirements.txt
- [ ] 1.4.2: Delete `core/vm_manager.py`
- [ ] 1.4.3: Delete `build_vm_image.py`
- [ ] 1.4.4: Delete `mount_existing_vhd.py`
- [ ] 1.4.5: Delete Windows .bat files
- [ ] 1.4.6: Update imports throughout codebase
- [ ] 1.4.7: Update documentation to remove Windows references

**Acceptance Criteria**:
- No pywin32 imports remain
- No Windows-specific files remain
- All imports resolve correctly
- Documentation updated
- CI/CD pipeline runs on Linux only

**Dependencies**: None (can run in parallel)

**Estimated Effort**: 1 day

---

## Phase 2: Service Layer Updates (3-4 days)

### Task 2.1: Update cross_writer.py to Remove pywin32

**Description**: Replace pywin32 calls with MountManager delegation in `services/filesystem/cross_writer.py`.

**Subtasks**:
- [ ] 2.1.1: Remove `win32api`, `win32con`, `pywintypes` imports
- [ ] 2.1.2: Replace `win32file.SetFileAttributes()` with `mount.set_ntfs_attributes()`
- [ ] 2.1.3: Replace `win32file.SetFileTime()` with `mount.utimens()`
- [ ] 2.1.4: Update error handling for new API
- [ ] 2.1.5: Add unit tests with mocked MountManager

**Acceptance Criteria**:
- No pywin32 imports
- All file attribute operations work via MountManager
- All timestamp operations work via MountManager
- Unit tests achieve >80% coverage
- Integration test validates file creation with attributes

**Dependencies**: Task 1.2

**Estimated Effort**: 1 day

---

### Task 2.2: Update hive_writer.py to Use Hivex

**Description**: Replace hand-rolled binary hive writes with hivex API in `services/registry/hive_writer.py`.

**Subtasks**:
- [ ] 2.2.1: Preserve `HiveOperation` abstraction for compatibility
- [ ] 2.2.2: Implement `_execute_operations()` using `backend.open_hive()`
- [ ] 2.2.3: Map HiveOperation types to hivex API calls
- [ ] 2.2.4: Handle all registry value types (REG_SZ, REG_DWORD, etc.)
- [ ] 2.2.5: Add UTF-16LE encoding for string values
- [ ] 2.2.6: Add unit tests with mocked backend

**Acceptance Criteria**:
- HiveOperation API unchanged (services unaffected)
- All value types supported
- Hive writes use hivex exclusively
- .LOG cleanup happens automatically
- Unit tests achieve >85% coverage
- Integration test validates registry writes

**Dependencies**: Task 1.1

**Estimated Effort**: 2 days

---

## Phase 3: NTFS Services (3-4 days)

### Task 3.1: Implement MftTimestampPatcher

**Description**: Create `services/ntfs/mft_timestamp_patcher.py` for $STANDARD_INFORMATION timestamp patching.

**Subtasks**:
- [ ] 3.1.1: Create service class inheriting from BaseService
- [ ] 3.1.2: Implement `apply()` method consuming scheduler events
- [ ] 3.1.3: Convert datetime to Windows FILETIME format
- [ ] 3.1.4: Pack 4 timestamps (atime, mtime, ctime, crtime) to binary
- [ ] 3.1.5: Use `setfattr system.ntfs_times` via FUSE mount
- [ ] 3.1.6: Handle files that don't exist gracefully
- [ ] 3.1.7: Add unit tests with mocked FUSE mount

**Acceptance Criteria**:
- Service registered in orchestrator
- Consumes FILE_CREATE and FILE_MODIFY events
- Sets all 4 NTFS timestamps correctly
- Handles missing files without crashing
- Unit tests achieve >80% coverage
- Integration test validates timestamps via forensic tools

**Dependencies**: Task 1.3

**Estimated Effort**: 1.5 days

---

### Task 3.2: Implement UsnJournalWriter

**Description**: Create `services/ntfs/usn_journal_writer.py` for $UsnJrnl:$J record appending.

**Subtasks**:
- [ ] 3.2.1: Create service class inheriting from BaseService
- [ ] 3.2.2: Implement USN_RECORD_V3 dataclass with to_bytes()
- [ ] 3.2.3: Read $UsnJrnl:$Max header to get current NextUsn
- [ ] 3.2.4: Construct USN records from scheduler events
- [ ] 3.2.5: Append records to $UsnJrnl:$J via FUSE mount
- [ ] 3.2.6: Update $UsnJrnl:$Max header with new NextUsn
- [ ] 3.2.7: Handle all USN reason flags correctly
- [ ] 3.2.8: Add unit tests with mocked FUSE mount

**Acceptance Criteria**:
- Service registered in orchestrator
- Consumes all file operation events
- USN records have correct structure
- $Max header updated correctly
- Reason flags match event types
- Unit tests achieve >80% coverage
- Integration test validates journal via forensic tools

**Dependencies**: Task 1.3

**Estimated Effort**: 2 days

---

### Task 3.3: Implement LogfileWriter (Optional)

**Description**: Create `services/ntfs/logfile_writer.py` for $LogFile stub generation.

**Subtasks**:
- [ ] 3.3.1: Create service class inheriting from BaseService
- [ ] 3.3.2: Implement best-effort $LogFile stub
- [ ] 3.3.3: Add unit tests

**Acceptance Criteria**:
- Service registered in orchestrator
- $LogFile stub created
- Unit tests achieve >70% coverage

**Dependencies**: Task 1.3

**Estimated Effort**: 0.5 days (optional)

---

## Phase 4: Baseline VHDX Automation (2-3 days)

### Task 4.1: Create Baseline Build Script

**Description**: Create `scripts/build_baseline_vhdx.sh` for automated VHDX creation.

**Subtasks**:
- [ ] 4.1.1: Write bash script using virt-install
- [ ] 4.1.2: Add parameter validation (ISO, unattend, output, size)
- [ ] 4.1.3: Implement VHDX creation via qemu-img
- [ ] 4.1.4: Implement Windows installation via virt-install
- [ ] 4.1.5: Add cleanup (virsh undefine)
- [ ] 4.1.6: Add error handling and logging

**Acceptance Criteria**:
- Script accepts 4 parameters (ISO, unattend, output, size)
- Creates valid VHDX file
- Installs Windows 11 unattended
- Completes OOBE automatically
- Cleans up VM definition
- Returns 0 on success, non-zero on failure

**Dependencies**: None

**Estimated Effort**: 1 day

---

### Task 4.2: Create Unattend.xml Template

**Description**: Create `examples/unattend.xml` for silent Windows installation.

**Subtasks**:
- [ ] 4.2.1: Create WindowsPE pass for disk partitioning
- [ ] 4.2.2: Create specialize pass for computer name
- [ ] 4.2.3: Create oobeSystem pass to skip OOBE
- [ ] 4.2.4: Add FirstLogonCommands with 180-second wait
- [ ] 4.2.5: Add shutdown command
- [ ] 4.2.6: Test with Windows 11 ISO

**Acceptance Criteria**:
- Windows installs without user interaction
- OOBE skipped automatically
- System waits 180 seconds for initialization
- System shuts down cleanly
- $UsnJrnl, Prefetch, EVTX initialized

**Dependencies**: None

**Estimated Effort**: 1 day

---

### Task 4.3: Create Baseline Validation Script

**Description**: Create script to validate baseline VHDX quality.

**Subtasks**:
- [ ] 4.3.1: Check for $UsnJrnl:$Max header
- [ ] 4.3.2: Check for Prefetch files
- [ ] 4.3.3: Check for non-empty EVTX files
- [ ] 4.3.4: Check for registry hives with .LOG files
- [ ] 4.3.5: Generate validation report

**Acceptance Criteria**:
- Script validates all critical structures
- Returns 0 if valid, non-zero if invalid
- Provides clear error messages for failures
- Generates JSON report

**Dependencies**: Task 4.1

**Estimated Effort**: 1 day

---

## Phase 5: Testing (3-4 days)

### Task 5.1: Write Unit Tests

**Description**: Achieve >80% unit test coverage for new code.

**Subtasks**:
- [ ] 5.1.1: Write tests for LinuxMountBackend (test_linux_mount.py)
- [ ] 5.1.2: Write tests for MountManager updates (test_mount_manager.py)
- [ ] 5.1.3: Write tests for Orchestrator updates (test_orchestrator.py)
- [ ] 5.1.4: Write tests for cross_writer updates (test_cross_writer.py)
- [ ] 5.1.5: Write tests for hive_writer updates (test_hive_writer.py)
- [ ] 5.1.6: Write tests for NTFS services (test_ntfs_services.py)
- [ ] 5.1.7: Run coverage report and fill gaps

**Acceptance Criteria**:
- Unit test coverage >80% for all new code
- All tests pass on Ubuntu 24.04
- Tests use mocks appropriately
- Tests are deterministic and fast (<5 seconds total)

**Dependencies**: Tasks 1.1-3.2

**Estimated Effort**: 2 days

---

### Task 5.2: Write Integration Tests

**Description**: Create end-to-end integration tests.

**Subtasks**:
- [ ] 5.2.1: Create test VHDX fixture (minimal Windows 11)
- [ ] 5.2.2: Write test for full pipeline (test_linux_workflow.py)
- [ ] 5.2.3: Write test for Phase A/B transition
- [ ] 5.2.4: Write test for registry hive writes
- [ ] 5.2.5: Write test for NTFS metadata writes
- [ ] 5.2.6: Write test for error recovery

**Acceptance Criteria**:
- Integration tests cover end-to-end workflows
- Tests use real VHDX (not mocked)
- Tests validate output with forensic tools
- Tests clean up resources properly
- Tests pass on Ubuntu 24.04

**Dependencies**: Tasks 1.1-3.2

**Estimated Effort**: 2 days

---

## Phase 6: Documentation (2-3 days)

### Task 6.1: Update README

**Description**: Update main README.md for Linux host support.

**Subtasks**:
- [ ] 6.1.1: Update system requirements section
- [ ] 6.1.2: Update installation instructions
- [ ] 6.1.3: Update quick-start guide
- [ ] 6.1.4: Add baseline VHDX creation section
- [ ] 6.1.5: Update CLI examples
- [ ] 6.1.6: Add troubleshooting section

**Acceptance Criteria**:
- README accurate for Linux host
- All commands tested and working
- Screenshots updated (if applicable)
- Links to detailed docs added

**Dependencies**: All implementation tasks

**Estimated Effort**: 1 day

---

### Task 6.2: Create API Documentation

**Description**: Generate API documentation from docstrings.

**Subtasks**:
- [ ] 6.2.1: Ensure all public APIs have docstrings
- [ ] 6.2.2: Generate Sphinx documentation
- [ ] 6.2.3: Add usage examples
- [ ] 6.2.4: Publish to GitHub Pages or ReadTheDocs

**Acceptance Criteria**:
- All public APIs documented
- Documentation builds without errors
- Examples are runnable
- Documentation published online

**Dependencies**: All implementation tasks

**Estimated Effort**: 1 day

---

### Task 6.3: Create Troubleshooting Guide

**Description**: Document common issues and solutions.

**Subtasks**:
- [ ] 6.3.1: Document mount failures and recovery
- [ ] 6.3.2: Document hive write failures
- [ ] 6.3.3: Document FUSE mount issues
- [ ] 6.3.4: Document version incompatibilities
- [ ] 6.3.5: Add diagnostic commands

**Acceptance Criteria**:
- All common issues documented
- Solutions tested and verified
- Diagnostic commands provided
- FAQ section added

**Dependencies**: All implementation tasks

**Estimated Effort**: 1 day

---

## Phase 7: Deployment (1-2 days)

### Task 7.1: Create Docker Container

**Description**: Create Dockerfile for portable deployment.

**Subtasks**:
- [ ] 7.1.1: Write Dockerfile based on Ubuntu 24.04
- [ ] 7.1.2: Install system dependencies
- [ ] 7.1.3: Install Python dependencies
- [ ] 7.1.4: Copy ARC code
- [ ] 7.1.5: Set entrypoint
- [ ] 7.1.6: Test container build and run

**Acceptance Criteria**:
- Docker image builds successfully
- Container runs ARC without errors
- FUSE operations work in container
- Image size <2 GB
- Published to Docker Hub

**Dependencies**: All implementation tasks

**Estimated Effort**: 1 day

---

### Task 7.2: Update CI/CD Pipeline

**Description**: Update GitHub Actions for Linux-only CI.

**Subtasks**:
- [ ] 7.2.1: Remove Windows runners
- [ ] 7.2.2: Add Ubuntu 24.04 runner
- [ ] 7.2.3: Install system dependencies in CI
- [ ] 7.2.4: Run unit tests
- [ ] 7.2.5: Run integration tests (if feasible)
- [ ] 7.2.6: Generate coverage report
- [ ] 7.2.7: Build Docker image

**Acceptance Criteria**:
- CI runs on Ubuntu 24.04
- All tests pass in CI
- Coverage report generated
- Docker image built and pushed
- CI completes in <10 minutes

**Dependencies**: Tasks 5.1, 5.2, 7.1

**Estimated Effort**: 1 day

---

## Phase 8: Validation (2-3 days)

### Task 8.1: Forensic Tool Validation

**Description**: Validate artifacts with forensic tools.

**Subtasks**:
- [ ] 8.1.1: Run MFTECmd on modified VHDX
- [ ] 8.1.2: Run PECmd on Prefetch files
- [ ] 8.1.3: Run RECmd on registry hives
- [ ] 8.1.4: Run EvtxECmd on event logs
- [ ] 8.1.5: Analyze results and fix issues
- [ ] 8.1.6: Document validation process

**Acceptance Criteria**:
- MFTECmd validates $MFT timestamps
- PECmd validates Prefetch files
- RECmd validates registry keys
- EvtxECmd validates event logs
- >95% of artifacts pass validation

**Dependencies**: All implementation tasks

**Estimated Effort**: 1.5 days

---

### Task 8.2: VM Detection Testing

**Description**: Test VM detection evasion.

**Subtasks**:
- [ ] 8.2.1: Boot modified VHDX in QEMU/KVM
- [ ] 8.2.2: Run pafish.exe
- [ ] 8.2.3: Run Al-Khaser.exe
- [ ] 8.2.4: Run InviZzzible
- [ ] 8.2.5: Analyze detection results
- [ ] 8.2.6: Fix detected VM indicators

**Acceptance Criteria**:
- VHDX boots successfully
- <10% detection rate by pafish
- <10% detection rate by Al-Khaser
- <10% detection rate by InviZzzible
- No obvious VM strings in registry

**Dependencies**: All implementation tasks

**Estimated Effort**: 1.5 days

---

## Task Dependencies Graph

```
Phase 1: Core Infrastructure
├── 1.1 LinuxMountBackend (3d)
├── 1.2 MountManager (1d) [depends on 1.1]
├── 1.3 Orchestrator (2d) [depends on 1.1, 1.2]
└── 1.4 Remove Windows (1d) [parallel]

Phase 2: Service Layer
├── 2.1 cross_writer (1d) [depends on 1.2]
└── 2.2 hive_writer (2d) [depends on 1.1]

Phase 3: NTFS Services
├── 3.1 MftTimestampPatcher (1.5d) [depends on 1.3]
├── 3.2 UsnJournalWriter (2d) [depends on 1.3]
└── 3.3 LogfileWriter (0.5d) [depends on 1.3, optional]

Phase 4: Baseline Automation
├── 4.1 Build Script (1d) [parallel]
├── 4.2 Unattend.xml (1d) [parallel]
└── 4.3 Validation Script (1d) [depends on 4.1]

Phase 5: Testing
├── 5.1 Unit Tests (2d) [depends on 1.1-3.2]
└── 5.2 Integration Tests (2d) [depends on 1.1-3.2]

Phase 6: Documentation
├── 6.1 README (1d) [depends on all]
├── 6.2 API Docs (1d) [depends on all]
└── 6.3 Troubleshooting (1d) [depends on all]

Phase 7: Deployment
├── 7.1 Docker (1d) [depends on all]
└── 7.2 CI/CD (1d) [depends on 5.1, 5.2, 7.1]

Phase 8: Validation
├── 8.1 Forensic Tools (1.5d) [depends on all]
└── 8.2 VM Detection (1.5d) [depends on all]
```

## Critical Path

The critical path (longest dependency chain) is:

1. Task 1.1: LinuxMountBackend (3 days)
2. Task 1.2: MountManager (1 day)
3. Task 1.3: Orchestrator (2 days)
4. Task 3.2: UsnJournalWriter (2 days)
5. Task 5.2: Integration Tests (2 days)
6. Task 8.1: Forensic Validation (1.5 days)

**Total Critical Path**: 11.5 days

With parallel work on non-critical tasks, total project duration: **15-20 days**

## Risk Mitigation Tasks

### RT-1: Early libguestfs Validation
**Task**: Validate libguestfs works on target systems  
**Effort**: 0.5 days  
**Schedule**: Before Task 1.1

### RT-2: Hivex API Exploration
**Task**: Prototype hivex usage for all value types  
**Effort**: 0.5 days  
**Schedule**: Before Task 2.2

### RT-3: FUSE Permission Testing
**Task**: Test FUSE mount with various permission configurations  
**Effort**: 0.5 days  
**Schedule**: Before Task 3.1

## Progress Tracking

| Phase | Tasks | Completed | In Progress | Not Started | % Complete |
|-------|-------|-----------|-------------|-------------|------------|
| 1     | 4     | 0         | 0           | 4           | 0%         |
| 2     | 2     | 0         | 0           | 2           | 0%         |
| 3     | 3     | 0         | 0           | 3           | 0%         |
| 4     | 3     | 0         | 0           | 3           | 0%         |
| 5     | 2     | 0         | 0           | 2           | 0%         |
| 6     | 3     | 0         | 0           | 3           | 0%         |
| 7     | 2     | 0         | 0           | 2           | 0%         |
| 8     | 2     | 0         | 0           | 2           | 0%         |
| **Total** | **21** | **0** | **0** | **21** | **0%** |

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-15  
**Status**: Ready for Implementation  
**Approved By**: [Pending]
