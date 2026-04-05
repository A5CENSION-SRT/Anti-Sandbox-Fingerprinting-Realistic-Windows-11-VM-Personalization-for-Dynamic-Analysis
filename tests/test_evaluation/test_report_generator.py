"""Unit tests for ReportGenerator."""

import pytest

from core.audit_logger import AuditLogger
from evaluation.report_generator import ReportGenerator


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def generator(audit_logger):
    return ReportGenerator(audit_logger)


@pytest.fixture
def context():
    return {
        "computer_name": "ALICE-WORKSTATION",
        "username": "alice",
        "profile_type": "developer",
        "installed_apps": ["vscode", "git"],
    }


def _populate_log(audit_logger):
    """Populate log with a realistic run."""
    audit_logger.log({
        "service": "HardwareNormalizer",
        "bios_vendor": "Dell Inc.",
    })
    audit_logger.log({
        "service": "InstalledPrograms",
        "operations_count": 20,
        "computer_name": "ALICE-WORKSTATION",
        "username": "alice",
    })
    audit_logger.log({"service": "MruRecentDocs"})
    audit_logger.log({"service": "SystemLog"})
    audit_logger.log({"service": "SecurityLog"})
    audit_logger.log({"service": "ApplicationLog"})
    audit_logger.log({"service": "BrowserProfile"})
    audit_logger.log({"service": "BrowserHistory"})


# ---------------------------------------------------------------
# Tests: generate returns structured report
# ---------------------------------------------------------------

class TestGenerate:
    def test_returns_dict(self, generator, context):
        report = generator.generate(context)
        assert isinstance(report, dict)

    def test_has_required_keys(self, generator, context):
        report = generator.generate(context)
        for key in ("consistency", "density", "signals", "scores",
                     "markdown", "generated_at"):
            assert key in report, f"Missing key: {key}"

    def test_consistency_is_list(self, generator, context):
        report = generator.generate(context)
        assert isinstance(report["consistency"], list)
        assert len(report["consistency"]) == 6  # 6 checks

    def test_density_is_dict(self, generator, context):
        report = generator.generate(context)
        assert isinstance(report["density"], dict)
        assert "registry" in report["density"]

    def test_signals_is_list(self, generator, context):
        report = generator.generate(context)
        assert isinstance(report["signals"], list)
        assert len(report["signals"]) == 7  # 7 signal checks

    def test_scores_contains_all_metrics(self, generator, context):
        report = generator.generate(context)
        scores = report["scores"]
        assert "consistency" in scores
        assert "density" in scores
        assert "detection_resistance" in scores

    def test_scores_are_numeric(self, generator, context):
        report = generator.generate(context)
        for key, val in report["scores"].items():
            assert isinstance(val, (int, float)), f"{key} is not numeric"
            assert 0.0 <= val <= 1.0

    def test_generated_at_is_iso(self, generator, context):
        report = generator.generate(context)
        # ISO format should contain 'T'
        assert "T" in report["generated_at"]

    def test_profile_type_in_report(self, generator, context):
        report = generator.generate(context)
        assert report["profile_type"] == "developer"

    def test_computer_name_in_report(self, generator, context):
        report = generator.generate(context)
        assert report["computer_name"] == "ALICE-WORKSTATION"


# ---------------------------------------------------------------
# Tests: consistency results structure
# ---------------------------------------------------------------

class TestConsistencyResults:
    def test_each_result_has_fields(self, generator, context):
        report = generator.generate(context)
        for r in report["consistency"]:
            assert "name" in r
            assert "passed" in r
            assert "detail" in r


# ---------------------------------------------------------------
# Tests: density results structure
# ---------------------------------------------------------------

class TestDensityResults:
    def test_each_category_has_fields(self, generator, context):
        report = generator.generate(context)
        for cat, d in report["density"].items():
            assert "entry_count" in d
            assert "min_baseline" in d
            assert "typical_baseline" in d
            assert "meets_minimum" in d
            assert "density_ratio" in d


# ---------------------------------------------------------------
# Tests: signal results structure
# ---------------------------------------------------------------

class TestSignalResults:
    def test_each_signal_has_fields(self, generator, context):
        report = generator.generate(context)
        for s in report["signals"]:
            assert "signal_name" in s
            assert "detected" in s
            assert "detail" in s


# ---------------------------------------------------------------
# Tests: markdown output
# ---------------------------------------------------------------

class TestMarkdown:
    def test_markdown_is_string(self, generator, context):
        report = generator.generate(context)
        assert isinstance(report["markdown"], str)

    def test_markdown_contains_header(self, generator, context):
        report = generator.generate(context)
        assert "# ARC Evaluation Report" in report["markdown"]

    def test_markdown_contains_scores_table(self, generator, context):
        report = generator.generate(context)
        md = report["markdown"]
        assert "## Summary Scores" in md
        assert "consistency" in md
        assert "density" in md
        assert "detection_resistance" in md

    def test_markdown_contains_consistency_section(self, generator, context):
        report = generator.generate(context)
        assert "## Consistency Checks" in report["markdown"]

    def test_markdown_contains_density_section(self, generator, context):
        report = generator.generate(context)
        assert "## Artifact Density" in report["markdown"]

    def test_markdown_contains_signals_section(self, generator, context):
        report = generator.generate(context)
        assert "## Sandbox Signal Tests" in report["markdown"]

    def test_markdown_with_populated_log(self, audit_logger, generator, context):
        _populate_log(audit_logger)
        report = generator.generate(context)
        md = report["markdown"]
        assert "PASS" in md
        assert "CLEAN" in md


# ---------------------------------------------------------------
# Tests: integration with populated log
# ---------------------------------------------------------------

class TestIntegration:
    def test_scores_improve_with_populated_log(self, audit_logger, generator, context):
        # Empty log scores
        empty_report = generator.generate(context)

        _populate_log(audit_logger)
        full_report = generator.generate(context)

        # At minimum, detection_resistance should improve
        assert (full_report["scores"]["detection_resistance"]
                >= empty_report["scores"]["detection_resistance"])
