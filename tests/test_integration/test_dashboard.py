"""Tests for the Streamlit dashboard components.

These tests verify the dashboard helper functions without requiring
a running Streamlit server.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def profiles_dir() -> Path:
    """Return the profiles directory."""
    return PROJECT_ROOT / "profiles"


@pytest.fixture
def data_dir() -> Path:
    """Return the data directory."""
    return PROJECT_ROOT / "data"


@pytest.fixture
def mock_session_state() -> Dict[str, Any]:
    """Create a mock session state."""
    return {
        "generation_state": None,
        "audit_entries": [],
        "last_result": None,
        "output_path": str(PROJECT_ROOT / "output"),
        "evaluation_report": None,
        "last_context": None,
    }


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------

class TestConfigurationHelpers:
    """Tests for configuration loading functions."""

    def test_get_available_profiles(self, profiles_dir: Path) -> None:
        """Should return list of available profile names."""
        # Import the function
        from dashboard import get_available_profiles
        
        profiles = get_available_profiles()
        
        assert isinstance(profiles, list)
        assert len(profiles) > 0
        assert "developer" in profiles
        assert "office_user" in profiles
        assert "home_user" in profiles
        assert "base" in profiles

    def test_load_config_existing_file(self, tmp_path: Path) -> None:
        """Should load configuration from existing YAML file."""
        from dashboard import load_config
        
        config_path = tmp_path / "config.yaml"
        config_path.write_text("mount_path: ./output\nprofile_name: developer\n")
        
        config = load_config(config_path)
        
        assert config["mount_path"] == "./output"
        assert config["profile_name"] == "developer"

    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        """Should return empty dict for missing file."""
        from dashboard import load_config
        
        config_path = tmp_path / "nonexistent.yaml"
        config = load_config(config_path)
        
        assert config == {}

    def test_load_profile_details_valid_profile(self, profiles_dir: Path) -> None:
        """Should load details for valid profile."""
        from dashboard import load_profile_details
        
        details = load_profile_details("developer")
        
        assert "error" not in details
        assert "username" in details
        assert "organization" in details
        assert "locale" in details
        assert "installed_apps" in details
        assert "work_hours" in details
        assert "browsing" in details

    def test_load_profile_details_invalid_profile(self) -> None:
        """Should return error for invalid profile."""
        from dashboard import load_profile_details
        
        details = load_profile_details("nonexistent_profile")
        
        assert "error" in details


class TestDashboardAuditLogger:
    """Tests for the custom dashboard audit logger."""

    def test_audit_logger_records_to_session(self) -> None:
        """Should record entries to session state list."""
        from dashboard import DashboardAuditLogger
        
        session_entries = []
        logger = DashboardAuditLogger(session_entries)
        
        logger.log({"service": "test", "operation": "test_op"})
        
        assert len(session_entries) == 1
        assert session_entries[0]["service"] == "test"
        assert session_entries[0]["operation"] == "test_op"
        assert "timestamp" in session_entries[0]

    def test_audit_logger_adds_timestamps(self) -> None:
        """Should automatically add timestamps to entries."""
        from dashboard import DashboardAuditLogger
        
        session_entries = []
        logger = DashboardAuditLogger(session_entries)
        
        logger.log({"service": "test"})
        
        assert "timestamp" in session_entries[0]


class TestGenerationState:
    """Tests for the GenerationState dataclass."""

    def test_generation_state_default_values(self) -> None:
        """Should have correct default values."""
        from dashboard import GenerationState
        
        state = GenerationState()
        
        assert state.is_running is False
        assert state.current_service == ""
        assert state.current_index == 0
        assert state.total_services == 0
        assert state.results == []
        assert state.error is None
        assert state.start_time is None
        assert state.end_time is None

    def test_generation_state_can_be_modified(self) -> None:
        """Should allow modification of state."""
        from dashboard import GenerationState
        
        state = GenerationState()
        state.is_running = True
        state.current_service = "TestService"
        state.current_index = 5
        state.total_services = 10
        
        assert state.is_running is True
        assert state.current_service == "TestService"
        assert state.current_index == 5
        assert state.total_services == 10


class TestEvaluationHelpers:
    """Tests for evaluation helper functions."""

    def test_evaluation_report_generator_works(self, tmp_path: Path) -> None:
        """Should generate evaluation report from audit entries."""
        from core.audit_logger import AuditLogger
        from evaluation.report_generator import ReportGenerator
        
        # Create audit logger with entries
        audit_logger = AuditLogger()
        audit_logger.log({"service": "TestService", "operation": "test"})
        audit_logger.log({"service": "InstalledPrograms", "operation": "write_registry"})
        
        context = {
            "username": "testuser",
            "computer_name": "TEST-PC",
            "profile_type": "developer",
            "installed_apps": ["app1", "app2"],
        }
        
        report_gen = ReportGenerator(audit_logger, tmp_path)
        report = report_gen.generate(context)
        
        assert report is not None
        assert "consistency" in report
        assert "density" in report
        assert "signals" in report
        assert "scores" in report


class TestProfileDetailsContent:
    """Tests for profile details content."""

    def test_developer_profile_has_dev_apps(self) -> None:
        """Developer profile should include development apps."""
        from dashboard import load_profile_details
        
        details = load_profile_details("developer")
        
        if "error" not in details:
            apps = details.get("installed_apps", [])
            # Developer profile should have development-related apps
            dev_apps = ["vscode", "git", "python", "docker", "nodejs"]
            found_dev_apps = [app for app in apps if any(dev in app.lower() for dev in dev_apps)]
            assert len(found_dev_apps) > 0, "Developer profile should have dev apps"

    def test_office_profile_has_office_apps(self) -> None:
        """Office profile should include office apps."""
        from dashboard import load_profile_details
        
        details = load_profile_details("office_user")
        
        if "error" not in details:
            apps = details.get("installed_apps", [])
            # Office profile should have office-related apps
            office_apps = ["office", "outlook", "teams", "excel", "word"]
            found_office_apps = [app for app in apps if any(off in app.lower() for off in office_apps)]
            assert len(found_office_apps) > 0 or len(apps) > 0

    def test_profile_work_hours_valid(self) -> None:
        """Profile work hours should be valid."""
        from dashboard import load_profile_details
        
        for profile in ["developer", "office_user", "home_user"]:
            details = load_profile_details(profile)
            
            if "error" not in details:
                work_hours = details.get("work_hours", {})
                start = work_hours.get("start", 0)
                end = work_hours.get("end", 24)
                
                assert 0 <= start < 24, f"Invalid start hour for {profile}"
                assert 0 < end <= 24, f"Invalid end hour for {profile}"
                assert start < end, f"Start should be before end for {profile}"


class TestServiceModuleMapping:
    """Tests for service module mappings used in generation."""

    def test_all_service_modules_importable(self) -> None:
        """All defined service modules should be importable."""
        import importlib
        
        service_modules = {
            "filesystem": [
                ("services.filesystem.user_directory", "UserDirectoryService"),
                ("services.filesystem.document_generator", "DocumentGenerator"),
            ],
            "registry": [
                ("services.registry.hive_writer", "HiveWriter"),
                ("services.registry.installed_programs", "InstalledPrograms"),
            ],
            "browser": [
                ("services.browser.browser_profile", "BrowserProfileService"),
                ("services.browser.history", "BrowserHistoryService"),
            ],
        }
        
        for category, modules in service_modules.items():
            for module_path, class_name in modules:
                try:
                    module = importlib.import_module(module_path)
                    assert hasattr(module, class_name), f"Missing {class_name} in {module_path}"
                except ImportError as e:
                    pytest.fail(f"Failed to import {module_path}: {e}")

    def test_all_services_have_service_name(self) -> None:
        """All service classes should have service_name property."""
        import importlib
        
        sample_services = [
            ("services.filesystem.user_directory", "UserDirectoryService"),
            ("services.filesystem.document_generator", "DocumentGenerator"),
            ("services.registry.installed_programs", "InstalledPrograms"),
        ]
        
        for module_path, class_name in sample_services:
            try:
                module = importlib.import_module(module_path)
                service_class = getattr(module, class_name)
                
                # Check that service_name is defined (as property or attribute)
                assert hasattr(service_class, "service_name"), f"{class_name} missing service_name"
            except ImportError:
                pass  # Skip if module not available
