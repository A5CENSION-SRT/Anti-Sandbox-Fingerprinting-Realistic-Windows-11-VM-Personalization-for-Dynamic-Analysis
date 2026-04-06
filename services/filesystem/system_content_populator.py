"""System content populator for empty Windows directories.

Fills system-level and user-level directories that would never be empty on
a real Windows 11 installation.  This addresses the most obvious sandbox
fingerprint: a directory tree full of empty folders.

The service runs in the INFRASTRUCTURE phase (after UserDirectoryService)
and populates:
- Windows system directories (Fonts, Temp, Logs, INF, etc.)
- Users/Public directories
- AppData subdirectories (SendTo, Templates, Themes, Libraries, etc.)
- Browser profile cache/storage subdirectories
"""

from __future__ import annotations

import hashlib
import io
import logging
import struct
import zipfile
from pathlib import Path
from random import Random
from typing import Any, Dict, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal valid TrueType font header (enough for `file` to identify as TTF)
# ---------------------------------------------------------------------------

def _ttf_stub(size_kb: int = 16) -> bytes:
    """Build a minimal valid TrueType font stub."""
    header = bytearray(64)
    # Offset table
    struct.pack_into(">I", header, 0, 0x00010000)   # sfVersion (TrueType)
    struct.pack_into(">H", header, 4, 1)             # numTables
    struct.pack_into(">H", header, 6, 16)            # searchRange
    struct.pack_into(">H", header, 8, 0)             # entrySelector
    struct.pack_into(">H", header, 10, 16)           # rangeShift
    # Table record for 'name'
    header[12:16] = b"name"
    struct.pack_into(">I", header, 16, 0)            # checkSum
    struct.pack_into(">I", header, 20, 64)           # offset
    struct.pack_into(">I", header, 24, 32)           # length
    target = max(size_kb * 1024, 128)
    return bytes(header) + b"\x00" * (target - len(header))


# ---------------------------------------------------------------------------
# Minimal MSI header (OLE/CFB format)
# ---------------------------------------------------------------------------

def _msi_stub(size_kb: int = 32) -> bytes:
    """Build a minimal OLE/CFB file header (used for .msi)."""
    header = bytearray(512)
    header[0:8] = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 0x0003)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    target = max(size_kb * 1024, 1024)
    return bytes(header) + b"\x00" * (target - len(header))


# ---------------------------------------------------------------------------
# Standard library XML definitions
# ---------------------------------------------------------------------------

_LIBRARY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<libraryDescription xmlns="http://schemas.microsoft.com/windows/2009/library">
  <name>@shell32.dll,-{res_id}</name>
  <version>1</version>
  <isLibraryPinned>true</isLibraryPinned>
  <iconReference>imageres.dll,-{icon_id}</iconReference>
  <templateInfo>
    <folderType>{{{folder_type}}}</folderType>
  </templateInfo>
  <searchConnectorDescriptionList>
    <searchConnectorDescription>
      <isDefaultSaveLocation>true</isDefaultSaveLocation>
      <simpleLocation>
        <url>knownfolder:{{{known_folder}}}</url>
      </simpleLocation>
    </searchConnectorDescription>
  </searchConnectorDescriptionList>
</libraryDescription>"""

_LIBRARIES = {
    "Documents.library-ms": {
        "res_id": "34575",
        "icon_id": "112",
        "folder_type": "7d49d726-3c21-4f05-99aa-fdc2c9474656",
        "known_folder": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    },
    "Music.library-ms": {
        "res_id": "34576",
        "icon_id": "108",
        "folder_type": "94d6ddcc-4a68-4175-a374-bd584a510b78",
        "known_folder": "4BD8D571-6D19-48D3-BE97-422220080E43",
    },
    "Pictures.library-ms": {
        "res_id": "34577",
        "icon_id": "113",
        "folder_type": "b3690e58-e961-423b-b687-386ebfd83239",
        "known_folder": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    },
    "Videos.library-ms": {
        "res_id": "34578",
        "icon_id": "189",
        "folder_type": "5fa96407-7e77-483c-ac93-691d05850de8",
        "known_folder": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    },
}

# ---------------------------------------------------------------------------
# Scheduled task XML template
# ---------------------------------------------------------------------------

_TASK_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\Microsoft\\Windows\\{task_path}</URI>
    <SecurityDescriptor>D:(A;;FA;;;BA)(A;;FA;;;SY)</SecurityDescriptor>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2024-01-01T03:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
  </Settings>
  <Actions>
    <Exec>
      <Command>{command}</Command>
    </Exec>
  </Actions>
</Task>"""

_SCHEDULED_TASKS = [
    {
        "path": "UpdateOrchestrator/Schedule Scan",
        "dirs": "Microsoft/Windows/UpdateOrchestrator",
        "command": "%systemroot%\\system32\\usoclient.exe StartScan",
    },
    {
        "path": "WindowsUpdate/Scheduled Start",
        "dirs": "Microsoft/Windows/WindowsUpdate",
        "command": "%systemroot%\\system32\\wuauclt.exe /RunHandlerComServer",
    },
    {
        "path": "DiskCleanup/SilentCleanup",
        "dirs": "Microsoft/Windows/DiskCleanup",
        "command": "%SystemRoot%\\system32\\cleanmgr.exe /autoclean /d %systemdrive%",
    },
    {
        "path": "Defrag/ScheduledDefrag",
        "dirs": "Microsoft/Windows/Defrag",
        "command": "%windir%\\system32\\defrag.exe -c -h -o -$",
    },
]


