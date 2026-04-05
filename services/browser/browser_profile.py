"""Browser profile directory scaffolding service.

Orchestrates the creation of Chrome/Edge profile directories
with configuration files on a mounted Windows 11 image.
Delegates actual JSON generation to the generators sub-package.
"""

import os
from pathlib import Path

from services.base_service import BaseService
from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import BROWSERS, PROFILE_SUBDIRS
from services.browser.generators.config_generator import (
    generate_local_state,
    generate_preferences,
    generate_secure_preferences,
    write_json,
)
from services.browser.generators.bookmark_enricher import load_and_enrich


class BrowserProfileService(BaseService):
    """Creates Chrome and Edge browser profile directory trees.

    Generates the complete profile directory with configuration
    JSON files, bookmarks, and empty sub-directories so the
    mounted image appears to have browsers that were actively used.
    """

    def __init__(self, mount_manager, timestamp_service, audit_logger,
                 profile_name: str = "home_user",
                 username: str = "default_user",
                 templates_dir: str | None = None):
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._profile_name = profile_name
        self._username = username
        self._templates_dir = (
            Path(templates_dir) if templates_dir else
            Path(__file__).resolve().parent.parent.parent
            / "templates" / "browser"
        )

    @property
    def service_name(self) -> str:
        return "BrowserProfile"

    def apply(self, context: dict) -> None:
        """Create browser profile directories and config files."""
        profile = context.get("profile_name", self._profile_name)
        user = context.get("username", self._username)
        browsers = context.get("browsers", None)

        for name, ud_rel in BROWSERS:
            if browsers and name not in browsers:
                continue
            self._create_profile(name, ud_rel, profile, user)

    # ------------------------------------------------------------------

    def _create_profile(self, browser: str, ud_rel: str,
                        profile: str, user: str) -> None:
        ud_path = os.path.join("Users", user, ud_rel)
        pf_path = os.path.join(ud_path, "Default")

        # Directories
        for sub in PROFILE_SUBDIRS:
            full = self._mount.resolve(os.path.join(pf_path, sub))
            full.mkdir(parents=True, exist_ok=True)
            self._audit.log({
                "service": self.service_name,
                "operation": "create_directory",
                "path": str(full), "browser": browser,
            })

        ts = self._ts.get_timestamp("browser_install")
        cts = datetime_to_chrome(ts["created"])

        # Config files
        write_json(self._mount, os.path.join(ud_path, "Local State"),
                   generate_local_state(browser, cts),
                   self._audit, self.service_name, browser)
        write_json(self._mount, os.path.join(pf_path, "Preferences"),
                   generate_preferences(user, cts),
                   self._audit, self.service_name, browser)
        write_json(self._mount, os.path.join(pf_path, "Secure Preferences"),
                   generate_secure_preferences(),
                   self._audit, self.service_name, browser)

        # Bookmarks
        bm = load_and_enrich(self._templates_dir, profile, cts)
        write_json(self._mount, os.path.join(pf_path, "Bookmarks"),
                   bm, self._audit, self.service_name, browser)
