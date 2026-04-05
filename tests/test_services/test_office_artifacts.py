"""Unit tests for OfficeArtifacts service."""

import os
from pathlib import Path
import pytest

from services.applications.office_artifacts import (
    OfficeArtifacts,
    OfficeArtifactsError,
    _PROFILE_DOCUMENTS,
    _TEMPLATE_FILES,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def service(mount_manager, audit_logger):
    return OfficeArtifacts(mount_manager, audit_logger)


@pytest.fixture
def office_context():
    return {
        "username": "jdoe",
        "profile_type": "office_user",
        "computer_name": "WORKSTATION-01",
        "installed_apps": ["outlook", "word", "excel", "powerpoint"],
    }


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "OfficeArtifacts"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: skip when no Office apps
# ---------------------------------------------------------------

class TestSkipCondition:
    def test_skips_when_no_office_apps(self, service, audit_logger, mount_dir):
        context = {
            "username": "jdoe",
            "profile_type": "home_user",
            "installed_apps": ["chrome", "notepad"],
        }
        service.apply(context)
        # No audit entries should be created
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "OfficeArtifacts"
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
            if e.get("service") == "OfficeArtifacts"
        ]
        assert len(entries) > 0


# ---------------------------------------------------------------
# Tests: directory creation
# ---------------------------------------------------------------

class TestDirectoryCreation:
    def test_creates_office_recent_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        recent = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Office" / "Recent"
        )
        assert recent.is_dir()

    def test_creates_office_templates_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        tpl = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Templates"
        )
        assert tpl.is_dir()

    def test_creates_office_cache_dir(self, service, mount_dir, office_context):
        service.apply(office_context)
        cache = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Local"
            / "Microsoft" / "Office" / "16.0" / "OfficeFileCache"
        )
        assert cache.is_dir()


# ---------------------------------------------------------------
# Tests: template files
# ---------------------------------------------------------------

class TestTemplateFiles:
    def test_creates_normal_dotm(self, service, mount_dir, office_context):
        service.apply(office_context)
        dotm = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Templates" / "Normal.dotm"
        )
        assert dotm.exists()

    def test_creates_all_template_files(self, service, mount_dir, office_context):
        service.apply(office_context)
        tpl_dir = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Templates"
        )
        created = {f.name for f in tpl_dir.iterdir() if f.is_file()}
        expected = set(_TEMPLATE_FILES)
        assert expected == created


# ---------------------------------------------------------------
# Tests: recent shortcuts
# ---------------------------------------------------------------

class TestRecentShortcuts:
    def test_creates_lnk_files(self, service, mount_dir, office_context):
        service.apply(office_context)
        recent = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Office" / "Recent"
        )
        lnks = list(recent.glob("*.lnk"))
        docs = _PROFILE_DOCUMENTS["office_user"]
        assert len(lnks) == len(docs)

    def test_lnk_names_match_documents(self, service, mount_dir, office_context):
        service.apply(office_context)
        recent = (
            mount_dir / "Users" / "jdoe" / "AppData" / "Roaming"
            / "Microsoft" / "Office" / "Recent"
        )
        expected = {d["name"] + ".lnk" for d in _PROFILE_DOCUMENTS["office_user"]}
        actual = {f.name for f in recent.iterdir() if f.suffix == ".lnk"}
        assert expected == actual


# ---------------------------------------------------------------
# Tests: placeholder documents
# ---------------------------------------------------------------

class TestDocuments:
    def test_creates_documents_for_office_user(self, service, mount_dir, office_context):
        service.apply(office_context)
        docs_dir = mount_dir / "Users" / "jdoe" / "Documents"
        files = {f.name for f in docs_dir.iterdir() if f.is_file()}
        expected = {d["name"] for d in _PROFILE_DOCUMENTS["office_user"]}
        assert expected == files

    def test_developer_profile_fewer_documents(self, service, mount_dir):
        context = {
            "username": "dev",
            "profile_type": "developer",
            "installed_apps": ["word", "excel"],
        }
        service.apply(context)
        docs_dir = mount_dir / "Users" / "dev" / "Documents"
        files = list(docs_dir.iterdir())
        assert len(files) == len(_PROFILE_DOCUMENTS["developer"])

    def test_documents_are_zero_byte(self, service, mount_dir, office_context):
        service.apply(office_context)
        docs_dir = mount_dir / "Users" / "jdoe" / "Documents"
        for f in docs_dir.iterdir():
            assert f.stat().st_size == 0


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entries_created(self, service, audit_logger, office_context):
        service.apply(office_context)
        entries = [
            e for e in audit_logger.entries
            if e.get("service") == "OfficeArtifacts"
        ]
        # Should have: create_templates, create_recent_shortcuts,
        # create_documents, create_office_artifacts
        assert len(entries) >= 4

    def test_main_audit_entry_fields(self, service, audit_logger, office_context):
        service.apply(office_context)
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_office_artifacts"
        ][0]
        assert entry["profile_type"] == "office_user"
        assert entry["username"] == "jdoe"
