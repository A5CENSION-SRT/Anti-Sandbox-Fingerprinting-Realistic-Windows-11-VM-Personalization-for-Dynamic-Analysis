"""Shared file-writing utility with timestamp application.

Centralises the write-file + apply-timestamps pattern that was duplicated
across DocumentGenerator, MediaStubService, RecentItemsService, and
CrossWriter.  All filesystem services should use this utility instead of
re-implementing the pattern.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Optional

# Windows file-time APIs — available only on Windows via pywin32
try:
    import pywintypes
    import win32con
    import win32file

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

logger = logging.getLogger(__name__)


class FileWriter:
    """Write files to a mounted image and apply realistic timestamps.

    Args:
        mount_manager: Resolves relative paths against the mount root.
        timestamp_service: Provides created/modified/accessed timestamps.
        audit_logger: Structured audit logger instance.
        default_service_name: Name used in audit log entries when no
            explicit service name is provided.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
        default_service_name: str = "FileWriter",
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._service_name = default_service_name

    def write(
        self,
        rel_path: Path,
        content: bytes,
        event_type: str = "file_create",
        service_name: Optional[str] = None,
    ) -> Path:
        """Write binary content and apply timestamps.

        Args:
            rel_path: Path relative to the mount root.
            content: Binary content to write.
            event_type: Event type for timestamp generation.
            service_name: Override service name in audit log.

        Returns:
            The absolute path to the written file.
        """
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        self.apply_timestamps(full_path, event_type)

        svc = service_name or self._service_name
        self._audit.log({
            "service": svc,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
            "timestamp_event": event_type,
        })

        return full_path

    def write_text(
        self,
        rel_path: Path,
        text: str,
        event_type: str = "file_create",
        service_name: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> Path:
        """Write text content and apply timestamps.

        Args:
            rel_path: Path relative to mount root.
            text: Text content to write.
            event_type: Event type for timestamp generation.
            service_name: Override service name in audit log.
            encoding: Text encoding (default: utf-8).

        Returns:
            The absolute path to the written file.
        """
        return self.write(
            rel_path,
            text.encode(encoding),
            event_type=event_type,
            service_name=service_name,
        )

    def ensure_dir(self, rel_path: Path) -> Path:
        """Create a directory under the mount root.

        Args:
            rel_path: Path relative to the mount root.

        Returns:
            The absolute path to the created directory.
        """
        full_path = self._mount.resolve(str(rel_path))
        full_path.mkdir(parents=True, exist_ok=True)
        return full_path

    def apply_timestamps(self, path: Path, event_type: str) -> None:
        """Apply created/modified/accessed timestamps from the timeline.

        Args:
            path: Absolute path to the file.
            event_type: Event type for timestamp generation.
        """
        timestamps = self._ts.get_timestamp(event_type)

        accessed = timestamps["accessed"].timestamp()
        modified = timestamps["modified"].timestamp()
        os.utime(str(path), (accessed, modified))

        # Creation time requires pywin32 on Windows
        if _HAS_WIN32 and platform.system() == "Windows":
            try:
                created = pywintypes.Time(timestamps["created"])
                handle = win32file.CreateFile(
                    str(path),
                    win32con.GENERIC_WRITE,
                    win32con.FILE_SHARE_WRITE,
                    None,
                    win32con.OPEN_EXISTING,
                    win32con.FILE_ATTRIBUTE_NORMAL,
                    None,
                )
                try:
                    win32file.SetFileTime(handle, created, None, None)
                finally:
                    handle.Close()
            except Exception as exc:
                logger.debug(
                    "Could not set creation time for %s: %s", path, exc
                )
