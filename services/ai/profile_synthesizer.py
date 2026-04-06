"""Profile synthesizer that converts PersonaContext to valid YAML profiles.

Takes AI-generated personas and produces profile YAML files compatible with
the existing ProfileEngine and ProfileContext schema.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ruamel.yaml import YAML

from services.ai.schemas import PersonaContext, ProfileSeeds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProfileSynthesisError(Exception):
    """Raised when profile synthesis fails."""


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

class ProfileSynthesizer:
    """Converts PersonaContext to valid profile YAML files.
    
    Generates profile YAML that:
    1. Passes ProfileEngine validation
    2. Contains extended AI-generated metadata
    3. Includes seed expansion configuration
    
    Args:
        profiles_dir: Directory to write generated profiles.
        templates_dir: Optional directory with profile templates.
    
    Example:
        >>> synthesizer = ProfileSynthesizer(Path("profiles/generated"))
        >>> yaml_path = synthesizer.synthesize(persona, seeds)
        >>> print(f"Profile written to {yaml_path}")
    """
    
    def __init__(
        self,
        profiles_dir: Path,
        templates_dir: Optional[Path] = None,
    ) -> None:
        self._profiles_dir = profiles_dir
        self._templates_dir = templates_dir
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        
        self._yaml = YAML()
        self._yaml.default_flow_style = False
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
    
    def synthesize(
        self,
        persona: PersonaContext,
        seeds: Optional[ProfileSeeds] = None,
        profile_name: Optional[str] = None,
    ) -> Path:
        """Generate a complete profile YAML from persona and seeds.
        
        Args:
            persona: AI-generated persona context.
            seeds: Optional artifact seeds (if None, uses minimal profile).
            profile_name: Custom profile name (defaults to persona.username).
        
        Returns:
            Path to the written YAML file.
        
        Raises:
            ProfileSynthesisError: If synthesis or write fails.
        """
        name = profile_name or persona.username.replace(".", "_")
        
        try:
            profile_data = self._build_profile_data(persona, seeds)
            
            # Write to file
            output_path = self._profiles_dir / f"{name}.yaml"
            with open(output_path, "w", encoding="utf-8") as f:
                self._yaml.dump(profile_data, f)
            
            logger.info("Synthesized profile: %s → %s", persona.full_name, output_path)
            return output_path
            
        except Exception as e:
            raise ProfileSynthesisError(f"Failed to synthesize profile: {e}") from e
    
    def _build_profile_data(
        self,
        persona: PersonaContext,
        seeds: Optional[ProfileSeeds],
    ) -> Dict[str, Any]:
        """Build the profile data dictionary.
        
        Args:
            persona: PersonaContext instance.
            seeds: Optional ProfileSeeds instance.
        
        Returns:
            Dictionary ready for YAML serialization.
        """
        # Determine profile type from persona
        profile_type = self._infer_profile_type(persona)
        
        # Base profile structure (compatible with ProfileEngine)
        profile = {
            # Inherit from appropriate base
            "extends": profile_type,
            
            # Core identity
            "username": persona.username,
            "organization": persona.organization,
            "locale": persona.locale,
            
            # Installed apps based on persona tools
            "installed_apps": self._derive_installed_apps(persona),
            
            # Browsing configuration
            "browsing": {
                "categories": self._derive_browsing_categories(persona),
                "daily_avg_sites": self._estimate_daily_sites(persona),
            },
            
            # Work hours
            "work_hours": {
                "start": persona.work_hours_start,
                "end": persona.work_hours_end,
                "active_days": persona.active_days,
            },
            
            # Extended AI-generated metadata (not validated by ProfileContext)
            "_ai_metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "full_name": persona.full_name,
                "email": persona.email,
                "occupation": persona.occupation,
                "department": persona.department,
                "age_range": persona.age_range,
                "location": persona.location,
                "tech_proficiency": persona.tech_proficiency.value,
                "project_names": persona.project_names,
                "colleague_names": persona.colleague_names,
            },
            
            # Persona interests for artifact generation
            "_persona_interests": {
                "hobbies": persona.interests.hobbies,
                "professional_topics": persona.interests.professional_topics,
                "entertainment": persona.interests.entertainment,
            },
            
            # Work style for realistic patterns
            "_work_style": {
                "description": persona.work_style.description,
                "typical_tools": persona.work_style.typical_tools,
                "collaboration_style": persona.work_style.collaboration_style,
                "meeting_frequency": persona.work_style.meeting_frequency,
            },
        }
        
        # Add seed data if provided
        if seeds:
            profile["_seeds"] = self._serialize_seeds(seeds)
        
        return profile
    
    def _infer_profile_type(self, persona: PersonaContext) -> str:
        """Infer base profile type from persona characteristics.
        
        Returns:
            One of: 'developer', 'office_user', 'home_user'
        """
        occupation_lower = persona.occupation.lower()
        tools_lower = [t.lower() for t in persona.work_style.typical_tools]
        
        # Developer indicators
        dev_keywords = {"developer", "engineer", "programmer", "devops", "sre", "architect"}
        dev_tools = {"vscode", "docker", "git", "terminal", "intellij", "vim", "emacs"}
        
        if any(kw in occupation_lower for kw in dev_keywords):
            return "developer"
        if any(tool in dev_tools for tool in tools_lower):
            return "developer"
        
        # Home user indicators
        home_keywords = {"personal", "retired", "student", "homemaker", "freelance"}
        if persona.organization.lower() == "personal":
            return "home_user"
        if any(kw in occupation_lower for kw in home_keywords):
            return "home_user"
        
        # Default to office user
        return "office_user"
    
    def _derive_installed_apps(self, persona: PersonaContext) -> list[str]:
        """Derive installed apps list from persona tools and interests."""
        apps = []
        
        # Add tools
        tool_to_app = {
            "vs code": "vscode",
            "vscode": "vscode",
            "visual studio code": "vscode",
            "docker": "docker",
            "git": "git",
            "terminal": "terminal",
            "slack": "slack",
            "zoom": "zoom",
            "teams": "teams",
            "microsoft teams": "teams",
            "outlook": "outlook",
            "excel": "excel",
            "word": "word",
            "powerpoint": "powerpoint",
            "chrome": "chrome",
            "firefox": "firefox",
            "edge": "edge",
            "spotify": "spotify",
            "discord": "discord",
            "steam": "steam",
            "adobe": "adobe_creative_cloud",
            "photoshop": "adobe_creative_cloud",
            "figma": "figma",
            "postman": "postman",
        }
        
        for tool in persona.work_style.typical_tools:
            tool_lower = tool.lower()
            for key, app in tool_to_app.items():
                if key in tool_lower and app not in apps:
                    apps.append(app)
                    break
        
        # Add hobby-based apps
        hobby_apps = {
            "photography": ["lightroom", "photos"],
            "music": ["spotify", "vlc"],
            "gaming": ["steam", "discord"],
            "video": ["vlc", "obs"],
            "coding": ["vscode", "git"],
        }
        
        for hobby in persona.interests.hobbies:
            hobby_lower = hobby.lower()
            for key, hobby_app_list in hobby_apps.items():
                if key in hobby_lower:
                    for app in hobby_app_list:
                        if app not in apps:
                            apps.append(app)
        
        # Ensure minimum apps
        if len(apps) < 3:
            defaults = ["chrome", "notepad", "calculator"]
            for d in defaults:
                if d not in apps:
                    apps.append(d)
        
        return apps[:15]  # Limit to reasonable number
    
    def _derive_browsing_categories(self, persona: PersonaContext) -> list[str]:
        """Derive browsing categories from persona interests and occupation."""
        categories = []
        
        occupation_lower = persona.occupation.lower()
        
        # Occupation-based categories
        if any(x in occupation_lower for x in ["developer", "engineer", "programmer"]):
            categories.extend(["stackoverflow", "github", "documentation"])
        elif any(x in occupation_lower for x in ["marketing", "sales", "business"]):
            categories.extend(["business", "news"])
        
        # Interest-based categories
        interest_to_category = {
            "gaming": "entertainment",
            "music": "entertainment",
            "movies": "entertainment",
            "travel": "shopping",
            "cooking": "general",
            "finance": "business",
            "investing": "business",
            "social": "social_media",
            "news": "news",
            "tech": "news",
        }
        
        for hobby in persona.interests.hobbies:
            hobby_lower = hobby.lower()
            for key, cat in interest_to_category.items():
                if key in hobby_lower and cat not in categories:
                    categories.append(cat)
        
        # Ensure minimum categories
        if len(categories) < 2:
            categories.extend(["general", "news"])
        
        return list(set(categories))[:8]
    
    def _estimate_daily_sites(self, persona: PersonaContext) -> int:
        """Estimate daily average sites visited based on persona."""
        base = 10
        
        # Tech-savvy users browse more
        if persona.tech_proficiency.value == "high":
            base += 15
        elif persona.tech_proficiency.value == "intermediate":
            base += 5
        
        # More interests = more browsing
        base += len(persona.interests.hobbies) * 2
        
        # Work style affects browsing
        if persona.work_style.collaboration_style == "collaborative":
            base += 5  # More communication tools
        
        return min(base, 50)  # Cap at 50
    
    def _serialize_seeds(self, seeds: ProfileSeeds) -> Dict[str, Any]:
        """Serialize ProfileSeeds for YAML storage."""
        return {
            "total_seed_count": seeds.total_seed_count(),
            "generated_at": seeds.generated_at.isoformat(),
            "gemini_model": seeds.gemini_model,
            "download_count": len(seeds.downloads),
            "document_count": len(seeds.documents),
            "media_count": len(seeds.media),
            # Full seed data stored separately or computed on demand
        }
    
    def to_dict(
        self,
        persona: PersonaContext,
        seeds: Optional[ProfileSeeds] = None,
    ) -> Dict[str, Any]:
        """Return profile data as dictionary without writing to disk.
        
        Useful for validation or preview before saving.
        """
        return self._build_profile_data(persona, seeds)
