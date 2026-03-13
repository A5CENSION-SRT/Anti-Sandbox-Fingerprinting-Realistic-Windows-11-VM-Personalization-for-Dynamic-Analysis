"""Identity Generation Service for creating consistent fake identities.

Generates a deterministic, internally-coherent identity bundle (user + hardware)
consumed by all downstream services.  Seeded via the profile's username for
reproducibility — two runs with the same ``ProfileContext`` always produce
identical output.
"""

import hashlib
import json
import logging
import re
import string
from datetime import date
from pathlib import Path
from random import Random
from typing import Any, Dict, List

from faker import Faker
from pydantic import BaseModel

from core.profile_engine import ProfileContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEVELOPER_APPS: frozenset[str] = frozenset({
    "vscode", "docker", "git", "terminal", "vim", "intellij",
    "sublime", "atom", "neovim", "emacs", "pycharm", "webstorm",
})

_HOME_CATEGORIES: frozenset[str] = frozenset({
    "social_media", "entertainment", "gaming", "shopping", "streaming",
})

_VM_STRINGS: frozenset[str] = frozenset({
    "vbox", "vmware", "virtual", "test-pc", "sandbox", "hyperv",
})

_ORG_SUFFIXES: List[str] = [
    "pvt. ltd.", "pvt ltd", "pvt.ltd.",
    "inc.", "inc", "llc.", "llc",
    "ltd.", "ltd", "corp.", "corp",
    "co.", "co", "gmbh", "plc",
    "sa", "ag", "s.a.", "s.r.l.",
]

_FALLBACK_LOCALE: str = "en_US"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IdentityGenerationError(Exception):
    """Raised when identity generation fails."""


# ---------------------------------------------------------------------------
# Pydantic v2 models — frozen + extra=forbid
# ---------------------------------------------------------------------------

class HardwareIdentity(BaseModel):
    """Hardware fingerprint data for the VM."""

    model_config = {"frozen": True, "extra": "forbid"}

    bios_vendor: str
    bios_version: str
    bios_release_date: date
    motherboard_model: str
    disk_model: str
    disk_serial: str
    gpu_model: str


class UserIdentity(BaseModel):
    """Human identity data for the VM user."""

    model_config = {"frozen": True, "extra": "forbid"}

    full_name: str
    username: str
    email: str
    organization: str
    computer_name: str


