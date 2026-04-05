"""Evaluation report generator.

Orchestrates :class:`ConsistencyChecker`, :class:`DensityAnalyzer`, and
:class:`SandboxSignalTester` into a single structured evaluation report.

The report is returned as both a Python dict (for programmatic consumption)
and a formatted Markdown string (for human review / ``docs/evaluation_report.md``
updates).

Usage
-----
::

    from evaluation.report_generator import ReportGenerator

    gen = ReportGenerator(audit_logger)
    report = gen.generate(context)
    print(report["markdown"])
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from evaluation.consistency_checker import ConsistencyChecker, CheckResult
from evaluation.density_analyzer import DensityAnalyzer, CategoryDensity
from evaluation.sandbox_signal_tester import SandboxSignalTester, SignalResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generates a unified evaluation report from all evaluation modules.

    Args:
        audit_logger: The :class:`AuditLogger` that recorded the run.
        mount_root: Optional mount root Path for filesystem-level checks.
    """

    def __init__(self, audit_logger, mount_root=None) -> None:
        self._audit = audit_logger
        self._mount_root = mount_root

    def generate(self, context: dict) -> Dict[str, Any]:
        """Run all evaluations and return a structured report.

        Args:
            context: Orchestrator context dict.

        Returns:
            Dict with keys:

            * ``consistency`` — list of :class:`CheckResult` dicts
            * ``density`` — dict of category → :class:`CategoryDensity` dicts
            * ``signals`` — list of :class:`SignalResult` dicts
            * ``scores`` — summary scores
            * ``markdown`` — formatted Markdown string
            * ``generated_at`` — ISO timestamp
        """
        # Run all modules
        checker = ConsistencyChecker(self._audit)
        consistency_results = checker.run(context)

        analyzer = DensityAnalyzer(self._audit)
        density_results = analyzer.analyze(context)
        density_score = analyzer.overall_score(context)

        tester = SandboxSignalTester(self._audit, self._mount_root)
        signal_results = tester.run(context)
        signal_score = tester.score(context)

        # Build structured output
        report: Dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profile_type": context.get("profile_type", "unknown"),
            "computer_name": context.get("computer_name", "unknown"),
            "consistency": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in consistency_results
            ],
            "density": {
                cat: {
                    "entry_count": d.entry_count,
                    "min_baseline": d.min_baseline,
                    "typical_baseline": d.typical_baseline,
                    "meets_minimum": d.meets_minimum,
                    "density_ratio": d.density_ratio,
                }
                for cat, d in density_results.items()
            },
            "signals": [
                {
                    "signal_name": s.signal_name,
                    "detected": s.detected,
                    "detail": s.detail,
                }
                for s in signal_results
            ],
            "scores": {
                "consistency": sum(
                    1 for r in consistency_results if r.passed
                ) / max(len(consistency_results), 1),
                "density": density_score,
                "detection_resistance": signal_score,
            },
        }

        report["markdown"] = self._render_markdown(
            report, consistency_results, density_results,
            signal_results, density_score, signal_score,
        )

        return report

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def _render_markdown(
        self,
        report: dict,
        consistency: List[CheckResult],
        density: Dict[str, CategoryDensity],
        signals: List[SignalResult],
        density_score: float,
        signal_score: float,
    ) -> str:
        """Format the report as Markdown."""
        lines: list[str] = []
        lines.append("# ARC Evaluation Report")
        lines.append("")
        lines.append(f"**Generated:** {report['generated_at']}")
        lines.append(f"**Profile:** {report['profile_type']}")
        lines.append(f"**Computer:** {report['computer_name']}")
        lines.append("")

        # Scores
        lines.append("## Summary Scores")
        lines.append("")
        lines.append("| Metric | Score |")
        lines.append("|--------|-------|")
        for name, val in report["scores"].items():
            pct = f"{val:.0%}"
            lines.append(f"| {name} | {pct} |")
        lines.append("")

        # Consistency
        lines.append("## Consistency Checks")
        lines.append("")
        lines.append("| Check | Result | Detail |")
        lines.append("|-------|--------|--------|")
        for r in consistency:
            icon = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.name} | {icon} | {r.detail} |")
        lines.append("")

        # Density
        lines.append("## Artifact Density")
        lines.append("")
        lines.append("| Category | Count | Min | Typical | Ratio | Status |")
        lines.append("|----------|-------|-----|---------|-------|--------|")
        for cat in sorted(density.keys()):
            d = density[cat]
            status = "PASS" if d.meets_minimum else "FAIL"
            lines.append(
                f"| {cat} | {d.entry_count} | {d.min_baseline} | "
                f"{d.typical_baseline} | {d.density_ratio:.2f} | {status} |"
            )
        lines.append(f"\n**Overall density score:** {density_score:.2f}")
        lines.append("")

        # Signals
        lines.append("## Sandbox Signal Tests")
        lines.append("")
        lines.append("| Signal | Status | Detail |")
        lines.append("|--------|--------|--------|")
        for s in signals:
            status = "DETECTED" if s.detected else "CLEAN"
            lines.append(f"| {s.signal_name} | {status} | {s.detail} |")
        lines.append(f"\n**Detection resistance score:** {signal_score:.2f}")
        lines.append("")

        return "\n".join(lines)
