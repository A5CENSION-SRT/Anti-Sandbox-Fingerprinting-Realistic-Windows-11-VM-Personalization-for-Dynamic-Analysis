#!/usr/bin/env python3
"""Unit tests for LinuxMountBackend.

Tests for core/linux_mount.py functionality.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock guestfs and hivex before importing
sys.modules['guestfs'] = MagicMock()
sys.modules['hivex'] = MagicMock()

from core.linux_mount import LinuxMountBackend, HivexHandle


# ============================================================================
# LinuxMountBackend Tests
# ============================================================================

class TestLinuxMountBackend:
    """Tests for LinuxMountBackend class."""
    
    @patch('core.linux_mount.guestfs')
    def test_init(self, mock_guestfs):
        """Test LinuxMountBackend initialization."""
        vhdx_path = Path("/path/to/test.vhdx")
        
        backend = LinuxMountBackend(vhdx_path)
        
        assert backend.vhdx_path == vhdx_path
        assert backend._gfs is None
        assert backend._fuse_mountpoint is None
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_mount_success(self, mock_guestfs_class):
        """Test successful mount operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        
        # Mock inspect_os to return Windows partition
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        backend.mount()
        
        # Verify guestfs operations
        mock_gfs.add_drive_opts.assert_called_once()
        mock_gfs.launch.assert_called_once()
        mock_gfs.mount.assert_called_once_with('/dev/sda2', '/')
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_unmount_success(self, mock_guestfs_class):
        """Test successful unmount operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        backend.mount()
        backend.unmount()
        
        # Verify unmount operations
        mock_gfs.umount_all.assert_called_once()
        mock_gfs.shutdown.assert_called_once()
        assert backend._gfs is None
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_read_bytes(self, mock_guestfs_class):
        """Test read_bytes operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        mock_gfs.read_file.return_value = b"test content"
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        content = backend.read_bytes("Windows/test.txt")
        
        assert content == b"test content"
        mock_gfs.read_file.assert_called_once_with("/Windows/test.txt")
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_write_bytes(self, mock_guestfs_class):
        """Test write_bytes operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        backend.write_bytes("Windows/test.txt", b"new content")
        
        mock_gfs.write.assert_called_once_with("/Windows/test.txt", b"new content")
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_mkdir_p(self, mock_guestfs_class):
        """Test mkdir_p operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        backend.mkdir_p("Windows/NewFolder/SubFolder")
        
        mock_gfs.mkdir_p.assert_called_once_with("/Windows/NewFolder/SubFolder")
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_exists(self, mock_guestfs_class):
        """Test exists operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        mock_gfs.exists.return_value = 1  # File exists
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        result = backend.exists("Windows/test.txt")
        
        assert result is True
        mock_gfs.exists.assert_called_once_with("/Windows/test.txt")
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_rm(self, mock_guestfs_class):
        """Test rm operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        backend.rm("Windows/test.txt")
        
        mock_gfs.rm.assert_called_once_with("/Windows/test.txt")
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_ls(self, mock_guestfs_class):
        """Test ls operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = ['/dev/sda2']
        mock_gfs.inspect_get_type.return_value = 'windows'
        mock_gfs.inspect_get_mountpoints.return_value = [('/', '/dev/sda2')]
        mock_gfs.ls.return_value = ['file1.txt', 'file2.txt', 'folder1']
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend.mount()
        
        files = backend.ls("Windows")
        
        assert files == ['file1.txt', 'file2.txt', 'folder1']
        mock_gfs.ls.assert_called_once_with("/Windows")
    
    @patch('subprocess.run')
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_host_fuse_mount(self, mock_guestfs_class, mock_subprocess):
        """Test FUSE mount operation."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        # Mock successful subprocess call
        mock_subprocess.return_value.returncode = 0
        
        mountpoint = backend.host_fuse_mount()
        
        # Verify guestmount was called
        assert mock_subprocess.called
        call_args = mock_subprocess.call_args[0][0]
        assert "guestmount" in call_args
        assert str(vhdx_path) in call_args
        
        # Verify mountpoint was set
        assert backend._fuse_mountpoint is not None
        assert mountpoint == backend._fuse_mountpoint
    
    @patch('subprocess.run')
    def test_host_fuse_unmount(self, mock_subprocess):
        """Test FUSE unmount operation."""
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        backend._fuse_mountpoint = Path("/tmp/arc_fuse_12345")
        
        # Mock successful subprocess call
        mock_subprocess.return_value.returncode = 0
        
        backend.host_fuse_unmount()
        
        # Verify guestunmount was called
        assert mock_subprocess.called
        call_args = mock_subprocess.call_args[0][0]
        assert "guestunmount" in call_args
        
        # Verify mountpoint was cleared
        assert backend._fuse_mountpoint is None


# ============================================================================
# HivexHandle Tests
# ============================================================================

class TestHivexHandle:
    """Tests for HivexHandle context manager."""
    
    @patch('core.linux_mount.hivex.Hivex')
    @patch('tempfile.NamedTemporaryFile')
    def test_context_manager(self, mock_tempfile, mock_hivex_class):
        """Test HivexHandle as context manager."""
        # Mock temporary file
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/hive_12345"
        mock_tempfile.return_value.__enter__.return_value = mock_temp
        
        # Mock hivex
        mock_hivex = MagicMock()
        mock_hivex_class.return_value = mock_hivex
        
        hive_data = b"REGF" + b"\x00" * 100  # Mock registry hive
        
        with HivexHandle(hive_data, write=True) as h:
            assert h == mock_hivex
        
        # Verify hivex was opened
        mock_hivex_class.assert_called_once()
        
        # Verify commit was called
        mock_hivex.commit.assert_called_once()
    
    @patch('core.linux_mount.hivex.Hivex')
    @patch('tempfile.NamedTemporaryFile')
    def test_read_only_mode(self, mock_tempfile, mock_hivex_class):
        """Test HivexHandle in read-only mode."""
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/hive_12345"
        mock_tempfile.return_value.__enter__.return_value = mock_temp
        
        mock_hivex = MagicMock()
        mock_hivex_class.return_value = mock_hivex
        
        hive_data = b"REGF" + b"\x00" * 100
        
        with HivexHandle(hive_data, write=False) as h:
            assert h == mock_hivex
        
        # Verify commit was NOT called in read-only mode
        mock_hivex.commit.assert_not_called()


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestLinuxMountBackendErrors:
    """Tests for error handling in LinuxMountBackend."""
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_mount_no_windows_partition(self, mock_guestfs_class):
        """Test mount fails when no Windows partition found."""
        mock_gfs = MagicMock()
        mock_guestfs_class.return_value = mock_gfs
        mock_gfs.inspect_os.return_value = []  # No OS found
        
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        with pytest.raises(RuntimeError, match="No Windows partition"):
            backend.mount()
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_unmount_not_mounted(self, mock_guestfs_class):
        """Test unmount when not mounted."""
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        # Should not raise exception
        backend.unmount()
    
    @patch('core.linux_mount.guestfs.GuestFS')
    def test_operations_require_mount(self, mock_guestfs_class):
        """Test operations fail when not mounted."""
        vhdx_path = Path("/path/to/test.vhdx")
        backend = LinuxMountBackend(vhdx_path)
        
        with pytest.raises(RuntimeError, match="not mounted"):
            backend.read_bytes("test.txt")
        
        with pytest.raises(RuntimeError, match="not mounted"):
            backend.write_bytes("test.txt", b"data")


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
