"""AI Orchestrator for coordinating AI-powered profile and artifact generation.

This module ties together:
1. PersonaGenerator - Creates detailed user personas from minimal input
2. ProfileSynthesizer - Converts personas to YAML profiles  
3. Seed Generators - Generate AI seeds for each artifact type
4. Bulk Generators - Expand seeds into thousands of unique artifacts

The AIOrchestrator is the main entry point for AI-driven generation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from services.ai.gemini_client import GeminiClient
from services.ai.persona_generator import PersonaGenerator, create_fallback_persona
from services.ai.profile_synthesizer import ProfileSynthesizer
from services.ai.schemas import (
    PersonaContext,
    ProfileSeeds,
    DownloadSeed,
    DocumentSeed,
    BrowsingSeed,
    MediaSeed,
    FilenameSeed,
)
from services.ai.seed_generators.browsing import BrowsingSeedGenerator
from services.ai.seed_generators.documents import DocumentSeedGenerator
from services.ai.seed_generators.downloads import DownloadSeedGenerator
from services.ai.seed_generators.filenames import FilenameSeedGenerator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AIGenerationConfig:
    """Configuration for AI-powered generation."""
    
    # Gemini settings
    api_key: Optional[str] = None
    model: str = "gemini-2.0-flash"
    temperature: float = 0.7
    
    # Generation settings
    seed_count_downloads: int = 20
    seed_count_documents: int = 30
    seed_count_browsing_urls: int = 50
    seed_count_browsing_search: int = 30
    seed_count_browsing_bookmarks: int = 20
    seed_count_media: int = 15
    seed_count_filenames: int = 15
    
    # Scale targets (for expansion)
    target_downloads: int = 1500
    target_documents: int = 4500
    target_pictures: int = 750
    target_videos: int = 240
    target_music: int = 500
    target_history_entries: int = 7500
    target_search_terms: int = 1500
    target_bookmarks: int = 200
    
    # Timeline
    timeline_days: int = 90
    
    # Cache settings
    cache_enabled: bool = True
    cache_dir: Optional[Path] = None
    cache_ttl_hours: int = 24
    
    # Fallback behavior
    fallback_enabled: bool = True
    
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "AIGenerationConfig":
        """Create from main config.yaml dictionary with environment variable overrides."""
        import os
        ai_config = config.get("ai", {})
        gemini_config = ai_config.get("gemini", {})
        scale_config = config.get("artifact_scale", {})

        downloads_cfg = scale_config.get("downloads", {})
        documents_cfg = scale_config.get("documents", {})
        media_cfg = scale_config.get("media", {})
        pictures_cfg = scale_config.get("pictures", {})
        videos_cfg = scale_config.get("videos", {})
        music_cfg = scale_config.get("music", {})
        browsing_cfg = scale_config.get("browsing", {})
        browser_history_cfg = scale_config.get("browser_history", {})
        search_terms_cfg = scale_config.get("search_terms", {})
        bookmarks_cfg = scale_config.get("bookmarks", {})
        filenames_cfg = scale_config.get("filenames", {})

        cache_cfg = ai_config.get("cache", {})
        cache_enabled = ai_config.get("cache_responses", cache_cfg.get("enabled", True))
        cache_dir_value = ai_config.get("cache_dir", cache_cfg.get("directory", ".ai_cache"))
        cache_ttl_hours = ai_config.get("cache_ttl_hours", cache_cfg.get("ttl_hours", 24))
        
        # Priority: Environment Var > config.yaml > Default
        api_key = os.environ.get("GEMINI_API_KEY") or gemini_config.get("api_key")
        model = os.environ.get("GEMINI_MODEL") or gemini_config.get("model", "gemini-2.0-flash")
        
        # Log which model is being used
        logger.info("Initializing AI generation with model: %s", model)
        
        try:
            temp_env = os.environ.get("GEMINI_TEMPERATURE")
            temperature = float(temp_env) if temp_env else gemini_config.get("temperature", 0.7)
        except ValueError:
            temperature = gemini_config.get("temperature", 0.7)

        return cls(
            api_key=api_key,
            model=model,
            temperature=temperature,
            seed_count_downloads=downloads_cfg.get("seeds", 20),
            seed_count_documents=documents_cfg.get("seeds", 30),
            seed_count_browsing_urls=browsing_cfg.get("url_seeds", browser_history_cfg.get("seeds", 50)),
            seed_count_browsing_search=browsing_cfg.get("search_seeds", search_terms_cfg.get("seeds", 30)),
            seed_count_browsing_bookmarks=browsing_cfg.get("bookmark_seeds", bookmarks_cfg.get("seeds", 20)),
            seed_count_media=media_cfg.get("seeds", 15),
            seed_count_filenames=filenames_cfg.get("seeds", 15),
            target_downloads=downloads_cfg.get("target", downloads_cfg.get("target_total", 1500)),
            target_documents=documents_cfg.get("target", documents_cfg.get("target_total", 4500)),
            target_pictures=media_cfg.get("pictures_target", pictures_cfg.get("target_total", 750)),
            target_videos=media_cfg.get("videos_target", videos_cfg.get("target_total", 240)),
            target_music=media_cfg.get("music_target", music_cfg.get("target_total", 500)),
            target_history_entries=browsing_cfg.get("history_target", browser_history_cfg.get("target_total", 7500)),
            target_search_terms=browsing_cfg.get("search_target", search_terms_cfg.get("target_total", 1500)),
            target_bookmarks=browsing_cfg.get("bookmarks_target", bookmarks_cfg.get("target_total", 200)),
            timeline_days=config.get("timeline_days", 90),
            cache_enabled=cache_enabled,
            cache_dir=Path(cache_dir_value) if cache_enabled and cache_dir_value else None,
            cache_ttl_hours=cache_ttl_hours,
            fallback_enabled=ai_config.get("fallback", {}).get("enabled", True),
        )


# ---------------------------------------------------------------------------
# Generation Result
# ---------------------------------------------------------------------------

@dataclass
class AIGenerationResult:
    """Result of AI-powered generation."""
    
    persona: PersonaContext
    profile_path: Optional[Path] = None
    seeds: Optional[ProfileSeeds] = None
    expanded_counts: Dict[str, int] = field(default_factory=dict)
    used_fallback: bool = False
    generation_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        """Check if generation was successful."""
        return self.persona is not None and len(self.errors) == 0


# ---------------------------------------------------------------------------
# AI Orchestrator
# ---------------------------------------------------------------------------

class AIOrchestrator:
    """Coordinates AI-powered profile and artifact generation.
    
    This is the main entry point for generating personalized profiles
    and massive-scale artifacts using the two-tier architecture:
    
    Tier 1: Gemini API generates 10-50 "seeds" per artifact type
    Tier 2: Local permutation engines expand seeds into 1000s of artifacts
    
    Example:
        >>> orchestrator = AIOrchestrator.from_config(config)
        >>> result = orchestrator.generate_profile(
        ...     occupation="Software Engineer",
        ...     interests=["gaming", "open source"],
        ... )
        >>> print(f"Generated persona: {result.persona.full_name}")
        >>> print(f"Profile saved to: {result.profile_path}")
    """
    
    def __init__(
        self,
        config: AIGenerationConfig,
        output_dir: Path,
        random_seed: Optional[int] = None,
    ) -> None:
        """Initialize AI orchestrator.
        
        Args:
            config: AI generation configuration.
            output_dir: Directory for generated profiles.
            random_seed: Optional seed for reproducibility.
        """
        self._config = config
        self._output_dir = Path(output_dir)
        self._random_seed = random_seed
        
        # Initialize Gemini client
        self._client = GeminiClient(
            api_key=config.api_key,
            model=config.model,
            temperature=config.temperature,
            cache_dir=config.cache_dir,
            cache_ttl_hours=config.cache_ttl_hours,
        )
        
        # Initialize generators
        self._persona_gen = PersonaGenerator(
            client=self._client,
        )
        self._profile_synth = ProfileSynthesizer(
            profiles_dir=self._output_dir,
        )
        self._random_seed = random_seed  # Store for later use
        
        logger.debug(
            "AIOrchestrator initialized with model=%s, output=%s",
            config.model,
            output_dir,
        )
    
    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        output_dir: Optional[Path] = None,
        random_seed: Optional[int] = None,
    ) -> "AIOrchestrator":
        """Create orchestrator from main config dictionary.
        
        Args:
            config: Main configuration dictionary (from config.yaml).
            output_dir: Override output directory.
            random_seed: Optional seed for reproducibility.
            
        Returns:
            Configured AIOrchestrator instance.
        """
        ai_config = AIGenerationConfig.from_config(config)
        out = output_dir or Path(config.get("profiles_dir", "profiles/generated"))
        return cls(config=ai_config, output_dir=out, random_seed=random_seed)
    
    def generate_persona(
        self,
        occupation: str,
        location: Optional[str] = None,
        interests: Optional[List[str]] = None,
        age_range: Optional[str] = None,
        tech_level: Optional[str] = None,
    ) -> Tuple[PersonaContext, bool]:
        """Generate a detailed persona from minimal inputs.
        
        Args:
            occupation: Primary job/role (e.g., "Marketing Manager").
            location: Optional location hint (e.g., "Seattle").
            interests: Optional list of interests/hobbies.
            age_range: Optional age range (e.g., "25-35").
            tech_level: Optional tech proficiency (low/intermediate/high).
            
        Returns:
            Tuple of (PersonaContext, used_fallback).
        """
        try:
            # Build hints from interests, age_range, tech_level
            hints_parts = []
            if interests:
                hints_parts.append(f"interests: {', '.join(interests)}")
            if age_range:
                hints_parts.append(f"age range: {age_range}")
            if tech_level:
                hints_parts.append(f"tech level: {tech_level}")
            hints = "; ".join(hints_parts) if hints_parts else ""
            
            persona = self._persona_gen.generate(
                occupation=occupation,
                location=location or "United States",
                hints=hints,
            )
            return persona, False
            
        except Exception as e:
            logger.warning("Gemini persona generation failed: %s", e)
            
            if not self._config.fallback_enabled:
                raise
            
            # Determine profile type from occupation
            profile_type = self._infer_profile_type(occupation)
            
            # Fallback doesn't support location parameter
            persona = create_fallback_persona(
                occupation=occupation,
                profile_type=profile_type,
            )
            return persona, True
    
    def generate_seeds(
        self,
        persona: PersonaContext,
    ) -> Tuple[ProfileSeeds, bool]:
        """Generate artifact seeds for a persona.
        
        Args:
            persona: The persona context to generate seeds for.
            
        Returns:
            Tuple of (ProfileSeeds, used_fallback).
        """
        used_fallback = False
        downloads: List[DownloadSeed] = []
        documents: List[DocumentSeed] = []
        browsing: Optional[BrowsingSeed] = None
        filename_patterns: List[FilenameSeed] = []
        media: List[MediaSeed] = []
        
        # Generate each seed type
        dl_gen: Optional[DownloadSeedGenerator] = None
        try:
            dl_gen = DownloadSeedGenerator(
                client=self._client,
                seed_count=self._config.seed_count_downloads,
                total_target=self._config.target_downloads,
            )
            downloads = dl_gen.generate(persona)
        except Exception as e:
            logger.warning("Download seed generation failed: %s", e)
            if self._config.fallback_enabled:
                if dl_gen is not None:
                    downloads = dl_gen._generate_fallback_seeds(persona)
                else:
                    logger.warning("Download generator unavailable; using empty download seeds")
                used_fallback = True
            
        doc_gen: Optional[DocumentSeedGenerator] = None
        try:
            doc_gen = DocumentSeedGenerator(
                client=self._client,
                seed_count=self._config.seed_count_documents,
                total_target=self._config.target_documents,
            )
            documents = doc_gen.generate(persona)
        except Exception as e:
            logger.warning("Document seed generation failed: %s", e)
            if self._config.fallback_enabled:
                if doc_gen is not None:
                    documents = doc_gen._generate_fallback_seeds(persona)
                else:
                    logger.warning("Document generator unavailable; using empty document seeds")
                used_fallback = True
                
        browse_gen: Optional[BrowsingSeedGenerator] = None
        try:
            browse_gen = BrowsingSeedGenerator(
                client=self._client,
                total_target=self._config.target_history_entries,
            )
            browsing = browse_gen.generate(persona)
        except Exception as e:
            logger.warning("Browsing seed generation failed: %s", e)
            if self._config.fallback_enabled:
                if browse_gen is not None:
                    browsing = browse_gen._generate_fallback_seed(persona)
                else:
                    logger.warning("Browsing generator unavailable; using no browsing seeds")
                used_fallback = True
        
        fname_gen: Optional[FilenameSeedGenerator] = None
        try:
            fname_gen = FilenameSeedGenerator(
                client=self._client,
                seed_count=self._config.seed_count_filenames,
            )
            filename_patterns = fname_gen.generate(persona)
        except Exception as e:
            logger.warning("Filename seed generation failed: %s", e)
            if self._config.fallback_enabled:
                if fname_gen is not None:
                    filename_patterns = fname_gen._generate_fallback_seeds(persona)
                else:
                    logger.warning("Filename generator unavailable; using empty filename seeds")
                used_fallback = True
        
        seeds = ProfileSeeds(
            persona=persona,
            downloads=downloads,
            documents=documents,
            browsing=browsing,
            media=media,
            filename_patterns=filename_patterns,
            gemini_model=self._config.model,
        )
        
        return seeds, used_fallback
    
    def generate_profile_yaml(
        self,
        persona: PersonaContext,
        seeds: ProfileSeeds,
        filename: Optional[str] = None,
    ) -> Path:
        """Generate a YAML profile file from persona and seeds.
        
        Args:
            persona: The persona context.
            seeds: Generated artifact seeds.
            filename: Optional override filename (without extension).
            
        Returns:
            Path to the generated YAML file.
        """
        # ProfileSynthesizer.synthesize() already writes the file and returns the path
        fname = filename or persona.username.replace(".", "_")
        profile_path = self._profile_synth.synthesize(persona, seeds, profile_name=fname)
        
        logger.info("Generated profile YAML: %s", profile_path)
        return profile_path
    
    def generate_profile(
        self,
        occupation: str,
        location: Optional[str] = None,
        interests: Optional[List[str]] = None,
        age_range: Optional[str] = None,
        tech_level: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> AIGenerationResult:
        """Complete profile generation pipeline.
        
        This is the main entry point that:
        1. Generates a persona
        2. Creates artifact seeds
        3. Produces a YAML profile file
        
        Args:
            occupation: Primary job/role.
            location: Optional location hint.
            interests: Optional list of interests.
            age_range: Optional age range.
            tech_level: Optional tech proficiency.
            filename: Optional output filename.
            
        Returns:
            AIGenerationResult with all outputs.
        """
        import time
        start = time.time()
        
        errors: List[str] = []
        used_fallback = False
        
        # Step 1: Generate persona
        try:
            persona, persona_fallback = self.generate_persona(
                occupation=occupation,
                location=location,
                interests=interests,
                age_range=age_range,
                tech_level=tech_level,
            )
            used_fallback = used_fallback or persona_fallback
        except Exception as e:
            logger.error("Persona generation failed completely: %s", e)
            errors.append(f"Persona generation: {e}")
            return AIGenerationResult(
                persona=None,  # type: ignore
                errors=errors,
                generation_time_ms=(time.time() - start) * 1000,
            )
        
        # Step 2: Generate seeds
        try:
            seeds, seeds_fallback = self.generate_seeds(persona)
            used_fallback = used_fallback or seeds_fallback
        except Exception as e:
            logger.error("Seed generation failed completely: %s", e)
            errors.append(f"Seed generation: {e}")
            seeds = None
        
        # Step 3: Generate profile YAML
        profile_path: Optional[Path] = None
        if seeds:
            try:
                profile_path = self.generate_profile_yaml(persona, seeds, filename)
            except Exception as e:
                logger.error("Profile YAML generation failed: %s", e)
                errors.append(f"Profile YAML: {e}")
        
        # Calculate artifact counts
        expanded_counts = {}
        if seeds:
            expanded_counts = {
                "downloads": len(seeds.downloads) if seeds.downloads else 0,
                "documents": len(seeds.documents) if seeds.documents else 0,
                "browsing": len(seeds.browsing.url_patterns) if seeds.browsing else 0,
                "media": len(seeds.media) if seeds.media else 0,
                "filenames": len(seeds.filename_patterns) if seeds.filename_patterns else 0,
            }
        
        elapsed_ms = (time.time() - start) * 1000
        
        return AIGenerationResult(
            persona=persona,
            profile_path=profile_path,
            seeds=seeds,
            expanded_counts=expanded_counts,
            used_fallback=used_fallback,
            generation_time_ms=elapsed_ms,
            errors=errors,
        )
    
    def _infer_profile_type(self, occupation: str) -> str:
        """Infer profile type from occupation string."""
        occupation_lower = occupation.lower()
        
        dev_keywords = [
            "developer", "engineer", "programmer", "coder",
            "devops", "sre", "architect", "data scientist",
        ]
        office_keywords = [
            "manager", "analyst", "accountant", "executive",
            "hr", "marketing", "sales", "consultant",
            "coordinator", "administrator",
        ]
        
        for kw in dev_keywords:
            if kw in occupation_lower:
                return "developer"
        
        for kw in office_keywords:
            if kw in occupation_lower:
                return "office_user"
        
        return "home_user"


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def generate_ai_profile(
    config: Dict[str, Any],
    occupation: str,
    **kwargs: Any,
) -> AIGenerationResult:
    """Convenience function to generate a profile.
    
    Args:
        config: Main configuration dictionary.
        occupation: Primary occupation/role.
        **kwargs: Additional persona parameters.
        
    Returns:
        AIGenerationResult with all outputs.
    """
    orchestrator = AIOrchestrator.from_config(config)
    return orchestrator.generate_profile(occupation=occupation, **kwargs)
