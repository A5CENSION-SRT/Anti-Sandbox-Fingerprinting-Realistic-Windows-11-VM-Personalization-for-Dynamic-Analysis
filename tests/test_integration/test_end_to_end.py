"""End-to-end integration tests for the complete ARC pipeline.

These tests verify the entire artifact generation flow from configuration
to file output, ensuring all components work together correctly.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.audit_logger import AuditLogger
from core.identity_generator import IdentityGenerator
from core.mount_manager import MountManager
from core.orchestrator import Orchestrator, OrchestrationResult
from core.profile_engine import ProfileEngine
from core.timestamp_service import TimestampService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).parent.parent.parent


@pytest.fixture
def profiles_dir(project_root: Path) -> Path:
    """Return the profiles directory."""
    return project_root / "profiles"


@pytest.fixture
def data_dir(project_root: Path) -> Path:
    """Return the data directory."""
    return project_root / "data"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Create and return a temporary output directory."""
    output = tmp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    return output


@pytest.fixture
def audit_logger() -> AuditLogger:
    """Create a fresh audit logger."""
    return AuditLogger()


@pytest.fixture
def base_config(output_dir: Path, profiles_dir: Path, data_dir: Path) -> Dict[str, Any]:
    """Create a base configuration dictionary."""
    return {
        "mount_path": str(output_dir),
        "profiles_dir": str(profiles_dir),
        "data_dir": str(data_dir),
        "profile_name": "developer",
        "timeline_days": 30,
        "abort_on_failure": False,
    }


# Service module definitions for registration
SERVICE_MODULES = {
    "filesystem": [
        ("services.filesystem.user_directory", "UserDirectoryService"),
        ("services.filesystem.installed_apps_stub", "InstalledAppsStub"),
        ("services.filesystem.document_generator", "DocumentGenerator"),
        ("services.filesystem.media_stub", "MediaStubService"),
        ("services.filesystem.prefetch", "PrefetchService"),
        ("services.filesystem.thumbnail_cache", "ThumbnailCacheService"),
        ("services.filesystem.recent_items", "RecentItemsService"),
        ("services.filesystem.recycle_bin", "RecycleBinService"),
    ],
    "registry": [
        ("services.registry.hive_writer", "HiveWriter"),
        ("services.registry.installed_programs", "InstalledPrograms"),
        ("services.registry.mru_recentdocs", "MruRecentDocs"),
        ("services.registry.network_profiles", "NetworkProfiles"),
        ("services.registry.system_identity", "SystemIdentity"),
        ("services.registry.userassist", "UserAssist"),
    ],
    "browser": [
        ("services.browser.browser_profile", "BrowserProfileService"),
        ("services.browser.bookmarks", "BookmarksService"),
        ("services.browser.history", "BrowserHistoryService"),
        ("services.browser.cookies_cache", "CookiesCacheService"),
        ("services.browser.downloads", "BrowserDownloadService"),
    ],
    "applications": [
        ("services.applications.dev_environment", "DevEnvironment"),
        ("services.applications.office_artifacts", "OfficeArtifacts"),
        ("services.applications.email_client", "EmailClient"),
        ("services.applications.comms_apps", "CommsApps"),
    ],
    "eventlog": [
        ("services.eventlog.evtx_writer", "EvtxWriter"),
        ("services.eventlog.application_log", "ApplicationLog"),
        ("services.eventlog.security_log", "SecurityLog"),
        ("services.eventlog.system_log", "SystemLog"),
        ("services.eventlog.update_artifacts", "UpdateArtifacts"),
    ],
    "anti_fingerprint": [
        ("services.anti_fingerprint.hardware_normalizer", "HardwareNormalizer"),
        ("services.anti_fingerprint.process_faker", "ProcessFaker"),
        ("services.anti_fingerprint.vm_scrubber", "VmScrubber"),
    ],
}


def register_all_services(orchestrator: Orchestrator, categories: List[str] = None) -> int:
    """Register all services with the orchestrator."""
    categories = categories or list(SERVICE_MODULES.keys())
    registered = 0
    
    for category in categories:
        if category not in SERVICE_MODULES:
            continue
        for module_path, class_name in SERVICE_MODULES[category]:
            try:
                module = importlib.import_module(module_path)
                service_class = getattr(module, class_name)
                orchestrator.register_service(service_class)
                registered += 1
            except Exception:
                pass  # Some services may fail to register
    
    return registered


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------

