"""Developer environment artifact generator.

Creates filesystem artifacts associated with common developer tools on a
mounted Windows 11 image — config files, project directories, and cache
structures that sandbox detectors expect to see on a real developer machine.

Artifacts created
-----------------
* ``Users/<user>/.gitconfig`` — Git global config with realistic identity
* ``Users/<user>/.ssh/`` — SSH directory with ``known_hosts`` stub
* ``Users/<user>/AppData/Roaming/Code/User/settings.json`` — VS Code settings
* ``Users/<user>/source/repos/`` — Visual Studio project root (empty dirs)
* ``Users/<user>/.docker/config.json`` — Docker config stub
* ``Users/<user>/AppData/Local/Programs/Python/`` — Python install marker

Only created when developer tools (``vscode``, ``docker``, ``git``, etc.)
are present in the profile's ``installed_apps``.
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

_DEV_APPS: frozenset[str] = frozenset({
    "vscode", "docker", "git", "terminal", "vim", "intellij",
    "sublime", "pycharm", "webstorm", "neovim",
})

_VSCODE_SETTINGS: Dict[str, Any] = {
    "editor.fontSize": 14,
    "editor.tabSize": 4,
    "editor.formatOnSave": True,
    "editor.minimap.enabled": True,
    "terminal.integrated.defaultProfile.windows": "PowerShell",
    "files.autoSave": "afterDelay",
    "files.autoSaveDelay": 1000,
    "workbench.colorTheme": "Default Dark Modern",
    "python.defaultInterpreterPath": "python",
    "git.autofetch": True,
}

_DOCKER_CONFIG: Dict[str, Any] = {
    "auths": {},
    "credsStore": "desktop",
    "currentContext": "default",
}

# Sample project directories per profile
_PROJECT_DIRS: Dict[str, List[str]] = {
    "developer": [
        os.path.join("source", "repos", "webapp-frontend"),
        os.path.join("source", "repos", "api-service"),
        os.path.join("source", "repos", "infrastructure"),
    ],
    "office_user": [
        os.path.join("source", "repos", "data-reports"),
    ],
    "home_user": [],
}

_KNOWN_HOSTS_ENTRIES: List[str] = [
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
    "gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DevEnvironmentError(Exception):
    """Raised when developer artifact creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DevEnvironment(BaseService):
    """Creates developer-tool filesystem artifacts on the mounted image.

    Produces configuration files and directory structures for Git, VS Code,
    Docker, and SSH that are consistent with the profile's installed
    applications list.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger: Structured audit logging.
    """

    def __init__(self, mount_manager, audit_logger) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "DevEnvironment"

    def apply(self, context: dict) -> None:
        """Create developer artifacts if dev tools are installed.

        Args:
            context: Runtime context dict.  Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str)
                * ``installed_apps`` (list[str])
                * ``computer_name`` (str) — RNG seed.
                * ``organization`` (str) — used in gitconfig.

        Raises:
            DevEnvironmentError: If file creation fails.
        """
        username = context.get("username", "default_user")
        profile = context.get("profile_type", "home_user")
        installed = set(context.get("installed_apps", []))
        seed = context.get("computer_name", username)
        org = context.get("organization", "personal")

        # Only proceed if at least one dev tool is installed
        if not _DEV_APPS.intersection(installed):
            logger.debug("No dev tools in profile — skipping DevEnvironment")
            return

        rng = Random(hash(seed + profile))
        user_root = os.path.join("Users", username)
        artifact_count = 0

        if "git" in installed:
            self._create_gitconfig(user_root, username, org)
            self._create_ssh_dir(user_root)
            artifact_count += 2

        if "vscode" in installed:
            self._create_vscode_settings(user_root)
            artifact_count += 1

        if "docker" in installed:
            self._create_docker_config(user_root)
            artifact_count += 1

        self._create_project_dirs(user_root, profile)
        artifact_count += 1

        self._audit.log({
            "service": self.service_name,
            "operation": "create_dev_artifacts",
            "profile_type": profile,
            "username": username,
            "artifact_groups": artifact_count,
        })

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _create_gitconfig(
        self, user_root: str, username: str, org: str,
    ) -> None:
        """Write a ~/.gitconfig with realistic identity."""
        gc_path = self._mount.resolve(
            os.path.join(user_root, ".gitconfig"),
        )
        gc_path.parent.mkdir(parents=True, exist_ok=True)

        email_domain = "gmail.com" if org == "personal" else f"{org}.com"
        content = (
            f"[user]\n"
            f"\tname = {username}\n"
            f"\temail = {username}@{email_domain}\n"
            f"[core]\n"
            f"\tautocrlf = true\n"
            f"\teditor = code --wait\n"
            f"[init]\n"
            f"\tdefaultBranch = main\n"
            f"[pull]\n"
            f"\trebase = false\n"
        )
        gc_path.write_text(content, encoding="utf-8")
        self._audit.log({
            "service": self.service_name,
            "operation": "create_gitconfig",
            "path": str(gc_path),
        })

    def _create_ssh_dir(self, user_root: str) -> None:
        """Create .ssh/ with a known_hosts stub."""
        ssh_dir = self._mount.resolve(
            os.path.join(user_root, ".ssh"),
        )
        ssh_dir.mkdir(parents=True, exist_ok=True)

        kh_path = ssh_dir / "known_hosts"
        kh_path.write_text(
            "\n".join(_KNOWN_HOSTS_ENTRIES) + "\n", encoding="utf-8",
        )
        self._audit.log({
            "service": self.service_name,
            "operation": "create_ssh_dir",
            "path": str(ssh_dir),
        })

    def _create_vscode_settings(self, user_root: str) -> None:
        """Write VS Code User/settings.json."""
        settings_dir = self._mount.resolve(
            os.path.join(
                user_root, "AppData", "Roaming", "Code", "User",
            ),
        )
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / "settings.json"
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump(_VSCODE_SETTINGS, fh, indent=4)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_vscode_settings",
            "path": str(settings_path),
        })

    def _create_docker_config(self, user_root: str) -> None:
        """Write .docker/config.json stub."""
        docker_dir = self._mount.resolve(
            os.path.join(user_root, ".docker"),
        )
        docker_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = docker_dir / "config.json"
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(_DOCKER_CONFIG, fh, indent=4)
        self._audit.log({
            "service": self.service_name,
            "operation": "create_docker_config",
            "path": str(cfg_path),
        })

    def _create_project_dirs(self, user_root: str, profile: str) -> None:
        """Create empty project directory trees."""
        dirs = _PROJECT_DIRS.get(profile, [])
        for rel in dirs:
            proj_dir = self._mount.resolve(os.path.join(user_root, rel))
            proj_dir.mkdir(parents=True, exist_ok=True)
        if dirs:
            self._audit.log({
                "service": self.service_name,
                "operation": "create_project_dirs",
                "project_count": len(dirs),
            })
