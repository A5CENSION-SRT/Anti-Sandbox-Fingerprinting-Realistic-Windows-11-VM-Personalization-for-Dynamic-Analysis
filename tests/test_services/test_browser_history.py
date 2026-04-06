"""Unit tests for browser profile and history services."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from services.browser.browser_profile import BrowserProfileService
from services.browser.history import BrowserHistoryService
from services.browser.utils.chrome_timestamps import datetime_to_chrome


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def mount_dir(tmp_path):
    """Provide a temporary mount root directory."""
    mount = tmp_path / "mount"
    mount.mkdir()
    return mount


@pytest.fixture
def mount_manager(mount_dir):
    return MountManager(str(mount_dir))


@pytest.fixture
def timestamp_service():
    svc = MagicMock()
    svc.get_timestamp.return_value = {
        "created": datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc),
        "modified": datetime(2025, 3, 10, 10, 0, 0, tzinfo=timezone.utc),
        "accessed": datetime(2025, 3, 10, 11, 0, 0, tzinfo=timezone.utc),
    }
    return svc


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def templates_dir(tmp_path):
    """Create temporary bookmark templates matching the project format."""
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
def data_dir(tmp_path):
    """Create temporary URL and search-term data files."""
    d = tmp_path / "data" / "wordlists"
    d.mkdir(parents=True)

    urls = {
        "general": [
            {"url": "https://www.google.com/", "title": "Google"},
            {"url": "https://mail.google.com/mail/", "title": "Gmail"},
            {"url": "https://www.wikipedia.org/", "title": "Wikipedia"},
        ],
        "business": [
            {"url": "https://www.linkedin.com/feed/", "title": "LinkedIn"},
            {"url": "https://teams.microsoft.com/", "title": "Teams"},
        ],
        "news": [
            {"url": "https://www.bbc.com/news", "title": "BBC News"},
        ],
        "social_media": [
            {"url": "https://www.reddit.com/", "title": "Reddit"},
        ],
        "stackoverflow": [
            {"url": "https://stackoverflow.com/", "title": "Stack Overflow"},
        ],
        "github": [
            {"url": "https://github.com/", "title": "GitHub"},
        ],
        "documentation": [
            {"url": "https://docs.python.org/3/", "title": "Python Docs"},
        ],
    }
    (d / "urls_by_category.json").write_text(
        json.dumps(urls), encoding="utf-8"
    )

    terms = "how to merge git branches\npython list comprehension\nbest laptop 2025\n"
    (d / "search_terms.txt").write_text(terms, encoding="utf-8")
    return d


@pytest.fixture
def profile_service(mount_manager, timestamp_service, audit_logger, templates_dir):
    return BrowserProfileService(
        mount_manager, timestamp_service, audit_logger,
        profile_name="home_user",
        username="TestUser",
        templates_dir=str(templates_dir),
    )


@pytest.fixture
def history_service(mount_manager, timestamp_service, audit_logger, data_dir):
    return BrowserHistoryService(
        mount_manager, timestamp_service, audit_logger,
        profile_config={
            "browsing": {"categories": ["general", "news"], "daily_avg_sites": 5},
            "work_hours": {"start": 9, "end": 17, "active_days": [1, 2, 3, 4, 5]},
        },
        username="TestUser",
        data_dir=str(data_dir),
    )


# ---------------------------------------------------------------
# BrowserProfileService Tests
# ---------------------------------------------------------------

class TestBrowserProfileCreation:
    """Test that the profile directory tree and config files are created."""

    def test_chrome_profile_dir_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        chrome_default = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
        assert chrome_default.is_dir()

    def test_edge_profile_dir_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        edge_default = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default"
        assert edge_default.is_dir()

    def test_subdirs_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        default = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
        for subdir in ("Network", "Cache", "Session Storage",
                       "Local Storage", "Extensions"):
            assert (default / subdir).is_dir()

    def test_local_state_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        local_state = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Local State"
        assert local_state.exists()
        data = json.loads(local_state.read_text())
        assert "profile" in data
        assert data["profile"]["last_used"] == "Default"

    def test_preferences_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        prefs = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Preferences"
        assert prefs.exists()
        data = json.loads(prefs.read_text())
        assert "download" in data
        assert "TestUser" in data["download"]["default_directory"]

    def test_secure_preferences_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        sec_prefs = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Secure Preferences"
        assert sec_prefs.exists()
        data = json.loads(sec_prefs.read_text())
        assert "protection" in data

    def test_only_specific_browser(self, profile_service, mount_manager):
        profile_service.apply({
            "username": "TestUser",
            "browsers": ["Google Chrome"],
        })
        chrome = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data"
        edge = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
        assert chrome.is_dir()
        assert not edge.exists()


class TestBookmarkTemplates:
    """Test that bookmark files are valid JSON with correct Chrome structure."""

    def test_bookmarks_file_created(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        bm = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Bookmarks"
        assert bm.exists()

    def test_bookmarks_valid_structure(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        bm = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Bookmarks"
        data = json.loads(bm.read_text())
        assert "roots" in data
        assert "bookmark_bar" in data["roots"]
        assert "other" in data["roots"]
        assert "synced" in data["roots"]
        assert data["version"] == 1

    def test_bookmarks_have_ids(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        bm = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Bookmarks"
        data = json.loads(bm.read_text())
        bar = data["roots"]["bookmark_bar"]
        assert "id" in bar
        assert "date_added" in bar

    def test_bookmarks_children_have_timestamps(self, profile_service, mount_manager):
        profile_service.apply({"username": "TestUser"})
        bm = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "Bookmarks"
        data = json.loads(bm.read_text())
        children = data["roots"]["bookmark_bar"].get("children", [])
        if children:
            assert "id" in children[0]
            assert "date_added" in children[0]


class TestChromeTimestamp:
    """Test the Chrome epoch timestamp conversion."""

    def test_known_timestamp(self):
        # Jan 1, 1970 00:00:00 UTC should be exactly the offset
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = datetime_to_chrome(dt)
        assert result == 11644473600 * 1_000_000

    def test_recent_date(self):
        dt = datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc)
        result = datetime_to_chrome(dt)
        assert result > 0
        # Check it's in the right ballpark (after 2020)
        dt_2020 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert result > datetime_to_chrome(dt_2020)


# ---------------------------------------------------------------
# BrowserHistoryService Tests
# ---------------------------------------------------------------

class TestBrowserHistorySchema:
    """Test that the SQLite DB has the correct tables and columns."""

    def test_history_file_created(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        assert db_path.exists()

    def test_tables_exist(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            for expected in ("urls", "visits", "keyword_search_terms",
                             "downloads", "meta", "segments"):
                assert expected in tables, f"Missing table: {expected}"
        finally:
            conn.close()

    def test_urls_columns(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(urls)"
            ).fetchall()]
            for expected in ("id", "url", "title", "visit_count",
                             "typed_count", "last_visit_time", "hidden"):
                assert expected in cols
        finally:
            conn.close()

    def test_meta_version(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key='version'"
            ).fetchone()
            assert version is not None
            assert version[0] == "46"
        finally:
            conn.close()


class TestBrowserHistoryContent:
    """Test that history content is populated correctly."""

    def test_urls_populated(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            assert count > 0, "urls table should not be empty"
        finally:
            conn.close()

    def test_visits_populated(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            assert count > 0, "visits table should not be empty"
        finally:
            conn.close()

    def test_search_terms_populated(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM keyword_search_terms"
            ).fetchone()[0]
            assert count > 0, "keyword_search_terms should not be empty"
        finally:
            conn.close()


class TestBrowserHistoryTimestamps:
    """Test that timestamps use Chrome epoch and are realistic."""

    def test_timestamps_are_chrome_epoch(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT visit_time FROM visits LIMIT 1"
            ).fetchone()
            assert row is not None
            # Chrome timestamps are microseconds since 1601, so very large
            assert row[0] > 13_000_000_000_000_000, \
                "Timestamp should be in Chrome epoch (> 13e15)"
        finally:
            conn.close()

    def test_last_visit_time_updated(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT last_visit_time FROM urls WHERE visit_count > 0"
            ).fetchall()
            for row in rows:
                assert row[0] > 0, "last_visit_time should be set for visited URLs"
        finally:
            conn.close()


class TestBrowserHistoryVisitChains:
    """Test that from_visit chains produce valid session flows."""

    def test_from_visit_chains_exist(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            # Some visits should have from_visit > 0 (linked to prev visit)
            count = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE from_visit > 0"
            ).fetchone()[0]
            assert count > 0, "Should have visits chained via from_visit"
        finally:
            conn.close()

    def test_first_visit_in_session_is_typed(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            # Visits with from_visit=0 are session starters and should be typed
            typed = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE from_visit = 0 AND transition = 1"
            ).fetchone()[0]
            assert typed > 0, \
                "Session-starting visits should have TRANSITION_TYPED"
        finally:
            conn.close()


class TestBrowserHistorySearchTerms:
    """Test that search terms are linked to search engine URLs."""

    def test_search_terms_have_valid_url_ids(self, history_service, mount_manager):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 7,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT kst.url_id, u.url FROM keyword_search_terms kst "
                "JOIN urls u ON kst.url_id = u.id LIMIT 5"
            ).fetchall()
            assert len(rows) > 0
            for _, url in rows:
                assert any(
                    se in url for se in ["google.com", "bing.com", "duckduckgo"]
                ), f"Search term linked to non-search URL: {url}"
        finally:
            conn.close()


# ---------------------------------------------------------------
# Service Interface Tests
# ---------------------------------------------------------------

class TestServiceInterface:
    """Test BaseService interface compliance."""

    def test_profile_service_name(self, profile_service):
        assert profile_service.service_name == "BrowserProfile"

    def test_history_service_name(self, history_service):
        assert history_service.service_name == "BrowserHistory"

    def test_profile_apply_runs(self, profile_service, mount_manager):
        """apply() should succeed without errors."""
        profile_service.apply({"username": "TestUser"})
        assert (mount_manager.root / "Users" / "TestUser").is_dir()

    def test_history_apply_runs(self, history_service, mount_manager):
        """apply() should succeed without errors."""
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 3,
        })
        db_path = mount_manager.root / "Users" / "TestUser" / \
            "AppData" / "Local" / "Google" / "Chrome" / "User Data" / \
            "Default" / "History"
        assert db_path.exists()


class TestAuditLogging:
    """Test that operations are logged via AuditLogger."""

    def test_profile_logs(self, profile_service, audit_logger):
        profile_service.apply({"username": "TestUser"})
        services = {e["service"] for e in audit_logger.entries}
        assert "BrowserProfile" in services

    def test_history_logs(self, history_service, audit_logger):
        history_service.apply({
            "username": "TestUser",
            "timeline_days": 3,
        })
        entries = [
            e for e in audit_logger.entries
            if e["service"] == "BrowserHistory"
        ]
        assert len(entries) > 0
        assert any(e.get("file_type") == "sqlite_history" for e in entries)
