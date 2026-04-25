"""Centralised time helpers (ADR-005, ADR-012).

Services must NOT call datetime.now() / datetime.utcnow() directly.
Use sched_now(ctx) instead so the scheduler owns the clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any


def sched_now(ctx: Any) -> datetime:
    """Return simulation 'now' (UTC).

    When a scheduler is present (Phase 4a onwards) returns its deterministic
    wall-clock anchor so all services share the same timeline baseline.
    Falls back to real wall-clock only in dry-run / unit-test contexts where
    the scheduler has not yet been wired in.
    """
    sched = getattr(ctx, "scheduler", None)
    if sched is not None:
        return sched.now
    return datetime.now(timezone.utc)


def sched_install_time(ctx: Any) -> datetime:
    """Return simulation install_time (UTC), or 360 days ago as fallback."""
    from datetime import timedelta
    sched = getattr(ctx, "scheduler", None)
    if sched is not None:
        return sched.install_time
    return datetime.now(timezone.utc) - timedelta(days=360)
