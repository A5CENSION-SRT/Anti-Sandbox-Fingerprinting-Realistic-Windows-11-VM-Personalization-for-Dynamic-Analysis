"""Linux-native VHDX mount backend.

Provides read/write access to Windows NTFS partitions inside a VHDX/VHD
image using libguestfs (for general I/O) and hivex (for offline registry hive
editing).  A FUSE mount via guestmount is offered for services that need
direct filesystem access (Phase 4b).

Dependencies (system packages):
    apt install -y libguestfs-tools python3-guestfs libhivex-bin python3-hivex
    apt install -y ntfs-3g fuse3 guestmount
"""

from __future__ import annotations

import logging
import os
import struct
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterator, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    import guestfs as _guestfs
    _HAS_GUESTFS = True
except ImportError:
    _HAS_GUESTFS = False
    logger.warning(
        "python3-guestfs not found. LinuxMountBackend requires: "
        "apt install libguestfs-tools python3-guestfs"
    )

try:
    import hivex as _hivex
    _HAS_HIVEX = True
except ImportError:
    _HAS_HIVEX = False
    logger.warning(
        "python3-hivex not found. Hive write support requires: "
        "apt install libhivex-bin python3-hivex"
    )

# ---------------------------------------------------------------------------
# NTFS attribute bit flags (winnt.h FILE_ATTRIBUTE_*)
# ---------------------------------------------------------------------------

_ATTR_READONLY = 0x0001
_ATTR_HIDDEN   = 0x0002
_ATTR_SYSTEM   = 0x0004
_ATTR_ARCHIVE  = 0x0020


class LinuxMountBackendError(Exception):
    """Raised on guestfs/hivex failures."""


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
        self._tmpfile: Optional[tempfile.NamedTemporaryFile] = None
        self._h: Optional[object] = None  # hivex.Hivex instance
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
        self._tmpfile = tempfile.NamedTemporaryFile(
            delete=False, suffix=".hive", prefix="arc_hive_"
        )
        self._tmpfile.write(data)
        self._tmpfile.flush()
        self._tmpfile.close()
        self._h = _hivex.Hivex(self._tmpfile.name, write=True)

    def commit(self) -> None:
        """Commit changes back to the VHDX and delete hive transaction logs."""
        if self._h is None:
            return
        self._h.commit(None)
        self._h = None

        with open(self._tmpfile.name, "rb") as f:
            modified_data = f.read()
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
        if self._tmpfile is not None:
            try:
                os.unlink(self._tmpfile.name)
            except OSError:
                pass
            self._tmpfile = None


# ---------------------------------------------------------------------------
# LinuxMountBackend
# ---------------------------------------------------------------------------

