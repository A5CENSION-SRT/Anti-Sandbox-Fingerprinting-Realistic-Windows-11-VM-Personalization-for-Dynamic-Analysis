"""Filesystem stub generator for installed applications.

Creates realistic application directory trees with stub executables,
DLLs, config files, and version metadata matching what
:class:`InstalledPrograms` registers in the SOFTWARE hive and what
:class:`PrefetchService` / :class:`UserAssist` reference.

This ensures coherence: if Prefetch says ``CHROME.EXE`` was run from
``C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe``, that path
actually exists on the VHD with a plausible PE file.

Design notes
------------
* Stub executables use a minimal but valid PE header so forensic
  tools won't flag them as corrupt zero-byte files.
* Each application definition includes the files to create plus
  optional ``version_info`` metadata written as a ``VERSION.dll``
  or ``chrome.VisualElementsManifest.xml`` (varies per app).
* Profile-aware: only apps listed in ``context['installed_apps']``
  are created.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal valid PE executable stub (matches Windows x64 PE format)
# ---------------------------------------------------------------------------

def _pe_stub(size_kb: int = 64) -> bytes:
    """Build a minimal valid PE-format executable stub.

    The stub has a valid DOS header + PE signature + COFF header +
    Optional header so forensic tools recognise it as a Windows
    binary.  The body is padded to ``size_kb`` KiB.
    """
    dos_header = bytearray(128)
    dos_header[0:2] = b"MZ"                          # e_magic
    struct.pack_into("<H", dos_header, 2, 144)        # e_cblp
    struct.pack_into("<H", dos_header, 4, 3)          # e_cp
    struct.pack_into("<H", dos_header, 8, 4)          # e_cparhdr
    struct.pack_into("<H", dos_header, 24, 0xFFFF)    # e_maxalloc
    struct.pack_into("<H", dos_header, 28, 0xB8)      # e_sp
    struct.pack_into("<I", dos_header, 60, 128)       # e_lfanew -> PE header at 128

    # DOS stub message
    dos_msg = b"This program cannot be run in DOS mode.\r\r\n$"
    dos_header[78:78 + len(dos_msg)] = dos_msg

    pe_sig = b"PE\x00\x00"

    # COFF header (20 bytes)
    coff = bytearray(20)
    struct.pack_into("<H", coff, 0, 0x8664)   # Machine = AMD64
    struct.pack_into("<H", coff, 2, 1)         # NumberOfSections
    struct.pack_into("<I", coff, 4, 0x65A00000)  # TimeDateStamp
    struct.pack_into("<H", coff, 16, 240)      # SizeOfOptionalHeader
    struct.pack_into("<H", coff, 18, 0x0022)   # Characteristics (EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE)

    # Optional header (PE32+, 240 bytes)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x020B)     # Magic = PE32+
    opt[2] = 14                                # MajorLinkerVersion
    opt[3] = 38                                # MinorLinkerVersion
    struct.pack_into("<I", opt, 16, 0x1000)    # AddressOfEntryPoint
    struct.pack_into("<Q", opt, 24, 0x140000000)  # ImageBase
    struct.pack_into("<I", opt, 32, 0x1000)    # SectionAlignment
    struct.pack_into("<I", opt, 36, 0x200)     # FileAlignment
    struct.pack_into("<H", opt, 40, 6)         # MajorOSVersion
    struct.pack_into("<H", opt, 44, 6)         # MajorSubsystemVersion
    struct.pack_into("<I", opt, 56, 0x10000)   # SizeOfImage
    struct.pack_into("<I", opt, 60, 0x200)     # SizeOfHeaders
    struct.pack_into("<H", opt, 68, 3)         # Subsystem = CONSOLE
    struct.pack_into("<Q", opt, 72, 0x100000)  # SizeOfStackReserve
    struct.pack_into("<Q", opt, 80, 0x1000)    # SizeOfStackCommit
    struct.pack_into("<Q", opt, 88, 0x100000)  # SizeOfHeapReserve
    struct.pack_into("<Q", opt, 96, 0x1000)    # SizeOfHeapCommit
    struct.pack_into("<I", opt, 108, 16)       # NumberOfRvaAndSizes

    # Section header (.text, 40 bytes)
    section = bytearray(40)
    section[0:5] = b".text"
    struct.pack_into("<I", section, 8, 0x1000)   # VirtualSize
    struct.pack_into("<I", section, 12, 0x1000)  # VirtualAddress
    struct.pack_into("<I", section, 16, 0x200)   # SizeOfRawData
    struct.pack_into("<I", section, 20, 0x200)   # PointerToRawData
    struct.pack_into("<I", section, 36, 0x60000020)  # Characteristics

    header = bytes(dos_header) + pe_sig + bytes(coff) + bytes(opt) + bytes(section)
    target_size = max(size_kb * 1024, len(header) + 512)
    return header + b"\x00" * (target_size - len(header))


def _dll_stub(size_kb: int = 32) -> bytes:
    """Build a minimal valid PE DLL stub."""
    data = bytearray(_pe_stub(size_kb))
    # Flip DLL characteristic bit in COFF header
    coff_offset = 128 + 4  # After DOS header + PE sig
    chars = struct.unpack_from("<H", data, coff_offset + 18)[0]
    struct.pack_into("<H", data, coff_offset + 18, chars | 0x2000)
    return bytes(data)


# ---------------------------------------------------------------------------
# Application definitions — coherent with installed_programs._PROGRAM_CATALOG
# and prefetch app paths
# ---------------------------------------------------------------------------

_APP_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "chrome": {
        "base_dir": "Program Files/Google/Chrome/Application",
        "files": {
            "chrome.exe": {"type": "exe", "size_kb": 2048},
            "chrome.dll": {"type": "dll", "size_kb": 512},
            "chrome_elf.dll": {"type": "dll", "size_kb": 64},
            "chrome_proxy.exe": {"type": "exe", "size_kb": 32},
            "elevation_service.exe": {"type": "exe", "size_kb": 64},
            "122.0.6261.112/chrome.dll": {"type": "dll", "size_kb": 256},
            "122.0.6261.112/chrome_child.dll": {"type": "dll", "size_kb": 128},
            "122.0.6261.112/v8_context_snapshot.bin": {"type": "binary", "size_kb": 512},
            "122.0.6261.112/icudtl.dat": {"type": "binary", "size_kb": 256},
            "master_preferences": {"type": "text", "content": "{}"},
        },
    },
    "docker": {
        "base_dir": "Program Files/Docker/Docker",
        "files": {
            "Docker Desktop.exe": {"type": "exe", "size_kb": 4096},
            "com.docker.backend.exe": {"type": "exe", "size_kb": 512},
            "com.docker.build.exe": {"type": "exe", "size_kb": 128},
            "com.docker.cli.exe": {"type": "exe", "size_kb": 128},
            "resources/docker.exe": {"type": "exe", "size_kb": 256},
            "resources/docker-compose.exe": {"type": "exe", "size_kb": 128},
            "resources/kubectl.exe": {"type": "exe", "size_kb": 128},
        },
    },
    "git": {
        "base_dir": "Program Files/Git",
        "files": {
            "cmd/git.exe": {"type": "exe", "size_kb": 64},
            "cmd/gitk.exe": {"type": "exe", "size_kb": 32},
            "git-bash.exe": {"type": "exe", "size_kb": 128},
            "git-cmd.exe": {"type": "exe", "size_kb": 32},
            "bin/git.exe": {"type": "exe", "size_kb": 1024},
            "bin/bash.exe": {"type": "exe", "size_kb": 512},
            "bin/sh.exe": {"type": "exe", "size_kb": 64},
            "mingw64/bin/git.exe": {"type": "exe", "size_kb": 256},
            "etc/gitconfig": {
                "type": "text",
                "content": "[core]\n\tautocrlf = true\n\tsymlinks = false\n[credential]\n\thelper = manager\n",
            },
        },
    },
    "vscode": {
        "user_dir": True,  # under Users/{username}/AppData/Local/Programs
        "base_dir": "AppData/Local/Programs/Microsoft VS Code",
        "files": {
            "Code.exe": {"type": "exe", "size_kb": 128},
            "resources/app/package.json": {
                "type": "text",
                "content": '{"name":"code-oss","version":"1.87.2","main":"./out/main"}',
            },
            "resources/app/out/main.js": {"type": "binary", "size_kb": 64},
            "unins000.exe": {"type": "exe", "size_kb": 64},
        },
    },
    "outlook": {
        "base_dir": "Program Files/Microsoft Office/root/Office16",
        "files": {
            "OUTLOOK.EXE": {"type": "exe", "size_kb": 4096},
            "WINWORD.EXE": {"type": "exe", "size_kb": 4096},
            "EXCEL.EXE": {"type": "exe", "size_kb": 4096},
            "POWERPNT.EXE": {"type": "exe", "size_kb": 2048},
            "MSACCESS.EXE": {"type": "exe", "size_kb": 1024},
            "mso.dll": {"type": "dll", "size_kb": 512},
            "mso20win32client.dll": {"type": "dll", "size_kb": 256},
        },
    },
    # word/excel share the same InstallLocation as outlook — no separate entry needed
    "teams": {
        "base_dir": "Program Files/WindowsApps/MSTeams",
        "files": {
            "ms-teams.exe": {"type": "exe", "size_kb": 512},
            "msteams_autostarter.exe": {"type": "exe", "size_kb": 32},
        },
    },
    "terminal": {
        "base_dir": "Program Files/WindowsApps/Microsoft.WindowsTerminal",
        "files": {
            "wt.exe": {"type": "exe", "size_kb": 256},
            "WindowsTerminal.exe": {"type": "exe", "size_kb": 256},
            "OpenConsole.exe": {"type": "exe", "size_kb": 128},
        },
    },
    "vlc": {
        "base_dir": "Program Files/VideoLAN/VLC",
        "files": {
            "vlc.exe": {"type": "exe", "size_kb": 256},
            "libvlc.dll": {"type": "dll", "size_kb": 512},
            "libvlccore.dll": {"type": "dll", "size_kb": 256},
            "plugins/plugins.dat": {"type": "binary", "size_kb": 16},
        },
    },
    "calculator": {
        "base_dir": "Program Files/WindowsApps/Microsoft.WindowsCalculator",
        "files": {
            "Calculator.exe": {"type": "exe", "size_kb": 128},
        },
    },
    "spotify": {
        "user_dir": True,
        "base_dir": "AppData/Roaming/Spotify",
        "files": {
            "Spotify.exe": {"type": "exe", "size_kb": 512},
            "SpotifyMigrator.exe": {"type": "exe", "size_kb": 64},
            "libcef.dll": {"type": "dll", "size_kb": 256},
            "prefs": {"type": "text", "content": '{"app.autostart-mode":"off"}'},
        },
    },
    "notepad": {
        "base_dir": "Windows/System32",
        "system_app": True,  # lives in system dir, don't create base
        "files": {
            "notepad.exe": {"type": "exe", "size_kb": 256},
        },
    },
}

# System executables always present regardless of profile
_SYSTEM_EXECUTABLES: Dict[str, int] = {
    # path relative to mount root -> size in KB
    "Windows/System32/cmd.exe": 256,
    "Windows/System32/conhost.exe": 512,
    "Windows/System32/explorer.exe": 4096,
    "Windows/System32/svchost.exe": 64,
    "Windows/System32/taskhostw.exe": 128,
    "Windows/System32/RuntimeBroker.exe": 128,
    "Windows/System32/SearchHost.exe": 256,
    "Windows/System32/dllhost.exe": 32,
    "Windows/System32/dwm.exe": 128,
    "Windows/System32/lsass.exe": 64,
    "Windows/System32/csrss.exe": 32,
    "Windows/System32/wininit.exe": 32,
    "Windows/System32/services.exe": 64,
    "Windows/System32/winlogon.exe": 64,
    "Windows/System32/spoolsv.exe": 128,
    "Windows/System32/msiexec.exe": 128,
    "Windows/System32/reg.exe": 32,
    "Windows/System32/regedit.exe": 384,
    "Windows/System32/powershell.exe": 512,
    "Windows/System32/WindowsPowerShell/v1.0/powershell.exe": 512,
    "Windows/System32/wbem/WmiPrvSE.exe": 128,
    "Windows/System32/SecurityHealthSystray.exe": 64,
    "Windows/System32/drivers/etc/hosts": 0,  # special
    "Windows/System32/drivers/etc/services": 0,  # special
    "Program Files (x86)/Microsoft/Edge/Application/msedge.exe": 2048,
    "Program Files (x86)/Microsoft/Edge/Application/msedge_proxy.exe": 64,
    "Program Files/Microsoft OneDrive/OneDrive.exe": 512,
    "Program Files/nodejs/node.exe": 1024,
    "Program Files/nodejs/npm.cmd": 0,  # special
}

# Special text files
_SPECIAL_FILES: Dict[str, str] = {
    "Windows/System32/drivers/etc/hosts": (
        "# Copyright (c) 1993-2009 Microsoft Corp.\r\n"
        "#\r\n"
        "# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.\r\n"
        "#\r\n"
        "# localhost name resolution is handled within DNS itself.\r\n"
        "#\t127.0.0.1       localhost\r\n"
        "#\t::1             localhost\r\n"
    ),
    "Windows/System32/drivers/etc/services": (
        "# Copyright (c) 1993-2004 Microsoft Corp.\r\n"
        "echo                7/tcp\r\n"
        "ftp-data           20/tcp\r\n"
        "ftp                21/tcp\r\n"
        "ssh                22/tcp\r\n"
        "telnet             23/tcp\r\n"
        "smtp               25/tcp\r\n"
        "http               80/tcp\r\n"
        "pop3              110/tcp\r\n"
        "https             443/tcp\r\n"
        "ms-sql-s         1433/tcp\r\n"
        "ms-sql-m         1434/udp\r\n"
        "rdp              3389/tcp\r\n"
    ),
    "Program Files/nodejs/npm.cmd": (
        "@ECHO off\r\n"
        'SETLOCAL\r\nSET "NODE_EXE=%~dp0\\node.exe"\r\n'
        'SET "NPM_CLI_JS=%~dp0\\node_modules\\npm\\bin\\npm-cli.js"\r\n'
        '"%NODE_EXE%" "%NPM_CLI_JS%" %*\r\n'
    ),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InstalledAppsStubError(Exception):
    """Raised on stub generation failure."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class InstalledAppsStub(BaseService):
    """Creates realistic filesystem stubs for installed applications.

    For each app in the profile's ``installed_apps`` list, this service
    creates:
    * Stub ``.exe`` files with valid PE headers
    * Stub ``.dll`` files matching the app's real layout
    * Config files, manifests, and supporting metadata

    This makes the VHD coherent — paths referenced by Prefetch,
    InstalledPrograms (registry), and UserAssist all resolve to actual
    files.

    Additionally, standard Windows system executables (``explorer.exe``,
    ``svchost.exe``, ``cmd.exe``, etc.) are always created for any VHD.

    Dependencies (injected):
        mount_manager: Resolves paths relative to the mounted image root.
        audit_logger: Shared audit logger for traceability.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "InstalledAppsStub"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            installed_apps: list[str] — application names from the profile.
            username: str — for user-scoped app paths (VS Code, Spotify).
        """
        installed_apps = context.get("installed_apps", [])
        username = context.get("username", "default_user")

        try:
            files_created = 0

            # 1) System executables — always present
            files_created += self._create_system_executables()

            # 2) Profile-specific application stubs
            files_created += self._create_app_stubs(installed_apps, username)

            self._audit.log({
                "service": self.service_name,
                "operation": "create_app_stubs",
                "installed_apps": list(installed_apps),
                "files_created": files_created,
            })

            logger.info(
                "Created %d application stub files for %d apps",
                files_created, len(installed_apps),
            )

        except Exception as exc:
            logger.error("Failed to create app stubs: %s", exc)
            raise InstalledAppsStubError(
                f"App stub generation failed: {exc}"
            ) from exc

    # -- system executables -------------------------------------------------

    def _create_system_executables(self) -> int:
        """Create standard Windows system executables + special files."""
        created = 0

        for rel_path, size_kb in _SYSTEM_EXECUTABLES.items():
            full_path = self._mount.resolve(rel_path)
            if full_path.exists():
                continue

            full_path.parent.mkdir(parents=True, exist_ok=True)

            if rel_path in _SPECIAL_FILES:
                full_path.write_text(
                    _SPECIAL_FILES[rel_path], encoding="utf-8"
                )
            elif rel_path.endswith(".exe"):
                full_path.write_bytes(_pe_stub(size_kb))
            elif rel_path.endswith(".cmd"):
                full_path.write_text(
                    _SPECIAL_FILES.get(rel_path, "@echo off\r\n"),
                    encoding="utf-8",
                )
            else:
                full_path.write_bytes(b"\x00" * max(1024, size_kb * 1024))

            created += 1
            self._audit.log({
                "service": self.service_name,
                "operation": "create_file",
                "path": str(full_path),
                "size": full_path.stat().st_size,
            })

        return created

    # -- per-app stubs ------------------------------------------------------

    def _create_app_stubs(
        self,
        installed_apps: List[str],
        username: str,
    ) -> int:
        """Create directory trees & stub files for profile apps."""
        created = 0
        seen_bases: set = set()

        for app_name in installed_apps:
            app_key = app_name.lower().strip()
            definition = _APP_DEFINITIONS.get(app_key)
            if definition is None:
                logger.debug(
                    "No stub definition for '%s' — skipping", app_name
                )
                continue

            base_dir = definition["base_dir"]

            # Resolve the base directory (user-scoped or system)
            if definition.get("user_dir"):
                base_rel = f"Users/{username}/{base_dir}"
            else:
                base_rel = base_dir

            # Skip if we've already populated this base (e.g. word/excel
            # share Office16 with outlook)
            if base_rel in seen_bases:
                continue
            seen_bases.add(base_rel)

            for file_rel, spec in definition["files"].items():
                full_path = self._mount.resolve(
                    f"{base_rel}/{file_rel}"
                )
                if full_path.exists():
                    continue

                full_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_stub_file(full_path, spec)
                created += 1

                self._audit.log({
                    "service": self.service_name,
                    "operation": "create_file",
                    "path": str(full_path),
                    "size": full_path.stat().st_size,
                    "app": app_key,
                })

        return created

    # -- file writers -------------------------------------------------------

    @staticmethod
    def _write_stub_file(path: Path, spec: Dict[str, Any]) -> None:
        """Write a single stub file based on its type spec."""
        file_type = spec.get("type", "binary")
        size_kb = spec.get("size_kb", 16)

        if file_type == "exe":
            path.write_bytes(_pe_stub(size_kb))
        elif file_type == "dll":
            path.write_bytes(_dll_stub(size_kb))
        elif file_type == "text":
            path.write_text(spec.get("content", ""), encoding="utf-8")
        elif file_type == "binary":
            # Deterministic pseudo-random content based on filename
            seed = hashlib.md5(path.name.encode()).digest()
            data = seed * ((size_kb * 1024) // len(seed) + 1)
            path.write_bytes(data[:size_kb * 1024])
        else:
            path.write_bytes(b"\x00" * (size_kb * 1024))
