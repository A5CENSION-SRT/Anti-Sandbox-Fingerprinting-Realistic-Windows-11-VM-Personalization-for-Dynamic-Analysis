"""Master timeline and realistic timestamp distribution."""

from datetime import datetime


class TimestampService:
    """Provides consistent timestamps for filesystem operations."""

    def get_timestamp(self, event_type: str) -> dict:
        """Return created/modified/accessed timestamps for an event type.

        Returns:
            dict with keys 'created', 'modified', 'accessed',
            each mapping to a datetime object.
        """
        raise NotImplementedError(
            "TimestampService.get_timestamp must be implemented or mocked"
        )
