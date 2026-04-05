"""Unit tests for DensityAnalyzer."""

import pytest

from core.audit_logger import AuditLogger
from evaluation.density_analyzer import (
    DensityAnalyzer,
    CategoryDensity,
    _REFERENCE_BASELINE,
    _SERVICE_CATEGORY,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def analyzer(audit_logger):
    return DensityAnalyzer(audit_logger)


# ---------------------------------------------------------------
# Tests: CategoryDensity dataclass
# ---------------------------------------------------------------

class TestCategoryDensity:
    def test_meets_minimum_true(self):
        d = CategoryDensity(
            category="registry", entry_count=60,
            min_baseline=50, typical_baseline=150,
        )
        assert d.meets_minimum is True

    def test_meets_minimum_false(self):
        d = CategoryDensity(
            category="registry", entry_count=10,
            min_baseline=50, typical_baseline=150,
        )
        assert d.meets_minimum is False

    def test_density_ratio_computed(self):
        d = CategoryDensity(
            category="registry", entry_count=75,
            min_baseline=50, typical_baseline=150,
        )
        assert d.density_ratio == 0.5

    def test_density_ratio_zero_typical(self):
        d = CategoryDensity(
            category="test", entry_count=10,
            min_baseline=0, typical_baseline=0,
        )
        assert d.density_ratio == 0.0


# ---------------------------------------------------------------
# Tests: analyze
# ---------------------------------------------------------------

class TestAnalyze:
    def test_returns_all_categories(self, analyzer):
        result = analyzer.analyze()
        assert set(result.keys()) == set(_REFERENCE_BASELINE.keys())

    def test_empty_log_all_zeros(self, analyzer):
        result = analyzer.analyze()
        for cat, density in result.items():
            assert density.entry_count == 0
            assert density.meets_minimum is False

    def test_counts_registry_entries(self, audit_logger, analyzer):
        for _ in range(60):
            audit_logger.log({"service": "SystemIdentity"})
        result = analyzer.analyze()
        assert result["registry"].entry_count == 60
        assert result["registry"].meets_minimum is True

    def test_counts_eventlog_entries(self, audit_logger, analyzer):
        for _ in range(35):
            audit_logger.log({"service": "SecurityLog"})
        result = analyzer.analyze()
        assert result["eventlog"].entry_count == 35
        assert result["eventlog"].meets_minimum is True

    def test_counts_browser_entries(self, audit_logger, analyzer):
        audit_logger.log({"service": "BrowserHistory"})
        audit_logger.log({"service": "BookmarksService"})
        audit_logger.log({"service": "CookiesCache"})
        result = analyzer.analyze()
        assert result["browser"].entry_count == 3

    def test_counts_filesystem_entries(self, audit_logger, analyzer):
        audit_logger.log({"service": "OfficeArtifacts"})
        audit_logger.log({"service": "DevEnvironment"})
        audit_logger.log({"service": "EmailClient"})
        result = analyzer.analyze()
        assert result["filesystem"].entry_count == 3

    def test_unknown_service_not_counted(self, audit_logger, analyzer):
        audit_logger.log({"service": "UnknownService"})
        result = analyzer.analyze()
        total = sum(d.entry_count for d in result.values())
        assert total == 0


# ---------------------------------------------------------------
# Tests: overall_score
# ---------------------------------------------------------------

class TestOverallScore:
    def test_empty_log_zero_score(self, analyzer):
        assert analyzer.overall_score() == 0.0

    def test_full_coverage_high_score(self, audit_logger, analyzer):
        # Fill all categories to exactly their typical baseline
        for _ in range(150):
            audit_logger.log({"service": "SystemIdentity"})
        for _ in range(100):
            audit_logger.log({"service": "SystemLog"})
        for _ in range(80):
            audit_logger.log({"service": "OfficeArtifacts"})
        for _ in range(30):
            audit_logger.log({"service": "BrowserHistory"})
        score = analyzer.overall_score()
        assert score == 1.0

    def test_score_capped_at_one(self, audit_logger, analyzer):
        """Over-saturating one category doesn't push score above 1.0."""
        for _ in range(10000):
            audit_logger.log({"service": "SystemIdentity"})
        for _ in range(10000):
            audit_logger.log({"service": "SystemLog"})
        for _ in range(10000):
            audit_logger.log({"service": "OfficeArtifacts"})
        for _ in range(10000):
            audit_logger.log({"service": "BrowserHistory"})
        score = analyzer.overall_score()
        assert score <= 1.0

    def test_partial_coverage_score(self, audit_logger, analyzer):
        # Fill only registry to its typical level
        for _ in range(150):
            audit_logger.log({"service": "SystemIdentity"})
        score = analyzer.overall_score()
        # 1 category at 1.0, 3 at 0.0 → 0.25
        assert score == 0.25


# ---------------------------------------------------------------
# Tests: summary
# ---------------------------------------------------------------

class TestSummary:
    def test_summary_returns_string(self, analyzer):
        result = analyzer.summary()
        assert isinstance(result, str)
        assert "Artifact Density Report" in result

    def test_summary_contains_pass_fail(self, audit_logger, analyzer):
        for _ in range(60):
            audit_logger.log({"service": "SystemIdentity"})
        result = analyzer.summary()
        assert "PASS" in result  # registry should pass
        assert "FAIL" in result  # other categories should fail


# ---------------------------------------------------------------
# Tests: service category mapping coverage
# ---------------------------------------------------------------

class TestServiceCategoryMapping:
    def test_all_mapped_services_have_valid_category(self):
        valid_cats = set(_REFERENCE_BASELINE.keys())
        for service, cat in _SERVICE_CATEGORY.items():
            assert cat in valid_cats, f"{service} maps to unknown category {cat}"

    def test_all_baseline_categories_have_services(self):
        mapped_cats = set(_SERVICE_CATEGORY.values())
        for cat in _REFERENCE_BASELINE:
            assert cat in mapped_cats, f"Category {cat} has no service mapping"
