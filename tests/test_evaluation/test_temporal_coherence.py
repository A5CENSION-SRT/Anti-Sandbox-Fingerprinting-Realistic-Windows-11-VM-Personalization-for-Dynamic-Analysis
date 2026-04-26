"""Tests for TemporalCoherenceCheck (§7.5, gate A9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from evaluation.consistency_checker import CheckResult, TemporalCoherenceCheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=_UTC)
_INSTALL = _NOW - timedelta(days=360)


def _event(kind: str, **payload):
    """Create a minimal SyntheticEvent-like object."""
    ev = MagicMock()
    ev.kind = kind
    ev.timestamp = _NOW - timedelta(days=10)
    ev.payload = payload
    return ev


def _make_scheduler(
    app_launches=10,
    file_creates=20,
    file_modifies=15,
    url_visits=50,
    url_downloads=5,
    logins=5,
):
    """Return a mock EventScheduler with deterministic event counts."""
    apps = [f"app_{i}.exe" for i in range(app_launches)]
    al_events = [_event("APP_LAUNCH", app=apps[i % len(apps)]) for i in range(app_launches)]
    fc_events = [_event("FILE_CREATE", path=f"doc_{i}.docx") for i in range(file_creates)]
    fm_events = [_event("FILE_MODIFY", path=f"doc_{i}.docx") for i in range(file_modifies)]
    uv_events = [_event("URL_VISIT", url=f"https://site{i}.com") for i in range(url_visits)]
    ud_events = [_event("URL_DOWNLOAD", url=f"https://dl{i}.com/f.exe") for i in range(url_downloads)]
    lo_events = [_event("LOGIN") for _ in range(logins)]

    all_events = al_events + fc_events + fm_events + uv_events + ud_events + lo_events

    def _events_of(*kinds):
        return [e for e in all_events if e.kind in kinds]

    sched = MagicMock()
    sched.events_of.side_effect = _events_of
    return sched


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit():
    return AuditLogger()


@pytest.fixture
def sched():
    return _make_scheduler()


@pytest.fixture
def tcc(sched, audit):
    return TemporalCoherenceCheck(sched, audit)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------

class TestStructure:
    def test_run_returns_six_results(self, tcc):
        results = tcc.run()
        assert len(results) == 6

    def test_all_results_are_check_result(self, tcc):
        for r in tcc.run():
            assert isinstance(r, CheckResult)

    def test_check_names_unique(self, tcc):
        names = [r.name for r in tcc.run()]
        assert len(names) == len(set(names))

    def test_expected_check_names(self, tcc):
        names = {r.name for r in tcc.run()}
        assert names == {
            "security_4688_coverage",
            "prefetch_coverage",
            "browser_history_coverage",
            "usn_journal_coverage",
            "zone_identifier_coverage",
            "mft_timestamp_divergence",
        }


# ---------------------------------------------------------------------------
# security_4688_coverage
# ---------------------------------------------------------------------------

class TestSecurity4688Coverage:
    def test_fails_when_no_security_log_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "security_4688_coverage")
        assert r.passed is False

    def test_passes_when_sufficient_records(self, sched, audit):
        audit.log({"service": "SecurityLog", "operation": "write_security_log",
                   "record_count": 20})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "security_4688_coverage")
        assert r.passed is True

    def test_fails_when_insufficient_records(self, sched, audit):
        # 10 app launches → min expected = 5; write only 1 record
        audit.log({"service": "SecurityLog", "operation": "write_security_log",
                   "record_count": 1})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "security_4688_coverage")
        assert r.passed is False

    def test_skipped_when_no_app_launches(self, audit):
        sched = _make_scheduler(app_launches=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "security_4688_coverage")
        assert r.passed is True
        assert "skipped" in r.detail.lower()


# ---------------------------------------------------------------------------
# prefetch_coverage
# ---------------------------------------------------------------------------

class TestPrefetchCoverage:
    def test_fails_when_no_prefetch_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "prefetch_coverage")
        assert r.passed is False

    def test_passes_with_enough_files(self, sched, audit):
        audit.log({"service": "PrefetchService", "operation": "generate_prefetch_files",
                   "files_created": 10})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "prefetch_coverage")
        assert r.passed is True

    def test_fails_with_too_few_files(self, sched, audit):
        # 10 unique apps → min expected = 5; write only 1 file
        audit.log({"service": "PrefetchService", "operation": "generate_prefetch_files",
                   "files_created": 1})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "prefetch_coverage")
        assert r.passed is False

    def test_skipped_when_no_app_launches(self, audit):
        sched = _make_scheduler(app_launches=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "prefetch_coverage")
        assert r.passed is True


# ---------------------------------------------------------------------------
# browser_history_coverage
# ---------------------------------------------------------------------------

class TestBrowserHistoryCoverage:
    def test_fails_when_no_history_db_in_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "browser_history_coverage")
        assert r.passed is False

    def test_passes_when_history_db_logged(self, sched, audit):
        audit.log({"service": "BrowserHistoryService", "operation": "create_file",
                   "file_type": "sqlite_history", "browser": "chrome"})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "browser_history_coverage")
        assert r.passed is True

    def test_fails_when_wrong_file_type(self, sched, audit):
        audit.log({"service": "BrowserHistoryService", "operation": "create_file",
                   "file_type": "other_file"})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "browser_history_coverage")
        assert r.passed is False

    def test_skipped_when_no_url_visits(self, audit):
        sched = _make_scheduler(url_visits=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "browser_history_coverage")
        assert r.passed is True


# ---------------------------------------------------------------------------
# usn_journal_coverage
# ---------------------------------------------------------------------------

class TestUsnJournalCoverage:
    def test_fails_when_usn_not_in_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "usn_journal_coverage")
        assert r.passed is False

    def test_passes_when_usn_records_sufficient(self, sched, audit):
        # file_creates=20 + file_modifies=15 = 35 file events; 0.9 × 35 = 31.5 → 31
        audit.log({"service": "UsnJournalWriter", "operation": "write_usn_journal",
                   "records_written": 35})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "usn_journal_coverage")
        assert r.passed is True

    def test_fails_when_usn_records_below_threshold(self, sched, audit):
        # 35 file events, 0.9 × 35 = 31.5 → need 31; give only 10
        audit.log({"service": "UsnJournalWriter", "operation": "write_usn_journal",
                   "records_written": 10})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "usn_journal_coverage")
        assert r.passed is False

    def test_skipped_when_no_file_events(self, audit):
        sched = _make_scheduler(file_creates=0, file_modifies=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "usn_journal_coverage")
        assert r.passed is True


# ---------------------------------------------------------------------------
# zone_identifier_coverage
# ---------------------------------------------------------------------------

class TestZoneIdentifierCoverage:
    def test_fails_when_no_zone_id_in_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "zone_identifier_coverage")
        assert r.passed is False

    def test_passes_when_zone_id_count_sufficient(self, sched, audit):
        # 5 downloads, 0.8 × 5 = 4
        audit.log({"service": "BrowserDownloads", "operation": "write_zone_identifier",
                   "count": 5})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "zone_identifier_coverage")
        assert r.passed is True

    def test_fails_when_zone_id_count_too_low(self, sched, audit):
        # 5 downloads, 0.8 × 5 = 4; give only 1
        audit.log({"service": "BrowserDownloads", "operation": "write_zone_identifier",
                   "count": 1})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "zone_identifier_coverage")
        assert r.passed is False

    def test_skipped_when_no_downloads(self, audit):
        sched = _make_scheduler(url_downloads=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "zone_identifier_coverage")
        assert r.passed is True


# ---------------------------------------------------------------------------
# mft_timestamp_divergence (A18)
# ---------------------------------------------------------------------------

class TestMftTimestampDivergence:
    def test_fails_when_mft_patcher_not_in_audit(self, tcc):
        r = next(r for r in tcc.run() if r.name == "mft_timestamp_divergence")
        assert r.passed is False

    def test_passes_when_patcher_ran_with_patches(self, sched, audit):
        audit.log({"service": "MftTimestampPatcher", "operation": "patch_si_timestamps",
                   "patched": 15, "skipped": 5, "failed": 0})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "mft_timestamp_divergence")
        assert r.passed is True

    def test_fails_when_patcher_ran_but_zero_patches(self, sched, audit):
        audit.log({"service": "MftTimestampPatcher", "operation": "patch_si_timestamps",
                   "patched": 0, "skipped": 20, "failed": 0})
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "mft_timestamp_divergence")
        assert r.passed is False

    def test_skipped_when_no_file_creates(self, audit):
        sched = _make_scheduler(file_creates=0)
        r = next(r for r in TemporalCoherenceCheck(sched, audit).run()
                 if r.name == "mft_timestamp_divergence")
        assert r.passed is True


# ---------------------------------------------------------------------------
# Integration: fully-passing synthetic run
# ---------------------------------------------------------------------------

class TestPassingRun:
    """Verify a complete coherent audit produces all-green results."""

    def test_all_checks_pass(self, sched):
        audit = AuditLogger()
        # Security log
        audit.log({"service": "SecurityLog", "operation": "write_security_log",
                   "record_count": 30})
        # Prefetch
        audit.log({"service": "PrefetchService", "operation": "generate_prefetch_files",
                   "files_created": 10})
        # Browser history
        audit.log({"service": "BrowserHistoryService", "operation": "create_file",
                   "file_type": "sqlite_history"})
        # USN journal (35 file events → need ≥31)
        audit.log({"service": "UsnJournalWriter", "operation": "write_usn_journal",
                   "records_written": 40})
        # Zone.Identifier (5 downloads → need ≥4)
        audit.log({"service": "BrowserDownloads", "operation": "write_zone_identifier",
                   "count": 5})
        # MFT patcher
        audit.log({"service": "MftTimestampPatcher", "operation": "patch_si_timestamps",
                   "patched": 18, "skipped": 2, "failed": 0})

        results = TemporalCoherenceCheck(sched, audit).run()
        failures = [r for r in results if not r.passed]
        assert failures == [], f"Unexpected failures: {[r.detail for r in failures]}"


class TestFailingRun:
    """Verify a desynced run produces failures (§7.5 test requirement)."""

    def test_empty_audit_fails_all_checks_with_events(self, sched):
        audit = AuditLogger()
        results = TemporalCoherenceCheck(sched, audit).run()
        # All checks should fail because no services logged anything
        failures = [r for r in results if not r.passed]
        assert len(failures) == 6, (
            f"Expected 6 failures for empty audit; got {len(failures)}: "
            + str([r.name for r in failures])
        )
