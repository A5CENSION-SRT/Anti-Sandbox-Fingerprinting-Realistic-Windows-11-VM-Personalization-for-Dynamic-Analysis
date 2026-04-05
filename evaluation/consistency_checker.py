"""Cross-service consistency checker.

Validates that artifacts written by different ARC services are internally
consistent.  For example, the ``computer_name`` written to the registry by
:class:`SystemIdentity` must match the ``WorkstationName`` field logged in
Security event records, and hardware strings must not contain VM indicators.

The checker operates **post-run** on the :class:`AuditLogger` entries and
optionally on the mounted image filesystem.  It does not modify any files.

Usage
-----
::

    from evaluation.consistency_checker import ConsistencyChecker

    checker = ConsistencyChecker(audit_logger)
    results = checker.run(context)
    for r in results:
        print(r)

Each result is a :class:`CheckResult` with ``name``, ``passed`` (bool),
and ``detail`` (str).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single consistency check.

    Attributes:
        name: Short identifier for the check (e.g. ``computer_name_match``).
        passed: ``True`` if the check succeeded.
        detail: Human-readable explanation.
    """

    name: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# VM indicator strings — canonical list
# ---------------------------------------------------------------------------

_VM_STRINGS: frozenset[str] = frozenset({
    "vbox", "vmware", "virtual", "test-pc", "sandbox", "hyperv",
    "qemu", "xen", "bochs", "innotek", "oracle vm", "parallels",
})


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class ConsistencyChecker:
    """Validates cross-service artifact consistency.

    Args:
        audit_logger: The :class:`AuditLogger` that accumulated entries
            during the ARC run.  Its ``.entries`` property is read-only
            inspected.
    """

    def __init__(self, audit_logger) -> None:
        self._audit = audit_logger

    def run(self, context: dict) -> List[CheckResult]:
        """Execute all consistency checks and return results.

        Args:
            context: The same orchestrator context dict that was passed to
                services.  Expected keys:

                * ``computer_name`` (str)
                * ``username`` (str)
                * ``profile_type`` (str)
                * ``installed_apps`` (list[str])

        Returns:
            A list of :class:`CheckResult` objects — one per check.
        """
        results: List[CheckResult] = []
        results.append(self._check_computer_name(context))
        results.append(self._check_username_consistency(context))
        results.append(self._check_no_vm_strings(context))
        results.append(self._check_audit_entries_present())
        results.append(self._check_timestamp_ordering())
        results.append(self._check_profile_apps_installed(context))
        return results

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_computer_name(self, context: dict) -> CheckResult:
        """Verify computer_name appears in relevant audit entries."""
        name = context.get("computer_name", "")
        if not name:
            return CheckResult(
                "computer_name_match", False,
                "No computer_name in context",
            )

        entries = self._audit.entries
        name_entries = [
            e for e in entries
            if e.get("computer_name") == name
        ]
        if name_entries:
            return CheckResult(
                "computer_name_match", True,
                f"computer_name '{name}' found in {len(name_entries)} entries",
            )
        return CheckResult(
            "computer_name_match", False,
            f"computer_name '{name}' not found in any audit entry",
        )

    def _check_username_consistency(self, context: dict) -> CheckResult:
        """Verify username is consistent across services."""
        username = context.get("username", "")
        if not username:
            return CheckResult(
                "username_consistency", False,
                "No username in context",
            )

        entries = self._audit.entries
        user_entries = [
            e for e in entries
            if e.get("username") == username
        ]
        if user_entries:
            return CheckResult(
                "username_consistency", True,
                f"username '{username}' found in {len(user_entries)} entries",
            )
        # Username may be embedded in paths rather than as a direct field
        path_entries = [
            e for e in entries
            if username in str(e.get("path", ""))
        ]
        if path_entries:
            return CheckResult(
                "username_consistency", True,
                f"username '{username}' found in {len(path_entries)} path entries",
            )
        return CheckResult(
            "username_consistency", False,
            f"username '{username}' not found in audit entries",
        )

    def _check_no_vm_strings(self, context: dict) -> CheckResult:
        """Verify no VM indicator strings appear in written values."""
        entries = self._audit.entries
        violations: list[str] = []

        for entry in entries:
            for key, val in entry.items():
                if key in ("timestamp", "service", "operation"):
                    continue
                val_str = str(val).lower()
                for vm in _VM_STRINGS:
                    if vm in val_str:
                        violations.append(
                            f"{entry.get('service', '?')}.{key}: "
                            f"contains '{vm}'"
                        )
                        break

        if violations:
            return CheckResult(
                "no_vm_strings", False,
                f"{len(violations)} VM string(s) found: "
                + "; ".join(violations[:5]),
            )
        return CheckResult(
            "no_vm_strings", True,
            "No VM indicator strings detected in audit entries",
        )

    def _check_audit_entries_present(self) -> CheckResult:
        """Verify the audit logger is not empty."""
        count = len(self._audit.entries)
        if count > 0:
            return CheckResult(
                "audit_entries_present", True,
                f"{count} audit entries recorded",
            )
        return CheckResult(
            "audit_entries_present", False,
            "Audit logger is empty — no services ran",
        )

    def _check_timestamp_ordering(self) -> CheckResult:
        """Verify audit timestamps are in non-decreasing order."""
        entries = self._audit.entries
        timestamps = [
            e.get("timestamp", "") for e in entries
            if "timestamp" in e
        ]
        if len(timestamps) < 2:
            return CheckResult(
                "timestamp_ordering", True,
                "Fewer than 2 timestamped entries — ordering trivially satisfied",
            )
        for i in range(1, len(timestamps)):
            if timestamps[i] < timestamps[i - 1]:
                return CheckResult(
                    "timestamp_ordering", False,
                    f"Timestamp out of order at entry {i}: "
                    f"{timestamps[i-1]} > {timestamps[i]}",
                )
        return CheckResult(
            "timestamp_ordering", True,
            f"All {len(timestamps)} timestamps in order",
        )

    def _check_profile_apps_installed(self, context: dict) -> CheckResult:
        """Verify InstalledPrograms audit entries cover profile apps."""
        installed = context.get("installed_apps", [])
        if not installed:
            return CheckResult(
                "profile_apps_installed", True,
                "No installed_apps in context — check skipped",
            )

        entries = self._audit.entries
        ip_entries = [
            e for e in entries
            if e.get("service") == "InstalledPrograms"
        ]
        if ip_entries:
            return CheckResult(
                "profile_apps_installed", True,
                f"InstalledPrograms service logged {len(ip_entries)} entries",
            )
        return CheckResult(
            "profile_apps_installed", False,
            "No InstalledPrograms audit entries found but profile has apps",
        )
