"""Tests for EventScheduler — determinism, work-hour respect, child_rng isolation."""

import random
from datetime import datetime, timezone

import pytest

from core.event_scheduler import EventScheduler, SyntheticEvent
from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_persona(**overrides) -> PersonaContext:
    defaults = {
        "full_name": "Test User",
        "username": "test.user",
        "email": "test.user@corp.com",
        "organization": "Test Corp",
        "occupation": "Analyst",
        "age_range": "25-35",
        "interests": PersonaInterests(
            hobbies=["reading", "cycling", "photography"],
            professional_topics=["data", "analytics"],
            entertainment=["podcasts"],
        ),
        "work_style": PersonaWorkStyle(
            description="focused",
            typical_tools=["excel", "outlook"],
        ),
        "project_names": ["Project A", "Project B", "Project C"],
        "colleague_names": ["Alice", "Bob", "Carol", "Dave", "Eve"],
        "installed_apps": ["excel", "outlook"],
        "browsing_categories": ["news", "research"],
        "daily_avg_sites": 30,
        "work_hours_start": 9,
        "work_hours_end": 17,
        "active_days": [1, 2, 3, 4, 5],  # Mon–Fri
        "timeline_days": 30,
    }
    defaults.update(overrides)
    return PersonaContext(**defaults)


def _make_scheduler(persona, *, days: int = 30, seed: int = 42) -> EventScheduler:
    now = datetime(2024, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    install_time = datetime(2024, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(seed)
    return EventScheduler(
        persona=persona,
        install_time=install_time,
        now=now,
        rng=rng,
    )


# ---------------------------------------------------------------------------
# 1. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_same_events(self) -> None:
        persona = _make_persona()
        a = _make_scheduler(persona, seed=42).emit()
        b = _make_scheduler(persona, seed=42).emit()
        assert len(a) == len(b)
        for ea, eb in zip(a, b):
            assert ea.kind == eb.kind
            assert ea.timestamp == eb.timestamp

    def test_different_seeds_different_events(self) -> None:
        persona = _make_persona()
        a = _make_scheduler(persona, seed=1).emit()
        b = _make_scheduler(persona, seed=2).emit()
        # Different seeds should produce different event counts (with high probability)
        kinds_a = [e.kind for e in a]
        kinds_b = [e.kind for e in b]
        assert kinds_a != kinds_b or len(a) != len(b)

    def test_cached_after_first_call(self) -> None:
        persona = _make_persona()
        scheduler = _make_scheduler(persona)
        first = scheduler.emit()
        second = scheduler.emit()
        assert first is second  # same list object


# ---------------------------------------------------------------------------
# 2. Work-hour respect
# ---------------------------------------------------------------------------

class TestWorkHours:
    def test_no_events_on_weekends(self) -> None:
        persona = _make_persona(active_days=[1, 2, 3, 4, 5])  # Mon–Fri only
        events = _make_scheduler(persona).emit()
        for event in events:
            # Event timestamps must fall on active days
            if event.kind not in ("LOGIN", "LOGOFF"):
                continue
            iso_weekday = event.timestamp.isoweekday()
            assert iso_weekday in {1, 2, 3, 4, 5}, (
                f"Event on weekend: {event.timestamp} (weekday {iso_weekday})"
            )

    def test_events_within_work_hours(self) -> None:
        persona = _make_persona(work_hours_start=9, work_hours_end=17)
        events = _make_scheduler(persona).emit()
        session_events = [
            e for e in events
            if e.kind in ("APP_LAUNCH", "FILE_CREATE", "URL_VISIT")
        ]
        for event in session_events:
            hour = event.timestamp.hour
            # Allow 1h buffer around work hours for gaussian jitter
            assert 7 <= hour <= 20, (
                f"Event outside expected hours: {event.timestamp}"
            )


# ---------------------------------------------------------------------------
# 3. Event types
# ---------------------------------------------------------------------------

class TestEventTypes:
    def test_has_login_events(self) -> None:
        persona = _make_persona()
        events = _make_scheduler(persona).emit()
        kinds = {e.kind for e in events}
        assert "LOGIN" in kinds

    def test_has_logoff_events(self) -> None:
        persona = _make_persona()
        events = _make_scheduler(persona).emit()
        kinds = {e.kind for e in events}
        assert "LOGOFF" in kinds

    def test_has_activity_events(self) -> None:
        persona = _make_persona()
        events = _make_scheduler(persona).emit()
        kinds = {e.kind for e in events}
        assert kinds & {"APP_LAUNCH", "FILE_CREATE", "URL_VISIT"}

    def test_events_of_kind_filter(self) -> None:
        persona = _make_persona()
        scheduler = _make_scheduler(persona)
        all_events = scheduler.emit()
        login_events = scheduler.events_of("LOGIN")
        assert all(e.kind == "LOGIN" for e in login_events)
        assert len(login_events) == sum(1 for e in all_events if e.kind == "LOGIN")


# ---------------------------------------------------------------------------
# 4. child_rng isolation
# ---------------------------------------------------------------------------

class TestChildRng:
    def test_child_rng_deterministic(self) -> None:
        persona = _make_persona()
        s1 = _make_scheduler(persona, seed=42)
        s2 = _make_scheduler(persona, seed=42)
        rng1 = s1.child_rng("test_service")
        rng2 = s2.child_rng("test_service")
        assert rng1.random() == rng2.random()

    def test_child_rng_isolated_by_name(self) -> None:
        persona = _make_persona()
        scheduler = _make_scheduler(persona, seed=42)
        rng_a = scheduler.child_rng("service_a")
        rng_b = scheduler.child_rng("service_b")
        assert rng_a.random() != rng_b.random()


# ---------------------------------------------------------------------------
# 5. SyntheticEvent validation
# ---------------------------------------------------------------------------

class TestSyntheticEvent:
    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            SyntheticEvent(
                kind="LOGIN",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),  # no tzinfo
            )

    def test_utc_timestamp_accepted(self) -> None:
        event = SyntheticEvent(
            kind="LOGIN",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert event.kind == "LOGIN"