class IdentityBundle(BaseModel):
    """Complete identity bundle combining user and hardware identities."""

    model_config = {"frozen": True, "extra": "forbid"}

    user: UserIdentity
    hardware: HardwareIdentity


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class IdentityGenerator:
    """Generates deterministic, coherent identity bundles from profile context.

    Args:
        profile_context: Validated profile data from the Profile Engine.
        data_dir: Path to the data directory containing ``hardware_models.json``.

    Raises:
        FileNotFoundError: If *data_dir* or ``hardware_models.json`` is missing.
        IdentityGenerationError: If hardware data is malformed.
    """

    _HW_DATA_FILE: str = "hardware_models.json"

    def __init__(self, profile_context: ProfileContext, data_dir: Path) -> None:
        if not data_dir.is_dir():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")
        self._profile = profile_context
        self._data_dir = data_dir
        self._hw_data = self._load_hardware_data()
        self._faker: Faker
        self._rng: Random

    # -- public API ---------------------------------------------------------

    def generate(self, override_username: str = None, override_hostname: str = None) -> IdentityBundle:
        """Generate a complete, deterministic identity bundle.

        Args:
            override_username: Hardcode the Windows username (vital for VM sync).
            override_hostname: Hardcode the Windows computer name.

        Returns:
            An immutable :class:`IdentityBundle` with user and hardware data.

        Raises:
            IdentityGenerationError: On any generation failure.
        """
        self._init_faker()
        user = self._generate_user_identity()
        
        # Apply strict overrides if provided (crucial for aligning with existing VMs)
        if override_username or override_hostname:
            user_update = {}
            if override_username:
                user_update["username"] = override_username
            if override_hostname:
                user_update["computer_name"] = override_hostname
            user = user.model_copy(update=user_update)

        hardware = self._generate_hardware_identity()
        bundle = IdentityBundle(user=user, hardware=hardware)
        logger.info(
            "Generated identity for profile user '%s': %s <%s>",
            self._profile.username,
            bundle.user.full_name,
            bundle.user.email,
        )
        return bundle

    # -- initialization -----------------------------------------------------

    def _init_faker(self) -> None:
        """Create Faker and RNG instances with deterministic seed."""
        seed_bytes = self._profile.username.encode("utf-8")
        seed = int(hashlib.sha256(seed_bytes).hexdigest(), 16) % (2**32)
        locale = self._profile.locale or _FALLBACK_LOCALE
        try:
            self._faker = Faker(locale)
        except AttributeError:
            logger.warning(
                "Locale '%s' unsupported by Faker, falling back to '%s'",
                locale,
                _FALLBACK_LOCALE,
            )
            self._faker = Faker(_FALLBACK_LOCALE)
        self._faker.seed_instance(seed)
        self._rng = Random(seed)

    def _load_hardware_data(self) -> Dict[str, Any]:
        """Load and validate hardware_models.json.

        Raises:
            FileNotFoundError: If the file does not exist.
            IdentityGenerationError: If parsing fails or required keys are absent.
        """
        path = self._data_dir / self._HW_DATA_FILE
        if not path.is_file():
            raise FileNotFoundError(f"Hardware models file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IdentityGenerationError(
                f"Failed to parse {self._HW_DATA_FILE}: {exc}"
            ) from exc
        required_keys = {"system_vendors", "disk_models", "gpu_models"}
        missing = required_keys - set(data.keys())
        if missing:
            raise IdentityGenerationError(
                f"Hardware data missing required keys: {missing}"
            )
        if not data["system_vendors"]:
            raise IdentityGenerationError(
                "Hardware data 'system_vendors' must not be empty"
            )
        return data

    # -- user identity ------------------------------------------------------

    def _generate_user_identity(self) -> UserIdentity:
        """Generate the human-facing identity."""
        full_name = self._faker.name()
        username = self._derive_username(full_name)
        organization = self._profile.organization
        domain = self._normalize_org_domain(organization)
        email = f"{username}@{domain}"
        computer_name = self._generate_computer_name(username)
        return UserIdentity(
            full_name=full_name,
            username=username,
            email=email,
            organization=organization,
            computer_name=computer_name,
        )

    def _derive_username(self, full_name: str) -> str:
        """Derive username from full name as ``firstname.lastname``.

        Returns:
            Lowercase string containing only alphanumeric chars and dots.
        """
        parts = full_name.lower().split()
        cleaned = [re.sub(r"[^a-z0-9]", "", p) for p in parts]
        cleaned = [p for p in cleaned if p]
        if len(cleaned) >= 2:
            return f"{cleaned[0]}.{cleaned[-1]}"
        if cleaned:
            return cleaned[0]
        return "user"

    def _normalize_org_domain(self, organization: str) -> str:
        """Normalize organization name to an email domain.

        Strips corporate suffixes, removes non-alpha chars, appends ``.com``.
        """
        domain = organization.lower().strip()
        for suffix in _ORG_SUFFIXES:
            if domain.endswith(suffix):
                domain = domain[: -len(suffix)].strip()
                break
        domain = re.sub(r"[^a-z0-9]", "", domain)
        if not domain:
            domain = "company"
        return f"{domain}.com"

    def _generate_computer_name(self, username: str) -> str:
        """Generate a realistic computer name based on inferred profile type."""
        profile_type = self._detect_profile_type()

        if profile_type == "developer":
            first = re.sub(r"[^A-Z0-9]", "", username.split(".")[0].upper())
            clean_user = re.sub(r"[^A-Z0-9]", "", username.upper())
            patterns = [
                f"DEV-{first}-PC",
                f"{clean_user}-WS",
            ]
        elif profile_type == "home":
            hex_suffix = self._faker.hexify("???????", upper=True)
            patterns = [
                f"DESKTOP-{hex_suffix}",
                "FAMILY-PC",
            ]
        else:  # office
            hex_suffix = self._faker.hexify("???????", upper=True)
            org_prefix = re.sub(
                r"[^A-Z0-9]", "", self._profile.organization.upper()
            )[:4]
            if not org_prefix:
                org_prefix = "CORP"
            digits = self._faker.numerify("###")
            patterns = [
                f"DESKTOP-{hex_suffix}",
                f"{org_prefix}-LT-{digits}",
            ]

        return self._rng.choice(patterns)

    def _detect_profile_type(self) -> str:
        """Infer profile type from installed apps and browsing categories.

        Returns:
            One of ``"developer"``, ``"home"``, or ``"office"``.
        """
        apps = frozenset(a.lower() for a in self._profile.installed_apps)
        if apps & _DEVELOPER_APPS:
            return "developer"
        categories = frozenset(
            c.lower() for c in self._profile.browsing.categories
        )
        if (
            self._profile.organization.lower() == "personal"
            or categories & _HOME_CATEGORIES
        ):
            return "home"
        return "office"

    # -- hardware identity --------------------------------------------------

    def _generate_hardware_identity(self) -> HardwareIdentity:
        """Generate hardware fingerprint from hardware_models.json data."""
        vendor_group = self._rng.choice(self._hw_data["system_vendors"])
        bios_vendor = vendor_group["bios_vendor"]
        bios_version = self._rng.choice(vendor_group["bios_versions"])
        motherboard_model = self._rng.choice(vendor_group["motherboard_models"])
        bios_release_date = self._faker.date_between(
            start_date="-5y", end_date="today"
        )
        disk_model = self._rng.choice(self._hw_data["disk_models"])
        disk_serial = self._generate_disk_serial()
        gpu_model = self._rng.choice(self._hw_data["gpu_models"])
        return HardwareIdentity(
            bios_vendor=bios_vendor,
            bios_version=bios_version,
            bios_release_date=bios_release_date,
            motherboard_model=motherboard_model,
            disk_model=disk_model,
            disk_serial=disk_serial,
            gpu_model=gpu_model,
        )

    def _generate_disk_serial(self) -> str:
        """Generate a realistic disk serial (10–16 uppercase alphanumeric)."""
        length = self._rng.randint(10, 16)
        chars = string.ascii_uppercase + string.digits
        return "".join(self._rng.choice(chars) for _ in range(length))