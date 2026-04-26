"""Pydantic schemas for AI-generated artifact seed data.

PersonaContext is the canonical schema from core.persona_context.
This module defines artifact seed schemas (Tier 1 → Tier 2 interface).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from enum import Enum

from pydantic import BaseModel, Field

from core.persona_context import (
    PersonaContext,
    PersonaInterests,
    PersonaWorkStyle,
    TechProficiency,
)

__all__ = [
    "PersonaContext",
    "PersonaInterests",
    "PersonaWorkStyle",
    "TechProficiency",
]


class VisitFrequency(str, Enum):
    DAILY = "daily"
    MULTIPLE_DAILY = "multiple_daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    OCCASIONAL = "occasional"


class FileCategory(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    FINANCIAL = "financial"
    CREATIVE = "creative"
    TECHNICAL = "technical"


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    MUSIC = "music"
    SCREENSHOT = "screenshot"


# ---------------------------------------------------------------------------
# Artifact Seeds (Tier 1 → Tier 2 Interface)
# ---------------------------------------------------------------------------

class ExpansionRule(BaseModel):
    """Rules for expanding a seed into multiple artifacts."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    target_count: int = Field(
        ..., ge=1, le=1000,
        description="Target number of artifacts to generate"
    )
    date_range_days: int = Field(
        default=90, ge=1, le=365,
        description="Spread artifacts over this many days"
    )
    include_versions: bool = Field(
        default=True,
        description="Generate v1, v2, etc. variations"
    )
    include_drafts: bool = Field(
        default=True,
        description="Generate _DRAFT, _Final variations"
    )
    include_dates: bool = Field(
        default=True,
        description="Include date stamps in filenames"
    )


class ArtifactSeed(BaseModel):
    """Base class for all artifact seeds."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    seed_id: str = Field(..., description="Unique identifier for this seed")
    context: str = Field(..., description="Why this artifact exists")
    expansion: ExpansionRule = Field(..., description="How to expand this seed")


class DownloadSeed(ArtifactSeed):
    """Seed for generating download artifacts."""
    
    filename_pattern: str = Field(
        ..., description="Pattern like 'Report_{quarter}_{year}.pdf'"
    )
    url_template: str = Field(
        ..., description="URL pattern for the download source"
    )
    referrer_template: str = Field(
        ..., description="Referrer URL pattern"
    )
    mime_type: str = Field(
        default="application/octet-stream",
        description="MIME type of the download"
    )
    size_range_bytes: tuple[int, int] = Field(
        default=(1024, 10485760),
        description="(min_bytes, max_bytes) for file size"
    )
    
    # Pattern variables
    variables: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Variable substitutions for patterns"
    )


class DocumentSeed(ArtifactSeed):
    """Seed for generating document artifacts."""
    
    filename_pattern: str = Field(
        ..., description="Pattern like '{project}_Report_{date}.docx'"
    )
    document_type: str = Field(
        ..., description="docx, xlsx, pdf, txt, etc."
    )
    category: FileCategory = Field(
        default=FileCategory.WORK,
        description="Document category"
    )
    content_theme: str = Field(
        ..., description="Theme for content generation"
    )
    content_template: Optional[str] = Field(
        None, description="Optional content template with variables"
    )
    
    # Pattern variables
    variables: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Variable substitutions for patterns"
    )
    
    # Location hints
    subfolder: str = Field(
        default="Documents",
        description="Subfolder under user directory"
    )


class BrowsingPatternSeed(BaseModel):
    """Seed for a browsing pattern (URL + visit behavior)."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    url: str = Field(..., description="Base URL to visit")
    title: str = Field(..., description="Page title")
    frequency: VisitFrequency = Field(..., description="Visit frequency")
    typical_times: List[str] = Field(
        default_factory=lambda: ["09:00-10:00", "14:00-15:00"],
        description="Time ranges when typically visited"
    )
    context: str = Field(..., description="Why user visits this site")
    generates_downloads: bool = Field(
        default=False,
        description="Whether visits spawn download records"
    )


