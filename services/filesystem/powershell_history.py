"""PowerShell ConsoleHost_history.txt generator.

Creates a realistic PowerShell command history file at:
    ``%APPDATA%\\Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt``

PSReadLine writes one command per line in plain UTF-8.  The history is
limited to 4096 entries by default (PSReadLine MaximumHistoryCount).
Forensic tools (Autopsy, KAPE) parse this to determine operator skill level
and activity scope.

Commands are selected from profile-appropriate pools:
- office_user: Get-ChildItem, Copy-Item, basic file ops, gpupdate
- developer: git, docker, python, npm, kubectl, az cli
- home_user: dir, Get-ChildItem, ipconfig, basic troubleshooting
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)

_PS_HISTORY_REL: str = (
    "Users/{username}/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine"
)
_HISTORY_FILE: str = "ConsoleHost_history.txt"
_MAX_HISTORY: int = 4096

# Command pools by profile archetype
_COMMON_COMMANDS: List[str] = [
    "Get-ChildItem",
    "Get-ChildItem -Recurse",
    "Get-ChildItem C:\\Users\\{username}\\Documents",
    "Set-Location Documents",
    "Set-Location Desktop",
    "Get-Date",
    "Get-Process",
    "Get-Service",
    "Get-Service | Where-Object Status -eq Running",
    "Get-EventLog -LogName System -Newest 20",
    "Start-Process notepad.exe",
    "Copy-Item .\\report.docx ..\\archive\\",
    "Remove-Item .\\tmp\\*",
    "ipconfig",
    "ipconfig /all",
    "ping 8.8.8.8",
    "nslookup google.com",
    "Test-NetConnection -ComputerName google.com -Port 443",
    "Get-NetAdapter",
    "Get-NetIPAddress",
    "Restart-Service -Name Spooler",
    "Get-WmiObject Win32_DiskDrive",
    "Get-WmiObject Win32_OperatingSystem",
    "systeminfo",
    "whoami",
    "hostname",
    "netstat -an",
    "tasklist",
    "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5",
    "winver",
    "cls",
    "cd ~",
    "dir",
    "dir /s /b *.log",
]

_OFFICE_COMMANDS: List[str] = [
    "Get-ChildItem \\\\server\\shares",
    "net use Z: \\\\fileserver\\department /persistent:yes",
    "gpupdate /force",
    "Get-ADUser -Identity {username}",
    "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned",
    "Get-Module -ListAvailable",
    "Import-Module ActiveDirectory",
    "Get-ADGroupMember -Identity IT-Staff",
    "Send-MailMessage -To manager@company.com -Subject 'Report' -SmtpServer mail.company.com",
    "Export-Csv -Path .\\users.csv -NoTypeInformation",
    "Import-Csv .\\employees.csv | Select-Object Name, Department",
    "ConvertTo-Json | Out-File .\\config.json",
    "Invoke-WebRequest -Uri https://update.company.com/latest -OutFile update.msi",
    "Start-Process msiexec -ArgumentList '/i update.msi /quiet' -Wait",
    "Get-ScheduledTask | Where-Object TaskName -like '*Update*'",
    "Register-ScheduledTask -TaskName BackupTask -Trigger (New-ScheduledTaskTrigger -Daily -At 2am)",
    "robocopy C:\\Users\\{username}\\Documents \\\\backup\\{username}\\Documents /MIR",
    "wmic product get name,version",
]

_DEVELOPER_COMMANDS: List[str] = [
    "git status",
    "git log --oneline -10",
    "git pull origin main",
    "git push origin feature/new-api",
    "git checkout -b fix/bug-123",
    "git stash",
    "git stash pop",
    "git diff HEAD~1",
    "docker ps",
    "docker ps -a",
    "docker images",
    "docker-compose up -d",
    "docker-compose logs -f api",
    "docker exec -it api-container bash",
    "docker build -t myapp:latest .",
    "npm install",
    "npm run build",
    "npm run test",
    "npm run dev",
    "python -m pytest tests/ -v",
    "python -m pip install -r requirements.txt",
    "python manage.py migrate",
    "python manage.py runserver",
    "kubectl get pods",
    "kubectl get pods -n production",
    "kubectl logs deployment/api -f",
    "kubectl apply -f k8s/deployment.yaml",
    "kubectl rollout status deployment/api",
    "az login",
    "az account list",
    "az webapp list",
    "code .",
    "wsl",
    "ssh ubuntu@10.0.0.5",
    "cat .env | grep DATABASE",
    "openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes",
    "netsh interface portproxy add v4tov4 listenport=3000 connectaddress=localhost connectport=8000",
]

_HOME_COMMANDS: List[str] = [
    "Get-ChildItem C:\\Users\\{username}\\Pictures -Recurse | Measure-Object",
    "Get-ChildItem C:\\Users\\{username}\\Downloads",
    "Remove-Item C:\\Users\\{username}\\Downloads\\*.tmp",
    "Start-Process ms-settings:",
    "Start-Process windowsupdate:",
    "Repair-Volume C: -Scan",
    "Optimize-Volume -DriveLetter C -Analyze",
    "Get-WinSystemLocale",
    "Set-WinSystemLocale en-US",
    "Get-TimeZone",
    "Set-TimeZone -Name 'Eastern Standard Time'",
    "Get-AppxPackage | Where-Object Name -like '*Netflix*'",
    "chkdsk C: /f",
    "sfc /scannow",
    "DISM.exe /Online /Cleanup-Image /RestoreHealth",
    "winget upgrade --all",
    "winget install 7zip.7zip",
    "msconfig",
]

_PROFILE_EXTRA: Dict[str, List[str]] = {
    "office_user": _OFFICE_COMMANDS,
    "developer": _DEVELOPER_COMMANDS,
    "home_user": _HOME_COMMANDS,
}


class PowerShellHistoryError(Exception):
    """Raised when PowerShell history generation fails."""


class PowerShellHistoryService(BaseService):
    """Generates a realistic ConsoleHost_history.txt for the user profile.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger:  Shared audit logger.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "PowerShellHistoryService"

    def apply(self, ctx: "ServiceContext") -> None:
        """Generate ConsoleHost_history.txt.

        Raises:
            PowerShellHistoryError: On write failure.
        """
        username = ctx.identity_bundle.user.username
        profile_type = ctx.persona.profile_archetype

        rng = (
            ctx.scheduler.child_rng("PowerShellHistory")
            if ctx.scheduler
            else Random(hash(username + "ps_history"))
        )

        entry_count = rng.randint(200, min(_MAX_HISTORY, 800))
        content = self.build_history(username, profile_type, entry_count, rng)

        rel_dir = Path(_PS_HISTORY_REL.format(username=username))
        rel_file = rel_dir / _HISTORY_FILE

        try:
            full_dir = self._mount.resolve(str(rel_dir))
            full_dir.mkdir(parents=True, exist_ok=True)
            full_path = self._mount.resolve(str(rel_file))
            full_path.write_bytes(content.encode("utf-8"))
        except Exception as exc:
            raise PowerShellHistoryError(
                f"Failed to write PowerShell history: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_ps_history",
            "username": username,
            "profile_type": profile_type,
            "entry_count": entry_count,
        })
        logger.info(
            "PowerShell history written: %d entries for %s (%s)",
            entry_count, username, profile_type,
        )

    def build_history(
        self,
        username: str,
        profile_type: str,
        entry_count: int,
        rng: Random,
    ) -> str:
        """Build ConsoleHost_history.txt content.

        Pure function — suitable for isolated testing.

        Args:
            username:     Windows username (substituted into commands).
            profile_type: Profile archetype for command pool selection.
            entry_count:  Number of command history lines to generate.
            rng:          RNG for command selection.

        Returns:
            UTF-8 text content for ConsoleHost_history.txt.
        """
        pool = _COMMON_COMMANDS + _PROFILE_EXTRA.get(profile_type, [])
        lines: List[str] = []

        for _ in range(entry_count):
            cmd = rng.choice(pool).replace("{username}", username)
            lines.append(cmd)

        return "\r\n".join(lines) + "\r\n"
