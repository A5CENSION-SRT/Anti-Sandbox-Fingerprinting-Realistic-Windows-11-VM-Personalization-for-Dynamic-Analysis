"""Document seed generator using Gemini API.

Generates 20-50 document seeds that will be expanded into 2000-5000
unique document artifacts by the local permutation engine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import DocumentSeed, ExpansionRule, FileCategory, PersonaContext

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DOCUMENTS_PROMPT_FILE = _PROMPTS_DIR / "documents.txt"


class DocumentSeedGenerationError(Exception):
    """Raised when document seed generation fails."""


class DocumentSeedGenerator:
    """Generates document artifact seeds via Gemini API.
    
    Args:
        client: Configured GeminiClient instance.
        seed_count: Number of seeds to generate (default: 30).
        total_target: Target total documents after expansion (default: 4500).
    """
    
    def __init__(
        self,
        client: GeminiClient,
        seed_count: int = 30,
        total_target: int = 4500,
    ) -> None:
        self._client = client
        self._seed_count = seed_count
        self._total_target = total_target
        self._prompt_template = self._load_prompt()
    
    @staticmethod
    def _load_prompt() -> str:
        """Load the documents prompt template."""
        if _DOCUMENTS_PROMPT_FILE.exists():
            return _DOCUMENTS_PROMPT_FILE.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Documents prompt not found: {_DOCUMENTS_PROMPT_FILE}")
    
    def generate(
        self,
        persona: PersonaContext,
        use_cache: bool = True,
    ) -> List[DocumentSeed]:
        """Generate document seeds for the given persona.
        
        Args:
            persona: PersonaContext to generate documents for.
            use_cache: Whether to use cached responses.
        
        Returns:
            List of DocumentSeed instances.
        """
        prompt = self._prompt_template.format(
            seed_count=self._seed_count,
            full_name=persona.full_name,
            occupation=persona.occupation,
            organization=persona.organization,
            department=persona.department or "General",
            projects=", ".join(persona.project_names[:5]),
            colleagues=", ".join(persona.colleague_names[:5]),
            work_style=persona.work_style.description,
            total_documents=self._total_target,
        )
        
        logger.info("Generating %d document seeds for %s", self._seed_count, persona.full_name)
        
        try:
            seeds = self._client.generate_list(
                prompt=prompt,
                item_schema=DocumentSeed,
                temperature=0.7,
                use_cache=use_cache,
            )
            
            logger.info("Generated %d document seeds", len(seeds))
            return seeds
            
        except GeminiClientError as e:
            logger.error("Gemini API error generating document seeds: %s", e)
            return self._generate_fallback_seeds(persona)
    
    def _generate_fallback_seeds(self, persona: PersonaContext) -> List[DocumentSeed]:
        """Generate fallback seeds when API is unavailable."""
        logger.warning("Using fallback document seeds for %s", persona.full_name)
        
        profile_type = self._infer_profile_type(persona)
        
        fallback_seeds = {
            "developer": [
                DocumentSeed(
                    seed_id="doc_dev_001",
                    filename_pattern="{project}_README.md",
                    document_type="md",
                    category=FileCategory.TECHNICAL,
                    content_theme="Project documentation with setup instructions",
                    content_template="# {project}\n\n## Overview\n\n{description}\n\n## Installation\n\n```bash\npip install -r requirements.txt\n```\n\n## Usage\n\n{usage}",
                    variables={
                        "project": persona.project_names,
                        "description": ["Core service implementation", "API gateway module", "Data processing pipeline"],
                        "usage": ["Run `python main.py`", "Execute `./run.sh`"],
                    },
                    subfolder="source/repos",
                    expansion=ExpansionRule(target_count=100, date_range_days=90),
                ),
                DocumentSeed(
                    seed_id="doc_dev_002",
                    filename_pattern="Sprint_{sprint_num}_Notes_{date}.txt",
                    document_type="txt",
                    category=FileCategory.WORK,
                    content_theme="Sprint planning and retrospective notes",
                    variables={
                        "sprint_num": [str(i) for i in range(20, 35)],
                    },
                    subfolder="Documents",
                    expansion=ExpansionRule(target_count=150, date_range_days=90, include_dates=True),
                ),
            ],
            "office_user": [
                DocumentSeed(
                    seed_id="doc_office_001",
                    filename_pattern="{project}_Status_Report_{date}.docx",
                    document_type="docx",
                    category=FileCategory.WORK,
                    content_theme="Weekly project status update with milestones and blockers",
                    content_template="# {project} Status Report\n\nDate: {date}\nPrepared by: {author}\n\n## Summary\n{summary}\n\n## This Week\n{accomplishments}\n\n## Blockers\n{blockers}",
                    variables={
                        "project": persona.project_names,
                        "author": [persona.full_name],
                        "summary": ["On track", "Minor delays", "Ahead of schedule"],
                        "accomplishments": ["Completed phase 1", "Client meeting done"],
                        "blockers": ["Awaiting approval", "Resource constraints"],
                    },
                    subfolder="Documents/Work",
                    expansion=ExpansionRule(target_count=200, date_range_days=90, include_dates=True),
                ),
                DocumentSeed(
                    seed_id="doc_office_002",
                    filename_pattern="{department}_Budget_{quarter}_{year}.xlsx",
                    document_type="xlsx",
                    category=FileCategory.FINANCIAL,
                    content_theme="Departmental budget spreadsheet",
                    variables={
                        "department": ["Marketing", "Sales", "Operations", "Engineering"],
                        "quarter": ["Q1", "Q2", "Q3", "Q4"],
                        "year": ["2023", "2024"],
                    },
                    subfolder="Documents/Finance",
                    expansion=ExpansionRule(target_count=150, date_range_days=90),
                ),
                DocumentSeed(
                    seed_id="doc_office_003",
                    filename_pattern="Meeting_Notes_{topic}_{date}.docx",
                    document_type="docx",
                    category=FileCategory.WORK,
                    content_theme="Meeting notes with attendees and action items",
                    variables={
                        "topic": ["TeamSync", "ClientCall", "Planning", "Review", "Standup"],
                    },
                    subfolder="Documents/Meetings",
                    expansion=ExpansionRule(target_count=300, date_range_days=90, include_dates=True),
                ),
            ],
            "home_user": [
                DocumentSeed(
                    seed_id="doc_home_001",
                    filename_pattern="Shopping_List_{date}.txt",
                    document_type="txt",
                    category=FileCategory.PERSONAL,
                    content_theme="Grocery and shopping lists",
                    content_template="Shopping List - {date}\n\nGroceries:\n- Milk\n- Bread\n- Eggs\n- {items}\n\nHousehold:\n- {household}",
                    variables={
                        "items": ["Cheese, Yogurt", "Vegetables, Fruit", "Snacks, Coffee"],
                        "household": ["Paper towels", "Dish soap", "Batteries"],
                    },
                    subfolder="Documents/Personal",
                    expansion=ExpansionRule(target_count=100, date_range_days=90, include_dates=True),
                ),
                DocumentSeed(
                    seed_id="doc_home_002",
                    filename_pattern="Recipe_{dish}.txt",
                    document_type="txt",
                    category=FileCategory.PERSONAL,
                    content_theme="Recipe collection",
                    variables={
                        "dish": ["Pasta", "Salad", "Soup", "Stir_Fry", "Cookies", "Bread", "Smoothie"],
                    },
                    subfolder="Documents/Recipes",
                    expansion=ExpansionRule(target_count=50, date_range_days=90),
                ),
                DocumentSeed(
                    seed_id="doc_home_003",
                    filename_pattern="Budget_{month}_{year}.xlsx",
                    document_type="xlsx",
                    category=FileCategory.FINANCIAL,
                    content_theme="Personal monthly budget tracking",
                    variables={
                        "month": ["January", "February", "March", "April", "May", "June",
                                 "July", "August", "September", "October", "November", "December"],
                        "year": ["2023", "2024"],
                    },
                    subfolder="Documents/Finance",
                    expansion=ExpansionRule(target_count=75, date_range_days=365),
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
