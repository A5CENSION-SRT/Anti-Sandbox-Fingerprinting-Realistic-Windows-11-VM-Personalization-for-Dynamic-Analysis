"""Browsing pattern seed generator using Gemini API.

Generates URL patterns, search term themes, and bookmark categories that
will be expanded into 5000-10000 browser history entries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import (
    BrowsingPatternSeed,
    BrowsingSeed,
    ExpansionRule,
    PersonaContext,
    VisitFrequency,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_BROWSING_PROMPT_FILE = _PROMPTS_DIR / "browsing.txt"


class BrowsingSeedGenerationError(Exception):
    """Raised when browsing seed generation fails."""


class BrowsingSeedGenerator:
    """Generates browsing behavior seeds via Gemini API.
    
    Args:
        client: Configured GeminiClient instance.
        total_target: Target total history entries after expansion (default: 7500).
    """
    
    def __init__(
        self,
        client: GeminiClient,
        total_target: int = 7500,
    ) -> None:
        self._client = client
        self._total_target = total_target
        self._prompt_template = self._load_prompt()
    
    @staticmethod
    def _load_prompt() -> str:
        """Load the browsing prompt template."""
        if _BROWSING_PROMPT_FILE.exists():
            return _BROWSING_PROMPT_FILE.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Browsing prompt not found: {_BROWSING_PROMPT_FILE}")
    
    def generate(
        self,
        persona: PersonaContext,
        use_cache: bool = True,
    ) -> BrowsingSeed:
        """Generate browsing seeds for the given persona.
        
        Args:
            persona: PersonaContext to generate browsing for.
            use_cache: Whether to use cached responses.
        
        Returns:
            BrowsingSeed instance.
        """
        prompt = self._prompt_template.format(
            full_name=persona.full_name,
            occupation=persona.occupation,
            organization=persona.organization,
            tech_proficiency=persona.tech_proficiency.value,
            hobbies=", ".join(persona.interests.hobbies),
            professional_topics=", ".join(persona.interests.professional_topics),
            entertainment=", ".join(persona.interests.entertainment),
            work_hours_start=persona.work_hours_start,
            work_hours_end=persona.work_hours_end,
            active_days=persona.active_days,
            total_history=self._total_target,
        )
        
        logger.info("Generating browsing seeds for %s", persona.full_name)
        
        try:
            seed = self._client.generate_structured(
                prompt=prompt,
                schema=BrowsingSeed,
                temperature=0.7,
                use_cache=use_cache,
            )
            
            logger.info(
                "Generated browsing seed: %d URLs, %d search themes, %d bookmark categories",
                len(seed.url_patterns),
                len(seed.search_term_themes),
                len(seed.bookmark_categories),
            )
            return seed
            
        except GeminiClientError as e:
            logger.error("Gemini API error generating browsing seeds: %s", e)
            return self._generate_fallback_seed(persona)
    
    def _generate_fallback_seed(self, persona: PersonaContext) -> BrowsingSeed:
        """Generate fallback seed when API is unavailable."""
        logger.warning("Using fallback browsing seeds for %s", persona.full_name)
        
        profile_type = self._infer_profile_type(persona)
        
        # Common URLs everyone uses
        common_urls = [
            BrowsingPatternSeed(
                url="https://www.google.com/",
                title="Google",
                frequency=VisitFrequency.MULTIPLE_DAILY,
                typical_times=["09:00-18:00"],
                context="Primary search engine",
                generates_downloads=False,
            ),
            BrowsingPatternSeed(
                url="https://mail.google.com/",
                title="Gmail - Inbox",
                frequency=VisitFrequency.MULTIPLE_DAILY,
                typical_times=["09:00-10:00", "12:00-13:00", "17:00-18:00"],
                context="Email communication",
                generates_downloads=True,
            ),
            BrowsingPatternSeed(
                url="https://www.youtube.com/",
                title="YouTube",
                frequency=VisitFrequency.DAILY,
                typical_times=["12:00-13:00", "19:00-22:00"],
                context="Video entertainment",
                generates_downloads=False,
            ),
            BrowsingPatternSeed(
                url="https://www.amazon.com/",
                title="Amazon.com: Online Shopping",
                frequency=VisitFrequency.WEEKLY,
                typical_times=["19:00-21:00"],
                context="Online shopping",
                generates_downloads=True,
            ),
        ]
        
        profile_urls = {
            "developer": [
                BrowsingPatternSeed(
                    url="https://github.com/",
                    title="GitHub",
                    frequency=VisitFrequency.MULTIPLE_DAILY,
                    typical_times=["09:00-18:00"],
                    context="Code hosting and collaboration",
                    generates_downloads=True,
                ),
                BrowsingPatternSeed(
                    url="https://stackoverflow.com/",
                    title="Stack Overflow",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["10:00-17:00"],
                    context="Programming Q&A",
                    generates_downloads=False,
                ),
                BrowsingPatternSeed(
                    url="https://docs.python.org/3/",
                    title="Python Documentation",
                    frequency=VisitFrequency.WEEKLY,
                    typical_times=["10:00-16:00"],
                    context="Programming reference",
                    generates_downloads=False,
                ),
            ],
            "office_user": [
                BrowsingPatternSeed(
                    url="https://outlook.office365.com/",
                    title="Outlook - Microsoft 365",
                    frequency=VisitFrequency.MULTIPLE_DAILY,
                    typical_times=["09:00-17:00"],
                    context="Work email",
                    generates_downloads=True,
                ),
                BrowsingPatternSeed(
                    url="https://teams.microsoft.com/",
                    title="Microsoft Teams",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["09:00-17:00"],
                    context="Team collaboration",
                    generates_downloads=True,
                ),
                BrowsingPatternSeed(
                    url="https://www.linkedin.com/feed/",
                    title="LinkedIn",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["08:30-09:00", "12:00-12:30"],
                    context="Professional networking",
                    generates_downloads=False,
                ),
            ],
            "home_user": [
                BrowsingPatternSeed(
                    url="https://www.netflix.com/browse",
                    title="Netflix",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["19:00-23:00"],
                    context="Streaming entertainment",
                    generates_downloads=False,
                ),
                BrowsingPatternSeed(
                    url="https://www.reddit.com/",
                    title="Reddit",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["12:00-13:00", "20:00-22:00"],
                    context="Social news and discussion",
                    generates_downloads=False,
                ),
                BrowsingPatternSeed(
                    url="https://www.facebook.com/",
                    title="Facebook",
                    frequency=VisitFrequency.DAILY,
                    typical_times=["08:00-09:00", "19:00-21:00"],
                    context="Social networking",
                    generates_downloads=False,
                ),
            ],
        }
        
        search_themes = {
            "developer": [
                "how to {task} in Python",
                "{error_message} fix",
                "best practices for {topic}",
                "{library} documentation",
                "git {command} tutorial",
            ],
            "office_user": [
                "excel {function} formula",
                "how to {task} in Word",
                "{industry} trends 2024",
                "best {tool} alternatives",
                "project management {topic}",
            ],
            "home_user": [
                "{product} reviews",
                "best {category} 2024",
                "{recipe} recipe easy",
                "{destination} travel tips",
                "how to {task} at home",
            ],
        }
        
        bookmark_categories = {
            "developer": {
                "Development": ["https://github.com/", "https://stackoverflow.com/"],
                "Documentation": ["https://docs.python.org/3/", "https://developer.mozilla.org/"],
                "Tools": ["https://cloud.google.com/", "https://aws.amazon.com/"],
            },
            "office_user": {
                "Work": ["https://outlook.office365.com/", "https://teams.microsoft.com/"],
                "News": ["https://www.reuters.com/", "https://www.bbc.com/news"],
                "Productivity": ["https://www.notion.so/", "https://trello.com/"],
            },
            "home_user": {
                "Entertainment": ["https://www.netflix.com/", "https://www.youtube.com/"],
                "Shopping": ["https://www.amazon.com/", "https://www.ebay.com/"],
                "Social": ["https://www.facebook.com/", "https://www.reddit.com/"],
            },
        }
        
        urls = common_urls + profile_urls.get(profile_type, [])
        
        return BrowsingSeed(
            seed_id="browsing_fallback",
            context=f"Fallback browsing patterns for {profile_type}",
            expansion=ExpansionRule(
                target_count=self._total_target,
                date_range_days=90,
            ),
            url_patterns=urls,
            search_term_themes=search_themes.get(profile_type, search_themes["home_user"]),
            bookmark_categories=bookmark_categories.get(profile_type, {}),
        )
    
    def _infer_profile_type(self, persona: PersonaContext) -> str:
        """Infer profile type from persona."""
        occupation_lower = persona.occupation.lower()
        if any(x in occupation_lower for x in ["developer", "engineer", "programmer"]):
            return "developer"
        if persona.organization.lower() == "personal":
            return "home_user"
        return "office_user"
