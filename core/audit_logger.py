"""Audit logging for all write/modify operations across services."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class AuditLogger:
    """Logs every write/modify operation for audit trail."""

    def __init__(self, log_path: Optional[Union[str, Path]] = None):
        self._entries = []
        self._log_path = Path(log_path) if log_path else None
        
        # Initialize the file if path provided
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            # Create or clear the log file
            with self._log_path.open("w", encoding="utf-8") as f:
                pass

    def log(self, entry: dict) -> None:
        """Record an audit log entry with automatic timestamp."""
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._entries.append(entry)
        
        # Write to file if configured
        if self._log_path:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                
        logger.info("AUDIT: %s", json.dumps(entry))

    @property
    def entries(self) -> list:
        """Return all recorded audit entries."""
        return list(self._entries)

    def clear(self) -> None:
        """Clear all recorded entries."""
        self._entries.clear()
