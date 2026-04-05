"""Communications application artifact generator.

Creates filesystem artifacts for communication tools (Teams, Slack, Discord,
Zoom) on a mounted Windows 11 image — configuration directories, cache stubs,
and log placeholders that sandbox detectors check for when assessing whether
a machine has real user activity.

Artifacts created
-----------------
* ``AppData/Roaming/Microsoft/Teams/`` — desktop config, storage.json
* ``AppData/Roaming/Slack/`` — config stub
* ``AppData/Roaming/discord/`` — config stub (home profile only)
* ``AppData/Roaming/Zoom/`` — data directory stub

Only created when relevant comms apps are present in ``installed_apps``.
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

# Mapping: app name → (AppData sub-path, list of files to create)
_COMMS_ARTIFACTS: Dict[str, Dict[str, Any]] = {
    "teams": {
        "base_dir": os.path.join("AppData", "Roaming", "Microsoft", "Teams"),
        "files": {
            "desktop-config.json": {
                "appIdleTimeoutSecs": 300,
                "currentWebLanguage": "en-us",
                "theme": "default",
                "isLoggedIn": True,
                "openAtLogin": True,
            },
            "storage.json": {
                "lastSessionEnd": "2025-03-10T17:00:00.000Z",
                "clientVersion": "24004.1309.2689.2246",
            },
        },
        "subdirs": ["Cache", "blob_storage", "databases", "IndexedDB"],
    },
    "slack": {
        "base_dir": os.path.join("AppData", "Roaming", "Slack"),
        "files": {
            "local-settings.json": {
                "isRelaunching": False,
                "lastLaunchTs": 1710090000,
                "theme": "dark",
            },
        },
        "subdirs": ["Cache", "Service Worker"],
    },
    "discord": {
        "base_dir": os.path.join("AppData", "Roaming", "discord"),
        "files": {
            "settings.json": {
                "DANGEROUS_ENABLE_DEVTOOLS_ONLY_ENABLE_IF_YOU_KNOW_WHAT_YOURE_DOING": False,
                "IS_MAXIMIZED": False,
                "IS_MINIMIZED": False,
                "WINDOW_BOUNDS": {"x": 100, "y": 100, "width": 1280, "height": 720},
            },
        },
        "subdirs": ["Cache", "blob_storage"],
    },
    "zoom": {
        "base_dir": os.path.join("AppData", "Roaming", "Zoom"),
        "files": {},
        "subdirs": ["data", "data/CrashDump"],
    },
}

# Which apps map to which profiles (used to filter irrelevant apps)
_PROFILE_COMMS: Dict[str, List[str]] = {
    "office_user": ["teams", "slack", "zoom"],
    "developer": ["teams", "slack", "discord"],
    "home_user": ["discord"],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CommsAppsError(Exception):
    """Raised when comms artifact creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CommsApps(BaseService):
    """Creates communication-app filesystem artifacts on the mounted image.

    For each comm app that appears in both the profile's ``installed_apps``
    and the relevant ``_PROFILE_COMMS`` mapping, creates the expected
    AppData directory tree, configuration JSON files, and cache sub-dirs.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger: Structured audit logging.
    """

    def __init__(self, mount_manager, audit_logger) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "CommsApps"

    def apply(self, context: dict) -> None:
        """Create comm-app artifacts for apps in the profile.

        Args:
            context: Runtime context dict.  Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str)
                * ``installed_apps`` (list[str])

        Raises:
            CommsAppsError: If file creation fails.
        """
        username = context.get("username", "default_user")
        profile = context.get("profile_type", "home_user")
        installed = set(context.get("installed_apps", []))

        profile_comms = _PROFILE_COMMS.get(profile, [])
        # Only create artifacts for apps present in both lists
        active_apps = [app for app in profile_comms if app in installed]

        if not active_apps:
            logger.debug("No comms apps in profile — skipping CommsApps")
            return

        user_root = os.path.join("Users", username)

        for app_name in active_apps:
            self._create_app_artifacts(user_root, app_name)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_comms_artifacts",
            "profile_type": profile,
            "username": username,
            "apps_created": active_apps,
        })

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _create_app_artifacts(self, user_root: str, app_name: str) -> None:
        """Create directories, config files, and subdirs for one app.

        Args:
            user_root: Relative user home (e.g. ``Users/jdoe``).
            app_name: Key into ``_COMMS_ARTIFACTS``.
        """
        spec = _COMMS_ARTIFACTS.get(app_name)
        if spec is None:
            return

        base = self._mount.resolve(
            os.path.join(user_root, spec["base_dir"]),
        )
        base.mkdir(parents=True, exist_ok=True)

        # Config files
        for filename, content in spec["files"].items():
            file_path = base / filename
            with open(file_path, "w", encoding="utf-8") as fh:
                json.dump(content, fh, indent=2)
            self._audit.log({
                "service": self.service_name,
                "operation": "create_config_file",
                "app": app_name,
                "path": str(file_path),
            })

        # Sub-directories
        for subdir in spec.get("subdirs", []):
            sub_path = base / subdir
            sub_path.mkdir(parents=True, exist_ok=True)
