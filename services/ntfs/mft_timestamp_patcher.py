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
from typing import Any, Optional

from services.base_service import BaseService

logger = logging.getLogger(__name__)

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
        """Patch SI timestamps for all FILE_CREATE / FILE_MODIFY events.

        Silently skips files that do not exist on the mounted filesystem
        (they may be registry-only or were never written by other services).

        Raises:
            MftTimestampPatcherError: On fatal xattr write error.
        """
        if ctx.scheduler is None:
            logger.warning("MftTimestampPatcher: no scheduler; skipping")
            return

        patched = 0
        skipped = 0
        failed = 0

        for event in ctx.scheduler.events_of("FILE_CREATE", "FILE_MODIFY"):
            rel_path: Optional[str] = (event.payload or {}).get("path")
            if not rel_path:
                skipped += 1
                continue

            try:
                host_path = self._mount.resolve(rel_path)
            except Exception:
                skipped += 1
                continue

            if not host_path.exists():
                skipped += 1
                continue

            ts = event.timestamp
            # creation = timestamp of the event; access slightly later
            access_ts = ts + timedelta(seconds=1)

            xattr_value = _pack_ntfs_times(
                creation=ts,
                modify=ts,
                mft_change=ts,
                access=access_ts,
            )

            try:
                os.setxattr(str(host_path), _NTFS_TIMES_XATTR, xattr_value)
                patched += 1
            except OSError as exc:
                # On non-NTFS / non-FUSE filesystems this silently fails.
                if exc.errno in (95, 1):  # ENOTSUP, EPERM — not an ntfs-3g mount
                    logger.debug(
                        "setxattr not supported on %s (errno=%d); "
                        "ensure ntfs-3g FUSE mount is active",
                        host_path, exc.errno,
                    )
                    skipped += 1
                else:
                    logger.warning("setxattr failed for %s: %s", host_path, exc)
                    failed += 1

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
