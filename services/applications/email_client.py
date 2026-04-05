"""Email client artifact generator.

Creates Outlook / Windows Mail filesystem artifacts on a mounted Windows 11
image — account configuration files, cache directories, and profile stubs
that sandbox detectors expect on a machine with active email usage.

Artifacts created
-----------------
* ``AppData/Local/Microsoft/Outlook/`` — Outlook profile root with
  ``Outlook.pst`` placeholder and ``RoamCache/`` directory
* ``AppData/Local/Packages/microsoft.windowscommunicationsapps_.../``
  — Windows Mail LocalState directory stub
* ``AppData/Local/Microsoft/Outlook/16/`` — Outlook 16.0 cache dir

Only created when email-related apps (``outlook``, ``teams``) are present
in the profile's ``installed_apps``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from random import Random
from typing import Any, Dict, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OUTLOOK_DATA_DIR: str = os.path.join(
    "AppData", "Local", "Microsoft", "Outlook",
)
_OUTLOOK_ROAM_CACHE: str = os.path.join(
    "AppData", "Local", "Microsoft", "Outlook", "RoamCache",
)
_OUTLOOK_16_DIR: str = os.path.join(
    "AppData", "Local", "Microsoft", "Outlook", "16",
)
_WINDOWS_MAIL_DIR: str = os.path.join(
    "AppData", "Local", "Packages",
    "microsoft.windowscommunicationsapps_8wekyb3d8bbwe",
    "LocalState",
)

_EMAIL_APPS: frozenset[str] = frozenset({"outlook", "teams"})

# Profile-specific .pst file sizes (simulated via placeholder content)
_PST_SIZES: Dict[str, int] = {
    "office_user": 4096,   # 4KB stub
    "developer": 2048,
    "home_user": 1024,
}

# Outlook profile XML stub
_OUTLOOK_PROFILE_XML: str = """<?xml version="1.0" encoding="utf-8"?>
<OutlookProfile>
  <Account>
    <DisplayName>{display_name}</DisplayName>
    <SmtpAddress>{email}</SmtpAddress>
    <AccountType>Exchange</AccountType>
  </Account>
</OutlookProfile>
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EmailClientError(Exception):
    """Raised when email client artifact creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EmailClient(BaseService):
    """Creates email client filesystem artifacts on the mounted image.

    Generates Outlook profile directories, .pst placeholders, and cache
    structures so sandbox detection heuristics that check for email-client
    presence see genuine-looking activity traces.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger: Structured audit logging.
    """

    def __init__(self, mount_manager, audit_logger) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "EmailClient"

    def apply(self, context: dict) -> None:
        """Create email client artifacts if email apps are installed.

        Args:
            context: Runtime context dict.  Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str)
                * ``installed_apps`` (list[str])
                * ``organization`` (str) — used for email address.
                * ``computer_name`` (str) — RNG seed.

        Raises:
            EmailClientError: If file creation fails.
        """
        username = context.get("username", "default_user")
        profile = context.get("profile_type", "home_user")
        installed = set(context.get("installed_apps", []))
        org = context.get("organization", "personal")
        seed = context.get("computer_name", username)

        if not _EMAIL_APPS.intersection(installed):
            logger.debug("No email apps in profile — skipping EmailClient")
            return

        user_root = os.path.join("Users", username)
        artifact_count = 0

        self._create_outlook_dirs(user_root)
        artifact_count += 1

        self._create_pst_placeholder(user_root, profile)
        artifact_count += 1

        self._create_outlook_profile(user_root, username, org)
        artifact_count += 1

        self._create_windows_mail_stub(user_root)
        artifact_count += 1

        self._audit.log({
            "service": self.service_name,
            "operation": "create_email_artifacts",
            "profile_type": profile,
            "username": username,
            "artifact_groups": artifact_count,
        })

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _create_outlook_dirs(self, user_root: str) -> None:
        """Create Outlook directory skeleton."""
        for rel in (_OUTLOOK_DATA_DIR, _OUTLOOK_ROAM_CACHE, _OUTLOOK_16_DIR):
            full = self._mount.resolve(os.path.join(user_root, rel))
            full.mkdir(parents=True, exist_ok=True)

    def _create_pst_placeholder(self, user_root: str, profile: str) -> None:
        """Write a small Outlook.pst placeholder file."""
        pst_dir = self._mount.resolve(
            os.path.join(user_root, _OUTLOOK_DATA_DIR),
        )
        pst_path = pst_dir / "Outlook.pst"
        size = _PST_SIZES.get(profile, 1024)
        # Write a minimal header-like stub (not a real PST, but non-zero)
        pst_path.write_bytes(b"\x21\x42\x44\x4e" + b"\x00" * (size - 4))
        self._audit.log({
            "service": self.service_name,
            "operation": "create_pst",
            "path": str(pst_path),
            "size_bytes": size,
        })

    def _create_outlook_profile(
        self, user_root: str, username: str, org: str,
    ) -> None:
        """Write a simple Outlook profile XML stub."""
        profile_dir = self._mount.resolve(
            os.path.join(user_root, _OUTLOOK_16_DIR),
        )
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / "profile.xml"

        email_domain = "gmail.com" if org == "personal" else f"{org}.com"
        content = _OUTLOOK_PROFILE_XML.format(
            display_name=username,
            email=f"{username}@{email_domain}",
        )
        profile_path.write_text(content, encoding="utf-8")
        self._audit.log({
            "service": self.service_name,
            "operation": "create_outlook_profile",
            "path": str(profile_path),
        })

    def _create_windows_mail_stub(self, user_root: str) -> None:
        """Scaffold Windows Mail LocalState directory."""
        mail_dir = self._mount.resolve(
            os.path.join(user_root, _WINDOWS_MAIL_DIR),
        )
        mail_dir.mkdir(parents=True, exist_ok=True)
        # Touch a state marker file
        (mail_dir / "LocalState.db").touch(exist_ok=True)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_windows_mail_stub",
            "path": str(mail_dir),
        })
