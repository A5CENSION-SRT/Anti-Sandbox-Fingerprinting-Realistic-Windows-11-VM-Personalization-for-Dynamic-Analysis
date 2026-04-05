"""Shared pytest fixtures for browser download tests.

Auto-discovered by pytest — no explicit import needed in test files.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from services.browser.history import BrowserHistoryService


@pytest.fixture
def mount_dir(tmp_path):
    d = tmp_path / "mount"
    d.mkdir()
    return d


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
def data_dir(tmp_path):
    """Minimal data dir with downloads catalogue, URLs, and search terms."""
    d = tmp_path / "data" / "wordlists"
    d.mkdir(parents=True)

    catalogue = {
        "home_user": [
            {
                "filename": "spotify_setup.exe",
                "mime_type": "application/octet-stream",
                "size_bytes": 47185920,
                "referrer": "https://www.spotify.com/download/",
                "url": "https://download.scdn.co/SpotifySetup.exe",
            },
            {
                "filename": "amazon_invoice.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 65536,
                "referrer": "https://www.amazon.com/",
                "url": "https://www.amazon.com/gp/css/summary/print.html",
            },
        ],
        "developer": [
            {
                "filename": "python_installer.exe",
                "mime_type": "application/octet-stream",
                "size_bytes": 26214400,
                "referrer": "https://www.python.org/downloads/",
                "url": "https://www.python.org/ftp/python/3.12.2/python-3.12.2-amd64.exe",
            },
        ],
    }
    (d / "downloads_by_profile.json").write_text(
        json.dumps(catalogue), encoding="utf-8"
    )
    urls = {
        "general": [
            {"url": "https://www.google.com/", "title": "Google"},
            {"url": "https://mail.google.com/mail/", "title": "Gmail"},
        ]
    }
    (d / "urls_by_category.json").write_text(
        json.dumps(urls), encoding="utf-8"
    )
    (d / "search_terms.txt").write_text(
        "test search term\nanother query\n", encoding="utf-8"
    )
    return d


@pytest.fixture
def history_db(mount_manager, timestamp_service, audit_logger, data_dir):
    """Pre-build the History SQLite DB so BrowserDownloadService can populate it."""
    hist = BrowserHistoryService(
        mount_manager, timestamp_service, audit_logger,
        profile_config={"browsing": {"categories": ["general"],
                                     "daily_avg_sites": 3}},
        username="TestUser",
        data_dir=str(data_dir),
    )
    hist.apply({"username": "TestUser", "timeline_days": 5})


def chrome_history_db(mount_manager):
    """Helper: return Path to Chrome's History file."""
    return (
        mount_manager.root / "Users" / "TestUser" / "AppData"
        / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "History"
    )