class BrowsingSeed(ArtifactSeed):
    """Seed for generating browser history, bookmarks, and search terms."""
    
    url_patterns: List[BrowsingPatternSeed] = Field(
        ..., min_length=10, max_length=150,
        description="URL patterns with visit behaviors"
    )
    search_term_themes: List[str] = Field(
        ..., min_length=10, max_length=100,
        description="Search term themes to expand"
    )
    bookmark_categories: Dict[str, List[str]] = Field(
        ..., description="Bookmark folders with URL lists"
    )


class MediaEventCluster(BaseModel):
    """A cluster of media files from an event."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    event_name: str = Field(..., description="Event identifier (vacation, birthday)")
    date_start: date = Field(..., description="Event start date")
    date_end: Optional[date] = Field(None, description="Event end date (None = single day)")
    file_count: int = Field(..., ge=1, le=500, description="Files from this event")
    naming_pattern: str = Field(
        default="IMG_{date}_{counter}",
        description="Filename pattern"
    )


class MediaSeed(ArtifactSeed):
    """Seed for generating media file artifacts (photos, videos, music)."""
    
    media_type: MediaType = Field(..., description="Type of media")
    
    # For photos/videos
    event_clusters: List[MediaEventCluster] = Field(
        default_factory=list,
        description="Event-based file clusters"
    )
    random_file_count: int = Field(
        default=50,
        description="Additional random files spread across timeline"
    )
    
    # For music
    artists: List[str] = Field(
        default_factory=list,
        description="Artist names for music files"
    )
    albums: List[str] = Field(
        default_factory=list,
        description="Album names"
    )
    playlists: List[str] = Field(
        default_factory=list,
        description="Playlist names"
    )
    
    # File specs
    extension: str = Field(default="jpg", description="File extension")
    size_range_bytes: tuple[int, int] = Field(
        default=(50000, 5000000),
        description="(min_bytes, max_bytes) for file size"
    )


class FilenameSeed(ArtifactSeed):
    """Seed for filename pattern generation."""
    
    pattern: str = Field(..., description="Filename pattern with variables")
    variables: Dict[str, List[str]] = Field(
        ..., description="Variable name → possible values"
    )
    date_formats: List[str] = Field(
        default_factory=lambda: ["%Y%m%d", "%Y-%m-%d", "%B_%d"],
        description="Date format strings to use"
    )
    version_styles: List[str] = Field(
        default_factory=lambda: ["v{n}", "V{n}", "_{n}", " ({n})"],
        description="Version number styles"
    )
    suffix_options: List[str] = Field(
        default_factory=lambda: ["", "_DRAFT", "_Final", "_reviewed", "_backup"],
        description="Filename suffixes"
    )


# ---------------------------------------------------------------------------
# Registry Seeds
# ---------------------------------------------------------------------------

class RegistryAppEntry(BaseModel):
    """One UserAssist entry — an application with run statistics."""

    model_config = {"frozen": True, "extra": "forbid"}

    exe_path: str = Field(..., description="Full Windows path to the executable")
    run_count: int = Field(..., ge=1, description="Number of times the app was launched")
    focus_count: int = Field(default=0, ge=0, description="Number of focus events")
    focus_time_ms: int = Field(default=0, ge=0, description="Total focus time in milliseconds")


class RegistrySeed(BaseModel):
    """Registry MRU / UserAssist artifact seeds."""

    model_config = {"frozen": True, "extra": "forbid"}

    seed_id: str = Field(..., description="Unique identifier")
    context: str = Field(..., description="Why these registry artifacts exist")
    run_mru_entries: List[str] = Field(
        default_factory=list,
        description="Entries for HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\RunMRU"
    )
    typed_paths: List[str] = Field(
        default_factory=list,
        description="Entries for Explorer TypedPaths (address bar history)"
    )
    userassist_apps: List[RegistryAppEntry] = Field(
        default_factory=list,
        description="UserAssist ROT13 application run records"
    )
    recent_doc_extensions: Dict[str, int] = Field(
        default_factory=dict,
        description="Extension → count for RecentDocs MRU"
    )


# ---------------------------------------------------------------------------
# EVTX Seeds
# ---------------------------------------------------------------------------

class EvtxEventStub(BaseModel):
    """Template for a repeating Application event log record."""

    model_config = {"frozen": True, "extra": "forbid"}

    event_id: int = Field(..., ge=0, le=65535)
    provider: str = Field(..., description="Event provider / source name")
    level: int = Field(default=4, description="0=LogAlways 1=Critical 2=Error 3=Warn 4=Info")
    description: str = Field(..., description="Human-readable event description")
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="EventData name→value pairs"
    )
    recurrence_days: Optional[int] = Field(
        None, ge=1,
        description="If set, repeat every N days across the timeline; else one-shot"
    )


class EvtxSeed(BaseModel):
    """Application event log seeds for a persona."""

    model_config = {"frozen": True, "extra": "forbid"}

    seed_id: str = Field(..., description="Unique identifier")
    context: str = Field(..., description="Why these events exist")
    application_events: List[EvtxEventStub] = Field(
        default_factory=list,
        description="Events written to the Application channel"
    )


# ---------------------------------------------------------------------------
# Prefetch Seeds
# ---------------------------------------------------------------------------

class PrefetchEntry(BaseModel):
    """One prefetch file specification."""

    model_config = {"frozen": True, "extra": "forbid"}

    exe_name: str = Field(..., description="Uppercase EXE filename, e.g. CODE.EXE")
    exe_path: str = Field(..., description="Full uppercase Windows path to the executable")
    run_count: int = Field(..., ge=1, le=9999, description="Number of recorded launches")
    last_run_offset_h: int = Field(
        default=8, ge=0,
        description="Hours before 'now' for the most recent run timestamp"
    )


class PrefetchAppSeed(BaseModel):
    """Prefetch file list and run-count distribution for a persona."""

    model_config = {"frozen": True, "extra": "forbid"}

    seed_id: str = Field(..., description="Unique identifier")
    context: str = Field(..., description="Why these apps were used")
    entries: List[PrefetchEntry] = Field(
        ..., min_length=5,
        description="One entry per .pf file to generate"
    )


# ---------------------------------------------------------------------------
# Aggregated Seed Collection
# ---------------------------------------------------------------------------

class ProfileSeeds(BaseModel):
    """Complete seed collection for a persona."""

    model_config = {"frozen": True, "extra": "forbid"}

    persona: PersonaContext = Field(..., description="The persona these seeds are for")

    downloads: List[DownloadSeed] = Field(
        default_factory=list,
        description="Download artifact seeds"
    )
    documents: List[DocumentSeed] = Field(
        default_factory=list,
        description="Document artifact seeds"
    )
    browsing: Optional[BrowsingSeed] = Field(
        None, description="Browsing behavior seeds"
    )
    media: List[MediaSeed] = Field(
        default_factory=list,
        description="Media file seeds"
    )
    filename_patterns: List[FilenameSeed] = Field(
        default_factory=list,
        description="Reusable filename patterns"
    )
    registry: Optional[RegistrySeed] = Field(
        None, description="Registry MRU / UserAssist seeds"
    )
    evtx: Optional[EvtxSeed] = Field(
        None, description="Application event log seeds"
    )
    prefetch: Optional[PrefetchAppSeed] = Field(
        None, description="Prefetch file list and run-count distribution"
    )

    # Metadata
    generated_at: datetime = Field(
        default_factory=lambda: datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
        description="When seeds were generated (set explicitly at generation time)"
    )
    gemini_model: str = Field(
        default="gemini-3.1-flash-lite-preview",
        description="Model used for generation"
    )

    def total_seed_count(self) -> int:
        """Return total number of seeds across all categories."""
        count = len(self.downloads) + len(self.documents) + len(self.media)
        count += len(self.filename_patterns)
        if self.browsing:
            count += len(self.browsing.url_patterns)
            count += len(self.browsing.search_term_themes)
        if self.registry:
            count += len(self.registry.userassist_apps)
        if self.prefetch:
            count += len(self.prefetch.entries)
        return count
