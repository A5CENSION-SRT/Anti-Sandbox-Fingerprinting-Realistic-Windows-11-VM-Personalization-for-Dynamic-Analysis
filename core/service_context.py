"""Typed execution context passed to every service's apply() method.

Replaces the old `context: dict` anti-pattern. All fields are typed and
validated at construction time. No silent key misses.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from core.persona_context import PersonaContext

if TYPE_CHECKING:
    from core.event_scheduler import EventScheduler
    from services.expansion.bundle import ExpansionBundle


@dataclass(frozen=True)
class ServiceContext:
    """Immutable typed context threaded through every service.apply() call.

    Fields
    ------
    persona           Full persona — behavioral and demographic fields.
    mount             MountManager — resolves paths inside the mounted image.
    rng               Shared Random instance (seeded from identity).
    audit             AuditLogger — structured audit trail.
    timestamp_service TimestampService — timeline-aware timestamps.
    install_time      When the OS was installed (UTC).
    boot_time         Current simulated boot time (UTC).
    identity_bundle   IdentityBundle — generated user + hardware identity.
    scheduler         EventScheduler — cross-domain event stream (Phase 4a).
    expansion         ExpansionBundle — artifact descriptors (Phase 2).
    """

    persona: PersonaContext
    mount: Any                           # MountManager
    rng: random.Random
    audit: Any                           # AuditLogger
    timestamp_service: Any               # TimestampService
    install_time: datetime
    boot_time: datetime
    identity_bundle: Any                 # IdentityBundle
    scheduler: Optional["EventScheduler"] = None
    expansion: Optional["ExpansionBundle"] = None
