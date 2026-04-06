# ARC Interactive Wizard 🎨

A user-friendly menu-driven interface for the Artifact Reality Composer.

## Quick Start

```bash
# Set your Gemini API key (required for AI features)
export GEMINI_API_KEY="your-api-key-here"

# Run the wizard
python arc_wizard.py
```

## Features

### 🧪 Automated AI Test
Quick test workflow that:
- Checks/dismounts Z: drive if needed
- Generates a test AI profile (Software Engineer with gaming/ML interests)
- Runs artifact generation in **dry-run mode** (no files written)
- **Keeps drives mounted** for inspection afterwards

Perfect for testing the AI workflow without making changes.

### 🎯 Manual Workflow
Full control over the entire process:

1. **Drive Management**
   - Check if Z: is mounted → offer to dismount
   - Option to mount new VHD/VHDX file
   - Uses VMManager for reliable mounting

2. **Profile Configuration**
   - Choose AI generation or static profile
   - For AI: enter occupation, location, interests, age range
   - For static: select from developer/office_user/home_user/base

3. **Artifact Generation**
   - Choose dry-run or full generation
   - Progress bar with service-by-service status
   - Detailed success/failure summary

4. **Cleanup**
   - Option to dismount drive after generation
   - Or leave mounted for manual inspection

### 📋 Utility Functions
- **Check Drive Status** - Quickly see if Z: is mounted
- **Dismount Z: Drive** - Manual dismount option

## Menu Navigation

```
════════════════════════════════════════════════════════════════════
  🎨 ARC Interactive Wizard
════════════════════════════════════════════════════════════════════

What would you like to do?
  1. 🧪 Automated AI Test (Quick test workflow, no drive mount)
  2. 🎯 Manual Workflow (Full control with drive management)
  3. 📋 Check Drive Status
  4. 🔧 Dismount Z: Drive
  5. ❌ Exit

Enter choice (1-5) [1]:
```

## API Key Setup

The wizard checks for Gemini API key in:
1. `GEMINI_API_KEY` environment variable (recommended)
2. `config.yaml` under `ai.gemini.api_key`

If no key is found, it falls back to static profile generation.

### Setting API Key

**Linux/macOS (bash):**
```bash
export GEMINI_API_KEY="your-key-here"
```

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY="your-key-here"
```

**Permanent (Windows):**
```powershell
setx GEMINI_API_KEY "your-key-here"
```

## Requirements

- Python 3.8+
- Dependencies: `pyyaml`
- For AI features: `google-generativeai`, `pydantic`, `jinja2`

Install all:
```bash
pip install -r requirements.txt
```

## Troubleshooting

### "Z: drive dismount failed"
- Try manually: `mountvol Z: /d` (Windows) or check Disk Management
- Ensure no programs are accessing the drive
- The wizard will ask if you want to continue anyway

### "AI modules not available"
```bash
pip install google-generativeai pydantic jinja2
```

### "No Gemini API key found"
Set the `GEMINI_API_KEY` environment variable or add to config.yaml:
```yaml
ai:
  gemini:
    api_key: "your-key-here"
```

## Tips

- Use **Automated Test** first to verify AI setup without making changes
- **Manual Workflow** gives you full control over each step
- The wizard always confirms destructive actions
- Press Ctrl+C anytime to exit safely
- Check `audit.log` for detailed execution logs

## Example Session

```
# First time? Try automated test
python arc_wizard.py
> Select option 1 (Automated AI Test)
> Review the generated profile and dry-run results
> Exit (option 5)

# Ready for real generation?
python arc_wizard.py
> Select option 2 (Manual Workflow)
> Follow prompts for drive management
> Enter your occupation and interests
> Review results
> Choose to keep drive mounted for inspection
```

## See Also

- `main.py` - Command-line interface with flags
- `services/ai/cli.py` - Dedicated AI profile generation CLI
- `config.yaml` - Configuration file
- `.env.example` - Environment variable template
