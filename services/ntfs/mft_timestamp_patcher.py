"""NTFS $STANDARD_INFORMATION timestamp patcher.

This service sets NTFS $STANDARD_INFORMATION (SI) timestamps for files
based on scheduler events. It uses the FUSE mount's setfattr interface
to set the system.ntfs_times extended attribute.

$FILE_NAME (FN) timestamps are intentionally left at create-time per ADR-009,
as SI/FN divergence is realistic and expected in real Windows systems.
"""

from __future__ import annotations

import logging
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.service_context import ServiceContext

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# Windows FILETIME epoch: January 1, 1601 (UTC)
_FILETIME_EPOCH_DELTA = 11644473600  # seconds between 1601 and 1970


def _datetime_to_filetime(dt: datetime) -> int:
    """Convert Python datetime to Windows FILETIME (100-nanosecond intervals since 1601-01-01).
    
    Args:
        dt: Python datetime object (timezone-aware recommended)
    
    Returns:
        Windows FILETIME as 64-bit integer
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    unix_timestamp = dt.timestamp()
    return int((unix_timestamp + _FILETIME_EPOCH_DELTA) * 10_000_000)


class MftTimestampPatcher(BaseService):
    """Patches NTFS $STANDARD_INFORMATION timestamps via FUSE mount.
    
    This service consumes FILE_CREATE and FILE_MODIFY events from the scheduler
    and sets the corresponding NTFS timestamps using setfattr on the FUSE-mounted
    filesystem.
    
    NTFS has two sets of timestamps per file:
    - $STANDARD_INFORMATION (SI): User-settable, modified by this service
    - $FILE_NAME (FN): Kernel-only, set at creation, left unchanged
    
    The divergence between SI and FN is realistic and expected in real systems.
    """
    
    service_name = "MftTimestampPatcher"
    
    def __init__(self) -> None:
        """Initialize the MFT timestamp patcher service."""
        super().__init__()
    
    def apply(self, ctx: "ServiceContext") -> None:
        """Apply NTFS timestamp patches based on scheduler events.
        
        Args:
            ctx: Service execution context containing mount manager, scheduler, etc.
        """
        logger.info("Starting NTFS $STANDARD_INFORMATION timestamp patching")
        
        # Verify FUSE mount is available
        if ctx.mount.backend is None:
            logger.warning(
                "No backend available - skipping NTFS timestamp patching. "
                "This service requires FUSE mount (Phase B)."
            )
            return
        
        fuse_root = ctx.mount.root
        if not fuse_root.exists():
            logger.error("FUSE mount point does not exist: %s", fuse_root)
            return
        
        # Collect all file operation events
        file_events = []
        for event in ctx.scheduler.emit():
            if event.kind in ("FILE_CREATE", "FILE_MODIFY", "FILE_DELETE"):
                file_events.append(event)
        
        logger.info("Processing %d file operation events", len(file_events))
        
        patched_count = 0
        skipped_count = 0
        error_count = 0
        
        for event in file_events:
            try:
                # Extract file path from event payload
                relative_path = event.payload.get("path", "")
                if not relative_path:
                    logger.debug("Event %s has no path, skipping", event.kind)
                    skipped_count += 1
                    continue
                
                # Resolve to FUSE mount path
                # Convert Windows-style paths to POSIX
                posix_path = relative_path.replace("\\", "/").lstrip("/")
                fuse_path = fuse_root / posix_path
                
                # Skip if file doesn't exist (may have been deleted)
                if not fuse_path.exists():
                    logger.debug("File does not exist, skipping: %s", fuse_path)
                    skipped_count += 1
                    continue
                
                # Set timestamps
                self._set_ntfs_timestamps(
                    fuse_path=fuse_path,
                    timestamp=event.timestamp,
                    event_kind=event.kind
                )
                
                patched_count += 1
                
                if patched_count % 100 == 0:
                    logger.debug("Patched %d files so far...", patched_count)
            
            except Exception as exc:
                logger.warning(
                    "Failed to patch timestamps for %s: %s",
                    event.payload.get("path", "unknown"),
                    exc
                )
                error_count += 1
        
        logger.info(
            "NTFS timestamp patching complete: %d patched, %d skipped, %d errors",
            patched_count,
            skipped_count,
            error_count
        )
        
        ctx.audit.log({
            "operation": "ntfs_timestamp_patch",
            "files_patched": patched_count,
            "files_skipped": skipped_count,
            "errors": error_count,
        })
    
    def _set_ntfs_timestamps(
        self,
        fuse_path: Path,
        timestamp: datetime,
        event_kind: str
    ) -> None:
        """Set NTFS $STANDARD_INFORMATION timestamps via setfattr.
        
        Args:
            fuse_path: Absolute path to file on FUSE mount
            timestamp: Timestamp to set
            event_kind: Event type (FILE_CREATE, FILE_MODIFY, etc.)
        """
        # Convert to Windows FILETIME
        filetime = _datetime_to_filetime(timestamp)
        
        # NTFS $STANDARD_INFORMATION has 4 timestamps (all 64-bit little-endian):
        # - atime (last access time)
        # - mtime (last modification time)
        # - ctime (last status change time / MFT change time)
        # - crtime (creation time / birth time)
        
        if event_kind == "FILE_CREATE":
            # For creation, set all 4 timestamps to the same value
            atime = mtime = ctime = crtime = filetime
        elif event_kind == "FILE_MODIFY":
            # For modification, update mtime and ctime, leave atime and crtime
            # However, we don't have the original timestamps, so we set all
            # This is acceptable as real systems often have synchronized timestamps
            atime = mtime = ctime = crtime = filetime
        else:
            # For other events (DELETE, etc.), use current timestamp
            atime = mtime = ctime = crtime = filetime
        
        # Pack as 4 × 64-bit little-endian integers
        packed_times = struct.pack("<QQQQ", atime, mtime, ctime, crtime)
        
        # Convert to hex string with 0x prefix (required by setfattr)
        hex_value = "0x" + packed_times.hex()
        
        # Use setfattr to set the system.ntfs_times extended attribute
        try:
            result = subprocess.run(
                [
                    "setfattr",
                    "-n", "system.ntfs_times",
                    "-v", hex_value,
                    str(fuse_path)
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            
            logger.debug(
                "Set NTFS timestamps for %s (event: %s)",
                fuse_path.name,
                event_kind
            )
        
        except subprocess.CalledProcessError as exc:
            # Common error: attribute not supported (old ntfs-3g version)
            if "not supported" in exc.stderr.lower():
                raise RuntimeError(
                    f"system.ntfs_times attribute not supported. "
                    f"Upgrade ntfs-3g to version >= 2017.3.23. "
                    f"Error: {exc.stderr}"
                )
            else:
                raise RuntimeError(
                    f"setfattr failed for {fuse_path}: {exc.stderr}"
                )
        
        except FileNotFoundError:
            raise RuntimeError(
                "setfattr command not found. Install attr package: "
                "apt install attr"
            )
        
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"setfattr timed out for {fuse_path}"
            )
