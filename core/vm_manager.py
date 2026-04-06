"""Offline VHDX volume management for artifact injection (Windows 11 Home Compatible)."""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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

    def mount_vhdx(self) -> str:
        """Mount the VHDX and discover the Windows partition drive letter.
        
        Returns:
            The drive letter of the Windows partition (e.g., 'E:\\').
        """
        if self.mounted_drive:
            return self.mounted_drive
            
        logger.info("Mounting disk image: %s", self.vhdx_path)
        
        # Mount the disk image using generic Windows cmdlets (works on Home edition)
        self._run_powershell(f"Mount-DiskImage -ImagePath '{self.vhdx_path}' -NoDriveLetter:$false")
        time.sleep(3)  # Give Windows a moment to assign drive letters
        
        # Find the OS partition by looking for the Windows directory on newly attached volumes associated with this image
        script = f"""
        $image = Get-DiskImage -ImagePath '{self.vhdx_path}'
        if (-not $image) {{ exit }}
        
        $volumes = $image | Get-Disk | Get-Partition | Get-Volume
        
        foreach ($vol in $volumes) {{
            if ($vol.DriveLetter) {{
                $testPath = $vol.DriveLetter + ":\\Windows"
                if (Test-Path $testPath) {{
                    Write-Output ($vol.DriveLetter + ":\\")
                    exit
                }}
            }}
        }}
        """
        
        drive = self._run_powershell(script)
        
        if not drive:
            # Cleanup on failure
            self.dismount_vhdx()
            raise VMManagerError("Failed to locate Windows partition on mounted VHDX.")
            
        logger.info("Discovered Windows partition at %s", drive)
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
