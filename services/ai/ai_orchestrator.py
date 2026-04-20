"""AI profile generation orchestrator.

Wraps PersonaGenerator + GeminiClient. No fallbacks — AI or error.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.persona_context import PersonaContext
from services.ai.gemini_client import GeminiClient
from services.ai.persona_generator import PersonaGenerator, PersonaGenerationError

logger = logging.getLogger(__name__)


@dataclass
class AIGenerationResult:
    success: bool
    persona: Optional[PersonaContext] = None
    profile_path: Optional[Path] = None
    generation_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)


@dataclass
class AIGenerationConfig:
    api_key: str
    model: str = "gemini-2.0-flash"
    temperature: float = 0.7
    output_dir: Path = Path("profiles/generated")


class AIOrchestrator:
    """Generate a PersonaContext via Gemini and persist it as YAML."""

    def __init__(self, config: AIGenerationConfig) -> None:
        self._cfg = config
        self._client = GeminiClient(
            api_key=config.api_key,
            model=config.model,
        )
        self._generator = PersonaGenerator(self._client)

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        output_dir: Optional[Path] = None,
    ) -> "AIOrchestrator":
        api_key = (
            config.get("ai", {}).get("gemini", {}).get("api_key")
            or __import__("os").environ.get("GEMINI_API_KEY", "")
        )
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. "
                "Export it or add it to config.yaml under ai.gemini.api_key."
            )
        model = config.get("ai", {}).get("gemini", {}).get("model", "gemini-2.0-flash")
        ai_cfg = AIGenerationConfig(
            api_key=api_key,
            model=model,
            output_dir=output_dir or Path("profiles/generated"),
        )
        return cls(ai_cfg)

    def generate_profile(
        self,
        occupation: str,
        location: Optional[str] = None,
        interests: Optional[List[str]] = None,
        age_range: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AIGenerationResult:
        hints_parts: List[str] = []
        if interests:
            hints_parts.append(f"interests: {', '.join(interests)}")
        if age_range:
            hints_parts.append(f"age range: {age_range}")
        hints = "; ".join(hints_parts) if hints_parts else "none provided"

        t0 = time.monotonic()
        try:
            persona = self._generator.generate(
                occupation=occupation,
                location=location or "United States",
                hints=hints,
                temperature=temperature,
            )
        except PersonaGenerationError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return AIGenerationResult(
                success=False,
                generation_time_ms=elapsed,
                errors=[str(exc)],
            )

        elapsed = (time.monotonic() - t0) * 1000

        profile_path = self._save(persona)

        return AIGenerationResult(
            success=True,
            persona=persona,
            profile_path=profile_path,
            generation_time_ms=elapsed,
        )

    def _save(self, persona: PersonaContext) -> Path:
        self._cfg.output_dir.mkdir(parents=True, exist_ok=True)
        slug = persona.username.replace(".", "_")
        path = self._cfg.output_dir / f"{slug}.yaml"
        data = persona.model_dump(mode="json")
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        logger.info("Saved persona to %s", path)
        return path


def generate_ai_profile(
    occupation: str,
    api_key: str,
    location: str = "United States",
    hints: str = "",
    output_dir: Path = Path("profiles/generated"),
) -> AIGenerationResult:
    cfg = AIGenerationConfig(api_key=api_key, output_dir=output_dir)
    orch = AIOrchestrator(cfg)
    return orch.generate_profile(occupation=occupation, location=location)
