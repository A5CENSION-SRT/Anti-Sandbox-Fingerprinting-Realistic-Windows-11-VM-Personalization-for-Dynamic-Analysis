"""Unit tests for CookiesCacheService."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.browser.cookies_cache import (
    CookiesCacheService,
    CookiesCacheError,
    _CATEGORY_DOMAINS,
    _COOKIE_TEMPLATES,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def timestamp_service():
    svc = MagicMock()
    svc.get_timestamp.return_value = {
        "created": datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc),
    }
    return svc


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data" / "wordlists"
    d.mkdir(parents=True)
    urls = {"general": [{"url": "https://google.com", "title": "Google"}]}
    (d / "urls_by_category.json").write_text(json.dumps(urls), encoding="utf-8")
    return d.parent


@pytest.fixture
def service(mount_manager, timestamp_service, audit_logger, data_dir):
    return CookiesCacheService(
        mount_manager=mount_manager,
        timestamp_service=timestamp_service,
        audit_logger=audit_logger,
        profile_name="office_user",
        username="jdoe",
        data_dir=data_dir,
    )


@pytest.fixture
def context():
    return {
        "profile_name": "office_user",
        "username": "jdoe",
        "browsing": {"categories": ["general", "business"]},
        "computer_name": "WORKSTATION-01",
        "timeline_days": 90,
    }


# ---------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "CookiesCache"

    def test_inherits_base_service(self, service):
        from services.base_service import BaseService
        assert isinstance(service, BaseService)


# ---------------------------------------------------------------
# Tests: cookies database
# ---------------------------------------------------------------

class TestCookiesDatabase:
    def test_creates_cookies_db_for_both_browsers(self, service, mount_dir, context):
        service.apply(context)
        chrome = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        edge = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
            / "Default" / "Cookies"
        )
        assert chrome.exists()
        assert edge.exists()

    def test_cookies_db_has_valid_schema(self, service, mount_dir, context):
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert "cookies" in tables
            assert "meta" in tables
        finally:
            conn.close()

    def test_meta_table_has_version_21(self, service, mount_dir, context):
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT value FROM meta WHERE key='version'"
            )
            version = cursor.fetchone()[0]
            assert version == "21"
        finally:
            conn.close()

    def test_cookies_table_has_rows(self, service, mount_dir, context):
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM cookies")
            count = cursor.fetchone()[0]
            assert count > 0
        finally:
            conn.close()

    def test_cookies_have_correct_host_keys(self, service, mount_dir, context):
        """Cookies should have host_key prefixed with a dot."""
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("SELECT DISTINCT host_key FROM cookies")
            hosts = [row[0] for row in cursor.fetchall()]
            for host in hosts:
                assert host.startswith(".")
        finally:
            conn.close()

    def test_cookies_match_profile_categories(self, service, mount_dir, context):
        """business category should include linkedin.com cookies."""
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key = '.linkedin.com'"
            )
            count = cursor.fetchone()[0]
            assert count > 0  # business maps to linkedin.com
        finally:
            conn.close()

    def test_session_cookies_have_zero_expiry(self, service, mount_dir, context):
        """Cookies with days=0 (session) should have expires_utc=0."""
        service.apply(context)
        db_path = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT expires_utc, has_expires, is_persistent "
                "FROM cookies WHERE has_expires = 0"
            )
            rows = cursor.fetchall()
            for expires, has_exp, is_pers in rows:
                assert expires == 0
                assert is_pers == 0
        finally:
            conn.close()


# ---------------------------------------------------------------
# Tests: cache stubs
# ---------------------------------------------------------------

class TestCacheStubs:
    def test_creates_cache_directory(self, service, mount_dir, context):
        service.apply(context)
        cache = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cache" / "Cache_Data"
        )
        assert cache.is_dir()

    def test_cache_contains_stub_files(self, service, mount_dir, context):
        service.apply(context)
        cache = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cache" / "Cache_Data"
        )
        expected = {"index", "data_0", "data_1", "data_2", "data_3"}
        actual = {f.name for f in cache.iterdir()}
        assert expected == actual


# ---------------------------------------------------------------
# Tests: browsers filter
# ---------------------------------------------------------------

class TestBrowsersFilter:
    def test_filter_to_chrome_only(self, service, mount_dir, context):
        context["browsers"] = ["Google Chrome"]
        service.apply(context)
        chrome = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        edge = (
            mount_dir / "Users" / "jdoe"
            / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
            / "Default" / "Cookies"
        )
        assert chrome.exists()
        assert not edge.exists()


# ---------------------------------------------------------------
# Tests: audit logging
# ---------------------------------------------------------------

class TestAuditLogging:
    def test_audit_entries_created(self, service, audit_logger, context):
        service.apply(context)
        entries = audit_logger.entries
        cookie_entries = [
            e for e in entries if e.get("operation") == "create_cookies_db"
        ]
        # One per browser (Chrome + Edge)
        assert len(cookie_entries) == 2

    def test_cookie_count_in_audit(self, service, audit_logger, context):
        service.apply(context)
        entry = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_cookies_db"
        ][0]
        assert entry["cookie_count"] > 0
        assert entry["service"] == "CookiesCache"

    def test_cache_stub_audit(self, service, audit_logger, context):
        service.apply(context)
        cache_entries = [
            e for e in audit_logger.entries
            if e.get("operation") == "create_cache_stubs"
        ]
        assert len(cache_entries) == 2


# ---------------------------------------------------------------
# Tests: domain category mapping
# ---------------------------------------------------------------

class TestDomainMapping:
    def test_domains_for_general_always_included(self):
        domains = CookiesCacheService._domains_for_categories(["business"])
        assert "google.com" in domains  # general is always prepended

    def test_domains_deduplication(self):
        """Passing same category twice should not produce duplicate domains."""
        domains = CookiesCacheService._domains_for_categories(
            ["general", "general"]
        )
        assert len(domains) == len(set(domains))

    def test_unknown_category_returns_general_only(self):
        domains = CookiesCacheService._domains_for_categories(
            ["nonexistent_category"]
        )
        # Should still have general domains
        assert "google.com" in domains


# ---------------------------------------------------------------
# Tests: deterministic output
# ---------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_produces_same_cookie_names_and_hosts(
        self, mount_manager, timestamp_service, audit_logger, data_dir, mount_dir,
    ):
        """Two runs with the same seed should produce same cookies (by host+name)."""
        ctx = {
            "profile_name": "home_user",
            "username": "alice",
            "browsing": {"categories": ["general"]},
            "computer_name": "STABLE-PC",
            "browsers": ["Google Chrome"],
        }

        svc1 = CookiesCacheService(
            mount_manager, timestamp_service, audit_logger,
            data_dir=data_dir,
        )
        svc1.apply(ctx)
        db1 = (
            mount_dir / "Users" / "alice"
            / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
            / "Default" / "Cookies"
        )
        conn = sqlite3.connect(str(db1))
        rows1 = conn.execute(
            "SELECT host_key, name FROM cookies ORDER BY host_key, name"
        ).fetchall()
        conn.close()

        # Delete and recreate
        db1.unlink()
        svc2 = CookiesCacheService(
            mount_manager, timestamp_service, audit_logger,
            data_dir=data_dir,
        )
        svc2.apply(ctx)
        conn = sqlite3.connect(str(db1))
        rows2 = conn.execute(
            "SELECT host_key, name FROM cookies ORDER BY host_key, name"
        ).fetchall()
        conn.close()

        assert rows1 == rows2
