"""Office application artifact generator.

Creates realistic Microsoft Office artifact files on the mounted Windows 11
image — MRU (Most Recently Used) files, template caches, and configuration
directories for Word, Excel, PowerPoint, and Outlook.  These artifacts resist
sandbox-detection heuristics that check for *empty* Office installations or
missing user-activity traces.

Artifacts created
-----------------
* ``AppData/Roaming/Microsoft/Office/Recent/`` — recent document shortcuts
* ``AppData/Roaming/Microsoft/Templates/`` — Normal.dotm template stub
* ``AppData/Local/Microsoft/Office/16.0/OfficeFileCache/`` — cache directory
* ``Documents/*.docx``, ``Documents/*.xlsx`` — zero-byte placeholder files
  matching the profile's expected document types

This module is a **pure operation builder** — it constructs filesystem paths
and delegates all I/O to :class:`MountManager` and :mod:`pathlib`, logging
every write through the injected :class:`AuditLogger`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OFFICE_RECENT_DIR: str = os.path.join(
    "AppData", "Roaming", "Microsoft", "Office", "Recent",
)
_OFFICE_TEMPLATES_DIR: str = os.path.join(
    "AppData", "Roaming", "Microsoft", "Templates",
)
_OFFICE_CACHE_DIR: str = os.path.join(
    "AppData", "Local", "Microsoft", "Office", "16.0", "OfficeFileCache",
)

# Document types per profile
_PROFILE_DOCUMENTS: Dict[str, List[Dict[str, str]]] = {
    "office_user": [
        {"name": "Q4_Report_2024.docx", "subdir": "Documents"},
        {"name": "Budget_FY2025.xlsx", "subdir": "Documents"},
        {"name": "Team_Meeting_Agenda.docx", "subdir": "Documents"},
        {"name": "Project_Timeline.xlsx", "subdir": "Documents"},
        {"name": "Annual_Review_Presentation.pptx", "subdir": "Documents"},
        {"name": "Client_Proposal_Draft.docx", "subdir": "Documents"},
        {"name": "Expense_Report_March.xlsx", "subdir": "Documents"},
        {"name": "Sales_Dashboard.xlsx", "subdir": "Documents"},
    ],
    "developer": [
        {"name": "API_Documentation.docx", "subdir": "Documents"},
        {"name": "Sprint_Tracking.xlsx", "subdir": "Documents"},
        {"name": "Architecture_Design.docx", "subdir": "Documents"},
        {"name": "Test_Results_Q1.xlsx", "subdir": "Documents"},
    ],
    "home_user": [
        {"name": "Recipe_Collection.docx", "subdir": "Documents"},
        {"name": "Household_Budget.xlsx", "subdir": "Documents"},
        {"name": "Vacation_Itinerary.docx", "subdir": "Documents"},
    ],
}

# Template files that Office installs create
_TEMPLATE_FILES: List[str] = [
    "Normal.dotm",
    "~$Normal.dotm",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OfficeArtifactsError(Exception):
    """Raised when Office artifact creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OfficeArtifacts(BaseService):
    """Creates Microsoft Office filesystem artifacts on the mounted image.

    Generates template caches, MRU recent-document directories, and
    placeholder document files appropriate to the profile type.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger: Structured audit logging.
    """

    def __init__(self, mount_manager, audit_logger) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "OfficeArtifacts"

    def apply(self, context: dict) -> None:
        """Create Office artifact directories and files.

        Args:
            context: Runtime context dict.  Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.
                * ``installed_apps`` (list[str]) — if no Office apps, skip.

        Raises:
            OfficeArtifactsError: If file creation fails.
        """
        username = context.get("username", "default_user")
        profile = context.get("profile_type", "home_user")
        installed = context.get("installed_apps", [])
        seed = context.get("computer_name", username)

        # Only create Office artifacts if Office apps are in the profile
        office_apps = {"outlook", "teams", "excel", "word", "powerpoint"}
        if not office_apps.intersection(set(installed)):
            logger.debug("No Office apps in profile — skipping OfficeArtifacts")
            return

        rng = Random(hash(seed + profile))
        user_root = os.path.join("Users", username)

        self._create_office_dirs(user_root)
        self._create_templates(user_root)
        self._create_recent_shortcuts(user_root, profile, rng)
        self._create_documents(user_root, profile)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_office_artifacts",
            "profile_type": profile,
            "username": username,
        })

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _create_office_dirs(self, user_root: str) -> None:
        """Create the standard Office directory skeleton."""
        for rel in (_OFFICE_RECENT_DIR, _OFFICE_TEMPLATES_DIR, _OFFICE_CACHE_DIR):
            full = self._mount.resolve(os.path.join(user_root, rel))
            full.mkdir(parents=True, exist_ok=True)

    def _create_templates(self, user_root: str) -> None:
        """Create template stubs (Normal.dotm etc.)."""
        tpl_dir = self._mount.resolve(
            os.path.join(user_root, _OFFICE_TEMPLATES_DIR),
        )
        for name in _TEMPLATE_FILES:
            (tpl_dir / name).touch(exist_ok=True)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_templates",
            "path": str(tpl_dir),
            "file_count": len(_TEMPLATE_FILES),
        })

    def _create_recent_shortcuts(
        self, user_root: str, profile: str, rng: Random,
    ) -> None:
        """Create .lnk-like recent-document stubs in the Office Recent dir."""
        recent_dir = self._mount.resolve(
            os.path.join(user_root, _OFFICE_RECENT_DIR),
        )
        docs = _PROFILE_DOCUMENTS.get(profile, _PROFILE_DOCUMENTS["home_user"])
        for doc in docs:
            # Windows Recent entries are .lnk files; we create stubs
            lnk_name = doc["name"] + ".lnk"
            (recent_dir / lnk_name).touch(exist_ok=True)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_recent_shortcuts",
            "path": str(recent_dir),
            "shortcut_count": len(docs),
        })

    def _create_documents(self, user_root: str, profile: str) -> None:
        """Create zero-byte placeholder document files."""
        docs = _PROFILE_DOCUMENTS.get(profile, _PROFILE_DOCUMENTS["home_user"])
        for doc in docs:
            doc_dir = self._mount.resolve(
                os.path.join(user_root, doc["subdir"]),
            )
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / doc["name"]).touch(exist_ok=True)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_documents",
            "document_count": len(docs),
        })
