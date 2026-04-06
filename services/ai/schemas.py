"""Pydantic schemas for AI-generated persona and artifact data.

All models use frozen=True and extra='forbid' for immutability and strict
validation. These schemas define the contract between Gemini outputs and
the local permutation engines.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TechProficiency(str, Enum):
    """User's technical skill level."""
    LOW = "low"
    INTERMEDIATE = "intermediate"
    HIGH = "high"


class VisitFrequency(str, Enum):
    """How often a URL is typically visited."""
    DAILY = "daily"
    MULTIPLE_DAILY = "multiple_daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    OCCASIONAL = "occasional"


class FileCategory(str, Enum):
    """Document category for content generation."""
    WORK = "work"
    PERSONAL = "personal"
    FINANCIAL = "financial"
    CREATIVE = "creative"
    TECHNICAL = "technical"


class MediaType(str, Enum):
    """Type of media file."""
    PHOTO = "photo"
    VIDEO = "video"
    MUSIC = "music"
    SCREENSHOT = "screenshot"


# ---------------------------------------------------------------------------
# Persona Context (Tier 1 Output)
# ---------------------------------------------------------------------------

class PersonaInterests(BaseModel):
    """User interests derived from persona."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    hobbies: List[str] = Field(
        ..., min_length=3, max_length=10,
        description="Personal hobbies and interests"
    )
    professional_topics: List[str] = Field(
        ..., min_length=2, max_length=8,
        description="Work-related topics they follow"
    )
    entertainment: List[str] = Field(
        default_factory=list,
        description="Entertainment preferences (shows, music genres, games)"
    )


class PersonaWorkStyle(BaseModel):
    """Work patterns and preferences."""
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    description: str = Field(..., description="Brief work style description")
    typical_tools: List[str] = Field(
        ..., description="Software/tools they use daily"
    )
    collaboration_style: str = Field(
        default="hybrid",
        description="solo, collaborative, or hybrid"
    )
    meeting_frequency: str = Field(
        default="moderate",
        description="low, moderate, or high"
    )


class PersonaContext(BaseModel):
    """Complete AI-generated persona for artifact generation.
    
    This is the primary output of the Gemini PersonaGenerator and serves
    as input to all seed generators.
    """
    
    model_config = {"frozen": True, "extra": "forbid"}
    
    # Core identity
    full_name: str = Field(..., description="Realistic full name")
    username: str = Field(..., pattern=r"^[a-z][a-z0-9_.]{2,19}$",
                          description="Windows username (firstname.lastname)")
    email: str = Field(..., description="Email address")
    organization: str = Field(..., description="Company/organization name")
    occupation: str = Field(..., description="Job title or role")
    department: Optional[str] = Field(None, description="Department if applicable")
    
    # Demographics
    age_range: str = Field(..., pattern=r"^\d{2}-\d{2}$",
                           description="Age range like '28-35'")
    locale: str = Field(default="en_US", description="Locale code")
    location: Optional[str] = Field(None, description="City/region")
    
    # Behavioral attributes
    tech_proficiency: TechProficiency = Field(
        default=TechProficiency.INTERMEDIATE,
        description="Technical skill level"
    )
    interests: PersonaInterests = Field(..., description="User interests")
    work_style: PersonaWorkStyle = Field(..., description="Work patterns")
    
    # Computed context for generators
    project_names: List[str] = Field(
        ..., min_length=3, max_length=15,
        description="Realistic project names they'd work on"
    )
    colleague_names: List[str] = Field(
        ..., min_length=5, max_length=20,
        description="Names of colleagues for document personalization"
    )
    
    # Schedule
    work_hours_start: int = Field(default=9, ge=0, le=23)
    work_hours_end: int = Field(default=17, ge=0, le=23)
    active_days: List[int] = Field(
        default=[1, 2, 3, 4, 5],
        description="ISO weekdays (1=Mon, 7=Sun)"
    )


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
    
    # Metadata
    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When seeds were generated"
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Model used for generation"
    )
    
    def total_seed_count(self) -> int:
        """Return total number of seeds across all categories."""
        count = len(self.downloads) + len(self.documents) + len(self.media)
        count += len(self.filename_patterns)
        if self.browsing:
            count += len(self.browsing.url_patterns)
            count += len(self.browsing.search_term_themes)
        return count
