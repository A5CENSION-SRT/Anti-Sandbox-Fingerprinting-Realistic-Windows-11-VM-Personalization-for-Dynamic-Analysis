"""Windows Recent Items generator.

Creates .lnk shortcut files in the user's Recent folder to simulate
recently accessed file history. Windows stores shortcuts to recently
opened files in AppData/Roaming/Microsoft/Windows/Recent/.

The .lnk format is a Windows Shell Link binary format containing:
- Header with magic signature and flags
- Link target info (file path)
- String data (description, relative path, etc.)
"""

from __future__ import annotations

import logging
import os
import platform
import struct
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Tuple
from uuid import UUID

from services.base_service import BaseService

# Windows file time APIs for setting creation time
try:
    import pywintypes
    import win32con
    import win32file

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# LNK file header magic
_LNK_MAGIC = bytes([
    0x4C, 0x00, 0x00, 0x00,  # HeaderSize (76 bytes)
    0x01, 0x14, 0x02, 0x00,  # LinkCLSID
    0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x46,
])

# Link flags
_LINK_FLAG_HAS_LINK_TARGET_ID_LIST = 0x00000001
_LINK_FLAG_HAS_LINK_INFO = 0x00000002
_LINK_FLAG_HAS_NAME = 0x00000004
_LINK_FLAG_HAS_RELATIVE_PATH = 0x00000008
_LINK_FLAG_HAS_WORKING_DIR = 0x00000010
_LINK_FLAG_IS_UNICODE = 0x00000080

# File attributes
_FILE_ATTRIBUTE_ARCHIVE = 0x00000020
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010

