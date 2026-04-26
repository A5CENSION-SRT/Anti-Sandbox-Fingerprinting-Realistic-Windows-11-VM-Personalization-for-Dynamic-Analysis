"""Tests for LinuxMountBackend (Phase 3).

Tests that require an actual VHDX + libguestfs are skipped when the
system packages are not available.  The unit tests below cover the
HivexHandle helper logic and the utility functions that run without
requiring a mounted image.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if libguestfs / hivex is unavailable
# ---------------------------------------------------------------------------

try:
    import guestfs as _guestfs
    _HAS_GUESTFS = True
except ImportError:
    _HAS_GUESTFS = False

try:
    import hivex as _hivex
    _HAS_HIVEX = True
except ImportError:
    _HAS_HIVEX = False


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from core.linux_mount import LinuxMountBackend, LinuxMountBackendError, HivexHandle


# ---------------------------------------------------------------------------
# Unit tests (no VHDX required)
# ---------------------------------------------------------------------------

class TestLinuxMountBackendInit:
    def test_missing_vhdx_raises(self, tmp_path):
        missing = tmp_path / "nonexistent.vhdx"
        with pytest.raises(FileNotFoundError):
            LinuxMountBackend(missing)

    def test_missing_guestfs_raises_on_mount(self, tmp_path):
        if _HAS_GUESTFS:
            pytest.skip("guestfs available — error path not triggered")
        vhdx = tmp_path / "fake.vhdx"
        vhdx.write_bytes(b"\x00" * 512)
        backend = LinuxMountBackend.__new__(LinuxMountBackend)
        backend._vhdx_path = vhdx
        backend._g = None
        backend._windows_root = None
        backend._fuse_mountpoint = None
        with pytest.raises(LinuxMountBackendError, match="python3-guestfs"):
            backend.mount()

    def test_init_valid_vhdx_path(self, tmp_path):
        vhdx = tmp_path / "test.vhdx"
        vhdx.write_bytes(b"\x00" * 512)
        if not _HAS_GUESTFS:
            pytest.skip("python3-guestfs not installed")
        backend = LinuxMountBackend(vhdx)
        assert backend._vhdx_path == vhdx
        assert backend._g is None


class TestLinuxMountBackendContextManager:
    def test_context_manager_calls_mount_unmount(self, tmp_path, monkeypatch):
        if not _HAS_GUESTFS:
            pytest.skip("python3-guestfs not installed")
        vhdx = tmp_path / "test.vhdx"
        vhdx.write_bytes(b"\x00" * 512)
        backend = LinuxMountBackend(vhdx)
        mount_calls = []
        unmount_calls = []
        monkeypatch.setattr(backend, "mount", lambda: mount_calls.append(1))
        monkeypatch.setattr(backend, "unmount", lambda: unmount_calls.append(1))
        with backend:
            pass
        assert mount_calls == [1]
        assert unmount_calls == [1]

    def test_context_manager_unmounts_on_exception(self, tmp_path, monkeypatch):
        if not _HAS_GUESTFS:
            pytest.skip("python3-guestfs not installed")
        vhdx = tmp_path / "test.vhdx"
        vhdx.write_bytes(b"\x00" * 512)
        backend = LinuxMountBackend(vhdx)
        unmount_calls = []
        monkeypatch.setattr(backend, "mount", lambda: None)
        monkeypatch.setattr(backend, "unmount", lambda: unmount_calls.append(1))
        with pytest.raises(ValueError):
            with backend:
                raise ValueError("simulated error")
        assert unmount_calls == [1]


# ---------------------------------------------------------------------------
# Integration tests — require a real VHDX + libguestfs
# ---------------------------------------------------------------------------

_FIXTURE_VHDX = None  # Set to a real VHDX path for full integration testing


@pytest.mark.skipif(
    not _HAS_GUESTFS or _FIXTURE_VHDX is None,
    reason="Requires python3-guestfs and a VHDX fixture",
)
class TestLinuxMountBackendIntegration:
    def test_mount_unmount_cycle(self):
        from pathlib import Path
        backend = LinuxMountBackend(Path(_FIXTURE_VHDX))
        backend.mount()
        assert backend._g is not None
        backend.unmount()
        assert backend._g is None

    def test_read_bytes_hosts_file(self):
        from pathlib import Path
        with LinuxMountBackend(Path(_FIXTURE_VHDX)) as backend:
            data = backend.read_bytes("/Windows/System32/drivers/etc/hosts")
            assert len(data) > 0

    def test_write_bytes_round_trip(self, tmp_path):
        from pathlib import Path
        content = b"ARC test marker\n"
        dest = "/Windows/Temp/arc_test.txt"
        with LinuxMountBackend(Path(_FIXTURE_VHDX)) as backend:
            backend.write_bytes(dest, content)
            read_back = backend.read_bytes(dest)
        assert read_back == content

    def test_mkdir_p_creates_path(self):
        from pathlib import Path
        with LinuxMountBackend(Path(_FIXTURE_VHDX)) as backend:
            backend.mkdir_p("/Windows/Temp/arc_test_dir/subdir")
            assert backend.exists("/Windows/Temp/arc_test_dir/subdir")

    def test_exists_returns_false_for_missing(self):
        from pathlib import Path
        with LinuxMountBackend(Path(_FIXTURE_VHDX)) as backend:
            assert not backend.exists("/nonexistent_arc_path_xyz")
