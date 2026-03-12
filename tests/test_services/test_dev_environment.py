"""Unit tests for DevEnvironment service."""

import json
import os
from pathlib import Path
import pytest

from services.applications.dev_environment import (
    DevEnvironment,
    DevEnvironmentError,
    _PROJECT_DIRS,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def service(mount_manager, audit_logger):
    return DevEnvironment(mount_manager, audit_logger)


@pytest.fixture
def dev_context():
    return {
        "username": "alice",
        "profile_type": "developer",
        "installed_apps": ["vscode", "docker", "git", "terminal"],
        "computer_name": "DEV-WS-001",
        "organization": "acmecorp",
    }


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "DevEnvironment"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: skip when no dev apps
# ---------------------------------------------------------------

class TestSkipCondition:
    def test_skips_when_no_dev_apps(self, service, audit_logger):
        context = {
            "username": "alice",
            "profile_type": "home_user",
            "installed_apps": ["outlook", "word"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "DevEnvironment"
        ]
        assert len(entries) == 0

    def test_runs_when_git_installed(self, service, audit_logger):
        context = {
            "username": "alice",
            "profile_type": "home_user",
            "installed_apps": ["git"],
            "organization": "personal",
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "DevEnvironment"
        ]
        assert len(entries) > 0


# ---------------------------------------------------------------
# Tests: gitconfig
# ---------------------------------------------------------------

class TestGitconfig:
    def test_creates_gitconfig(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        gc = mount_dir / "Users" / "alice" / ".gitconfig"
        assert gc.exists()

    def test_gitconfig_contains_username(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        gc = mount_dir / "Users" / "alice" / ".gitconfig"
        content = gc.read_text(encoding="utf-8")
        assert "alice" in content

    def test_gitconfig_uses_org_email(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        gc = mount_dir / "Users" / "alice" / ".gitconfig"
        content = gc.read_text(encoding="utf-8")
        assert "alice@acmecorp.com" in content

    def test_gitconfig_personal_uses_gmail(self, service, mount_dir):
        context = {
            "username": "bob",
            "profile_type": "developer",
            "installed_apps": ["git"],
            "organization": "personal",
        }
        service.apply(context)
        gc = mount_dir / "Users" / "bob" / ".gitconfig"
        content = gc.read_text(encoding="utf-8")
        assert "bob@gmail.com" in content


# ---------------------------------------------------------------
# Tests: SSH directory
# ---------------------------------------------------------------

class TestSSH:
    def test_creates_ssh_dir(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        ssh = mount_dir / "Users" / "alice" / ".ssh"
        assert ssh.is_dir()

    def test_creates_known_hosts(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        kh = mount_dir / "Users" / "alice" / ".ssh" / "known_hosts"
        assert kh.exists()
        content = kh.read_text(encoding="utf-8")
        assert "github.com" in content
        assert "gitlab.com" in content


# ---------------------------------------------------------------
# Tests: VS Code settings
# ---------------------------------------------------------------

class TestVSCode:
    def test_creates_vscode_settings(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        settings = (
            mount_dir / "Users" / "alice" / "AppData" / "Roaming"
            / "Code" / "User" / "settings.json"
        )
        assert settings.exists()

    def test_vscode_settings_valid_json(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        settings = (
            mount_dir / "Users" / "alice" / "AppData" / "Roaming"
            / "Code" / "User" / "settings.json"
        )
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["editor.fontSize"] == 14
        assert "terminal.integrated.defaultProfile.windows" in data

    def test_no_vscode_when_not_installed(self, service, mount_dir):
        context = {
            "username": "alice",
            "profile_type": "developer",
            "installed_apps": ["git"],
            "organization": "personal",
        }
        service.apply(context)
        settings = (
            mount_dir / "Users" / "alice" / "AppData" / "Roaming"
            / "Code" / "User" / "settings.json"
        )
        assert not settings.exists()


# ---------------------------------------------------------------
# Tests: Docker config
# ---------------------------------------------------------------

class TestDocker:
    def test_creates_docker_config(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        cfg = mount_dir / "Users" / "alice" / ".docker" / "config.json"
        assert cfg.exists()

    def test_docker_config_valid_json(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        cfg = mount_dir / "Users" / "alice" / ".docker" / "config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["credsStore"] == "desktop"

    def test_no_docker_when_not_installed(self, service, mount_dir):
        context = {
            "username": "alice",
            "profile_type": "developer",
            "installed_apps": ["git"],
            "organization": "personal",
        }
        service.apply(context)
        cfg = mount_dir / "Users" / "alice" / ".docker" / "config.json"
        assert not cfg.exists()


# ---------------------------------------------------------------
# Tests: project directories
# ---------------------------------------------------------------

class TestProjectDirs:
    def test_developer_project_dirs_created(self, service, mount_dir, dev_context):
        service.apply(dev_context)
        for rel in _PROJECT_DIRS["developer"]:
            proj = mount_dir / "Users" / "alice" / rel
            assert proj.is_dir()

    def test_home_user_no_project_dirs(self, service, mount_dir):
        context = {
            "username": "alice",
            "profile_type": "home_user",
            "installed_apps": ["vscode"],
        }
        service.apply(context)
        repos = mount_dir / "Users" / "alice" / "source" / "repos"
        assert not repos.exists()


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entries_created(self, service, audit_logger, dev_context):
        service.apply(dev_context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "DevEnvironment"
        ]
        # gitconfig, ssh, vscode, docker, project_dirs, main summary
        assert len(entries) >= 5

    def test_main_audit_entry_fields(self, service, audit_logger, dev_context):
        service.apply(dev_context)
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_dev_artifacts"
        ][0]
        assert entry["profile_type"] == "developer"
        assert entry["username"] == "alice"
