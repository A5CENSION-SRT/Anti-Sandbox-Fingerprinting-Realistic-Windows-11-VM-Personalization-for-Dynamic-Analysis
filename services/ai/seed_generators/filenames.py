"""Filename pattern seed generator using Gemini API.

Generates reusable filename patterns that can be combined with
permutation engines to create thousands of realistic filenames.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import ExpansionRule, FilenameSeed, PersonaContext

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_FILENAMES_PROMPT_FILE = _PROMPTS_DIR / "filenames.txt"


class FilenameSeedGenerationError(Exception):
    """Raised when filename seed generation fails."""


class FilenameSeedGenerator:
    """Generates filename pattern seeds via Gemini API.
    
    Args:
        client: Configured GeminiClient instance.
        seed_count: Number of patterns to generate (default: 15).
    """
    
    def __init__(
        self,
        client: GeminiClient,
        seed_count: int = 15,
    ) -> None:
        self._client = client
        self._seed_count = seed_count
        self._prompt_template = self._load_prompt()
    
    @staticmethod
    def _load_prompt() -> str:
        """Load the filenames prompt template."""
        if _FILENAMES_PROMPT_FILE.exists():
            return _FILENAMES_PROMPT_FILE.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Filenames prompt not found: {_FILENAMES_PROMPT_FILE}")
    
    def generate(
        self,
        persona: PersonaContext,
        use_cache: bool = True,
    ) -> List[FilenameSeed]:
        """Generate filename pattern seeds for the given persona.
        
        Args:
            persona: PersonaContext to generate patterns for.
            use_cache: Whether to use cached responses.
        
        Returns:
            List of FilenameSeed instances.
        """
        prompt = self._prompt_template.format(
            full_name=persona.full_name,
            occupation=persona.occupation,
            organization=persona.organization,
            projects=", ".join(persona.project_names[:5]),
            work_style=persona.work_style.description,
        )
        
        logger.info("Generating %d filename patterns for %s", self._seed_count, persona.full_name)
        
        try:
            seeds = self._client.generate_list(
                prompt=prompt,
                item_schema=FilenameSeed,
                temperature=0.7,
                use_cache=use_cache,
            )
            
            logger.info("Generated %d filename patterns", len(seeds))
            return seeds
            
        except GeminiClientError as e:
            logger.error("Gemini API error generating filename patterns: %s", e)
            return self._generate_fallback_seeds(persona)
    
    def _generate_fallback_seeds(self, persona: PersonaContext) -> List[FilenameSeed]:
        """Generate fallback seeds when API is unavailable."""
        logger.warning("Using fallback filename patterns for %s", persona.full_name)
        
        return [
            # Work documents
            FilenameSeed(
                seed_id="fn_project_doc",
                pattern="{project}_{doc_type}_{date}{suffix}.{ext}",
                context="Project documentation files",
                variables={
                    "project": persona.project_names,
                    "doc_type": ["Report", "Notes", "Summary", "Plan", "Review"],
                    "ext": ["docx", "pdf", "xlsx"],
                },
                date_formats=["%Y%m%d", "%Y-%m-%d"],
                version_styles=["v{n}", "_v{n}"],
                suffix_options=["", "_DRAFT", "_Final", "_reviewed"],
                expansion=ExpansionRule(target_count=300, date_range_days=90),
            ),
            
            # Meeting notes
            FilenameSeed(
                seed_id="fn_meeting",
                pattern="Meeting_{topic}_{date}.{ext}",
                context="Meeting notes and agendas",
                variables={
                    "topic": ["TeamSync", "ClientCall", "Planning", "Review", "Standup", "Retro"],
                    "ext": ["docx", "txt", "md"],
                },
                date_formats=["%Y%m%d", "%B_%d"],
                version_styles=[],
                suffix_options=[""],
                expansion=ExpansionRule(target_count=200, date_range_days=90),
            ),
            
            # Photos
            FilenameSeed(
                seed_id="fn_photo",
                pattern="IMG_{date}_{counter}.jpg",
                context="Camera photos with sequential numbering",
                variables={},
                date_formats=["%Y%m%d"],
                version_styles=[],
                suffix_options=[""],
                expansion=ExpansionRule(target_count=500, date_range_days=90),
            ),
            
            # Screenshots
            FilenameSeed(
                seed_id="fn_screenshot",
                pattern="Screenshot_{date}_{time}.png",
                context="Desktop screenshots",
                variables={},
                date_formats=["%Y-%m-%d"],
                version_styles=[],
                suffix_options=[""],
                expansion=ExpansionRule(target_count=100, date_range_days=90),
            ),
            
            # Downloads
            FilenameSeed(
                seed_id="fn_download",
                pattern="{app}_Setup{suffix}.exe",
                context="Application installers",
                variables={
                    "app": ["Chrome", "Firefox", "Spotify", "Discord", "Zoom", "Teams"],
                },
                date_formats=[],
                version_styles=["_{n}", "-{n}"],
                suffix_options=["", "_x64", "_win64"],
                expansion=ExpansionRule(target_count=50, date_range_days=90),
            ),
            
            # Spreadsheets
            FilenameSeed(
                seed_id="fn_spreadsheet",
                pattern="{category}_{period}_{year}{suffix}.xlsx",
                context="Financial and tracking spreadsheets",
                variables={
                    "category": ["Budget", "Expenses", "Revenue", "Forecast", "Tracking"],
                    "period": ["Q1", "Q2", "Q3", "Q4", "Annual", "Monthly"],
                    "year": ["2023", "2024"],
                },
                date_formats=[],
                version_styles=["v{n}"],
                suffix_options=["", "_Final", "_DRAFT"],
                expansion=ExpansionRule(target_count=150, date_range_days=180),
            ),
            
            # PDFs
            FilenameSeed(
                seed_id="fn_pdf",
                pattern="{doc_name}_{date}.pdf",
                context="PDF reports and documents",
                variables={
                    "doc_name": ["Invoice", "Receipt", "Contract", "Agreement", "Policy", "Manual"],
                },
                date_formats=["%Y%m%d", "%Y-%m-%d"],
                version_styles=[],
                suffix_options=["", "_signed", "_final"],
                expansion=ExpansionRule(target_count=100, date_range_days=90),
            ),
        ]
