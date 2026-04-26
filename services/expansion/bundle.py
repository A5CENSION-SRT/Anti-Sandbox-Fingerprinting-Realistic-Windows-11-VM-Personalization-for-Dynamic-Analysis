"""ExpansionBundle — the typed container services read from ctx.expansion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DocumentDescriptor:
    """A single document artifact to be written to disk."""
    relative_path: str           # e.g. "Users/Jane/Documents/Q4_Budget.xlsx"
    content: bytes               # file content (may be header stub)
    event_type: str = "document_edit"
    attributes: List[str] = field(default_factory=list)
    timestamp_offset_days: float = -1.0  # days since install_time; -1 = use TimestampService


@dataclass(frozen=True)
class DownloadDescriptor:
    """A single download artifact."""
    relative_path: str
    content: bytes
    source_url: str = ""
    event_type: str = "file_download"
    timestamp_offset_days: float = -1.0


@dataclass(frozen=True)
class MediaDescriptor:
    """A single media file artifact (image, audio, video)."""
    relative_path: str
    content: bytes
    media_type: str = "image"    # "image" | "audio" | "video"
    event_type: str = "media_view"
    timestamp_offset_days: float = -1.0


@dataclass(frozen=True)
class BrowsingDescriptor:
    """A single browser history entry."""
    url: str
    title: str
    visit_time_offset_days: float  # days from install_time
    visit_count: int = 1


@dataclass(frozen=True)
class ExpansionBundle:
    """All artifact descriptors produced by the expansion phase.

    Services consume this via ``ctx.expansion`` rather than generating
    their own content from Random(42) or static data/*.json files.

    Fields are None when not yet wired (Phase 1 stub); Phase 2 populates
    all lists.
    """

    documents: List[DocumentDescriptor] = field(default_factory=list)
    downloads: List[DownloadDescriptor] = field(default_factory=list)
    media: List[MediaDescriptor] = field(default_factory=list)
    browsing: List[BrowsingDescriptor] = field(default_factory=list)

    # Artifact seeds populated by ExpansionOrchestrator for downstream services
    registry: Optional[Any] = None   # RegistrySeed
    evtx: Optional[Any] = None       # EvtxSeed
    prefetch: Optional[Any] = None   # PrefetchAppSeed

    @property
    def total_artifacts(self) -> int:
        return (
            len(self.documents)
            + len(self.downloads)
            + len(self.media)
            + len(self.browsing)
        )
