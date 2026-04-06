"""Persona generator using Gemini API.

Generates detailed, coherent user personas from minimal inputs (occupation, hints).
The persona serves as the foundation for all artifact seed generation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import PersonaContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PERSONA_PROMPT_FILE = _PROMPTS_DIR / "persona.txt"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PersonaGenerationError(Exception):
    """Raised when persona generation fails."""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class PersonaGenerator:
    """Generates detailed user personas via Gemini API.
    
    Takes minimal input (occupation, location, hints) and produces a complete
    PersonaContext with coherent identity, interests, work style, and context
    for artifact generation.
    
    Args:
        client: Configured GeminiClient instance.
        prompt_template: Optional custom prompt template (uses default if None).
    
    Example:
        >>> client = GeminiClient(api_key="...")
        >>> generator = PersonaGenerator(client)
        >>> persona = generator.generate(
        ...     occupation="Senior Marketing Manager",
        ...     location="San Francisco, CA",
        ...     hints="loves travel photography, works at tech startup"
        ... )
        >>> print(persona.full_name, persona.interests.hobbies)
    """
    
    def __init__(
        self,
        client: GeminiClient,
        prompt_template: Optional[str] = None,
    ) -> None:
        self._client = client
        self._prompt_template = prompt_template or self._load_default_prompt()
    
    @staticmethod
    def _load_default_prompt() -> str:
        """Load the default persona prompt template."""
        if _PERSONA_PROMPT_FILE.exists():
            return _PERSONA_PROMPT_FILE.read_text(encoding="utf-8")
        
        # Fallback minimal prompt if file missing
        return """Generate a detailed persona for a Windows 11 user.
Role: {occupation}
Location: {location}
Hints: {hints}

