"""Generates download records in the Chrome History SQLite database
and creates placeholder files in the Windows Downloads folder.

Two complementary artifacts are produced:
  1. Rows in `downloads` + `downloads_url_chains` tables (History DB)
  2. Zero-byte placeholder files in Users/<name>/Downloads/

This makes the environment pass both database inspection and filesystem
checks that malware uses to confirm genuine user activity.
"""

import json
import os
import random
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.browser.utils.chrome_timestamps import datetime_to_chrome

# Download state codes (Chromium InProgressState)
DOWNLOAD_STATE_COMPLETE = 1
DOWNLOAD_STATE_INTERRUPTED = 2

# Danger type for safe downloads
DOWNLOAD_DANGER_TYPE_NOT_DANGEROUS = 0


def load_download_catalogue(data_dir: Path) -> dict:
    """Load downloads_by_profile.json from data/wordlists/."""
    path = data_dir / "downloads_by_profile.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def select_downloads(catalogue: dict, profile_name: str,
                     rng: random.Random, count: int) -> list[dict]:
    """Pick ``count`` download entries for this profile.

    Falls back to ``home_user`` if the profile key is missing.
    """
    pool = catalogue.get(profile_name) or catalogue.get("home_user", [])
    n = min(count, len(pool))
    return rng.sample(pool, n)


def generate_download_time(rng: random.Random, base_day: datetime,
                           hour_start: int, hour_end: int) -> datetime:
    """Random timestamp inside the profile's active window."""
    total_mins = (hour_end - hour_start) * 60
    offset_mins = rng.randint(0, max(1, total_mins - 1))
    return base_day.replace(
        hour=min(hour_start + offset_mins // 60, 23),
        minute=offset_mins % 60,
        second=rng.randint(0, 59),
        microsecond=rng.randint(0, 999999),
        tzinfo=timezone.utc,
    )


def insert_download(conn: sqlite3.Connection, entry: dict,
                    download_id: int, username: str,
                    start_ts: int, end_ts: int) -> None:
    """Insert one row into downloads + downloads_url_chains."""
    dl_path = f"C:\\Users\\{username}\\Downloads\\{entry['filename']}"
    guid = str(uuid.uuid4()).upper()

    conn.execute(
        "INSERT INTO downloads (id, guid, current_path, target_path, "
        "start_time, received_bytes, total_bytes, state, danger_type, "
        "interrupt_reason, end_time, opened, last_access_time, "
        "referrer, site_url, tab_url, mime_type, original_mime_type) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            download_id, guid, dl_path, dl_path,
            start_ts, entry["size_bytes"], entry["size_bytes"],
            DOWNLOAD_STATE_COMPLETE, DOWNLOAD_DANGER_TYPE_NOT_DANGEROUS,
            0, end_ts, 1, end_ts,
            entry.get("referrer", ""), entry.get("referrer", ""),
            entry.get("referrer", ""),
            entry.get("mime_type", "application/octet-stream"),
            entry.get("mime_type", "application/octet-stream"),
        ),
    )
    conn.execute(
        "INSERT INTO downloads_url_chains (id, chain_index, url) "
        "VALUES (?,?,?)",
        (download_id, 0, entry["url"]),
    )


def create_placeholder_file(downloads_dir: Path, filename: str,
                            size_bytes: int) -> None:
    """Write a zero-byte stub file so the path exists on the filesystem.

    We do not write ``size_bytes`` of data (that would be wasteful), but
    we do create the file so directory listings show real entries.
    """
    target = downloads_dir / filename
    target.touch(exist_ok=True)
