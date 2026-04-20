"""Tests for persona_loader.load_yaml."""

from pathlib import Path

import pytest
import yaml

from core.persona_loader import PersonaLoaderError, load_yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_DATA = {
    "full_name": "Alice Brennan",
    "username": "alice.brennan",
    "email": "alice.brennan@hartwell.com",
    "organization": "Hartwell Corp",
    "occupation": "Financial Analyst",
    "age_range": "35-45",
    "locale": "en_US",
    "interests": {
        "hobbies": ["cycling", "photography", "cooking"],
        "professional_topics": ["finance", "excel", "forecasting"],
        "entertainment": ["podcasts", "documentaries"],
    },
    "work_style": {
        "description": "Detail-oriented, deadline-driven",
        "typical_tools": ["excel", "outlook", "powerbi"],
    },
    "project_names": ["Q3 Budget Review", "Annual Forecast", "Cost Reduction Initiative"],
    "colleague_names": [
        "Tom Hargreaves", "Sarah Lin", "Mark Okafor", "Priya Nair", "Daniel Cruz"
    ],
    "installed_apps": ["outlook", "excel"],
    "browsing_categories": ["finance", "news"],
    "daily_avg_sites": 20,
    "work_hours_start": 8,
    "work_hours_end": 18,
    "active_days": [1, 2, 3, 4, 5],
}


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "persona.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestValidPersona:
    def test_returns_persona_context(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _VALID_DATA)
        persona = load_yaml(path)
        assert persona.full_name == "Alice Brennan"
        assert persona.username == "alice.brennan"
        assert persona.organization == "Hartwell Corp"

    def test_email_preserved(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _VALID_DATA)
        persona = load_yaml(path)
        assert persona.email == "alice.brennan@hartwell.com"

    def test_interests_loaded(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _VALID_DATA)
        persona = load_yaml(path)
        assert "cycling" in persona.interests.hobbies

    def test_work_style_loaded(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _VALID_DATA)
        persona = load_yaml(path)
        assert "excel" in persona.work_style.typical_tools


# ---------------------------------------------------------------------------
# 2. Missing file
# ---------------------------------------------------------------------------

class TestMissingFile:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PersonaLoaderError, match="not found"):
            load_yaml(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# 3. Malformed YAML
# ---------------------------------------------------------------------------

class TestMalformedYaml:
    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("{invalid yaml: [unclosed", encoding="utf-8")
        with pytest.raises(PersonaLoaderError, match="parse|Failed"):
            load_yaml(p)


# ---------------------------------------------------------------------------
# 4. Pydantic validation failures
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        data = {k: v for k, v in _VALID_DATA.items() if k != "full_name"}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)

    def test_invalid_username_pattern_raises(self, tmp_path: Path) -> None:
        data = {**_VALID_DATA, "username": "UPPERCASE_BAD"}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)

    def test_invalid_age_range_raises(self, tmp_path: Path) -> None:
        data = {**_VALID_DATA, "age_range": "thirty-five"}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)

    def test_extra_field_raises(self, tmp_path: Path) -> None:
        data = {**_VALID_DATA, "unknown_field": "should_fail"}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)

    def test_too_few_project_names_raises(self, tmp_path: Path) -> None:
        data = {**_VALID_DATA, "project_names": ["Only One", "Two"]}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)

    def test_too_few_colleague_names_raises(self, tmp_path: Path) -> None:
        data = {**_VALID_DATA, "colleague_names": ["A", "B", "C", "D"]}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(PersonaLoaderError, match="validation"):
            load_yaml(path)
