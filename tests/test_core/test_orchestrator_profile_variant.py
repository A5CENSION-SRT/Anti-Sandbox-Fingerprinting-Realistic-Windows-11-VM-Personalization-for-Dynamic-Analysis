"""Tests for orchestrator profile-type variant resolution."""

from pathlib import Path

from core.audit_logger import AuditLogger
from core.orchestrator import Orchestrator
from services.registry.hive_writer import HiveWriter
from services.registry.mru_recentdocs import MruRecentDocs
from services.registry.network_profiles import NetworkProfiles
from services.registry.userassist import UserAssist


def _make_orchestrator(config: dict) -> Orchestrator:
    """Construct an orchestrator for testing helper behavior."""
    return Orchestrator(config=config, audit_logger=AuditLogger(), dry_run=True)


def test_resolve_profile_variant_from_alias_name(tmp_path: Path) -> None:
    """Known profile aliases should normalize to service-compatible variants."""
    orchestrator = _make_orchestrator({"profile_name": "home"})

    variant = orchestrator._resolve_profile_variant("home", tmp_path)

    assert variant == "home_user"


def test_resolve_profile_variant_from_generated_extends(tmp_path: Path) -> None:
    """Generated profile names should resolve from their extends field."""
    profiles_dir = tmp_path / "profiles"
    generated_dir = profiles_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    generated_profile = generated_dir / "persona.yaml"
    generated_profile.write_text("extends: office_user\n", encoding="utf-8")

    orchestrator = _make_orchestrator({"profile_name": "generated/persona"})

    variant = orchestrator._resolve_profile_variant("generated/persona", profiles_dir)

    assert variant == "office_user"


def test_resolve_profile_variant_falls_back_to_home_user(tmp_path: Path) -> None:
    """Unknown custom profile names should use safe default variant."""
    orchestrator = _make_orchestrator({"profile_name": "custom_profile"})

    variant = orchestrator._resolve_profile_variant("custom_profile", tmp_path)

    assert variant == "home_user"


def test_initialize_sets_generated_profile_type_from_extends(tmp_path: Path) -> None:
    """Initialize should expose service-compatible profile_type for generated profiles."""
    profiles_dir = tmp_path / "profiles"
    generated_dir = profiles_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    (profiles_dir / "base.yaml").write_text(
        """
username: base.user
organization: Personal
locale: en_US
installed_apps: []
browsing:
  categories: [general]
  daily_avg_sites: 5
work_hours:
  start: 9
  end: 17
  active_days: [1, 2, 3, 4, 5]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    (profiles_dir / "home_user.yaml").write_text(
        """
extends: base
username: home.user
installed_apps:
  - chrome
""".strip()
        + "\n",
        encoding="utf-8",
    )

    (generated_dir / "persona.yaml").write_text(
        """
extends: home_user
username: generated.persona
""".strip()
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    config = {
        "mount_path": str(output_dir),
        "profiles_dir": str(profiles_dir),
        "data_dir": str(Path("data").resolve()),
        "profile_name": "generated/persona",
        "timeline_days": 30,
    }
    orchestrator = _make_orchestrator(config)

    orchestrator.initialize()
    try:
        assert orchestrator.context["profile_name"] == "generated/persona"
        assert orchestrator.context["profile_type"] == "home_user"
    finally:
        orchestrator.cleanup()


def test_generated_profile_registry_services_execute_without_profile_type_errors(
    tmp_path: Path,
) -> None:
    """Generated profile names should not break strict registry profile lookups."""
    profiles_dir = tmp_path / "profiles"
    generated_dir = profiles_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    (profiles_dir / "base.yaml").write_text(
        """
username: base.user
organization: Personal
locale: en_US
installed_apps: []
browsing:
  categories: [general]
  daily_avg_sites: 5
work_hours:
  start: 9
  end: 17
  active_days: [1, 2, 3, 4, 5]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (profiles_dir / "home_user.yaml").write_text(
        """
extends: base
username: home.user
installed_apps:
  - chrome
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (generated_dir / "persona.yaml").write_text(
        """
extends: home_user
username: generated.persona
""".strip()
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    config = {
        "mount_path": str(output_dir),
        "profiles_dir": str(profiles_dir),
        "data_dir": str(Path("data").resolve()),
        "profile_name": "generated/persona",
        "timeline_days": 30,
        "abort_on_failure": False,
    }

    orchestrator = _make_orchestrator(config)
    orchestrator._dry_run = False
    orchestrator.initialize()
    try:
        orchestrator.register_service(HiveWriter)
        orchestrator.register_service(MruRecentDocs)
        orchestrator.register_service(NetworkProfiles)
        orchestrator.register_service(UserAssist)

        result = orchestrator.run()

        assert result.services_failed == 0
        assert result.success is True
    finally:
        orchestrator.cleanup()