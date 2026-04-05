"""Master timeline and realistic timestamp distribution.

Provides deterministic, consistent timestamps for all artifact generation.
Timestamps are distributed across a configurable timeline (e.g., 90 days)
respecting work hours and weekends for realistic usage patterns.

The service is seeded by the profile's username/computer_name to ensure
reproducibility — two runs with identical config produce identical timestamps.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Chrome/Chromium timestamp epoch: 1601-01-01 00:00:00 UTC
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# Windows FILETIME epoch: same as Chrome
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# Default timeline parameters
_DEFAULT_TIMELINE_DAYS: int = 90
_DEFAULT_WORK_START: int = 9
_DEFAULT_WORK_END: int = 18
_DEFAULT_ACTIVE_DAYS: List[int] = [0, 1, 2, 3, 4]  # Mon-Fri

# Event type weights for temporal distribution
_EVENT_WEIGHTS: Dict[str, Tuple[float, float]] = {
    # event_type: (recency_weight, work_hours_weight)
    # Higher recency_weight = more recent timestamps
    # Higher work_hours_weight = more likely during work hours
    "system_boot": (0.3, 0.8),
    "user_login": (0.5, 0.9),
    "file_create": (0.5, 0.6),
    "file_modify": (0.7, 0.6),
    "file_access": (0.8, 0.5),
    "browser_visit": (0.6, 0.4),
    "app_launch": (0.6, 0.7),
    "document_open": (0.5, 0.8),
    "document_save": (0.6, 0.8),
    "download": (0.5, 0.4),
    "install": (0.2, 0.5),
    "update": (0.3, 0.3),
    "registry_write": (0.4, 0.6),
    "prefetch": (0.6, 0.7),
    "thumbnail": (0.5, 0.5),
    "recycle": (0.4, 0.5),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TimestampServiceError(Exception):
    """Raised when timestamp generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TimestampService:
    """Provides consistent, deterministic timestamps for filesystem operations.

    Timestamps are distributed across a configurable timeline respecting
    work hours and day-of-week patterns for realistic usage simulation.

    Args:
        seed: String seed for deterministic RNG (e.g., username + computer_name).
        timeline_days: Number of days in the past to distribute timestamps.
        work_hours: Optional dict with 'start', 'end', 'active_days' keys.
        base_time: Optional base datetime (defaults to now).

    Example:
        >>> svc = TimestampService(seed="jdoe-WORKSTATION-01", timeline_days=90)
        >>> ts = svc.get_timestamp("file_create")
        >>> ts["created"], ts["modified"], ts["accessed"]
    """

    def __init__(
        self,
        seed: str,
        timeline_days: int = _DEFAULT_TIMELINE_DAYS,
        work_hours: Optional[Dict[str, Any]] = None,
        base_time: Optional[datetime] = None,
    ) -> None:
        self._seed = seed
        self._timeline_days = timeline_days
        self._base_time = base_time or datetime.now(timezone.utc)

        # Parse work hours config
        wh = work_hours or {}
        self._work_start = wh.get("start", _DEFAULT_WORK_START)
        self._work_end = wh.get("end", _DEFAULT_WORK_END)
        self._active_days = wh.get("active_days", _DEFAULT_ACTIVE_DAYS)

        # Initialize deterministic RNG
        seed_int = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % (2**32)
        self._rng = Random(seed_int)

        # Event counter for consistent sequence
        self._event_counter: int = 0

        logger.debug(
            "TimestampService initialized: seed=%s, timeline=%d days, "
            "work_hours=%d-%d, active_days=%s",
            seed, timeline_days, self._work_start, self._work_end, self._active_days,
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_timestamp(self, event_type: str) -> Dict[str, datetime]:
        """Return created/modified/accessed timestamps for an event type.

        Args:
            event_type: Type of event (e.g., "file_create", "browser_visit").

        Returns:
            Dict with keys 'created', 'modified', 'accessed', each a datetime.

        Raises:
            TimestampServiceError: If event_type is unknown.
        """
        self._event_counter += 1

        # Get weights for this event type
        recency_weight, work_weight = _EVENT_WEIGHTS.get(
            event_type, (0.5, 0.5)
        )

        # Generate base timestamp
        created = self._generate_timestamp(recency_weight, work_weight)

        # Modified is same or slightly after created
        mod_delta = timedelta(
            minutes=self._rng.randint(0, 60 * 24),
            seconds=self._rng.randint(0, 59),
        )
        modified = created + mod_delta

        # Accessed is same as modified or slightly after
        acc_delta = timedelta(
            minutes=self._rng.randint(0, 60),
            seconds=self._rng.randint(0, 59),
        )
        accessed = modified + acc_delta

        # Clamp to not exceed base_time
        modified = min(modified, self._base_time)
        accessed = min(accessed, self._base_time)

        return {
            "created": created,
            "modified": modified,
            "accessed": accessed,
        }

    def get_timestamp_in_range(
        self,
        start: datetime,
        end: datetime,
        prefer_work_hours: bool = True,
    ) -> datetime:
        """Generate a timestamp within a specific range.

        Args:
            start: Range start (inclusive).
            end: Range end (inclusive).
            prefer_work_hours: If True, bias toward work hours.

        Returns:
            A datetime within the specified range.
        """
        if start >= end:
            return start

        # Generate candidate timestamps until one fits
        for _ in range(100):
            delta = (end - start).total_seconds()
            offset = self._rng.random() * delta
            candidate = start + timedelta(seconds=offset)

            if not prefer_work_hours:
                return candidate

            # Check if within work hours
            if self._is_work_time(candidate):
                return candidate

        # Fallback: return middle of range
        return start + (end - start) / 2

    def get_boot_sequence(self, count: int = 10) -> List[datetime]:
        """Generate a sequence of boot timestamps over the timeline.

        Args:
            count: Number of boot events to generate.

        Returns:
            Sorted list of boot datetimes (oldest first).
        """
        boots = []
        for i in range(count):
            # Distribute boots across timeline
            day_offset = int(self._timeline_days * (i / count))
            base = self._base_time - timedelta(days=self._timeline_days - day_offset)

            # Boot typically happens in morning
            hour = self._rng.randint(7, 10)
            minute = self._rng.randint(0, 59)
            boot_time = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            boots.append(boot_time)

        return sorted(boots)

    # -----------------------------------------------------------------------
    # Conversion utilities
    # -----------------------------------------------------------------------

    @staticmethod
    def datetime_to_chrome(dt: datetime) -> int:
        """Convert datetime to Chrome/Chromium timestamp (microseconds since 1601).

        Args:
            dt: Datetime to convert (must be timezone-aware).

        Returns:
            Integer microseconds since Chrome epoch (1601-01-01).
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - _CHROME_EPOCH
        return int(delta.total_seconds() * 1_000_000)

    @staticmethod
    def chrome_to_datetime(chrome_ts: int) -> datetime:
        """Convert Chrome timestamp to datetime.

        Args:
            chrome_ts: Microseconds since Chrome epoch.

        Returns:
            Timezone-aware datetime (UTC).
        """
        delta = timedelta(microseconds=chrome_ts)
        return _CHROME_EPOCH + delta

    @staticmethod
    def datetime_to_filetime(dt: datetime) -> int:
        """Convert datetime to Windows FILETIME (100-nanosecond intervals since 1601).

        Args:
            dt: Datetime to convert.

        Returns:
            FILETIME as 64-bit integer.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - _FILETIME_EPOCH
        return int(delta.total_seconds() * 10_000_000)

    @staticmethod
    def filetime_to_datetime(filetime: int) -> datetime:
        """Convert Windows FILETIME to datetime.

        Args:
            filetime: 100-nanosecond intervals since 1601.

        Returns:
            Timezone-aware datetime (UTC).
        """
        delta = timedelta(microseconds=filetime // 10)
        return _FILETIME_EPOCH + delta

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _generate_timestamp(
        self,
        recency_weight: float,
        work_hours_weight: float,
    ) -> datetime:
        """Generate a timestamp with specified distribution weights.

        Args:
            recency_weight: 0.0 = older, 1.0 = more recent.
            work_hours_weight: 0.0 = any time, 1.0 = work hours only.

        Returns:
            Generated datetime.
        """
        # Calculate days offset from base_time
        # Higher recency_weight = smaller offset (more recent)
        max_days = self._timeline_days
        days_offset = int(max_days * (1 - recency_weight * self._rng.random()))
        base_date = self._base_time - timedelta(days=days_offset)

        # Determine hour based on work_hours_weight
        if self._rng.random() < work_hours_weight:
            # Work hours
            hour = self._rng.randint(self._work_start, self._work_end - 1)
        else:
            # Any hour (but less likely late night)
            weights = [1] * 6 + [3] * 12 + [2] * 4 + [1] * 2  # 0-23
            hour = self._rng.choices(range(24), weights=weights)[0]

        minute = self._rng.randint(0, 59)
        second = self._rng.randint(0, 59)
        microsecond = self._rng.randint(0, 999999)

        return base_date.replace(
            hour=hour,
            minute=minute,
            second=second,
            microsecond=microsecond,
        )

    def _is_work_time(self, dt: datetime) -> bool:
        """Check if datetime falls within work hours."""
        return (
            dt.weekday() in self._active_days
            and self._work_start <= dt.hour < self._work_end
        )
