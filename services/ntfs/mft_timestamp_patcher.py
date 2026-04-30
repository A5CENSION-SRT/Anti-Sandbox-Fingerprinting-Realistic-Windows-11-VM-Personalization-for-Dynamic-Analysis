"""MFT $STANDARD_INFORMATION timestamp patcher.

Patches SI timestamps on files via ntfs-3g's ``system.ntfs_times`` xattr so
that file system metadata matches the scheduler-driven activity timeline.

Precondition
------------
The target NTFS volume must be FUSE-mounted via ntfs-3g before calling
``apply()``.  The orchestrator backend handles mount/unmount.

$FILE_NAME timestamps are deliberately left at creation time per ADR-009
to maintain the natural birth-time/SI-time split that forensic tools expect.

xattr format (ntfs-3g ``system.ntfs_times``)
---------------------------------------------
32 bytes, little-endian: four 64-bit Windows FILETIME values in the order::

    [creation_time, last_modification_time, mft_change_time, last_access_time]

All values are 100-nanosecond intervals since 1601-01-01 00:00:00 UTC.
"""

from __future__ import annotations

import logging
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from services.base_service import BaseService

logger = logging.getLogger(__name__)


def _iter_file_descriptors(bundle: Any) -> Generator:
    """Yield all file-bearing descriptors from an ExpansionBundle."""
    yield from getattr(bundle, "documents", [])
    yield from getattr(bundle, "downloads", [])
    yield from getattr(bundle, "media", [])

_FILETIME_EPOCH: int = 116_444_736_000_000_000

# ntfs-3g xattr name for the four NTFS timestamps
_NTFS_TIMES_XATTR: bytes = b"system.ntfs_times"

# Struct: 4 × uint64-LE (creation, modify, mft_change, access)
_NTFS_TIMES_FMT: str = "<4Q"
_NTFS_TIMES_SIZE: int = 32


class MftTimestampPatcherError(Exception):
    """Raised when MFT timestamp patching fails."""


def _to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ticks = int((dt - epoch).total_seconds() * 1e7)
    return ticks + _FILETIME_EPOCH


def _pack_ntfs_times(
    creation: datetime,
    modify: datetime,
    mft_change: datetime,
    access: datetime,
) -> bytes:
    return struct.pack(
        _NTFS_TIMES_FMT,
        _to_filetime(creation),
        _to_filetime(modify),
        _to_filetime(mft_change),
        _to_filetime(access),
    )


class MftTimestampPatcher(BaseService):
    """Patches $STANDARD_INFORMATION timestamps via ntfs-3g xattr.

    Args:
        mount_manager: Resolves paths against the FUSE-mounted NTFS root.
        audit_logger:  Shared audit logger.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "MftTimestampPatcher"

    def apply(self, ctx: "ServiceContext") -> None:
        """Patch SI timestamps for scheduler events AND expansion bundle files.

        Pass 1: scheduler FILE_CREATE / FILE_MODIFY events — patches any file
                whose path appears in the event payload.
        Pass 2: ctx.expansion descriptors — patches every DocumentDescriptor,
                DownloadDescriptor, and MediaDescriptor that carries a
                ``timestamp_offset_days`` value, using the scheduler's
                install_time as the timeline anchor.

        Silently skips files that do not exist on the mounted filesystem.
        Silently skips entirely on non-Linux platforms (os.setxattr is
        Linux-only and only meaningful on an ntfs-3g FUSE mount).

        Raises:
            MftTimestampPatcherError: On fatal xattr write error.
        """
        # Platform guard: os.setxattr is Linux-only and only works on
        # ntfs-3g FUSE mounts.  Skip gracefully on Windows and macOS.
        if not hasattr(os, "setxattr"):
            logger.info(
                "MftTimestampPatcher: os.setxattr not available on this platform "
                "(requires Linux + ntfs-3g FUSE mount). Skipping."
            )
            return

        # Check that an ntfs backend is actually mounted
        backend = getattr(ctx.mount, "backend", None)
        if backend is None:
            logger.info(
                "MftTimestampPatcher: no NTFS FUSE backend mounted "
                "(output-directory mode). SI timestamps set via os.utime only."
            )
            return

        if ctx.scheduler is None:
            logger.warning("MftTimestampPatcher: no scheduler; skipping")
            return

        patched = 0
        skipped = 0
        failed = 0

        # -- Pass 1: scheduler event paths ------------------------------------
        for event in ctx.scheduler.events_of("FILE_CREATE", "FILE_MODIFY"):
            rel_path: Optional[str] = (event.payload or {}).get("path")
            if not rel_path:
                skipped += 1
                continue

            p, s, f = self._patch_one(rel_path, event.timestamp)
            patched += p; skipped += s; failed += f

        # -- Pass 2: expansion bundle descriptors -----------------------------
        if ctx.expansion is not None:
            install_time = ctx.scheduler.install_time
            for desc in _iter_file_descriptors(ctx.expansion):
                offset = getattr(desc, "timestamp_offset_days", -1.0)
                if offset < 0.0:
                    skipped += 1
                    continue
                ts = install_time + timedelta(days=offset)
                p, s, f = self._patch_one(desc.relative_path, ts)
                patched += p; skipped += s; failed += f

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "patch_si_timestamps",
            "patched": patched,
            "skipped": skipped,
            "failed": failed,
        })
        logger.info(
            "MFT SI timestamp patch: %d patched, %d skipped, %d failed",
            patched, skipped, failed,
        )

        if failed > patched and failed > 10:
            raise MftTimestampPatcherError(
                f"Too many setxattr failures ({failed}); "
                "check that ntfs-3g FUSE mount is active"
            )

    def _patch_one(self, rel_path: str, ts: datetime) -> tuple:
        """Attempt to patch SI timestamps on one file.

        Returns:
            (patched, skipped, failed) increment tuple.
        """
        try:
            host_path = self._mount.resolve(rel_path)
        except Exception:
            return 0, 1, 0

        if not host_path.exists():
            return 0, 1, 0

        access_ts = ts + timedelta(seconds=1)
        xattr_value = _pack_ntfs_times(
            creation=ts,
            modify=ts,
            mft_change=ts,
            access=access_ts,
        )

        try:
            os.setxattr(str(host_path), _NTFS_TIMES_XATTR, xattr_value)
            return 1, 0, 0
        except OSError as exc:
            if exc.errno in (95, 1):  # ENOTSUP, EPERM — not ntfs-3g FUSE
                logger.debug(
                    "setxattr not supported on %s (errno=%d); "
                    "ensure ntfs-3g FUSE mount is active",
                    host_path, exc.errno,
                )
                return 0, 1, 0
            logger.warning("setxattr failed for %s: %s", host_path, exc)
            return 0, 0, 1
