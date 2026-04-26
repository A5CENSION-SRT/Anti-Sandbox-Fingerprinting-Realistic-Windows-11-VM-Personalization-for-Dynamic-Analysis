"""ExpansionOrchestrator — produces ExpansionBundle from seeds + rates.

Phase 2: generates artifact descriptors from per-day rates × timeline_days.
Phase 4a: will consume EventScheduler events instead of rolling its own RNG.

Per-day rates come from config.yaml::artifact_scale:
    downloads:     {per_day: 4.2,  jitter: 0.3}
    documents:     {per_day: 12.5, jitter: 0.3}
    pictures:      {per_day: 2.1,  jitter: 0.4}
    browser_history: {per_day: 83, jitter: 0.5}
"""

from __future__ import annotations

import dataclasses
import logging
import math
from random import Random
from typing import Any, Dict, List, Optional

from services.base_service import BaseService
from services.expansion.bundle import (
    BrowsingDescriptor,
    DocumentDescriptor,
    DownloadDescriptor,
    ExpansionBundle,
    MediaDescriptor,
)
def _get_seed_generators():
    from services.ai.seed_generators.registry import RegistrySeedGenerator
    from services.ai.seed_generators.evtx import EvtxSeedGenerator
    from services.ai.seed_generators.prefetch import PrefetchSeedGenerator
    return RegistrySeedGenerator, EvtxSeedGenerator, PrefetchSeedGenerator

logger = logging.getLogger(__name__)

# Minimal valid JPEG header (1x1 red pixel stub)
_JPEG_STUB = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xD9,
])

_DOCX_STUB = bytes([0x50, 0x4B, 0x03, 0x04, 0x14, 0x00, 0x00, 0x00])
_PDF_STUB  = b"%PDF-1.7\n%%EOF\n"


