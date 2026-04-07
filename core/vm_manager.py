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

    @property
    def _ps_image_path(self) -> str:
        """Return PowerShell-safe image path wrapped for single quotes."""
        return str(self.vhdx_path).replace("'", "''")

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

    def _is_image_attached(self) -> bool:
        """Return True when the VHD/VHDX is currently attached."""
        script = f"""
        $image = Get-DiskImage -ImagePath '{self._ps_image_path}' -ErrorAction SilentlyContinue
        if (-not $image) {{
            Write-Output 'false'
            return
        }}
        Write-Output ([string]$image.Attached).ToLowerInvariant()
        """
        return self._run_powershell(script).strip() == "true"

    def _list_image_drive_candidates(self) -> list[str]:
        """Return drive roots currently mapped to the attached image."""
        script = f"""
        $image = Get-DiskImage -ImagePath '{self._ps_image_path}' -ErrorAction SilentlyContinue
        if (-not $image -or -not $image.Attached) {{
            return
        }}

        $disk = $null
        if ($image.DevicePath) {{
            $disk = Get-Disk -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $image.DevicePath }}
        }}
        if (-not $disk -and $null -ne $image.Number) {{
            $disk = Get-Disk -Number $image.Number -ErrorAction SilentlyContinue
        }}
        if (-not $disk) {{
            return
        }}

        $partitions = $disk | Get-Partition -ErrorAction SilentlyContinue
        foreach ($part in $partitions) {{
            if ($part.DriveLetter) {{
                Write-Output ($part.DriveLetter + ':\\')
            }}
        }}
        """
        output = self._run_powershell(script)
        candidates = [line.strip() for line in output.splitlines() if line.strip()]
        return list(dict.fromkeys(candidates))

    def _try_assign_drive_letter(self) -> bool:
        """Try assigning a drive letter to the largest partition without one."""
        script = f"""
        $image = Get-DiskImage -ImagePath '{self._ps_image_path}' -ErrorAction SilentlyContinue
        if (-not $image -or -not $image.Attached) {{
            Write-Output 'false'
            return
        }}

        $disk = $null
        if ($image.DevicePath) {{
            $disk = Get-Disk -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $image.DevicePath }}
        }}
        if (-not $disk -and $null -ne $image.Number) {{
            $disk = Get-Disk -Number $image.Number -ErrorAction SilentlyContinue
        }}
        if (-not $disk) {{
            Write-Output 'false'
            return
        }}

        $partition = $disk |
            Get-Partition -ErrorAction SilentlyContinue |
            Where-Object {{ -not $_.DriveLetter -and $_.Size -gt 0 }} |
            Sort-Object Size -Descending |
            Select-Object -First 1

        if (-not $partition) {{
            Write-Output 'false'
            return
        }}

        try {{
            $partition | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction Stop | Out-Null
            Write-Output 'true'
        }} catch {{
            Write-Output 'false'
        }}
        """
        return self._run_powershell(script).strip() == "true"

    def _get_image_partition_style(self) -> Optional[str]:
        """Return partition style for the attached image disk (RAW, MBR, GPT)."""
        script = f"""
        $image = Get-DiskImage -ImagePath '{self._ps_image_path}' -ErrorAction SilentlyContinue
        if (-not $image -or -not $image.Attached) {{
            return
        }}

        $disk = $null
        if ($image.DevicePath) {{
            $disk = Get-Disk -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $image.DevicePath }} | Select-Object -First 1
        }}
        if (-not $disk -and $null -ne $image.Number) {{
            $disk = Get-Disk -Number $image.Number -ErrorAction SilentlyContinue
        }}
        if (-not $disk) {{
            return
        }}

        Write-Output ([string]$disk.PartitionStyle)
        """
        style = self._run_powershell(script).strip()
        return style if style else None

    def _discover_windows_drive(self) -> Optional[str]:
        """Find the mounted drive letter containing the Windows folder."""
        candidates = self._list_image_drive_candidates()

        for candidate in candidates:
            if Path(candidate, "Windows").exists():
                return candidate

        if candidates:
            logger.info(
                "No Windows folder found on mounted image; using first available partition: %s",
                candidates[0],
            )
            return candidates[0]

        if self._try_assign_drive_letter():
            candidates = self._list_image_drive_candidates()
            for candidate in candidates:
                if Path(candidate, "Windows").exists():
                    return candidate
            if candidates:
                logger.info(
                    "Assigned drive letter to mounted image; using partition: %s",
                    candidates[0],
                )
                return candidates[0]

        return None

    def stop_vm(self, vm_name: str, force: bool = False) -> None:
        """Not supported on Windows Home (Hyper-V API required)."""
        logger.warning("VM Start/Stop automation requires Hyper-V. Ensure your VM is powered off manually.")

    def mount_vhdx(self) -> str:
        """Mount the VHDX and discover the Windows partition drive letter.
        
        Returns:
            The drive letter of the Windows partition (e.g., 'E:\\').
        """
        if self.mounted_drive and Path(self.mounted_drive).exists():
            return self.mounted_drive

        already_attached = self._is_image_attached()
        mounted_by_this_call = False

        if already_attached:
            logger.info("Disk image already attached: %s", self.vhdx_path)
        else:
            logger.info("Mounting disk image: %s", self.vhdx_path)
            try:
                self._run_powershell(
                    f"Mount-DiskImage -ImagePath '{self._ps_image_path}' -NoDriveLetter:$false"
                )
                mounted_by_this_call = True
            except VMManagerError:
                # Handle race: if another process attached after our pre-check,
                # continue to drive discovery instead of failing hard.
                if self._is_image_attached():
                    logger.warning(
                        "Mount command failed, but image is attached; proceeding to drive discovery."
                    )
                else:
                    raise

        time.sleep(2)
        drive = self._discover_windows_drive()

        if not drive and already_attached and not mounted_by_this_call:
            logger.warning(
                "Image is attached but no drive was discovered; attempting remount to clear stale state."
            )
            try:
                self._run_powershell(
                    f"Dismount-DiskImage -ImagePath '{self._ps_image_path}' -ErrorAction SilentlyContinue"
                )
                self._run_powershell(
                    f"Mount-DiskImage -ImagePath '{self._ps_image_path}' -NoDriveLetter:$false"
                )
                time.sleep(2)
                drive = self._discover_windows_drive()
            except VMManagerError:
                logger.warning("Remount attempt failed while recovering stale attachment state.")
        
        if not drive:
            if mounted_by_this_call:
                self.dismount_vhdx()

            partition_style = self._get_image_partition_style()
            if partition_style and partition_style.upper() == "RAW":
                raise VMManagerError(
                    "Disk image is attached but uses RAW partition style (no partition/drive letter). "
                    "Initialize and format the VHD/VHDX once, then retry."
                )

            raise VMManagerError("Failed to locate Windows partition on mounted VHDX.")
            
        logger.info("Discovered Windows partition at %s", drive)
        self.mounted_drive = drive
        return drive

    def dismount_vhdx(self) -> None:
        """Dismount the VHDX file safely."""
        logger.info("Dismounting disk image: %s", self.vhdx_path)
        try:
            if not self._is_image_attached():
                self.mounted_drive = None
                logger.info("Disk image already dismounted.")
                return

            self._run_powershell(f"Dismount-DiskImage -ImagePath '{self._ps_image_path}'")
            self.mounted_drive = None
            logger.info("Successfully dismounted image.")
        except VMManagerError as e:
            logger.warning("Error during dismount: %s", e)

    def start_vm(self, vm_name: str) -> None:
        """Not supported on Windows Home (Hyper-V API required)."""
        if self.mounted_drive:
            self.dismount_vhdx()
        logger.warning("VM Start automation requires Hyper-V. You can boot your VM manually now.")
