"""Profile Engine Service for loading, resolving, and validating VM profiles.

Loads YAML profile definitions from disk, resolves inheritance chains via
recursive deep-merge, validates the result against a strict Pydantic schema,
and returns an immutable ``ProfileContext`` consumed by all downstream services.
"""

import functools
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

from deepmerge import Merger
from pydantic import BaseModel
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CircularProfileInheritanceError(Exception):
    """Raised when a profile's inheritance chain contains a cycle."""


class ProfileLoadError(Exception):
    """Raised when a profile file cannot be found, parsed, or resolved."""


# ---------------------------------------------------------------------------
# Pydantic v2 schema — frozen + extra=forbid
# ---------------------------------------------------------------------------

class WorkHours(BaseModel):
    """Work-schedule window configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    start: int
    end: int
    active_days: List[int]


class BrowsingHabits(BaseModel):
    """Browsing-behaviour configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    categories: List[str]
    daily_avg_sites: int


class ProfileContext(BaseModel):
    """Immutable, validated profile context consumed by all services."""

    model_config = {"frozen": True, "extra": "forbid"}

    username: str
    organization: str
    locale: str
    installed_apps: List[str]
    browsing: BrowsingHabits
    work_hours: WorkHours


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ProfileEngine:
    """Loads profile YAML files, resolves inheritance, and returns validated contexts.

    Args:
        profiles_dir: Path to the directory containing ``*.yaml`` profile files.

    Raises:
        FileNotFoundError: If *profiles_dir* does not exist or is not a directory.
    """

    _BASE_PROFILE_NAME: str = "base"

    def __init__(self, profiles_dir: Path) -> None:
        if not profiles_dir.is_dir():
            raise FileNotFoundError(
                f"Profiles directory not found: {profiles_dir}"
            )
        self._profiles_dir = profiles_dir
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._merger = Merger(
            type_strategies=[
                (list, ["override"]),
                (dict, ["merge"]),
                (set, ["override"]),
            ],
            fallback_strategies=["override"],
            type_conflict_strategies=["override"],
        )

    # -- public API ---------------------------------------------------------

    @functools.lru_cache(maxsize=32)
    def load_profile(self, name: str) -> ProfileContext:
        """Load, resolve, validate and cache a profile by name.

        Args:
            name: Profile name **without** the ``.yaml`` extension.

        Returns:
            A validated, immutable :class:`ProfileContext`.

        Raises:
            ProfileLoadError: If the profile or any parent cannot be loaded.
            CircularProfileInheritanceError: If a cycle is detected.
            pydantic.ValidationError: If the merged data fails schema validation.
        """
        logger.info("Loading profile: %s", name)
        resolved = self._resolve_inheritance(name, frozenset())
        resolved.pop("extends", None)
        context = ProfileContext(**resolved)
        logger.info("Profile loaded successfully: %s", name)
        return context

    # -- internal helpers ---------------------------------------------------

    def _load_yaml(self, name: str) -> Dict[str, Any]:
        """Read and parse a single profile YAML file.

        Args:
            name: Profile name without extension.

        Returns:
            Parsed YAML mapping as a plain ``dict``.

        Raises:
            ProfileLoadError: On missing file, parse error, or non-mapping content.
        """
        path = self._profiles_dir / f"{name}.yaml"
        if not path.is_file():
            raise ProfileLoadError(f"Profile not found: {path}")
        try:
            data = self._yaml.load(path)
        except Exception as exc:
            raise ProfileLoadError(
                f"Failed to parse profile '{name}': {exc}"
            ) from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ProfileLoadError(
                f"Profile '{name}' must be a YAML mapping, "
                f"got {type(data).__name__}"
            )
        return dict(data)

    def _resolve_inheritance(
        self, name: str, visited: frozenset[str]
    ) -> Dict[str, Any]:
        """Recursively resolve a profile's inheritance chain.

        Resolution order:
        1. Collect parent names from the ``extends`` field.
        2. Prepend ``base`` implicitly (unless *this* profile **is** base,
           or base is already listed).
        3. Recursively resolve each parent.
        4. Deep-merge: ``base → parent_1 → … → parent_n → child``.

        Args:
            name: Profile to resolve.
            visited: Profiles already on the current resolution path.

        Raises:
            CircularProfileInheritanceError: If *name* is in *visited*.
            ProfileLoadError: If a parent file is missing or malformed.
        """
        if name in visited:
            chain = " -> ".join([*visited, name])
            raise CircularProfileInheritanceError(
                f"Circular inheritance detected: {chain}"
            )
        visited = visited | {name}

        profile_data = self._load_yaml(name)
        extends_raw = profile_data.pop("extends", None)

        # Normalise extends → list[str]
        parents: List[str] = []
        if extends_raw is not None:
            if isinstance(extends_raw, str):
                parents = [extends_raw]
            elif isinstance(extends_raw, list):
                parents = list(extends_raw)
            else:
                raise ProfileLoadError(
                    f"Profile '{name}': 'extends' must be a string or list, "
                    f"got {type(extends_raw).__name__}"
                )

        # Implicit base inheritance
        if name != self._BASE_PROFILE_NAME:
            if self._BASE_PROFILE_NAME not in parents:
                parents.insert(0, self._BASE_PROFILE_NAME)

        # Resolve & merge parents in order
        merged: Dict[str, Any] = {}
        for parent_name in parents:
            parent_resolved = self._resolve_inheritance(parent_name, visited)
            parent_resolved.pop("extends", None)
            merged = self._merge_profiles(merged, parent_resolved)

        # Child overrides everything
        merged = self._merge_profiles(merged, profile_data)
        return merged

    def _merge_profiles(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deep-merge *override* onto *base* using configured merge strategy.

        Args:
            base: Lower-priority profile data (modified in-place and returned).
            override: Higher-priority profile data.

        Returns:
            The merged dictionary.
        """
        if not override:
            return base
        if not base:
            return dict(override)
        return self._merger.merge(base, override)