class LinuxMountBackend:
    """libguestfs-backed read/write access to a Windows NTFS VHDX image.

    Args:
        vhdx_path: Path to the .vhdx or .vhd image file.

    Usage::

        backend = LinuxMountBackend(Path("windows.vhdx"))
        backend.mount()
        data = backend.read_bytes("/Windows/System32/drivers/etc/hosts")
        backend.unmount()

    Or as a context manager::

        with LinuxMountBackend(Path("windows.vhdx")) as backend:
            data = backend.read_bytes("/Windows/win.ini")
    """

    def __init__(self, vhdx_path: Path) -> None:
        if not _HAS_GUESTFS:
            raise LinuxMountBackendError(
                "python3-guestfs required: apt install libguestfs-tools python3-guestfs"
            )
        self._vhdx_path = Path(vhdx_path)
        if not self._vhdx_path.exists():
            raise FileNotFoundError(f"VHDX image not found: {self._vhdx_path}")
        self._g: Optional[object] = None          # guestfs.GuestFS instance
        self._windows_root: Optional[str] = None  # e.g. "/dev/sda2"
        self._fuse_mountpoint: Optional[Path] = None

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
        """Launch guestfs appliance, inspect the image, and mount the Windows partition."""
        if self._g is not None:
            return
        g = _guestfs.GuestFS(python_return_dict=True)
        g.set_backend(os.environ.get("LIBGUESTFS_BACKEND", "direct"))
        suffix = self._vhdx_path.suffix.lower()
        fmt = "vhd" if suffix in (".vhd", ".vhdx") else "raw"
        g.add_drive_opts(str(self._vhdx_path), format=fmt, readonly=False)
        g.launch()

        roots = g.inspect_os()
        if not roots:
            g.shutdown()
            raise LinuxMountBackendError(
                f"No OS found in image: {self._vhdx_path}"
            )

        windows_root = roots[0]
        mps = g.inspect_get_mountpoints(windows_root)

        # Sort by path length so we mount root first, then subdirs
        for mp, dev in sorted(mps.items(), key=lambda x: len(x[0])):
            try:
                g.mount(dev, mp)
            except Exception as exc:
                logger.warning("Could not mount %s at %s: %s", dev, mp, exc)

        self._g = g
        self._windows_root = windows_root
        logger.info("Mounted %s (root=%s)", self._vhdx_path.name, windows_root)

    def unmount(self) -> None:
        """Flush writes, unmount all partitions, and shut down the appliance."""
        if self._g is None:
            return
        if self._fuse_mountpoint is not None:
            self.host_fuse_unmount()
        try:
            self._g.umount_all()
            self._g.shutdown()
        finally:
            self._g = None
            self._windows_root = None
        logger.info("Unmounted %s", self._vhdx_path.name)

    # -- file I/O --

    def read_bytes(self, path: str) -> bytes:
        """Read file contents from the mounted image."""
        self._require_mounted()
        try:
            return self._g.read_file(path)
        except Exception as exc:
            raise LinuxMountBackendError(
                f"Failed to read {path}: {exc}"
            ) from exc

    def write_bytes(self, path: str, data: bytes) -> None:
        """Write binary content to a path in the mounted image."""
        self._require_mounted()
        try:
            parent = path.rsplit("/", 1)[0] or "/"
            if not self._g.exists(parent):
                self._g.mkdir_p(parent)
            self._g.write(path, data)
        except Exception as exc:
            raise LinuxMountBackendError(
                f"Failed to write {path}: {exc}"
            ) from exc

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> None:
        """Write text content to a path in the mounted image."""
        self.write_bytes(path, text.encode(encoding))

    def mkdir_p(self, path: str) -> None:
        """Create directories recursively in the mounted image."""
        self._require_mounted()
        try:
            self._g.mkdir_p(path)
        except Exception as exc:
            raise LinuxMountBackendError(
                f"Failed to mkdir_p {path}: {exc}"
            ) from exc

    def exists(self, path: str) -> bool:
        """Return True if *path* exists in the mounted image."""
        self._require_mounted()
        try:
            return bool(self._g.exists(path))
        except Exception:
            return False

    def rm(self, path: str) -> None:
        """Remove a file from the mounted image."""
        self._require_mounted()
        try:
            self._g.rm(path)
        except Exception as exc:
            raise LinuxMountBackendError(
                f"Failed to rm {path}: {exc}"
            ) from exc

    def ls(self, path: str) -> List[str]:
        """List directory contents in the mounted image."""
        self._require_mounted()
        try:
            return list(self._g.ls(path))
        except Exception as exc:
            raise LinuxMountBackendError(
                f"Failed to ls {path}: {exc}"
            ) from exc

    # -- timestamps --

    def utimens(self, path: str, atime: datetime, mtime: datetime) -> None:
        """Set access and modification timestamps on a file in the image.

        Sets NTFS $STANDARD_INFORMATION timestamps (SI) only.  $FILE_NAME (FN)
        timestamps are left at create-time per ADR-009 — SI/FN divergence is
        realistic and expected.
        """
        self._require_mounted()
        atsecs = int(atime.timestamp())
        mtsecs = int(mtime.timestamp())
        try:
            self._g.utimens(path, atsecs, 0, mtsecs, 0)
        except Exception as exc:
            logger.debug("utimens failed for %s: %s", path, exc)

    # -- NTFS attributes --

    def set_ntfs_attributes(
        self,
        path: str,
        *,
        hidden: bool = False,
        system: bool = False,
        archive: bool = True,
    ) -> None:
        """Set NTFS file attribute bits on a path in the mounted image.

        Uses the ``system.ntfs_attrib_be`` xattr (big-endian 4-byte packed int)
        supported by ntfs-3g via libguestfs.
        """
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
            self._g.setxattr("system.ntfs_attrib_be", packed, 4, path)
        except Exception as exc:
            logger.debug(
                "setxattr system.ntfs_attrib_be failed for %s: %s", path, exc
            )

    # -- hive operations --

    @contextmanager
    def open_hive(self, hive_guest_path: str) -> Generator[HivexHandle, None, None]:
        """Context manager for an offline hive write session.

        Pulls the hive from the image, opens it with hivex, yields a
        HivexHandle for callers to perform node/value operations, then
        commits and writes the modified hive back.  .LOG1 / .LOG2 are
        deleted after commit (ADR-010).

        Args:
            hive_guest_path: Absolute guest path, e.g.
                ``"/Windows/System32/config/SOFTWARE"``

        Yields:
            HivexHandle — wraps the raw hivex.Hivex instance.

        Example::

            with backend.open_hive("/Windows/System32/config/SOFTWARE") as h:
                root = h.h.root()
                ms = h.h.node_get_child(root, "Microsoft")
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

    # -- FUSE mount for raw stream work (Phase 4b) --

    def host_fuse_mount(self) -> Path:
        """Mount the image via guestmount (ntfs-3g FUSE) and return the mountpoint.

        Services that need raw POSIX filesystem access (e.g. for $UsnJrnl:$J
        appending in Phase 4b) can use the returned path as a host directory.

        The caller must call host_fuse_unmount() when done.
        """
        if self._fuse_mountpoint is not None:
            return self._fuse_mountpoint

        mp = Path(tempfile.mkdtemp(prefix="arc_fuse_"))
        import subprocess
        result = subprocess.run(
            [
                "guestmount",
                "--add", str(self._vhdx_path),
                "--inspector",
                "--rw",
                str(mp),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            mp.rmdir()
            raise LinuxMountBackendError(
                f"guestmount failed: {result.stderr.strip()}"
            )
        self._fuse_mountpoint = mp
        logger.info("FUSE-mounted %s at %s", self._vhdx_path.name, mp)
        return mp

    def host_fuse_unmount(self) -> None:
        """Unmount the guestmount FUSE mountpoint."""
        if self._fuse_mountpoint is None:
            return
        import subprocess
        subprocess.run(
            ["guestunmount", str(self._fuse_mountpoint)],
            capture_output=True,
            timeout=60,
        )
        try:
            self._fuse_mountpoint.rmdir()
        except OSError:
            pass
        self._fuse_mountpoint = None

    # -- internal helpers --

    def _require_mounted(self) -> None:
        if self._g is None:
            raise LinuxMountBackendError(
                "Backend not mounted — call mount() first"
            )
