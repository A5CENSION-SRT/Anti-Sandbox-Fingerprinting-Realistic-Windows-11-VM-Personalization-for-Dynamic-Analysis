"""Registry service for UserAssist entries (with ROT13 key encoding).

Windows Explorer records application execution counts and timestamps
under the ``UserAssist`` registry key in NTUSER.DAT.  The subkey names
are **ROT13-encoded** GUIDs, and the value names (program paths) are
also ROT13-encoded.  Each value holds a fixed-size binary structure
containing a run counter, focus count, focus time, and last-execution
FILETIME timestamp.

This module is a **pure operation builder** — it constructs
:class:`HiveOperation` lists and delegates execution to :class:`HiveWriter`.

Target hive paths
-----------------
* ``NTUSER.DAT``
    * ``Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\UserAssist\\{rot13_guid}\\Count``
        — ROT13(program_path) → UserAssistEntry binary struct

Binary format (UserAssist v5, Windows 7+)
------------------------------------------
Offset  Size  Field
 0       4    Session ID (DWORD)
 4       4    Run count (DWORD)
 8       4    Focus count (DWORD)
12       4    Focus time (DWORD, milliseconds)
16      44    Padding (zeros)
60       8    Last execution time (FILETIME, 100ns since 1601-01-01)
68       4    Always zero
─────────────
Total: 72 bytes
"""

from __future__ import annotations

import codecs
import hashlib
import logging
import struct
from typing import Any, Dict, List

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NTUSER_HIVE: str = "Users/{username}/NTUSER.DAT"

_USERASSIST_KEY: str = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
)

# Well-known UserAssist GUIDs (Windows 10/11)
# CEBFF5CD — executable file execution
_GUID_EXE: str = "{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}"
# F4E57C4B — shortcut file execution
_GUID_LNK: str = "{F4E57C4B-2036-45F0-A9AB-443BCFE33D9F}"

# UserAssist entry struct size (v5)
_ENTRY_SIZE: int = 72

# FILETIME epoch delta (seconds between 1601-01-01 and 1970-01-01)
_FILETIME_EPOCH_DELTA: int = 116444736000000000  # in 100ns ticks

# Default execution entries per profile type
_HOME_PROGRAMS: List[Dict[str, Any]] = [
    {
        "path": r"{F38BF404-1D43-42F2-9305-67DE0B28FC23}\spotify.exe",
        "run_count": 45,
        "focus_count": 120,
        "focus_time_ms": 7200000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        "run_count": 32,
        "focus_count": 88,
        "focus_time_ms": 5400000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "run_count": 156,
        "focus_count": 450,
        "focus_time_ms": 28800000,
        "guid": _GUID_EXE,
    },
]

_OFFICE_PROGRAMS: List[Dict[str, Any]] = [
    {
        "path": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
        "run_count": 220,
        "focus_count": 600,
        "focus_time_ms": 43200000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        "run_count": 85,
        "focus_count": 250,
        "focus_time_ms": 18000000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        "run_count": 110,
        "focus_count": 320,
        "focus_time_ms": 21600000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"Microsoft.WindowsTerminal_8wekyb3d8bbwe!App",
        "run_count": 15,
        "focus_count": 30,
        "focus_time_ms": 1800000,
        "guid": _GUID_EXE,
    },
]

_DEVELOPER_PROGRAMS: List[Dict[str, Any]] = [
    {
        "path": r"C:\Users\{username}\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        "run_count": 312,
        "focus_count": 900,
        "focus_time_ms": 72000000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "run_count": 95,
        "focus_count": 180,
        "focus_time_ms": 14400000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Git\git-bash.exe",
        "run_count": 245,
        "focus_count": 500,
        "focus_time_ms": 36000000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "run_count": 410,
        "focus_count": 1200,
        "focus_time_ms": 57600000,
        "guid": _GUID_EXE,
    },
    {
        "path": r"Microsoft.WindowsTerminal_8wekyb3d8bbwe!App",
        "run_count": 380,
        "focus_count": 700,
        "focus_time_ms": 54000000,
        "guid": _GUID_EXE,
    },
]

