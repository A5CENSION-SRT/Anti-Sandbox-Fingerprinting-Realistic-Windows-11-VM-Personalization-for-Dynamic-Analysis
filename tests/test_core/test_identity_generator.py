"""Tests for the Identity Generation Service."""

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
from core.profile_engine import BrowsingHabits, ProfileContext, WorkHours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**overrides: object) -> ProfileContext:
    """Build a ProfileContext with sensible defaults, applying *overrides*."""
    defaults = {
        "username": "test_user",
        "organization": "Acme Solutions Pvt Ltd",
        "locale": "en_US",
        "installed_apps": ["outlook", "teams"],
        "browsing": BrowsingHabits(
            categories=["business", "news"], daily_avg_sites=15
        ),
        "work_hours": WorkHours(start=9, end=17, active_days=[1, 2, 3, 4, 5]),
    }
    defaults.update(overrides)
    return ProfileContext(**defaults)


def _hw_data_path(tmp_path: Path) -> Path:
    """Create a minimal hardware_models.json in *tmp_path* and return data dir."""
    data = {
        "system_vendors": [
            {
                "bios_vendor": "Dell Inc.",
                "bios_versions": ["1.15.0", "2.3.1"],
                "motherboard_models": [
                    "Dell Inc. 0T7D40",
                    "Dell Inc. 06D7TR",
                ],
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
    """Temporary data directory with valid hardware_models.json."""
    return _hw_data_path(tmp_path)


@pytest.fixture()
def office_profile() -> ProfileContext:
    """Office-type profile."""
    return _make_profile()


@pytest.fixture()
def developer_profile() -> ProfileContext:
    """Developer-type profile."""
    return _make_profile(
        username="dev_user",
        installed_apps=["vscode", "docker", "git", "terminal"],
        browsing=BrowsingHabits(
            categories=["stackoverflow", "github"], daily_avg_sites=30
        ),
    )


@pytest.fixture()
def home_profile() -> ProfileContext:
    """Home-type profile."""
    return _make_profile(
        username="home_person",
        organization="personal",
        installed_apps=["spotify", "vlc"],
        browsing=BrowsingHabits(
            categories=["social_media", "entertainment"], daily_avg_sites=20
        ),
        work_hours=WorkHours(start=18, end=23, active_days=[6, 7]),
    )


# ---------------------------------------------------------------------------
# 1. Deterministic output with same seed
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Two runs with same ProfileContext must produce identical output."""

    def test_same_profile_same_output(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        gen = IdentityGenerator(office_profile, data_dir)
        bundle_a = gen.generate()
        bundle_b = gen.generate()
        assert bundle_a == bundle_b

    def test_new_instance_same_output(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        gen_a = IdentityGenerator(office_profile, data_dir)
        gen_b = IdentityGenerator(office_profile, data_dir)
        assert gen_a.generate() == gen_b.generate()


# ---------------------------------------------------------------------------
# 2. Different profiles produce different identities
# ---------------------------------------------------------------------------

class TestDifferentProfiles:
    """Profiles with different usernames must produce different bundles."""

    def test_different_usernames_different_bundles(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        other = _make_profile(username="other_user")
        gen_a = IdentityGenerator(office_profile, data_dir)
        gen_b = IdentityGenerator(other, data_dir)
        bundle_a = gen_a.generate()
        bundle_b = gen_b.generate()
        assert bundle_a.user.full_name != bundle_b.user.full_name

    def test_developer_vs_home(
        self,
        data_dir: Path,
        developer_profile: ProfileContext,
        home_profile: ProfileContext,
    ) -> None:
        ba = IdentityGenerator(developer_profile, data_dir).generate()
        bb = IdentityGenerator(home_profile, data_dir).generate()
        assert ba.user.username != bb.user.username


# ---------------------------------------------------------------------------
# 3. Email matches organization domain
# ---------------------------------------------------------------------------

class TestEmailDomain:
    """Email domain must be derived from the organization name."""

    def test_email_domain_matches_org(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        domain = bundle.user.email.split("@")[1]
        # "Acme Solutions Pvt Ltd" → "acmesolutions.com"
        assert domain == "acmesolutions.com"

    def test_email_local_matches_username(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        local = bundle.user.email.split("@")[0]
        assert local == bundle.user.username

    def test_personal_org_domain(
        self, data_dir: Path, home_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(home_profile, data_dir).generate()
        domain = bundle.user.email.split("@")[1]
        assert domain == "personal.com"


# ---------------------------------------------------------------------------
# 4. Username rules enforced
# ---------------------------------------------------------------------------

class TestUsernameRules:
    """Username must be lowercase, no spaces, alphanumeric + dot only."""

    def test_lowercase(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert bundle.user.username == bundle.user.username.lower()

    def test_no_spaces(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert " " not in bundle.user.username

    def test_valid_chars_only(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert re.fullmatch(r"[a-z0-9.]+", bundle.user.username)

    def test_firstname_dot_lastname_format(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert "." in bundle.user.username


# ---------------------------------------------------------------------------
# 5. Computer name contains no VM strings
# ---------------------------------------------------------------------------

class TestComputerName:
    """Computer name must not contain known VM indicators."""

    _VM_PATTERNS = ["VBOX", "VMWARE", "VIRTUAL", "TEST-PC", "SANDBOX", "HYPERV"]

    def test_no_vm_strings_office(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        upper = bundle.user.computer_name.upper()
        for pattern in self._VM_PATTERNS:
            assert pattern not in upper

    def test_no_vm_strings_developer(
        self, data_dir: Path, developer_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(developer_profile, data_dir).generate()
        upper = bundle.user.computer_name.upper()
        for pattern in self._VM_PATTERNS:
            assert pattern not in upper

    def test_no_vm_strings_home(
        self, data_dir: Path, home_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(home_profile, data_dir).generate()
        upper = bundle.user.computer_name.upper()
        for pattern in self._VM_PATTERNS:
            assert pattern not in upper

    def test_developer_computer_name_pattern(
        self, data_dir: Path, developer_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(developer_profile, data_dir).generate()
        name = bundle.user.computer_name
        assert (
            name.startswith("DEV-") and name.endswith("-PC")
        ) or name.endswith("-WS")

    def test_office_computer_name_pattern(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        name = bundle.user.computer_name
        assert name.startswith("DESKTOP-") or "-LT-" in name


# ---------------------------------------------------------------------------
# 6. Hardware strings exist in hardware_models.json
# ---------------------------------------------------------------------------

class TestHardwareFromData:
    """All hardware identifiers must come from hardware_models.json."""

    def test_bios_vendor_from_data(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        hw_data = json.loads(
            (data_dir / "hardware_models.json").read_text(encoding="utf-8")
        )
        vendors = {v["bios_vendor"] for v in hw_data["system_vendors"]}
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert bundle.hardware.bios_vendor in vendors

    def test_motherboard_from_matching_vendor(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        hw_data = json.loads(
            (data_dir / "hardware_models.json").read_text(encoding="utf-8")
        )
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        # Find the vendor group that matches
        for group in hw_data["system_vendors"]:
            if group["bios_vendor"] == bundle.hardware.bios_vendor:
                assert bundle.hardware.motherboard_model in group["motherboard_models"]
                assert bundle.hardware.bios_version in group["bios_versions"]
                return
        pytest.fail("BIOS vendor not found in hardware data")

    def test_disk_model_from_data(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        hw_data = json.loads(
            (data_dir / "hardware_models.json").read_text(encoding="utf-8")
        )
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert bundle.hardware.disk_model in hw_data["disk_models"]

    def test_gpu_model_from_data(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        hw_data = json.loads(
            (data_dir / "hardware_models.json").read_text(encoding="utf-8")
        )
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert bundle.hardware.gpu_model in hw_data["gpu_models"]

    def test_disk_serial_format(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        serial = bundle.hardware.disk_serial
        assert 10 <= len(serial) <= 16
        assert re.fullmatch(r"[A-Z0-9]+", serial)

    def test_bios_release_date_plausible(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        assert isinstance(bundle.hardware.bios_release_date, date)
        assert bundle.hardware.bios_release_date <= date.today()


# ---------------------------------------------------------------------------
# 7. Returned bundle is immutable
# ---------------------------------------------------------------------------

class TestImmutability:
    """IdentityBundle and its sub-models must be frozen."""

    def test_bundle_frozen(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        with pytest.raises(ValidationError):
            bundle.user = UserIdentity(
                full_name="x",
                username="x",
                email="x@x.com",
                organization="x",
                computer_name="x",
            )

    def test_user_frozen(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        with pytest.raises(ValidationError):
            bundle.user.username = "hacked"

    def test_hardware_frozen(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        with pytest.raises(ValidationError):
            bundle.hardware.bios_vendor = "hacked"


# ---------------------------------------------------------------------------
# 8. No empty strings
# ---------------------------------------------------------------------------

class TestNoEmptyStrings:
    """All string fields must be non-empty."""

    def test_user_fields_non_empty(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        for field_name in UserIdentity.model_fields:
            value = getattr(bundle.user, field_name)
            if isinstance(value, str):
                assert value, f"user.{field_name} is empty"

    def test_hardware_fields_non_empty(
        self, data_dir: Path, office_profile: ProfileContext
    ) -> None:
        bundle = IdentityGenerator(office_profile, data_dir).generate()
        for field_name in HardwareIdentity.model_fields:
            value = getattr(bundle.hardware, field_name)
            if isinstance(value, str):
                assert value, f"hardware.{field_name} is empty"


# ---------------------------------------------------------------------------
# Edge-case: missing hardware data file
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Invalid data must produce clear errors."""

    def test_missing_data_dir(
        self, tmp_path: Path, office_profile: ProfileContext
    ) -> None:
        with pytest.raises(FileNotFoundError):
            IdentityGenerator(office_profile, tmp_path / "nope")

    def test_missing_hardware_json(
        self, tmp_path: Path, office_profile: ProfileContext
    ) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            IdentityGenerator(office_profile, empty_dir)

    def test_malformed_hardware_json(
        self, tmp_path: Path, office_profile: ProfileContext
    ) -> None:
        (tmp_path / "hardware_models.json").write_text("{invalid json")
        with pytest.raises(IdentityGenerationError, match="parse"):
            IdentityGenerator(office_profile, tmp_path)

    def test_missing_required_keys(
        self, tmp_path: Path, office_profile: ProfileContext
    ) -> None:
        (tmp_path / "hardware_models.json").write_text("{}")
        with pytest.raises(IdentityGenerationError, match="missing required"):
            IdentityGenerator(office_profile, tmp_path)