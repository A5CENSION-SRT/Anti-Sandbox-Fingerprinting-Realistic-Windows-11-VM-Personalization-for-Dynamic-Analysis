"""Registry service for MRU (Most Recently Used) and RecentDocs entries.

Populates the NTUSER.DAT hive with recent-document lists that Windows
Explorer maintains — the ``RecentDocs`` key and its per-extension subkeys.

Each entry contains a binary ``MRUListEx`` (ordered DWORD array terminated
by ``0xFFFFFFFF``) and numbered values (``0``, ``1``, …) holding the
filename in a specific binary format (null-terminated UTF-16LE + padding).

This module is a **pure operation builder** — it constructs
:class:`HiveOperation` lists and delegates execution to :class:`HiveWriter`.

Target hive paths
-----------------
* ``NTUSER.DAT``
    * ``Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\RecentDocs``
        — MRUListEx, 0, 1, 2, …
    * ``Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\RecentDocs\\.docx``
        — MRUListEx, 0, 1, …  (per extension)
"""

from __future__ import annotations

import logging
import struct
from pathlib import PurePosixPath
from typing import Any, Dict, List, Sequence

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

_RECENTDOCS_KEY: str = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs"
)

# MRUListEx terminator
_MRULISTEX_TERMINATOR: bytes = struct.pack("<I", 0xFFFFFFFF)

# Default recent document sets per profile type
_HOME_RECENT_DOCS: List[str] = [
    "vacation_photos.jpg",
    "recipe_collection.pdf",
    "budget_2024.xlsx",
    "family_video.mp4",
    "shopping_list.txt",
]

_OFFICE_RECENT_DOCS: List[str] = [
    "Q4_Report_Final.docx",
    "Budget_FY2025.xlsx",
    "Meeting_Notes_Jan.docx",
    "Project_Timeline.xlsx",
    "HR_Policy_Update.pdf",
    "Team_Roster.xlsx",
    "Client_Proposal_v3.docx",
]

_DEVELOPER_RECENT_DOCS: List[str] = [
    "architecture_design.md",
    "api_spec_v2.yaml",
    "deployment_notes.txt",
    "database_schema.sql",
    "performance_results.csv",
    "README.md",
]

