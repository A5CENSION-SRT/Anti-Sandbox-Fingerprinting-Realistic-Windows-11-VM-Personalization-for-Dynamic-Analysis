"""Registry artifact seed generator.

Generates :class:`RegistrySeed` objects that drive UserAssist ROT13 entries,
Explorer TypedPaths, RunMRU, and RecentDocs MRU lists.  Falls back to rich
offline seeds when Gemini is unavailable.

The generated seeds are consumed by:
* ``services/registry/userassist.py``    — UserAssist ROT13 counters + times
* ``services/registry/mru_recentdocs.py`` — RecentDocs MRU extension buckets
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import PersonaContext, RegistrySeed, RegistryAppEntry

logger = logging.getLogger(__name__)

_PROMPTS_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "prompts"
_REGISTRY_PROMPT_FILE = _PROMPTS_DIR / "registry_artifacts.txt"


class RegistrySeedGenerationError(Exception):
    """Raised when registry seed generation fails."""


class RegistrySeedGenerator:
    """Generates registry MRU / UserAssist seeds via Gemini with offline fallback.

    Args:
        client:       Configured GeminiClient (may be None → always fallback).
        timeline_days: Days of history to represent.
    """

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        timeline_days: int = 360,
    ) -> None:
        self._client = client
        self._timeline_days = timeline_days
        self._prompt_template = (
            _REGISTRY_PROMPT_FILE.read_text(encoding="utf-8")
            if _REGISTRY_PROMPT_FILE.exists()
            else None
        )

    def generate(self, persona: PersonaContext, use_cache: bool = True) -> RegistrySeed:
        """Generate registry seeds, falling back to offline data on error."""
        if self._client and self._prompt_template:
            try:
                prompt = self._prompt_template.format(
                    full_name=persona.full_name,
                    occupation=persona.occupation,
                    profile_archetype=persona.profile_archetype,
                    installed_apps=", ".join(getattr(persona, "installed_apps", [])),
                    timeline_days=self._timeline_days,
                )
                result = self._client.generate_structured(
                    prompt=prompt,
                    schema=RegistrySeed,
                    temperature=0.6,
                    use_cache=use_cache,
                )
                logger.info("Generated registry seed via Gemini for %s", persona.full_name)
                return result
            except (GeminiClientError, Exception) as exc:
                logger.warning("Gemini registry seed failed (%s); using fallback", exc)

        return self._generate_fallback(persona)

    def _generate_fallback(self, persona: PersonaContext) -> RegistrySeed:
        archetype = getattr(persona, "profile_archetype", "office_user")
        installed = getattr(persona, "installed_apps", [])

        run_mru = _RUN_MRU_BY_PROFILE.get(archetype, _RUN_MRU_BY_PROFILE["office_user"])
        typed_paths = _TYPED_PATHS_BY_PROFILE.get(archetype, _TYPED_PATHS_BY_PROFILE["office_user"])
        apps = _userassist_apps(archetype, installed, self._timeline_days)

        return RegistrySeed(
            seed_id=f"registry_{archetype}",
            context=f"Registry MRU artifacts for {archetype}",
            run_mru_entries=run_mru,
            typed_paths=typed_paths,
            userassist_apps=apps,
            recent_doc_extensions=_RECENT_DOC_EXTS_BY_PROFILE.get(
                archetype, _RECENT_DOC_EXTS_BY_PROFILE["office_user"]
            ),
        )


# ---------------------------------------------------------------------------
# Fallback data tables
# ---------------------------------------------------------------------------

_RUN_MRU_BY_PROFILE: Dict[str, List[str]] = {
    "developer": [
        "powershell", "cmd", "wt", "code .", "python manage.py runserver",
        "git log --oneline -20", "docker ps", "npm start", ".\\.venv\\Scripts\\activate",
        "ssh ubuntu@10.0.0.5", "ipconfig /flushdns", "netstat -ano", "tasklist",
        "winver", "msconfig", "resmon", "perfmon", "eventvwr", "regedit",
    ],
    "office_user": [
        "excel", "winword", "powerpnt", "outlook", "teams", "onenote",
        "mspaint", "notepad", "calc", "control panel", "gpupdate /force",
        "\\\\fileserver\\shared", "\\\\backup01\\archive", "cmd", "powershell",
        "winver", "msconfig", "cleanmgr", "dfrgui", "eventvwr",
    ],
    "home_user": [
        "calc", "notepad", "mspaint", "winver", "msconfig", "cmd",
        "control panel", "dxdiag", "ipconfig", "ping google.com",
        "taskmgr", "regedit", "cleanmgr", "dfrgui", "charmap",
        "snippingtool", "sfc /scannow", "DISM /Online /Cleanup-Image /RestoreHealth",
    ],
}

_TYPED_PATHS_BY_PROFILE: Dict[str, List[str]] = {
    "developer": [
        "C:\\Users\\{username}\\source\\repos",
        "C:\\Users\\{username}\\AppData\\Roaming\\npm",
        "C:\\Program Files\\Git\\cmd",
        "\\\\wsl.localhost\\Ubuntu\\home",
        "C:\\Users\\{username}\\Documents",
        "C:\\ProgramData\\docker",
        "C:\\Windows\\System32",
        "D:\\projects",
        "C:\\Users\\{username}\\Downloads",
    ],
    "office_user": [
        "C:\\Users\\{username}\\Documents",
        "C:\\Users\\{username}\\Desktop",
        "C:\\Users\\{username}\\Downloads",
        "\\\\fileserver\\Shared\\Department",
        "\\\\backup01\\archive\\2024",
        "C:\\Program Files\\Microsoft Office\\root\\Office16",
        "C:\\Users\\{username}\\OneDrive - Company",
        "C:\\Windows\\System32",
    ],
    "home_user": [
        "C:\\Users\\{username}\\Documents",
        "C:\\Users\\{username}\\Pictures",
        "C:\\Users\\{username}\\Downloads",
        "C:\\Users\\{username}\\Desktop",
        "C:\\Users\\{username}\\Videos",
        "C:\\Users\\{username}\\Music",
        "C:\\Windows\\System32",
    ],
}

_RECENT_DOC_EXTS_BY_PROFILE: Dict[str, Dict[str, int]] = {
    "developer": {
        ".py": 45, ".json": 38, ".yaml": 29, ".md": 51, ".txt": 24,
        ".sh": 17, ".js": 33, ".ts": 28, ".go": 12, ".sql": 19,
        ".pdf": 22, ".png": 31, ".log": 16,
    },
    "office_user": {
        ".docx": 58, ".xlsx": 47, ".pptx": 31, ".pdf": 44, ".msg": 22,
        ".txt": 19, ".csv": 28, ".eml": 14, ".zip": 17, ".png": 23,
    },
    "home_user": {
        ".pdf": 38, ".jpg": 52, ".png": 41, ".mp4": 19, ".docx": 28,
        ".xlsx": 16, ".txt": 34, ".zip": 22, ".mp3": 17,
    },
}


def _userassist_apps(
    archetype: str,
    installed: List[str],
    timeline_days: int,
) -> List[RegistryAppEntry]:
    """Build a list of UserAssist ROT13 app entries with run counts."""
    import random as _rand
    rng = _rand.Random(hash(archetype + str(timeline_days)))

    base_apps = _USERASSIST_APPS_BY_PROFILE.get(archetype, [])
    entries: List[RegistryAppEntry] = []
    for app in base_apps:
        run_count = rng.randint(
            max(1, timeline_days // 30),
            min(timeline_days * app["daily_rate"], 2000),
        )
        entries.append(RegistryAppEntry(
            exe_path=app["exe"],
            run_count=run_count,
            focus_count=int(run_count * rng.uniform(1.5, 4.0)),
            focus_time_ms=int(run_count * rng.randint(120_000, 1_800_000)),
        ))
    return entries


_USERASSIST_APPS_BY_PROFILE: Dict[str, List[Dict[str, Any]]] = {
    "developer": [
        {"exe": r"C:\Program Files\Microsoft VS Code\Code.exe", "daily_rate": 8},
        {"exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "daily_rate": 12},
        {"exe": r"C:\Program Files\Git\cmd\git.exe", "daily_rate": 10},
        {"exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "daily_rate": 15},
        {"exe": r"C:\Windows\explorer.exe", "daily_rate": 6},
        {"exe": r"C:\Program Files\Docker\Docker\Docker Desktop.exe", "daily_rate": 4},
        {"exe": r"C:\Program Files\Slack\slack.exe", "daily_rate": 7},
        {"exe": r"C:\Program Files\Postman\Postman.exe", "daily_rate": 3},
        {"exe": r"C:\Windows\System32\cmd.exe", "daily_rate": 9},
        {"exe": r"C:\Windows\System32\notepad.exe", "daily_rate": 2},
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE", "daily_rate": 5},
        {"exe": r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal\wt.exe", "daily_rate": 11},
    ],
    "office_user": [
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE", "daily_rate": 7},
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE", "daily_rate": 6},
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE", "daily_rate": 14},
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE", "daily_rate": 3},
        {"exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "daily_rate": 12},
        {"exe": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "daily_rate": 8},
        {"exe": r"C:\Windows\explorer.exe", "daily_rate": 5},
        {"exe": r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe", "daily_rate": 10},
        {"exe": r"C:\Windows\System32\notepad.exe", "daily_rate": 3},
        {"exe": r"C:\Windows\System32\mmc.exe", "daily_rate": 1},
        {"exe": r"C:\Program Files\Zoom\bin\Zoom.exe", "daily_rate": 4},
        {"exe": r"C:\Program Files\Microsoft OneDrive\OneDrive.exe", "daily_rate": 2},
    ],
    "home_user": [
        {"exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "daily_rate": 18},
        {"exe": r"C:\Windows\explorer.exe", "daily_rate": 8},
        {"exe": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "daily_rate": 6},
        {"exe": r"C:\Program Files\VideoLAN\VLC\vlc.exe", "daily_rate": 2},
        {"exe": r"C:\Program Files\Windows Media Player\wmplayer.exe", "daily_rate": 1},
        {"exe": r"C:\Windows\System32\notepad.exe", "daily_rate": 2},
        {"exe": r"C:\Windows\System32\calc.exe", "daily_rate": 1},
        {"exe": r"C:\Windows\System32\mspaint.exe", "daily_rate": 1},
        {"exe": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE", "daily_rate": 2},
        {"exe": r"C:\Program Files\Spotify\Spotify.exe", "daily_rate": 5},
        {"exe": r"C:\Users\Public\Desktop\Steam.lnk", "daily_rate": 3},
    ],
}
