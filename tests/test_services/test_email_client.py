"""Unit tests for EmailClient service."""

import os
from pathlib import Path
import pytest

from services.applications.email_client import (
    EmailClient,
    EmailClientError,
    _PST_SIZES,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def service(mount_manager, audit_logger):
    return EmailClient(mount_manager, audit_logger)


@pytest.fixture
def email_context():
    return {
        "username": "jdoe",
        "profile_type": "office_user",
        "installed_apps": ["outlook", "teams"],
        "organization": "acmecorp",
        "computer_name": "WORK-PC-01",
    }


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "EmailClient"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: skip when no email apps
# ---------------------------------------------------------------

class TestSkipCondition:
    def test_skips_when_no_email_apps(self, service, audit_logger):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["chrome", "notepad"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "EmailClient"
        ]
        assert len(entries) == 0

    def test_runs_when_outlook_installed(self, service, audit_logger):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["outlook"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "EmailClient"
        ]
        assert len(entries) > 0

    def test_runs_when_teams_installed(self, service, audit_logger):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["teams"],
        }
        service.apply(context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "EmailClient"
        ]
        assert len(entries) > 0


# ---------------------------------------------------------------
# Tests: Outlook directories
# ---------------------------------------------------------------

class TestOutlookDirs:
    def test_creates_outlook_data_dir(self, service, mount_dir, email_context):
        service.apply(email_context)
        outlook = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook"
        )
        assert outlook.is_dir()

    def test_creates_roam_cache(self, service, mount_dir, email_context):
        service.apply(email_context)
        roam = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "RoamCache"
        )
        assert roam.is_dir()

    def test_creates_outlook_16_dir(self, service, mount_dir, email_context):
        service.apply(email_context)
        o16 = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "16"
        )
        assert o16.is_dir()


# ---------------------------------------------------------------
# Tests: PST placeholder
# ---------------------------------------------------------------

class TestPST:
    def test_creates_pst_file(self, service, mount_dir, email_context):
        service.apply(email_context)
        pst = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "Outlook.pst"
        )
        assert pst.exists()

    def test_pst_size_matches_profile(self, service, mount_dir, email_context):
        service.apply(email_context)
        pst = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "Outlook.pst"
        )
        assert pst.stat().st_size == _PST_SIZES["office_user"]

    def test_pst_starts_with_magic_bytes(self, service, mount_dir, email_context):
        service.apply(email_context)
        pst = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "Outlook.pst"
        )
        header = pst.read_bytes()[:4]
        assert header == b"\x21\x42\x44\x4e"

    def test_pst_non_zero_content(self, service, mount_dir, email_context):
        service.apply(email_context)
        pst = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "Outlook.pst"
        )
        assert pst.stat().st_size > 0


# ---------------------------------------------------------------
# Tests: Outlook profile XML
# ---------------------------------------------------------------

class TestOutlookProfile:
    def test_creates_profile_xml(self, service, mount_dir, email_context):
        service.apply(email_context)
        profile = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "16" / "profile.xml"
        )
        assert profile.exists()

    def test_profile_xml_contains_email(self, service, mount_dir, email_context):
        service.apply(email_context)
        profile = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "16" / "profile.xml"
        )
        content = profile.read_text(encoding="utf-8")
        assert "jdoe@acmecorp.com" in content
        assert "<DisplayName>jdoe</DisplayName>" in content

    def test_personal_org_uses_gmail(self, service, mount_dir):
        context = {
            "username": "bob",
            "profile_type": "home_user",
            "installed_apps": ["outlook"],
            "organization": "personal",
        }
        service.apply(context)
        profile = (
            mount_dir / "Users" / "bob" / "AppData" / "Local"
            / "Microsoft" / "Outlook" / "16" / "profile.xml"
        )
        content = profile.read_text(encoding="utf-8")
        assert "bob@gmail.com" in content


# ---------------------------------------------------------------
# Tests: Windows Mail stub
# ---------------------------------------------------------------

class TestWindowsMail:
    def test_creates_windows_mail_dir(self, service, mount_dir, email_context):
        service.apply(email_context)
        mail = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local" / "Packages"
            / "microsoft.windowscommunicationsapps_8wekyb3d8bbwe"
            / "LocalState"
        )
        assert mail.is_dir()

    def test_creates_localstate_db(self, service, mount_dir, email_context):
        service.apply(email_context)
        db = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local" / "Packages"
            / "microsoft.windowscommunicationsapps_8wekyb3d8bbwe"
            / "LocalState" / "LocalState.db"
        )
        assert db.exists()


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entries_created(self, service, audit_logger, email_context):
        service.apply(email_context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "EmailClient"
        ]
        # create_pst, create_outlook_profile, create_windows_mail_stub,
        # create_email_artifacts
        assert len(entries) >= 4

    def test_pst_audit_has_size(self, service, audit_logger, email_context):
        service.apply(email_context)
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_pst"
        ][0]
        assert entry["size_bytes"] == _PST_SIZES["office_user"]
