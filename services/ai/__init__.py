"""AI-powered persona and artifact seed generation.

Gemini generates a complete PersonaContext from minimal user input.
Seed generators expand that context into typed artifact seeds consumed
by the expansion pipeline (Phase 2).
"""

__all__ = [
    "GeminiClient",
    "PersonaGenerator",
    "PersonaGenerationError",
]


def __getattr__(name):
    if name == "GeminiClient":
        from services.ai.gemini_client import GeminiClient
        return GeminiClient
    if name in ("PersonaGenerator", "PersonaGenerationError"):
        from services.ai.persona_generator import PersonaGenerator, PersonaGenerationError
        return PersonaGenerator if name == "PersonaGenerator" else PersonaGenerationError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
