# Environment Setup Guide

## Quick Setup (3 Steps)

### 1. Install Dependencies

**Windows:**
```cmd
install_deps.bat
```

**Linux/macOS:**
```bash
./install_deps.sh
```

**Manual:**
```bash
pip install -r requirements.txt
```

### 2. Configure API Key

You have **THREE options** for setting your Gemini API key:

#### Option A: .env file in project root (Recommended)

```bash
# Copy the template
cp .env.example .env

# Edit .env and set:
GEMINI_API_KEY=your-actual-api-key-here
```

**Location:** `/Anti-Sandbox-Fingerprinting-.../.env`

#### Option B: .env file in services/ai/

```bash
# Copy the template
cp services/ai/.env.example services/ai/.env

# Edit services/ai/.env and set:
GEMINI_API_KEY=your-actual-api-key-here
```

**Location:** `/Anti-Sandbox-Fingerprinting-.../services/ai/.env`

#### Option C: Environment variable

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY="your-key-here"
```

**Windows (Command Prompt - Permanent):**
```cmd
setx GEMINI_API_KEY "your-key-here"
```

**Linux/macOS (Session):**
```bash
export GEMINI_API_KEY="your-key-here"
```

**Linux/macOS (Permanent - add to ~/.bashrc or ~/.zshrc):**
```bash
echo 'export GEMINI_API_KEY="your-key-here"' >> ~/.bashrc
source ~/.bashrc
```

### 3. Verify Setup

```bash
python test_env.py
```

Expected output:
```
🔍 Loading .env files...
   ✅ Loaded: .env
   ✅ Loaded: services/ai/.env

🔑 Checking for GEMINI_API_KEY...
   ✅ GEMINI_API_KEY found: AIzaSyD...xyz
   Length: 39 characters

============================================================
✅ Environment setup looks good!

You can now run:
  python arc_wizard.py
============================================================
```

## Priority Order

The scripts check for API key in this order:
1. Environment variable `GEMINI_API_KEY`
2. `.env` file in project root
3. `services/ai/.env` file
4. `config.yaml` under `ai.gemini.api_key`

**Tip:** Use `.env` files for development, environment variables for production.

## Get a Gemini API Key

1. Go to https://makersuite.google.com/app/apikey
2. Click "Create API Key"
3. Copy the key (starts with `AIza...`)
4. Add to your `.env` file or environment

## Troubleshooting

### "python-dotenv not installed"
```bash
pip install python-dotenv
```

### "GEMINI_API_KEY not found"
Check:
1. Is `.env` file in project root?
2. Did you edit `.env` to replace `REPLACE_WITH_YOUR_GEMINI_API_KEY`?
3. Try running: `echo $GEMINI_API_KEY` (Linux/macOS) or `echo %GEMINI_API_KEY%` (Windows)

### "Invalid API key"
- Make sure you copied the full key (usually 39 characters)
- No quotes around the key in `.env` file:
  ```
  # ✅ Correct
  GEMINI_API_KEY=AIzaSyD...xyz
  
  # ❌ Wrong
  GEMINI_API_KEY="AIzaSyD...xyz"
  GEMINI_API_KEY='AIzaSyD...xyz'
  ```

### Still not working?
```bash
# Check if .env is being loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('GEMINI_API_KEY'))"
```

## Security Notes

- **Never commit .env files to git** (already in .gitignore)
- Use `.env.example` as a template (no secrets)
- For production, use secret management systems (Azure Key Vault, AWS Secrets Manager, etc.)
- Rotate keys regularly

## Ready to Go!

Once setup is complete:
```bash
python arc_wizard.py
```

Select option 1 (Automated AI Test) to verify everything works! 🚀