# Profile-specific recent files
# Profile-specific recent files — names/paths MUST match DocumentGenerator,
# MediaStubService, and UserDirectoryService output for cross-reference
# coherence.
_PROFILE_RECENT_FILES: Dict[str, List[Dict[str, Any]]] = {
    "home_user": [
        {"name": "IMG_20240115_vacation.jpg", "path": r"C:\Users\{username}\Pictures\IMG_20240115_vacation.jpg"},
        {"name": "birthday_2024.mp4", "path": r"C:\Users\{username}\Videos\birthday_2024.mp4"},
        {"name": "Shopping_List.txt", "path": r"C:\Users\{username}\Documents\Shopping_List.txt"},
        {"name": "Budget.xlsx", "path": r"C:\Users\{username}\Documents\Budget.xlsx"},
        {"name": "Recipe_Collection.docx", "path": r"C:\Users\{username}\Documents\Recipe_Collection.docx"},
        {"name": "Vacation_Plan.docx", "path": r"C:\Users\{username}\Documents\Vacation_Plan.docx"},
        {"name": "Downloads", "path": r"C:\Users\{username}\Downloads", "is_dir": True},
        {"name": "Pictures", "path": r"C:\Users\{username}\Pictures", "is_dir": True},
    ],
    "office_user": [
        {"name": "Q4_Report_2024.docx", "path": r"C:\Users\{username}\Documents\Q4_Report_2024.docx"},
        {"name": "Budget_FY2025.xlsx", "path": r"C:\Users\{username}\Documents\Budget_FY2025.xlsx"},
        {"name": "Project_Timeline.xlsx", "path": r"C:\Users\{username}\Documents\Project_Timeline.xlsx"},
        {"name": "Meeting_Notes.txt", "path": r"C:\Users\{username}\Documents\Meeting_Notes.txt"},
        {"name": "Client_Proposal.docx", "path": r"C:\Users\{username}\Documents\Client_Proposal.docx"},
        {"name": "Policy_Document.pdf", "path": r"C:\Users\{username}\Documents\Policy_Document.pdf"},
        {"name": "Expense_Report.xlsx", "path": r"C:\Users\{username}\Documents\Expense_Report.xlsx"},
        {"name": "Documents", "path": r"C:\Users\{username}\Documents", "is_dir": True},
    ],
    "developer": [
        {"name": "README.md", "path": r"C:\Users\{username}\Documents\README.md"},
        {"name": "ARCHITECTURE.md", "path": r"C:\Users\{username}\Documents\ARCHITECTURE.md"},
        {"name": "Sprint_Notes.txt", "path": r"C:\Users\{username}\Documents\Sprint_Notes.txt"},
        {"name": "requirements.txt", "path": r"C:\Users\{username}\Documents\requirements.txt"},
        {"name": "config.json", "path": r"C:\Users\{username}\Documents\config.json"},
        {"name": "API_Spec.docx", "path": r"C:\Users\{username}\Documents\API_Spec.docx"},
        {"name": "source", "path": r"C:\Users\{username}\source\repos", "is_dir": True},
        {"name": "Documents", "path": r"C:\Users\{username}\Documents", "is_dir": True},
    ],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RecentItemsError(Exception):
    """Raised when Recent Items generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RecentItemsService(BaseService):
    """Creates Windows Recent Items shortcut files.

    Generates .lnk files in AppData/Roaming/Microsoft/Windows/Recent/
    to simulate recently accessed file history.

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
        return "RecentItemsService"

    def apply(self, context: dict) -> None:
        """Generate Recent Items for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.

        Raises:
            RecentItemsError: If Recent Items generation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)

        rng = Random(hash(seed + profile_type))
        recent_dir = (
            Path("Users") / username / "AppData" / "Roaming"
            / "Microsoft" / "Windows" / "Recent"
        )
        created_files = 0

        try:
            # Ensure Recent directory exists
            full_recent_dir = self._mount.resolve(str(recent_dir))
            full_recent_dir.mkdir(parents=True, exist_ok=True)

            # Also create AutomaticDestinations and CustomDestinations
            for subdir in ["AutomaticDestinations", "CustomDestinations"]:
                dest_dir = full_recent_dir / subdir
                dest_dir.mkdir(exist_ok=True)

            # Get files for this profile
            recent_files = _PROFILE_RECENT_FILES.get(profile_type, [])

            for file_spec in recent_files:
                # Skip some files randomly
                if rng.random() < 0.1:
                    continue

                name = file_spec["name"]
                target_path = file_spec["path"].replace("{username}", username)
                is_dir = file_spec.get("is_dir", False)

                # Create .lnk filename
                lnk_filename = f"{name}.lnk"
                lnk_path = recent_dir / lnk_filename

                # Generate LNK content
                content = self._create_lnk_file(
                    target_path, is_dir, rng
                )

                self._write_file(lnk_path, content)
                created_files += 1

            # Create some Jump List files in AutomaticDestinations
            self._create_jump_lists(recent_dir / "AutomaticDestinations", rng)
            # Also populate CustomDestinations with jump list stubs
            self._create_jump_lists(recent_dir / "CustomDestinations", rng)

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_recent_items",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })

            logger.info(
                "Generated %d Recent Items for user '%s'",
                created_files, username,
            )

        except Exception as exc:
            logger.error("Failed to generate Recent Items: %s", exc)
            raise RecentItemsError(
                f"Recent Items generation failed: {exc}"
            ) from exc

    def _write_file(
        self,
        rel_path: Path,
        content: bytes,
        event_type: str = "recent",
    ) -> None:
        """Write file content to the mounted filesystem and apply timestamps.

        Args:
            rel_path: Path relative to mount root.
            content: Binary content to write.
            event_type: Event type for timestamp generation.
        """
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        # Apply realistic timestamps from the timeline
        self._apply_timestamps(full_path, event_type)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
            "timestamp_event": event_type,
        })

    def _apply_timestamps(self, path: Path, event_type: str) -> None:
        """Apply created/modified/accessed timestamps from the timestamp service.

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

    def _create_lnk_file(
        self,
        target_path: str,
        is_directory: bool,
        rng: Random,
    ) -> bytes:
        """Create a Windows .lnk shortcut file.

        Args:
            target_path: Full path to the target file/folder.
            is_directory: True if target is a directory.
            rng: Random number generator.

        Returns:
            Binary content for the .lnk file.
        """
        # Get timestamps
        ts = self._ts.get_timestamp("recent")
        created = ts["created"]
        modified = ts["modified"]
        accessed = ts["accessed"]

        # Build header (76 bytes)
        header = bytearray(76)
        header[0:20] = _LNK_MAGIC

        # Link flags
        flags = (
            _LINK_FLAG_HAS_LINK_INFO |
            _LINK_FLAG_HAS_RELATIVE_PATH |
            _LINK_FLAG_IS_UNICODE
        )
        struct.pack_into("<I", header, 20, flags)

        # File attributes
        attrs = _FILE_ATTRIBUTE_DIRECTORY if is_directory else _FILE_ATTRIBUTE_ARCHIVE
        struct.pack_into("<I", header, 24, attrs)

        # Timestamps (FILETIME format)
        struct.pack_into("<Q", header, 28, self._ts.datetime_to_filetime(created))
        struct.pack_into("<Q", header, 36, self._ts.datetime_to_filetime(accessed))
        struct.pack_into("<Q", header, 44, self._ts.datetime_to_filetime(modified))

        # File size (0 for dirs)
        struct.pack_into("<I", header, 52, 0 if is_directory else rng.randint(1024, 1048576))

        # Icon index
        struct.pack_into("<i", header, 56, 0)

        # Show command (SW_SHOWNORMAL = 1)
        struct.pack_into("<I", header, 60, 1)

        # Hot key info (none)
        struct.pack_into("<H", header, 64, 0)

        # Reserved
        header[66:76] = bytes(10)

        # Build LinkInfo structure
        link_info = self._build_link_info(target_path)

        # Build StringData (relative path)
        filename = Path(target_path).name
        string_data = self._build_string_data(filename)

        return bytes(header) + link_info + string_data

    def _build_link_info(self, target_path: str) -> bytes:
        """Build the LinkInfo structure.

        Args:
            target_path: Full path to the target.

        Returns:
            Binary LinkInfo structure.
        """
        # LinkInfo header
        link_info_size = 28  # Minimum header size

        # Local base path
        local_path = target_path.encode("utf-8") + b"\x00"

        # Common network relative link (not used)
        total_size = link_info_size + len(local_path) + 16

        link_info = bytearray(total_size)

        # LinkInfoSize
        struct.pack_into("<I", link_info, 0, total_size)

        # LinkInfoHeaderSize
        struct.pack_into("<I", link_info, 4, 28)

        # LinkInfoFlags (VolumeIDAndLocalBasePath)
        struct.pack_into("<I", link_info, 8, 0x01)

        # VolumeIDOffset
        struct.pack_into("<I", link_info, 12, 28)

        # LocalBasePathOffset
        struct.pack_into("<I", link_info, 16, 28 + 16)

        # Build minimal VolumeID
        volume_id = bytearray(16)
        struct.pack_into("<I", volume_id, 0, 16)  # VolumeIDSize
        struct.pack_into("<I", volume_id, 4, 3)   # DriveType (DRIVE_FIXED)
        struct.pack_into("<I", volume_id, 8, 0x12345678)  # DriveSerialNumber
        struct.pack_into("<I", volume_id, 12, 16)  # VolumeLabelOffset

        link_info[28:44] = volume_id
        link_info[44:44 + len(local_path)] = local_path

        return bytes(link_info)

    def _build_string_data(self, filename: str) -> bytes:
        """Build the StringData section.

        Args:
            filename: Target filename for relative path.

        Returns:
            Binary StringData.
        """
        # Just the relative path string
        name_utf16 = filename.encode("utf-16-le") + b"\x00\x00"
        count = len(name_utf16) // 2

        string_data = bytearray(2 + len(name_utf16))
        struct.pack_into("<H", string_data, 0, count)
        string_data[2:] = name_utf16

        return bytes(string_data)

    def _create_jump_lists(self, dest_dir: Path, rng: Random) -> None:
        """Create Jump List files in AutomaticDestinations.

        Args:
            dest_dir: AutomaticDestinations directory path.
            rng: Random number generator.
        """
        # Common application AppIDs
        app_ids = [
            "5d696d521de238c3",  # Chrome
            "f01b4d95cf55d32a",  # Explorer
            "9b9cdc69c1c24e2b",  # Notepad
            "7e4dca80246863e3",  # Control Panel
        ]

        full_dest_dir = self._mount.resolve(str(dest_dir))
        full_dest_dir.mkdir(parents=True, exist_ok=True)

        for app_id in app_ids:
            if rng.random() < 0.3:  # Skip some
                continue

            filename = f"{app_id}.automaticDestinations-ms"
            file_path = full_dest_dir / filename

            # Create minimal compound file structure
            content = self._create_minimal_jump_list(rng)
            file_path.write_bytes(content)

            # Apply realistic timestamps
            self._apply_timestamps(file_path, "recent")

            self._audit.log({
                "service": self.service_name,
                "operation": "create_jump_list",
                "path": str(file_path),
            })

    def _create_minimal_jump_list(self, rng: Random) -> bytes:
        """Create a minimal Jump List file.

        Args:
            rng: Random number generator.

        Returns:
            Binary content (simplified compound file).
        """
        # OLE/Compound File Binary Format header (simplified)
        header = bytearray(512)

        # Magic number
        header[0:8] = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])

        # Minor version
        struct.pack_into("<H", header, 24, 0x003E)

        # Major version
        struct.pack_into("<H", header, 26, 0x0003)

        # Byte order (little-endian)
        struct.pack_into("<H", header, 28, 0xFFFE)

        # Sector size power (512 bytes = 2^9)
        struct.pack_into("<H", header, 30, 9)

        # Fill rest with pattern
        for i in range(48, 512):
            header[i] = rng.randint(0, 255)

        # Add some data sectors
        data_size = rng.randint(512, 4096)
        data = bytes([rng.randint(0, 255) for _ in range(data_size)])

        return bytes(header) + data
