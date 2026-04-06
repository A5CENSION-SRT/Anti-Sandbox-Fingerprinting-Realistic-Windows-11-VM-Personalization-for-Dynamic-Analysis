"""Windows Recycle Bin generator.

Creates $Recycle.Bin structures with $I (index) and $R (data) files to
simulate deleted file history. Each deleted file generates:
- $I{id}.{ext} - Index file with deletion metadata
- $R{id}.{ext} - Renamed original file (placeholder)

The Recycle Bin path is: $Recycle.Bin/{SID}/
"""

from __future__ import annotations

import logging
import os
import platform
import struct
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from random import Random
from typing import Any, Dict, List, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)

# Windows file time APIs for setting creation time
try:
    import pywintypes
    import win32con
    import win32file

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# $I file header version (Windows 10+)
_INDEX_VERSION_WIN10 = 2

# Profile-specific deleted files
_PROFILE_DELETED_FILES: Dict[str, List[Dict[str, Any]]] = {
    "home_user": [
        {"name": "old_photo.jpg", "path": r"C:\Users\{username}\Pictures\old_photo.jpg", "size": (50000, 200000)},
        {"name": "screenshot_old.png", "path": r"C:\Users\{username}\Pictures\screenshot_old.png", "size": (100000, 500000)},
        {"name": "temp_download.exe", "path": r"C:\Users\{username}\Downloads\temp_download.exe", "size": (1000000, 5000000)},
        {"name": "old_notes.txt", "path": r"C:\Users\{username}\Documents\old_notes.txt", "size": (1000, 10000)},
        {"name": "duplicate_video.mp4", "path": r"C:\Users\{username}\Videos\duplicate_video.mp4", "size": (10000000, 50000000)},
    ],
    "office_user": [
        {"name": "draft_report_v1.docx", "path": r"C:\Users\{username}\Documents\Work\draft_report_v1.docx", "size": (50000, 200000)},
        {"name": "old_budget.xlsx", "path": r"C:\Users\{username}\Documents\Work\old_budget.xlsx", "size": (30000, 150000)},
        {"name": "temp_data.csv", "path": r"C:\Users\{username}\Documents\Work\temp_data.csv", "size": (10000, 100000)},
        {"name": "presentation_backup.pptx", "path": r"C:\Users\{username}\Documents\Work\presentation_backup.pptx", "size": (500000, 2000000)},
        {"name": "meeting_notes_old.txt", "path": r"C:\Users\{username}\Documents\Work\meeting_notes_old.txt", "size": (1000, 5000)},
    ],
    "developer": [
        {"name": "test_output.log", "path": r"C:\Users\{username}\Projects\test_output.log", "size": (10000, 100000)},
        {"name": "debug_dump.txt", "path": r"C:\Users\{username}\Projects\debug_dump.txt", "size": (5000, 50000)},
        {"name": "old_backup.zip", "path": r"C:\Users\{username}\Projects\old_backup.zip", "size": (1000000, 10000000)},
        {"name": "node_modules_backup", "path": r"C:\Users\{username}\Projects\node_modules_backup", "size": (50000000, 200000000)},
        {"name": "temp_script.py", "path": r"C:\Users\{username}\Projects\temp_script.py", "size": (1000, 10000)},
        {"name": ".env.backup", "path": r"C:\Users\{username}\Projects\.env.backup", "size": (500, 2000)},
    ],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RecycleBinError(Exception):
    """Raised when Recycle Bin generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RecycleBinService(BaseService):
    """Creates Windows Recycle Bin artifacts.

    Generates $Recycle.Bin/{SID}/ structures with $I and $R files
    to simulate deleted file history.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        timestamp_service: Provides timestamps for file operations.
        audit_logger: Structured audit logging.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "RecycleBinService"

    def apply(self, context: dict) -> None:
        """Generate Recycle Bin artifacts for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.
                * ``user_sid`` (str) — Windows SID for the user.

        Raises:
            RecycleBinError: If Recycle Bin generation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)
        user_sid = context.get("user_sid", self._generate_sid(username, seed))

        rng = Random(hash(seed + profile_type))
        recycle_dir = Path("$Recycle.Bin") / user_sid
        created_files = 0

        try:
            # Ensure Recycle Bin directory exists
            full_recycle_dir = self._mount.resolve(str(recycle_dir))
            full_recycle_dir.mkdir(parents=True, exist_ok=True)

            # Create desktop.ini
            self._create_desktop_ini(recycle_dir)

            # Get deleted files for this profile
            deleted_files = _PROFILE_DELETED_FILES.get(profile_type, [])

            for i, file_spec in enumerate(deleted_files):
                # Skip some files randomly
                if rng.random() < 0.15:
                    continue

                name = file_spec["name"]
                original_path = file_spec["path"].replace("{username}", username)
                size_range = file_spec.get("size", (1000, 100000))

                # Generate unique ID for this deleted file
                file_id = self._generate_file_id(rng)

                # Get file extension
                ext = Path(name).suffix if "." in name else ""

                # Create $I file (index)
                i_filename = f"$I{file_id}{ext}"
                i_content = self._create_index_file(
                    original_path, rng, size_range
                )
                self._write_file(recycle_dir / i_filename, i_content, event_type="delete")
                created_files += 1

                # Create $R file (placeholder data)
                r_filename = f"$R{file_id}{ext}"
                r_size = rng.randint(*size_range)
                r_content = self._create_data_stub(r_size, rng)
                self._write_file(recycle_dir / r_filename, r_content, event_type="recycle")
                created_files += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_recycle_bin",
                "username": username,
                "profile_type": profile_type,
                "user_sid": user_sid,
                "files_created": created_files,
            })

            logger.info(
                "Generated %d Recycle Bin files for user '%s' (SID: %s)",
                created_files, username, user_sid,
            )

        except Exception as exc:
            logger.error("Failed to generate Recycle Bin: %s", exc)
            raise RecycleBinError(
                f"Recycle Bin generation failed: {exc}"
            ) from exc

    def _write_file(self, rel_path: Path, content: bytes, event_type: str = "recycle") -> None:
        """Write file content to the mounted filesystem and apply timestamps."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        self._apply_timestamps(full_path, event_type)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
        })

    def _apply_timestamps(self, path: Path, event_type: str) -> None:
        """Apply created/modified/accessed timestamps from the timestamp service."""
        timestamps = self._ts.get_timestamp(event_type)
        accessed = timestamps["accessed"].timestamp()
        modified = timestamps["modified"].timestamp()
        os.utime(str(path), (accessed, modified))

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
                logger.debug("Could not set creation time for %s: %s", path, exc)

    def _generate_sid(self, username: str, seed: str) -> str:
        """Generate a deterministic Windows SID.

        Args:
            username: Windows username.
            seed: RNG seed (computer name).

        Returns:
            SID string in format S-1-5-21-X-X-X-1001.
        """
        rng = Random(hash(username + seed + "sid"))

        # Standard Windows SID format
        sub1 = rng.randint(1000000000, 4000000000)
        sub2 = rng.randint(1000000000, 4000000000)
        sub3 = rng.randint(1000000000, 4000000000)
        rid = 1001  # Standard first user RID

        return f"S-1-5-21-{sub1}-{sub2}-{sub3}-{rid}"

    def _generate_file_id(self, rng: Random) -> str:
        """Generate a random file ID for Recycle Bin entries.

        Args:
            rng: Random number generator.

        Returns:
            6-character alphanumeric ID.
        """
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(rng.choice(chars) for _ in range(6))

    def _create_desktop_ini(self, recycle_dir: Path) -> None:
        """Create desktop.ini for Recycle Bin folder.

        Args:
            recycle_dir: Path to Recycle Bin folder.
        """
        content = (
            "[.ShellClassInfo]\r\n"
            "CLSID={645FF040-5081-101B-9F08-00AA002F954E}\r\n"
            "LocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-8964\r\n"
        ).encode("utf-8")

        self._write_file(recycle_dir / "desktop.ini", content, event_type="system_file")

    def _create_index_file(
        self,
        original_path: str,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Create a $I index file for a deleted item.

        Windows 10+ $I file format:
        - Header (8 bytes): version + file size
        - Delete timestamp (8 bytes): FILETIME
        - Path length (4 bytes)
        - Original path (UTF-16LE, null-terminated)

        Args:
            original_path: Original file path.
            rng: Random number generator.
            size_range: (min_size, max_size) for file.

        Returns:
            Binary $I file content.
        """
        # Get deletion timestamp
        ts = self._ts.get_timestamp("delete")
        delete_time = ts["modified"]
        delete_filetime = self._ts.datetime_to_filetime(delete_time)

        # File size
        file_size = rng.randint(*size_range)

        # Original path as UTF-16LE
        path_utf16 = original_path.encode("utf-16-le") + b"\x00\x00"
        path_len = len(path_utf16)

        # Build $I file
        header = bytearray(28 + path_len)

        # Version (2 for Win10+)
        struct.pack_into("<Q", header, 0, _INDEX_VERSION_WIN10)

        # Original file size
        struct.pack_into("<Q", header, 8, file_size)

        # Delete timestamp
        struct.pack_into("<Q", header, 16, delete_filetime)

        # Path length (in bytes, including null)
        struct.pack_into("<I", header, 24, path_len)

        # Original path
        header[28:28 + path_len] = path_utf16

        return bytes(header)

    def _create_data_stub(self, size: int, rng: Random) -> bytes:
        """Create a placeholder data file ($R).

        Args:
            size: Target file size.
            rng: Random number generator.

        Returns:
            Binary content (random bytes).
        """
        # For efficiency, create pattern-based content
        if size <= 4096:
            return bytes([rng.randint(0, 255) for _ in range(size)])

        # For larger files, repeat a pattern
        pattern_size = 4096
        pattern = bytes([rng.randint(0, 255) for _ in range(pattern_size)])

        content = pattern * (size // pattern_size)
        remainder = size % pattern_size
        if remainder > 0:
            content += pattern[:remainder]

        return content
