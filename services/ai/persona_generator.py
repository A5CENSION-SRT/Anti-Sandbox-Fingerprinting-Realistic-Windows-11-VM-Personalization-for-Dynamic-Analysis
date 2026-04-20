"""Persona generator using Gemini API.

Generates detailed, coherent user personas from minimal inputs (occupation, hints).
The persona serves as the foundation for all artifact seed generation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.persona_context import PersonaContext
from services.ai.gemini_client import GeminiClient, GeminiClientError

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


