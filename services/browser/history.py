"""Browser history SQLite database service.

Orchestrates creation of Chrome/Edge History databases using
the generator modules for schema, visits, and search terms.
"""

import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from services.base_service import BaseService
from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import BROWSERS
from services.browser.utils.url_loader import UrlLoader
from services.browser.generators.schema import (
    HISTORY_SCHEMA_SQL, SCHEMA_VERSION, LAST_COMPATIBLE_VERSION,
)
from services.browser.generators.visit_generator import (
    assign_visit_counts,
    compute_day_visits,
    generate_visits_for_day,
    visit_datetime,
    visit_transition,
)
from services.browser.generators.search_term_generator import (
    populate_search_terms,
)


class BrowserHistoryService(BaseService):
    """Creates realistic Chrome/Edge History SQLite databases."""

    def __init__(self, mount_manager, timestamp_service, audit_logger,
                 profile_config: dict | None = None,
                 username: str = "default_user",
                 data_dir: str | None = None):
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._cfg = profile_config or {}
        self._username = username
        self._loader = UrlLoader(data_dir)

    @property
    def service_name(self) -> str:
        return "BrowserHistory"

    def apply(self, context: dict) -> None:
        cfg = context.get("profile_config", self._cfg)
        user = context.get("username", self._username)
        days = context.get("timeline_days", 90)
        browsers = context.get("browsers", None)

        for name, ud_rel in BROWSERS:
            if browsers and name not in browsers:
                continue
            pf = os.path.join("Users", user, ud_rel, "Default")
            self._build_db(name, pf, cfg, days)

    # ------------------------------------------------------------------

    def _build_db(self, browser: str, pf_path: str,
                  cfg: dict, days: int) -> None:
        db_dir = self._mount.resolve(pf_path)
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "History"

        cats = cfg.get("browsing", {}).get("categories", ["general"])
        entries = self._loader.urls_for_categories(cats)

        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(HISTORY_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                ("version", SCHEMA_VERSION))
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                ("last_compatible_version", LAST_COMPATIBLE_VERSION))

            rng = random.Random(42)
            url_id_map = self._insert_urls(conn, entries, rng)
            self._insert_visits(conn, entries, url_id_map, cfg, days, rng)
            self._backfill_last_visit_times(conn, days, rng)
            populate_search_terms(
                conn, url_id_map, self._loader.load_search_terms(), rng)
            conn.commit()
        finally:
            conn.close()

        self._audit.log({
            "service": self.service_name, "operation": "create_file",
            "path": str(db_path), "browser": browser,
            "file_type": "sqlite_history",
        })

    def _insert_urls(self, conn, entries, rng):
        counts = assign_visit_counts(entries, rng)
        id_map: dict[str, int] = {}
        for e in entries:
            url, title = e["url"], e.get("title", "")
            vc = counts.get(url, 1)
            tc = max(1, vc // 3) if rng.random() > 0.3 else 0
            cur = conn.execute(
                "INSERT INTO urls (url,title,visit_count,typed_count,"
                "last_visit_time,hidden) VALUES (?,?,?,?,0,0)",
                (url, title, vc, tc))
            id_map[url] = cur.lastrowid
        return id_map

    def _insert_visits(self, conn, entries, id_map, cfg, days, rng):
        daily = cfg.get("browsing", {}).get("daily_avg_sites", 10)
        wh = cfg.get("work_hours", {})
        hs, he = wh.get("start", 9), wh.get("end", 17)
        active = wh.get("active_days", [1, 2, 3, 4, 5])

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        last: dict[str, int] = {}

        for d in range(days):
            day = start + timedelta(days=d)
            dv = compute_day_visits(rng, daily, day.isoweekday() in active)
            for sess in generate_visits_for_day(rng, entries, dv, hs, he):
                prev = 0
                for i, (url, moff) in enumerate(sess):
                    uid = id_map.get(url)
                    if uid is None:
                        continue
                    vdt = visit_datetime(rng, day, hs, moff)
                    cts = datetime_to_chrome(vdt)
                    dur = rng.randint(5, 300) * 1_000_000
                    tr = visit_transition(i, url)
                    cur = conn.execute(
                        "INSERT INTO visits (url,visit_time,from_visit,"
                        "transition,visit_duration) VALUES (?,?,?,?,?)",
                        (uid, cts, prev, tr, dur))
                    prev = cur.lastrowid
                    if url not in last or cts > last[url]:
                        last[url] = cts

        for url, lt in last.items():
            uid = id_map.get(url)
            if uid:
                conn.execute(
                    "UPDATE urls SET last_visit_time=? WHERE id=?",
                    (lt, uid))

    def _backfill_last_visit_times(self, conn, days: int, rng) -> None:
        """Assign a Chrome-epoch last_visit_time to any URL that has
        visit_count > 0 but was never visited in the generated sessions.

        This prevents coherence gaps where the visit count claims visits
        happened but the timestamp says otherwise.
        """
        orphans = conn.execute(
            "SELECT id FROM urls WHERE visit_count > 0 AND last_visit_time = 0"
        ).fetchall()
        if not orphans:
            return

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        for (uid,) in orphans:
            # Pick a random moment inside the timeline window
            offset_seconds = rng.randint(0, max(1, days * 86400))
            synthetic_dt = start + timedelta(seconds=offset_seconds)
            cts = datetime_to_chrome(synthetic_dt)
            conn.execute(
                "UPDATE urls SET last_visit_time=? WHERE id=?",
                (cts, uid))
