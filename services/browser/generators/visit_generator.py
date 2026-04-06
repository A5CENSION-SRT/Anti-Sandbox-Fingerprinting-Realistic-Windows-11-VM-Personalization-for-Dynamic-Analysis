"""Generates realistic browsing visit records.

Produces daily sessions that follow a diurnal (circadian) model:
  • Activity concentrated during the profile's work_hours window
  • Multiple sessions per day with natural inter-session gaps
  • Intra-session page chains via from_visit (typed → link → link)
  • Power-law–weighted URL selection (popular domains visited more)

This module is the core of what makes the generated History DB
plausible to malware that inspects timestamp distributions.
"""

import random
from datetime import datetime, timedelta, timezone

from services.browser.utils.chrome_timestamps import datetime_to_chrome
from services.browser.utils.constants import (
    HIGH_TRAFFIC_DOMAINS,
    SEARCH_ENGINE_PREFIXES,
    TRANSITION_GENERATED,
    TRANSITION_LINK,
    TRANSITION_TYPED,
)


def assign_visit_counts(url_entries: list[dict],
                        rng: random.Random) -> dict[str, int]:
    """Decide how often each URL is visited over the timeline."""
    counts: dict[str, int] = {}
    for entry in url_entries:
        url = entry["url"]
        if any(d in url for d in HIGH_TRAFFIC_DOMAINS):
            counts[url] = rng.randint(10, 50)
        else:
            counts[url] = rng.randint(1, 15)
    return counts


def generate_visits_for_day(
    rng: random.Random,
    url_entries: list[dict],
    day_visits: int,
    hour_start: int,
    hour_end: int,
) -> list[list[tuple[str, int]]]:
    """Return a list of browsing sessions for one day.

    Each session is a list of ``(url, minute_offset)`` tuples.
    ``minute_offset`` is relative to ``hour_start``.
    """
    if not url_entries:
        return []

    total_minutes = (hour_end - hour_start) * 60
    num_sessions = rng.randint(1, min(5, max(1, day_visits)))
    visits_per = max(1, day_visits // num_sessions)

    sessions: list[list[tuple[str, int]]] = []
    for _ in range(num_sessions):
        session: list[tuple[str, int]] = []
        start = rng.randint(0, max(1, total_minutes - 30))

        low = max(1, visits_per - 2)
        high = max(low, visits_per + 2)
        length = rng.randint(low, high)

        for v in range(length):
            idx = min(int(rng.expovariate(0.05)), len(url_entries) - 1)
            url = url_entries[idx]["url"]
            minute = start + v * rng.randint(1, 5)
            session.append((url, minute))

        if session:
            sessions.append(session)
    return sessions


def compute_day_visits(rng: random.Random, daily_avg: int,
                       is_active_day: bool) -> int:
    """How many page loads happen on this calendar day."""
    if not is_active_day:
        return max(2, daily_avg // 4)
    low = max(2, daily_avg - daily_avg // 3)
    high = max(low, daily_avg + daily_avg // 3)
    return rng.randint(low, high)


def visit_transition(index: int, url: str) -> int:
    """Pick the Chromium transition code for a visit."""
    if index == 0:
        return TRANSITION_TYPED
    if any(se in url for se in SEARCH_ENGINE_PREFIXES):
        return TRANSITION_GENERATED
    return TRANSITION_LINK


def visit_datetime(rng: random.Random, base_day: datetime,
                   hour_start: int, minute_offset: int) -> datetime:
    """Build the exact datetime for one visit."""
    hour = min(hour_start + minute_offset // 60, 23)
    minute = minute_offset % 60
    return base_day.replace(
        hour=hour, minute=minute,
        second=rng.randint(0, 59),
        microsecond=rng.randint(0, 999999),
        tzinfo=timezone.utc,
    )