_PROFILE_DOCS_MAP: Dict[str, List[str]] = {
    "home": _HOME_RECENT_DOCS,
    "office": _OFFICE_RECENT_DOCS,
    "developer": _DEVELOPER_RECENT_DOCS,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MruRecentDocsError(Exception):
    """Raised when MRU/RecentDocs operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MruRecentDocs(BaseService):
    """Writes RecentDocs MRU entries into the NTUSER.DAT hive.

    Produces ``MRUListEx`` ordering values and per-document binary entries
    under the ``RecentDocs`` key, plus per-extension subkeys.

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
        return "MruRecentDocs"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type: str — one of "home", "office", "developer".
            username: str — the profile username.

        Raises:
            MruRecentDocsError: If required keys are missing.
        """
        profile_type = context.get("profile_type")
        if profile_type is None:
            raise MruRecentDocsError(
                "Missing required 'profile_type' in context"
            )
        username = context.get("username")
        if username is None:
            raise MruRecentDocsError(
                "Missing required 'username' in context"
            )
        self.write_recent_docs(profile_type, username)

    # -- public API ---------------------------------------------------------

    def write_recent_docs(
        self,
        profile_type: str,
        username: str,
    ) -> None:
        """Build and execute RecentDocs registry operations.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
            username: Profile username for NTUSER.DAT path resolution.

        Raises:
            MruRecentDocsError: On invalid profile type or write failure.
        """
        filenames = self._get_recent_docs(profile_type)
        operations = self.build_operations(filenames, username)

        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise MruRecentDocsError(
                f"Failed to write recent docs: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_recent_docs_complete",
            "profile_type": profile_type,
            "username": username,
            "documents_count": len(filenames),
            "operations_count": len(operations),
        })
        logger.info(
            "Written %d recent docs (%d ops) for '%s'",
            len(filenames),
            len(operations),
            username,
        )

    def build_operations(
        self,
        filenames: List[str],
        username: str,
    ) -> List[HiveOperation]:
        """Build all RecentDocs registry operations.

        Creates:
        1. Main ``RecentDocs`` key with MRUListEx + numbered entries.
        2. Per-extension subkeys (e.g. ``.docx``) with their own
           MRUListEx + entries.

        Args:
            filenames: Ordered list of recent document filenames.
            username: Profile username for hive path.

        Returns:
            List of :class:`HiveOperation` ready for execution.
        """
        hive_path = _NTUSER_HIVE.replace("{username}", username)
        ops: List[HiveOperation] = []

        # 1. Main RecentDocs key
        ops.extend(
            self._build_mru_key_ops(
                hive_path, _RECENTDOCS_KEY, filenames
            )
        )

        # 2. Per-extension subkeys
        by_ext = self._group_by_extension(filenames)
        for ext, ext_files in sorted(by_ext.items()):
            ext_key = rf"{_RECENTDOCS_KEY}\{ext}"
            ops.extend(
                self._build_mru_key_ops(hive_path, ext_key, ext_files)
            )

        return ops

    # -- operation builders -------------------------------------------------

    def _build_mru_key_ops(
        self,
        hive_path: str,
        key_path: str,
        filenames: List[str],
    ) -> List[HiveOperation]:
        """Build MRUListEx + numbered value ops for one key.

        Args:
            hive_path: Relative hive file path.
            key_path: Registry key path.
            filenames: Ordered filenames for this key.

        Returns:
            List of operations (MRUListEx + one per filename).
        """
        ops: List[HiveOperation] = []

        # MRUListEx — ordered DWORD array + terminator
        mru_data = self.encode_mrulistex(len(filenames))
        ops.append(HiveOperation(
            hive_path=hive_path,
            key_path=key_path,
            value_name="MRUListEx",
            value_data=mru_data,
            value_type=RegistryValueType.REG_BINARY,
        ))

        # Numbered values: "0", "1", "2", ...
        for idx, filename in enumerate(filenames):
            entry_data = self.encode_recentdocs_entry(filename)
            ops.append(HiveOperation(
                hive_path=hive_path,
                key_path=key_path,
                value_name=str(idx),
                value_data=entry_data,
                value_type=RegistryValueType.REG_BINARY,
            ))

        return ops

    # -- encoding helpers ---------------------------------------------------

    @staticmethod
    def encode_mrulistex(count: int) -> bytes:
        """Encode an MRUListEx binary value.

        The MRUListEx is an array of little-endian DWORDs representing
        the ordering of entries (most-recent first), terminated by
        ``0xFFFFFFFF``.

        Args:
            count: Number of entries.

        Returns:
            Raw bytes for the MRUListEx value.
        """
        parts = [struct.pack("<I", i) for i in range(count)]
        parts.append(_MRULISTEX_TERMINATOR)
        return b"".join(parts)

    @staticmethod
    def encode_recentdocs_entry(filename: str) -> bytes:
        """Encode a RecentDocs numbered entry.

        Windows stores each entry as:
        1. Filename in UTF-16LE, null-terminated.
        2. 8 bytes of padding/metadata (zeros).

        This simplified encoding matches the structure that forensic
        tools (RegRipper, Registry Explorer) expect.

        Args:
            filename: The document filename.

        Returns:
            Raw bytes for the entry value.
        """
        name_bytes = filename.encode("utf-16-le") + b"\x00\x00"
        padding = b"\x00" * 8
        return name_bytes + padding

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _get_recent_docs(profile_type: str) -> List[str]:
        """Look up recent document filenames for a profile type.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
                ``*_user`` aliases are accepted and normalized.

        Returns:
            Ordered list of filenames.

        Raises:
            MruRecentDocsError: If profile type is unknown.
        """
        profile_key = profile_type.lower().removesuffix("_user")
        docs = _PROFILE_DOCS_MAP.get(profile_key)
        if docs is None:
            valid = ", ".join(sorted(_PROFILE_DOCS_MAP.keys()))
            raise MruRecentDocsError(
                f"Unknown profile type '{profile_type}'. "
                f"Valid types: {valid}"
            )
        return docs

    @staticmethod
    def _group_by_extension(
        filenames: List[str],
    ) -> Dict[str, List[str]]:
        """Group filenames by their file extension.

        Args:
            filenames: List of filenames.

        Returns:
            Dict mapping extension (e.g. ``".docx"``) to filenames.
        """
        by_ext: Dict[str, List[str]] = {}
        for name in filenames:
            _, dot, ext = name.rpartition(".")
            if dot:
                key = f".{ext}"
            else:
                key = ".unknown"
            by_ext.setdefault(key, []).append(name)
        return by_ext
