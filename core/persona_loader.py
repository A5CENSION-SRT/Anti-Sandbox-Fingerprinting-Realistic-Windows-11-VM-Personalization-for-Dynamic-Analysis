"""Load PersonaContext from an AI-generated YAML file.

No preset fallbacks — every run requires a Gemini-generated profile.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.persona_context import PersonaContext


class PersonaLoaderError(Exception):
    """Raised when a persona cannot be loaded or validated."""


def load_yaml(path: Path) -> PersonaContext:
    """Load a PersonaContext from a YAML file produced by AIOrchestrator.

    Raises:
        PersonaLoaderError: If the file is missing, unparseable, or fails
            Pydantic validation.
    """
    path = Path(path)
    if not path.is_file():
        raise PersonaLoaderError(f"Persona file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PersonaLoaderError(f"Failed to parse {path}: {exc}") from exc
    try:
        return PersonaContext.model_validate(raw)
    except Exception as exc:
        raise PersonaLoaderError(
            f"Persona validation failed for {path}: {exc}"
        ) from exc
