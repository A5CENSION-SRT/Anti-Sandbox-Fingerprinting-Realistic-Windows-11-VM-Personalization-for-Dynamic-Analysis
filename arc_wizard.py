#!/usr/bin/env python3
"""ARC Interactive Wizard - Menu-driven CLI for VM personalization.

A user-friendly CLI tool that guides you through:
- Drive mount/dismount management
- AI-powered profile generation
- Artifact generation with progress tracking
- Automated testing workflows

Usage:
    python arc_wizard.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Load .env files if available
try:
    from dotenv import load_dotenv
    # Load from project root first
    root_loaded = load_dotenv(Path(__file__).parent / ".env")
    # Then load from services/ai/.env (more specific)
    ai_loaded = load_dotenv(Path(__file__).parent / "services" / "ai" / ".env")
    
    # Debug info
    if root_loaded or ai_loaded:
        import os
        if os.environ.get("GEMINI_API_KEY"):
            pass  # Key loaded successfully, no output needed
        else:
            print("⚠️  .env files loaded but GEMINI_API_KEY not found")
            print("   Check that your .env file contains:")
            print("   GEMINI_API_KEY=your-actual-key-here\n")
except ImportError:
    print("\n" + "=" * 60)
    print("⚠️  python-dotenv is NOT installed!")
    print("=" * 60)
    print("\n📦 Install it now:")
    print("   Option 1: pip install python-dotenv")
    print("   Option 2: Run quick_install.bat")
    print("   Option 3: Run install_deps.bat (installs everything)")
    print("\n💡 Without python-dotenv, .env files won't be loaded.")
    print("   You'll need to set GEMINI_API_KEY as an environment variable.")
    print("\n" + "=" * 60 + "\n")
    input("Press ENTER to continue anyway (will use env vars)...")

# Ensure project root is in path
_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Console Utilities
# ---------------------------------------------------------------------------

def clear_screen() -> None:
    """Clear the console screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def print_section(title: str) -> None:
    """Print a section divider."""
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}\n")


def get_choice(prompt: str, options: list, default: Optional[int] = None) -> int:
    """Get a numbered choice from the user.
    
    Args:
        prompt: The question to ask
        options: List of option strings
        default: Default choice (1-indexed), None for required
        
    Returns:
        Selected index (0-indexed)
    """
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    
    while True:
        if default:
            choice = input(f"\nEnter choice (1-{len(options)}) [{default}]: ").strip()
            if not choice:
                return default - 1
        else:
            choice = input(f"\nEnter choice (1-{len(options)}): ").strip()
        
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(options):
                return idx - 1
        
        print(f"❌ Invalid choice. Please enter a number between 1 and {len(options)}.")


def get_yes_no(prompt: str, default: bool = True) -> bool:
    """Get a yes/no response from the user."""
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} ({default_str}): ").strip().lower()
        if not response:
            return default
        if response in ('y', 'yes'):
            return True
        if response in ('n', 'no'):
            return False
        print("❌ Please enter 'y' or 'n'.")


