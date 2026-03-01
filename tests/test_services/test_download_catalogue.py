"""Tests for download_generator helper functions (unit level)."""

import random

import pytest

from services.browser.generators.download_generator import (
    load_download_catalogue,
    select_downloads,
    generate_download_time,
)
from datetime import datetime, timezone


# ---------------------------------------------------------------
# Catalogue loading
# ---------------------------------------------------------------

class TestDownloadCatalogue:

    def test_loads_catalogue(self, data_dir):
        cat = load_download_catalogue(data_dir)
        assert "home_user" in cat
        assert len(cat["home_user"]) == 2

    def test_missing_file_returns_empty(self, tmp_path):
        cat = load_download_catalogue(tmp_path)
        assert cat == {}

    def test_select_count(self, data_dir):
        cat = load_download_catalogue(data_dir)
        rng = random.Random(42)
        selected = select_downloads(cat, "home_user", rng, 2)
        assert len(selected) == 2

    def test_select_clamps_to_pool_size(self, data_dir):
        cat = load_download_catalogue(data_dir)
        rng = random.Random(42)
        # pool for developer only has 1 entry
        selected = select_downloads(cat, "developer", rng, 99)
        assert len(selected) == 1

    def test_select_falls_back_to_home_user(self, data_dir):
        cat = load_download_catalogue(data_dir)
        rng = random.Random(42)
        selected = select_downloads(cat, "nonexistent_profile", rng, 1)
        assert len(selected) == 1

    def test_entries_have_required_fields(self, data_dir):
        cat = load_download_catalogue(data_dir)
        rng = random.Random(42)
        for entry in select_downloads(cat, "home_user", rng, 2):
            assert "filename" in entry
            assert "mime_type" in entry
            assert "size_bytes" in entry
            assert "url" in entry


# ---------------------------------------------------------------
# Timestamp generation
# ---------------------------------------------------------------

class TestDownloadTimestamp:

    def test_within_active_window(self):
        rng = random.Random(0)
        base = datetime(2025, 3, 10, tzinfo=timezone.utc)
        dt = generate_download_time(rng, base, hour_start=9, hour_end=17)
        assert 9 <= dt.hour <= 17

    def test_returns_utc(self):
        rng = random.Random(1)
        base = datetime(2025, 3, 10, tzinfo=timezone.utc)
        dt = generate_download_time(rng, base, hour_start=9, hour_end=17)
        assert dt.tzinfo == timezone.utc

    def test_same_calendar_day(self):
        rng = random.Random(2)
        base = datetime(2025, 3, 10, tzinfo=timezone.utc)
        dt = generate_download_time(rng, base, hour_start=9, hour_end=17)
        assert dt.year == 2025
        assert dt.month == 3
        assert dt.day == 10