# ---------------------------------------------------------------------------
# CBS log stub content
# ---------------------------------------------------------------------------

_CBS_LOG_CONTENT = """\
{ts}  Info                  CBS    Loaded Servicing Stack v10.0.22621.1 with Core: C:\\Windows\\winsxs\\amd64_microsoft-windows-servicingstack_31bf3856ad364e35_10.0.22621.1_none_5e5e5e5e5e5e5e5e\\cbscore.dll
{ts}  Info                  CBS    Startup Processing
{ts}  Info                  CBS    Session: 30786688_3584889167 initialized by client WindowsUpdateAgent.
{ts}  Info                  CBS    Appl: Package KB5034441 state: Installed
{ts}  Info                  CBS    Appl: Package KB5034765 state: Installed
{ts}  Info                  CBS    Session: 30786688_3584889167 finalized. Reboot required: no
"""

_DISM_LOG_CONTENT = """\
DISM   DISM.EXE
DISM   DISM.EXE version 10.0.22621.1
DISM   DISM Provider Store: PID=4688 TID=7124 Getting Provider DismPkg - CDISMProviderStore::GetProvider
DISM   DISM Package Manager: PID=4688 TID=7124 Initializing Package Manager. - CPackageManager::Initialize
DISM   DISM Package Manager: PID=4688 Loaded servicing stack for online image.
DISM   DISM Package Manager: PID=4688 No reboot required.
"""


# ---------------------------------------------------------------------------
# INF driver stub
# ---------------------------------------------------------------------------

_INF_CONTENT = """\
; {name}
; Copyright (c) Microsoft Corporation. All rights reserved.

[Version]
Signature   = "$Windows NT$"
Class       = {cls}
ClassGuid   = {{{guid}}}
Provider    = %Msft%
DriverVer   = 06/21/2006,10.0.22621.1

[Strings]
Msft        = "Microsoft"
"""

_INF_FILES = [
    {"name": "usbport.inf", "cls": "USB", "guid": "36FC9E60-C465-11CF-8056-444553540000"},
    {"name": "netrtl64.inf", "cls": "Net", "guid": "4D36E972-E325-11CE-BFC1-08002BE10318"},
    {"name": "hdaudio.inf", "cls": "MEDIA", "guid": "4D36E96C-E325-11CE-BFC1-08002BE10318"},
]


# ---------------------------------------------------------------------------
# SendTo shortcuts content (simplified .lnk stubs)
# ---------------------------------------------------------------------------

def _sendto_lnk_stub(target_clsid: str) -> bytes:
    """Minimal .lnk pointing at a Shell CLSID."""
    header = bytearray(76)
    header[0:4] = b"\x4C\x00\x00\x00"
    header[4:20] = bytes([
        0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0xC0, 0x00, 0x00, 0x46,
    ])
    struct.pack_into("<I", header, 20, 0x00000001)  # HasLinkTargetIDList
    return bytes(header) + b"\x00" * 128


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SystemContentPopulatorError(Exception):
    """Raised when content population fails."""


