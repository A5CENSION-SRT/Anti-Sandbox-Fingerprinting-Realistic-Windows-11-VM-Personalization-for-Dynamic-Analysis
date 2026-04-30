"""ntfs-3g direct partition mount backend (ADR-017).

Provides read/write access to a dual-boot Windows NTFS partition mounted
directly from Ubuntu via ntfs-3g.  No libguestfs, no VHDX, no guestmount.

The Windows partition must be fully shut down (Fast Startup / hibernation
disabled) before calling mount().

Dependencies (system packages):
    apt install -y ntfs-3g fuse3 attr libhivex-bin python3-hivex sleuthkit
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    import hivex as _hivex
    _HAS_HIVEX = True
    logger.debug("hivex: using python3-hivex C extension")
except ImportError:
    try:
        from core import hivex_ctypes as _hivex  # type: ignore[no-redef]
        _HAS_HIVEX = True
        logger.debug("hivex: using ctypes wrapper (libhivex.so.0)")
    except ImportError:
        _HAS_HIVEX = False
        logger.warning(
            "hivex not available. Registry write support requires: "
            "sudo apt install libhivex-bin python3-hivex"
        )

# ---------------------------------------------------------------------------
# NTFS attribute bit flags (winnt.h FILE_ATTRIBUTE_*)
# ---------------------------------------------------------------------------

_ATTR_READONLY = 0x0001
_ATTR_HIDDEN   = 0x0002
_ATTR_SYSTEM   = 0x0004
_ATTR_ARCHIVE  = 0x0020

# FILETIME epoch offset: 100-ns ticks from 1601-01-01 to 1970-01-01
_FILETIME_EPOCH: int = 116_444_736_000_000_000


class LinuxMountBackendError(Exception):
    """Raised on mount/hivex failures."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ticks = int((dt - epoch).total_seconds() * 1e7)
    return ticks + _FILETIME_EPOCH


def _pack_ntfs_times(creation: datetime, modify: datetime,
                     mft_change: datetime, access: datetime) -> bytes:
    return struct.pack(
        "<4Q",
        _to_filetime(creation),
        _to_filetime(modify),
        _to_filetime(mft_change),
        _to_filetime(access),
    )


def _guest_to_host(mount_point: Path, guest_path: str) -> Path:
    """Convert a Windows-style guest path to a host FUSE path."""
    relative = guest_path.replace("\\", "/").lstrip("/")
    return mount_point / relative


# ---------------------------------------------------------------------------
# HivexHandle — context-managed wrapper for a single hive
# ---------------------------------------------------------------------------

class HivexHandle:
    """Context manager wrapping a hivex write session for one hive file.

    Do not construct directly — use LinuxMountBackend.open_hive().
    """

    def __init__(self, backend: "LinuxMountBackend", hive_guest_path: str) -> None:
        self._backend = backend
        self._hive_guest_path = hive_guest_path
        self._tmp_path: Optional[Path] = None
        self._h: Optional[object] = None
        self._committed = False

    def __enter__(self) -> "HivexHandle":
        self._open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None and not self._committed:
            self.commit()
        self._cleanup()

    @property
    def h(self) -> object:
        """Raw hivex.Hivex handle for direct access."""
        if self._h is None:
            raise LinuxMountBackendError("HivexHandle not opened")
        return self._h

    def _open(self) -> None:
        if not _HAS_HIVEX:
            raise LinuxMountBackendError(
                "python3-hivex required: apt install libhivex-bin python3-hivex"
            )
        data = self._backend.read_bytes(self._hive_guest_path)
        fd, tmp = tempfile.mkstemp(suffix=".hive", prefix="arc_hive_")
        os.close(fd)
        self._tmp_path = Path(tmp)
        self._tmp_path.write_bytes(data)
        self._h = _hivex.Hivex(str(self._tmp_path), write=True)

    def commit(self) -> None:
        """Commit changes back to the mounted partition and delete hive logs."""
        if self._h is None:
            return
        self._h.commit(None)
        self._h = None

        modified_data = self._tmp_path.read_bytes()
        self._backend.write_bytes(self._hive_guest_path, modified_data)

        # ADR-010: delete .LOG1 / .LOG2 so Windows doesn't roll back our writes
        for suffix in (".LOG1", ".LOG2"):
            log_path = self._hive_guest_path + suffix
            try:
                self._backend.rm(log_path)
                logger.debug("Deleted hive log: %s", log_path)
            except Exception:
                logger.warning(
                    "Could not delete hive log %s — "
                    "Windows may replay transaction on next mount",
                    log_path,
                )
        self._committed = True

    def _cleanup(self) -> None:
        if self._h is not None:
            try:
                self._h.close()
            except Exception:
                pass
            self._h = None
        if self._tmp_path is not None:
            try:
                self._tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._tmp_path = None


