# Environment Setup — ARC

ARC runs on Linux. See `SETUP.md` for the full setup walkthrough including system packages,
baseline VHDX creation, and CLI usage. This file covers environment variable configuration only.

---

## Required System Packages

```bash
sudo apt install -y libguestfs-tools python3-guestfs libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount virtinst qemu-system-x86 libvirt-daemon-system
pip install -r requirements.txt
```

---

## Environment Variables

### Required for libguestfs

```bash
export LIBGUESTFS_BACKEND=direct
```

The `direct` backend is faster than the default `libvirt` backend and does not require the
libvirt daemon to be running. Set this permanently in `~/.bashrc` or `~/.zshrc`.

### Required for AI persona generation (optional)

```bash
export GEMINI_API_KEY=your-key-here
```

Only needed when using `--ai-generate`. Preset runs (`--preset developer`) work without this.

### Configuration via `.env` file

Create a `.env` file in the project root:

```ini
LIBGUESTFS_BACKEND=direct
GEMINI_API_KEY=your-key-here
```

The application loads this automatically via `python-dotenv`. The `.env` file is listed in
`.gitignore` — do not commit it.

---

## API Key Sources

- **Gemini**: https://makersuite.google.com/app/apikey — creates a key starting with `AIza`
- **Local LLM** (`core/llm_client.py`): no API key; requires a local Ollama or llama.cpp
  endpoint. Configure the endpoint URL in `config.yaml::ai.local_llm.endpoint`.

---

## Priority Order (for `GEMINI_API_KEY`)

1. Environment variable `GEMINI_API_KEY`
2. `.env` file in project root
3. `services/ai/.env` file
4. `config.yaml` under `ai.gemini.api_key`

---

## Verify

```bash
python -c "
import os, pathlib
from dotenv import load_dotenv
load_dotenv()
key = os.getenv('GEMINI_API_KEY', '')
backend = os.getenv('LIBGUESTFS_BACKEND', 'not set')
print(f'LIBGUESTFS_BACKEND = {backend}')
print(f'GEMINI_API_KEY     = {key[:8]}... ({len(key)} chars)' if key else 'GEMINI_API_KEY     = not set (preset runs only)')
"
```
