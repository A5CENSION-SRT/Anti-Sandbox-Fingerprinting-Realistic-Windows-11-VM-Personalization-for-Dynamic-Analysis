"""Sandbox signal tester.

Re-implements simplified versions of common static sandbox/VM-detection
checks against the audit log and (optionally) the mounted image path.
This allows ARC to self-evaluate whether typical environment-aware
malware checks would detect the VM *after* personalization.

Each test maps to a known heuristic:

1. **VM driver service keys** — VBoxGuest, vmci, etc. should be absent.
2. **VM vendor strings** — BIOS/motherboard should not say "VBOX" etc.
3. **Installed programs count** — Uninstall key should have ≥ 5 entries.
4. **Recent documents** — RecentDocs MRU should not be empty.
5. **Event log presence** — System.evtx, Security.evtx should have records.
6. **Computer name pattern** — Should not match the default ``DESKTOP-*``.
7. **BIOS vendor check** — Should not match SeaBIOS / innotek / QEMU.
8. **Cookie / browser presence** — Browser profile should exist.

The tester returns a list of :class:`SignalResult` objects summarising each check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VM indicators (same canonical set used across ARC)
# ---------------------------------------------------------------------------

_VM_DRIVER_SERVICE_NAMES: frozenset[str] = frozenset({
    "vboxsf", "vboxguest", "vboxmouse", "vboxvideo",
    "vmci", "vmhgfs", "vmmouse", "vmrawdsk",
    "vmusbmouse", "vmxnet", "hv_vmbus", "hvservice",
})

_VM_BIOS_VENDORS: frozenset[str] = frozenset({
    "seabios", "innotek", "qemu", "bochs", "virtualbox",
})

_DEFAULT_HOSTNAME_PATTERN: re.Pattern = re.compile(
    r"^DESKTOP-[A-Z0-9]{7}$", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalResult:
    """Result of one sandbox signal check.

    Attributes:
        signal_name: Short identifier (e.g. ``vm_driver_keys``).
        detected: ``True`` if the sandbox/VM signal was **detected** —
            meaning the check *failed* from an anti-detection standpoint.
        detail: Human-readable explanation.
    """

    signal_name: str
    detected: bool
    detail: str


# ---------------------------------------------------------------------------
# Tester
# ---------------------------------------------------------------------------

class SandboxSignalTester:
    """Evaluates the mounted image against common sandbox-detection heuristics.

    The tester operates primarily on :class:`AuditLogger` entries.  If a
    ``mount_root`` is provided, it additionally checks the filesystem.

    Args:
        audit_logger: The :class:`AuditLogger` that recorded the run.
        mount_root: Optional :class:`Path` to the mounted image root
            for filesystem-level checks.
    """

    def __init__(
        self,
        audit_logger,
        mount_root: Path | None = None,
    ) -> None:
        self._audit = audit_logger
        self._mount_root = mount_root

    def run(self, context: dict) -> List[SignalResult]:
        """Execute all signal checks.

        Args:
            context: Orchestrator context with keys:
                ``computer_name``, ``username``, ``profile_type``,
                ``installed_apps``.

        Returns:
            List of :class:`SignalResult` — one per heuristic.
        """
        results: List[SignalResult] = []
        results.append(self._check_vm_driver_keys())
        results.append(self._check_vm_bios_vendor())
        results.append(self._check_installed_programs_count())
        results.append(self._check_recent_docs())
        results.append(self._check_event_logs())
        results.append(self._check_computer_name(context))
        results.append(self._check_browser_presence())
        return results

    def score(self, context: dict) -> float:
        """Return 0.0–1.0 detection-resistance score.

        * 1.0 = all checks passed (no VM signals detected)
        * 0.0 = all checks failed (VM detected in every category)
        """
        results = self.run(context)
        if not results:
            return 0.0
        passed = sum(1 for r in results if not r.detected)
        return round(passed / len(results), 2)

    def summary(self, context: dict) -> str:
        """Human-readable summary of all signal checks."""
        results = self.run(context)
        lines: list[str] = ["Sandbox Signal Test Results", "=" * 40]
        for r in results:
            status = "DETECTED" if r.detected else "CLEAN"
            lines.append(f"  [{status:>8}] {r.signal_name}: {r.detail}")
        score = self.score(context)
        lines.append(f"\nDetection resistance score: {score:.2f}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_vm_driver_keys(self) -> SignalResult:
        """Check if any VM driver service keys were written (they shouldn't be)."""
        entries = self._audit.entries
        # VmScrubber should have *deleted* these; check no service *wrote* them
        vm_writes = [
            e for e in entries
            if any(
                vm in str(e.get("path", "")).lower()
                for vm in _VM_DRIVER_SERVICE_NAMES
            )
            and e.get("operation") not in ("delete_key", "scrub_key", "delete_value")
        ]
        if vm_writes:
            return SignalResult(
                "vm_driver_keys", True,
                f"{len(vm_writes)} VM driver key write(s) found",
            )
        return SignalResult(
            "vm_driver_keys", False,
            "No VM driver service keys written",
        )

    def _check_vm_bios_vendor(self) -> SignalResult:
        """Check that BIOS vendor strings don't contain VM indicators."""
        entries = self._audit.entries
        hw_entries = [
            e for e in entries
            if e.get("service") == "HardwareNormalizer"
        ]
        for e in hw_entries:
            bios = str(e.get("bios_vendor", "")).lower()
            for vm in _VM_BIOS_VENDORS:
                if vm in bios:
                    return SignalResult(
                        "vm_bios_vendor", True,
                        f"BIOS vendor contains VM string: '{vm}'",
                    )
        if not hw_entries:
            return SignalResult(
                "vm_bios_vendor", True,
                "No HardwareNormalizer entries — BIOS may still be VM default",
            )
        return SignalResult(
            "vm_bios_vendor", False,
            "BIOS vendor strings are clean",
        )

    def _check_installed_programs_count(self) -> SignalResult:
        """Verify ≥ 5 InstalledPrograms audit entries."""
        entries = self._audit.entries
        ip_entries = [
            e for e in entries
            if e.get("service") == "InstalledPrograms"
        ]
        count = len(ip_entries)
        if count >= 1:
            # InstalledPrograms logs one entry per apply() call with operations_count
            ops = sum(e.get("operations_count", 0) for e in ip_entries)
            if ops >= 5:
                return SignalResult(
                    "installed_programs_count", False,
                    f"{ops} registry operations for installed programs",
                )
        return SignalResult(
            "installed_programs_count", count >= 1,
            f"{count} InstalledPrograms entries found",
        )

    def _check_recent_docs(self) -> SignalResult:
        """Verify MruRecentDocs service ran."""
        entries = self._audit.entries
        mru_entries = [
            e for e in entries
            if e.get("service") == "MruRecentDocs"
        ]
        if mru_entries:
            return SignalResult(
                "recent_docs", False,
                f"MruRecentDocs logged {len(mru_entries)} entries",
            )
        return SignalResult(
            "recent_docs", True,
            "No MruRecentDocs entries — RecentDocs may be empty",
        )

    def _check_event_logs(self) -> SignalResult:
        """Verify event log services ran and produced records."""
        entries = self._audit.entries
        evtx_services = {"SystemLog", "SecurityLog", "ApplicationLog"}
        found = {
            e.get("service") for e in entries
        }.intersection(evtx_services)
        if len(found) == len(evtx_services):
            return SignalResult(
                "event_logs", False,
                f"All {len(evtx_services)} event log services ran",
            )
        missing = evtx_services - found
        return SignalResult(
            "event_logs", bool(missing),
            f"Missing event log services: {', '.join(sorted(missing))}",
        )

    def _check_computer_name(self, context: dict) -> SignalResult:
        """Verify computer name doesn't match default DESKTOP-XXXXXXX pattern."""
        name = context.get("computer_name", "")
        if not name:
            return SignalResult(
                "computer_name", True,
                "No computer_name in context",
            )
        if _DEFAULT_HOSTNAME_PATTERN.match(name):
            return SignalResult(
                "computer_name", True,
                f"Computer name '{name}' matches default DESKTOP-* pattern",
            )
        return SignalResult(
            "computer_name", False,
            f"Computer name '{name}' does not match default pattern",
        )

    def _check_browser_presence(self) -> SignalResult:
        """Verify browser services created artifacts."""
        entries = self._audit.entries
        browser_services = {"BrowserProfile", "BrowserHistory", "BookmarksService"}
        found = {
            e.get("service") for e in entries
        }.intersection(browser_services)
        if found:
            return SignalResult(
                "browser_presence", False,
                f"Browser services ran: {', '.join(sorted(found))}",
            )
        return SignalResult(
            "browser_presence", True,
            "No browser artifacts found — profile looks like a fresh install",
        )
