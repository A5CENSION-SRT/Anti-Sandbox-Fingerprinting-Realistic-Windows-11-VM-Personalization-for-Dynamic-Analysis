"""User directory structure generator for Windows profiles.

Creates the standard Windows user directory tree under Users/{username}/,
including Desktop, Documents, Downloads, Pictures, Videos, Music, and the
AppData hierarchy (Local, LocalLow, Roaming).

This service is typically run early in the artifact generation pipeline
to ensure all subsequent services have their expected directory targets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Standard Windows 11 user directories (relative to Users/{username}/)
_USER_DIRS: List[str] = [
    "Desktop",
    "Documents",
    "Downloads",
    "Favorites",
    "Links",
    "Music",
    "Pictures",
    "Saved Games",
    "Searches",
    "Videos",
    "Contacts",
    "OneDrive",
    "3D Objects",
]

# AppData subdirectories
_APPDATA_DIRS: List[str] = [
    "AppData/Local",
    "AppData/LocalLow",
    "AppData/Roaming",
]

# Common AppData/Local subdirectories
_LOCAL_SUBDIRS: List[str] = [
    "Microsoft/Windows/History",
    "Microsoft/Windows/INetCache",
    "Microsoft/Windows/INetCookies",
    "Microsoft/Windows/Explorer",
    "Microsoft/Windows/Temporary Internet Files",
    "Microsoft/Windows/WER",
    "Microsoft/Windows/Notifications",
    "Microsoft/WindowsApps",
    "Packages",
    "Programs",
    "Temp",
    "VirtualStore",
]

# Common AppData/Roaming subdirectories
_ROAMING_SUBDIRS: List[str] = [
    "Microsoft/Windows/Start Menu/Programs",
    "Microsoft/Windows/Start Menu/Programs/Startup",
    "Microsoft/Windows/Recent",
    "Microsoft/Windows/SendTo",
    "Microsoft/Windows/Themes",
    "Microsoft/Windows/Libraries",
    "Microsoft/Windows/Network Shortcuts",
    "Microsoft/Windows/Printer Shortcuts",
    "Microsoft/Windows/Templates",
    "Microsoft/Credentials",
    "Microsoft/Crypto",
    "Microsoft/Protect",
    "Microsoft/SystemCertificates",
]

# Profile-specific directories
_PROFILE_DIRS: Dict[str, List[str]] = {
    "developer": [
        "source/repos",
        ".ssh",
        ".config",
        ".docker",
        ".kube",
        ".aws",
        ".azure",
        "go",
        ".npm",
        ".nuget",
    ],
    "office_user": [
        "Documents/Work",
        "Documents/Projects",
        "Documents/Reports",
    ],
    "home_user": [
        "Documents/Personal",
        "Pictures/Family",
        "Pictures/Vacations",
        "Videos/Home Movies",
    ],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UserDirectoryError(Exception):
    """Raised when user directory creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class UserDirectoryService(BaseService):
    """Creates the standard Windows user directory structure.

    Generates the complete Users/{username}/ tree including standard
    shell folders, AppData hierarchy, and profile-specific directories.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger: Structured audit logging.
    """

    def __init__(self, mount_manager, audit_logger) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "UserDirectoryService"

    def apply(self, context: dict) -> None:
        """Create the user directory structure.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.

        Raises:
            UserDirectoryError: If directory creation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")

        user_root = Path("Users") / username
        created_count = 0

        try:
            # Create base user directories
            for dir_name in _USER_DIRS:
                dir_path = user_root / dir_name
                self._create_dir(dir_path)
                created_count += 1

            # Create AppData hierarchy
            for appdata_dir in _APPDATA_DIRS:
                dir_path = user_root / appdata_dir
                self._create_dir(dir_path)
                created_count += 1

            # Create Local subdirectories
            for subdir in _LOCAL_SUBDIRS:
                dir_path = user_root / "AppData" / "Local" / subdir
                self._create_dir(dir_path)
                created_count += 1

            # Create Roaming subdirectories
            for subdir in _ROAMING_SUBDIRS:
                dir_path = user_root / "AppData" / "Roaming" / subdir
                self._create_dir(dir_path)
                created_count += 1

            # Create profile-specific directories
            profile_dirs = _PROFILE_DIRS.get(profile_type, [])
            for subdir in profile_dirs:
                dir_path = user_root / subdir
                self._create_dir(dir_path)
                created_count += 1

            # Create NTUSER.DAT placeholder location
            self._create_dir(user_root)

            self._audit.log({
                "service": self.service_name,
                "operation": "create_user_directory_tree",
                "username": username,
                "profile_type": profile_type,
                "directories_created": created_count,
            })

            logger.info(
                "Created %d directories for user '%s' (%s profile)",
                created_count, username, profile_type,
            )

        except Exception as exc:
            logger.error(
                "Failed to create user directory structure: %s", exc
            )
            raise UserDirectoryError(
                f"Failed to create user directories for {username}: {exc}"
            ) from exc

    def _create_dir(self, rel_path: Path) -> None:
        """Create a directory under the mount root.

        Args:
            rel_path: Path relative to mount root.
        """
        full_path = self._mount.resolve(str(rel_path))
        full_path.mkdir(parents=True, exist_ok=True)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_directory",
            "path": str(full_path),
        })

    def create_additional_dirs(
        self,
        username: str,
        directories: List[str],
    ) -> None:
        """Create additional directories under user root.

        Utility method for other services to request directory creation.

        Args:
            username: Windows username.
            directories: List of paths relative to Users/{username}/.
        """
        user_root = Path("Users") / username
        for dir_name in directories:
            dir_path = user_root / dir_name
            self._create_dir(dir_path)