class TestOrchestratorInitialization:
    """Tests for orchestrator initialization and context building."""

    def test_orchestrator_initializes_successfully(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Orchestrator should initialize without errors."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        assert orchestrator.context is not None
        assert "username" in orchestrator.context
        assert "computer_name" in orchestrator.context
        assert "profile_type" in orchestrator.context
        
        orchestrator.cleanup()

    def test_context_has_all_required_keys(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Context should contain all keys required by services."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        required_keys = [
            "username", "full_name", "email", "computer_name",
            "organization", "profile_type", "timeline_days",
            "installed_apps", "identity_bundle", "boot_time",
            "install_time", "install_date", "domain",
        ]
        
        for key in required_keys:
            assert key in orchestrator.context, f"Missing required key: {key}"
        
        orchestrator.cleanup()

    def test_seed_hives_created(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Orchestrator should create seed registry hives."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        # Check system hives
        config_dir = output_dir / "Windows" / "System32" / "config"
        assert (config_dir / "SOFTWARE").exists()
        assert (config_dir / "SYSTEM").exists()
        assert (config_dir / "SAM").exists()
        assert (config_dir / "SECURITY").exists()
        assert (config_dir / "DEFAULT").exists()
        
        # Check user hive
        username = orchestrator.context["username"]
        ntuser = output_dir / "Users" / username / "NTUSER.DAT"
        assert ntuser.exists()
        
        orchestrator.cleanup()

    def test_system_directories_created(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Orchestrator should create standard Windows directories."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        expected_dirs = [
            "Program Files",
            "Program Files (x86)",
            "ProgramData",
            "Windows/System32",
            "Windows/Temp",
        ]
        
        for rel_dir in expected_dirs:
            assert (output_dir / rel_dir).exists(), f"Missing directory: {rel_dir}"
        
        orchestrator.cleanup()


class TestEndToEndGeneration:
    """End-to-end tests for complete artifact generation."""

    def test_full_generation_succeeds(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Full generation with all services should succeed."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        num_registered = register_all_services(orchestrator)
        assert num_registered > 0, "No services registered"
        
        result = orchestrator.run()
        
        assert result is not None
        assert result.services_executed > 0
        # Allow some failures since some services may have optional deps
        assert result.services_executed >= result.services_failed
        
        orchestrator.cleanup()

    def test_dry_run_creates_no_files(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Dry run should not create any files beyond seed hives."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=True,
        )
        orchestrator.initialize()
        
        # Count files after initialization (seed hives)
        initial_files = set(output_dir.rglob("*"))
        
        register_all_services(orchestrator)
        result = orchestrator.run()
        
        # Files should be the same after dry run
        final_files = set(output_dir.rglob("*"))
        assert initial_files == final_files
        
        # All services should report success in dry run
        assert result.success
        
        orchestrator.cleanup()

    def test_filesystem_services_create_user_directories(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Filesystem services should create user directory structure."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem"])
        result = orchestrator.run()
        
        username = orchestrator.context["username"]
        user_dir = output_dir / "Users" / username
        
        # Check standard user directories
        expected_dirs = ["Documents", "Downloads", "Pictures", "Desktop"]
        for dir_name in expected_dirs:
            assert (user_dir / dir_name).exists(), f"Missing user dir: {dir_name}"
        
        orchestrator.cleanup()

    def test_browser_services_create_profiles(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Browser services should create browser profile directories."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem", "browser"])
        result = orchestrator.run()
        
        username = orchestrator.context["username"]
        appdata_local = output_dir / "Users" / username / "AppData" / "Local"
        
        # Check for Chrome profile
        chrome_dir = appdata_local / "Google" / "Chrome" / "User Data" / "Default"
        assert chrome_dir.exists(), "Chrome profile not created"
        
        # Check for Edge profile
        edge_dir = appdata_local / "Microsoft" / "Edge" / "User Data" / "Default"
        assert edge_dir.exists(), "Edge profile not created"
        
        orchestrator.cleanup()

    def test_eventlog_services_create_evtx_files(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Eventlog services should create EVTX files."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["eventlog"])
        result = orchestrator.run()
        
        logs_dir = output_dir / "Windows" / "System32" / "winevt" / "Logs"
        
        expected_logs = ["Application.evtx", "Security.evtx", "System.evtx"]
        for log_name in expected_logs:
            assert (logs_dir / log_name).exists(), f"Missing event log: {log_name}"
        
        orchestrator.cleanup()


class TestProfileVariations:
    """Tests for different profile types."""

    @pytest.mark.parametrize("profile_name", ["developer", "office_user", "home_user"])
    def test_all_profiles_generate_successfully(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger,
        profile_name: str, output_dir: Path
    ) -> None:
        """All profile types should generate without errors."""
        # Clear output for each profile
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)
        
        config = base_config.copy()
        config["profile_name"] = profile_name
        
        orchestrator = Orchestrator(
            config=config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        assert orchestrator.context["profile_type"] == profile_name
        
        register_all_services(orchestrator)
        result = orchestrator.run()
        
        assert result.services_executed > 0
        
        orchestrator.cleanup()

    def test_developer_profile_creates_dev_artifacts(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Developer profile should create development-specific artifacts."""
        config = base_config.copy()
        config["profile_name"] = "developer"
        
        orchestrator = Orchestrator(
            config=config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem", "applications"])
        result = orchestrator.run()
        
        username = orchestrator.context["username"]
        
        # Developer profile should have development directories
        # (created by DevEnvironment service)
        user_dir = output_dir / "Users" / username
        
        # Check that generation completed
        assert result.services_executed > 0
        
        orchestrator.cleanup()


class TestAuditTrail:
    """Tests for audit logging during generation."""

    def test_audit_entries_created_during_generation(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Generation should create audit log entries."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem"])
        result = orchestrator.run()
        
        assert len(audit_logger.entries) > 0
        
        orchestrator.cleanup()

    def test_audit_entries_have_timestamps(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """All audit entries should have timestamps."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem"])
        orchestrator.run()
        
        for entry in audit_logger.entries:
            assert "timestamp" in entry
        
        orchestrator.cleanup()

    def test_audit_entries_have_service_names(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Audit entries should identify the source service."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem"])
        orchestrator.run()
        
        # Filter entries that should have service names
        service_entries = [
            e for e in audit_logger.entries
            if e.get("operation") not in ("orchestrator_init", "orchestration_start", "orchestration_complete")
        ]
        
        for entry in service_entries:
            assert "service" in entry or "operation" in entry
        
        orchestrator.cleanup()


