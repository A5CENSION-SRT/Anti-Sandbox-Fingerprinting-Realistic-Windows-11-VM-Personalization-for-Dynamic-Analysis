# 🎨 ARC Wizard - Quick Start

## Installation

```bash
# Windows
install_deps.bat

# Linux/macOS
./install_deps.sh

# Or manually:
pip install python-dotenv pyyaml pydantic google-generativeai jinja2
```

## Setup API Key

**Option 1: .env file (Recommended)**
```bash
# Copy the template
cp .env.example .env

# Edit .env and add your key:
GEMINI_API_KEY=your-actual-api-key-here
```

**Option 2: Environment variable**
```bash
# Windows (PowerShell)
$env:GEMINI_API_KEY="your-key-here"

# Linux/macOS
export GEMINI_API_KEY="your-key-here"
```

## Fixed Issues ✅
1. ✅ Dismount drive logic updated (no longer uses unavailable Dismount-VHD cmdlet)
2. ✅ PersonaGenerator initialization fixed (removed invalid seed parameter)
3. ✅ API key validation added (warns if missing, falls back gracefully)
4. ✅ Interest list properly converted to hints string
5. ✅ **Now loads .env files automatically!**

## Usage

### Test AI Workflow (Recommended First)
```
python arc_wizard.py
> Select: 1 (Automated AI Test)
```

This will:
- Check/dismount Z: if needed
- Generate test profile (Software Engineer)
- Run dry-run generation (no files written)
- Leave drives mounted for inspection

### Full Manual Control
```
python arc_wizard.py
> Select: 2 (Manual Workflow)
> Follow prompts
```

## Dismount Options

The wizard now uses multiple fallback methods:
1. Diskpart (select volume + remove letter)
2. PowerShell Remove-PartitionAccessPath
3. Manual instructions if all fail

## What's New

- Interactive menu with emoji indicators
- API key validation before generation
- Graceful fallback to static profiles if AI unavailable
- Progress bars for artifact generation
- Clear success/failure indicators
- Option to keep drives mounted for inspection

## Files Created

- `arc_wizard.py` - Main wizard (project root)
- `docs/wizard_guide.md` - Detailed guide
- `.env.example` - API key template

## Next Steps

1. Run automated test to verify setup
2. Check generated profile in `profiles/generated/`
3. Use manual workflow for real VM generation
4. Inspect `audit.log` for details

Enjoy! 🚀
