"""Tests for core.timestamp_service module."""

from datetime import datetime, timedelta, timezone

import pytest

from core.timestamp_service import TimestampService, TimestampServiceError


class TestTimestampServiceInit:
    """Tests for TimestampService initialization."""

    def test_init_default_parameters(self) -> None:
        """Service initializes with default parameters."""
        svc = TimestampService(seed="test-seed")
        assert svc._seed == "test-seed"
        assert svc._timeline_days == 90
        assert svc._work_start == 9
        assert svc._work_end == 18

    def test_init_custom_timeline(self) -> None:
        """Service accepts custom timeline_days."""
        svc = TimestampService(seed="test", timeline_days=30)
        assert svc._timeline_days == 30

    def test_init_custom_work_hours(self) -> None:
        """Service accepts custom work hours config."""
        work_hours = {"start": 8, "end": 17, "active_days": [0, 1, 2]}
        svc = TimestampService(seed="test", work_hours=work_hours)
        assert svc._work_start == 8
        assert svc._work_end == 17
        assert svc._active_days == [0, 1, 2]

    def test_init_custom_base_time(self) -> None:
        """Service accepts custom base time."""
        base = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        svc = TimestampService(seed="test", base_time=base)
        assert svc._base_time == base


class TestGetTimestamp:
    """Tests for get_timestamp method."""

    def test_returns_dict_with_required_keys(self) -> None:
        """get_timestamp returns dict with created/modified/accessed."""
        svc = TimestampService(seed="test")
        ts = svc.get_timestamp("file_create")
        
        assert "created" in ts
        assert "modified" in ts
        assert "accessed" in ts

    def test_all_values_are_datetimes(self) -> None:
        """All timestamp values are datetime objects."""
        svc = TimestampService(seed="test")
        ts = svc.get_timestamp("file_modify")
        
        assert isinstance(ts["created"], datetime)
        assert isinstance(ts["modified"], datetime)
        assert isinstance(ts["accessed"], datetime)

    def test_timestamp_ordering_invariant(self) -> None:
        """created <= modified <= accessed always holds."""
        svc = TimestampService(seed="test-ordering")
        
        for event_type in ["file_create", "file_modify", "browser_visit", "download"]:
            for _ in range(10):
                ts = svc.get_timestamp(event_type)
                assert ts["created"] <= ts["modified"], f"created > modified for {event_type}"
                assert ts["modified"] <= ts["accessed"], f"modified > accessed for {event_type}"

    def test_timestamps_within_timeline(self) -> None:
        """Generated timestamps fall within the timeline window."""
        base = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        timeline_days = 30
        svc = TimestampService(
            seed="test-timeline",
            timeline_days=timeline_days,
            base_time=base,
        )
        
        earliest_allowed = base - timedelta(days=timeline_days)
        
        for _ in range(20):
            ts = svc.get_timestamp("file_create")
            assert ts["created"] >= earliest_allowed
            assert ts["created"] <= base

    def test_unknown_event_type_uses_defaults(self) -> None:
        """Unknown event types use default weights without error."""
        svc = TimestampService(seed="test")
        # Should not raise, uses default (0.5, 0.5) weights
        ts = svc.get_timestamp("unknown_event_type_xyz")
        assert "created" in ts


class TestDeterminism:
    """Tests for deterministic behavior."""

    def test_same_seed_produces_same_timestamps(self) -> None:
        """Two services with same seed produce identical timestamps."""
        base = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        svc1 = TimestampService(seed="determinism-test", base_time=base)
        svc2 = TimestampService(seed="determinism-test", base_time=base)
        
        for event_type in ["file_create", "browser_visit", "download"]:
            ts1 = svc1.get_timestamp(event_type)
            ts2 = svc2.get_timestamp(event_type)
            
            assert ts1["created"] == ts2["created"]
            assert ts1["modified"] == ts2["modified"]
            assert ts1["accessed"] == ts2["accessed"]

    def test_different_seeds_produce_different_timestamps(self) -> None:
        """Two services with different seeds produce different timestamps."""
        base = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        svc1 = TimestampService(seed="seed-alpha", base_time=base)
        svc2 = TimestampService(seed="seed-beta", base_time=base)
        
        ts1 = svc1.get_timestamp("file_create")
        ts2 = svc2.get_timestamp("file_create")
        
        # At least one timestamp should differ
        timestamps_match = (
            ts1["created"] == ts2["created"] and
            ts1["modified"] == ts2["modified"] and
            ts1["accessed"] == ts2["accessed"]
        )
        assert not timestamps_match, "Different seeds produced identical timestamps"


class TestTimeConversions:
    """Tests for datetime conversion methods."""

    def test_datetime_to_chrome_epoch(self) -> None:
        """datetime_to_chrome converts correctly to Chrome epoch."""
        svc = TimestampService(seed="test")
        
        # Chrome epoch starts at 1601-01-01
        # 2024-01-01 00:00:00 UTC should be a large number
        test_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        chrome_ts = svc.datetime_to_chrome(test_dt)
        
        assert isinstance(chrome_ts, int)
        assert chrome_ts > 0
        # Chrome timestamps are in microseconds since 1601-01-01
        # 2024 is ~423 years after 1601
        expected_years = 423
        micros_per_year = 365.25 * 24 * 60 * 60 * 1_000_000
        expected_approx = int(expected_years * micros_per_year)
        
        # Should be within 1% of expected
        assert abs(chrome_ts - expected_approx) / expected_approx < 0.01

    def test_datetime_to_filetime_epoch(self) -> None:
        """datetime_to_filetime converts correctly to FILETIME epoch."""
        svc = TimestampService(seed="test")
        
        test_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        filetime = svc.datetime_to_filetime(test_dt)
        
        assert isinstance(filetime, int)
        assert filetime > 0
        # FILETIME is in 100-nanosecond intervals since 1601-01-01
        # Should be 10x Chrome timestamp (microseconds vs 100ns)
        chrome_ts = svc.datetime_to_chrome(test_dt)
        assert filetime == chrome_ts * 10

    def test_conversion_round_trip_consistency(self) -> None:
        """Chrome and FILETIME conversions are consistent."""
        svc = TimestampService(seed="test")
        
        test_times = [
            datetime(2020, 6, 15, 10, 30, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2010, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        ]
        
        for dt in test_times:
            chrome = svc.datetime_to_chrome(dt)
            filetime = svc.datetime_to_filetime(dt)
            # FILETIME should always be 10x Chrome (100ns vs 1μs intervals)
            assert filetime == chrome * 10


class TestEventTypes:
    """Tests for different event types."""

    def test_all_known_event_types(self) -> None:
        """All documented event types work without error."""
        known_events = [
            "system_boot", "user_login", "file_create", "file_modify",
            "file_access", "browser_visit", "app_launch", "document_open",
            "document_save", "download", "install", "update",
            "registry_write", "prefetch", "thumbnail", "recycle",
        ]
        
        svc = TimestampService(seed="event-types-test")
        
        for event_type in known_events:
            ts = svc.get_timestamp(event_type)
            assert ts is not None
            assert "created" in ts

    def test_event_counter_increments(self) -> None:
        """Event counter increments with each call."""
        svc = TimestampService(seed="counter-test")
        
        assert svc._event_counter == 0
        svc.get_timestamp("file_create")
        assert svc._event_counter == 1
        svc.get_timestamp("browser_visit")
        assert svc._event_counter == 2
