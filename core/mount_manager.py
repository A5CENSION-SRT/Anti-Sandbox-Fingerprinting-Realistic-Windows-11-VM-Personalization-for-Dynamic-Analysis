"""Mount point validation and path resolution.

In standalone mode (no backend), resolves paths against a local directory
suitable for testing and dry-runs.  When a LinuxMountBackend is provided,
the backend's FUSE mountpoint is used as the root — so all downstream services
write directly to the mounted NTFS filesystem without any API changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.linux_mount import LinuxMountBackend

logger = logging.getLogger(__name__)


class MountManager:
    """Manages the mounted drive root and resolves relative paths safely.

    Args:
        mount_root: Host path to use as the mount root.  Ignored when
            *backend* is supplied (the backend's FUSE mountpoint is used).
        backend: Optional LinuxMountBackend.  When provided, the VHDX is
            FUSE-mounted and *mount_root* is derived from the mountpoint.
    """

    def __init__(
        self,
        mount_root: str,
        backend: Optional["LinuxMountBackend"] = None,
    ) -> None:
        self._backend = backend
        if backend is not None:
            fuse_mp = backend.host_fuse_mount()
            self._mount_root = fuse_mp.resolve()
            logger.debug("MountManager using FUSE mountpoint: %s", self._mount_root)
        else:
            self._mount_root = Path(mount_root).resolve()
            if not self._mount_root.exists():
                self._mount_root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Return the resolved mount root path."""
        return self._mount_root

    @property
    def backend(self) -> Optional["LinuxMountBackend"]:
        """Return the underlying LinuxMountBackend, or None in standalone mode."""
        return self._backend

    def resolve(self, relative_path: str = "") -> Path:
        """Resolve a relative path against the mount root.

        Raises:
            ValueError: If the resolved path escapes the mount root.
        """
        resolved = (self._mount_root / relative_path).resolve()
        if not str(resolved).startswith(str(self._mount_root)):
            raise ValueError(
                f"Path escape detected: {relative_path} resolves outside mount root"
            )
        return resolved

    def set_ntfs_attributes(
        self,
        relative_path: str,
        *,
        hidden: bool = False,
        system: bool = False,
        archive: bool = True,
    ) -> None:
        """Set NTFS file attributes on a path within the mount root.

        When a LinuxMountBackend is active, delegates to the backend's
        ``set_ntfs_attributes()`` using the guest path derived from the
        relative path.  In standalone mode this is a no-op — NTFS attribute
        bits are only meaningful inside an actual NTFS filesystem.
        """
        if self._backend is None:
            return
        guest_path = "/" + relative_path.replace("\\", "/").lstrip("/")
        self._backend.set_ntfs_attributes(
            guest_path, hidden=hidden, system=system, archive=archive
        )

    def utimens(
        self,
        relative_path: str,
        atime: "datetime",
        mtime: "datetime",
    ) -> None:
        """Set NTFS timestamps on a path via the backend.

        No-op in standalone mode (timestamps are set via os.utime by services
        directly).
        """
        if self._backend is None:
            return
        from datetime import datetime
        guest_path = "/" + relative_path.replace("\\", "/").lstrip("/")
        self._backend.utimens(guest_path, atime, mtime)
