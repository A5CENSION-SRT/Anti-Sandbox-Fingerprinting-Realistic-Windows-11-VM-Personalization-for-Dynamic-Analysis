"""Hyper-V VM and VHDX volume management for offline artifact injection."""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class VMManagerError(Exception):
    """Raised for errors in VM/VHDX operations."""
    pass


class VMManager:
    """Manages offline volume mounting for Hyper-V Windows VMs.
    
    This allows ARC to write artifacts directly to the dormant Windows partition.
    """

    def __init__(self, vhdx_path: str):
        self.vhdx_path = Path(vhdx_path).resolve()
        if not self.vhdx_path.exists():
            raise FileNotFoundError(f"VHDX file not found: {self.vhdx_path}")
            
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

    def get_vm_state(self, vm_name: str) -> str:
        """Get the current state of a Hyper-V VM."""
        cmd = f"(Get-VM -Name '{vm_name}').State"
        return self._run_powershell(cmd)

    def stop_vm(self, vm_name: str, force: bool = False) -> None:
        """Ensure the VM is powered down before mounting its disk."""
        state = self.get_vm_state(vm_name)
        if state == "Off":
            logger.info("VM %s is already off.", vm_name)
            return

        logger.info("Stopping VM %s...", vm_name)
        turn_off_arg = "-TurnOff" if force else ""
        self._run_powershell(f"Stop-VM -Name '{vm_name}' {turn_off_arg}")
        
        # Wait until confirmed off
        for _ in range(15):
            if self.get_vm_state(vm_name) == "Off":
                logger.info("VM %s successfully stopped.", vm_name)
                return
            time.sleep(2)
            
        raise VMManagerError(f"Timed out waiting for VM {vm_name} to stop.")

    def mount_vhdx(self) -> str:
        """Mount the VHDX and discover the Windows partition drive letter.
        
        Returns:
            The drive letter of the Windows partition (e.g., 'E:\\').
        """
        if self.mounted_drive:
            return self.mounted_drive
            
        logger.info("Mounting VHDX: %s", self.vhdx_path)
        
        # Mount the disk image and wait for volume initialization
        self._run_powershell(f"Mount-VHD -Path '{self.vhdx_path}'")
        time.sleep(3)  # Give Windows a moment to assign drive letters
        
        # Find the OS partition (usually the largest NTFS partition, typically C: inside the VM, 
        # but gets a new letter on the host. We look for the one with the 'Windows' directory.)
        script = f"""
        $disk = Get-VHD -Path '{self.vhdx_path}'
        $volumes = Get-Partition -DiskNumber $disk.DiskNumber | Get-Volume
        
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
        logger.info("Dismounting VHDX: %s", self.vhdx_path)
        try:
            self._run_powershell(f"Dismount-VHD -Path '{self.vhdx_path}'")
            self.mounted_drive = None
            logger.info("Successfully dismounted VHDX.")
        except VMManagerError as e:
            logger.warning("Error during dismount: %s", e)

    def start_vm(self, vm_name: str) -> None:
        """Boot the Hyper-V VM."""
        if self.mounted_drive:
            self.dismount_vhdx()
            
        logger.info("Starting VM %s...", vm_name)
        self._run_powershell(f"Start-VM -Name '{vm_name}'")
