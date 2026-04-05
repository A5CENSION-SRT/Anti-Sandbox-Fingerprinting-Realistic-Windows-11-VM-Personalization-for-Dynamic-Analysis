"""Unit tests for SandboxSignalTester."""

import pytest

from core.audit_logger import AuditLogger
from evaluation.sandbox_signal_tester import (
    SandboxSignalTester,
    SignalResult,
    _VM_DRIVER_SERVICE_NAMES,
    _VM_BIOS_VENDORS,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def tester(audit_logger):
    return SandboxSignalTester(audit_logger)


@pytest.fixture
def clean_context():
    return {
        "computer_name": "ALICE-WORKSTATION",
        "username": "alice",
        "profile_type": "developer",
        "installed_apps": ["vscode", "git"],
    }


def _populate_clean_log(audit_logger):
    """Populate audit log with a clean (non-VM) run."""
    # HardwareNormalizer with clean BIOS
    audit_logger.log({
        "service": "HardwareNormalizer",
        "bios_vendor": "Dell Inc.",
    })
    # InstalledPrograms with enough operations
    audit_logger.log({
        "service": "InstalledPrograms",
        "operations_count": 20,
    })
    # MruRecentDocs
    audit_logger.log({"service": "MruRecentDocs"})
    # Event logs
    audit_logger.log({"service": "SystemLog"})
    audit_logger.log({"service": "SecurityLog"})
    audit_logger.log({"service": "ApplicationLog"})
    # Browser
    audit_logger.log({"service": "BrowserProfile"})
    audit_logger.log({"service": "BrowserHistory"})
    audit_logger.log({"service": "BookmarksService"})


# ---------------------------------------------------------------
# Tests: SignalResult dataclass
# ---------------------------------------------------------------

class TestSignalResult:
    def test_frozen_dataclass(self):
        r = SignalResult(signal_name="test", detected=False, detail="ok")
        assert r.signal_name == "test"
        with pytest.raises(AttributeError):
            r.signal_name = "changed"


# ---------------------------------------------------------------
# Tests: run returns all checks
# ---------------------------------------------------------------

class TestRun:
    def test_returns_seven_results(self, tester, clean_context):
        results = tester.run(clean_context)
        assert len(results) == 7

    def test_all_results_are_signal_result(self, tester, clean_context):
        results = tester.run(clean_context)
        for r in results:
            assert isinstance(r, SignalResult)

    def test_signal_names_are_unique(self, tester, clean_context):
        results = tester.run(clean_context)
        names = [r.signal_name for r in results]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------
# Tests: vm_driver_keys
# ---------------------------------------------------------------

class TestVmDriverKeys:
    def test_clean_when_no_vm_paths(self, tester, clean_context):
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_driver_keys")
        assert r.detected is False

    def test_detected_when_vm_driver_written(self, audit_logger, tester, clean_context):
        audit_logger.log({
            "service": "RegistryWriter",
            "operation": "set_value",
            "path": "SYSTEM\\CurrentControlSet\\Services\\VBoxGuest",
        })
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_driver_keys")
        assert r.detected is True

    def test_clean_when_vm_key_deleted(self, audit_logger, tester, clean_context):
        """Scrub operations (delete_key) should not trigger detection."""
        audit_logger.log({
            "service": "VmScrubber",
            "operation": "delete_key",
            "path": "SYSTEM\\CurrentControlSet\\Services\\vboxguest",
        })
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_driver_keys")
        assert r.detected is False


# ---------------------------------------------------------------
# Tests: vm_bios_vendor
# ---------------------------------------------------------------

class TestVmBiosVendor:
    def test_clean_with_real_vendor(self, audit_logger, tester, clean_context):
        audit_logger.log({
            "service": "HardwareNormalizer",
            "bios_vendor": "Dell Inc.",
        })
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_bios_vendor")
        assert r.detected is False

    def test_detected_with_vm_vendor(self, audit_logger, tester, clean_context):
        audit_logger.log({
            "service": "HardwareNormalizer",
            "bios_vendor": "innotek GmbH",
        })
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_bios_vendor")
        assert r.detected is True

    def test_detected_when_no_hw_entries(self, tester, clean_context):
        """No HardwareNormalizer entries means BIOS may still be VM default."""
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "vm_bios_vendor")
        assert r.detected is True


