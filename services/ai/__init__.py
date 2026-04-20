"""AI-powered persona and artifact seed generation.

Tier 1 (Gemini): generate a complete PersonaContext from minimal user input.
Tier 2 (local):  expand seeds into thousands of unique artifacts (Phase 2).

Usage:
    from services.ai import AIOrchestrator
    orch = AIOrchestrator.from_config(config, output_dir=Path("profiles/generated"))
    result = orch.generate_profile(occupation="Software Engineer", location="Austin")
    print(result.profile_path)
"""

from services.ai.gemini_client import GeminiClient
from services.ai.schemas import (
    PersonaContext,
    PersonaInterests,
    PersonaWorkStyle,
    TechProficiency,
    ArtifactSeed,
    DownloadSeed,
    DocumentSeed,
    BrowsingSeed,
    MediaSeed,
    FilenameSeed,
    ExpansionRule,
    ProfileSeeds,
)
from services.ai.persona_generator import PersonaGenerator, PersonaGenerationError
from services.ai.ai_orchestrator import (
    AIOrchestrator,
    AIGenerationConfig,
    AIGenerationResult,
    generate_ai_profile,
)

__all__ = [
    "AIOrchestrator",
    "AIGenerationConfig",
    "AIGenerationResult",
    "generate_ai_profile",
    "GeminiClient",
    "PersonaGenerator",
    "PersonaGenerationError",
    "PersonaContext",
    "PersonaInterests",
    "PersonaWorkStyle",
    "TechProficiency",
    "ArtifactSeed",
    "DownloadSeed",
    "DocumentSeed",
    "BrowsingSeed",
    "MediaSeed",
    "FilenameSeed",
    "ExpansionRule",
    "ProfileSeeds",
]