class SystemContentPopulator(BaseService):
    """Fills empty system and user directories with realistic content.

    This service addresses the #1 sandbox fingerprint: empty directories
    that would never be empty on a real Windows installation.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        timestamp_service: Provides timestamps for file operations.
        audit_logger: Structured audit logging.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "SystemContentPopulator"

    def apply(self, context: dict) -> None:
        """Populate empty directories with realistic content.

        Runs in EVALUATION phase (last) so every other service has
        already created its directories and files.  After targeted
        population a final sweep fills any dirs still empty.

        Args:
            context: Runtime context with ``username``, ``profile_type``.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)
        rng = Random(hash(seed + "syscontent"))

        created = 0

        try:
            created += self._populate_windows_system(rng)
            created += self._populate_user_shell_folders(username, profile_type, rng)
            created += self._populate_user_appdata(username, rng)
            created += self._populate_public_dirs(rng)
            created += self._populate_scheduled_tasks(rng)
            created += self._populate_browser_caches(username, profile_type, rng)
            created += self._populate_dev_tool_configs(username, profile_type, rng)
            created += self._populate_app_caches(username, profile_type, rng)
            created += self._populate_programdata(rng)

            # Final sweep: fill ANY remaining empty dirs
            created += self._sweep_remaining_empty_dirs()

            self._audit.log({
                "service": self.service_name,
                "operation": "populate_empty_dirs",
                "username": username,
                "files_created": created,
            })

            logger.info(
                "SystemContentPopulator: created %d files to fill empty directories",
                created,
            )

        except Exception as exc:
            logger.error("SystemContentPopulator failed: %s", exc)
            raise SystemContentPopulatorError(
                f"Content population failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Windows system directories
    # ------------------------------------------------------------------

    def _populate_windows_system(self, rng: Random) -> int:
        """Fill Windows/Fonts, Temp, Logs, INF, Installer, SysWOW64, WinSxS, etc."""
        created = 0

        # --- Windows/Fonts ---
        fonts = [
            ("arial.ttf", 16), ("arialbd.ttf", 16), ("times.ttf", 16),
            ("calibri.ttf", 24), ("calibrib.ttf", 24),
            ("segoeui.ttf", 32), ("segoeuib.ttf", 32),
            ("consola.ttf", 16), ("consolab.ttf", 16),
            ("verdana.ttf", 16), ("tahoma.ttf", 16),
        ]
        for fname, size_kb in fonts:
            path = Path("Windows/Fonts") / fname
            self._write_bytes(path, _ttf_stub(size_kb), "system_file")
            created += 1

        # --- Windows/Temp ---
        for i in range(rng.randint(150, 200)):
            tmp_name = f"tmp{rng.randint(1000, 99999):05X}.tmp"
            size = rng.randint(32, 512)
            content = bytes(rng.getrandbits(8) for _ in range(size))
            self._write_bytes(Path("Windows/Temp") / tmp_name, content, "system_file")
            created += 1

        # --- Windows/Logs ---
        ts_str = "2024-10-15 08:32:01"
        cbs_content = _CBS_LOG_CONTENT.format(ts=ts_str)
        cbs_dir = Path("Windows/Logs/CBS")
        self._write_text(cbs_dir / "CBS.log", cbs_content, "system_file")
        created += 1

        dism_dir = Path("Windows/Logs/DISM")
        self._write_text(dism_dir / "dism.log", _DISM_LOG_CONTENT, "system_file")
        created += 1

        # --- Windows/INF ---
        for inf in _INF_FILES:
            content = _INF_CONTENT.format(**inf)
            self._write_text(Path("Windows/INF") / inf["name"], content, "system_file")
            created += 1

        # --- Windows/Installer ---
        for i in range(rng.randint(1, 3)):
            guid = f"{rng.randint(0, 0xFFFFFFFF):08x}-{rng.randint(0, 0xFFFF):04x}-{rng.randint(0, 0xFFFF):04x}-{rng.randint(0, 0xFFFF):04x}-{rng.randint(0, 0xFFFFFFFFFFFF):012x}"
            fname = f"{{{guid}}}.msi"
            self._write_bytes(Path("Windows/Installer") / fname, _msi_stub(rng.randint(16, 64)), "install")
            created += 1

        # --- Windows/SysWOW64 ---
        wow64_dlls = [
            "kernel32.dll", "ntdll.dll", "user32.dll", "advapi32.dll",
            "ole32.dll", "shell32.dll", "msvcrt.dll",
        ]
        for dll in wow64_dlls:
            pe_data = self._dll_stub_32bit(rng.randint(32, 128))
            self._write_bytes(Path("Windows/SysWOW64") / dll, pe_data, "system_file")
            created += 1

        # --- Windows/WinSxS ---
        manifests = [
            "amd64_microsoft.windows.common-controls_6595b64144ccf1df_6.0.22621.1_none_abc123",
            "amd64_microsoft-windows-shell-explorer_31bf3856ad364e35_10.0.22621.1_none_def456",
        ]
        for mf in manifests:
            mf_dir = Path("Windows/WinSxS") / mf
            self._write_text(mf_dir / f"{mf.split('_')[1]}.manifest", '<?xml version="1.0" encoding="UTF-8"?>\n<assembly xmlns="urn:schemas-microsoft-com:asm.v3" manifestVersion="1.0"/>\n', "system_file")
            created += 1

        # --- Windows/SoftwareDistribution/Download ---
        for i in range(rng.randint(2, 5)):
            guid = f"{rng.randint(0, 0xFFFFFFFF):08x}{rng.randint(0, 0xFFFF):04x}"
            content = bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 8192)))
            self._write_bytes(
                Path("Windows/SoftwareDistribution/Download") / guid,
                content, "update",
            )
            created += 1

        return created

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------

    def _populate_scheduled_tasks(self, rng: Random) -> int:
        """Create scheduled task XML files in System32/Tasks."""
        created = 0
        tasks_root = Path("Windows/System32/Tasks")

        for task in _SCHEDULED_TASKS:
            task_dir = tasks_root / task["dirs"]
            task_name = task["path"].split("/")[-1]
            xml = _TASK_XML_TEMPLATE.format(
                task_path=task["path"],
                command=task["command"],
            )
            self._write_text(task_dir / task_name, xml, "system_file")
            created += 1

        return created

    # ------------------------------------------------------------------
    # User AppData directories
    # ------------------------------------------------------------------

    def _populate_user_appdata(self, username: str, rng: Random) -> int:
        """Fill AppData subdirectories with realistic content."""
        created = 0
        user_root = Path("Users") / username

        # --- SendTo shortcuts ---
        sendto = user_root / "AppData/Roaming/Microsoft/Windows/SendTo"
        sendto_items = {
            "Desktop (create shortcut).DeskLink": b"\x00" * 32,
            "Mail Recipient.MAPIMail": b"\x00" * 32,
            "Compressed (zipped) Folder.ZFSendToTarget": b"\x00" * 32,
        }
        for name, content in sendto_items.items():
            self._write_bytes(sendto / name, content, "system_file")
            created += 1

        # --- Templates ---
        templates = user_root / "AppData/Roaming/Microsoft/Windows/Templates"
        self._write_text(templates / "Normal.dotm", "", "system_file")
        created += 1

        # --- Themes ---
        themes = user_root / "AppData/Roaming/Microsoft/Windows/Themes"
        wallpaper_data = bytes(rng.getrandbits(8) for _ in range(4096))
        self._write_bytes(themes / "TranscodedWallpaper", wallpaper_data, "system_file")
        self._write_text(themes / "CachedFiles" / "readme.txt", "", "system_file")
        created += 2

        # --- Libraries ---
        libraries = user_root / "AppData/Roaming/Microsoft/Windows/Libraries"
        for lib_name, params in _LIBRARIES.items():
            xml = _LIBRARY_TEMPLATE.format(**params)
            self._write_text(libraries / lib_name, xml, "system_file")
            created += 1

        # --- Credentials / Protect / Crypto ---
        sid_stub = f"S-1-5-21-{rng.randint(1000000000, 4000000000)}"
        cred_blob = bytes(rng.getrandbits(8) for _ in range(256))
        cred_path = user_root / "AppData/Roaming/Microsoft/Credentials"
        self._write_bytes(cred_path / f"{rng.randint(10000, 99999):X}", cred_blob, "system_file")
        created += 1

        protect_dir = user_root / "AppData/Roaming/Microsoft/Protect" / sid_stub
        mk_blob = bytes(rng.getrandbits(8) for _ in range(468))
        self._write_bytes(protect_dir / "Preferred", mk_blob, "system_file")
        created += 1

        crypto_dir = user_root / "AppData/Roaming/Microsoft/Crypto/RSA" / sid_stub
        self._write_bytes(crypto_dir / "d1a2b3c4e5f6", bytes(rng.getrandbits(8) for _ in range(512)), "system_file")
        created += 1

        # --- SystemCertificates ---
        certs = user_root / "AppData/Roaming/Microsoft/SystemCertificates/My/Certificates"
        self._write_bytes(certs / "desktop.ini", b"[.ShellClassInfo]\r\n", "system_file")
        created += 1

        # --- History / INetCache / INetCookies ---
        local_ms = user_root / "AppData/Local/Microsoft/Windows"
        self._write_bytes(local_ms / "History" / "desktop.ini", b"[.ShellClassInfo]\r\nCLSID={FF393560-C2A7-11CF-BFF4-444553540000}\r\n", "system_file")
        self._write_bytes(local_ms / "INetCache" / "Content.IE5" / "index.dat", bytes(rng.getrandbits(8) for _ in range(1024)), "system_file")
        self._write_bytes(local_ms / "INetCookies" / "index.dat", bytes(rng.getrandbits(8) for _ in range(512)), "system_file")
        created += 3

        # --- Notifications ---
        notifications = local_ms / "Notifications"
        self._write_bytes(notifications / "wpndatabase.db", b"SQLite format 3\x00" + bytes(rng.getrandbits(8) for _ in range(4096)), "system_file")
        created += 1

        # --- Temporary Internet Files ---
        tif = local_ms / "Temporary Internet Files" / "Content.IE5"
        for i in range(3):
            subdir = f"{rng.randint(0, 0xFFFFFFFF):08X}"
            self._write_bytes(tif / subdir / "desktop.ini", b"[.ShellClassInfo]\r\n", "system_file")
            created += 1

        # --- WER ---
        wer = local_ms / "WER" / "ReportArchive"
        self._write_text(wer / "Report.wer", "[General]\nEventType=AppCrash\nEventTime=133500000000000000\n", "system_file")
        created += 1

        # --- WindowsApps ---
        wa = user_root / "AppData/Local/Microsoft/WindowsApps"
        self._write_text(wa / "MicrosoftEdge.exe", "", "system_file")
        created += 1

        # --- Start Menu Startup ---
        startup = user_root / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
        self._write_text(startup / "desktop.ini", "[.ShellClassInfo]\r\nLocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-21787\r\n", "system_file")
        created += 1

        # --- Network Shortcuts / Printer Shortcuts ---
        ns = user_root / "AppData/Roaming/Microsoft/Windows/Network Shortcuts"
        self._write_text(ns / "desktop.ini", "[.ShellClassInfo]\r\n", "system_file")
        created += 1
        ps = user_root / "AppData/Roaming/Microsoft/Windows/Printer Shortcuts"
        self._write_text(ps / "desktop.ini", "[.ShellClassInfo]\r\n", "system_file")
        created += 1

        return created

    # ------------------------------------------------------------------
    # User shell folders (Desktop, Downloads, Music, etc.)
    # ------------------------------------------------------------------

    def _populate_user_shell_folders(
        self,
        username: str,
        profile_type: str,
        rng: Random,
    ) -> int:
        """Seed user-visible shell folders with plausible contents.

        Goal: avoid the "only desktop.ini everywhere" look while keeping the
        artifacts small, deterministic, and safe (no executable code).
        """
        created = 0
        user_root = Path("Users") / username

        shell_ini = "[.ShellClassInfo]\r\nLocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-{resid}\r\n"
        shell_dirs = {
            "Desktop": "21769",
            "Downloads": "21798",
            "Favorites": "21796",
            "Links": "22090",
            "Music": "21790",
            "Videos": "21791",
            "Searches": "22090",
            "Contacts": "21786",
            "Saved Games": "22084",
            "3D Objects": "21781",
            "OneDrive": "21614",
        }
        for folder, resid in shell_dirs.items():
            ini_path = user_root / folder / "desktop.ini"
            self._write_text(ini_path, shell_ini.format(resid=resid), "system_file")
            created += 1

        # Seed Downloads with a couple of stub installers
        dl = user_root / "Downloads"
        self._write_bytes(dl / "ChromeSetup.exe", b"MZ" + b"\x00" * 126, "download")
        self._write_bytes(dl / "VCRedist_x64.exe", b"MZ" + b"\x00" * 126, "download")
        created += 2

        # Favorites: real machines often have a few .url shortcuts.
        fav = user_root / "Favorites"
        favorites = [
            ("Microsoft.url", "https://www.microsoft.com/"),
            ("Google.url", "https://www.google.com/"),
        ]
        if profile_type == "developer":
            favorites.extend([
                ("GitHub.url", "https://github.com/"),
                ("Stack Overflow.url", "https://stackoverflow.com/"),
                ("Python Docs.url", "https://docs.python.org/3/"),
            ])
        elif profile_type == "office_user":
            favorites.extend([
                ("Outlook Web.url", "https://outlook.office.com/"),
                ("OneDrive.url", "https://onedrive.live.com/"),
                ("Microsoft 365.url", "https://www.office.com/"),
            ])
        else:
            favorites.extend([
                ("YouTube.url", "https://www.youtube.com/"),
                ("News.url", "https://www.bbc.com/news"),
            ])

        for fname, url in favorites:
            content = f"[InternetShortcut]\r\nURL={url}\r\n".encode("utf-8")
            self._write_bytes(fav / fname, content, "browser_visit")
            created += 1

        # Links: add a couple of shortcuts pointing at common user folders.
        links = user_root / "Links"
        for link_name in ("Downloads.url", "Documents.url", "Pictures.url"):
            target_folder = link_name.replace(".url", "")
            target = f"file:///C:/Users/{username}/{target_folder}/"
            content = f"[InternetShortcut]\r\nURL={target}\r\n".encode("utf-8")
            self._write_bytes(links / link_name, content, "recent")
            created += 1

        # OneDrive: seed with a couple of plausible user files.
        od = user_root / "OneDrive"
        self._write_text(
            od / "Readme.txt",
            "OneDrive\n=======\n\n- Shared files sync here.\n- Recent activity is available in the OneDrive client.\n",
            "system_file",
        )
        created += 1
        if profile_type == "developer":
            self._write_text(od / "Dev_Notes.md", "- TODO: review CI failures\n- Draft: architecture notes\n", "recent")
            created += 1
        elif profile_type == "office_user":
            self._write_text(od / "Meeting_Agenda.txt", "Agenda:\n- Status update\n- Next steps\n", "recent")
            created += 1
        else:
            self._write_text(od / "Family_Todo.txt", "Weekend:\n- groceries\n- laundry\n", "recent")
            created += 1

        # Music: even if empty, a couple tiny artifacts are common (playlist / thumbs).
        music = user_root / "Music"
        self._write_text(music / "playlist.m3u", "#EXTM3U\n#EXTINF:0,Sample Track\n", "media_created")
        created += 1

        # Contacts / Searches / Saved Games / 3D Objects: seed with minimal, harmless files.
        contacts = user_root / "Contacts"
        self._write_text(contacts / "contacts.txt", "John Smith <john.smith@example.com>\n", "system_file")
        created += 1

        searches = user_root / "Searches"
        self._write_text(
            searches / "Recent Documents.search-ms",
            "<?xml version=\"1.0\"?><searchConnectorDescription />\n",
            "system_file",
        )
        created += 1

        saved_games = user_root / "Saved Games"
        self._write_text(saved_games / "readme.txt", "Saved games and settings.\n", "system_file")
        created += 1

        objects3d = user_root / "3D Objects"
        self._write_text(objects3d / "readme.txt", "3D Objects\n", "system_file")
        created += 1

        # Seed Desktop with a couple of realistic items (shortcuts + a note).
        desktop = user_root / "Desktop"
        # A short "notes" file is very common on real desktops.
        self._write_text(
            desktop / "Notes.txt",
            "TODO:\n- Review pull requests\n- Update documentation\n- Follow up on meeting action items\n",
            "recent",
        )
        created += 1

        # Shortcuts are common. We generate minimal .lnk-like stubs.
        # (We don't attempt a full LNK spec here; RecentItemsService already
        # produces proper LNKs elsewhere.)
        self._write_bytes(desktop / "Google Chrome.lnk", _sendto_lnk_stub(""), "recent")
        created += 1
        if profile_type == "developer":
            self._write_bytes(desktop / "Visual Studio Code.lnk", _sendto_lnk_stub(""), "recent")
            self._write_bytes(desktop / "Windows Terminal.lnk", _sendto_lnk_stub(""), "recent")
            created += 2
        elif profile_type == "office_user":
            self._write_bytes(desktop / "Outlook.lnk", _sendto_lnk_stub(""), "recent")
            self._write_bytes(desktop / "Excel.lnk", _sendto_lnk_stub(""), "recent")
            created += 2

        return created

    # ------------------------------------------------------------------
    # Public directories
    # ------------------------------------------------------------------

    def _populate_public_dirs(self, rng: Random) -> int:
        """Add desktop.ini and sample files to Users/Public/."""
        created = 0

        desktop_ini = b"[.ShellClassInfo]\r\nLocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-21798\r\n"

        for subdir in ("Desktop", "Documents", "Downloads"):
            path = Path("Users/Public") / subdir / "desktop.ini"
            self._write_bytes(path, desktop_ini, "system_file")
            created += 1

        return created

    # ------------------------------------------------------------------
    # Browser cache/storage subdirectories
    # ------------------------------------------------------------------

    def _populate_browser_caches(
        self, username: str, profile_type: str, rng: Random
    ) -> int:
        """Fill browser profile subdirectories unconditionally.

        NOTE: This runs in INFRASTRUCTURE phase, BEFORE BrowserProfile
        creates the parent directories. We create all dirs ourselves here
        so they are never empty regardless of run order.
        """
        created = 0
        user_root = Path("Users") / username / "AppData/Local"

        browser_bases = [
            user_root / "Google/Chrome/User Data/Default",
            user_root / "Microsoft/Edge/User Data/Default",
        ]

        for base in browser_bases:
            # Code Cache/js
            cc = base / "Code Cache/js"
            self._write_bytes(cc / "index", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            self._write_bytes(cc / "data_0", bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 8192))), "browser_visit")
            self._write_bytes(cc / "data_1", bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 8192))), "browser_visit")
            created += 3

            # GPUCache
            gpu = base / "GPUCache"
            self._write_bytes(gpu / "index", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            self._write_bytes(gpu / "data_0", bytes(rng.getrandbits(8) for _ in range(rng.randint(4096, 16384))), "browser_visit")
            self._write_bytes(gpu / "data_1", bytes(rng.getrandbits(8) for _ in range(rng.randint(4096, 16384))), "browser_visit")
            self._write_bytes(gpu / "data_2", bytes(rng.getrandbits(8) for _ in range(rng.randint(4096, 16384))), "browser_visit")
            self._write_bytes(gpu / "data_3", bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 4096))), "browser_visit")
            created += 5

            # IndexedDB
            idb = base / "IndexedDB/https_www.google.com_0.indexeddb.leveldb"
            self._write_bytes(idb / "MANIFEST-000001", b"\x00" * 64, "browser_visit")
            self._write_bytes(idb / "000003.log", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            self._write_bytes(idb / "LOCK", b"", "browser_visit")
            created += 3

            # Extension State
            ext_state = base / "Extension State"
            self._write_bytes(ext_state / "MANIFEST-000001", b"\x00" * 64, "browser_visit")
            self._write_bytes(ext_state / "000003.log", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            created += 2

            # Extensions — Google Translate
            ext_id = "aapbdbdomjkkjkaonfhkkikfgjllcleb"
            ext = base / "Extensions" / ext_id / "2.0.13_0"
            manifest = '{"manifest_version":3,"name":"Google Translate","version":"2.0.13","description":"View translations easily as you browse the web"}'
            self._write_text(ext / "manifest.json", manifest, "install")
            self._write_bytes(ext / "icon128.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "install")
            created += 2

            # Session Storage
            ss = base / "Session Storage"
            self._write_bytes(ss / "MANIFEST-000001", b"\x00" * 64, "browser_visit")
            self._write_bytes(ss / "000003.log", bytes(rng.getrandbits(8) for _ in range(rng.randint(256, 2048))), "browser_visit")
            self._write_bytes(ss / "LOCK", b"", "browser_visit")
            created += 3

            # Local Storage/leveldb
            ls = base / "Local Storage/leveldb"
            self._write_bytes(ls / "MANIFEST-000001", b"\x00" * 64, "browser_visit")
            self._write_bytes(ls / "000003.log", bytes(rng.getrandbits(8) for _ in range(rng.randint(256, 2048))), "browser_visit")
            self._write_bytes(ls / "LOCK", b"", "browser_visit")
            created += 3

            # Network
            net = base / "Network"
            cookie_db = b"SQLite format 3\x00" + bytes(rng.getrandbits(8) for _ in range(4096))
            self._write_bytes(net / "Cookies", cookie_db, "browser_visit")
            self._write_text(
                net / "NetworkPersistentState",
                '{"net":{"http_server_properties":{"servers":[],"version":5},"quic_server_info_map":{}}}',
                "browser_visit",
            )
            created += 2

        return created

    # ------------------------------------------------------------------
    # Developer tool config directories
    # ------------------------------------------------------------------

    def _populate_dev_tool_configs(
        self, username: str, profile_type: str, rng: Random
    ) -> int:
        """Populate developer hidden config dirs if profile is developer."""
        if profile_type != "developer":
            return 0

        created = 0
        user_root = Path("Users") / username

        # .aws
        aws = user_root / ".aws"
        self._write_text(aws / "config", "[default]\nregion = eu-west-1\noutput = json\n", "system_file")
        self._write_text(aws / "credentials", "[default]\naws_access_key_id = AKIAIOSFODNN7EXAMPLE\naws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n", "system_file")
        created += 2

        # .azure
        azure = user_root / ".azure"
        self._write_text(azure / "config", "[cloud]\nname = AzureCloud\n[core]\noutput = json\n", "system_file")
        self._write_text(azure / "msal_token_cache.bin", "", "system_file")
        created += 2

        # .config (generic)
        config = user_root / ".config"
        self._write_text(config / "configstore" / "update-notifier-npm.json", '{"optOut":false}', "system_file")
        created += 1

        # .kube
        kube = user_root / ".kube"
        self._write_text(kube / "config", "apiVersion: v1\nclusters: []\ncontexts: []\ncurrent-context: ''\nkind: Config\npreferences: {}\nusers: []\n", "system_file")
        created += 1

        # .npm
        npm = user_root / ".npm"
        self._write_text(npm / ".package-lock.json", '{"name":"","lockfileVersion":3,"requires":true,"packages":{}}', "system_file")
        created += 1

        # .nuget
        nuget = user_root / ".nuget" / "packages"
        self._write_text(nuget / "repositories.config", '<?xml version="1.0" encoding="utf-8"?>\n<repositories/>\n', "system_file")
        created += 1

        # go
        go_dir = user_root / "go"
        self._write_text(go_dir / "env", "GOPATH=C:\\Users\\" + username + "\\go\nGOROOT=C:\\Program Files\\Go\n", "system_file")
        created += 1

        return created

    # ------------------------------------------------------------------
    # Application-specific cache dirs
    # ------------------------------------------------------------------

    def _populate_app_caches(
        self, username: str, profile_type: str, rng: Random
    ) -> int:
        """Populate Teams, Office, Outlook, and other app cache dirs."""
        created = 0
        user_root = Path("Users") / username

        # AppData/Local/Temp  
        temp = user_root / "AppData/Local/Temp"
        for i in range(rng.randint(2, 5)):
            fname = f"{rng.randint(0, 0xFFFF):04X}.tmp"
            self._write_bytes(temp / fname, bytes(rng.getrandbits(8) for _ in range(rng.randint(64, 512))), "system_file")
            created += 1

        # AppData/Local/VirtualStore  
        vstore = user_root / "AppData/Local/VirtualStore"
        self._write_text(vstore / "desktop.ini", "[.ShellClassInfo]\r\n", "system_file")
        created += 1

        # AppData/LocalLow
        locallow = user_root / "AppData/LocalLow"
        self._write_text(locallow / "desktop.ini", "[.ShellClassInfo]\r\n", "system_file")
        created += 1

        # Teams cache dirs (developer/office only)
        if profile_type in ("developer", "office_user"):
            teams_root = user_root / "AppData/Roaming/Microsoft/Teams"
            teams_dirs = {
                "blob_storage": b"\x00" * 32,
                "databases": b"\x00" * 32,
            }
            for subdir, content in teams_dirs.items():
                self._write_bytes(teams_root / subdir / "desktop.ini", content, "browser_visit")
                created += 1

            # Teams Cache
            tc = teams_root / "Cache"
            self._write_bytes(tc / "index", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            self._write_bytes(tc / "data_0", bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 8192))), "browser_visit")
            created += 2

            # Teams IndexedDB
            t_idb = teams_root / "IndexedDB" / "https_teams.microsoft.com_0.indexeddb.leveldb"
            self._write_bytes(t_idb / "MANIFEST-000001", b"\x00" * 64, "browser_visit")
            self._write_bytes(t_idb / "000003.log", bytes(rng.getrandbits(8) for _ in range(256)), "browser_visit")
            created += 2

        # Office cache
        office_cache = user_root / "AppData/Local/Microsoft/Office/16.0/OfficeFileCache"
        self._write_bytes(office_cache / "FSD.dat", b"\x00" * 512, "system_file")
        self._write_bytes(office_cache / "FSF.dat", b"\x00" * 256, "system_file")
        created += 2

        # Outlook RoamCache
        roam = user_root / "AppData/Local/Microsoft/Outlook/RoamCache"
        self._write_text(roam / "Stream_Autocomplete_0_ABCDEF1234567890.dat", "", "system_file")
        self._write_text(roam / "Stream_SenderSMIMERecipientCache_0.dat", "", "system_file")
        created += 2

        return created

    # ------------------------------------------------------------------
    # ProgramData Microsoft dirs
    # ------------------------------------------------------------------

    def _populate_programdata(self, rng: Random) -> int:
        """Populate ProgramData/Microsoft dirs with minimal stubs."""
        created = 0

        # Windows Defender
        defender = Path("ProgramData/Microsoft/Windows Defender")
        self._write_text(
            defender / "Platform" / "4.18.24010.12-0" / "MsMpEng.exe",
            "", "system_file",
        )
        self._write_bytes(
            defender / "Definition Updates" / "{00000000-0000-0000-0000-000000000000}" / "mpasbase.vdm",
            bytes(rng.getrandbits(8) for _ in range(512)),
            "system_file",
        )
        created += 2

        # Start Menu Programs
        sm = Path("ProgramData/Microsoft/Windows/Start Menu/Programs")
        self._write_text(sm / "desktop.ini", "[.ShellClassInfo]\r\nLocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-21782\r\n", "system_file")
        created += 1

        # InstalledAppsStub missing: VLC & Calculator
        vlc_dir = Path("Program Files/VideoLAN/VLC")
        self._write_bytes(vlc_dir / "vlc.exe", b"MZ" + b"\x00" * 126, "system_file")
        self._write_bytes(vlc_dir / "libvlc.dll", b"MZ" + b"\x00" * 126, "system_file")
        created += 2

        calc_dir = Path("Program Files/WindowsApps/Microsoft.WindowsCalculator")
        self._write_bytes(calc_dir / "Calculator.exe", b"MZ" + b"\x00" * 126, "system_file")
        created += 1

        return created

    # ------------------------------------------------------------------
    # Final sweep — catch-all for any remaining empty directories
    # ------------------------------------------------------------------

    def _sweep_remaining_empty_dirs(self) -> int:
        """Walk the output tree and fill any directory that has no children.

        Uses contextual desktop.ini content for Windows shell folders and
        generic placeholder stubs for everything else.  This runs last
        and guarantees zero empty directories in the output.
        """
        import os

        root = self._mount.resolve("")
        if not root.exists():
            return 0

        created = 0

        for dirpath, dirnames, filenames in os.walk(str(root), topdown=False):
            # Skip the root directory itself
            if dirpath == str(root):
                continue

            # Check if directory is truly empty (no files AND no subdirs)
            dir_path = Path(dirpath)
            children = list(dir_path.iterdir())
            if children:
                continue

            # Determine appropriate stub content based on path
            rel = dir_path.relative_to(root)
            rel_str = str(rel).replace("\\", "/")

            if "AppData" in rel_str or "ProgramData" in rel_str:
                stub_name = "desktop.ini"
                stub_content = b"[.ShellClassInfo]\r\n"
            elif "Program Files" in rel_str:
                stub_name = "desktop.ini"
                stub_content = b"[.ShellClassInfo]\r\n"
            elif "Users" in rel_str:
                stub_name = "desktop.ini"
                stub_content = b"[.ShellClassInfo]\r\n"
            else:
                stub_name = "desktop.ini"
                stub_content = b"[.ShellClassInfo]\r\n"

            stub_path = dir_path / stub_name
            stub_path.write_bytes(stub_content)
            self._apply_ts(stub_path, "system_file")
            created += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "sweep_empty_dir",
                "path": str(stub_path),
            })
            logger.debug("Swept empty dir: %s", rel_str)

        return created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_bytes(self, rel_path: Path, content: bytes, event_type: str) -> None:
        """Write bytes to a path and apply timestamps."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        self._apply_ts(full_path, event_type)

    def _write_text(self, rel_path: Path, text: str, event_type: str) -> None:
        """Write text to a path and apply timestamps."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(text, encoding="utf-8")
        self._apply_ts(full_path, event_type)

    def _apply_ts(self, path: Path, event_type: str) -> None:
        """Apply timestamps from the timestamp service."""
        import os
        import platform

        timestamps = self._ts.get_timestamp(event_type)
        accessed = timestamps["accessed"].timestamp()
        modified = timestamps["modified"].timestamp()
        os.utime(str(path), (accessed, modified))

        try:
            import pywintypes
            import win32con
            import win32file
            if platform.system() == "Windows":
                created = pywintypes.Time(timestamps["created"])
                handle = win32file.CreateFile(
                    str(path), win32con.GENERIC_WRITE,
                    win32con.FILE_SHARE_WRITE, None,
                    win32con.OPEN_EXISTING,
                    win32con.FILE_ATTRIBUTE_NORMAL, None,
                )
                try:
                    win32file.SetFileTime(handle, created, None, None)
                finally:
                    handle.Close()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Could not set creation time for %s: %s", path, exc)

    @staticmethod
    def _dll_stub_32bit(size_kb: int = 32) -> bytes:
        """Build a minimal 32-bit PE DLL stub for SysWOW64."""
        dos_header = bytearray(128)
        dos_header[0:2] = b"MZ"
        struct.pack_into("<I", dos_header, 60, 128)

        pe_sig = b"PE\x00\x00"
        coff = bytearray(20)
        struct.pack_into("<H", coff, 0, 0x014C)   # Machine = i386
        struct.pack_into("<H", coff, 2, 0)
        struct.pack_into("<H", coff, 16, 0)
        struct.pack_into("<H", coff, 18, 0x2102)  # DLL | EXECUTABLE_IMAGE

        header = bytes(dos_header) + pe_sig + bytes(coff)
        target = max(size_kb * 1024, len(header) + 64)
        return header + b"\x00" * (target - len(header))