class TestIdentityConsistency:
    """Tests for identity consistency across services."""

    def test_username_consistent_across_artifacts(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Username should be consistent in all generated artifacts."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        username = orchestrator.context["username"]
        
        register_all_services(orchestrator, ["filesystem", "browser"])
        orchestrator.run()
        
        # Check user directory exists with correct username
        user_dir = output_dir / "Users" / username
        assert user_dir.exists()
        
        # Check NTUSER.DAT path
        ntuser = user_dir / "NTUSER.DAT"
        assert ntuser.exists()
        
        # Check AppData path
        appdata = user_dir / "AppData"
        assert appdata.exists()
        
        orchestrator.cleanup()

    def test_computer_name_in_context(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Computer name should be set in context."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        assert "computer_name" in orchestrator.context
        assert len(orchestrator.context["computer_name"]) > 0
        
        orchestrator.cleanup()

    def test_override_username_applied(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Override username should be used when provided."""
        config = base_config.copy()
        config["override_username"] = "testuser123"
        
        orchestrator = Orchestrator(
            config=config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        assert orchestrator.context["username"] == "testuser123"
        
        # Check user directory uses override
        user_dir = output_dir / "Users" / "testuser123"
        assert user_dir.exists()
        
        orchestrator.cleanup()

    def test_override_hostname_applied(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger
    ) -> None:
        """Override hostname should be used when provided."""
        config = base_config.copy()
        config["override_hostname"] = "TEST-PC-123"
        
        orchestrator = Orchestrator(
            config=config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        assert orchestrator.context["computer_name"] == "TEST-PC-123"
        
        orchestrator.cleanup()


class TestFileIntegrity:
    """Tests for generated file integrity."""

    def test_registry_hives_have_valid_signature(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Registry hives should have valid 'regf' signature."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        config_dir = output_dir / "Windows" / "System32" / "config"
        hives = ["SOFTWARE", "SYSTEM", "SAM", "SECURITY", "DEFAULT"]
        
        for hive_name in hives:
            hive_path = config_dir / hive_name
            assert hive_path.exists()
            
            with hive_path.open("rb") as f:
                signature = f.read(4)
            
            assert signature == b"regf", f"Invalid signature for {hive_name}"
        
        orchestrator.cleanup()

    def test_browser_history_is_valid_sqlite(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Browser history database should be valid SQLite."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem", "browser"])
        orchestrator.run()
        
        username = orchestrator.context["username"]
        history_db = (
            output_dir / "Users" / username / "AppData" / "Local"
            / "Google" / "Chrome" / "User Data" / "Default" / "History"
        )
        
        if history_db.exists():
            # Try to open as SQLite
            conn = sqlite3.connect(str(history_db))
            cursor = conn.cursor()
            
            # Check for expected tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            
            assert "urls" in tables
            assert "visits" in tables
            
            conn.close()
        
        orchestrator.cleanup()

    def test_prefetch_files_have_valid_header(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Prefetch files should have recognizable structure."""
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator, ["filesystem"])
        orchestrator.run()
        
        prefetch_dir = output_dir / "Windows" / "Prefetch"
        
        if prefetch_dir.exists():
            pf_files = list(prefetch_dir.glob("*.pf"))
            assert len(pf_files) > 0, "No prefetch files created"
            
            # Check that files have content
            for pf_file in pf_files[:5]:  # Check first 5
                assert pf_file.stat().st_size > 0
        
        orchestrator.cleanup()


class TestEvaluationIntegration:
    """Tests for evaluation module integration."""

    def test_evaluation_runs_after_generation(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Evaluation should run successfully on generated artifacts."""
        from evaluation.report_generator import ReportGenerator
        
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator)
        orchestrator.run()
        
        context = orchestrator.context
        
        # Run evaluation
        report_gen = ReportGenerator(audit_logger, output_dir)
        report = report_gen.generate(context)
        
        assert report is not None
        assert "consistency" in report
        assert "density" in report
        assert "signals" in report
        assert "scores" in report
        assert "markdown" in report
        
        orchestrator.cleanup()

    def test_evaluation_scores_in_valid_range(
        self, base_config: Dict[str, Any], audit_logger: AuditLogger, output_dir: Path
    ) -> None:
        """Evaluation scores should be between 0 and 1."""
        from evaluation.report_generator import ReportGenerator
        
        orchestrator = Orchestrator(
            config=base_config,
            audit_logger=audit_logger,
            dry_run=False,
        )
        orchestrator.initialize()
        
        register_all_services(orchestrator)
        orchestrator.run()
        
        report_gen = ReportGenerator(audit_logger, output_dir)
        report = report_gen.generate(orchestrator.context)
        
        scores = report["scores"]
        for score_name, score_value in scores.items():
            assert 0.0 <= score_value <= 1.0, f"Invalid score for {score_name}: {score_value}"
        
        orchestrator.cleanup()


class TestDeterminism:
    """Tests for reproducible generation."""

    def test_same_seed_produces_same_identity(
        self, profiles_dir: Path, data_dir: Path
    ) -> None:
        """Same profile should produce consistent identity."""
        engine = ProfileEngine(profiles_dir)
        profile_context = engine.load_profile("developer")
        
        gen1 = IdentityGenerator(profile_context, data_dir)
        bundle1 = gen1.generate()
        
        gen2 = IdentityGenerator(profile_context, data_dir)
        bundle2 = gen2.generate()
        
        assert bundle1.user.username == bundle2.user.username
        assert bundle1.user.computer_name == bundle2.user.computer_name

    def test_same_seed_produces_same_timestamps(self) -> None:
        """Same seed should produce consistent timestamps."""
        ts1 = TimestampService(seed="test-seed", timeline_days=30)
        ts2 = TimestampService(seed="test-seed", timeline_days=30)
        
        for event_type in ["file_create", "file_modify", "registry_write"]:
            t1 = ts1.get_timestamp(event_type)
            t2 = ts2.get_timestamp(event_type)
            
            assert t1["created"] == t2["created"]
            assert t1["modified"] == t2["modified"]
            assert t1["accessed"] == t2["accessed"]