_PROFILE_PROGRAMS_MAP: Dict[str, List[Dict[str, Any]]] = {
    "home": _HOME_PROGRAMS,
    "office": _OFFICE_PROGRAMS,
    "developer": _DEVELOPER_PROGRAMS,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UserAssistError(Exception):
    """Raised when UserAssist operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class UserAssist(BaseService):
    """Writes UserAssist execution-tracking entries into NTUSER.DAT.

    Program paths are ROT13-encoded as Windows does.  Each entry contains
    a 72-byte binary struct with run counts, focus metrics, and a FILETIME
    timestamp.

    Dependencies (injected):
        hive_writer: Low-level hive read/write service.
        audit_logger: Shared audit logger for traceability.

    Args:
        hive_writer: ``HiveWriter`` instance for offline hive I/O.
        audit_logger: ``AuditLogger`` instance for recording operations.
    """

    def __init__(self, hive_writer: HiveWriter, audit_logger: Any) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "UserAssist"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type: str — one of "home", "office", "developer".
            username: str — the profile username.

        Raises:
            UserAssistError: If required keys are missing.
        """
        profile_type = context.get("profile_type")
        if profile_type is None:
            raise UserAssistError(
                "Missing required 'profile_type' in context"
            )
        username = context.get("username")
        if username is None:
            raise UserAssistError(
                "Missing required 'username' in context"
            )
        self.write_userassist(profile_type, username)

    # -- public API ---------------------------------------------------------

    def write_userassist(
        self,
        profile_type: str,
        username: str,
    ) -> None:
        """Build and execute UserAssist registry operations.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
            username: Profile username for NTUSER.DAT path resolution.

        Raises:
            UserAssistError: On invalid profile type or write failure.
        """
        programs = self._get_programs(profile_type)
        operations = self.build_operations(programs, username)

        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise UserAssistError(
                f"Failed to write UserAssist entries: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_userassist_complete",
            "profile_type": profile_type,
            "username": username,
            "programs_count": len(programs),
            "operations_count": len(operations),
        })
        logger.info(
            "Written %d UserAssist entries (%d ops) for '%s'",
            len(programs),
            len(operations),
            username,
        )

    def build_operations(
        self,
        programs: List[Dict[str, Any]],
        username: str,
    ) -> List[HiveOperation]:
        """Build all UserAssist registry operations.

        Args:
            programs: Program execution metadata dicts.
            username: Profile username for path/hive resolution.

        Returns:
            List of :class:`HiveOperation` ready for execution.
        """
        hive_path = _NTUSER_HIVE.replace("{username}", username)
        ops: List[HiveOperation] = []

        for prog in programs:
            path = prog["path"].replace("{username}", username)
            guid = prog["guid"]
            rot13_guid = self.rot13(guid)
            rot13_path = self.rot13(path)

            key_path = rf"{_USERASSIST_KEY}\{rot13_guid}\Count"
            entry_data = self.encode_entry(
                run_count=prog["run_count"],
                focus_count=prog["focus_count"],
                focus_time_ms=prog["focus_time_ms"],
                seed_name=username,
            )

            ops.append(HiveOperation(
                hive_path=hive_path,
                key_path=key_path,
                value_name=rot13_path,
                value_data=entry_data,
                value_type=RegistryValueType.REG_BINARY,
            ))

        return ops

    # -- encoding helpers ---------------------------------------------------

    @staticmethod
    def rot13(text: str) -> str:
        """Apply ROT13 transformation to a string.

        Only ASCII letters are rotated; digits, punctuation, braces,
        and backslashes pass through unchanged — matching Windows
        Explorer's UserAssist encoding behaviour.

        Args:
            text: Input string.

        Returns:
            ROT13-encoded string.
        """
        return codecs.encode(text, "rot_13")

    @staticmethod
    def encode_entry(
        run_count: int,
        focus_count: int,
        focus_time_ms: int,
        seed_name: str,
    ) -> bytes:
        """Encode a UserAssist v5 binary entry (72 bytes).

        The last-execution FILETIME is derived deterministically from
        *seed_name* + *run_count* so identical inputs always produce
        identical output.

        Args:
            run_count: Number of times the program was executed.
            focus_count: Number of times the program received focus.
            focus_time_ms: Total focus time in milliseconds.
            seed_name: Deterministic seed for timestamp derivation
                (typically the profile username).

        Returns:
            72-byte binary struct.
        """
        # Deterministic FILETIME
        seed = f"{seed_name}:{run_count}:{focus_count}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        # Place in ~2023 range
        base_filetime = 133200000000000000  # approx 2023-02-01
        offset = int(digest[:12], 16) % 315360000000000  # ~1 year range
        filetime = base_filetime + offset

        entry = bytearray(_ENTRY_SIZE)
        struct.pack_into("<I", entry, 0, 0)            # session_id
        struct.pack_into("<I", entry, 4, run_count)
        struct.pack_into("<I", entry, 8, focus_count)
        struct.pack_into("<I", entry, 12, focus_time_ms)
        # bytes 16–59: padding (already zeros)
        struct.pack_into("<Q", entry, 60, filetime)
        # bytes 68–71: always zero (already zeros)
        return bytes(entry)

    @staticmethod
    def decode_entry(data: bytes) -> Dict[str, Any]:
        """Decode a UserAssist v5 binary entry.

        Args:
            data: 72-byte binary struct.

        Returns:
            Dict with session_id, run_count, focus_count, focus_time_ms,
            filetime keys.

        Raises:
            UserAssistError: If data is wrong length.
        """
        if len(data) != _ENTRY_SIZE:
            raise UserAssistError(
                f"UserAssist entry must be {_ENTRY_SIZE} bytes, "
                f"got {len(data)}"
            )
        return {
            "session_id": struct.unpack_from("<I", data, 0)[0],
            "run_count": struct.unpack_from("<I", data, 4)[0],
            "focus_count": struct.unpack_from("<I", data, 8)[0],
            "focus_time_ms": struct.unpack_from("<I", data, 12)[0],
            "filetime": struct.unpack_from("<Q", data, 60)[0],
        }

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _get_programs(
        profile_type: str,
    ) -> List[Dict[str, Any]]:
        """Look up program execution entries for a profile type.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
                ``*_user`` aliases are accepted and normalized.

        Returns:
            List of program execution metadata dicts.

        Raises:
            UserAssistError: If profile type is unknown.
        """
        profile_key = profile_type.lower().removesuffix("_user")
        programs = _PROFILE_PROGRAMS_MAP.get(profile_key)
        if programs is None:
            valid = ", ".join(sorted(_PROFILE_PROGRAMS_MAP.keys()))
            raise UserAssistError(
                f"Unknown profile type '{profile_type}'. "
                f"Valid types: {valid}"
            )
        return programs
