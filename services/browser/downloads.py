"""Browser download simulation service.

Creates download records in the Chrome/Edge History SQLite database
AND placeholder files in the Windows Downloads folder, so both DB
inspection and filesystem enumeration by malware show genuine activity.
"""

import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.base_service import BaseService
from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import BROWSERS
from services.browser.utils.url_loader import UrlLoader
from services.browser.generators.download_generator import (
    load_download_catalogue,
    select_downloads,
    generate_download_time,
    insert_download,
    create_placeholder_file,
)

import sqlite3


class BrowserDownloadService(BaseService):
    """Populates browser download records and filesystem stubs.

    For each browser:
      - Opens the existing History SQLite DB (created by
        BrowserHistoryService) and inserts rows into the
        `downloads` and `downloads_url_chains` tables.

    Also creates zero-byte stubs in the user's Downloads folder
    so the filesystem looks lived-in regardless of browser.
    """

    def __init__(self, mount_manager, timestamp_service, audit_logger,
                 profile_name: str = "home_user",
                 username: str = "default_user",
                 data_dir: str | None = None,
                 download_count: int = 6):
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._profile = profile_name
        self._username = username
        self._count = download_count
        self._data_dir = (
            Path(data_dir) if data_dir else
            Path(__file__).resolve().parent.parent.parent / "data" / "wordlists"
        )

    @property
    def service_name(self) -> str:
        return "BrowserDownloads"

    def apply(self, context: dict) -> None:
        profile = context.get("profile_type", context.get("profile_name", self._profile))
        user = context.get("username", self._username)
        count = context.get("download_count", self._count)
        timeline_days = context.get("timeline_days", 90)
        browsers = context.get("browsers", None)
        wh = context.get("work_hours", {"start": 9, "end": 17})

        catalogue = load_download_catalogue(self._data_dir)
        rng = random.Random(43)          # different seed from visits
        entries = select_downloads(catalogue, profile, rng, count)
        now = datetime.now(timezone.utc)

        # 1. Filesystem stubs in Downloads folder (browser-agnostic)
        dl_dir = self._mount.resolve(
            os.path.join("Users", user, "Downloads")
        )
        dl_dir.mkdir(parents=True, exist_ok=True)
        for e in entries:
            create_placeholder_file(
                dl_dir,
                e["filename"],
                e["size_bytes"],
                timestamp_service=self._ts,
                event_type="download",
            )
            self._audit.log({
                "service": self.service_name,
                "operation": "create_file",
                "path": str(dl_dir / e["filename"]),
                "file_type": "download_stub",
            })

        # 2. SQLite records in each browser's History DB
        for browser_name, ud_rel in BROWSERS:
            if browsers and browser_name not in browsers:
                continue
            pf = os.path.join("Users", user, ud_rel, "Default")
            db_path = self._mount.resolve(pf) / "History"
            if not db_path.exists():
                continue
            self._write_db_records(
                db_path, entries, user, rng, now,
                timeline_days, wh, browser_name
            )

    # ------------------------------------------------------------------

    def _write_db_records(self, db_path: Path, entries: list,
                          user: str, rng: random.Random,
                          now: datetime, days: int, wh: dict,
                          browser: str) -> None:
        hs = wh.get("start", 9)
        he = wh.get("end", 17)
        conn = sqlite3.connect(str(db_path))
        try:
            # Find the next free download ID
            max_id = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM downloads"
            ).fetchone()[0]

            for i, entry in enumerate(entries):
                dl_id = max_id + i + 1
                # Spread downloads across the timeline
                day_offset = rng.randint(1, days)
                base_day = now - timedelta(days=day_offset)
                start_dt = generate_download_time(rng, base_day, hs, he)
                # Download completes 5–120 seconds later
                end_dt = start_dt + timedelta(seconds=rng.randint(5, 120))
                start_ts = datetime_to_chrome(start_dt)
                end_ts = datetime_to_chrome(end_dt)

                insert_download(conn, entry, dl_id, user, start_ts, end_ts)

            conn.commit()
        finally:
            conn.close()

        self._audit.log({
            "service": self.service_name,
            "operation": "modify_db",
            "path": str(db_path),
            "browser": browser,
            "records_added": len(entries),
            "file_type": "sqlite_history",
        })
