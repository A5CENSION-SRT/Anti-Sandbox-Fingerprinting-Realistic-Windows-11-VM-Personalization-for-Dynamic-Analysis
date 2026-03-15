"""Master timeline and realistic timestamp distribution.

Provides consistent, deterministic timestamps for all services.
Event types control how far back in time the timestamps fall,
so browser installs are older than recent visits, etc.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from random import Random


# ---------------------------------------------------------------------------
# Event-type offsets — days before "now" for each category
# ---------------------------------------------------------------------------

_EVENT_OFFSETS = {
    # Installations happened 6-18 months ago
    "browser_install":  {"min_days": 180, "max_days": 540},
    "app_install":      {"min_days": 180, "max_days": 540},
    "os_install":       {"min_days": 360, "max_days": 720},
    # Usage artifacts are recent
    "browser_visit":    {"min_days": 0,   "max_days": 90},
    "file_create":      {"min_days": 1,   "max_days": 180},
    "file_modify":      {"min_days": 0,   "max_days": 90},
    "registry_write":   {"min_days": 0,   "max_days": 30},
    # Fallback
    "default":          {"min_days": 1,   "max_days": 90},
}


class TimestampService:
    """Provides consistent, seeded timestamps for filesystem operations.

    Args:
        seed: Integer seed for deterministic output (default: 42).
        anchor: The "now" reference point (default: current UTC time).
    """

    def __init__(self, seed: int = 42, anchor: datetime | None = None):
        self._rng = Random(seed)
        self._anchor = anchor or datetime.now(timezone.utc)

    def get_timestamp(self, event_type: str) -> dict:
        """Return created/modified/accessed timestamps for an event type.

        Args:
            event_type: One of the known event types (e.g. "browser_install",
                "file_create"). Unknown types fall back to "default".

        Returns:
            dict with keys 'created', 'modified', 'accessed',
            each mapping to a timezone-aware datetime object.
        """
        offsets = _EVENT_OFFSETS.get(event_type, _EVENT_OFFSETS["default"])
        min_d, max_d = offsets["min_days"], offsets["max_days"]

        # Created timestamp: somewhere in the offset range
        created_days = self._rng.uniform(min_d, max_d)
        created = self._anchor - timedelta(days=created_days)

        # Modified: between created and now
        mod_days = self._rng.uniform(0, created_days)
        modified = self._anchor - timedelta(days=mod_days)

        # Accessed: between modified and now
        acc_days = self._rng.uniform(0, mod_days)
        accessed = self._anchor - timedelta(days=acc_days)

        return {
            "created": created,
            "modified": modified,
            "accessed": accessed,
        }
