"""Windows NTFS partition auto-discovery via lsblk.

Used when config.yaml::windows_partition is not set.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class PartitionDiscoveryError(Exception):
    """Raised when no suitable Windows partition is found."""


def find_windows_partition() -> str:
    """Return the device path of an unmounted NTFS partition.

    Walks lsblk JSON output and returns the first NTFS partition that is
    not currently mounted (to avoid mounting an active Windows session).

    Returns:
        Device path string, e.g. ``"/dev/nvme0n1p3"``.

    Raises:
        PartitionDiscoveryError: If lsblk fails or no candidate is found.
    """
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--output", "NAME,FSTYPE,MOUNTPOINT,SIZE,TYPE"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise PartitionDiscoveryError(f"lsblk failed: {exc}") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PartitionDiscoveryError(f"Could not parse lsblk output: {exc}") from exc

    candidates = []
    _walk(data.get("blockdevices", []), candidates)

    if not candidates:
        raise PartitionDiscoveryError(
            "No unmounted NTFS partition found. "
            "Ensure Windows is shut down (not hibernated) and the partition is visible to lsblk."
        )

    if len(candidates) > 1:
        logger.warning(
            "Multiple unmounted NTFS partitions found: %s — using the first. "
            "Set windows_partition in config.yaml to choose explicitly.",
            candidates,
        )

    device = "/dev/" + candidates[0]
    logger.info("Auto-discovered Windows partition: %s", device)
    return device


def _walk(devices: list, candidates: list) -> None:
    for dev in devices:
        if dev.get("type") == "part" and dev.get("fstype") == "ntfs":
            if not dev.get("mountpoint"):
                candidates.append(dev["name"])
        for child in dev.get("children", []):
            _walk([child], candidates)
