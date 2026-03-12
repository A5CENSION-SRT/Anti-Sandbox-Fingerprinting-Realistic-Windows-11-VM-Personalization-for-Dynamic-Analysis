"""Artifact density analyzer.

Compares the number and distribution of artifacts produced by ARC against
a reference baseline representing a real Windows 11 installation.  The
analysis helps assess whether the mounted image has enough artifacts to
pass sandbox-detection density thresholds.

The analyzer works on :class:`AuditLogger` entries — it does **not** read
the mounted image directly, keeping it side-effect-free and testable.

Usage
-----
::

    from evaluation.density_analyzer import DensityAnalyzer

    analyzer = DensityAnalyzer(audit_logger)
    report = analyzer.analyze(context)
    for category, metrics in report.items():
        print(category, metrics)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reference baselines — approximate artifact counts from a real Win11 system
# ---------------------------------------------------------------------------

_REFERENCE_BASELINE: Dict[str, Dict[str, int]] = {
    "registry": {
        "min_entries": 50,
        "typical_entries": 150,
    },
    "eventlog": {
        "min_entries": 30,
        "typical_entries": 100,
    },
    "filesystem": {
        "min_entries": 20,
        "typical_entries": 80,
    },
    "browser": {
        "min_entries": 5,
        "typical_entries": 30,
    },
}

# Map service names → category
_SERVICE_CATEGORY: Dict[str, str] = {
    # Registry
    "HiveWriter": "registry",
    "SystemIdentity": "registry",
    "InstalledPrograms": "registry",
    "NetworkProfiles": "registry",
    "MruRecentDocs": "registry",
    "UserAssist": "registry",
    "VmScrubber": "registry",
    "HardwareNormalizer": "registry",
    "ProcessFaker": "registry",
    # Event log
    "SystemLog": "eventlog",
    "SecurityLog": "eventlog",
    "ApplicationLog": "eventlog",
    "UpdateArtifacts": "eventlog",
    # Filesystem / applications
    "OfficeArtifacts": "filesystem",
    "DevEnvironment": "filesystem",
    "EmailClient": "filesystem",
    "CommsApps": "filesystem",
    # Browser
    "BrowserProfile": "browser",
    "BrowserHistory": "browser",
    "BrowserDownloads": "browser",
    "BookmarksService": "browser",
    "CookiesCache": "browser",
}


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CategoryDensity:
    """Density metrics for one artifact category.

    Attributes:
        category: Category name (e.g. ``registry``, ``eventlog``).
        entry_count: Number of audit entries in this category.
        min_baseline: Minimum expected count from reference.
        typical_baseline: Typical count from reference.
        meets_minimum: Whether ``entry_count >= min_baseline``.
        density_ratio: ``entry_count / typical_baseline``.
    """

    category: str
    entry_count: int
    min_baseline: int
    typical_baseline: int
    meets_minimum: bool = False
    density_ratio: float = 0.0

    def __post_init__(self) -> None:
        self.meets_minimum = self.entry_count >= self.min_baseline
        if self.typical_baseline > 0:
            self.density_ratio = round(
                self.entry_count / self.typical_baseline, 2,
            )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class DensityAnalyzer:
    """Compares artifact counts against reference baselines.

    Args:
        audit_logger: The :class:`AuditLogger` that recorded the run.
    """

    def __init__(self, audit_logger) -> None:
        self._audit = audit_logger

    def analyze(self, context: dict | None = None) -> Dict[str, CategoryDensity]:
        """Compute per-category density metrics.

        Args:
            context: Optional orchestrator context (unused currently,
                reserved for per-profile baseline selection).

        Returns:
            Dict mapping category name → :class:`CategoryDensity`.
        """
        counts = self._count_by_category()

        results: Dict[str, CategoryDensity] = {}
        for cat, baseline in _REFERENCE_BASELINE.items():
            results[cat] = CategoryDensity(
                category=cat,
                entry_count=counts.get(cat, 0),
                min_baseline=baseline["min_entries"],
                typical_baseline=baseline["typical_entries"],
            )
        return results

    def overall_score(self, context: dict | None = None) -> float:
        """Return a 0.0–1.0 overall density score.

        The score is the average density_ratio across all categories,
        capped at 1.0.

        Returns:
            Float between 0.0 and 1.0.
        """
        densities = self.analyze(context)
        if not densities:
            return 0.0
        total = sum(min(d.density_ratio, 1.0) for d in densities.values())
        return round(total / len(densities), 2)

    def summary(self, context: dict | None = None) -> str:
        """Return a human-readable density summary.

        Returns:
            Multi-line string with per-category metrics.
        """
        densities = self.analyze(context)
        lines: list[str] = ["Artifact Density Report", "=" * 40]
        for cat, d in sorted(densities.items()):
            status = "PASS" if d.meets_minimum else "FAIL"
            lines.append(
                f"  {cat:<12}: {d.entry_count:>4} entries "
                f"(min={d.min_baseline}, typical={d.typical_baseline}) "
                f"ratio={d.density_ratio:.2f} [{status}]"
            )
        score = self.overall_score(context)
        lines.append(f"\nOverall score: {score:.2f}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_by_category(self) -> Dict[str, int]:
        """Count audit entries per artifact category."""
        counts: Dict[str, int] = {}
        for entry in self._audit.entries:
            service = entry.get("service", "")
            cat = _SERVICE_CATEGORY.get(service)
            if cat:
                counts[cat] = counts.get(cat, 0) + 1
        return counts
