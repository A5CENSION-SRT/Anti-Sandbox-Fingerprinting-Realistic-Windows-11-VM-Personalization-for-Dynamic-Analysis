"""EVTX (Windows Event Log) seed generator.

Produces :class:`EvtxSeed` objects that drive the application event log
service with persona-specific application install events, crash events,
scheduled task completions, and service errors.

Falls back to rich offline seeds when Gemini is unavailable.

Consumed by: ``services/eventlog/application_log.py``
"""

from __future__ import annotations

import logging
from typing import List, Optional

from services.ai.gemini_client import GeminiClient, GeminiClientError
from services.ai.schemas import (
    PersonaContext,
    EvtxSeed,
    EvtxEventStub,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "prompts"
_EVTX_PROMPT_FILE = _PROMPTS_DIR / "evtx_events.txt"


class EvtxSeedGenerationError(Exception):
    """Raised when EVTX seed generation fails."""


class EvtxSeedGenerator:
    """Generates Application event log seeds via Gemini with offline fallback.

    Args:
        client:        Configured GeminiClient (may be None → always fallback).
        timeline_days: Days of history to spread events across.
    """

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        timeline_days: int = 360,
    ) -> None:
        self._client = client
        self._timeline_days = timeline_days
        self._prompt_template = (
            _EVTX_PROMPT_FILE.read_text(encoding="utf-8")
            if _EVTX_PROMPT_FILE.exists()
            else None
        )

    def generate(self, persona: PersonaContext, use_cache: bool = True) -> EvtxSeed:
        """Generate EVTX seeds, falling back to offline data on error."""
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
                    schema=EvtxSeed,
                    temperature=0.65,
                    use_cache=use_cache,
                )
                logger.info("Generated EVTX seed via Gemini for %s", persona.full_name)
                return result
            except (GeminiClientError, Exception) as exc:
                logger.warning("Gemini EVTX seed failed (%s); using fallback", exc)

        return self._generate_fallback(persona)

    def _generate_fallback(self, persona: PersonaContext) -> EvtxSeed:
        archetype = getattr(persona, "profile_archetype", "office_user")

        events = []
        # Application install events (EID 11707 = installed successfully)
        events.extend(_install_events(archetype))
        # Application crash events (EID 1000)
        events.extend(_crash_events(archetype))
        # Scheduled task completion events (EID 200 in Task Scheduler)
        events.extend(_scheduled_task_events(archetype))
        # Windows Update events (EID 19 = successful install, EID 43 = started)
        events.extend(_windows_update_events())

        return EvtxSeed(
            seed_id=f"evtx_{archetype}",
            context=f"Application event log seeds for {archetype}",
            application_events=events,
        )


# ---------------------------------------------------------------------------
# Event stub builders
# ---------------------------------------------------------------------------

def _install_events(archetype: str) -> List[EvtxEventStub]:
    apps = _INSTALLED_APPS_BY_PROFILE.get(archetype, _INSTALLED_APPS_BY_PROFILE["office_user"])
    events = []
    for app in apps:
        events.append(EvtxEventStub(
            event_id=11707,
            provider="MsiInstaller",
            level=4,
            description=f"Product: {app['name']} -- Installation operation completed successfully.",
            data={"ProductName": app["name"], "ProductVersion": app["version"]},
            recurrence_days=None,  # one-time
        ))
    return events


def _crash_events(archetype: str) -> List[EvtxEventStub]:
    crashable = _CRASHABLE_APPS_BY_PROFILE.get(archetype, [])
    events = []
    for app in crashable:
        events.append(EvtxEventStub(
            event_id=1000,
            provider="Application Error",
            level=2,
            description=(
                f"Faulting application name: {app['exe']}, version: {app['version']}, "
                f"time stamp: 0x{app['ts']:08x}\n"
                f"Faulting module name: {app['module']}, version: {app['module_ver']}, "
                f"time stamp: 0x{app['module_ts']:08x}\n"
                f"Exception code: 0x{app['exception']:08x}"
            ),
            data={
                "FaultingApplication": app["exe"],
                "FaultingApplicationVersion": app["version"],
                "FaultingModule": app["module"],
            },
            recurrence_days=30,
        ))
    return events


def _scheduled_task_events(archetype: str) -> List[EvtxEventStub]:
    tasks = _SCHEDULED_TASKS_BY_PROFILE.get(archetype, _SCHEDULED_TASKS_BY_PROFILE["office_user"])
    events = []
    for task in tasks:
        events.append(EvtxEventStub(
            event_id=200,
            provider="Microsoft-Windows-TaskScheduler",
            level=4,
            description=f"Task Scheduler launched action '{task['action']}' in task '{task['name']}'.",
            data={"TaskName": task["name"], "ActionName": task["action"]},
            recurrence_days=task.get("recurrence_days", 7),
        ))
    return events


def _windows_update_events() -> List[EvtxEventStub]:
    updates = [
        ("2024-KB5034441", "Security Update for Windows 11"),
        ("2024-KB5034765", "Cumulative Update for Windows 11 Version 23H2"),
        ("2024-KB5033375", "Cumulative Update for .NET Framework"),
        ("2024-KB890830", "Windows Malicious Software Removal Tool"),
        ("2024-KB5032192", "Security Update for Microsoft Defender Antivirus"),
    ]
    events = []
    for kb, desc in updates:
        events.append(EvtxEventStub(
            event_id=19,
            provider="Microsoft-Windows-WindowsUpdateClient",
            level=4,
            description=f"Installation Successful: Windows successfully installed the following update: {desc} ({kb})",
            data={"UpdateTitle": desc, "UpdateGuid": kb},
            recurrence_days=30,
        ))
    return events


