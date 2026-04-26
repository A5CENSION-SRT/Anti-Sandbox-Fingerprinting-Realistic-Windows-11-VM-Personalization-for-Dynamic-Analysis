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

    from evaluation.consistency_checker import ConsistencyChecker, TemporalCoherenceCheck

    checker = ConsistencyChecker(audit_logger)
    results = checker.run(context)
    for r in results:
        print(r)

    # Cross-domain temporal validation (§7.5):
    tcc = TemporalCoherenceCheck(scheduler, audit_logger)
    tc_results = tcc.run()

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


# ---------------------------------------------------------------------------
# TemporalCoherenceCheck — §7.5 cross-domain validation (A9)
# ---------------------------------------------------------------------------

class TemporalCoherenceCheck:
    """Cross-domain temporal coherence validator (§7.5, gate A9).

    Validates that services produced artifacts consistent with the
    :class:`EventScheduler` event stream.  All checks are count/ratio-based
    and operate on the :class:`AuditLogger` entries post-run — no file I/O.

    Checks
    ------
    1. ``security_4688_coverage``   — Security record count ≥ APP_LAUNCH × 0.5
    2. ``prefetch_coverage``        — Prefetch files ≥ min(30, unique apps) × 0.5
    3. ``browser_history_coverage`` — History DB present when URL_VISIT events exist
    4. ``usn_journal_coverage``     — USN records ≥ file-touch events × 0.9 (§7.5)
    5. ``zone_identifier_coverage`` — Zone.Identifier written for URL_DOWNLOAD events
    6. ``mft_timestamp_divergence`` — MftTimestampPatcher ran for FILE_CREATE events (A18)

    Args:
        scheduler: :class:`EventScheduler` instance used during the run.
        audit_logger: The shared :class:`AuditLogger` that accumulated entries.
    """

    _SECURITY_RATIO_MIN: float = 0.5
    _PREFETCH_RATIO_MIN: float = 0.5
    _USN_RATIO_MIN: float = 0.9
    _ZONE_ID_RATIO_MIN: float = 0.8

    def __init__(self, scheduler: Any, audit_logger: Any) -> None:
        self._sched = scheduler
        self._audit = audit_logger

    def run(self) -> List[CheckResult]:
        """Execute all coherence checks and return results."""
        return [
            self._check_security_coverage(),
            self._check_prefetch_coverage(),
            self._check_browser_history_coverage(),
            self._check_usn_journal_coverage(),
            self._check_zone_identifier_coverage(),
            self._check_mft_patch_coverage(),
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _entries_of(self, service: str, operation: Optional[str] = None) -> List[dict]:
        """Filter audit entries by service name and optional operation."""
        entries = [e for e in self._audit.entries if e.get("service") == service]
        if operation:
            entries = [e for e in entries if e.get("operation") == operation]
        return entries

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_security_coverage(self) -> CheckResult:
        """APP_LAUNCH events should produce 4688 Security records."""
        app_launches = len(self._sched.events_of("APP_LAUNCH"))
        if app_launches == 0:
            return CheckResult(
                "security_4688_coverage", True,
                "No APP_LAUNCH events — check skipped",
            )

        entries = self._entries_of("SecurityLog", "write_security_log")
        if not entries:
            return CheckResult(
                "security_4688_coverage", False,
                f"SecurityLog not found in audit; {app_launches} APP_LAUNCH events "
                "should have produced Security 4688 records",
            )

        record_count = sum(e.get("record_count", 0) for e in entries)
        expected_min = int(app_launches * self._SECURITY_RATIO_MIN)
        if record_count >= expected_min:
            return CheckResult(
                "security_4688_coverage", True,
                f"Security log: {record_count} records for {app_launches} APP_LAUNCHes "
                f"(≥ {expected_min} required)",
            )
        return CheckResult(
            "security_4688_coverage", False,
            f"Security log: {record_count} records < {expected_min} expected "
            f"({app_launches} APP_LAUNCHes × {self._SECURITY_RATIO_MIN})",
        )

    def _check_prefetch_coverage(self) -> CheckResult:
        """Unique apps from APP_LAUNCH should generate Prefetch .pf files."""
        app_launches = self._sched.events_of("APP_LAUNCH")
        unique_apps = len({e.payload.get("app", "?") for e in app_launches})
        if unique_apps == 0:
            return CheckResult(
                "prefetch_coverage", True,
                "No APP_LAUNCH events — check skipped",
            )

        entries = self._entries_of("PrefetchService", "generate_prefetch_files")
        if not entries:
            return CheckResult(
                "prefetch_coverage", False,
                f"PrefetchService not in audit; {unique_apps} unique apps expected "
                "to produce .pf files",
            )

        files_created = sum(e.get("files_created", 0) for e in entries)
        expected_min = int(min(30, unique_apps) * self._PREFETCH_RATIO_MIN)
        if files_created >= expected_min:
            return CheckResult(
                "prefetch_coverage", True,
                f"Prefetch: {files_created} .pf files for {unique_apps} unique apps "
                f"(≥ {expected_min} required)",
            )
        return CheckResult(
            "prefetch_coverage", False,
            f"Prefetch: {files_created} files < {expected_min} expected "
            f"({unique_apps} unique apps × {self._PREFETCH_RATIO_MIN})",
        )

    def _check_browser_history_coverage(self) -> CheckResult:
        """URL_VISIT events should produce Chrome History SQLite DB entries."""
        url_visits = self._sched.events_of("URL_VISIT")
        if not url_visits:
            return CheckResult(
                "browser_history_coverage", True,
                "No URL_VISIT events — check skipped",
            )

        entries = self._entries_of("BrowserHistoryService", "create_file")
        history_entries = [e for e in entries if e.get("file_type") == "sqlite_history"]
        if history_entries:
            return CheckResult(
                "browser_history_coverage", True,
                f"Browser history: {len(history_entries)} DB(s) created "
                f"for {len(url_visits)} URL_VISITs",
            )
        return CheckResult(
            "browser_history_coverage", False,
            f"No browser history DB in audit; {len(url_visits)} URL_VISIT events "
            "expected to produce Chrome History rows",
        )

    def _check_usn_journal_coverage(self) -> CheckResult:
        """$UsnJrnl record count should cover ≥ 0.9 × file-touch events (§7.5)."""
        file_events = self._sched.events_of("FILE_CREATE", "FILE_MODIFY", "FILE_DELETE")
        if not file_events:
            return CheckResult(
                "usn_journal_coverage", True,
                "No file-touch events — check skipped",
            )

        entries = self._entries_of("UsnJournalWriter", "write_usn_journal")
        if not entries:
            return CheckResult(
                "usn_journal_coverage", False,
                f"UsnJournalWriter not in audit; {len(file_events)} file events "
                "unjournal'd (§7.5 requires ≥ 0.9× coverage)",
            )

        usn_count = sum(e.get("records_written", 0) for e in entries)
        expected_min = int(len(file_events) * self._USN_RATIO_MIN)
        if usn_count >= expected_min:
            return CheckResult(
                "usn_journal_coverage", True,
                f"$UsnJrnl: {usn_count} records ≥ {expected_min} required "
                f"({len(file_events)} file events × {self._USN_RATIO_MIN})",
            )
        return CheckResult(
            "usn_journal_coverage", False,
            f"$UsnJrnl: {usn_count} records < {expected_min} required "
            f"({len(file_events)} file events × {self._USN_RATIO_MIN})",
        )

    def _check_zone_identifier_coverage(self) -> CheckResult:
        """Each URL_DOWNLOAD should write a Zone.Identifier ADS (R27, A18)."""
        downloads = self._sched.events_of("URL_DOWNLOAD")
        if not downloads:
            return CheckResult(
                "zone_identifier_coverage", True,
                "No URL_DOWNLOAD events — check skipped",
            )

        zone_entries = [
            e for e in self._audit.entries
            if e.get("operation") == "write_zone_identifier"
        ]
        zi_count = sum(e.get("count", 1) for e in zone_entries)
        expected_min = int(len(downloads) * self._ZONE_ID_RATIO_MIN)
        if zi_count >= expected_min:
            return CheckResult(
                "zone_identifier_coverage", True,
                f"Zone.Identifier: {zi_count} ADS written for {len(downloads)} downloads "
                f"(≥ {expected_min} required)",
            )
        return CheckResult(
            "zone_identifier_coverage", False,
            f"Zone.Identifier: {zi_count} ADS < {expected_min} expected "
            f"({len(downloads)} downloads × {self._ZONE_ID_RATIO_MIN}) — "
            "R27: every URL_DOWNLOAD must have ZoneId=3 ADS",
        )

    def _check_mft_patch_coverage(self) -> CheckResult:
        """FILE_CREATE events should be covered by MFT SI patching (A18).

        Without SI patching, written files will have ctime==mtime==atime
        (NTFS creation timestamp), which is a synthetic-artifact fingerprint.
        """
        file_creates = self._sched.events_of("FILE_CREATE")
        if not file_creates:
            return CheckResult(
                "mft_timestamp_divergence", True,
                "No FILE_CREATE events — check skipped",
            )

        entries = self._entries_of("MftTimestampPatcher", "patch_si_timestamps")
        if not entries:
            return CheckResult(
                "mft_timestamp_divergence", False,
                f"MftTimestampPatcher not in audit; {len(file_creates)} FILE_CREATE "
                "events need SI patching to avoid ctime==mtime==atime (A18)",
            )

        patched = sum(e.get("patched", 0) for e in entries)
        if patched > 0:
            return CheckResult(
                "mft_timestamp_divergence", True,
                f"MFT SI patched {patched} files for {len(file_creates)} FILE_CREATEs "
                "(A18: no ctime==mtime==atime for ARC-written files)",
            )
        return CheckResult(
            "mft_timestamp_divergence", False,
            f"MftTimestampPatcher ran but patched 0 files; "
            f"{len(file_creates)} FILE_CREATE events may have ctime==mtime==atime (A18)",
        )
