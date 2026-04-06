"""Browser cookies and cache artifact service.

Creates realistic Chrome/Edge ``Cookies`` SQLite databases on the mounted
Windows 11 image.  The generated databases use the real Chromium schema
(version 21) and contain session / persistent cookies that match the
profile's browsing categories — so that sandbox-detection heuristics that
check for cookie presence or cookie-count thresholds see genuine activity.

A lightweight ``Cache`` directory is also scaffolded (with ``index`` and
``data_*`` stub files) so ``dir /s Cache`` returns a non-empty listing.

Chromium Cookies schema (key columns)
--------------------------------------
``cookies``
    creation_utc, host_key, name, value, encrypted_value, path,
    expires_utc, is_secure, is_httponly, last_access_utc, has_expires,
    is_persistent, priority, samesite, source_scheme, source_port,
    last_update_utc

This module is a **pure operation builder** — it constructs rows and
delegates all SQLite I/O to local ``sqlite3`` calls, and logs every write
through the injected :class:`AuditLogger`.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.base_service import BaseService
from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import BROWSERS
from services.browser.utils.url_loader import UrlLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chromium Cookies SQLite schema (version 21 — Chrome 120+)
# ---------------------------------------------------------------------------

_COOKIES_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT NOT NULL UNIQUE PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS cookies (
    creation_utc   INTEGER NOT NULL,
    host_key       TEXT    NOT NULL DEFAULT '',
    top_frame_site_key TEXT NOT NULL DEFAULT '',
    name           TEXT    NOT NULL DEFAULT '',
    value          TEXT    NOT NULL DEFAULT '',
    encrypted_value BLOB   NOT NULL DEFAULT x'',
    path           TEXT    NOT NULL DEFAULT '/',
    expires_utc    INTEGER NOT NULL DEFAULT 0,
    is_secure      INTEGER NOT NULL DEFAULT 1,
    is_httponly    INTEGER NOT NULL DEFAULT 0,
    last_access_utc INTEGER NOT NULL DEFAULT 0,
    has_expires    INTEGER NOT NULL DEFAULT 1,
    is_persistent  INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 1,
    samesite       INTEGER NOT NULL DEFAULT -1,
    source_scheme  INTEGER NOT NULL DEFAULT 2,
    source_port    INTEGER NOT NULL DEFAULT 443,
    last_update_utc INTEGER NOT NULL DEFAULT 0,
    source_type    INTEGER NOT NULL DEFAULT 0,
    has_cross_site_ancestor INTEGER NOT NULL DEFAULT 0,
    UNIQUE (host_key, top_frame_site_key, name, path,
            source_scheme, source_port)
);
"""

_COOKIES_SCHEMA_VERSION: str = "21"

# ---------------------------------------------------------------------------
# Common cookie templates per domain category
# ---------------------------------------------------------------------------

_COOKIE_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "google.com": [
        {"name": "NID", "path": "/", "httponly": False, "days": 180},
        {"name": "1P_JAR", "path": "/", "httponly": False, "days": 30},
        {"name": "CONSENT", "path": "/", "httponly": False, "days": 365},
        {"name": "SID", "path": "/", "httponly": True, "days": 730},
        {"name": "HSID", "path": "/", "httponly": True, "days": 730},
    ],
    "youtube.com": [
        {"name": "VISITOR_INFO1_LIVE", "path": "/", "httponly": True, "days": 180},
        {"name": "YSC", "path": "/", "httponly": True, "days": 0},
        {"name": "PREF", "path": "/", "httponly": False, "days": 365},
    ],
    "github.com": [
        {"name": "_gh_sess", "path": "/", "httponly": True, "days": 0},
        {"name": "logged_in", "path": "/", "httponly": True, "days": 365},
        {"name": "_octo", "path": "/", "httponly": False, "days": 365},
    ],
    "linkedin.com": [
        {"name": "li_at", "path": "/", "httponly": True, "days": 365},
        {"name": "JSESSIONID", "path": "/", "httponly": False, "days": 0},
        {"name": "bcookie", "path": "/", "httponly": False, "days": 730},
    ],
    "reddit.com": [
        {"name": "reddit_session", "path": "/", "httponly": True, "days": 365},
        {"name": "token_v2", "path": "/", "httponly": True, "days": 365},
    ],
    "stackoverflow.com": [
        {"name": "prov", "path": "/", "httponly": True, "days": 365},
        {"name": "OptanonConsent", "path": "/", "httponly": False, "days": 365},
    ],
    "microsoft.com": [
        {"name": "MC1", "path": "/", "httponly": False, "days": 390},
        {"name": "MS0", "path": "/", "httponly": False, "days": 0},
        {"name": "MUID", "path": "/", "httponly": False, "days": 390},
    ],
}

