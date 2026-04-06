"""Tests for the Profile Engine service."""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.profile_engine import (
    CircularProfileInheritanceError,
    ProfileContext,
    ProfileEngine,
    ProfileLoadError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    """Write dedented YAML content to *path*."""
    path.write_text(textwrap.dedent(content))


def _base_yaml() -> str:
    """Minimal valid base profile YAML."""
    return """\
        username: "default_user"
        organization: "default_org"
        locale: "en_US"
        installed_apps:
          - notepad
        browsing:
          categories:
            - general
          daily_avg_sites: 5
        work_hours:
          start: 9
          end: 17
          active_days: [1, 2, 3, 4, 5]
    """


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    """Create a temporary profiles directory with a valid base.yaml."""
    d = tmp_path / "profiles"
    d.mkdir()
    _write(d / "base.yaml", _base_yaml())
    return d


@pytest.fixture()
def engine(profiles_dir: Path) -> ProfileEngine:
    """ProfileEngine wired to the temporary profiles directory."""
    return ProfileEngine(profiles_dir)


# ---------------------------------------------------------------------------
# 1. Loads a simple profile (base)
# ---------------------------------------------------------------------------

class TestLoadSimpleProfile:
    """Loading base.yaml directly must succeed and return valid context."""

    def test_loads_base_profile(self, engine: ProfileEngine) -> None:
        ctx = engine.load_profile("base")
        assert isinstance(ctx, ProfileContext)
        assert ctx.username == "default_user"
        assert ctx.organization == "default_org"
        assert ctx.locale == "en_US"
        assert ctx.installed_apps == ["notepad"]
        assert ctx.browsing.categories == ["general"]
        assert ctx.browsing.daily_avg_sites == 5
        assert ctx.work_hours.start == 9
        assert ctx.work_hours.end == 17
        assert ctx.work_hours.active_days == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# 2. Resolves single inheritance
# ---------------------------------------------------------------------------

class TestSingleInheritance:
    """A child profile with no explicit extends still inherits from base."""

    def test_implicit_base_inheritance(
        self, profiles_dir: Path, engine: ProfileEngine
    ) -> None:
        _write(
            profiles_dir / "office_user.yaml",
            """\
                organization: "acme_corp"
                installed_apps:
                  - outlook
                  - teams
                browsing:
                  categories:
                    - business
                  daily_avg_sites: 15
            """,
        )
        ctx = engine.load_profile("office_user")
        assert ctx.organization == "acme_corp"
        assert ctx.installed_apps == ["outlook", "teams"]
        # Inherited from base
        assert ctx.username == "default_user"
        assert ctx.locale == "en_US"
        # Browsing overridden
        assert ctx.browsing.daily_avg_sites == 15
        # Work hours inherited from base
        assert ctx.work_hours.start == 9


# ---------------------------------------------------------------------------
# 3. Resolves multiple inheritance
# ---------------------------------------------------------------------------

class TestMultipleInheritance:
    """A profile extending two parents merges both in listed order."""

    def test_multiple_parents(
        self, profiles_dir: Path
    ) -> None:
        _write(
            profiles_dir / "parent_a.yaml",
            """\
                organization: "org_a"
                installed_apps:
                  - app_a
                browsing:
                  categories:
                    - cat_a
                  daily_avg_sites: 10
            """,
        )
        _write(
            profiles_dir / "parent_b.yaml",
            """\
                organization: "org_b"
                installed_apps:
                  - app_b
                browsing:
                  categories:
                    - cat_b
                  daily_avg_sites: 20
            """,
        )
        _write(
            profiles_dir / "child.yaml",
            """\
                extends:
                  - parent_a
                  - parent_b
                username: "child_user"
                installed_apps:
                  - app_child
                browsing:
                  categories:
                    - cat_child
                  daily_avg_sites: 30
            """,
        )
        engine = ProfileEngine(profiles_dir)
        ctx = engine.load_profile("child")
        # child overrides everything
        assert ctx.username == "child_user"
        assert ctx.installed_apps == ["app_child"]
        assert ctx.browsing.categories == ["cat_child"]
        assert ctx.browsing.daily_avg_sites == 30
        # parent_b overrides parent_a for org (both override base)
        # but child doesn't set org, so parent_b's value wins
        assert ctx.organization == "org_b"


# ---------------------------------------------------------------------------
# 4. Detects circular inheritance
# ---------------------------------------------------------------------------

class TestCircularInheritance:
    """Cycle in extends chain must raise CircularProfileInheritanceError."""

    def test_direct_cycle(self, profiles_dir: Path) -> None:
        _write(
            profiles_dir / "alpha.yaml",
            """\
                extends: bravo
                username: "alpha"
                organization: "org"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        _write(
            profiles_dir / "bravo.yaml",
            """\
                extends: alpha
                username: "bravo"
                organization: "org"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        engine = ProfileEngine(profiles_dir)
        with pytest.raises(CircularProfileInheritanceError):
            engine.load_profile("alpha")

    def test_indirect_cycle(self, profiles_dir: Path) -> None:
        _write(
            profiles_dir / "x.yaml",
            """\
                extends: y
                username: "x"
                organization: "o"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        _write(
            profiles_dir / "y.yaml",
            """\
                extends: z
                username: "y"
                organization: "o"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        _write(
            profiles_dir / "z.yaml",
            """\
                extends: x
                username: "z"
                organization: "o"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        engine = ProfileEngine(profiles_dir)
        with pytest.raises(CircularProfileInheritanceError):
            engine.load_profile("x")


# ---------------------------------------------------------------------------
# 5. Fails on unknown field
# ---------------------------------------------------------------------------

class TestUnknownField:
    """Extra fields not in the schema must be rejected."""

    def test_extra_field_rejected(
        self, profiles_dir: Path, engine: ProfileEngine
    ) -> None:
        _write(
            profiles_dir / "bad_extra.yaml",
            """\
                username: "u"
                organization: "o"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
                surprise_field: "oops"
            """,
        )
        with pytest.raises(ValidationError, match="surprise_field"):
            engine.load_profile("bad_extra")


# ---------------------------------------------------------------------------
# 6. Fails on missing required field
# ---------------------------------------------------------------------------

class TestMissingField:
    """Omitting a required field must raise a validation error."""

    def test_missing_username(self, profiles_dir: Path) -> None:
        # Rewrite base without 'username'
        _write(
            profiles_dir / "base.yaml",
            """\
                organization: "default_org"
                locale: "en_US"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 9
                  end: 17
                  active_days: [1, 2, 3, 4, 5]
            """,
        )
        _write(
            profiles_dir / "incomplete.yaml",
            """\
                organization: "org"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        engine = ProfileEngine(profiles_dir)
        with pytest.raises(ValidationError, match="username"):
            engine.load_profile("incomplete")


# ---------------------------------------------------------------------------
# 7. Returned object is immutable
# ---------------------------------------------------------------------------

class TestImmutability:
    """ProfileContext must be frozen — attribute assignment must fail."""

    def test_cannot_set_attribute(self, engine: ProfileEngine) -> None:
        ctx = engine.load_profile("base")
        with pytest.raises(ValidationError):
            ctx.username = "hacked"

    def test_nested_immutable(self, engine: ProfileEngine) -> None:
        ctx = engine.load_profile("base")
        with pytest.raises(ValidationError):
            ctx.browsing.daily_avg_sites = 999


# ---------------------------------------------------------------------------
# 8. Merge precedence
# ---------------------------------------------------------------------------

class TestMergePrecedence:
    """Child values must override parent values; parent overrides base."""

    def test_child_overrides_parent_overrides_base(
        self, profiles_dir: Path
    ) -> None:
        _write(
            profiles_dir / "mid.yaml",
            """\
                organization: "mid_org"
                locale: "fr_FR"
                installed_apps:
                  - mid_app
                browsing:
                  categories:
                    - mid_cat
                  daily_avg_sites: 50
            """,
        )
        _write(
            profiles_dir / "leaf.yaml",
            """\
                extends: mid
                locale: "de_DE"
                installed_apps:
                  - leaf_app
                browsing:
                  categories:
                    - leaf_cat
                  daily_avg_sites: 99
            """,
        )
        engine = ProfileEngine(profiles_dir)
        ctx = engine.load_profile("leaf")
        # base → mid → leaf
        assert ctx.username == "default_user"       # from base
        assert ctx.organization == "mid_org"         # from mid (overrides base)
        assert ctx.locale == "de_DE"                 # from leaf (overrides mid)
        assert ctx.installed_apps == ["leaf_app"]    # list override from leaf
        assert ctx.browsing.daily_avg_sites == 99    # leaf wins
        assert ctx.work_hours.start == 9             # inherited from base


# ---------------------------------------------------------------------------
# Edge-case: missing parent profile
# ---------------------------------------------------------------------------

class TestMissingParent:
    """Extending a non-existent parent must raise ProfileLoadError."""

    def test_missing_parent_raises(
        self, profiles_dir: Path, engine: ProfileEngine
    ) -> None:
        _write(
            profiles_dir / "orphan.yaml",
            """\
                extends: nonexistent_parent
                username: "orphan"
                organization: "o"
                locale: "en"
                installed_apps: []
                browsing:
                  categories: []
                  daily_avg_sites: 0
                work_hours:
                  start: 0
                  end: 0
                  active_days: []
            """,
        )
        with pytest.raises(ProfileLoadError, match="nonexistent_parent"):
            engine.load_profile("orphan")


# ---------------------------------------------------------------------------
# Edge-case: LRU cache returns same object
# ---------------------------------------------------------------------------

class TestCaching:
    """Repeated calls for the same profile must return the cached object."""

    def test_cached_identity(self, engine: ProfileEngine) -> None:
        ctx1 = engine.load_profile("base")
        ctx2 = engine.load_profile("base")
        assert ctx1 is ctx2


# ---------------------------------------------------------------------------
# Edge-case: profiles_dir validation
# ---------------------------------------------------------------------------

class TestProfilesDirValidation:
    """Engine must reject invalid profiles directory at construction time."""

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ProfileEngine(tmp_path / "does_not_exist")