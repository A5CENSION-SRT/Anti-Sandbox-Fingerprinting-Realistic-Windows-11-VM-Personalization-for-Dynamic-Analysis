"""NTFS $LogFile best-effort stub.

The NTFS $LogFile is the filesystem's redo/undo log.  Its internal format
(LFS — Log File Service) is complex and Windows-version-specific.  Writing
valid LFS records from scratch is out of scope; however, an empty or near-empty
$LogFile is a forensic tell (real volumes accumulate GBs of redo pages).

This service takes a pragmatic approach:
1.  If the FUSE-mounted $LogFile is accessible, it writes a zeroed header page
    and appends random-seeded page-sized blocks that pass basic size checks.
2.  It records the operation in the audit log regardless.

A production deployment would use ntfs-3g's built-in ``ntfsfix`` utility to
clean the log after all writes are complete — which zeroes the log cleanly.
This stub satisfies the orchestrator interface while noting the limitation.

$LogFile layout (simplified)
-----------------------------
- Page 0: Restart Area (RSTR signature, 4096 bytes)
- Page 1: Restart Area backup
- Page 2+: Log record pages (RCRD signature, 4096 bytes each)

This stub writes two RSTR pages followed by a configurable number of
zeroed RCRD pages to simulate a partially-used log.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from random import Random
from typing import Any

from services.base_service import BaseService

logger = logging.getLogger(__name__)

_LOG_PAGE_SIZE: int = 4096
_RSTR_SIGNATURE: bytes = b"RSTR"
_RCRD_SIGNATURE: bytes = b"RCRD"

# Number of RCRD pages to write (each 4 KiB → 4 MiB for 1024 pages)
_RCRD_PAGE_COUNT: int = 1024

# $LogFile path relative to NTFS root (FUSE layout)
_LOGFILE_REL: str = "$LogFile"


class LogfileWriterError(Exception):
    """Raised when $LogFile stub write fails."""


def _make_rstr_page(seq: int) -> bytes:
    """Build a minimal Restart Area page."""
    buf = bytearray(_LOG_PAGE_SIZE)
    struct.pack_into("<4s", buf, 0, _RSTR_SIGNATURE)
    struct.pack_into("<H", buf, 4, 1)    # UpdateSequenceArrayOffset = 28 typical
    struct.pack_into("<H", buf, 6, 2)    # UpdateSequenceArraySize
    struct.pack_into("<Q", buf, 8, seq)  # CurrentLsn
    return bytes(buf)


def _make_rcrd_page(lsn: int, rng: Random) -> bytes:
    """Build a plausible Log Record page."""
    buf = bytearray(_LOG_PAGE_SIZE)
    struct.pack_into("<4s", buf, 0, _RCRD_SIGNATURE)
    struct.pack_into("<H", buf, 4, 1)   # UpdateSequenceArrayOffset
    struct.pack_into("<H", buf, 6, 2)   # UpdateSequenceArraySize
    struct.pack_into("<Q", buf, 8, lsn) # LastLsnOrFileOffset
    # Fill remainder with noise to avoid trivial entropy detection
    noise = rng.getrandbits((_LOG_PAGE_SIZE - 16) * 8).to_bytes(
        _LOG_PAGE_SIZE - 16, "little"
    )
    buf[16:] = noise
    return bytes(buf)


class LogfileWriter(BaseService):
    """Writes a plausible $LogFile stub to the FUSE-mounted NTFS volume.

    Args:
        mount_manager: Resolves paths against the FUSE-mounted NTFS root.
        audit_logger:  Shared audit logger.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "LogfileWriter"

    def apply(self, ctx: "ServiceContext") -> None:
        """Write $LogFile stub pages.

        Degrades gracefully if the path is inaccessible (non-FUSE mount).

        Raises:
            LogfileWriterError: On fatal write error after open succeeds.
        """
        username = ctx.identity_bundle.user.username
        rng = (
            ctx.scheduler.child_rng("LogfileWriter")
            if ctx.scheduler
            else Random(hash(username + "logfile"))
        )

        try:
            logfile_path = self._mount.resolve(_LOGFILE_REL)
        except Exception as exc:
            logger.warning("LogfileWriter: cannot resolve $LogFile: %s", exc)
            return

        try:
            with logfile_path.open("wb") as fh:
                # Two RSTR restart-area pages
                fh.write(_make_rstr_page(seq=1))
                fh.write(_make_rstr_page(seq=2))

                # RCRD log record pages
                lsn_base = rng.randint(0x0001_0000, 0x0010_0000)
                for i in range(_RCRD_PAGE_COUNT):
                    lsn = lsn_base + i * 4
                    fh.write(_make_rcrd_page(lsn, rng))

        except PermissionError:
            logger.warning(
                "LogfileWriter: no write access to %s; "
                "ensure ntfs-3g FUSE mount is active",
                logfile_path,
            )
            return
        except OSError as exc:
            raise LogfileWriterError(
                f"Failed to write $LogFile stub: {exc}"
            ) from exc

        total_bytes = (2 + _RCRD_PAGE_COUNT) * _LOG_PAGE_SIZE
        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_logfile_stub",
            "path": _LOGFILE_REL,
            "bytes_written": total_bytes,
            "rcrd_pages": _RCRD_PAGE_COUNT,
        })
        logger.info(
            "$LogFile stub written: %d RCRD pages (%d KiB)",
            _RCRD_PAGE_COUNT, total_bytes // 1024,
        )
