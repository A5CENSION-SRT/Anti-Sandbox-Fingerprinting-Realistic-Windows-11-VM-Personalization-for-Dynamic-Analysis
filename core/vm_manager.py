"""Offline VHDX volume management for artifact injection (Windows 11 Home Compatible)."""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MOUNT_DISCOVERY_ATTEMPTS = 8
_MOUNT_DISCOVERY_DELAY_SECONDS = 1.0


class VMManagerError(Exception):
    """Raised for errors in VHDX offline operations."""
    pass


class VMManager:
    """Manages offline volume mounting for Windows VHD/VHDX files.
    
    Uses standard Windows storage cmdlets (Mount-DiskImage) available
    on all editions of Windows, including Home, to mount offline disks
    and inject artifacts into the dormant Windows partition.
    """

    def __init__(self, vhdx_path: str):
        self.vhdx_path = Path(vhdx_path).resolve()
        if not self.vhdx_path.exists():
            raise FileNotFoundError(f"Disk image not found: {self.vhdx_path}")
            
        # The drive letter assigned to the Windows partition after mounting
        self.mounted_drive: Optional[str] = None

    def _run_powershell(self, command: str) -> str:
        """Execute a PowerShell command and return its stdout."""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"PowerShell command failed: {e.stderr.strip()}")
            raise VMManagerError(f"PowerShell error: {e.stderr.strip()}") from e

    def stop_vm(self, vm_name: str, force: bool = False) -> None:
        """Not supported on Windows Home (Hyper-V API required)."""
        logger.warning("VM Start/Stop automation requires Hyper-V. Ensure your VM is powered off manually.")

    @staticmethod
    def _normalize_drive(drive_text: str) -> str:
        """Normalize PowerShell drive output to `X:\\` form."""
        drive = drive_text.strip().replace("/", "\\")
        if len(drive) >= 2 and drive[1] == ":":
            drive = drive[0].upper() + drive[1:]
            if not drive.endswith("\\"):
                drive += "\\"
            return drive
        return ""

    def _discover_mounted_drive(self) -> str:
        """Discover the most likely writable OS/data partition drive letter.

        Returns an empty string when no usable drive can be discovered yet.
        """
        script = f"""
        $image = Get-DiskImage -ImagePath '{self.vhdx_path}' -ErrorAction SilentlyContinue
        if (-not $image) {{ return }}

        $disk = $image | Get-Disk -ErrorAction SilentlyContinue
        if (-not $disk) {{ return }}

        $partitions = $disk | Get-Partition | Sort-Object -Property Size -Descending
        if (-not $partitions) {{ return }}

        $usedLetters = @(
            Get-Volume |
            Where-Object {{ $_.DriveLetter }} |
            ForEach-Object {{ [string]$_.DriveLetter }}
        )

        $preferredLetters = @(
            'Z','Y','X','W','V','U','T','S','R','Q','P','O','N',
            'M','L','K','J','I','H','G','F','E','D'
        )

        $candidates = New-Object System.Collections.Generic.List[string]

        foreach ($part in $partitions) {{
            $vol = $null
            try {{
                $vol = $part | Get-Volume -ErrorAction Stop
            }} catch {{
                continue
            }}

            if (-not $vol) {{
                continue
            }}

            if (-not $vol.DriveLetter) {{
                $freeLetter = $preferredLetters | Where-Object {{ $_ -notin $usedLetters }} | Select-Object -First 1
                if ($freeLetter) {{
                    try {{
                        Set-Partition -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -NewDriveLetter $freeLetter -ErrorAction Stop
                        Start-Sleep -Milliseconds 400
                        $usedLetters += $freeLetter
                        $vol = $part | Get-Volume -ErrorAction SilentlyContinue
                    }} catch {{
                        # Some partitions (EFI/recovery) cannot be assigned a drive letter.
                    }}
                }}
            }}

            if ($vol -and $vol.DriveLetter) {{
                $drive = "$($vol.DriveLetter):\\"
                if (Test-Path ($drive + 'Windows')) {{
                    Write-Output $drive
                    return
                }}

                if (-not $candidates.Contains($drive)) {{
                    $candidates.Add($drive)
                }}
            }}
        }}

        if ($candidates.Count -gt 0) {{
            Write-Output $candidates[0]
        }}
        """

        output = self._run_powershell(script)
        for line in output.splitlines():
            drive = self._normalize_drive(line)
            if drive:
                return drive

        return ""

    def mount_vhdx(self) -> str:
        """Mount the VHDX and discover the best writable partition drive letter.
        
        Returns:
            The selected drive letter (e.g., 'E:\\').
        """
        if self.mounted_drive:
            return self.mounted_drive
            
        logger.info("Mounting disk image: %s", self.vhdx_path)
        
        # Mount the disk image using generic Windows cmdlets (works on Home edition)
        self._run_powershell(f"Mount-DiskImage -ImagePath '{self.vhdx_path}' -NoDriveLetter:$false")
        time.sleep(_MOUNT_DISCOVERY_DELAY_SECONDS)

        drive = ""
        for attempt in range(_MOUNT_DISCOVERY_ATTEMPTS):
            drive = self._discover_mounted_drive()
            if drive:
                break
            logger.debug(
                "Drive discovery attempt %d/%d returned no candidate",
                attempt + 1,
                _MOUNT_DISCOVERY_ATTEMPTS,
            )
            if attempt < _MOUNT_DISCOVERY_ATTEMPTS - 1:
                time.sleep(_MOUNT_DISCOVERY_DELAY_SECONDS)
        
        if not drive:
            # Cleanup on failure
            self.dismount_vhdx()
            raise VMManagerError("Failed to locate Windows partition on mounted VHDX.")

        windows_dir = Path(f"{drive}Windows")
        if not windows_dir.exists():
            logger.warning(
                "Mounted drive %s does not expose a Windows directory; using best available partition.",
                drive,
            )
            
        logger.info("Discovered mounted partition at %s", drive)
        self.mounted_drive = drive
        return drive

    def dismount_vhdx(self) -> None:
        """Dismount the VHDX file safely."""
        logger.info("Dismounting disk image: %s", self.vhdx_path)
        try:
            self._run_powershell(f"Dismount-DiskImage -ImagePath '{self.vhdx_path}'")
            self.mounted_drive = None
            logger.info("Successfully dismounted image.")
        except VMManagerError as e:
            logger.warning("Error during dismount: %s", e)

    def start_vm(self, vm_name: str) -> None:
        """Not supported on Windows Home (Hyper-V API required)."""
        if self.mounted_drive:
            self.dismount_vhdx()
        logger.warning("VM Start automation requires Hyper-V. You can boot your VM manually now.")
