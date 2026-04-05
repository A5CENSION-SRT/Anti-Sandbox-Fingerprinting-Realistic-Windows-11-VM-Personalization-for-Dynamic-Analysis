"""Standalone bookmark writing service.

Writes Chrome/Edge Bookmarks JSON files for all configured browsers using
profile-specific templates from ``templates/browser/``.  Delegates template
loading and timestamp enrichment to
:mod:`services.browser.generators.bookmark_enricher`.

This service can be used independently of :class:`BrowserProfileService`
to update bookmarks without recreating the full profile directory tree.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services.base_service import BaseService
from services.browser.generators.bookmark_enricher import load_and_enrich
from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import BROWSERS

logger = logging.getLogger(__name__)


class BookmarksServiceError(Exception):
    """Raised when bookmark writing fails."""


class BookmarksService(BaseService):
    """Writes Chromium Bookmarks JSON files for Chrome and Edge.

    Loads the profile-appropriate bookmark template, enriches each node with
    a unique ``id`` and Chrome-epoch timestamp, and writes the result to
    ``Users/<user>/<browser_ud>/Default/Bookmarks``.

    Handles both a first-time write and an idempotent overwrite — the output
    is always a deterministic function of ``(profile_name, creation_ts)``.

    Args:
        mount_manager: Resolves relative paths against the mounted image root.
        timestamp_service: Provides ``created`` datetime via ``get_timestamp``.
        audit_logger: Records every write operation.
        profile_name: One of ``home_user``, ``office_user``, ``developer``.
        username: Windows username (for the user directory path).
        templates_dir: Optional override for the bookmark templates directory.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
        profile_name: str = "home_user",
        username: str = "default_user",
        templates_dir: str | Path | None = None,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._profile_name = profile_name
        self._username = username
        self._templates_dir: Path = (
            Path(templates_dir)
            if templates_dir
            else Path(__file__).resolve().parent.parent.parent
            / "templates"
            / "browser"
        )

    @property
    def service_name(self) -> str:
        return "BookmarksService"

    def apply(self, context: dict) -> None:
        """Write enriched Bookmarks files for all configured browsers.

        Args:
            context: Runtime context dict.  Recognised keys:
                * ``profile_name`` (str) — overrides constructor value.
                * ``username`` (str) — overrides constructor value.
                * ``browsers`` (list[str] | None) — restrict to named browsers.

        Raises:
            BookmarksServiceError: If writing any bookmark file fails.
        """
        profile = context.get("profile_name", self._profile_name)
        user = context.get("username", self._username)
        browsers = context.get("browsers", None)

        ts = self._ts.get_timestamp("browser_bookmarks")
        chrome_ts = datetime_to_chrome(ts["created"])

        for browser_name, ud_rel in BROWSERS:
            if browsers and browser_name not in browsers:
                continue
            self._write_browser_bookmarks(
                browser_name, ud_rel, profile, user, chrome_ts
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_browser_bookmarks(
        self,
        browser: str,
        ud_rel: str,
        profile: str,
        user: str,
        chrome_ts: int,
    ) -> None:
        """Enrich the template and write to the browser-profile directory.

        Args:
            browser: Display name of the browser (for audit logging).
            ud_rel: Relative path to the browser User Data directory.
            profile: Profile key used to select the template file.
            user: Windows username.
            chrome_ts: Chrome-epoch anchor timestamp for node enrichment.

        Raises:
            BookmarksServiceError: If the file cannot be written.
        """
        pf_path = os.path.join("Users", user, ud_rel, "Default")
        dest_dir = self._mount.resolve(pf_path)
        dest_dir.mkdir(parents=True, exist_ok=True)

        bookmarks = load_and_enrich(self._templates_dir, profile, chrome_ts)
        bm_path = dest_dir / "Bookmarks"
        try:
            with open(bm_path, "w", encoding="utf-8") as fh:
                json.dump(bookmarks, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            raise BookmarksServiceError(
                f"Failed to write Bookmarks for {browser}: {exc}"
            ) from exc

        self._audit.log({
            "service": self.service_name,
            "operation": "write_bookmarks",
            "path": str(bm_path),
            "browser": browser,
            "profile": profile,
        })
        logger.debug("Wrote Bookmarks for %s → %s", browser, bm_path)
