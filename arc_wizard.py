#!/usr/bin/env python3
"""ARC Interactive Wizard - menu-driven CLI for VM personalization."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent / "services" / "ai" / ".env")
    if not os.environ.get("GEMINI_API_KEY"):
        print("WARNING: GEMINI_API_KEY not found in .env files.")
        print("  Set it in .env or export GEMINI_API_KEY=<key>")
except ImportError:
    print("WARNING: python-dotenv not installed. Relying on environment variables.")
    print("  Install: pip install python-dotenv")

_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Console utilities
# ---------------------------------------------------------------------------

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def print_section(title: str) -> None:
    print(f"\n{'-' * 70}")
    print(f"  {title}")
    print(f"{'-' * 70}\n")


def get_choice(prompt: str, options: List[str], default: Optional[int] = None) -> int:
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        suffix = f" [{default}]" if default else ""
        choice = input(f"\nEnter choice (1-{len(options)}){suffix}: ").strip()
        if not choice and default:
            return default - 1
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        print(f"  Invalid choice. Enter a number between 1 and {len(options)}.")


def get_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        resp = input(f"{prompt} ({suffix}): ").strip().lower()
        if not resp:
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("  Enter y or n.")


def get_input(
    prompt: str,
    default: Optional[str] = None,
    required: bool = False,
) -> Optional[str]:
    if default:
        prompt_str = f"{prompt} [{default}]: "
    elif required:
        prompt_str = f"{prompt} (required): "
    else:
        prompt_str = f"{prompt} (optional, ENTER to skip): "
    while True:
        value = input(prompt_str).strip()
        if not value and default:
            return default
        if not value and not required:
            return None
        if value:
            return value
        if required:
            print("  This field is required.")


# ---------------------------------------------------------------------------
# Drive utilities
# ---------------------------------------------------------------------------

def is_mounted(path: str) -> bool:
    return Path(path).exists() and Path(path).is_mount()


def umount(path: str) -> bool:
    result = subprocess.run(
        ["sudo", "umount", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        print(f"Unmounted {path}.")
        return True
    print(f"Unmount failed: {result.stderr.strip()}")
    return False


# ---------------------------------------------------------------------------
# AI generation
# ---------------------------------------------------------------------------

def run_ai_generation(
    config: Dict[str, Any],
    occupation: str,
    location: Optional[str] = None,
    interests: Optional[List[str]] = None,
    age_range: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Call AIOrchestrator to generate and save a persona. Returns YAML path or None."""
    try:
        from services.ai import AIOrchestrator

        print_section("AI Persona Generation")

        orchestrator = AIOrchestrator.from_config(
            config=config,
            output_dir=output_dir or Path("profiles/generated"),
        )

        print(f"  Occupation : {occupation}")
        if location:
            print(f"  Location   : {location}")
        if interests:
            print(f"  Interests  : {', '.join(interests)}")
        if age_range:
            print(f"  Age range  : {age_range}")

        print("\nGenerating persona via Gemini...")

        result = orchestrator.generate_profile(
            occupation=occupation,
            location=location,
            interests=interests,
            age_range=age_range,
        )

        if result.success and result.persona:
            p = result.persona
            print(f"\n  Generated  : {p.full_name}")
            print(f"  Username   : {p.username}")
            print(f"  Org        : {p.organization}")
            print(f"  Email      : {p.email}")
            print(f"  Archetype  : {p.profile_archetype}")
            if result.profile_path:
                print(f"  Saved to   : {result.profile_path}")
            print(f"  Time       : {result.generation_time_ms:.1f}ms")
            return result.profile_path

        print("\nAI generation failed:")
        for err in result.errors:
            print(f"  - {err}")
        return None

    except RuntimeError as exc:
        print(f"\nConfiguration error: {exc}")
        return None
    except Exception as exc:
        print(f"\nAI generation error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Artifact generation
# ---------------------------------------------------------------------------

def run_artifact_generation(
    config: Dict[str, Any],
    profile_path: Optional[Path],
    mount_path: Optional[Path],
    dry_run: bool = False,
) -> bool:
    from core.audit_logger import AuditLogger
    from core.orchestrator import Orchestrator
    from main import register_services

    print_section("Artifact Generation")

    run_config = dict(config)

    if profile_path:
        resolved = Path(profile_path).resolve()
        if not resolved.exists():
            print(f"Profile file not found: {resolved}")
            return False

        profiles_root = Path(run_config.get("profiles_dir", "profiles")).resolve()
        if resolved.is_relative_to(profiles_root):
            rel = resolved.relative_to(profiles_root).with_suffix("")
            run_config["profiles_dir"] = str(profiles_root)
            run_config["profile_name"] = rel.as_posix()
        else:
            run_config["profiles_dir"] = str(resolved.parent)
            run_config["profile_name"] = resolved.stem

        run_config["profile_path"] = str(resolved)
        print(f"  Profile : {run_config['profile_name']}")

        if not run_config.get("override_username"):
            try:
                data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
                if data.get("username"):
                    run_config["override_username"] = str(data["username"])
            except Exception:
                pass

    if mount_path:
        run_config["mount_path"] = str(mount_path)

    audit = AuditLogger(Path(run_config.get("audit_log_path", "audit.log")))
    orchestrator = Orchestrator(config=run_config, audit_logger=audit, dry_run=dry_run)
    orchestrator.initialize()

    num = register_services(orchestrator)
    print(f"  Services : {num} registered")

    if num == 0:
        print("  No services registered. Nothing to do.")
        return False

    print(f"\nStarting generation{' (dry run)' if dry_run else ''}...\n")

    def _progress(current: int, total: int, name: str) -> None:
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(40 * current // total)
        bar = "#" * filled + "-" * (40 - filled)
        print(f"\r[{bar}] {pct:3d}% | {name[:30].ljust(30)}", end="", flush=True)
        if current == total:
            print()

    result = orchestrator.run(progress_callback=_progress)

    def _fmt(ms: float) -> str:
        if ms < 0.1:
            return "<0.1ms"
        return f"{ms:.1f}ms" if ms < 1000 else f"{ms / 1000:.2f}s"

    print("\n" + "=" * 70)
    print("  GENERATION SUMMARY")
    print("=" * 70)
    if dry_run:
        print("  (dry-run: no files written)\n")
    for r in result.results:
        status = "PASS" if r.success else "FAIL"
        print(f"  [{status}] {r.service_name[:35].ljust(35)} {_fmt(r.duration_ms):>10}")
        if not r.success and r.error:
            print(f"         -> {r.error}")
    print("=" * 70)

    if result.success:
        print(f"  SUCCESS: {result.services_executed} services in {_fmt(result.total_duration_ms)}")
        return True
    print(f"  FAILED : {result.services_failed}/{result.services_executed + result.services_failed} services failed")
    return False


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def automated_test_workflow(config: Dict[str, Any]) -> None:
    clear_screen()
    print_header("Automated AI Test Workflow")
    print("  Generates a test persona and runs a dry-run artifact generation.")

    if not get_yes_no("Proceed?", default=True):
        return

    profile_path = run_ai_generation(
        config=config,
        occupation="Senior Software Engineer",
        location="Seattle",
        interests=["open source", "gaming", "machine learning"],
        age_range="28-35",
    )

    if not profile_path:
        print("\nTest failed at AI generation step.")
        input("\nPress ENTER to return to menu...")
        return

    run_artifact_generation(
        config=config,
        profile_path=profile_path,
        mount_path=None,
        dry_run=True,
    )

    print(f"\n  Profile saved to: {profile_path}")
    input("\nPress ENTER to return to menu...")


def manual_workflow(config: Dict[str, Any]) -> None:
    clear_screen()
    print_header("Manual Generation Workflow")

    print_section("Step 1: Output Directory")
    print("  Point at a pre-mounted VHDX partition or any local folder.")
    mount_path_str = get_input("  Output / mount path", default="./output")
    mount_path = Path(mount_path_str) if mount_path_str else None

    print_section("Step 2: Persona")
    occupation = get_input("  Occupation", default="Software Engineer", required=True)
    location = get_input("  Location", default="United States")
    interests_raw = get_input("  Interests (comma-separated)", default="coding,reading")
    interests = [i.strip() for i in interests_raw.split(",")] if interests_raw else None
    age_range = get_input("  Age range", default="25-35")

    profile_path = run_ai_generation(
        config=config,
        occupation=occupation,
        location=location,
        interests=interests,
        age_range=age_range,
    )

    if not profile_path:
        print("\nAI generation failed. Cannot continue without a persona.")
        input("\nPress ENTER to return to menu...")
        return

    print_section("Step 3: Artifact Generation")
    dry_run = get_yes_no("Dry-run mode (no files written)?", default=False)

    run_artifact_generation(
        config=config,
        profile_path=profile_path,
        mount_path=mount_path,
        dry_run=dry_run,
    )

    input("\nPress ENTER to return to menu...")


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu() -> None:
    config_path = Path("config.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    while True:
        clear_screen()
        print_header("ARC - Artifact Reality Composer")

        choice = get_choice(
            "Select an action:",
            [
                "Automated test (dry-run, no mount required)",
                "Manual workflow (full control)",
                "Exit",
            ],
            default=1,
        )

        if choice == 0:
            automated_test_workflow(config)
        elif choice == 1:
            manual_workflow(config)
        elif choice == 2:
            clear_screen()
            sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        main_menu()
    except KeyboardInterrupt:
        clear_screen()
        print("\nInterrupted. Exiting.\n")
        sys.exit(0)
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        logging.exception("Fatal error in wizard")
        sys.exit(1)
