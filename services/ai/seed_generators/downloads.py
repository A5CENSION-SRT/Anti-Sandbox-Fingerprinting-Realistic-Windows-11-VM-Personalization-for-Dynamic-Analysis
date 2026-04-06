"""Download seed generator using Gemini API.

Generates 10-30 download seeds that will be expanded into 500-2000
unique download artifacts by the local permutation engine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import DownloadSeed, ExpansionRule, PersonaContext

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DOWNLOADS_PROMPT_FILE = _PROMPTS_DIR / "downloads.txt"


class DownloadSeedGenerationError(Exception):
    """Raised when download seed generation fails."""


class DownloadSeedGenerator:
    """Generates download artifact seeds via Gemini API.
    
    Args:
        client: Configured GeminiClient instance.
        seed_count: Number of seeds to generate (default: 20).
        total_target: Target total downloads after expansion (default: 1500).
    """
    
    def __init__(
        self,
        client: GeminiClient,
        seed_count: int = 20,
        total_target: int = 1500,
    ) -> None:
        self._client = client
        self._seed_count = seed_count
        self._total_target = total_target
        self._prompt_template = self._load_prompt()
    
    @staticmethod
    def _load_prompt() -> str:
        """Load the downloads prompt template."""
        if _DOWNLOADS_PROMPT_FILE.exists():
            return _DOWNLOADS_PROMPT_FILE.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Downloads prompt not found: {_DOWNLOADS_PROMPT_FILE}")
    
    def generate(
        self,
        persona: PersonaContext,
        use_cache: bool = True,
    ) -> List[DownloadSeed]:
        """Generate download seeds for the given persona.
        
        Args:
            persona: PersonaContext to generate downloads for.
            use_cache: Whether to use cached responses.
        
        Returns:
            List of DownloadSeed instances.
        """
        prompt = self._prompt_template.format(
            seed_count=self._seed_count,
            full_name=persona.full_name,
            occupation=persona.occupation,
            organization=persona.organization,
            tech_proficiency=persona.tech_proficiency.value,
            interests=", ".join(persona.interests.hobbies),
            tools=", ".join(persona.work_style.typical_tools),
            total_downloads=self._total_target,
        )
        
        logger.info("Generating %d download seeds for %s", self._seed_count, persona.full_name)
        
        try:
            seeds = self._client.generate_list(
                prompt=prompt,
                item_schema=DownloadSeed,
                temperature=0.7,
                use_cache=use_cache,
            )
            
            logger.info("Generated %d download seeds", len(seeds))
            return seeds
            
        except GeminiClientError as e:
            logger.error("Gemini API error generating download seeds: %s", e)
            return self._generate_fallback_seeds(persona)
    
    def _generate_fallback_seeds(self, persona: PersonaContext) -> List[DownloadSeed]:
        """Generate fallback seeds when API is unavailable."""
        logger.warning("Using fallback download seeds for %s", persona.full_name)
        
        profile_type = self._infer_profile_type(persona)
        
        fallback_seeds = {
            "developer": [
                DownloadSeed(
                    seed_id="dl_dev_001",
                    filename_pattern="Python-{version}-amd64.exe",
                    url_template="https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe",
                    referrer_template="https://www.python.org/downloads/",
                    mime_type="application/octet-stream",
                    size_range_bytes=(26000000, 30000000),
                    context="Python installer downloads",
                    variables={"version": ["3.11.0", "3.11.5", "3.12.0", "3.12.1", "3.12.2"]},
                    expansion=ExpansionRule(target_count=30, date_range_days=90),
                ),
                DownloadSeed(
                    seed_id="dl_dev_002",
                    filename_pattern="VSCodeSetup-x64-{version}.exe",
                    url_template="https://update.code.visualstudio.com/{version}/win32-x64/stable",
                    referrer_template="https://code.visualstudio.com/download",
                    mime_type="application/octet-stream",
                    size_range_bytes=(80000000, 95000000),
                    context="VS Code updates",
                    variables={"version": ["1.85.0", "1.85.1", "1.86.0", "1.86.1", "1.87.0"]},
                    expansion=ExpansionRule(target_count=25, date_range_days=90),
                ),
                DownloadSeed(
                    seed_id="dl_dev_003",
                    filename_pattern="{repo}_main.zip",
                    url_template="https://github.com/{owner}/{repo}/archive/refs/heads/main.zip",
                    referrer_template="https://github.com/{owner}/{repo}",
                    mime_type="application/zip",
                    size_range_bytes=(100000, 50000000),
                    context="GitHub repository downloads",
                    variables={
                        "owner": ["microsoft", "facebook", "google", "torvalds"],
                        "repo": ["vscode", "react", "tensorflow", "linux"],
                    },
                    expansion=ExpansionRule(target_count=50, date_range_days=90),
                ),
            ],
            "office_user": [
                DownloadSeed(
                    seed_id="dl_office_001",
                    filename_pattern="{doc_type}_{quarter}_{year}.pdf",
                    url_template="https://acmecorp.sharepoint.com/sites/Reports/{doc_type}_{quarter}_{year}.pdf",
                    referrer_template="https://acmecorp.sharepoint.com/sites/Reports",
                    mime_type="application/pdf",
                    size_range_bytes=(500000, 5000000),
                    context="Quarterly reports from SharePoint",
                    variables={
                        "doc_type": ["Financial_Report", "Sales_Summary", "Marketing_Review"],
                        "quarter": ["Q1", "Q2", "Q3", "Q4"],
                        "year": ["2023", "2024"],
                    },
                    expansion=ExpansionRule(target_count=100, date_range_days=90),
                ),
                DownloadSeed(
                    seed_id="dl_office_002",
                    filename_pattern="Teams_installer.exe",
                    url_template="https://statics.teams.cdn.office.net/production-windows-x64/{version}/Teams_installer.exe",
                    referrer_template="https://www.microsoft.com/en-us/microsoft-teams/download-app",
                    mime_type="application/octet-stream",
                    size_range_bytes=(100000000, 120000000),
                    context="Microsoft Teams installer",
                    variables={"version": ["24.1.0.21", "24.2.0.15", "24.3.0.10"]},
                    expansion=ExpansionRule(target_count=15, date_range_days=90),
                ),
            ],
            "home_user": [
                DownloadSeed(
                    seed_id="dl_home_001",
                    filename_pattern="Spotify-Setup.exe",
                    url_template="https://download.scdn.co/SpotifySetup.exe",
                    referrer_template="https://www.spotify.com/download/",
                    mime_type="application/octet-stream",
                    size_range_bytes=(45000000, 50000000),
                    context="Spotify installer",
                    variables={},
                    expansion=ExpansionRule(target_count=10, date_range_days=90),
                ),
                DownloadSeed(
                    seed_id="dl_home_002",
                    filename_pattern="amazon_order_{order_id}.pdf",
                    url_template="https://www.amazon.com/gp/css/summary/print.html?orderID={order_id}",
                    referrer_template="https://www.amazon.com/gp/your-account/order-history",
                    mime_type="application/pdf",
                    size_range_bytes=(50000, 200000),
                    context="Amazon order invoices",
                    variables={"order_id": ["113-1234567", "114-2345678", "115-3456789"]},
                    expansion=ExpansionRule(target_count=50, date_range_days=90),
                ),
            ],
        }
        
        return fallback_seeds.get(profile_type, fallback_seeds["home_user"])
    
    def _infer_profile_type(self, persona: PersonaContext) -> str:
        """Infer profile type from persona."""
        occupation_lower = persona.occupation.lower()
        if any(x in occupation_lower for x in ["developer", "engineer", "programmer"]):
            return "developer"
        if persona.organization.lower() == "personal":
            return "home_user"
        return "office_user"
