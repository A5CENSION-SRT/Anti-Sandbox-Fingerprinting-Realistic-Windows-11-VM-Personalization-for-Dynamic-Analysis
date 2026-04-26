#!/usr/bin/env python3
"""Basic import tests for ARC Linux host support.

This test verifies that all core modules can be imported without errors.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_core_imports():
    """Test that core modules can be imported."""
    print("Testing core module imports...")
    
    try:
        from core import linux_mount
        print("✓ core.linux_mount imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import core.linux_mount: {e}")
        return False
    
    try:
        from core import mount_manager
        print("✓ core.mount_manager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import core.mount_manager: {e}")
        return False
    
    try:
        from core import orchestrator
        print("✓ core.orchestrator imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import core.orchestrator: {e}")
        return False
    
    return True


def test_ntfs_service_imports():
    """Test that NTFS service modules can be imported."""
    print("\nTesting NTFS service imports...")
    
    try:
        from services.ntfs import mft_timestamp_patcher
        print("✓ services.ntfs.mft_timestamp_patcher imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import mft_timestamp_patcher: {e}")
        return False
    
    try:
        from services.ntfs import usn_journal_writer
        print("✓ services.ntfs.usn_journal_writer imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import usn_journal_writer: {e}")
        return False
    
    try:
        from services.ntfs import logfile_writer
        print("✓ services.ntfs.logfile_writer imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import logfile_writer: {e}")
        return False
    
    return True


def test_class_instantiation():
    """Test that key classes can be instantiated."""
    print("\nTesting class instantiation...")
    
    try:
        from services.ntfs.logfile_writer import LogfileWriter
        # Mock objects for testing
        class MockMount:
            def resolve(self, path):
                return Path("/tmp/test")
        
        class MockAudit:
            def log(self, data):
                pass
        
        writer = LogfileWriter(MockMount(), MockAudit())
        print(f"✓ LogfileWriter instantiated: {writer.service_name}")
    except Exception as e:
        print(f"✗ Failed to instantiate LogfileWriter: {e}")
        return False
    
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("ARC Linux Host Support - Basic Import Tests")
    print("=" * 60)
    
    results = []
    
    results.append(("Core Imports", test_core_imports()))
    results.append(("NTFS Service Imports", test_ntfs_service_imports()))
    results.append(("Class Instantiation", test_class_instantiation()))
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{test_name}: {status}")
    
    all_passed = all(result[1] for result in results)
    
    print("=" * 60)
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
