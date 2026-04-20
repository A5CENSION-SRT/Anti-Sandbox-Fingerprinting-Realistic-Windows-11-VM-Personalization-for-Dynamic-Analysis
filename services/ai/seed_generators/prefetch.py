"""Prefetch app seed generator.

Produces :class:`PrefetchAppSeed` objects that drive
``services/filesystem/prefetch.py`` — telling it which executables to
generate .pf files for and how many times each app was run.

Falls back to profile-appropriate offline seeds when Gemini is unavailable.

The run-count distribution matters for forensic realism: pafish checks
whether the run_count field in the .pf header is plausible for the
declared timestamps.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import PersonaContext, PrefetchAppSeed, PrefetchEntry

logger = logging.getLogger(__name__)

_PROMPTS_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "prompts"
_PREFETCH_PROMPT_FILE = _PROMPTS_DIR / "prefetch_apps.txt"


class PrefetchSeedGenerationError(Exception):
    """Raised when prefetch seed generation fails."""


class PrefetchSeedGenerator:
    """Generates prefetch app list seeds via Gemini with offline fallback.

    Args:
        client:        Configured GeminiClient (may be None → always fallback).
        timeline_days: Days of history; used to compute realistic run counts.
    """

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        timeline_days: int = 360,
    ) -> None:
        self._client = client
        self._timeline_days = timeline_days
        self._prompt_template = (
            _PREFETCH_PROMPT_FILE.read_text(encoding="utf-8")
            if _PREFETCH_PROMPT_FILE.exists()
            else None
        )

    def generate(self, persona: PersonaContext, use_cache: bool = True) -> PrefetchAppSeed:
        """Generate prefetch seeds, falling back to offline data on error."""
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
                    schema=PrefetchAppSeed,
                    temperature=0.5,
                    use_cache=use_cache,
                )
                logger.info("Generated prefetch seed via Gemini for %s", persona.full_name)
                return result
            except (GeminiClientError, Exception) as exc:
                logger.warning("Gemini prefetch seed failed (%s); using fallback", exc)

        return self._generate_fallback(persona)

    def _generate_fallback(self, persona: PersonaContext) -> PrefetchAppSeed:
        import random as _rand
        archetype = getattr(persona, "profile_archetype", "office_user")
        rng = _rand.Random(hash(persona.full_name + archetype))

        raw = _PREFETCH_APPS_BY_PROFILE.get(archetype, _PREFETCH_APPS_BY_PROFILE["office_user"])
        entries: List[PrefetchEntry] = []

        for app in raw:
            # run_count = daily_launches × active_days (with jitter)
            base = int(app["daily_launches"] * self._timeline_days * 0.71)
            run_count = max(1, int(base * rng.uniform(0.7, 1.3)))

            entries.append(PrefetchEntry(
                exe_name=app["exe"],
                exe_path=app["path"],
                run_count=min(run_count, 9999),  # SCCA header field is DWORD
                # last_run_offset_h: hours back from "now" for most recent run
                last_run_offset_h=rng.randint(1, 48),
            ))

        return PrefetchAppSeed(
            seed_id=f"prefetch_{archetype}",
            context=f"Prefetch entries for {archetype} over {self._timeline_days} days",
            entries=entries,
        )


# ---------------------------------------------------------------------------
# Fallback data tables
# ---------------------------------------------------------------------------
# ``daily_launches`` is the average number of times per active day this exe runs.
# Active days ≈ 71% of timeline_days (5/7 weekdays).

_PREFETCH_APPS_BY_PROFILE = {
    "developer": [
        {"exe": "CODE.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT VS CODE\CODE.EXE", "daily_launches": 3.5},
        {"exe": "POWERSHELL.EXE", "path": r"C:\WINDOWS\SYSTEM32\WINDOWSPOWERSHELL\V1.0\POWERSHELL.EXE", "daily_launches": 8.0},
        {"exe": "WT.EXE", "path": r"C:\PROGRAM FILES\WINDOWSAPPS\MICROSOFT.WINDOWSTERMINAL_1.19.10821.0_X64__8WEKYB3D8BBWE\WT.EXE", "daily_launches": 6.0},
        {"exe": "CHROME.EXE", "path": r"C:\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME.EXE", "daily_launches": 5.0},
        {"exe": "GIT.EXE", "path": r"C:\PROGRAM FILES\GIT\CMD\GIT.EXE", "daily_launches": 12.0},
        {"exe": "PYTHON.EXE", "path": r"C:\PROGRAM FILES\PYTHON311\PYTHON.EXE", "daily_launches": 6.5},
        {"exe": "PYTHON3.11.EXE", "path": r"C:\PROGRAM FILES\PYTHON311\PYTHON3.11.EXE", "daily_launches": 4.0},
        {"exe": "NODE.EXE", "path": r"C:\PROGRAM FILES\NODEJS\NODE.EXE", "daily_launches": 3.0},
        {"exe": "NPM.CMD", "path": r"C:\PROGRAM FILES\NODEJS\NPM.CMD", "daily_launches": 2.5},
        {"exe": "DOCKER.EXE", "path": r"C:\PROGRAM FILES\DOCKER\DOCKER\RESOURCES\BIN\DOCKER.EXE", "daily_launches": 4.0},
        {"exe": "DOCKERD.EXE", "path": r"C:\PROGRAM FILES\DOCKER\DOCKER\RESOURCES\DOCKERD.EXE", "daily_launches": 1.0},
        {"exe": "OUTLOOK.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OUTLOOK.EXE", "daily_launches": 2.0},
        {"exe": "SLACK.EXE", "path": r"C:\PROGRAM FILES\SLACK\SLACK.EXE", "daily_launches": 2.5},
        {"exe": "EXPLORER.EXE", "path": r"C:\WINDOWS\EXPLORER.EXE", "daily_launches": 3.0},
        {"exe": "TASKMGR.EXE", "path": r"C:\WINDOWS\SYSTEM32\TASKMGR.EXE", "daily_launches": 1.0},
        {"exe": "CMD.EXE", "path": r"C:\WINDOWS\SYSTEM32\CMD.EXE", "daily_launches": 4.0},
        {"exe": "POSTMAN.EXE", "path": r"C:\PROGRAM FILES\POSTMAN\POSTMAN.EXE", "daily_launches": 1.5},
        {"exe": "WINRAR.EXE", "path": r"C:\PROGRAM FILES\WINRAR\WINRAR.EXE", "daily_launches": 0.3},
        {"exe": "NOTEPAD.EXE", "path": r"C:\WINDOWS\SYSTEM32\NOTEPAD.EXE", "daily_launches": 1.5},
        {"exe": "MSIEXEC.EXE", "path": r"C:\WINDOWS\SYSTEM32\MSIEXEC.EXE", "daily_launches": 0.1},
        {"exe": "DISM.EXE", "path": r"C:\WINDOWS\SYSTEM32\DISM.EXE", "daily_launches": 0.05},
        {"exe": "SVCHOST.EXE", "path": r"C:\WINDOWS\SYSTEM32\SVCHOST.EXE", "daily_launches": 1.0},
        {"exe": "CONHOST.EXE", "path": r"C:\WINDOWS\SYSTEM32\CONHOST.EXE", "daily_launches": 8.0},
        {"exe": "7ZFM.EXE", "path": r"C:\PROGRAM FILES\7-ZIP\7ZFM.EXE", "daily_launches": 0.4},
    ],
    "office_user": [
        {"exe": "WINWORD.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\WINWORD.EXE", "daily_launches": 4.0},
        {"exe": "EXCEL.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\EXCEL.EXE", "daily_launches": 3.5},
        {"exe": "OUTLOOK.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OUTLOOK.EXE", "daily_launches": 7.0},
        {"exe": "POWERPNT.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\POWERPNT.EXE", "daily_launches": 1.5},
        {"exe": "TEAMS.EXE", "path": r"C:\PROGRAM FILES\WINDOWSAPPS\MSTEAMS_24004.1415.2866.7070_X64__8WEKYB3D8BBWE\MS-TEAMS.EXE", "daily_launches": 5.0},
        {"exe": "CHROME.EXE", "path": r"C:\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME.EXE", "daily_launches": 6.0},
        {"exe": "MSEDGE.EXE", "path": r"C:\PROGRAM FILES (X86)\MICROSOFT\EDGE\APPLICATION\MSEDGE.EXE", "daily_launches": 3.0},
        {"exe": "ZOOM.EXE", "path": r"C:\PROGRAM FILES\ZOOM\BIN\ZOOM.EXE", "daily_launches": 2.0},
        {"exe": "SLACK.EXE", "path": r"C:\PROGRAM FILES\SLACK\SLACK.EXE", "daily_launches": 2.5},
        {"exe": "ACROBAT.EXE", "path": r"C:\PROGRAM FILES\ADOBE\ACROBAT DC\ACROBAT\ACROBAT.EXE", "daily_launches": 1.0},
        {"exe": "EXPLORER.EXE", "path": r"C:\WINDOWS\EXPLORER.EXE", "daily_launches": 3.0},
        {"exe": "ONENOTE.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\ONENOTE.EXE", "daily_launches": 1.5},
        {"exe": "NOTEPAD.EXE", "path": r"C:\WINDOWS\SYSTEM32\NOTEPAD.EXE", "daily_launches": 1.0},
        {"exe": "TASKMGR.EXE", "path": r"C:\WINDOWS\SYSTEM32\TASKMGR.EXE", "daily_launches": 0.5},
        {"exe": "POWERSHELL.EXE", "path": r"C:\WINDOWS\SYSTEM32\WINDOWSPOWERSHELL\V1.0\POWERSHELL.EXE", "daily_launches": 1.0},
        {"exe": "SVCHOST.EXE", "path": r"C:\WINDOWS\SYSTEM32\SVCHOST.EXE", "daily_launches": 1.0},
        {"exe": "7ZFM.EXE", "path": r"C:\PROGRAM FILES\7-ZIP\7ZFM.EXE", "daily_launches": 0.3},
        {"exe": "MSIEXEC.EXE", "path": r"C:\WINDOWS\SYSTEM32\MSIEXEC.EXE", "daily_launches": 0.05},
    ],
    "home_user": [
        {"exe": "CHROME.EXE", "path": r"C:\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME.EXE", "daily_launches": 8.0},
        {"exe": "MSEDGE.EXE", "path": r"C:\PROGRAM FILES (X86)\MICROSOFT\EDGE\APPLICATION\MSEDGE.EXE", "daily_launches": 2.5},
        {"exe": "EXPLORER.EXE", "path": r"C:\WINDOWS\EXPLORER.EXE", "daily_launches": 4.0},
        {"exe": "VLC.EXE", "path": r"C:\PROGRAM FILES\VIDEOLAN\VLC\VLC.EXE", "daily_launches": 1.5},
        {"exe": "SPOTIFY.EXE", "path": r"C:\USERS\PUBLIC\APPDATA\ROAMING\SPOTIFY\SPOTIFY.EXE", "daily_launches": 2.5},
        {"exe": "DISCORD.EXE", "path": r"C:\USERS\PUBLIC\APPDATA\ROAMING\DISCORD\DISCORD.EXE", "daily_launches": 3.0},
        {"exe": "STEAM.EXE", "path": r"C:\PROGRAM FILES (X86)\STEAM\STEAM.EXE", "daily_launches": 2.0},
        {"exe": "NOTEPAD.EXE", "path": r"C:\WINDOWS\SYSTEM32\NOTEPAD.EXE", "daily_launches": 1.0},
        {"exe": "WINWORD.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\WINWORD.EXE", "daily_launches": 0.5},
        {"exe": "EXCEL.EXE", "path": r"C:\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\EXCEL.EXE", "daily_launches": 0.4},
        {"exe": "TASKMGR.EXE", "path": r"C:\WINDOWS\SYSTEM32\TASKMGR.EXE", "daily_launches": 0.4},
        {"exe": "ACROBAT.EXE", "path": r"C:\PROGRAM FILES\ADOBE\ACROBAT DC\ACROBAT\ACROBAT.EXE", "daily_launches": 0.5},
        {"exe": "7ZFM.EXE", "path": r"C:\PROGRAM FILES\7-ZIP\7ZFM.EXE", "daily_launches": 0.3},
        {"exe": "SVCHOST.EXE", "path": r"C:\WINDOWS\SYSTEM32\SVCHOST.EXE", "daily_launches": 1.0},
        {"exe": "MSIEXEC.EXE", "path": r"C:\WINDOWS\SYSTEM32\MSIEXEC.EXE", "daily_launches": 0.05},
        {"exe": "POWERSHELL.EXE", "path": r"C:\WINDOWS\SYSTEM32\WINDOWSPOWERSHELL\V1.0\POWERSHELL.EXE", "daily_launches": 0.5},
    ],
}
