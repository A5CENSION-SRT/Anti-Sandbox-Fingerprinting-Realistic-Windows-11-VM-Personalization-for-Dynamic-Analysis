"""Unit tests for ConsistencyChecker."""

import pytest

from core.audit_logger import AuditLogger
from evaluation.consistency_checker import ConsistencyChecker, CheckResult


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def checker(audit_logger):
    return ConsistencyChecker(audit_logger)


@pytest.fixture
def base_context():
    return {
        "computer_name": "ALICE-WORKSTATION",
        "username": "alice",
        "profile_type": "developer",
        "installed_apps": ["vscode", "git", "docker"],
    }


# ---------------------------------------------------------------
# Tests: CheckResult dataclass
# ---------------------------------------------------------------

class TestCheckResult:
    def test_frozen_dataclass(self):
        r = CheckResult(name="test", passed=True, detail="ok")
        assert r.name == "test"
        assert r.passed is True
        with pytest.raises(AttributeError):
            r.name = "changed"


# ---------------------------------------------------------------
# Tests: run returns all checks
# ---------------------------------------------------------------

class TestRun:
    def test_returns_six_results(self, checker, base_context):
        results = checker.run(base_context)
        assert len(results) == 6

    def test_all_results_are_check_result(self, checker, base_context):
        results = checker.run(base_context)
        for r in results:
            assert isinstance(r, CheckResult)

    def test_check_names_are_unique(self, checker, base_context):
        results = checker.run(base_context)
        names = [r.name for r in results]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------
# Tests: computer_name_match
# ---------------------------------------------------------------

class TestComputerNameMatch:
    def test_passes_when_name_in_entries(self, audit_logger, checker, base_context):
        audit_logger.log({
            "service": "SystemIdentity",
            "computer_name": "ALICE-WORKSTATION",
        })
        results = checker.run(base_context)
        cn = next(r for r in results if r.name == "computer_name_match")
        assert cn.passed is True

    def test_fails_when_name_not_in_entries(self, checker, base_context):
        results = checker.run(base_context)
        cn = next(r for r in results if r.name == "computer_name_match")
        assert cn.passed is False

    def test_fails_when_no_name_in_context(self, checker):
        results = checker.run({})
        cn = next(r for r in results if r.name == "computer_name_match")
        assert cn.passed is False


# ---------------------------------------------------------------
# Tests: username_consistency
# ---------------------------------------------------------------

class TestUsernameConsistency:
    def test_passes_with_direct_username_field(self, audit_logger, checker, base_context):
        audit_logger.log({"service": "X", "username": "alice"})
        results = checker.run(base_context)
        u = next(r for r in results if r.name == "username_consistency")
        assert u.passed is True

    def test_passes_with_username_in_path(self, audit_logger, checker, base_context):
        audit_logger.log({
            "service": "X",
            "path": "C:\\mount\\Users\\alice\\AppData",
        })
        results = checker.run(base_context)
        u = next(r for r in results if r.name == "username_consistency")
        assert u.passed is True

    def test_fails_when_username_absent(self, checker, base_context):
        results = checker.run(base_context)
        u = next(r for r in results if r.name == "username_consistency")
        assert u.passed is False

    def test_fails_when_no_username_in_context(self, checker):
        results = checker.run({})
        u = next(r for r in results if r.name == "username_consistency")
        assert u.passed is False


# ---------------------------------------------------------------
# Tests: no_vm_strings
# ---------------------------------------------------------------

class TestNoVmStrings:
    def test_passes_when_clean(self, audit_logger, checker, base_context):
        audit_logger.log({
            "service": "InstalledPrograms",
            "app_name": "Chrome",
        })
        results = checker.run(base_context)
        vm = next(r for r in results if r.name == "no_vm_strings")
        assert vm.passed is True

    def test_fails_when_vmware_in_value(self, audit_logger, checker, base_context):
        audit_logger.log({
            "service": "HardwareNormalizer",
            "bios_vendor": "VMware Inc.",
        })
        results = checker.run(base_context)
        vm = next(r for r in results if r.name == "no_vm_strings")
        assert vm.passed is False

    def test_fails_when_vbox_in_value(self, audit_logger, checker, base_context):
        audit_logger.log({
            "service": "SystemIdentity",
            "driver_name": "VBoxGuest",
        })
        results = checker.run(base_context)
        vm = next(r for r in results if r.name == "no_vm_strings")
        assert vm.passed is False

    def test_ignores_timestamp_service_operation(self, audit_logger, checker, base_context):
        """VM strings in 'timestamp', 'service', 'operation' keys should be ignored."""
        audit_logger.log({
            "service": "VmScrubber",
            "operation": "scrub_virtual_drivers",
            "result": "clean",
        })
        results = checker.run(base_context)
        vm = next(r for r in results if r.name == "no_vm_strings")
        assert vm.passed is True


# ---------------------------------------------------------------
# Tests: audit_entries_present
# ---------------------------------------------------------------

class TestAuditEntriesPresent:
    def test_passes_when_entries_exist(self, audit_logger, checker, base_context):
        audit_logger.log({"service": "X"})
        results = checker.run(base_context)
        ae = next(r for r in results if r.name == "audit_entries_present")
        assert ae.passed is True

    def test_fails_when_empty(self, checker, base_context):
        results = checker.run(base_context)
        ae = next(r for r in results if r.name == "audit_entries_present")
        assert ae.passed is False


# ---------------------------------------------------------------
# Tests: timestamp_ordering
# ---------------------------------------------------------------

class TestTimestampOrdering:
    def test_passes_with_ordered_timestamps(self, audit_logger, checker, base_context):
        audit_logger.log({"service": "A"})
        audit_logger.log({"service": "B"})
        results = checker.run(base_context)
        to = next(r for r in results if r.name == "timestamp_ordering")
        assert to.passed is True

    def test_passes_with_single_entry(self, audit_logger, checker, base_context):
        audit_logger.log({"service": "A"})
        results = checker.run(base_context)
        to = next(r for r in results if r.name == "timestamp_ordering")
        assert to.passed is True

    def test_passes_with_no_entries(self, checker, base_context):
        results = checker.run(base_context)
        to = next(r for r in results if r.name == "timestamp_ordering")
        # Fewer than 2 → trivially passes
        assert to.passed is True


# ---------------------------------------------------------------
# Tests: profile_apps_installed
# ---------------------------------------------------------------

class TestProfileAppsInstalled:
    def test_passes_when_installed_programs_logged(
        self, audit_logger, checker, base_context,
    ):
        audit_logger.log({
            "service": "InstalledPrograms",
            "operations_count": 15,
        })
        results = checker.run(base_context)
        pa = next(r for r in results if r.name == "profile_apps_installed")
        assert pa.passed is True

    def test_fails_when_no_installed_programs_entries(self, checker, base_context):
        results = checker.run(base_context)
        pa = next(r for r in results if r.name == "profile_apps_installed")
        assert pa.passed is False

    def test_passes_when_no_apps_in_context(self, checker):
        results = checker.run({"username": "x", "computer_name": "x"})
        pa = next(r for r in results if r.name == "profile_apps_installed")
        assert pa.passed is True  # Check is skipped
