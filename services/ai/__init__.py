"""AI-powered profile and artifact generation services.

This package provides Gemini-based persona generation and artifact seeding,
combined with local permutation engines for massive-scale artifact creation.

Architecture:
    Tier 1 (Gemini): Generate 10-50 personalized "seeds" per artifact type
    Tier 2 (Local):  Expand seeds into 1000s of unique artifacts via permutation

Usage:
    # Generate a complete profile
    from services.ai import AIOrchestrator
    
    orchestrator = AIOrchestrator.from_config(config)
    result = orchestrator.generate_profile(occupation="Software Engineer")
    print(f"Profile saved to: {result.profile_path}")

CLI:
    # Generate from command line
    python -m services.ai.cli generate --occupation "Marketing Manager"
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
from services.ai.persona_generator import PersonaGenerator, create_fallback_persona
from services.ai.profile_synthesizer import ProfileSynthesizer
from services.ai.ai_orchestrator import (
    AIOrchestrator,
    AIGenerationConfig,
    AIGenerationResult,
    generate_ai_profile,
)

__all__ = [
    # Core orchestrator
    "AIOrchestrator",
    "AIGenerationConfig",
    "AIGenerationResult",
    "generate_ai_profile",
    
    # Generators
    "GeminiClient",
    "PersonaGenerator",
    "ProfileSynthesizer",
    "create_fallback_persona",
    
    # Schemas
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