# Map browsing categories → domains whose cookies should appear
_CATEGORY_DOMAINS: Dict[str, List[str]] = {
    "general": ["google.com", "microsoft.com"],
    "social_media": ["reddit.com"],
    "entertainment": ["youtube.com"],
    "business": ["linkedin.com", "microsoft.com"],
    "news": ["microsoft.com"],
    "stackoverflow": ["stackoverflow.com"],
    "github": ["github.com"],
    "documentation": ["google.com"],
    "shopping": ["google.com"],
    "streaming": ["youtube.com"],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CookiesCacheError(Exception):
    """Raised when Cookies/Cache artifact creation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CookiesCacheService(BaseService):
    """Creates Chrome/Edge Cookies databases and Cache directory stubs.

    For each configured browser the service:

    1. Creates a ``Cookies`` SQLite database matching the Chromium v21 schema.
    2. Inserts realistic cookie rows derived from the profile's browsing
       categories — domains the user would have visited get expected cookies.
    3. Scaffolds an empty ``Cache`` directory with ``index`` + ``data_*``
       placeholder files so filesystem enumeration looks lived-in.

    All operations are seeded deterministically on ``(computer_name, profile)``
    for reproducibility.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        timestamp_service: Provides anchor timestamps.
        audit_logger: Structured audit logging.
        profile_name: Profile key (``home_user`` / ``office_user`` / ``developer``).
        username: Windows username for user-directory path construction.
        data_dir: Optional override for URL data directory.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
        profile_name: str = "home_user",
        username: str = "default_user",
        data_dir: str | Path | None = None,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._profile_name = profile_name
        self._username = username
        self._loader = UrlLoader(data_dir)

    @property
    def service_name(self) -> str:
        return "CookiesCache"

    def apply(self, context: dict) -> None:
        """Create Cookies DBs and Cache stubs for all configured browsers.

        Args:
            context: Runtime context dict.  Recognised keys:

                * ``profile_name`` (str)
                * ``username`` (str)
                * ``browsers`` (list[str] | None)
                * ``browsing`` (dict) — with ``categories`` list
                * ``computer_name`` (str) — used as RNG seed
                * ``timeline_days`` (int) — look-back window for timestamps

        Raises:
            CookiesCacheError: If database creation fails.
        """
        profile = context.get("profile_name", self._profile_name)
        user = context.get("username", self._username)
        browsers = context.get("browsers", None)
        categories = (
            context.get("browsing", {}).get("categories", ["general"])
        )
        seed = context.get("computer_name", user)
        timeline_days = context.get("timeline_days", 90)
        rng = random.Random(hash(seed + profile))

        for browser_name, ud_rel in BROWSERS:
            if browsers and browser_name not in browsers:
                continue
            pf_path = os.path.join("Users", user, ud_rel, "Default")
            self._create_cookies_db(
                browser_name, pf_path, categories, rng, timeline_days,
            )
            self._create_cache_stubs(browser_name, pf_path)

    # ------------------------------------------------------------------
    # Cookies database
    # ------------------------------------------------------------------

    def _create_cookies_db(
        self,
        browser: str,
        pf_path: str,
        categories: list[str],
        rng: random.Random,
        timeline_days: int,
    ) -> None:
        """Build a Chromium Cookies SQLite database.

        Args:
            browser: Display name for audit logging.
            pf_path: Relative path to the browser profile directory.
            categories: Browsing categories from the profile config.
            rng: Seeded Random instance.
            timeline_days: How far back cookie creation dates should span.
        """
        dest_dir = self._mount.resolve(pf_path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        db_path = dest_dir / "Cookies"

        # Collect domains relevant to this profile
        domains = self._domains_for_categories(categories)

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=timeline_days)

        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_COOKIES_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                ("version", _COOKIES_SCHEMA_VERSION),
            )

            row_count = 0
            for domain in domains:
                templates = _COOKIE_TEMPLATES.get(domain, [])
                for tpl in templates:
                    creation = start + timedelta(
                        seconds=rng.randint(0, timeline_days * 86400),
                    )
                    creation_cts = datetime_to_chrome(creation)
                    last_access = creation + timedelta(
                        seconds=rng.randint(3600, timeline_days * 43200),
                    )
                    last_access_cts = datetime_to_chrome(
                        min(last_access, now),
                    )

                    # Expiry
                    if tpl["days"] > 0:
                        expires_cts = datetime_to_chrome(
                            creation + timedelta(days=tpl["days"]),
                        )
                        has_expires = 1
                        is_persistent = 1
                    else:
                        expires_cts = 0
                        has_expires = 0
                        is_persistent = 0

                    host_key = f".{domain}"

                    conn.execute(
                        "INSERT OR IGNORE INTO cookies ("
                        "  creation_utc, host_key, name, value, path,"
                        "  expires_utc, is_secure, is_httponly,"
                        "  last_access_utc, has_expires, is_persistent,"
                        "  priority, samesite, source_scheme, source_port,"
                        "  last_update_utc"
                        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            creation_cts,
                            host_key,
                            tpl["name"],
                            "",  # value is encrypted in real Chrome
                            tpl["path"],
                            expires_cts,
                            1,  # is_secure
                            int(tpl["httponly"]),
                            last_access_cts,
                            has_expires,
                            is_persistent,
                            1,  # PRIORITY_MEDIUM
                            -1,  # UNSET samesite
                            2,  # SOURCE_SCHEME_SECURE
                            443,
                            last_access_cts,
                        ),
                    )
                    row_count += 1

            conn.commit()
        except sqlite3.Error as exc:
            raise CookiesCacheError(
                f"Failed to create Cookies DB for {browser}: {exc}"
            ) from exc
        finally:
            conn.close()

        self._audit.log({
            "service": self.service_name,
            "operation": "create_cookies_db",
            "path": str(db_path),
            "browser": browser,
            "cookie_count": row_count,
        })

    # ------------------------------------------------------------------
    # Cache directory stubs
    # ------------------------------------------------------------------

    def _create_cache_stubs(self, browser: str, pf_path: str) -> None:
        """Scaffold Cache/index and data_* files.

        Real Chrome caches use a custom block-file format.  We only need
        the files to *exist* so that ``os.listdir()`` / ``dir`` returns
        non-empty results — content is not inspected by known detectors.
        """
        cache_dir = self._mount.resolve(os.path.join(pf_path, "Cache", "Cache_Data"))
        cache_dir.mkdir(parents=True, exist_ok=True)

        for name in ("index", "data_0", "data_1", "data_2", "data_3"):
            stub = cache_dir / name
            stub.touch(exist_ok=True)
            try:
                ts = self._ts.get_timestamp("browser_visit")
                os.utime(
                    str(stub),
                    (ts["accessed"].timestamp(), ts["modified"].timestamp()),
                )
            except Exception:
                # Best-effort: cache stubs are not parsed, but should not
                # cluster at execution time either.
                pass

        self._audit.log({
            "service": self.service_name,
            "operation": "create_cache_stubs",
            "path": str(cache_dir),
            "browser": browser,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _domains_for_categories(categories: list[str]) -> list[str]:
        """Deduplicate and return domains relevant to the given categories."""
        seen: set[str] = set()
        result: list[str] = []
        # Always include general
        for cat in ["general"] + categories:
            for domain in _CATEGORY_DOMAINS.get(cat, []):
                if domain not in seen:
                    seen.add(domain)
                    result.append(domain)
        return result
