"""Media file seed generator.

Produces :class:`MediaSeed` objects that drive the thumbnail cache and media
stub services.  Falls back to profile-appropriate offline seeds when the
Gemini API is unavailable.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import (
    MediaEventCluster,
    MediaSeed,
    MediaType,
    ExpansionRule,
    PersonaContext,
)

logger = logging.getLogger(__name__)


class MediaSeedGenerationError(Exception):
    """Raised when media seed generation fails."""


class MediaSeedGenerator:
    """Generates media artifact seeds via Gemini API with offline fallback.

    Args:
        client:       Configured GeminiClient.
        timeline_days: Days of history to cover (default 360).
    """

    def __init__(self, client: GeminiClient, timeline_days: int = 360) -> None:
        self._client = client
        self._timeline_days = timeline_days

    def generate(self, persona: PersonaContext, use_cache: bool = True) -> List[MediaSeed]:
        """Generate media seeds for the persona.

        Returns:
            List of MediaSeed objects (photos, screenshots, music).
        """
        try:
            prompt = self._build_prompt(persona)
            result = self._client.generate_structured(
                prompt=prompt,
                schema=List[MediaSeed],
                temperature=0.7,
                use_cache=use_cache,
            )
            logger.info("Generated %d media seeds via Gemini", len(result))
            return result
        except (GeminiClientError, Exception) as exc:
            logger.warning("Gemini media seed failed (%s); using fallback", exc)
            return self._generate_fallback(persona)

    def _build_prompt(self, persona: PersonaContext) -> str:
        archetype = persona.profile_archetype
        return (
            f"Generate realistic media file seeds for a Windows 11 {archetype} named "
            f"{persona.full_name} ({persona.occupation}). Include photo events, "
            f"screenshots, and music collections appropriate for this persona over "
            f"{self._timeline_days} days. Return a JSON array of MediaSeed objects."
        )

    def _generate_fallback(self, persona: PersonaContext) -> List[MediaSeed]:
        archetype = getattr(persona, "profile_archetype", "home_user")
        today = date.today()
        seeds: List[MediaSeed] = []

        # --- Camera roll photos ---
        if archetype in ("home_user", "office_user"):
            events = _PHOTO_EVENTS_BY_PROFILE.get(archetype, _PHOTO_EVENTS_BY_PROFILE["home_user"])
            photo_clusters = []
            for i, ev in enumerate(events):
                event_date = today - timedelta(days=30 * (i + 1))
                photo_clusters.append(MediaEventCluster(
                    event_name=ev["name"],
                    date_start=event_date,
                    date_end=event_date + timedelta(days=ev.get("span_days", 1)),
                    file_count=ev["count"],
                    naming_pattern="IMG_{date}_{counter}",
                ))
            seeds.append(MediaSeed(
                seed_id="photos_camera_roll",
                context=f"Camera roll photos for {persona.full_name}",
                expansion=ExpansionRule(
                    target_count=sum(e["count"] for e in events),
                    date_range_days=self._timeline_days,
                ),
                media_type=MediaType.PHOTO,
                event_clusters=photo_clusters,
                random_file_count=50,
                extension="jpg",
                size_range_bytes=(1_200_000, 8_000_000),
            ))

        # --- Screenshots ---
        seeds.append(MediaSeed(
            seed_id="screenshots",
            context="Desktop screenshots",
            expansion=ExpansionRule(
                target_count=_SCREENSHOT_COUNT.get(archetype, 60),
                date_range_days=self._timeline_days,
            ),
            media_type=MediaType.SCREENSHOT,
            random_file_count=_SCREENSHOT_COUNT.get(archetype, 60),
            extension="png",
            size_range_bytes=(150_000, 2_000_000),
        ))

        # --- Music (home / developer) ---
        if archetype in ("home_user", "developer"):
            music_data = _MUSIC_BY_PROFILE.get(archetype, _MUSIC_BY_PROFILE["home_user"])
            seeds.append(MediaSeed(
                seed_id="music_collection",
                context="Local music library",
                expansion=ExpansionRule(
                    target_count=music_data["track_count"],
                    date_range_days=self._timeline_days,
                ),
                media_type=MediaType.MUSIC,
                artists=music_data["artists"],
                albums=music_data["albums"],
                playlists=music_data["playlists"],
                extension="mp3",
                size_range_bytes=(3_000_000, 12_000_000),
            ))

        return seeds


# ---------------------------------------------------------------------------
# Fallback data tables
# ---------------------------------------------------------------------------

_PHOTO_EVENTS_BY_PROFILE = {
    "home_user": [
        {"name": "vacation_beach", "count": 85, "span_days": 5},
        {"name": "birthday_party", "count": 42, "span_days": 1},
        {"name": "thanksgiving_dinner", "count": 28, "span_days": 1},
        {"name": "backyard_weekend", "count": 31, "span_days": 2},
        {"name": "christmas_morning", "count": 47, "span_days": 1},
        {"name": "new_years_eve", "count": 19, "span_days": 1},
        {"name": "spring_hiking", "count": 63, "span_days": 3},
        {"name": "kids_school_play", "count": 24, "span_days": 1},
    ],
    "office_user": [
        {"name": "team_offsite", "count": 38, "span_days": 2},
        {"name": "conference_chicago", "count": 51, "span_days": 3},
        {"name": "company_picnic", "count": 29, "span_days": 1},
        {"name": "product_launch_event", "count": 22, "span_days": 1},
        {"name": "weekend_family", "count": 44, "span_days": 2},
        {"name": "holiday_party", "count": 33, "span_days": 1},
    ],
}

_SCREENSHOT_COUNT = {
    "developer": 180,
    "office_user": 90,
    "home_user": 55,
}

_MUSIC_BY_PROFILE = {
    "home_user": {
        "track_count": 340,
        "artists": ["Taylor Swift", "Ed Sheeran", "Billie Eilish", "Drake",
                    "Imagine Dragons", "Post Malone", "Dua Lipa", "The Weeknd"],
        "albums": ["Midnights", "Divide", "Happier Than Ever", "Certified Lover Boy",
                   "Night Visions", "Hollywood's Bleeding", "Future Nostalgia"],
        "playlists": ["Workout Mix", "Chill Vibes", "Road Trip", "Top Hits 2024"],
    },
    "developer": {
        "track_count": 520,
        "artists": ["Daft Punk", "Boards of Canada", "Aphex Twin", "Radiohead",
                    "Nine Inch Nails", "Deadmau5", "Massive Attack", "Portishead"],
        "albums": ["Random Access Memories", "Music Has the Right to Children",
                   "OK Computer", "The Downward Spiral", "Mezzanine"],
        "playlists": ["Focus / Deep Work", "Lo-fi Coding", "Synthwave", "Post-Rock"],
    },
}
