"""Audit logging for all write/modify operations across services."""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AuditLogger:
    """Logs every write/modify operation for audit trail."""

    def __init__(self):
        self._entries = []

    def log(self, entry: dict) -> None:
        """Record an audit log entry with automatic timestamp."""
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._entries.append(entry)
        logger.info("AUDIT: %s", json.dumps(entry))

    @property
    def entries(self) -> list:
        """Return all recorded audit entries."""
        return list(self._entries)

    def clear(self) -> None:
        """Clear all recorded entries."""
        self._entries.clear()