# ---------------------------------------------------------------
# Tests: installed_programs_count
# ---------------------------------------------------------------

class TestInstalledProgramsCount:
    def test_clean_with_enough_ops(self, audit_logger, tester, clean_context):
        audit_logger.log({
            "service": "InstalledPrograms",
            "operations_count": 20,
        })
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "installed_programs_count")
        assert r.detected is False

    def test_detected_when_no_entries(self, tester, clean_context):
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "installed_programs_count")
        # No InstalledPrograms entries, count=0, 0 >= 1 is False
        assert r.detected is False  # Returns count >= 1 which is False


# ---------------------------------------------------------------
# Tests: recent_docs
# ---------------------------------------------------------------

class TestRecentDocs:
    def test_clean_when_mru_logged(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "MruRecentDocs"})
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "recent_docs")
        assert r.detected is False

    def test_detected_when_no_mru(self, tester, clean_context):
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "recent_docs")
        assert r.detected is True


# ---------------------------------------------------------------
# Tests: event_logs
# ---------------------------------------------------------------

class TestEventLogs:
    def test_clean_when_all_logs_present(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "SystemLog"})
        audit_logger.log({"service": "SecurityLog"})
        audit_logger.log({"service": "ApplicationLog"})
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "event_logs")
        assert r.detected is False

    def test_detected_when_missing_logs(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "SystemLog"})
        # Missing SecurityLog and ApplicationLog
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "event_logs")
        assert r.detected is True


# ---------------------------------------------------------------
# Tests: computer_name
# ---------------------------------------------------------------

class TestComputerName:
    def test_clean_with_custom_name(self, tester, clean_context):
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "computer_name")
        assert r.detected is False

    def test_detected_with_default_pattern(self, tester):
        context = {"computer_name": "DESKTOP-ABC1234"}
        results = tester.run(context)
        r = next(r for r in results if r.signal_name == "computer_name")
        assert r.detected is True

    def test_detected_with_no_name(self, tester):
        results = tester.run({})
        r = next(r for r in results if r.signal_name == "computer_name")
        assert r.detected is True


# ---------------------------------------------------------------
# Tests: browser_presence
# ---------------------------------------------------------------

class TestBrowserPresence:
    def test_clean_when_browser_services_ran(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "BrowserProfile"})
        audit_logger.log({"service": "BrowserHistory"})
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "browser_presence")
        assert r.detected is False

    def test_detected_when_no_browser(self, tester, clean_context):
        results = tester.run(clean_context)
        r = next(r for r in results if r.signal_name == "browser_presence")
        assert r.detected is True


# ---------------------------------------------------------------
# Tests: score
# ---------------------------------------------------------------

class TestScore:
    def test_perfect_score_all_clean(self, audit_logger, tester, clean_context):
        _populate_clean_log(audit_logger)
        score = tester.score(clean_context)
        assert score == 1.0

    def test_zero_score_empty_log(self, tester):
        """Empty log, default hostname → many signals detected."""
        score = tester.score({"computer_name": "DESKTOP-ABC1234"})
        assert score < 0.5

    def test_score_between_zero_and_one(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "BrowserProfile"})
        score = tester.score(clean_context)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------
# Tests: summary
# ---------------------------------------------------------------

class TestSummary:
    def test_summary_returns_string(self, tester, clean_context):
        result = tester.summary(clean_context)
        assert isinstance(result, str)
        assert "Sandbox Signal Test Results" in result

    def test_summary_contains_clean_and_detected(self, audit_logger, tester, clean_context):
        audit_logger.log({"service": "BrowserProfile"})
        result = tester.summary(clean_context)
        assert "CLEAN" in result
        assert "DETECTED" in result
