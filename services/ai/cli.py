#!/usr/bin/env python3
"""CLI interface for AI-powered profile generation.

Provides commands for:
- Generating new personas from occupation/interests
- Previewing generated profiles
- Expanding seeds into massive artifact counts

Usage::

    # Generate a new AI profile
    python -m services.ai.cli generate --occupation "Software Engineer"
    
    # Generate with more details
    python -m services.ai.cli generate \\
        --occupation "Marketing Manager" \\
        --location "San Francisco" \\
        --interests "yoga" "travel" "sustainable living"
    
    # Preview without writing files
    python -m services.ai.cli preview --occupation "Data Scientist"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Load .env files if available
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).parent.parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
    load_dotenv(_PROJECT_ROOT / "services" / "ai" / ".env")
except ImportError:
    pass  # Silent fail for CLI

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration Loading
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = _PROJECT_ROOT / "config.yaml"
    
    if not config_path.exists():
        logging.warning("Config file not found: %s, using defaults", config_path)
        return _default_config()
    
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _default_config() -> Dict[str, Any]:
    """Return default configuration."""
    return {
        "ai": {
            "provider": "gemini",
            "gemini": {
                "api_key": os.environ.get("GEMINI_API_KEY"),
                "model": "gemini-2.0-flash",
                "temperature": 0.7,
            },
            "cache_responses": True,
            "cache_ttl_hours": 24,
            "fallback": {"enabled": True},
        },
        "artifact_scale": {
            "downloads": {"seeds": 20, "target": 1500},
            "documents": {"seeds": 30, "target": 4500},
            "media": {"seeds": 15, "pictures_target": 750, "videos_target": 240, "music_target": 500},
            "browsing": {"url_seeds": 50, "search_seeds": 30, "bookmark_seeds": 20, "history_target": 7500},
            "filenames": {"seeds": 15},
        },
        "timeline_days": 90,
        "profiles_dir": "profiles/generated",
    }


# ---------------------------------------------------------------------------
# Generate Command
# ---------------------------------------------------------------------------

def cmd_generate(args: argparse.Namespace) -> int:
    """Execute the generate command."""
    from services.ai.ai_orchestrator import AIOrchestrator, AIGenerationConfig
    
    config = load_config(args.config)
    
    # Override API key from env if not in config
    if args.api_key:
        config.setdefault("ai", {}).setdefault("gemini", {})["api_key"] = args.api_key
    elif os.environ.get("GEMINI_API_KEY"):
        config.setdefault("ai", {}).setdefault("gemini", {})["api_key"] = os.environ["GEMINI_API_KEY"]
    
    # Create orchestrator
    output_dir = args.output or Path(config.get("profiles_dir", "profiles/generated"))
    orchestrator = AIOrchestrator.from_config(
        config=config,
        output_dir=output_dir,
        random_seed=args.seed,
    )
    
    print(f"\n{'='*60}")
    print(" AI Profile Generator")
    print(f"{'='*60}")
    print(f" Occupation: {args.occupation}")
    if args.location:
        print(f" Location:   {args.location}")
    if args.interests:
        print(f" Interests:  {', '.join(args.interests)}")
    print(f"{'='*60}\n")
    
    # Generate profile
    print("Generating persona...")
    result = orchestrator.generate_profile(
        occupation=args.occupation,
        location=args.location,
        interests=args.interests,
        age_range=args.age_range,
        tech_level=args.tech_level,
        filename=args.filename,
    )
    
    if result.errors:
        print(f"\n⚠ Errors encountered:")
        for err in result.errors:
            print(f"  - {err}")
    
    if result.persona:
        print(f"\n✓ Generated persona: {result.persona.full_name}")
        print(f"  Username:     {result.persona.username}")
        print(f"  Organization: {result.persona.organization}")
        print(f"  Occupation:   {result.persona.occupation}")
        
        if result.profile_path:
            print(f"\n✓ Profile saved to: {result.profile_path}")
        
        if result.expanded_counts:
            print(f"\n  Seed counts:")
            for artifact_type, count in result.expanded_counts.items():
                print(f"    {artifact_type}: {count} seeds")
        
        print(f"\n  Generation time: {result.generation_time_ms:.1f}ms")
        
        if result.used_fallback:
            print(f"\n  ⚠ Used fallback generators (API unavailable)")
    
    print(f"\n{'='*60}")
    
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Preview Command
# ---------------------------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> int:
    """Execute the preview command (no file writes)."""
    from services.ai.ai_orchestrator import AIOrchestrator
    from services.ai.persona_generator import PersonaGenerator, create_fallback_persona
    from services.ai.gemini_client import GeminiClient
    
    config = load_config(args.config)
    
    # Override API key
    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    
    print(f"\n{'='*60}")
    print(" AI Profile Preview (Dry Run)")
    print(f"{'='*60}")
    print(f" Occupation: {args.occupation}")
    print(f"{'='*60}\n")
    
    # Generate persona only
    try:
        client = GeminiClient(api_key=api_key)
        gen = PersonaGenerator(client=client)
        persona = gen.generate(
            occupation=args.occupation,
            location=args.location,
            interests=args.interests or [],
        )
        used_fallback = False
    except Exception as e:
        print(f"⚠ API call failed: {e}")
        print("  Using fallback generator...\n")
        persona = create_fallback_persona(
            occupation=args.occupation,
            profile_type="developer" if "engineer" in args.occupation.lower() else "office_user",
        )
        used_fallback = True
    
    # Display persona details
    print("Generated Persona:")
    print(f"  Full Name:        {persona.full_name}")
    print(f"  Username:         {persona.username}")
    print(f"  Email:            {persona.email}")
    print(f"  Organization:     {persona.organization}")
    print(f"  Occupation:       {persona.occupation}")
    print(f"  Age Range:        {persona.age_range}")
    print(f"  Tech Proficiency: {persona.tech_proficiency.value}")
    
    print(f"\n  Interests:")
    print(f"    Hobbies:        {', '.join(persona.interests.hobbies)}")
    print(f"    Professional:   {', '.join(persona.interests.professional_topics)}")
    
    print(f"\n  Work Style:")
    print(f"    Description:    {persona.work_style.description}")
    print(f"    Tools:          {', '.join(persona.work_style.typical_tools)}")
    
    print(f"\n  Projects:         {', '.join(persona.project_names)}")
    print(f"  Colleagues:       {', '.join(persona.colleague_names)}")
    
    if used_fallback:
        print(f"\n  ⚠ Generated using fallback (no API)")
    
    print(f"\n{'='*60}")
    
    return 0


# ---------------------------------------------------------------------------
# Expand Command
# ---------------------------------------------------------------------------

def cmd_expand(args: argparse.Namespace) -> int:
    """Execute the expand command (expand seeds to artifacts)."""
    from services.generators.bulk_downloads import BulkDownloadsGenerator
    from services.generators.bulk_documents import BulkDocumentsGenerator
    from services.generators.bulk_media import BulkMediaGenerator
    from services.generators.bulk_browsing import BulkBrowsingGenerator
    from services.ai.ai_orchestrator import AIOrchestrator
    from services.ai.persona_generator import create_fallback_persona
    
    config = load_config(args.config)
    
    print(f"\n{'='*60}")
    print(" Artifact Expansion (Seed → Bulk)")
    print(f"{'='*60}\n")
    
    # Create a test persona for expansion demo
    persona = create_fallback_persona(
        occupation=args.occupation or "Software Engineer",
        profile_type="developer",
    )
    
    # Get seed counts from config
    scale_config = config.get("artifact_scale", {})
    
    # Create orchestrator and generate seeds
    output_dir = args.output or Path("profiles/generated")
    orchestrator = AIOrchestrator.from_config(config, output_dir=output_dir)
    
    print(f"Generating seeds for: {persona.occupation}...")
    seeds, _ = orchestrator.generate_seeds(persona)
    
    print(f"\n  Download seeds: {len(seeds.downloads)}")
    print(f"  Document seeds: {len(seeds.documents)}")
    print(f"  Browsing seeds: {len(seeds.browsing.url_patterns) if seeds.browsing else 0}")
    print(f"  Filename seeds: {len(seeds.filename_patterns)}")
    
    # Expand downloads
    if seeds.downloads:
        print(f"\nExpanding downloads...")
        dl_gen = BulkDownloadsGenerator(
            seed=42,
            timeline_days=config.get("timeline_days", 90),
            target_total=scale_config.get("downloads", {}).get("target", 1500),
        )
        expanded_downloads = dl_gen.expand_seeds(seeds.downloads, persona)
        print(f"  ✓ Expanded to {len(expanded_downloads)} downloads")
        
        if args.verbose:
            print(f"\n  Sample downloads:")
            for dl in expanded_downloads[:5]:
                print(f"    - {dl.filename}")
    
    # Expand documents
    if seeds.documents:
        print(f"\nExpanding documents...")
        doc_gen = BulkDocumentsGenerator(
            seed=42,
            timeline_days=config.get("timeline_days", 90),
            target_total=scale_config.get("documents", {}).get("target", 4500),
        )
        expanded_docs = doc_gen.expand_seeds(seeds.documents, persona)
        print(f"  ✓ Expanded to {len(expanded_docs)} documents")
        
        if args.verbose:
            print(f"\n  Sample documents:")
            for doc in expanded_docs[:5]:
                print(f"    - {doc.filename}")
    
    # Expand browsing history
    if seeds.browsing:
        print(f"\nExpanding browsing history...")
        browse_gen = BulkBrowsingGenerator(
            seed=42,
            timeline_days=config.get("timeline_days", 90),
            target_history=scale_config.get("browsing", {}).get("history_target", 7500),
            target_searches=scale_config.get("browsing", {}).get("search_target", 1500),
            target_bookmarks=scale_config.get("browsing", {}).get("bookmarks_target", 200),
        )
        history, searches, bookmarks = browse_gen.expand_seeds(seeds.browsing, persona)
        print(f"  ✓ Expanded to {len(history)} history entries")
        print(f"  ✓ Expanded to {len(searches)} search terms")
        print(f"  ✓ Expanded to {len(bookmarks)} bookmarks")
    
    print(f"\n{'='*60}")
    print(" Expansion Summary")
    print(f"{'='*60}")
    total = 0
    if seeds.downloads:
        total += len(expanded_downloads)
    if seeds.documents:
        total += len(expanded_docs)
    if seeds.browsing:
        total += len(history) + len(searches) + len(bookmarks)
    print(f" Total artifacts generated: {total:,}")
    print(f"{'='*60}\n")
    
    return 0


# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="ai-profile-gen",
        description="AI-powered profile and artifact generation",
        epilog="Use Gemini to create realistic, personalized VM profiles.",
    )
    
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        help="Path to config.yaml file",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Gemini API key (or set GEMINI_API_KEY env var)",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Generate command
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate a new AI-powered profile",
    )
    gen_parser.add_argument(
        "--occupation", "-o",
        type=str,
        required=True,
        help="Primary occupation/role (e.g., 'Software Engineer')",
    )
    gen_parser.add_argument(
        "--location", "-l",
        type=str,
        default=None,
        help="Location hint (e.g., 'Seattle')",
    )
    gen_parser.add_argument(
        "--interests", "-i",
        type=str,
        nargs="+",
        default=None,
        help="List of interests/hobbies",
    )
    gen_parser.add_argument(
        "--age-range",
        type=str,
        default=None,
        help="Age range (e.g., '25-35')",
    )
    gen_parser.add_argument(
        "--tech-level",
        type=str,
        choices=["low", "intermediate", "high"],
        default=None,
        help="Tech proficiency level",
    )
    gen_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for generated profile",
    )
    gen_parser.add_argument(
        "--filename",
        type=str,
        default=None,
        help="Override output filename (without .yaml)",
    )
    gen_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    
    # Preview command
    preview_parser = subparsers.add_parser(
        "preview",
        help="Preview a persona without writing files",
    )
    preview_parser.add_argument(
        "--occupation", "-o",
        type=str,
        required=True,
        help="Primary occupation/role",
    )
    preview_parser.add_argument(
        "--location", "-l",
        type=str,
        default=None,
        help="Location hint",
    )
    preview_parser.add_argument(
        "--interests", "-i",
        type=str,
        nargs="+",
        default=None,
        help="List of interests",
    )
    
    # Expand command
    expand_parser = subparsers.add_parser(
        "expand",
        help="Demonstrate seed expansion to bulk artifacts",
    )
    expand_parser.add_argument(
        "--occupation", "-o",
        type=str,
        default="Software Engineer",
        help="Test occupation for expansion demo",
    )
    expand_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory",
    )
    
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point."""
    args = parse_args()
    setup_logging(verbose=args.verbose)
    
    if args.command == "generate":
        return cmd_generate(args)
    elif args.command == "preview":
        return cmd_preview(args)
    elif args.command == "expand":
        return cmd_expand(args)
    else:
        print("Please specify a command: generate, preview, or expand")
        print("Use --help for more information.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
