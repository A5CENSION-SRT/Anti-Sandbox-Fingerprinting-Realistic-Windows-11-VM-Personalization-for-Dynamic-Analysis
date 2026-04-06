"""Windows Prefetch file generator.

Creates Prefetch (.pf) files in Windows/Prefetch/ that simulate application
launch history. Prefetch files have a specific binary format that Windows
uses to optimize application startup.

The format includes:
- Header with magic signature ("SCCA" for Win10+)
- File metrics and trace chains
- Filename strings
- Volume information
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import struct
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Tuple

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

# Prefetch magic signature for Windows 10/11
_PREFETCH_MAGIC = b"SCCA"  # Standard Compressed Cache Accelerator
_PREFETCH_VERSION_WIN10 = 30

# Profile-specific applications — paths MUST match InstalledAppsStub
# definitions for cross-reference coherence.
_PROFILE_APPS: Dict[str, List[Dict[str, Any]]] = {
    "office_user": [
        {"name": "OUTLOOK.EXE", "path": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE", "runs": (50, 200)},
        {"name": "WINWORD.EXE", "path": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE", "runs": (30, 150)},
        {"name": "EXCEL.EXE", "path": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE", "runs": (20, 100)},
        {"name": "POWERPNT.EXE", "path": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE", "runs": (10, 50)},
        {"name": "MS-TEAMS.EXE", "path": r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe", "runs": (100, 500)},
        {"name": "CHROME.EXE", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "runs": (200, 800)},
        {"name": "MSEDGE.EXE", "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "runs": (50, 200)},
    ],
    "developer": [
        {"name": "CODE.EXE", "path": r"C:\Users\{username}\AppData\Local\Programs\Microsoft VS Code\Code.exe", "runs": (200, 800)},
        {"name": "DOCKER DESKTOP.EXE", "path": r"C:\Program Files\Docker\Docker\Docker Desktop.exe", "runs": (50, 200)},
        {"name": "WT.EXE", "path": r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal\wt.exe", "runs": (300, 1000)},
        {"name": "GIT.EXE", "path": r"C:\Program Files\Git\cmd\git.exe", "runs": (100, 500)},
        {"name": "NODE.EXE", "path": r"C:\Program Files\nodejs\node.exe", "runs": (50, 200)},
        {"name": "CHROME.EXE", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "runs": (200, 800)},
        {"name": "MSEDGE.EXE", "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "runs": (50, 200)},
    ],
    "home_user": [
        {"name": "CHROME.EXE", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "runs": (300, 1000)},
        {"name": "MSEDGE.EXE", "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "runs": (100, 400)},
        {"name": "SPOTIFY.EXE", "path": r"C:\Users\{username}\AppData\Roaming\Spotify\Spotify.exe", "runs": (100, 500)},
        {"name": "VLC.EXE", "path": r"C:\Program Files\VideoLAN\VLC\vlc.exe", "runs": (30, 150)},
        {"name": "ONEDRIVE.EXE", "path": r"C:\Program Files\Microsoft OneDrive\OneDrive.exe", "runs": (50, 200)},
    ],
}

# Common system applications for all profiles
_COMMON_APPS: List[Dict[str, Any]] = [
    {"name": "EXPLORER.EXE", "path": r"C:\Windows\explorer.exe", "runs": (500, 2000)},
    {"name": "DLLHOST.EXE", "path": r"C:\Windows\System32\dllhost.exe", "runs": (100, 500)},
    {"name": "SVCHOST.EXE", "path": r"C:\Windows\System32\svchost.exe", "runs": (200, 1000)},
    {"name": "TASKHOSTW.EXE", "path": r"C:\Windows\System32\taskhostw.exe", "runs": (50, 200)},
    {"name": "RUNTIMEBROKER.EXE", "path": r"C:\Windows\System32\RuntimeBroker.exe", "runs": (100, 400)},
    {"name": "SEARCHHOST.EXE", "path": r"C:\Windows\SystemApps\SearchHost.exe", "runs": (50, 200)},
    {"name": "NOTEPAD.EXE", "path": r"C:\Windows\System32\notepad.exe", "runs": (20, 100)},
    {"name": "CMD.EXE", "path": r"C:\Windows\System32\cmd.exe", "runs": (10, 50)},
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PrefetchError(Exception):
    """Raised when Prefetch file generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PrefetchService(BaseService):
    """Creates Windows Prefetch files for simulated application history.

    Generates .pf files in Windows/Prefetch/ with realistic binary structure
    reflecting profile-specific application usage patterns.

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
        return "PrefetchService"

    def apply(self, context: dict) -> None:
        """Generate Prefetch files for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.
                * ``timeline_days`` (int) — days of history.

        Raises:
            PrefetchError: If Prefetch generation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)
        timeline_days = context.get("timeline_days", 90)

        rng = Random(hash(seed + profile_type))
        prefetch_dir = Path("Windows") / "Prefetch"
        created_files = 0

        try:
            # Ensure Prefetch directory exists
            full_prefetch = self._mount.resolve(str(prefetch_dir))
            full_prefetch.mkdir(parents=True, exist_ok=True)

            # Get apps for this profile
            apps = _COMMON_APPS.copy()
            apps.extend(_PROFILE_APPS.get(profile_type, []))

            for app_spec in apps:
                app_name = app_spec["name"]
                app_path = app_spec["path"].replace("{username}", username)
                run_range = app_spec.get("runs", (10, 100))

                # Calculate hash for prefetch filename
                pf_hash = self._calculate_prefetch_hash(app_path)
                pf_filename = f"{app_name}-{pf_hash:08X}.pf"
                pf_path = prefetch_dir / pf_filename

                # Generate run count
                run_count = rng.randint(*run_range)

                # Generate prefetch content
                content = self._create_prefetch_file(
                    app_name, app_path, run_count, rng, timeline_days
                )

                self._write_file(pf_path, content)
                created_files += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_prefetch_files",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })

            logger.info(
                "Generated %d Prefetch files for profile '%s'",
                created_files, profile_type,
            )

        except Exception as exc:
            logger.error("Failed to generate Prefetch files: %s", exc)
            raise PrefetchError(f"Prefetch generation failed: {exc}") from exc

    def _write_file(self, rel_path: Path, content: bytes) -> None:
        """Write file content to the mounted filesystem."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        # Apply realistic timestamps (critical for hour-distribution realism).
        self._apply_timestamps(full_path, "prefetch")

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

    def _calculate_prefetch_hash(self, path: str) -> int:
        """Calculate the Prefetch hash for an executable path.

        Windows uses a specific hash algorithm for Prefetch filenames.
        This is a simplified version that produces consistent hashes.

        Args:
            path: Full path to the executable.

        Returns:
            32-bit hash value.
        """
        # Normalize path to uppercase
        normalized = path.upper()
        # Use CRC32-like hash (simplified)
        hash_bytes = hashlib.md5(normalized.encode("utf-16-le")).digest()
        return struct.unpack("<I", hash_bytes[:4])[0]

    def _create_prefetch_file(
        self,
        app_name: str,
        app_path: str,
        run_count: int,
        rng: Random,
        timeline_days: int,
    ) -> bytes:
        """Create a Prefetch file with Windows 10/11 format.

        Args:
            app_name: Application executable name.
            app_path: Full path to executable.
            run_count: Number of times app was run.
            rng: Random number generator.
            timeline_days: Timeline range for last run timestamp.

        Returns:
            Binary content for the .pf file.
        """
        # Generate timestamps
        ts = self._ts.get_timestamp("prefetch")
        last_run = ts["modified"]
        last_run_filetime = self._ts.datetime_to_filetime(last_run)

        # Build header (simplified Win10 format - 84 bytes)
        header = bytearray(84)

        # Version and magic
        struct.pack_into("<I", header, 0, _PREFETCH_VERSION_WIN10)
        header[4:8] = _PREFETCH_MAGIC

        # File size (placeholder - will update)
        total_size = 512  # Minimum size
        struct.pack_into("<I", header, 12, total_size)

        # Executable name (60 bytes, UTF-16LE, null-padded)
        exe_name_bytes = app_name.encode("utf-16-le")[:58]
        header[16:16 + len(exe_name_bytes)] = exe_name_bytes

        # Hash
        pf_hash = self._calculate_prefetch_hash(app_path)
        struct.pack_into("<I", header, 76, pf_hash)

        # Run count
        struct.pack_into("<I", header, 80, run_count)

        # Build file metrics section (simplified)
        metrics = bytearray(128)

        # Last run times (up to 8 timestamps, we'll use 1)
        struct.pack_into("<Q", metrics, 0, last_run_filetime)

        # Build volume info (simplified)
        volume_info = bytearray(104)

        # Volume path
        vol_path = b"C:\\\x00".ljust(32, b"\x00")
        volume_info[0:32] = vol_path

        # Volume serial and creation time
        struct.pack_into("<I", volume_info, 32, rng.randint(0x10000000, 0xFFFFFFFF))
        struct.pack_into("<Q", volume_info, 36, last_run_filetime - rng.randint(0, 10**16))

        # Combine all sections
        content = bytes(header) + bytes(metrics) + bytes(volume_info)

        # Pad to minimum size with pattern
        if len(content) < total_size:
            padding = bytes([0x00] * (total_size - len(content)))
            content += padding

        return content
