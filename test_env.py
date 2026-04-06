#!/usr/bin/env python3
"""Test script to verify .env loading and API key detection."""

import os
from pathlib import Path

# Load .env files
try:
    from dotenv import load_dotenv
    
    print("🔍 Loading .env files...")
    
    # Try project root
    root_env = Path(__file__).parent / ".env"
    if root_env.exists():
        load_dotenv(root_env)
        print(f"   ✅ Loaded: {root_env}")
    else:
        print(f"   ⚠️  Not found: {root_env}")
    
    # Try services/ai/.env
    ai_env = Path(__file__).parent / "services" / "ai" / ".env"
    if ai_env.exists():
        load_dotenv(ai_env)
        print(f"   ✅ Loaded: {ai_env}")
    else:
        print(f"   ⚠️  Not found: {ai_env}")
    
    print("\n🔑 Checking for GEMINI_API_KEY...")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        # Mask the key for security
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        print(f"   ✅ GEMINI_API_KEY found: {masked}")
        print(f"   Length: {len(api_key)} characters")
    else:
        print("   ❌ GEMINI_API_KEY not found!")
        print("\n   Please set it in:")
        print("   1. .env file (copy from .env.example)")
        print("   2. services/ai/.env file")
        print("   3. Environment variable")
        
    print("\n" + "=" * 60)
    if api_key:
        print("✅ Environment setup looks good!")
        print("\nYou can now run:")
        print("  python arc_wizard.py")
    else:
        print("❌ Setup incomplete - add GEMINI_API_KEY")
    print("=" * 60)
    
except ImportError:
    print("❌ python-dotenv not installed!")
    print("\nInstall with:")
    print("  pip install python-dotenv")
    print("\nOr run:")
    print("  pip install -r requirements.txt")
