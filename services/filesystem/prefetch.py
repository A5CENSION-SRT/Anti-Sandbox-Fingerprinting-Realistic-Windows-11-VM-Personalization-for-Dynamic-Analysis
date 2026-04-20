"""Windows Prefetch v30 (Win10/11) binary generator.

Format: SCCA version 30 (Windows 8.1 / 10 / 11).
Layout:
    [84 bytes]   File Header
    [224 bytes]  File Information block (offset 84)
    [N*32 bytes] Section A — file metrics array
    [M*12 bytes] Section B — trace chain array
    [variable]   Section C — UTF-16LE filename strings (null-terminated)
    [variable]   Section D — volume information

Files are written uncompressed (direct SCCA header, no MAM wrapper).
Uncompressed SCCA files are accepted by WinPrefetchView, PECmd, and
Volatility without modification.

Typical sizes: 15-55 KB per file (100-250 loaded DLLs).
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCCA_MAGIC: bytes = b"SCCA"
_PF_VERSION: int = 30
_FILE_INFO_OFFSET: int = 84
_FILE_INFO_SIZE: int = 224          # v30: same as v26
_SECTIONS_START: int = _FILE_INFO_OFFSET + _FILE_INFO_SIZE  # = 308

_SECTION_A_ENTRY_SIZE: int = 32
_SECTION_B_ENTRY_SIZE: int = 12

# Device path for C: drive (appears in Section D)
_VOLUME_DEVICE_PATH: str = r"\DEVICE\HARDDISKVOLUME3"

# FILETIME epoch delta (100-ns ticks from 1601-01-01 to 1970-01-01)
_FILETIME_EPOCH: int = 116_444_736_000_000_000

# ---------------------------------------------------------------------------
# DLL pools — every Windows app loads these regardless of profile
# ---------------------------------------------------------------------------

_SYSTEM32 = r"\DEVICE\HARDDISKVOLUME3\WINDOWS\SYSTEM32"
_SYSWOW64 = r"\DEVICE\HARDDISKVOLUME3\WINDOWS\SYSWOW64"
_WIN_ROOT  = r"\DEVICE\HARDDISKVOLUME3\WINDOWS"

_UNIVERSAL_DLLS: List[str] = [
    f"{_SYSTEM32}\\NTDLL.DLL",
    f"{_SYSTEM32}\\KERNEL32.DLL",
    f"{_SYSTEM32}\\KERNELBASE.DLL",
    f"{_SYSTEM32}\\USER32.DLL",
    f"{_SYSTEM32}\\GDI32.DLL",
    f"{_SYSTEM32}\\WIN32U.DLL",
    f"{_SYSTEM32}\\GDI32FULL.DLL",
    f"{_SYSTEM32}\\COMBASE.DLL",
    f"{_SYSTEM32}\\RPCRT4.DLL",
    f"{_SYSTEM32}\\SECHOST.DLL",
    f"{_SYSTEM32}\\ADVAPI32.DLL",
    f"{_SYSTEM32}\\MSVCRT.DLL",
    f"{_SYSTEM32}\\UCRTBASE.DLL",
    f"{_SYSTEM32}\\BCRYPTPRIMITIVES.DLL",
    f"{_SYSTEM32}\\BCRYPT.DLL",
    f"{_SYSTEM32}\\NTOSKRNL.EXE",
    f"{_SYSTEM32}\\COMDLG32.DLL",
    f"{_SYSTEM32}\\SHELL32.DLL",
    f"{_SYSTEM32}\\SHLWAPI.DLL",
    f"{_SYSTEM32}\\WINDOWS.STORAGE.DLL",
    f"{_SYSTEM32}\\WLDP.DLL",
    f"{_SYSTEM32}\\PROFAPI.DLL",
    f"{_SYSTEM32}\\SHCORE.DLL",
    f"{_SYSTEM32}\\MSVCP_WIN.DLL",
    f"{_SYSTEM32}\\CFGMGR32.DLL",
    f"{_SYSTEM32}\\MSCTF.DLL",
    f"{_SYSTEM32}\\IMM32.DLL",
    f"{_SYSTEM32}\\OLEAUT32.DLL",
    f"{_SYSTEM32}\\OLE32.DLL",
    f"{_SYSTEM32}\\CLBCATQ.DLL",
    f"{_SYSTEM32}\\CRYPTBASE.DLL",
    f"{_SYSTEM32}\\SSPICLI.DLL",
    f"{_SYSTEM32}\\IMAGEHLP.DLL",
    f"{_SYSTEM32}\\SETUPAPI.DLL",
    f"{_SYSTEM32}\\DEVOBJ.DLL",
    f"{_SYSTEM32}\\WINTRUST.DLL",
    f"{_SYSTEM32}\\CRYPT32.DLL",
    f"{_SYSTEM32}\\MSASN1.DLL",
    f"{_SYSTEM32}\\WININET.DLL",
    f"{_SYSTEM32}\\IERTUTIL.DLL",
    f"{_SYSTEM32}\\ONECORECOMMONPROXY.DLL",
    f"{_SYSTEM32}\\ONECOREUAPCOMMONPROXY.DLL",
    f"{_SYSTEM32}\\URLMON.DLL",
    f"{_SYSTEM32}\\USERENV.DLL",
    f"{_SYSTEM32}\\WINSTA.DLL",
    f"{_SYSTEM32}\\NTMARTA.DLL",
    f"{_SYSTEM32}\\POWRPROF.DLL",
    f"{_SYSTEM32}\\MSWSOCK.DLL",
    f"{_SYSTEM32}\\WS2_32.DLL",
    f"{_SYSTEM32}\\NSI.DLL",
    f"{_SYSTEM32}\\DHCPCSVC6.DLL",
    f"{_SYSTEM32}\\DHCPCSVC.DLL",
    f"{_WIN_ROOT}\\SYSTEMAPPS\\MICROSOFT.WINDOWS.STARTMENUEXPERIENCEHOST_CW5N1H2TXYEWY\\STARTMENUEXPERIENCEHOST.EXE",
    f"{_WIN_ROOT}\\SYSTEMAPPS\\MICROSOFT.WINDOWS.SEARCH_CW5N1H2TXYEWY\\SEARCHAPP.EXE",
    f"{_SYSTEM32}\\DXGI.DLL",
    f"{_SYSTEM32}\\D3D11.DLL",
    f"{_SYSTEM32}\\D3D12.DLL",
    f"{_SYSTEM32}\\DCOMP.DLL",
    f"{_SYSTEM32}\\DWMAPI.DLL",
    f"{_SYSTEM32}\\UXTheme.DLL",
    f"{_SYSTEM32}\\MSFTEDIT.DLL",
    f"{_SYSTEM32}\\COMCTL32.DLL",
    f"{_SYSTEM32}\\COML2.DLL",
    f"{_SYSTEM32}\\WUCEFFECTS.DLL",
    f"{_SYSTEM32}\\WINDOWS.UI.DLL",
    f"{_SYSTEM32}\\TWINAPI.DLL",
    f"{_SYSTEM32}\\TWINAPI.APPCORE.DLL",
    f"{_SYSTEM32}\\RMCLIENT.DLL",
    f"{_SYSTEM32}\\POLICYMANAGER.DLL",
    f"{_SYSTEM32}\\MRMCORER.DLL",
    f"{_SYSTEM32}\\NINPUT.DLL",
    f"{_SYSTEM32}\\TEXTINPUTFRAMEWORK.DLL",
    f"{_SYSTEM32}\\INPUTHOST.DLL",
    f"{_SYSTEM32}\\COML2.DLL",
]

_OFFICE_DLLS: List[str] = [
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOERES.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSORIENT.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOAUTH.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOFORM.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSO.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOPENAPIV3.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOINTL.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOCOAUTH.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSOSB.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OUTLLIB.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OUTLFLTR.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OLMAPI32.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OSFVIEWER.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\WWLIB.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\WINWORD.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\MSEXCL.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\EXCEL.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\OUTLOOK.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\POWERPNT.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\MICROSOFT OFFICE\ROOT\OFFICE16\PPCORE.DLL",
]

_CHROME_DLLS: List[str] = [
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\CHROME_ELF.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\ELEVATION_SERVICE.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GOOGLE\CHROME\APPLICATION\NACL64.EXE",
]

_EDGE_DLLS: List[str] = [
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES (X86)\MICROSOFT\EDGE\APPLICATION\MSEDGE.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES (X86)\MICROSOFT\EDGE\APPLICATION\MSEDGE.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES (X86)\MICROSOFT\EDGE\APPLICATION\MSEDGE_ELF.DLL",
]

_DEV_DLLS: List[str] = [
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GIT\CMD\GIT.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GIT\MINGW64\BIN\GIT.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GIT\USR\BIN\MSYS-2.0.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\GIT\MINGW64\BIN\LIBCURL-4.DLL",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\NODEJS\NODE.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\NODEJS\NPM.CMD",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\DOCKER\DOCKER\DOCKER DESKTOP.EXE",
    r"\DEVICE\HARDDISKVOLUME3\PROGRAM FILES\DOCKER\DOCKER\DOCKER.EXE",
]

_SYSTEM_DLLS_EXTRA: List[str] = [
    f"{_SYSTEM32}\\APPXDEPLOYMENTCLIENT.DLL",
    f"{_SYSTEM32}\\CDPRT.DLL",
    f"{_SYSTEM32}\\CDPSVC.DLL",
    f"{_SYSTEM32}\\USOAPI.DLL",
    f"{_SYSTEM32}\\CABINET.DLL",
    f"{_SYSTEM32}\\WTSAPI32.DLL",
    f"{_SYSTEM32}\\SLWGA.DLL",
    f"{_SYSTEM32}\\SRVCLI.DLL",
    f"{_SYSTEM32}\\NETUTILS.DLL",
    f"{_SYSTEM32}\\WKSCLI.DLL",
    f"{_SYSTEM32}\\LOGONCLI.DLL",
    f"{_SYSTEM32}\\DSROLE.DLL",
    f"{_SYSTEM32}\\NETLOGON.DLL",
    f"{_SYSTEM32}\\NETAPI32.DLL",
    f"{_SYSTEM32}\\SAMLIB.DLL",
    f"{_SYSTEM32}\\KERBEROS.DLL",
    f"{_SYSTEM32}\\MSV1_0.DLL",
    f"{_SYSTEM32}\\LSASRV.DLL",
    f"{_SYSTEM32}\\CRYPTSP.DLL",
    f"{_SYSTEM32}\\RSAENH.DLL",
    f"{_SYSTEM32}\\APPHELP.DLL",
    f"{_SYSTEM32}\\COREMESSAGING.DLL",
    f"{_SYSTEM32}\\WUAPI.DLL",
    f"{_SYSTEM32}\\WUAUENG.DLL",
    f"{_SYSTEM32}\\WSCSVC.DLL",
    f"{_SYSTEM32}\\GAMEUX.DLL",
    f"{_SYSTEM32}\\MFPLAT.DLL",
    f"{_SYSTEM32}\\MFREADWRITE.DLL",
    f"{_SYSTEM32}\\AVRT.DLL",
    f"{_SYSTEM32}\\MMDEVAPI.DLL",
    f"{_SYSTEM32}\\AUDIOSES.DLL",
    f"{_SYSTEM32}\\KSUSER.DLL",
    f"{_SYSTEM32}\\RESUTILS.DLL",
    f"{_SYSTEM32}\\WBEM\\FASTPROX.DLL",
    f"{_SYSTEM32}\\WBEM\\WBEMSVC.DLL",
    f"{_SYSTEM32}\\WBEM\\WBEMPROX.DLL",
    f"{_SYSTEM32}\\WBEM\\WBEMCOMN.DLL",
    f"{_SYSTEM32}\\VSSMGR.EXE",
    f"{_SYSTEM32}\\VSSTRACE.DLL",
    f"{_SYSTEM32}\\TASKSCHD.DLL",
    f"{_SYSTEM32}\\MSTASK.DLL",
    f"{_SYSTEM32}\\XMLLITE.DLL",
    f"{_SYSTEM32}\\MSXML3.DLL",
    f"{_SYSTEM32}\\MSXML6.DLL",
    f"{_SYSTEM32}\\WINDOWS.GLOBALIZATION.DLL",
    f"{_SYSTEM32}\\TZRES.DLL",
    f"{_SYSTEM32}\\MUI\\0409\\TZRES.DLL.MUI",
    f"{_SYSTEM32}\\PCACLI.DLL",
    f"{_SYSTEM32}\\SPPC.DLL",
    f"{_SYSTEM32}\\SPPWINOB.DLL",
    f"{_SYSTEM32}\\PROPSYS.DLL",
    f"{_SYSTEM32}\\OLEACC.DLL",
    f"{_SYSTEM32}\\UIAUTOMATIONCORE.DLL",
    f"{_SYSTEM32}\\FRAMEDYNOS.DLL",
    f"{_SYSTEM32}\\FRAMEDYN.DLL",
    f"{_SYSTEM32}\\WTSAPI32.DLL",
    f"{_SYSTEM32}\\DPAPI.DLL",
    f"{_SYSTEM32}\\WINHTTPCOM.DLL",
    f"{_SYSTEM32}\\WINHTTP.DLL",
    f"{_SYSTEM32}\\ONDEMANDCONNROUTEHELPER.DLL",
    f"{_SYSTEM32}\\MSWB7.DLL",
    f"{_SYSTEM32}\\BTHPAN.DLL",
    f"{_SYSTEM32}\\NLAAPI.DLL",
    f"{_SYSTEM32}\\DNSAPI.DLL",
    f"{_SYSTEM32}\\IPHLPAPI.DLL",
    f"{_SYSTEM32}\\WINNSI.DLL",
    f"{_SYSTEM32}\\FWBASE.DLL",
    f"{_SYSTEM32}\\WFAPIGP.DLL",
    f"{_SYSTEM32}\\MPSSVC.DLL",
    f"{_SYSTEM32}\\MPSDRV.SYS",
    f"{_SYSTEM32}\\DRIVERS\\TCPIP.SYS",
    f"{_SYSTEM32}\\DRIVERS\\HTTP.SYS",
    f"{_WIN_ROOT}\\WINSXS\\AMD64_MICROSOFT.WINDOWS.COMMON-CONTROLS_6595B64144CCF1DF_6.0.19041.1_NONE_60B8B9EB71F62BAE\\COMCTL32.DLL",
    f"{_WIN_ROOT}\\WINSXS\\AMD64_MICROSOFT.VC90.CRT_1FC8B3B9A1E18E3B_9.0.30729.9635_NONE_08E4299FA83D7E3C\\MSVCR90.DLL",
]

# Profile-specific application specs
_PROFILE_APPS: Dict[str, List[Dict[str, Any]]] = {
    "office_user": [
        {"name": "OUTLOOK.EXE",  "path": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",  "runs": (50, 200),  "extra_dlls": _OFFICE_DLLS},
        {"name": "WINWORD.EXE",  "path": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",  "runs": (30, 150),  "extra_dlls": _OFFICE_DLLS},
        {"name": "EXCEL.EXE",    "path": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",    "runs": (20, 100),  "extra_dlls": _OFFICE_DLLS},
        {"name": "POWERPNT.EXE", "path": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE", "runs": (10, 50),   "extra_dlls": _OFFICE_DLLS},
        {"name": "MS-TEAMS.EXE", "path": r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe",           "runs": (100, 500), "extra_dlls": _EDGE_DLLS},
        {"name": "CHROME.EXE",   "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",        "runs": (200, 800), "extra_dlls": _CHROME_DLLS},
        {"name": "MSEDGE.EXE",   "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "runs": (50, 200),  "extra_dlls": _EDGE_DLLS},
    ],
    "developer": [
        {"name": "CODE.EXE",            "path": r"C:\Users\{username}\AppData\Local\Programs\Microsoft VS Code\Code.exe",   "runs": (200, 800), "extra_dlls": _EDGE_DLLS},
        {"name": "DOCKER DESKTOP.EXE",  "path": r"C:\Program Files\Docker\Docker\Docker Desktop.exe",                      "runs": (50, 200),  "extra_dlls": _DEV_DLLS},
        {"name": "WT.EXE",              "path": r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal\wt.exe",           "runs": (300, 1000),"extra_dlls": []},
        {"name": "GIT.EXE",             "path": r"C:\Program Files\Git\cmd\git.exe",                                        "runs": (100, 500), "extra_dlls": _DEV_DLLS},
        {"name": "NODE.EXE",            "path": r"C:\Program Files\nodejs\node.exe",                                        "runs": (50, 200),  "extra_dlls": _DEV_DLLS},
        {"name": "CHROME.EXE",          "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",                   "runs": (200, 800), "extra_dlls": _CHROME_DLLS},
        {"name": "MSEDGE.EXE",          "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",            "runs": (50, 200),  "extra_dlls": _EDGE_DLLS},
    ],
    "home_user": [
        {"name": "CHROME.EXE",   "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",        "runs": (300, 1000),"extra_dlls": _CHROME_DLLS},
        {"name": "MSEDGE.EXE",   "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "runs": (100, 400), "extra_dlls": _EDGE_DLLS},
        {"name": "SPOTIFY.EXE",  "path": r"C:\Users\{username}\AppData\Roaming\Spotify\Spotify.exe",      "runs": (100, 500), "extra_dlls": []},
        {"name": "VLC.EXE",      "path": r"C:\Program Files\VideoLAN\VLC\vlc.exe",                        "runs": (30, 150),  "extra_dlls": []},
        {"name": "ONEDRIVE.EXE", "path": r"C:\Program Files\Microsoft OneDrive\OneDrive.exe",             "runs": (50, 200),  "extra_dlls": []},
    ],
}

_COMMON_APPS: List[Dict[str, Any]] = [
    {"name": "EXPLORER.EXE",       "path": r"C:\Windows\explorer.exe",                          "runs": (500, 2000), "extra_dlls": []},
    {"name": "DLLHOST.EXE",        "path": r"C:\Windows\System32\dllhost.exe",                  "runs": (100, 500),  "extra_dlls": []},
    {"name": "SVCHOST.EXE",        "path": r"C:\Windows\System32\svchost.exe",                  "runs": (200, 1000), "extra_dlls": []},
    {"name": "TASKHOSTW.EXE",      "path": r"C:\Windows\System32\taskhostw.exe",                "runs": (50, 200),   "extra_dlls": []},
    {"name": "RUNTIMEBROKER.EXE",  "path": r"C:\Windows\System32\RuntimeBroker.exe",            "runs": (100, 400),  "extra_dlls": []},
    {"name": "SEARCHHOST.EXE",     "path": r"C:\Windows\SystemApps\SearchHost.exe",             "runs": (50, 200),   "extra_dlls": []},
    {"name": "NOTEPAD.EXE",        "path": r"C:\Windows\System32\notepad.exe",                  "runs": (20, 100),   "extra_dlls": []},
    {"name": "CMD.EXE",            "path": r"C:\Windows\System32\cmd.exe",                      "runs": (10, 50),    "extra_dlls": []},
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PrefetchError(Exception):
    """Raised when Prefetch file generation fails."""


# ---------------------------------------------------------------------------
# Hash algorithm — SCCAv1 (djb2 variant on uppercase path)
# ---------------------------------------------------------------------------

def _prefetch_hash(path: str) -> int:
    """Compute Windows prefetch filename hash for an executable path.

    Uses the SCCAv1 algorithm: multiply-accumulate over uppercase UTF-16LE
    code units starting from 0.
    """
    h = 0
    for ch in path.upper():
        h = (h * 37 + ord(ch)) & 0xFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# FILETIME helper
# ---------------------------------------------------------------------------

def _to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ticks = int((dt - epoch).total_seconds() * 1e7)
    return ticks + _FILETIME_EPOCH


# ---------------------------------------------------------------------------
# Binary builders
# ---------------------------------------------------------------------------

def _build_section_c(filenames: List[str]) -> Tuple[bytes, List[int]]:
    """Build Section C (filename strings) as contiguous UTF-16LE data.

    Returns:
        (section_c_bytes, offsets) where offsets[i] is the byte offset of
        filenames[i] within section_c_bytes.
    """
    buf = bytearray()
    offsets: List[int] = []
    for name in filenames:
        offsets.append(len(buf))
        buf += name.encode("utf-16-le") + b"\x00\x00"
    return bytes(buf), offsets


def _build_section_a(
    filenames: List[str],
    offsets_c: List[int],
    rng: Random,
    base_filetime: int,
) -> bytes:
    """Build Section A (file metrics array).

    Each entry is 32 bytes:
        0x00  UINT32  start_time (relative, microseconds)
        0x04  UINT32  duration
        0x08  UINT32  average_duration
        0x0C  UINT32  filename_string_offset (bytes into Section C)
        0x10  UINT16  filename_string_length (wchars, not incl. null)
        0x12  UINT16  flags
        0x14  UINT64  file_reference (MFT ref: seq<<48 | low)
        0x1C  UINT32  padding
    """
    buf = bytearray()
    for i, name in enumerate(filenames):
        start_us = rng.randint(0, 500_000)
        dur_us = rng.randint(100, 50_000)
        avg_us = (start_us + dur_us) // 2
        c_off = offsets_c[i]
        c_len = len(name)          # length in wchars
        flags = 0x0002             # PREFETCH_FILE_METRICS_FLAG_PREFETCHED
        # MFT ref: high 16 = sequence, low 48 = record number
        seq = rng.randint(1, 255)
        rec = rng.randint(2, 0xFFFF)
        file_ref = (seq << 48) | rec
        buf += struct.pack(
            "<IIIIHHQi",
            start_us, dur_us, avg_us,
            c_off, c_len, flags,
            file_ref, 0,
        )
    return bytes(buf)


def _build_section_b(count: int, rng: Random) -> bytes:
    """Build Section B (trace chains).

    Each entry is 12 bytes:
        0x00  UINT32  next_entry_index (0xFFFFFFFF = end of chain)
        0x04  UINT32  total_block_load_count
        0x08  UINT32  unknown
    """
    buf = bytearray()
    for i in range(count):
        next_idx = i + 1 if i < count - 1 else 0xFFFFFFFF
        load_count = rng.randint(1, 128)
        buf += struct.pack("<III", next_idx, load_count, 0)
    return bytes(buf)


def _build_section_d(vol_serial: int, vol_creation_filetime: int) -> bytes:
    """Build Section D (volume information) — one C: volume entry.

    Simplified structure (96-byte fixed header + device path):
        0x00  UINT32  device_path_offset (relative to entry start)
        0x04  UINT16  device_path_length (in wchars, excl null)
        0x06  UINT16  unknown
        0x08  UINT64  creation_time
        0x10  UINT32  serial_number
        0x14  UINT32  file_refs_offset
        0x18  UINT32  file_refs_size
        0x1C  UINT32  dir_strings_offset
        0x20  UINT32  dir_strings_count
        0x24  ... (padding to 96 bytes)
        0x60  <device path UTF-16LE>
    """
    dev_path_utf16 = _VOLUME_DEVICE_PATH.encode("utf-16-le")
    dev_path_offset = 96                  # right after the fixed header
    dev_path_len = len(_VOLUME_DEVICE_PATH)

    hdr = bytearray(96)
    struct.pack_into("<I", hdr,  0, dev_path_offset)
    struct.pack_into("<H", hdr,  4, dev_path_len)
    struct.pack_into("<H", hdr,  6, 0)
    struct.pack_into("<Q", hdr,  8, vol_creation_filetime)
    struct.pack_into("<I", hdr, 16, vol_serial)
    struct.pack_into("<I", hdr, 20, 0)  # file_refs_offset (no refs)
    struct.pack_into("<I", hdr, 24, 0)  # file_refs_size
    struct.pack_into("<I", hdr, 28, 0)  # dir_strings_offset
    struct.pack_into("<I", hdr, 32, 0)  # dir_strings_count

    return bytes(hdr) + dev_path_utf16 + b"\x00\x00"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PrefetchService(BaseService):
    """Creates Windows Prefetch v30 files for simulated application history.

    Generates .pf files in Windows/Prefetch/ with a properly structured
    SCCA v30 binary format (header + 4 sections), sized 15-55 KB each.

    Args:
        mount_manager:     Resolves paths against the mounted image root.
        timestamp_service: Provides timestamps for file operations.
        audit_logger:      Structured audit logging.
    """

    def __init__(self, mount_manager: Any, timestamp_service: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "PrefetchService"

    def apply(self, ctx: "ServiceContext") -> None:
        """Generate Prefetch files driven by the ServiceContext.

        Raises:
            PrefetchError: If Prefetch generation fails.
        """
        username = ctx.identity_bundle.user.username
        profile_type = ctx.persona.profile_archetype
        seed = ctx.identity_bundle.user.computer_name
        timeline_days = ctx.persona.timeline_days

        rng = Random(hash(seed + profile_type))
        prefetch_dir = Path("Windows") / "Prefetch"
        created_files = 0

        try:
            full_prefetch = self._mount.resolve(str(prefetch_dir))
            full_prefetch.mkdir(parents=True, exist_ok=True)

            now = datetime.now(timezone.utc)

            # Use AI-generated seed if available; fall back to static profile tables
            expansion = getattr(ctx, "expansion", None)
            expansion_prefetch = getattr(expansion, "prefetch", None) if expansion is not None else None

            if expansion_prefetch is not None:
                app_specs = [
                    {
                        "name": e.exe_name,
                        "path": e.exe_path,
                        "runs": (e.run_count, e.run_count),
                        "extra_dlls": [],
                        "last_run": now - timedelta(hours=e.last_run_offset_h),
                    }
                    for e in expansion_prefetch.entries
                ]
            else:
                app_specs = _COMMON_APPS.copy()
                app_specs.extend(_PROFILE_APPS.get(profile_type, _PROFILE_APPS["office_user"]))

            for app_spec in app_specs:
                app_name = app_spec["name"]
                app_path = app_spec["path"].replace("{username}", username)
                run_range = app_spec.get("runs", (10, 100))
                extra_dlls: List[str] = app_spec.get("extra_dlls", [])

                pf_hash = _prefetch_hash(app_path)
                pf_filename = f"{app_name}-{pf_hash:08X}.pf"
                pf_rel = prefetch_dir / pf_filename

                run_count = rng.randint(*run_range)
                last_run = app_spec.get("last_run") or (
                    now - timedelta(minutes=rng.randint(0, timeline_days * 60 * 8))
                )

                content = self._build_pf_file(
                    app_name=app_name,
                    app_path=app_path,
                    run_count=run_count,
                    last_run=last_run,
                    extra_dlls=extra_dlls,
                    rng=rng,
                )

                full_path = self._mount.resolve(str(pf_rel))
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_bytes(content)
                created_files += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_prefetch_files",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })
            logger.info(
                "Generated %d Prefetch files (v30) for profile '%s'",
                created_files, profile_type,
            )

        except Exception as exc:
            raise PrefetchError(f"Prefetch generation failed: {exc}") from exc

    # -- v30 binary builder -------------------------------------------------

    def _build_pf_file(
        self,
        app_name: str,
        app_path: str,
        run_count: int,
        last_run: datetime,
        extra_dlls: List[str],
        rng: Random,
    ) -> bytes:
        """Build a complete SCCA v30 prefetch file.

        Args:
            app_name:   Executable basename (e.g. ``"CHROME.EXE"``).
            app_path:   Full win32 path (used to compute the hash).
            run_count:  Number of times the app ran.
            last_run:   UTC datetime of most recent run.
            extra_dlls: Profile-specific DLL paths to include in Section C.
            rng:        RNG for jitter in metrics values.

        Returns:
            Raw bytes for the .pf file (15-55 KB uncompressed).
        """
        pf_hash = _prefetch_hash(app_path)
        last_run_ft = _to_filetime(last_run)

        # Build the filename list for Section C
        filenames = self._build_filename_list(app_path, extra_dlls, rng)

        # Section C
        sec_c_bytes, offsets_c = _build_section_c(filenames)

        # Section A
        sec_a_bytes = _build_section_a(filenames, offsets_c, rng, last_run_ft)

        # Section B — trace chains (roughly 2× file count for realism)
        trace_count = max(len(filenames), rng.randint(len(filenames), len(filenames) * 2))
        sec_b_bytes = _build_section_b(trace_count, rng)

        # Section D
        vol_serial = rng.randint(0x10000000, 0xEFFFFFFF)
        vol_creation = last_run_ft - rng.randint(10**15, 10**16)
        sec_d_bytes = _build_section_d(vol_serial, vol_creation)

        # Compute section offsets (all relative to file start)
        sec_a_off = _SECTIONS_START
        sec_b_off = sec_a_off + len(sec_a_bytes)
        sec_c_off = sec_b_off + len(sec_b_bytes)
        sec_d_off = sec_c_off + len(sec_c_bytes)

        total_size = sec_d_off + len(sec_d_bytes)

        # Build 8 last-run timestamps (most recent first, earlier ones jittered back)
        last_runs: List[int] = []
        ts = last_run_ft
        for _ in range(8):
            last_runs.append(ts)
            ts -= rng.randint(3600 * 10**7, 48 * 3600 * 10**7)  # 1h–48h back

        # ---- File Header (84 bytes) ----
        header = bytearray(84)
        struct.pack_into("<I", header,  0, _PF_VERSION)
        header[4:8] = _SCCA_MAGIC
        struct.pack_into("<I", header,  8, 0x0F)         # unknown, typically 0x0F
        struct.pack_into("<I", header, 12, total_size)
        # Exe name: 60 bytes = 29 wchars + null, UTF-16LE
        exe_utf16 = app_name.encode("utf-16-le")[:58]
        header[16:16 + len(exe_utf16)] = exe_utf16
        struct.pack_into("<I", header, 76, pf_hash)
        struct.pack_into("<I", header, 80, 0)

        # ---- File Information block (224 bytes at offset 84) ----
        info = bytearray(224)
        struct.pack_into("<I", info,  0, sec_a_off)
        struct.pack_into("<I", info,  4, len(filenames))
        struct.pack_into("<I", info,  8, sec_b_off)
        struct.pack_into("<I", info, 12, trace_count)
        struct.pack_into("<I", info, 16, sec_c_off)
        struct.pack_into("<I", info, 20, len(sec_c_bytes))
        struct.pack_into("<I", info, 24, sec_d_off)
        struct.pack_into("<I", info, 28, 1)              # volume count
        struct.pack_into("<I", info, 32, len(sec_d_bytes))
        struct.pack_into("<I", info, 36, 0)              # unknown
        # last_run_times at offset 40 (0x28)
        for i, ft in enumerate(last_runs):
            struct.pack_into("<Q", info, 40 + i * 8, ft)
        struct.pack_into("<I", info, 104, 0)             # unknown at 0x68
        struct.pack_into("<I", info, 108, run_count)
        struct.pack_into("<I", info, 112, 0)
        struct.pack_into("<I", info, 116, 0)

        return bytes(header) + bytes(info) + sec_a_bytes + sec_b_bytes + sec_c_bytes + sec_d_bytes

    @staticmethod
    def _build_filename_list(
        app_path: str,
        extra_dlls: List[str],
        rng: Random,
    ) -> List[str]:
        """Build the list of filenames for Section C.

        Converts win32 paths to device paths, combines universal DLLs with
        app-specific ones, and samples from extra system DLLs to hit a
        realistic file-count (100-250 entries).
        """
        # Convert app_path C:\... → \DEVICE\HARDDISKVOLUME3\...
        dev_app_path = app_path.replace("C:\\", r"\DEVICE\HARDDISKVOLUME3\\").upper()

        base: List[str] = [dev_app_path] + _UNIVERSAL_DLLS[:]
        base.extend(extra_dlls)

        # Deduplicate preserving order
        seen: set = set()
        unique: List[str] = []
        for p in base:
            up = p.upper()
            if up not in seen:
                seen.add(up)
                unique.append(p.upper())

        # Sample from extra system DLLs to reach 120-220 total entries
        target = rng.randint(120, 220)
        pool = [p.upper() for p in _SYSTEM_DLLS_EXTRA if p.upper() not in seen]
        rng.shuffle(pool)
        for p in pool:
            if len(unique) >= target:
                break
            unique.append(p)
            seen.add(p)

        return unique
