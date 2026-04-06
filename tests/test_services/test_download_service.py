"""Integration tests for BrowserDownloadService.

Tests filesystem stubs, SQLite record content, and service interface.
Requires conftest_downloads.py fixtures (mount_manager, data_dir,
history_db, audit_logger, timestamp_service).
"""

import sqlite3

import pytest

from services.browser.downloads import BrowserDownloadService
from tests.test_services.conftest import chrome_history_db


def _make_svc(mount_manager, timestamp_service, audit_logger,
              data_dir, count=2):
    return BrowserDownloadService(
        mount_manager, timestamp_service, audit_logger,
        profile_name="home_user", username="TestUser",
        data_dir=str(data_dir), download_count=count,
    )


# ---------------------------------------------------------------
# Filesystem stubs
# ---------------------------------------------------------------

class TestFilesystemStubs:

    def test_downloads_folder_created(self, mount_manager, timestamp_service,
                                      audit_logger, data_dir):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger, data_dir)
        svc.apply({"username": "TestUser", "timeline_days": 10})
        dl = mount_manager.root / "Users" / "TestUser" / "Downloads"
        assert dl.is_dir()

    def test_stub_count_matches_request(self, mount_manager, timestamp_service,
                                        audit_logger, data_dir):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                        count=2)
        svc.apply({"username": "TestUser", "timeline_days": 10})
        dl = mount_manager.root / "Users" / "TestUser" / "Downloads"
        assert len(list(dl.iterdir())) == 2

    def test_stub_filenames_realistic(self, mount_manager, timestamp_service,
                                      audit_logger, data_dir):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                        count=2)
        svc.apply({"username": "TestUser", "timeline_days": 10})
        dl = mount_manager.root / "Users" / "TestUser" / "Downloads"
        names = {f.name for f in dl.iterdir()}
        assert any(n.endswith((".exe", ".pdf", ".zip", ".msi")) for n in names)


# ---------------------------------------------------------------
# SQLite records
# ---------------------------------------------------------------

class TestDbRecords:

    @pytest.fixture(autouse=True)
    def setup(self, history_db):
        """Ensure History DB exists before each test."""

    def test_download_rows_inserted(self, mount_manager, timestamp_service,
                                    audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=2).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM downloads").fetchone()[0] == 2
        finally:
            conn.close()

    def test_url_chains_inserted(self, mount_manager, timestamp_service,
                                  audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=2).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM downloads_url_chains"
            ).fetchone()[0] == 2
        finally:
            conn.close()

    def test_timestamps_chrome_epoch(self, mount_manager, timestamp_service,
                                     audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=1).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            row = conn.execute(
                "SELECT start_time, end_time FROM downloads LIMIT 1"
            ).fetchone()
            assert row[0] > 13_000_000_000_000_000
            assert row[1] >= row[0]
        finally:
            conn.close()

    def test_state_is_complete(self, mount_manager, timestamp_service,
                               audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=1).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            state = conn.execute(
                "SELECT state FROM downloads LIMIT 1").fetchone()[0]
            assert state == 1  # COMPLETE
        finally:
            conn.close()

    def test_path_contains_username_and_downloads(
            self, mount_manager, timestamp_service, audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=1).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            path = conn.execute(
                "SELECT target_path FROM downloads LIMIT 1").fetchone()[0]
            assert "TestUser" in path and "Downloads" in path
        finally:
            conn.close()

    def test_received_equals_total_bytes(self, mount_manager, timestamp_service,
                                         audit_logger, data_dir):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=2).apply({"username": "TestUser", "timeline_days": 10})
        conn = sqlite3.connect(str(chrome_history_db(mount_manager)))
        try:
            for recv, total in conn.execute(
                    "SELECT received_bytes, total_bytes FROM downloads"):
                assert recv == total
        finally:
            conn.close()


# ---------------------------------------------------------------
# Service interface + audit
# ---------------------------------------------------------------

class TestServiceInterface:

    def test_service_name(self, mount_manager, timestamp_service,
                          audit_logger, data_dir):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger, data_dir)
        assert svc.service_name == "BrowserDownloads"

    def test_audit_logged(self, mount_manager, timestamp_service,
                          audit_logger, data_dir, history_db):
        _make_svc(mount_manager, timestamp_service, audit_logger, data_dir,
                  count=1).apply({"username": "TestUser", "timeline_days": 5})
        svcs = {e["service"] for e in audit_logger.entries}
        assert "BrowserDownloads" in svcs
