"""Chrome/WebKit timestamp conversion utilities.

Chrome and Chromium-based browsers (Edge, Brave, Opera) all use
the WebKit timestamp format: microseconds since January 1, 1601
00:00:00 UTC. This module converts between Python datetimes and
Chrome timestamps.
"""

from datetime import datetime

# Offset from Unix epoch (1970-01-01) to Chrome epoch (1601-01-01)
# in microseconds: 369 years × 365.25 days × 86400 seconds × 1e6 µs
CHROME_EPOCH_OFFSET_US = 11644473600 * 1_000_000


def datetime_to_chrome(dt: datetime) -> int:
    """Convert a datetime to Chrome timestamp.

    Args:
        dt: A timezone-aware or naive datetime object.

    Returns:
        Microseconds since 1601-01-01 00:00:00 UTC.
    """
    unix_us = int(dt.timestamp() * 1_000_000)
    return unix_us + CHROME_EPOCH_OFFSET_US


def chrome_to_unix_seconds(chrome_ts: int) -> float:
    """Convert a Chrome timestamp back to Unix epoch seconds.

    Useful for debugging and validation.
    """
    return (chrome_ts - CHROME_EPOCH_OFFSET_US) / 1_000_000
