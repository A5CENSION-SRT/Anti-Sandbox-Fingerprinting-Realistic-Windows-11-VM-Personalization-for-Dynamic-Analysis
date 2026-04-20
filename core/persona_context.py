"""Canonical persona schema — the single source of truth for all services.

This is the single PersonaContext used by both the AI generation layer
(services/ai/) and all artifact services. Gemini is prompted to produce
every field here; no static presets or fallbacks exist.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TechProficiency(str, Enum):
    LOW = "low"
    INTERMEDIATE = "intermediate"
    HIGH = "high"


class PersonaInterests(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    hobbies: List[str] = Field(..., min_length=3, max_length=10)
    professional_topics: List[str] = Field(..., min_length=2, max_length=8)
    entertainment: List[str] = Field(default_factory=list)


class PersonaWorkStyle(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    description: str
    typical_tools: List[str]
    collaboration_style: str = "hybrid"
    meeting_frequency: str = "moderate"


class PersonaContext(BaseModel):
    """Complete persona — drives identity generation and all artifact services."""

    model_config = {"frozen": True, "extra": "forbid"}

    # --- core identity -------------------------------------------------------
    full_name: str
    username: str = Field(..., pattern=r"^[a-z][a-z0-9_.]{2,19}$")
    email: str
    organization: str
    occupation: str
    department: Optional[str] = None

    # --- demographics --------------------------------------------------------
    age_range: str = Field(..., pattern=r"^\d{2}-\d{2}$")
    locale: str = "en_US"
    location: Optional[str] = None

    # --- behavioral ----------------------------------------------------------
    tech_proficiency: TechProficiency = TechProficiency.INTERMEDIATE
    interests: PersonaInterests
    work_style: PersonaWorkStyle

    # --- social --------------------------------------------------------------
    project_names: List[str] = Field(..., min_length=3, max_length=15)
    colleague_names: List[str] = Field(..., min_length=5, max_length=20)

    # --- schedule ------------------------------------------------------------
    work_hours_start: int = Field(default=9, ge=0, le=23)
    work_hours_end: int = Field(default=17, ge=0, le=23)
    active_days: List[int] = Field(default=[1, 2, 3, 4, 5])

    # --- artifact generation context -----------------------------------------
    profile_archetype: Literal["developer", "office_user", "home_user"] = "home_user"
    installed_apps: List[str] = Field(default_factory=list)
    browsing_categories: List[str] = Field(default_factory=lambda: ["general"])
    daily_avg_sites: int = Field(default=50, ge=1, le=500)
    timeline_days: int = Field(default=360, ge=1, le=3650)
    windows_build_hint: Literal["22H2", "23H2", "24H2"] = "23H2"
    hostname_hints: List[str] = Field(default_factory=list)