# ---------------------------------------------------------------------------
# LinuxMountBackend
# ---------------------------------------------------------------------------

class LinuxMountBackend:
    """ntfs-3g FUSE mount of the dual-boot Windows NTFS partition (ADR-017).

    Args:
        partition:    Block device, e.g. ``"/dev/nvme0n1p3"``.
        mount_point:  Host directory to mount at, e.g. ``Path("/mnt/arc_windows")``.

    Usage::

        backend = LinuxMountBackend("/dev/nvme0n1p3", Path("/mnt/arc_windows"))
        backend.mount()
        data = backend.read_bytes("/Windows/System32/drivers/etc/hosts")
        backend.unmount()

    Or as a context manager::

        with LinuxMountBackend("/dev/nvme0n1p3", Path("/mnt/arc_windows")) as backend:
            data = backend.read_bytes("/Windows/win.ini")

    All I/O uses standard Python Path operations on the FUSE mountpoint — no
    guestfs, no guestmount subprocess.  setxattr calls go to ntfs-3g directly
    via Python's os.setxattr().
    """

    def __init__(self, partition: str, mount_point: Path) -> None:
        self._partition = partition
        self._mount_point = Path(mount_point)
        self._mounted = False

    # -- context manager --

    def __enter__(self) -> "LinuxMountBackend":
        self.mount()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            self.unmount()
        except Exception as exc:
            logger.error("Error during unmount: %s", exc)

    # -- lifecycle --

    def mount(self) -> None:
        """Pre-flight dirty-bit clear, then ntfs-3g FUSE mount.

        Raises:
            LinuxMountBackendError: If ntfsfix or mount fails.
        """
        if self._mounted:
            return

        self._mount_point.mkdir(parents=True, exist_ok=True)

        # Clear the NTFS dirty bit (in case Windows hibernated unexpectedly).
        # Non-fatal: a clean shutdown leaves no dirty bit, ntfsfix exits 0.
        ntfsfix = subprocess.run(
            ["sudo", "ntfsfix", "-d", self._partition],
            capture_output=True, text=True,
        )
        if ntfsfix.returncode != 0:
            logger.warning(
                "ntfsfix -d exited %d: %s",
                ntfsfix.returncode, ntfsfix.stderr.strip(),
            )

        uid = os.getuid()
        gid = os.getgid()
        base_opts = (
            f"uid={uid},gid={gid},"
            "streams_interface=windows,"
            "allow_other,"
            "windows_names"
        )

        hibernated = ntfsfix.returncode != 0 and "hibernat" in ntfsfix.stderr.lower()

        # If Windows is hibernated, add remove_hiberfile so ntfs-3g can mount rw.
        # This deletes hiberfil.sys (the hibernated session is lost) but Windows
        # boots normally from disk — acceptable for offline injection (ADR-003).
        mount_opts = base_opts + (",remove_hiberfile" if hibernated else "")
        if hibernated:
            logger.warning(
                "Windows partition appears hibernated — mounting with remove_hiberfile. "
                "Boot Windows after ARC completes to let it run chkdsk if needed."
            )

        result = subprocess.run(
            [
                "sudo", "mount", "-t", "ntfs-3g",
                "-o", mount_opts,
                self._partition,
                str(self._mount_point),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise LinuxMountBackendError(
                f"ntfs-3g mount failed ({result.returncode}): {result.stderr.strip()}"
            )
        self._mounted = True
        logger.info("Mounted %s at %s", self._partition, self._mount_point)

    def unmount(self) -> None:
        """Flush writes and unmount the partition."""
        if not self._mounted:
            return
        subprocess.run(["sync"], check=False)
        result = subprocess.run(
            ["sudo", "umount", str(self._mount_point)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.warning("umount returned %d: %s", result.returncode, result.stderr.strip())
        self._mounted = False
        logger.info("Unmounted %s", self._mount_point)

    # -- file I/O --

    def _host(self, guest_path: str) -> Path:
        return _guest_to_host(self._mount_point, guest_path)

    def read_bytes(self, path: str) -> bytes:
        self._require_mounted()
        try:
            return self._host(path).read_bytes()
        except OSError as exc:
            raise LinuxMountBackendError(f"Failed to read {path}: {exc}") from exc

    def write_bytes(self, path: str, data: bytes) -> None:
        self._require_mounted()
        try:
            hp = self._host(path)
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_bytes(data)
        except OSError as exc:
            raise LinuxMountBackendError(f"Failed to write {path}: {exc}") from exc

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, text.encode(encoding))

    def mkdir_p(self, path: str) -> None:
        self._require_mounted()
        try:
            self._host(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LinuxMountBackendError(f"Failed to mkdir_p {path}: {exc}") from exc

    def exists(self, path: str) -> bool:
        self._require_mounted()
        return self._host(path).exists()

    def rm(self, path: str) -> None:
        self._require_mounted()
        try:
            self._host(path).unlink()
        except OSError as exc:
            raise LinuxMountBackendError(f"Failed to rm {path}: {exc}") from exc

    def ls(self, path: str) -> List[str]:
        self._require_mounted()
        try:
            return [p.name for p in self._host(path).iterdir()]
        except OSError as exc:
            raise LinuxMountBackendError(f"Failed to ls {path}: {exc}") from exc

    # -- timestamps --

    def utimens(self, path: str, atime: datetime, mtime: datetime) -> None:
        """Set NTFS $STANDARD_INFORMATION timestamps via ntfs-3g xattr.

        Sets creation=mtime, last_mod=mtime, mft_change=mtime, access=atime.
        $FILE_NAME left at create-time (ADR-009).
        """
        self._require_mounted()
        hp = self._host(path)
        xval = _pack_ntfs_times(
            creation=mtime, modify=mtime, mft_change=mtime, access=atime
        )
        try:
            os.setxattr(str(hp), b"system.ntfs_times", xval)
        except OSError as exc:
            logger.debug("setxattr system.ntfs_times failed for %s: %s", path, exc)

    # -- NTFS attributes --

    def set_ntfs_attributes(
        self,
        path: str,
        *,
        hidden: bool = False,
        system: bool = False,
        archive: bool = True,
    ) -> None:
        """Set NTFS FILE_ATTRIBUTE_* bits via ntfs-3g system.ntfs_attrib_be xattr."""
        self._require_mounted()
        flags = 0
        if hidden:
            flags |= _ATTR_HIDDEN
        if system:
            flags |= _ATTR_SYSTEM
        if archive:
            flags |= _ATTR_ARCHIVE
        packed = struct.pack(">I", flags)
        try:
            os.setxattr(str(self._host(path)), b"system.ntfs_attrib_be", packed)
        except OSError as exc:
            logger.debug("setxattr system.ntfs_attrib_be failed for %s: %s", path, exc)

    # -- hive operations --

    @contextmanager
    def open_hive(self, hive_guest_path: str) -> Generator[HivexHandle, None, None]:
        """Context manager for an offline hive write session (ADR-010).

        Copies the hive to a temp file, opens it with hivex, yields the handle,
        then commits and writes back.  .LOG1 / .LOG2 are deleted after commit.

        Args:
            hive_guest_path: Absolute Windows path, e.g.
                ``"/Windows/System32/config/SOFTWARE"``
        """
        handle = HivexHandle(self, hive_guest_path)
        handle._open()
        try:
            yield handle
        except Exception:
            handle._cleanup()
            raise
        else:
            handle.commit()
            handle._cleanup()

    def commit_hive(self, handle: HivexHandle) -> None:
        """Explicit commit for callers that don't use the context manager."""
        handle.commit()

    # -- FUSE mount access (ADR-017: unified single mount) --

    def host_fuse_mount(self) -> Path:
        """Return the ntfs-3g FUSE mountpoint.

        ADR-017: the partition is already FUSE-mounted — this is the same
        mount used for all I/O.  No additional subprocess needed.
        """
        self._require_mounted()
        return self._mount_point

    def host_fuse_unmount(self) -> None:
        """No-op — ADR-017 uses a single unified mount for all I/O."""

    # -- internal helpers --

    def _require_mounted(self) -> None:
        if not self._mounted:
            raise LinuxMountBackendError(
                "Backend not mounted — call mount() first"
            )
