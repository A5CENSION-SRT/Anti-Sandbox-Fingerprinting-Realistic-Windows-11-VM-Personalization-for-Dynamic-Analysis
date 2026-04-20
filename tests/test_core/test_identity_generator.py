"""Tests for IdentityGenerator — uses PersonaContext (canonical schema)."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.identity_generator import (
    HardwareIdentity,
    IdentityBundle,
    IdentityGenerationError,
    IdentityGenerator,
    UserIdentity,
)
from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_persona(**overrides: object) -> PersonaContext:
    defaults: dict = {
        "full_name": "Jane Smith",
        "username": "jane.smith",
        "email": "jane.smith@acmesolutions.com",
        "organization": "Acme Solutions Pvt Ltd",
        "occupation": "Marketing Manager",
        "age_range": "30-40",
        "locale": "en_US",
        "interests": PersonaInterests(
            hobbies=["reading", "yoga", "travel"],
            professional_topics=["marketing", "analytics"],
            entertainment=["documentaries"],
        ),
        "work_style": PersonaWorkStyle(
            description="Collaborative, deadline-driven",
            typical_tools=["outlook", "excel", "teams"],
        ),
        "project_names": ["Q4 Campaign", "Brand Refresh", "Customer Survey"],
        "colleague_names": [
            "John Smith", "Emily Davis", "Michael Brown",
            "Jessica Wilson", "David Lee",
        ],
        "installed_apps": ["outlook", "teams"],
        "browsing_categories": ["business", "news"],
        "daily_avg_sites": 15,
        "work_hours_start": 9,
        "work_hours_end": 17,
        "active_days": [1, 2, 3, 4, 5],
    }
    defaults.update(overrides)
    return PersonaContext(**defaults)


def _hw_data_path(tmp_path: Path) -> Path:
    data = {
        "system_vendors": [
            {
                "bios_vendor": "Dell Inc.",
                "bios_versions": ["1.15.0", "2.3.1"],
                "motherboard_models": ["Dell Inc. 0T7D40", "Dell Inc. 06D7TR"],
            },
            {
                "bios_vendor": "Lenovo",
                "bios_versions": ["N2CET64W (1.41)"],
                "motherboard_models": ["Lenovo 20Y7S0GV00"],
            },
        ],
        "disk_models": [
            "Samsung SSD 870 EVO 500GB",
            "WDC WD10EZEX-08WN4A0",
        ],
        "gpu_models": [
            "NVIDIA GeForce RTX 3060",
            "Intel UHD Graphics 630",
        ],
    }
    hw_path = tmp_path / "hardware_models.json"
    hw_path.write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return _hw_data_path(tmp_path)


@pytest.fixture()
def office_persona() -> PersonaContext:
    return _make_persona()


@pytest.fixture()
def developer_persona() -> PersonaContext:
    return _make_persona(
        full_name="Dev User",
        username="dev.user",
        email="dev.user@techcorp.com",
        organization="TechCorp Inc",
        occupation="Software Engineer",
        installed_apps=["vscode", "docker", "git", "terminal"],
        browsing_categories=["stackoverflow", "github"],
        daily_avg_sites=80,
        profile_archetype="developer",
    )


@pytest.fixture()
def home_persona() -> PersonaContext:
    return _make_persona(
        full_name="Home Person",
        username="home.person",
        email="home.person@personal.com",
        organization="Personal",
        occupation="Homeowner",
        installed_apps=["spotify", "vlc"],
        browsing_categories=["social", "entertainment"],
        daily_avg_sites=20,
        work_hours_start=18,
        work_hours_end=23,
        active_days=[6, 7],
        profile_archetype="home_user",
    )


# ---------------------------------------------------------------------------
# 1. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_persona_same_output(self, data_dir: Path, office_persona: PersonaContext) -> None:
        gen = IdentityGenerator(office_persona, data_dir)
        assert gen.generate() == gen.generate()

    def test_new_instance_same_output(self, data_dir: Path, office_persona: PersonaContext) -> None:
        a = IdentityGenerator(office_persona, data_dir).generate()
        b = IdentityGenerator(office_persona, data_dir).generate()
        assert a == b


# ---------------------------------------------------------------------------
# 2. Different personas produce different bundles
# ---------------------------------------------------------------------------

class TestDifferentPersonas:
    def test_different_usernames(
        self, data_dir: Path, office_persona: PersonaContext
    ) -> None:
        other = _make_persona(username="other.user", email="other.user@acmesolutions.com")
        a = IdentityGenerator(office_persona, data_dir).generate()
        b = IdentityGenerator(other, data_dir).generate()
        assert a.user.full_name != b.user.full_name

    def test_developer_vs_home(
        self,
        data_dir: Path,
        developer_persona: PersonaContext,
        home_persona: PersonaContext,
    ) -> None:
        a = IdentityGenerator(developer_persona, data_dir).generate()
        b = IdentityGenerator(home_persona, data_dir).generate()
        assert a.user.username != b.user.username


# ---------------------------------------------------------------------------
# 3. Email matches org
# ---------------------------------------------------------------------------

class TestEmailDomain:
    def test_email_domain_derived_from_org(
        self, data_dir: Path, office_persona: PersonaContext
    ) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        domain = bundle.user.email.split("@")[1]
        assert domain == "acmesolutions.com"

    def test_personal_org_uses_personal_domain(
        self, data_dir: Path, home_persona: PersonaContext
    ) -> None:
        bundle = IdentityGenerator(home_persona, data_dir).generate()
        domain = bundle.user.email.split("@")[1]
        assert domain == "personal.com"


# ---------------------------------------------------------------------------
# 4. Username rules
# ---------------------------------------------------------------------------

class TestUsernameRules:
    def test_lowercase(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert bundle.user.username == bundle.user.username.lower()

    def test_no_spaces(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert " " not in bundle.user.username

    def test_valid_chars(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert re.fullmatch(r"[a-z0-9.]+", bundle.user.username)


# ---------------------------------------------------------------------------
# 5. Computer name has no VM strings
# ---------------------------------------------------------------------------

class TestComputerName:
    _VM_PATTERNS = ["VBOX", "VMWARE", "VIRTUAL", "TEST-PC", "SANDBOX", "HYPERV"]

    def _assert_no_vm(self, bundle: IdentityBundle) -> None:
        upper = bundle.user.computer_name.upper()
        for p in self._VM_PATTERNS:
            assert p not in upper, f"VM string '{p}' found in computer name"

    def test_office(self, data_dir: Path, office_persona: PersonaContext) -> None:
        self._assert_no_vm(IdentityGenerator(office_persona, data_dir).generate())

    def test_developer(self, data_dir: Path, developer_persona: PersonaContext) -> None:
        self._assert_no_vm(IdentityGenerator(developer_persona, data_dir).generate())

    def test_home(self, data_dir: Path, home_persona: PersonaContext) -> None:
        self._assert_no_vm(IdentityGenerator(home_persona, data_dir).generate())


# ---------------------------------------------------------------------------
# 6. Hardware from data file
# ---------------------------------------------------------------------------

class TestHardwareFromData:
    def test_bios_vendor_from_data(self, data_dir: Path, office_persona: PersonaContext) -> None:
        hw = json.loads((data_dir / "hardware_models.json").read_text())
        vendors = {v["bios_vendor"] for v in hw["system_vendors"]}
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert bundle.hardware.bios_vendor in vendors

    def test_disk_from_data(self, data_dir: Path, office_persona: PersonaContext) -> None:
        hw = json.loads((data_dir / "hardware_models.json").read_text())
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert bundle.hardware.disk_model in hw["disk_models"]

    def test_gpu_from_data(self, data_dir: Path, office_persona: PersonaContext) -> None:
        hw = json.loads((data_dir / "hardware_models.json").read_text())
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert bundle.hardware.gpu_model in hw["gpu_models"]

    def test_disk_serial_format(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert re.fullmatch(r"[A-Z0-9]+", bundle.hardware.disk_serial)
        assert 10 <= len(bundle.hardware.disk_serial) <= 16

    def test_bios_date_plausible(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        assert isinstance(bundle.hardware.bios_release_date, date)
        assert bundle.hardware.bios_release_date <= date.today()


# ---------------------------------------------------------------------------
# 7. Immutability
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_bundle_frozen(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        with pytest.raises((ValidationError, TypeError)):
            bundle.user = UserIdentity(
                full_name="x", username="x", email="x@x.com",
                organization="x", computer_name="x",
            )

    def test_user_frozen(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        with pytest.raises((ValidationError, TypeError)):
            bundle.user.username = "hacked"


# ---------------------------------------------------------------------------
# 8. No empty strings
# ---------------------------------------------------------------------------

class TestNoEmptyStrings:
    def test_user_fields(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        for f in UserIdentity.model_fields:
            v = getattr(bundle.user, f)
            if isinstance(v, str):
                assert v, f"user.{f} is empty"

    def test_hardware_fields(self, data_dir: Path, office_persona: PersonaContext) -> None:
        bundle = IdentityGenerator(office_persona, data_dir).generate()
        for f in HardwareIdentity.model_fields:
            v = getattr(bundle.hardware, f)
            if isinstance(v, str):
                assert v, f"hardware.{f} is empty"


# ---------------------------------------------------------------------------
# 9. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_missing_data_dir(self, tmp_path: Path, office_persona: PersonaContext) -> None:
        with pytest.raises(FileNotFoundError):
            IdentityGenerator(office_persona, tmp_path / "nope")

    def test_missing_hardware_json(self, tmp_path: Path, office_persona: PersonaContext) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            IdentityGenerator(office_persona, empty)

    def test_malformed_json(self, tmp_path: Path, office_persona: PersonaContext) -> None:
        (tmp_path / "hardware_models.json").write_text("{invalid json")
        with pytest.raises(IdentityGenerationError, match="parse"):
            IdentityGenerator(office_persona, tmp_path)

    def test_missing_required_keys(self, tmp_path: Path, office_persona: PersonaContext) -> None:
        (tmp_path / "hardware_models.json").write_text("{}")
        with pytest.raises(IdentityGenerationError, match="missing required"):
            IdentityGenerator(office_persona, tmp_path)