class ExpansionOrchestrator(BaseService):
    """Generates artifact descriptors from per-day rates.

    Registered as an EXPANSION-phase service; its ``apply()`` method
    builds the ExpansionBundle and stores it in ctx.expansion.
    """

    def __init__(self, scale_config: Optional[Dict[str, Any]] = None) -> None:
        self._scale_config: Dict[str, Any] = scale_config or {}

    @property
    def service_name(self) -> str:
        return "ExpansionOrchestrator"

    def apply(self, ctx: "ServiceContext") -> None:
        persona = ctx.persona
        rng = ctx.rng
        config: Dict[str, Any] = self._scale_config
        timeline_days = persona.timeline_days

        bundle = self.expand(
            persona=persona,
            rng=rng,
            scale_config=config,
        )

        # Generate artifact seeds with offline fallback (no Gemini client required)
        RegistrySeedGenerator, EvtxSeedGenerator, PrefetchSeedGenerator = _get_seed_generators()
        registry_seed = RegistrySeedGenerator(timeline_days=timeline_days).generate(persona)
        evtx_seed = EvtxSeedGenerator(timeline_days=timeline_days).generate(persona)
        prefetch_seed = PrefetchSeedGenerator(timeline_days=timeline_days).generate(persona)

        bundle = dataclasses.replace(
            bundle,
            registry=registry_seed,
            evtx=evtx_seed,
            prefetch=prefetch_seed,
        )

        object.__setattr__(ctx, "expansion", bundle)
        logger.info(
            "ExpansionOrchestrator: %d artifacts, registry=%d userassist entries, "
            "evtx=%d event stubs, prefetch=%d entries",
            bundle.total_artifacts,
            len(registry_seed.userassist_apps),
            len(evtx_seed.application_events),
            len(prefetch_seed.entries),
        )

    def expand(
        self,
        persona: Any,
        rng: Random,
        scale_config: Dict[str, Any],
    ) -> ExpansionBundle:
        """Build an ExpansionBundle deterministically from persona + config.

        Args:
            persona: PersonaContext — drives content theming.
            rng: Seeded Random instance.
            scale_config: artifact_scale section of config.yaml (per-day rates).

        Returns:
            Fully populated ExpansionBundle.
        """
        timeline = persona.timeline_days
        archetype = persona.profile_archetype

        docs_rate = self._rate(scale_config, "documents", default=12.5)
        dl_rate   = self._rate(scale_config, "downloads",  default=4.2)
        pic_rate  = self._rate(scale_config, "pictures",   default=2.1)
        url_rate  = self._rate(scale_config, "browser_history", default=83.0)

        jitter = lambda j: 1.0 + rng.uniform(-j, j)

        docs_count = max(1, round(docs_rate * timeline * jitter(0.3)))
        dl_count   = max(1, round(dl_rate   * timeline * jitter(0.3)))
        pic_count  = max(1, round(pic_rate  * timeline * jitter(0.4)))
        url_count  = max(1, round(url_rate  * timeline * jitter(0.5)))

        username = persona.username.split(".")[0].capitalize()
        documents = self._gen_documents(docs_count, rng, username, archetype, timeline)
        downloads = self._gen_downloads(dl_count, rng, username, archetype, timeline)
        media     = self._gen_media(pic_count, rng, username, archetype, timeline)
        browsing  = self._gen_browsing(url_count, rng, persona)

        return ExpansionBundle(
            documents=documents,
            downloads=downloads,
            media=media,
            browsing=browsing,
        )

    # -- generation helpers -------------------------------------------------

    @staticmethod
    def _rate(config: Dict, key: str, default: float) -> float:
        section = config.get(key, {})
        if isinstance(section, dict):
            return float(section.get("per_day", default))
        return default

    def _gen_documents(
        self,
        count: int,
        rng: Random,
        username: str,
        archetype: str,
        timeline: float = 360.0,
    ) -> List[DocumentDescriptor]:
        exts = {
            "developer":  [".md", ".txt", ".py", ".json"],
            "office_user": [".docx", ".xlsx", ".pdf", ".txt"],
            "home_user":  [".pdf", ".txt", ".docx"],
        }.get(archetype, [".txt", ".docx"])

        names = [
            "Report", "Notes", "Summary", "Draft", "Proposal", "Budget",
            "Plan", "Spec", "Analysis", "Review", "Memo", "Invoice",
        ]
        result = []
        for i in range(count):
            name = rng.choice(names)
            ext = rng.choice(exts)
            suffix = f"_{i // 50 + 1}" if i >= 50 else ""
            rel = f"Users/{username}/Documents/{name}{suffix}{ext}"
            content = _DOCX_STUB if ext == ".docx" else _PDF_STUB if ext == ".pdf" else b""
            result.append(DocumentDescriptor(
                relative_path=rel,
                content=content,
                event_type="document_edit",
                timestamp_offset_days=rng.uniform(0.0, timeline),
            ))
        return result

    def _gen_downloads(
        self,
        count: int,
        rng: Random,
        username: str,
        archetype: str,
        timeline: float = 360.0,
    ) -> List[DownloadDescriptor]:
        result = []
        for i in range(count):
            ext = rng.choice([".exe", ".zip", ".pdf", ".msi", ".dmg"])
            name = f"download_{i:04d}{ext}"
            rel = f"Users/{username}/Downloads/{name}"
            result.append(DownloadDescriptor(
                relative_path=rel,
                content=b"",
                source_url=f"https://example.com/files/{name}",
                event_type="file_download",
                timestamp_offset_days=rng.uniform(0.0, timeline),
            ))
        return result

    def _gen_media(
        self,
        count: int,
        rng: Random,
        username: str,
        archetype: str,
        timeline: float = 360.0,
    ) -> List[MediaDescriptor]:
        result = []
        for i in range(count):
            ext = rng.choice([".jpg", ".png", ".mp4", ".mp3"])
            media_type = (
                "image" if ext in (".jpg", ".png") else
                "video" if ext == ".mp4" else "audio"
            )
            folder = {
                "image": "Pictures",
                "video": "Videos",
                "audio": "Music",
            }[media_type]
            name = f"media_{i:04d}{ext}"
            rel = f"Users/{username}/{folder}/{name}"
            result.append(MediaDescriptor(
                relative_path=rel,
                content=_JPEG_STUB if media_type == "image" else b"",
                media_type=media_type,
                event_type="media_view",
                timestamp_offset_days=rng.uniform(0.0, timeline),
            ))
        return result

    def _gen_browsing(
        self,
        count: int,
        rng: Random,
        persona: Any,
    ) -> List[BrowsingDescriptor]:
        categories = persona.browsing_categories or ["general"]
        timeline = persona.timeline_days
        result = []
        for _ in range(count):
            cat = rng.choice(categories)
            url = f"https://www.{cat.lower()}.example.com/page{rng.randint(1, 9999)}"
            result.append(BrowsingDescriptor(
                url=url,
                title=f"{cat.capitalize()} page",
                visit_time_offset_days=rng.uniform(0, timeline),
                visit_count=rng.randint(1, 5),
            ))
        return result
