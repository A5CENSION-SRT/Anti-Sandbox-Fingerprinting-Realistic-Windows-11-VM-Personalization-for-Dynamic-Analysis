"""Shared pytest fixtures for service tests.

Auto-discovered by pytest for all test files in this directory.
"""

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from core.mount_manager import MountManager
from datetime import date

from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle
from core.identity_generator import HardwareIdentity, IdentityBundle, UserIdentity
from core.service_context import ServiceContext


@pytest.fixture
def mount_dir(tmp_path):
    d = tmp_path / "mount"
    d.mkdir()
    return d


@pytest.fixture
def mount_manager(mount_dir):
    return MountManager(str(mount_dir))


@pytest.fixture
def timestamp_service():
    svc = MagicMock()
    svc.get_timestamp.return_value = {
        "created": datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc),
        "modified": datetime(2025, 3, 10, 10, 0, 0, tzinfo=timezone.utc),
        "accessed": datetime(2025, 3, 10, 11, 0, 0, tzinfo=timezone.utc),
    }
    return svc


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def data_dir(tmp_path):
    """Minimal data dir with downloads catalogue, URLs, and search terms."""
    d = tmp_path / "data" / "wordlists"
    d.mkdir(parents=True)

    catalogue = {
        "home_user": [
            {
                "filename": "spotify_setup.exe",
                "mime_type": "application/octet-stream",
                "size_bytes": 47185920,
                "referrer": "https://www.spotify.com/download/",
                "url": "https://download.scdn.co/SpotifySetup.exe",
            },
            {
                "filename": "amazon_invoice.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 65536,
                "referrer": "https://www.amazon.com/",
                "url": "https://www.amazon.com/gp/css/summary/print.html",
            },
        ],
        "developer": [
            {
                "filename": "python_installer.exe",
                "mime_type": "application/octet-stream",
                "size_bytes": 26214400,
                "referrer": "https://www.python.org/downloads/",
                "url": "https://www.python.org/ftp/python/3.12.2/python-3.12.2-amd64.exe",
            },
            {
                "filename": "vscode_setup.exe",
                "mime_type": "application/octet-stream",
                "size_bytes": 90000000,
                "referrer": "https://code.visualstudio.com/download",
                "url": "https://code.visualstudio.com/sha/download?build=stable&os=win32-x64",
            },
        ],
    }
    (d / "downloads_by_profile.json").write_text(
        json.dumps(catalogue), encoding="utf-8"
    )
    urls = {
        "general": [
            {"url": "https://www.google.com/", "title": "Google"},
            {"url": "https://mail.google.com/mail/", "title": "Gmail"},
        ]
    }
    (d / "urls_by_category.json").write_text(
        json.dumps(urls), encoding="utf-8"
    )
    (d / "search_terms.txt").write_text(
        "test search term\nanother query\n", encoding="utf-8"
    )
    return d


@pytest.fixture
def persona():
    """Comprehensive PersonaContext covering developer, office, and home app sets.

    Using 'developer' archetype with a wide installed_apps list ensures all
    app-conditional service logic fires during tests (DevEnvironment, CommsApps,
    OfficeArtifacts, etc.) without needing per-test persona variants.
    """
    return PersonaContext(
        full_name="Jane Doe",
        username="jane.doe",
        email="jane.doe@corp.example.com",
        organization="Test Corp",
        occupation="Software Engineer",
        age_range="25-35",
        interests=PersonaInterests(
            hobbies=["reading", "cycling", "open source"],
            professional_topics=["software engineering", "DevOps"],
            entertainment=["podcasts", "streaming", "gaming"],
        ),
        work_style=PersonaWorkStyle(
            description="detail-oriented and collaborative",
            typical_tools=["vscode", "git", "docker", "excel", "outlook"],
        ),
        project_names=["Alpha", "Beta", "Gamma"],
        colleague_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
        installed_apps=[
            # Dev tools
            "vscode", "git", "docker", "terminal", "python",
            # Office tools
            "microsoft office", "excel", "word", "outlook", "teams",
            # Comms
            "slack", "discord", "zoom",
            # Browser + system
            "chrome", "edge", "explorer",
        ],
        browsing_categories=["news", "shopping", "research", "tech", "social"],
        daily_avg_sites=50,
        work_hours_start=9,
        work_hours_end=17,
        active_days=[1, 2, 3, 4, 5],
        timeline_days=360,
        profile_archetype="developer",
    )


@pytest.fixture
def identity_bundle():
    """Minimal IdentityBundle for service tests."""
    user = UserIdentity(
        full_name="Jane Doe",
        username="TestUser",
        email="jane.doe@corp.example.com",
        organization="Test Corp",
        computer_name="CORP-LT-001",
    )
    hardware = HardwareIdentity(
        bios_vendor="Dell Inc.",
        bios_version="A15",
        bios_release_date=date(2023, 5, 22),
        motherboard_model="Dell OptiPlex 7090",
        disk_model="Samsung SSD 870 EVO",
        disk_serial="S5ABCDEF123456",
        gpu_model="Intel UHD Graphics 630",
    )
    return IdentityBundle(user=user, hardware=hardware)


@pytest.fixture
def service_ctx(persona, mount_manager, audit_logger, timestamp_service, identity_bundle):
    """Fully wired ServiceContext for service tests (Phase 1 migration)."""
    _UTC = timezone.utc
    return ServiceContext(
        persona=persona,
        mount=mount_manager,
        rng=random.Random(42),
        audit=audit_logger,
        timestamp_service=timestamp_service,
        install_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=_UTC),
        boot_time=datetime(2024, 12, 1, 9, 0, 0, tzinfo=_UTC),
        identity_bundle=identity_bundle,
        scheduler=None,
        expansion=None,
    )


@pytest.fixture
def history_db(service_ctx, data_dir):
    """Pre-build the Chrome History SQLite DB for BrowserDownloadService tests."""
    from services.browser.history import BrowserHistoryService
    hist = BrowserHistoryService(
        service_ctx.mount,
        service_ctx.timestamp_service,
        service_ctx.audit,
        profile_config={"browsing": {"categories": ["general"], "daily_avg_sites": 3}},
        username=service_ctx.identity_bundle.user.username,
        data_dir=str(data_dir),
    )
    hist.apply(service_ctx)


def chrome_history_db(mount_manager) -> Path:
    """Return the Path to Chrome's History file under the mount root."""
    return (
        mount_manager.root / "Users" / "TestUser" / "AppData"
        / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "History"
    )
