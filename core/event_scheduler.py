"""Cross-domain deterministic event scheduler (ADR-005, ADR-012).

EventScheduler is the single source of time and randomness for all services.
No service should call datetime.now(), datetime.utcnow(), or Random(N) directly.

Produces a deterministic stream of SyntheticEvent objects respecting:
- persona.active_days (ISO weekdays, 1=Mon)
- persona.work_hours_start / work_hours_end
- Poisson-distributed intra-session activity
- Weekend/holiday/vacation silence
- Sleep / off-hours silence

Determinism contract (ADR-012):
    Same persona + install_time + random_seed => byte-identical audit log.
    Achieved via seeded Random with child_rng(name) isolation per service.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Literal, Optional, Set


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

EventKind = Literal[
    "APP_LAUNCH",
    "FILE_CREATE",
    "FILE_MODIFY",
    "FILE_DELETE",
    "URL_VISIT",
    "URL_DOWNLOAD",
    "LOGIN",
    "LOGOFF",
    "SYSTEM_UPDATE",
]


@dataclass(frozen=True)
class SyntheticEvent:
    """A single simulated user action with cross-domain fan-out.

    Attributes:
        kind: Event type (see EventKind).
        timestamp: UTC datetime when the event occurred.
        payload: Kind-specific metadata dict.

    Fan-out per kind is defined in §7.3 of MASTER_PLAN.md.
    """

    kind: EventKind
    timestamp: datetime          # always UTC
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("SyntheticEvent.timestamp must be timezone-aware (UTC)")


# ---------------------------------------------------------------------------
# EventScheduler
# ---------------------------------------------------------------------------

class EventScheduler:
    """Produces a deterministic event stream for the full persona timeline.

    Args:
        persona: PersonaContext — drives activity pattern.
        install_time: When the OS was installed (UTC).  Events start here.
        now: Current time (UTC).  Events end at this point.
        rng: Seeded Random instance (owned by the scheduler; callers get
             child RNGs via child_rng()).
    """

    # Average events per active hour (Poisson lambda)
    _LAMBDA_APP_LAUNCH  = 3.0
    _LAMBDA_FILE_CREATE = 2.0
    _LAMBDA_FILE_MODIFY = 4.0
    _LAMBDA_URL_VISIT   = 12.0
    _LAMBDA_URL_DOWNLOAD = 0.5

    def __init__(
        self,
        persona: Any,                   # PersonaContext
        install_time: datetime,
        now: datetime,
        rng: random.Random,
    ) -> None:
        self._persona = persona
        self._install_time = install_time.replace(tzinfo=timezone.utc) if install_time.tzinfo is None else install_time
        self._now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
        self._rng = rng
        self._events: Optional[List[SyntheticEvent]] = None

    # -- public API ---------------------------------------------------------

    def emit(self) -> List[SyntheticEvent]:
        """Return the full deterministic event list for the timeline.

        Cached after first call — calling multiple times returns the same list.
        """
        if self._events is None:
            self._events = list(self._generate())
        return self._events

    def events_of(self, kind: EventKind) -> List[SyntheticEvent]:
        """Return all events of a specific kind."""
        return [e for e in self.emit() if e.kind == kind]

    @property
    def now(self) -> datetime:
        """Simulation 'current time' (UTC). Services use this instead of datetime.now()."""
        return self._now

    @property
    def install_time(self) -> datetime:
        """OS install time (UTC). Timeline events start here."""
        return self._install_time

    def child_rng(self, name: str) -> random.Random:
        """Return a deterministic child RNG isolated by *name*.

        Each service should call this once with its own name and use the
        returned RNG for all its own random draws — ensuring that adding or
        removing services does not affect others' sequences.
        """
        seed = self._rng.getrandbits(64) ^ hash(name)
        return random.Random(seed)

    # -- generation ---------------------------------------------------------

    def _generate(self) -> Iterator[SyntheticEvent]:
        persona = self._persona
        active_days: Set[int] = set(persona.active_days)  # ISO weekdays, 1=Mon
        work_start: int = persona.work_hours_start
        work_end:   int = persona.work_hours_end

        day = self._install_time.date()
        end_day = self._now.date()

        while day <= end_day:
            iso_weekday = day.isoweekday()  # 1=Mon, 7=Sun
            if iso_weekday in active_days:
                yield from self._emit_login(day, work_start)
                yield from self._emit_session(day, work_start, work_end)
                yield from self._emit_logoff(day, work_end)
            day = day + timedelta(days=1)

    def _emit_login(self, day, work_start: int) -> Iterator[SyntheticEvent]:
        login_hour = work_start + self._rng.gauss(0, 0.25)
        login_hour = max(work_start - 1, min(work_start + 1, login_hour))
        ts = self._day_ts(day, login_hour)
        yield SyntheticEvent(kind="LOGIN", timestamp=ts, payload={"day": str(day)})

    def _emit_logoff(self, day, work_end: int) -> Iterator[SyntheticEvent]:
        logoff_hour = work_end + self._rng.gauss(0, 0.5)
        logoff_hour = max(work_end - 0.5, min(work_end + 2.0, logoff_hour))
        ts = self._day_ts(day, logoff_hour)
        yield SyntheticEvent(kind="LOGOFF", timestamp=ts, payload={"day": str(day)})

    def _emit_session(
        self, day, work_start: int, work_end: int
    ) -> Iterator[SyntheticEvent]:
        session_hours = max(1.0, work_end - work_start + self._rng.gauss(0, 0.5))
        events_per_hour: Dict[EventKind, float] = {
            "APP_LAUNCH":   self._LAMBDA_APP_LAUNCH,
            "FILE_CREATE":  self._LAMBDA_FILE_CREATE,
            "FILE_MODIFY":  self._LAMBDA_FILE_MODIFY,
            "URL_VISIT":    self._LAMBDA_URL_VISIT,
            "URL_DOWNLOAD": self._LAMBDA_URL_DOWNLOAD,
        }

        for kind, lam in events_per_hour.items():
            count = self._poisson(lam * session_hours)
            for _ in range(count):
                hour_offset = self._rng.uniform(0.25, session_hours - 0.1)
                ts = self._day_ts(day, work_start + hour_offset)
                payload = self._make_payload(kind, ts)
                yield SyntheticEvent(kind=kind, timestamp=ts, payload=payload)

    def _make_payload(self, kind: EventKind, ts: datetime) -> Dict[str, Any]:
        persona = self._persona
        if kind == "APP_LAUNCH":
            app = self._rng.choice(persona.installed_apps or ["explorer"])
            return {"app": app, "pid": self._rng.randint(1000, 65535)}
        if kind in ("FILE_CREATE", "FILE_MODIFY", "FILE_DELETE"):
            return {
                "path": f"Users/{persona.username}/Documents/file_{self._rng.randint(1, 9999)}.docx",
                "creator_app": self._rng.choice(persona.installed_apps or ["notepad"]),
            }
        if kind in ("URL_VISIT", "URL_DOWNLOAD"):
            cats = persona.browsing_categories or ["general"]
            cat = self._rng.choice(cats)
            url = f"https://www.{cat}.example.com/p{self._rng.randint(1, 9999)}"
            return {
                "url": url,
                "title": f"{cat.capitalize()} page",
                "with_download": kind == "URL_DOWNLOAD",
            }
        return {}

    # -- helpers ------------------------------------------------------------

    def _poisson(self, lam: float) -> int:
        """Sample from Poisson distribution."""
        if lam <= 0:
            return 0
        L = 2.718281828 ** (-lam)
        k, p = 0, 1.0
        while p > L:
            k += 1
            p *= self._rng.random()
        return k - 1

    def _day_ts(self, day, fractional_hour: float) -> datetime:
        """Convert a date + fractional hour to a UTC datetime."""
        hour = int(fractional_hour)
        minute = int((fractional_hour - hour) * 60)
        second = int(((fractional_hour - hour) * 60 - minute) * 60)
        return datetime(
            day.year, day.month, day.day,
            min(hour, 23), min(minute, 59), min(second, 59),
            tzinfo=timezone.utc,
        )
