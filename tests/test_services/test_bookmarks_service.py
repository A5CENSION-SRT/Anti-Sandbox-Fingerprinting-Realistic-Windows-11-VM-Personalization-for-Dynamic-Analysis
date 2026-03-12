"""Unit tests for BookmarksService."""

import json
from pathlib import Path

import pytest

from services.browser.bookmarks import BookmarksService, BookmarksServiceError


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def templates_dir(tmp_path):
    """Create temporary bookmark templates."""
    tpl = tmp_path / "templates" / "browser"
    tpl.mkdir(parents=True)
    bookmarks = {
        "checksum": "",
        "roots": {
            "bookmark_bar": {
                "children": [
                    {"name": "Test Site", "type": "url",
                     "url": "https://example.com/"}
                ],
                "name": "Bookmarks bar",
                "type": "folder",
            },
            "other": {
                "children": [],
                "name": "Other bookmarks",
                "type": "folder",
            },
            "synced": {
                "children": [],
                "name": "Mobile bookmarks",
                "type": "folder",
            },
        },
        "version": 1,
    }
    for name in ("bookmarks_office.json", "bookmarks_developer.json",
                 "bookmarks_home.json"):
        (tpl / name).write_text(json.dumps(bookmarks), encoding="utf-8")
    return tpl


@pytest.fixture
def service(mount_manager, timestamp_service, audit_logger, templates_dir):
    return BookmarksService(
        mount_manager=mount_manager,
        timestamp_service=timestamp_service,
        audit_logger=audit_logger,
        profile_name="office_user",
        username="jdoe",
        templates_dir=templates_dir,
    )


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "BookmarksService"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: apply writes bookmarks
# ---------------------------------------------------------------

class TestApply:
    def test_creates_bookmarks_for_both_browsers(self, service, mount_dir):
        """apply() should create Bookmarks JSON files for Chrome and Edge."""
        service.apply({"profile_name": "office_user", "username": "jdoe"})

        chrome_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        edge_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
            / "Default" / "Bookmarks"
        )
        assert chrome_bm.exists()
        assert edge_bm.exists()

    def test_bookmarks_file_is_valid_json(self, service, mount_dir):
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        chrome_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        data = json.loads(chrome_bm.read_text(encoding="utf-8"))
        assert "roots" in data
        assert "version" in data

    def test_bookmarks_contain_enriched_nodes(self, service, mount_dir):
        """Enricher should add date_added to bookmark nodes."""
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        chrome_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        data = json.loads(chrome_bm.read_text(encoding="utf-8"))
        bar = data["roots"]["bookmark_bar"]
        # Enricher adds date_added to folders
        assert "date_added" in bar

    def test_respects_browsers_filter(self, service, mount_dir):
        """When browsers filter is set, only that browser gets bookmarks."""
        service.apply({
            "profile_name": "office_user",
            "username": "jdoe",
            "browsers": ["Google Chrome"],
        })
        chrome_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        edge_bm = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
            / "Default" / "Bookmarks"
        )
        assert chrome_bm.exists()
        assert not edge_bm.exists()

    def test_context_overrides_constructor_profile(
        self, mount_manager, timestamp_service, audit_logger, templates_dir, mount_dir,
    ):
        svc = BookmarksService(
            mount_manager, timestamp_service, audit_logger,
            profile_name="home_user", username="alice",
            templates_dir=templates_dir,
        )
        svc.apply({"profile_name": "developer", "username": "bob"})
        bm = (
            mount_dir / "Users" / "bob"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        assert bm.exists()


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entries_created(self, service, audit_logger):
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        entries = audit_logger.entries
        bm_entries = [e for e in entries if e.get("operation") == "write_bookmarks"]
        # One per browser (Chrome + Edge)
        assert len(bm_entries) == 2

    def test_audit_entry_fields(self, service, audit_logger):
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "write_bookmarks"
        ][0]
        assert entry["service"] == "BookmarksService"
        assert "path" in entry
        assert entry["profile"] == "office_user"
        assert "browser" in entry


# ---------------------------------------------------------------
# Tests: idempotent overwrite
# ---------------------------------------------------------------

class TestIdempotency:
    def test_double_apply_produces_same_result(self, service, mount_dir):
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        bm_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Bookmarks"
        )
        first = bm_path.read_text(encoding="utf-8")
        service.apply({"profile_name": "office_user", "username": "jdoe"})
        second = bm_path.read_text(encoding="utf-8")
        assert first == second
