# 🚀 START HERE - First Time Setup

## ⚠️ IMPORTANT: Install AI Dependencies First!

Your `.env` file with the API key exists, but you need to install the required packages.

## Quick Fix (Choose One)

### Option 1: AI Dependencies Only (Fastest)
```cmd
install_ai_deps.bat
```
Installs: python-dotenv, google-generativeai, pydantic, jinja2

### Option 2: Full Install (Recommended)
```cmd
install_deps.bat
```
This installs ALL dependencies from requirements.txt.

### Option 3: Manual Install
```cmd
pip install python-dotenv google-generativeai pydantic jinja2
```

## After Installing

1. **Verify Setup:**
   ```cmd
   python test_env.py
   ```
   
   You should see:
   ```
   ✅ GEMINI_API_KEY found: AIzaSy...xyz
   ```

2. **Run the Wizard:**
   ```cmd
   python arc_wizard.py
   ```

## Why This Happened

- ✅ Your API key is correctly saved in `services/ai/.env`
- ❌ But `python-dotenv` package is not installed (can't read .env files)
- ❌ And `google-generativeai` package is not installed (can't call Gemini API)
- 💡 The wizard can use fallback generators, but needs these packages for full AI features

## What's Already Done

✅ API key file created: `services/ai/.env`
✅ Wizard script: `arc_wizard.py`
✅ All AI code: `services/ai/`
✅ Install scripts created

## What You Need to Do

❌ Install AI dependencies (ONE TIME - takes ~30 seconds)

```cmd
REM Option 1 - AI only (fastest):
install_ai_deps.bat

REM Option 2 - Everything:
install_deps.bat

REM Option 3 - Manual:
pip install python-dotenv google-generativeai pydantic jinja2

REM Then verify:
python test_env.py

REM Then use:
python arc_wizard.py
```

## Alternative: Use Environment Variable Instead

If you don't want to install python-dotenv, you can set the key as an environment variable:

**PowerShell:**
```powershell
$env:GEMINI_API_KEY="your-key-from-env-file"
python arc_wizard.py
```

**Command Prompt:**
```cmd
set GEMINI_API_KEY=your-key-from-env-file
python arc_wizard.py
```

But installing python-dotenv is easier! 😊

---

## TL;DR

```cmd
# 1. Install (ONCE - choose one)
install_ai_deps.bat
# OR
install_deps.bat

# 2. Verify
python test_env.py

# 3. Run
python arc_wizard.py
```

**What gets installed:**
- `python-dotenv` - Reads .env files
- `google-generativeai` - Gemini API client
- `pydantic` - Data validation
- `jinja2` - Template engine

That's it! 🎉