Output valid JSON matching PersonaContext schema."""
    
    def generate(
        self,
        occupation: str,
        location: str = "United States",
        hints: str = "",
        temperature: float = 0.7,
        use_cache: bool = True,
    ) -> PersonaContext:
        """Generate a complete persona from minimal inputs.
        
        Args:
            occupation: Job title or role (e.g., "Software Engineer", "College Student")
            location: Geographic location for locale/cultural context
            hints: Additional context (interests, company type, age, etc.)
            temperature: Generation temperature (higher = more creative)
            use_cache: Whether to use cached responses
        
        Returns:
            Validated PersonaContext instance.
        
        Raises:
            PersonaGenerationError: If generation or validation fails.
        """
        prompt = self._prompt_template.format(
            occupation=occupation,
            location=location,
            hints=hints or "none provided",
        )
        
        logger.info(
            "Generating persona for occupation='%s', location='%s'",
            occupation, location
        )
        
        try:
            persona = self._client.generate_structured(
                prompt=prompt,
                schema=PersonaContext,
                temperature=temperature,
                use_cache=use_cache,
            )
            
            logger.info(
                "Generated persona: %s <%s> at %s",
                persona.full_name, persona.email, persona.organization
            )
            return persona
            
        except GeminiClientError as e:
            raise PersonaGenerationError(f"Gemini API error: {e}") from e
        except Exception as e:
            raise PersonaGenerationError(
                f"Unexpected error generating persona: {e}"
            ) from e
    
    def generate_batch(
        self,
        specifications: list[dict],
        temperature: float = 0.8,
        use_cache: bool = True,
    ) -> list[PersonaContext]:
        """Generate multiple diverse personas.
        
        Args:
            specifications: List of dicts with keys: occupation, location, hints
            temperature: Higher temperature for diversity
            use_cache: Whether to use cached responses
        
        Returns:
            List of PersonaContext instances.
        """
        personas = []
        for spec in specifications:
            try:
                persona = self.generate(
                    occupation=spec.get("occupation", "Office Worker"),
                    location=spec.get("location", "United States"),
                    hints=spec.get("hints", ""),
                    temperature=temperature,
                    use_cache=use_cache,
                )
                personas.append(persona)
            except PersonaGenerationError as e:
                logger.error("Failed to generate persona for %s: %s", spec, e)
                # Continue with other specs
        
        return personas


# ---------------------------------------------------------------------------
# Fallback Persona (when API unavailable)
# ---------------------------------------------------------------------------

def create_fallback_persona(
    occupation: str = "Office Worker",
    profile_type: str = "office_user",
) -> PersonaContext:
    """Create a minimal fallback persona when Gemini API is unavailable.
    
    Uses hardcoded values that align with existing static profiles.
    
    Args:
        occupation: Job title
        profile_type: One of home_user, office_user, developer
    
    Returns:
        Basic PersonaContext instance.
    """
    from services.ai.schemas import PersonaInterests, PersonaWorkStyle, TechProficiency
    
    # Profile-specific defaults
    defaults = {
        "home_user": {
            "full_name": "Alex Johnson",
            "organization": "Personal",
            "occupation": "Homeowner",
            "tech_proficiency": TechProficiency.INTERMEDIATE,
            "hobbies": ["cooking", "gardening", "reading", "hiking", "photography"],
            "professional_topics": ["home improvement", "personal finance"],
            "entertainment": ["streaming shows", "podcasts", "casual games"],
            "tools": ["web browser", "email", "social media"],
            "projects": ["Home Renovation", "Garden Project", "Photo Organization"],
            "work_hours": (18, 23),
            "active_days": [6, 7],
        },
        "office_user": {
            "full_name": "Sarah Mitchell",
            "organization": "Acme Corporation",
            "occupation": occupation or "Marketing Manager",
            "tech_proficiency": TechProficiency.INTERMEDIATE,
            "hobbies": ["yoga", "travel", "wine tasting", "networking"],
            "professional_topics": ["marketing trends", "leadership", "analytics"],
            "entertainment": ["business podcasts", "documentaries"],
            "tools": ["Microsoft Office", "Slack", "Salesforce", "Zoom"],
            "projects": ["Q4 Campaign", "Brand Refresh", "Customer Survey", "Website Redesign"],
            "work_hours": (9, 17),
            "active_days": [1, 2, 3, 4, 5],
        },
        "developer": {
            "full_name": "Jordan Chen",
            "organization": "TechStart Inc.",
            "occupation": occupation or "Senior Software Engineer",
            "tech_proficiency": TechProficiency.HIGH,
            "hobbies": ["open source", "gaming", "3D printing", "home automation"],
            "professional_topics": ["cloud architecture", "machine learning", "devops"],
            "entertainment": ["tech podcasts", "sci-fi", "strategy games"],
            "tools": ["VS Code", "Docker", "Git", "Terminal", "Postman"],
            "projects": ["API Gateway", "Auth Service", "Data Pipeline", "Mobile App"],
            "work_hours": (10, 19),
            "active_days": [1, 2, 3, 4, 5],
        },
    }
    
    cfg = defaults.get(profile_type, defaults["office_user"])
    
    # Derive username from name
    name_parts = cfg["full_name"].lower().split()
    username = f"{name_parts[0]}.{name_parts[-1]}" if len(name_parts) >= 2 else name_parts[0]
    
    # Derive email domain from org
    org_clean = cfg["organization"].lower().replace(" ", "").replace(".", "")
    domain = f"{org_clean[:12]}.com" if cfg["organization"] != "Personal" else "gmail.com"
    
    return PersonaContext(
        full_name=cfg["full_name"],
        username=username,
        email=f"{username}@{domain}",
        organization=cfg["organization"],
        occupation=cfg["occupation"],
        department=None,
        age_range="28-42",
        locale="en_US",
        location="United States",
        tech_proficiency=cfg["tech_proficiency"],
        interests=PersonaInterests(
            hobbies=cfg["hobbies"],
            professional_topics=cfg["professional_topics"],
            entertainment=cfg["entertainment"],
        ),
        work_style=PersonaWorkStyle(
            description="Balanced work style with regular hours",
            typical_tools=cfg["tools"],
            collaboration_style="hybrid",
            meeting_frequency="moderate",
        ),
        project_names=cfg["projects"] + ["General Tasks", "Admin", "Planning"],
        colleague_names=[
            "John Smith", "Emily Davis", "Michael Brown", "Jessica Wilson",
            "David Lee", "Amanda Garcia", "Chris Martinez", "Rachel Taylor",
            "Kevin Anderson", "Lisa Thomas",
        ],
        work_hours_start=cfg["work_hours"][0],
        work_hours_end=cfg["work_hours"][1],
        active_days=cfg["active_days"],
    )