def get_input(prompt: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Get text input from the user."""
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
            print("❌ This field is required.")


# ---------------------------------------------------------------------------
# Drive Management
# ---------------------------------------------------------------------------

def check_drive_mounted(drive_letter: str = "Z") -> bool:
    """Check if a drive is currently mounted.
    
    Args:
        drive_letter: Drive letter to check (without colon)
        
    Returns:
        True if drive exists, False otherwise
    """
    if os.name == 'nt':
        # Windows
        drive_path = f"{drive_letter}:\\"
        return Path(drive_path).exists()
    else:
        # Linux - check common mount points
        mount_point = f"/mnt/{drive_letter.lower()}"
        return Path(mount_point).exists() and Path(mount_point).is_mount()


def dismount_drive(drive_letter: str = "Z") -> bool:
    """Attempt to dismount a drive.
    
    Args:
        drive_letter: Drive letter to dismount
        
    Returns:
        True if successful or already dismounted, False on error
    """
    if not check_drive_mounted(drive_letter):
        print(f"ℹ️  Drive {drive_letter}: is not mounted.")
        return True
    
    print(f"\n🔄 Dismounting drive {drive_letter}:...")
    
    # Use VMManager for reliable dismount
    try:
        from core.vm_manager import VMManager
        
        # VMManager needs a VHD path, but for dismount we just need to find any mounted VHD
        # Try using PowerShell to find and dismount
        if os.name == 'nt':
            # Find VHD mounted to this drive letter
            find_cmd = f"Get-VHD | Where-Object {{$_.Attached -and $_.DiskNumber}} | Select-Object -First 1"
            result = subprocess.run(
                ["powershell", "-Command", find_cmd],
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            # Try generic dismount using diskpart
            diskpart_script = f"select volume {drive_letter}\nremove letter={drive_letter}\n"
            try:
                proc = subprocess.Popen(
                    ["diskpart"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, stderr = proc.communicate(input=diskpart_script, timeout=30)
                
                if proc.returncode == 0 or "successfully" in stdout.lower():
                    print(f"✅ Drive {drive_letter}: dismounted successfully.")
                    return True
                else:
                    print(f"⚠️  Diskpart result: {stdout}")
                    # Try one more method - remove-partition
                    remove_cmd = f"Get-Partition -DriveLetter {drive_letter} | Remove-PartitionAccessPath -AccessPath '{drive_letter}:'"
                    result = subprocess.run(
                        ["powershell", "-Command", remove_cmd],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        print(f"✅ Drive {drive_letter}: dismounted successfully.")
                        return True
                    return False
            except Exception as e:
                print(f"⚠️  Dismount attempt: {e}")
                return False
        else:
            # Linux - umount
            mount_point = f"/mnt/{drive_letter.lower()}"
            result = subprocess.run(
                ["sudo", "umount", mount_point],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                print(f"✅ Drive dismounted from {mount_point}.")
                return True
            else:
                print(f"❌ Unmount failed: {result.stderr}")
                return False
                
    except Exception as e:
        print(f"❌ Error dismounting drive: {e}")
        print(f"ℹ️  You may need to manually dismount using Disk Management or 'mountvol {drive_letter}: /d'")
        return False


# ---------------------------------------------------------------------------
# AI Generation Workflow
# ---------------------------------------------------------------------------

def run_ai_generation(
    config: Dict[str, Any],
    occupation: str,
    location: Optional[str] = None,
    interests: Optional[list] = None,
    age_range: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Run AI profile generation.
    
    Returns:
        Path to generated profile YAML, or None on failure
    """
    try:
        from services.ai import AIOrchestrator
        
        print_section("AI Profile Generation")
        
        # Check for API key
        api_key = config.get("ai", {}).get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("⚠️  No Gemini API key found!")
            print("   Set GEMINI_API_KEY environment variable or add to config.yaml")
            print("   Continuing anyway: fallback generation will be used if enabled.")
        
        orchestrator = AIOrchestrator.from_config(
            config=config,
            output_dir=output_dir or Path("profiles/generated"),
        )
        
        print(f"📝 Occupation:   {occupation}")
        if location:
            print(f"📍 Location:     {location}")
        if interests:
            print(f"🎯 Interests:    {', '.join(interests)}")
        if age_range:
            print(f"🎂 Age Range:    {age_range}")
        
        print("\n🤖 Generating AI persona...")
        
        result = orchestrator.generate_profile(
            occupation=occupation,
            location=location,
            interests=interests,
            age_range=age_range,
        )
        
        if result.success and result.persona:
            print(f"\n✅ Generated persona: {result.persona.full_name}")
            print(f"   Username:     {result.persona.username}")
            print(f"   Organization: {result.persona.organization}")
            print(f"   Email:        {result.persona.email}")
            
            if result.profile_path:
                print(f"\n💾 Profile saved to: {result.profile_path}")
                
            if result.expanded_counts:
                print(f"\n📊 Generated seeds:")
                for artifact_type, count in result.expanded_counts.items():
                    print(f"   {artifact_type}: {count} seeds")
            
            print(f"\n⏱️  Generation time: {result.generation_time_ms:.1f}ms")
            
            if result.used_fallback:
                print(f"\n⚠️  Used fallback generators (API unavailable)")
            
            return result.profile_path
        else:
            print(f"\n❌ AI generation failed:")
            for err in result.errors:
                print(f"   - {err}")
            return None
            
    except ImportError as e:
        print(f"\n❌ AI modules not available: {e}")
        print("   Install dependencies: pip install google-generativeai pydantic jinja2")
        return None
    except Exception as e:
        print(f"\n❌ Error during AI generation: {e}")
        return None


def run_artifact_generation(
    config: Dict[str, Any],
    profile_path: Optional[Path],
    mount_path: Optional[Path],
    dry_run: bool = False,
) -> bool:
    """Run the main artifact generation workflow.
    
    Returns:
        True if successful, False otherwise
    """
    from core.audit_logger import AuditLogger
    from core.orchestrator import Orchestrator
    from main import register_services
    
    print_section("Artifact Generation")
    
    # Build run-specific config so menu-level config is not mutated across runs.
    run_config = dict(config)

    # Update config with profile and mount path
    if profile_path:
        resolved_profile = Path(profile_path).resolve()
        if not resolved_profile.exists():
            print(f"❌ Profile file not found: {resolved_profile}")
            return False

        # Orchestrator loads by profile_name from profiles_dir.
        # Keep profiles_dir at the root so inheritance (e.g., base, developer)
        # can still resolve for generated profiles under profiles/generated.
        profiles_root = Path(run_config.get("profiles_dir", "profiles")).resolve()
        if resolved_profile.is_relative_to(profiles_root):
            relative_profile = resolved_profile.relative_to(profiles_root).with_suffix("")
            profile_name = relative_profile.as_posix()
            run_config["profiles_dir"] = str(profiles_root)
        else:
            # Fallback for external profile locations.
            profile_name = resolved_profile.stem
            run_config["profiles_dir"] = str(resolved_profile.parent)

        run_config["profile_path"] = str(resolved_profile)
        run_config["profile_name"] = profile_name
        print(f"👤 Using generated profile: {run_config['profile_name']}")

        # Keep generated identity consistent with the selected profile username.
        if not run_config.get("override_username"):
            try:
                profile_data = yaml.safe_load(resolved_profile.read_text(encoding="utf-8")) or {}
                profile_username = profile_data.get("username")
                if profile_username:
                    run_config["override_username"] = str(profile_username)
            except Exception as exc:
                print(f"⚠️  Could not read profile username override: {exc}")
    if mount_path:
        run_config["mount_path"] = str(mount_path)
    
    # Initialize
    audit_logger = AuditLogger(Path(run_config.get("audit_log_path", "audit.log")))
    orchestrator = Orchestrator(
        config=run_config,
        audit_logger=audit_logger,
        dry_run=dry_run,
    )
    
    orchestrator.initialize()
    
    # Register services
    num_services = register_services(orchestrator)
    print(f"📦 Registered {num_services} services")
    
    if num_services == 0:
        print("⚠️  No services registered. Nothing to do.")
        return False
    
    # Run generation
    print(f"\n🚀 Starting generation{' (DRY RUN)' if dry_run else ''}...\n")
    
    def progress_callback(current: int, total: int, service_name: str):
        if total == 0:
            return
        percentage = int(current / total * 100)
        bar_length = 40
        filled = int(bar_length * current // total)
        bar = '█' * filled + '░' * (bar_length - filled)
        print(f"\r[{bar}] {percentage}% | {service_name[:30].ljust(30)}", end='', flush=True)
        if current == total:
            print()
    
    result = orchestrator.run(progress_callback=progress_callback)

    def format_duration(duration_ms: float) -> str:
        """Format service duration for readable summaries."""
        if duration_ms < 0.1:
            return "<0.1ms"
        if duration_ms < 1000:
            return f"{duration_ms:.1f}ms"
        return f"{duration_ms / 1000:.2f}s"
    
    # Results
    print("\n" + "=" * 70)
    print("  GENERATION SUMMARY")
    print("=" * 70 + "\n")

    if dry_run:
        print("ℹ️  Dry-run mode: service timings are simulated and can appear as <0.1ms.\n")
    
    for svc_result in result.results:
        status = "✅ PASS" if svc_result.success else "❌ FAIL"
        time_str = format_duration(svc_result.duration_ms)
        print(f"{status} | {svc_result.service_name[:30].ljust(30)} | {time_str:>10}")
        if not svc_result.success and svc_result.error:
            print(f"       ⮡ ERROR: {svc_result.error}")
    
    print("\n" + "=" * 70)
    
    if result.success:
        print(f"✅ SUCCESS: All {result.services_executed} services completed")
        print(f"⏱️  Total time: {format_duration(result.total_duration_ms)} ({result.total_duration_ms / 1000:.2f} seconds)")
        return True
    else:
        print(f"❌ FAILED: {result.services_failed} out of {result.services_executed + result.services_failed} services failed")
        return False


# ---------------------------------------------------------------------------
# Main Menu
# ---------------------------------------------------------------------------

def automated_test_workflow(config: Dict[str, Any]) -> None:
    """Automated test workflow for AI generation."""
    clear_screen()
    print_header("🧪 Automated AI Test Workflow")
    
    print("This will:")
    print("  1. Check and dismount Z: if present")
    print("  2. Generate an AI profile (Software Engineer)")
    print("  3. Run artifact generation (dry-run)")
    print("  4. Keep any mounted drives intact for inspection")
    print()
    
    if not get_yes_no("Proceed with automated test?", default=True):
        return
    
    # Step 1: Check Z: drive
    print_section("Step 1: Drive Check")
    if check_drive_mounted("Z"):
        print("⚠️  Z: drive is currently mounted")
        if get_yes_no("Dismount Z: drive?", default=True):
            dismount_drive("Z")
    else:
        print("✅ Z: drive is not mounted")
    
    # Step 2: AI Generation
    print_section("Step 2: AI Profile Generation (Test)")
    
    test_occupation = "Senior Software Engineer"
    test_interests = ["open source", "gaming", "machine learning"]
    test_location = "Seattle"
    
    profile_path = run_ai_generation(
        config=config,
        occupation=test_occupation,
        location=test_location,
        interests=test_interests,
        age_range="28-35",
    )
    
    if not profile_path:
        print("\n❌ Test failed at AI generation step")
        input("\nPress ENTER to return to main menu...")
        return
    
    # Step 3: Artifact Generation (Dry Run)
    print_section("Step 3: Artifact Generation (Dry Run)")
    
    success = run_artifact_generation(
        config=config,
        profile_path=profile_path,
        mount_path=None,  # No mount for test
        dry_run=True,
    )
    
    # Summary
    print_section("Test Summary")
    if success:
        print("✅ Automated test completed successfully!")
        print(f"\n📄 Generated profile: {profile_path}")
        print("   You can inspect the profile YAML file")
        print("\n💡 To run full generation, use the manual workflow from main menu")
    else:
        print("❌ Test encountered errors (see above)")
    
    print("\n" + "=" * 70)
    input("\nPress ENTER to return to main menu...")


def manual_workflow(config: Dict[str, Any]) -> None:
    """Interactive manual workflow."""
    clear_screen()
    print_header("🎯 Manual Generation Workflow")
    
    # Step 1: Drive management
    print_section("Step 1: Drive Management")
    
    drive_mounted = check_drive_mounted("Z")
    if drive_mounted:
        print("ℹ️  Z: drive is currently mounted")
        if get_yes_no("Dismount Z: drive?", default=True):
            if not dismount_drive("Z"):
                print("⚠️  Failed to dismount drive. Continue anyway?")
                if not get_yes_no("Continue?", default=False):
                    return
    else:
        print("ℹ️  Z: drive is not currently mounted")
    
    # Ask if they want to mount a new drive
    mount_new = get_yes_no("\nMount a new VHD/VHDX?", default=False)
    mount_path = None
    vm_manager = None
    
    if mount_new:
        vhdx_path = get_input("\nEnter path to VHD/VHDX file", required=True)
        if vhdx_path and Path(vhdx_path).exists():
            print(f"\n🔄 Mounting {vhdx_path}...")
            # Use VMManager from core
            try:
                from core.vm_manager import VMManager
                vm_manager = VMManager(vhdx_path)
                mount_path = Path(vm_manager.mount_vhdx())
                print(f"✅ Mounted to: {mount_path}")
            except Exception as e:
                print(f"❌ Failed to mount: {e}")
                mount_path = None
                vm_manager = None
        else:
            print(f"❌ File not found: {vhdx_path}")
    
    # Step 2: Profile configuration
    print_section("Step 2: Profile Configuration")
    
    use_ai = get_yes_no("Use AI generation for profile?", default=True)
    profile_path = None
    
    if use_ai:
        print("\n📝 Enter persona details:")
        occupation = get_input("  Occupation", default="Software Engineer", required=True)
        location = get_input("  Location", default="Seattle")
        interests_str = get_input("  Interests (comma-separated)", default="coding,gaming")
        interests = [i.strip() for i in interests_str.split(",")] if interests_str else None
        age_range = get_input("  Age range", default="25-35")
        
        profile_path = run_ai_generation(
            config=config,
            occupation=occupation,
            location=location,
            interests=interests,
            age_range=age_range,
        )
        
        if not profile_path:
            print("\n❌ AI generation failed")
            if not get_yes_no("Continue with static profile?", default=False):
                return
    else:
        # Use static profile
        profile_choice = get_choice(
            "\nSelect a static profile:",
            ["developer", "office_user", "home_user", "base"],
            default=1,
        )
        profile_names = ["developer", "office_user", "home_user", "base"]
        config["profile_name"] = profile_names[profile_choice]
        print(f"✅ Using profile: {profile_names[profile_choice]}")
    
    # Step 3: Generation
    print_section("Step 3: Artifact Generation")
    
    dry_run = get_yes_no("Run in dry-run mode (no files written)?", default=False)
    
    success = run_artifact_generation(
        config=config,
        profile_path=profile_path,
        mount_path=mount_path,
        dry_run=dry_run,
    )
    
    # Step 4: Cleanup
    if mount_path and vm_manager:
        print_section("Step 4: Drive Cleanup")
        if get_yes_no("Dismount the VHD/VHDX?", default=True):
            try:
                vm_manager.dismount_vhdx()
                print("✅ Dismounted image successfully")
            except Exception as e:
                print(f"❌ Failed to dismount image: {e}")
        else:
            print("ℹ️  Drive left mounted for inspection")
    
    print("\n" + "=" * 70)
    input("\nPress ENTER to return to main menu...")


def main_menu() -> None:
    """Display and handle the main menu."""
    # Load config
    config_path = Path("config.yaml")
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    
    while True:
        clear_screen()
        print_header("🎨 ARC Interactive Wizard")
        
        print("Welcome to the Artifact Reality Composer!")
        print()
        
        choice = get_choice(
            "What would you like to do?",
            [
                "🧪 Automated AI Test (Quick test workflow, no drive mount)",
                "🎯 Manual Workflow (Full control with drive management)",
                "📋 Check Drive Status",
                "🔧 Dismount Z: Drive",
                "❌ Exit",
            ],
            default=1,
        )
        
        if choice == 0:  # Automated test
            automated_test_workflow(config)
        elif choice == 1:  # Manual workflow
            manual_workflow(config)
        elif choice == 2:  # Check drive
            clear_screen()
            print_header("Drive Status")
            if check_drive_mounted("Z"):
                print("✅ Z: drive is currently mounted")
            else:
                print("❌ Z: drive is not mounted")
            input("\nPress ENTER to continue...")
        elif choice == 3:  # Dismount
            clear_screen()
            print_header("Dismount Drive")
            dismount_drive("Z")
            input("\nPress ENTER to continue...")
        elif choice == 4:  # Exit
            clear_screen()
            print("\n👋 Goodbye!\n")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Setup basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    try:
        main_menu()
    except KeyboardInterrupt:
        clear_screen()
        print("\n\n👋 Interrupted by user. Goodbye!\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        logging.exception("Fatal error in wizard")
        sys.exit(1)