# ---------------------------------------------------------------------------
# Fallback data tables
# ---------------------------------------------------------------------------

_INSTALLED_APPS_BY_PROFILE = {
    "developer": [
        {"name": "Microsoft Visual Studio Code", "version": "1.85.1"},
        {"name": "Git version 2.43.0", "version": "2.43.0"},
        {"name": "Docker Desktop", "version": "4.26.1"},
        {"name": "Python 3.11.7", "version": "3.11.7"},
        {"name": "Node.js", "version": "20.11.0"},
        {"name": "Postman", "version": "10.22.0"},
        {"name": "Microsoft Windows Terminal", "version": "1.19.10821"},
        {"name": "PowerShell 7.4.0", "version": "7.4.0"},
    ],
    "office_user": [
        {"name": "Microsoft Office Professional Plus 2021", "version": "16.0.14332.20291"},
        {"name": "Microsoft Teams", "version": "1.6.00.31460"},
        {"name": "Zoom", "version": "5.17.1"},
        {"name": "Adobe Acrobat Reader DC", "version": "23.008.20533"},
        {"name": "7-Zip 23.01", "version": "23.01"},
        {"name": "Google Chrome", "version": "120.0.6099.130"},
        {"name": "Slack", "version": "4.36.140"},
    ],
    "home_user": [
        {"name": "Google Chrome", "version": "120.0.6099.130"},
        {"name": "VLC media player", "version": "3.0.20"},
        {"name": "7-Zip 23.01", "version": "23.01"},
        {"name": "Adobe Acrobat Reader DC", "version": "23.008.20533"},
        {"name": "Spotify", "version": "1.2.26.1187"},
        {"name": "Steam", "version": "2.10.91.91"},
        {"name": "Discord", "version": "1.0.9028"},
    ],
}

_CRASHABLE_APPS_BY_PROFILE = {
    "developer": [
        {"exe": "Code.exe", "version": "1.85.1.0", "ts": 0x65a12345,
         "module": "node.dll", "module_ver": "18.18.2.0", "module_ts": 0x6512abcd,
         "exception": 0xc0000005},
        {"exe": "docker.exe", "version": "4.26.1.0", "ts": 0x65891234,
         "module": "ntdll.dll", "module_ver": "10.0.22621.2506", "module_ts": 0x64f00000,
         "exception": 0xc000001d},
    ],
    "office_user": [
        {"exe": "OUTLOOK.EXE", "version": "16.0.14332.20291", "ts": 0x65234567,
         "module": "mso40uitools.dll", "module_ver": "16.0.14332.20264", "module_ts": 0x65100000,
         "exception": 0xc0000005},
        {"exe": "Teams.exe", "version": "1.6.00.31460", "ts": 0x65987654,
         "module": "KERNELBASE.dll", "module_ver": "10.0.22621.2506", "module_ts": 0x64f00000,
         "exception": 0xe0434352},
    ],
    "home_user": [
        {"exe": "chrome.exe", "version": "120.0.6099.130", "ts": 0x65765432,
         "module": "chrome.dll", "module_ver": "120.0.6099.130", "module_ts": 0x65760000,
         "exception": 0xc0000005},
    ],
}

_SCHEDULED_TASKS_BY_PROFILE = {
    "developer": [
        {"name": r"\Microsoft\Windows\UpdateOrchestrator\Schedule Scan", "action": "USO Client", "recurrence_days": 1},
        {"name": r"\Microsoft\Windows\Defrag\ScheduledDefrag", "action": "defrag.exe", "recurrence_days": 7},
        {"name": r"\Docker Desktop Update Task", "action": "Update.exe", "recurrence_days": 14},
        {"name": r"\GoogleUpdate\GoogleUpdateTaskMachineCore", "action": "GoogleUpdate.exe", "recurrence_days": 1},
        {"name": r"\Microsoft\Windows\Windows Defender\Windows Defender Cache Maintenance", "action": "MpCmdRun.exe", "recurrence_days": 1},
    ],
    "office_user": [
        {"name": r"\Microsoft\Windows\UpdateOrchestrator\Schedule Scan", "action": "USO Client", "recurrence_days": 1},
        {"name": r"\Microsoft\Office\Office Automatic Updates 2.0", "action": "officesvcmgr.exe", "recurrence_days": 1},
        {"name": r"\GoogleUpdate\GoogleUpdateTaskMachineUA", "action": "GoogleUpdate.exe", "recurrence_days": 1},
        {"name": r"\Microsoft\Windows\Defrag\ScheduledDefrag", "action": "defrag.exe", "recurrence_days": 7},
        {"name": r"\CCleaner\CCleaner Smart Cleaning", "action": "CCleaner64.exe", "recurrence_days": 3},
    ],
    "home_user": [
        {"name": r"\Microsoft\Windows\UpdateOrchestrator\Schedule Scan", "action": "USO Client", "recurrence_days": 1},
        {"name": r"\GoogleUpdate\GoogleUpdateTaskMachineUA", "action": "GoogleUpdate.exe", "recurrence_days": 1},
        {"name": r"\Spotify\Spotify Update", "action": "SpotifyUpdate.exe", "recurrence_days": 3},
        {"name": r"\Microsoft\Windows\Defrag\ScheduledDefrag", "action": "defrag.exe", "recurrence_days": 7},
        {"name": r"\Microsoft\Windows\Windows Defender\Windows Defender Cache Maintenance", "action": "MpCmdRun.exe", "recurrence_days": 1},
    ],
}
