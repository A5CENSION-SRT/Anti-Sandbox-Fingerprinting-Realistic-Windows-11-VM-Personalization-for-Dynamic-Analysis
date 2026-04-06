"""Unit tests for CommsApps service."""

import json
import os
from pathlib import Path
import pytest

from services.applications.comms_apps import (
    CommsApps,
    CommsAppsError,
    _COMMS_ARTIFACTS,
    _PROFILE_COMMS,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def service(mount_manager, audit_logger):
    return CommsApps(mount_manager, audit_logger)


@pytest.fixture
def office_context():
    return {
        "username": "jdoe",
        "profile_type": "office_user",
        "installed_apps": ["teams", "slack", "zoom"],
    }


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "CommsApps"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: skip when no comms apps
# ---------------------------------------------------------------

class TestSkipCondition:
    def test_skips_when_no_comms_apps(self, service, audit_logger):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["chrome", "notepad"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "CommsApps"
        ]
        assert len(entries) == 0

    def test_skips_when_app_not_in_profile(self, service, audit_logger):
        """teams is installed but home_user profile only maps to discord."""
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["teams"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "CommsApps"
        ]
        assert len(entries) == 0

    def test_runs_when_matching_app_installed(self, service, audit_logger):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["discord"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "CommsApps"
        ]
        assert len(entries) > 0


# ---------------------------------------------------------------
# Tests: Teams artifacts
# ---------------------------------------------------------------

class TestTeamsArtifacts:
    def test_creates_teams_base_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        teams = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Teams"
        )
        assert teams.is_dir()

    def test_creates_teams_config_files(self, service, mount_dir, office_context):
        service.apply(office_context)
        teams = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Teams"
        )
        config = teams / "desktop-config.json"
        storage = teams / "storage.json"
        assert config.exists()
        assert storage.exists()

    def test_teams_config_valid_json(self, service, mount_dir, office_context):
        service.apply(office_context)
        config = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Teams" / "desktop-config.json"
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["currentWebLanguage"] == "en-us"
        assert data["isLoggedIn"] is True

    def test_teams_subdirs_created(self, service, mount_dir, office_context):
        service.apply(office_context)
        teams = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Teams"
        )
        for subdir_name in _COMMS_ARTIFACTS["teams"]["subdirs"]:
            assert (teams / subdir_name).is_dir()


# ---------------------------------------------------------------
# Tests: Slack artifacts
# ---------------------------------------------------------------

class TestSlackArtifacts:
    def test_creates_slack_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        slack = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming" / "Slack"
        )
        assert slack.is_dir()

    def test_slack_local_settings(self, service, mount_dir, office_context):
        service.apply(office_context)
        settings = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Slack" / "local-settings.json"
        )
        assert settings.exists()
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "theme" in data


# ---------------------------------------------------------------
# Tests: Discord artifacts (home_user profile)
# ---------------------------------------------------------------

class TestDiscordArtifacts:
    def test_creates_discord_for_home_user(self, service, mount_dir):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["discord"],
        }
        service.apply(context)
        discord = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming" / "discord"
        )
        assert discord.is_dir()
        settings = discord / "settings.json"
        assert settings.exists()

    def test_discord_settings_valid_json(self, service, mount_dir):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["discord"],
        }
        service.apply(context)
        settings = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "discord" / "settings.json"
        )
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "WINDOW_BOUNDS" in data


# ---------------------------------------------------------------
# Tests: Zoom artifacts
# ---------------------------------------------------------------

class TestZoomArtifacts:
    def test_creates_zoom_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        zoom = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming" / "Zoom"
        )
        assert zoom.is_dir()

    def test_zoom_subdirs_created(self, service, mount_dir, office_context):
        service.apply(office_context)
        zoom = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming" / "Zoom"
        )
        for subdir_name in _COMMS_ARTIFACTS["zoom"]["subdirs"]:
            assert (zoom / subdir_name).is_dir()


# ---------------------------------------------------------------
# Tests: profile-app mapping
# ---------------------------------------------------------------

class TestProfileMapping:
    def test_office_user_creates_teams_slack_zoom(self, service, mount_dir, office_context):
        service.apply(office_context)
        user_roaming = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
        )
        assert (user_roaming / "Microsoft" / "Teams").is_dir()
        assert (user_roaming / "Slack").is_dir()
        assert (user_roaming / "Zoom").is_dir()
        # Discord should NOT be created for office_user
        assert not (user_roaming / "discord").is_dir()

    def test_developer_creates_teams_slack_discord(self, service, mount_dir):
        context = {
            "username": "dev",
            "profile_type": "developer",
            "installed_apps": ["teams", "slack", "discord"],
        }
        service.apply(context)
        user_roaming = (
            mount_dir / "Users" / "dev" / "AppData" / "Roaming"
        )
        assert (user_roaming / "Microsoft" / "Teams").is_dir()
        assert (user_roaming / "Slack").is_dir()
        assert (user_roaming / "discord").is_dir()


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entry_fields(self, service, audit_logger, office_context):
        service.apply(office_context)
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_comms_artifacts"
        ][0]
        assert entry["profile_type"] == "office_user"
        assert entry["username"] == "jdoe"
        assert isinstance(entry["apps_created"], list)

    def test_config_file_audit_entries(self, service, audit_logger, office_context):
        service.apply(office_context)
        config_entries = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_config_file"
        ]
        # Teams has 2 files, Slack has 1, Zoom has 0 → ≥3
        assert len(config_entries) >= 3
