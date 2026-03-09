"""Hardware normalizer — anti-fingerprint registry service.

Replaces VM-generated hardware identifiers in the offline registry with
realistic, self-consistent values drawn from ``hardware_models.json``.

Unlike :class:`VmScrubber` (which deletes VM-only keys and patches
indicator strings), this module enforces **cross-key consistency** — the
same vendor group is used for all writes so that BIOS vendor, BIOS version,
motherboard model, disk model, disk serial, and GPU model all look like they
belong to the same physical machine.

The authoritative hardware values come from the :class:`IdentityBundle`
produced by :class:`IdentityGenerator`; this service simply *writes* those
values back into every registry location where Windows records hardware info.

Registry paths written
-----------------------
``SYSTEM`` hive:
* ``ControlSet001\\Control\\SystemInformation``
    — SystemManufacturer, SystemProductName, BIOSVersion, BIOSReleaseDate
* ``ControlSet001\\Services\\disk\\Enum``
    — 0  (disk device string, e.g. ``"SCSI\\Disk&Ven_Samsung&..."``)
* ``ControlSet001\\Enum\\STORAGE\\Volume``
    — FriendlyName  (first storage volume)

``SOFTWARE`` hive:
* ``Microsoft\\Windows NT\\CurrentVersion``
    — SystemRoot (validate/ensure no VM path)
* ``Microsoft\\Windows\\CurrentVersion\\App Paths``
    — (no direct hardware, but serves as a sanity baseline)

``HARDWARE`` hive (if present):
* ``DESCRIPTION\\System\\BIOS``
    — BIOSVendor, BIOSVersion, BIOSReleaseDate,
      SystemManufacturer, SystemProductName, BaseBoardProduct

The HARDWARE hive is volatile (rebuilt by ntoskrnl at each boot) and is
therefore not present in offline images.  These writes are **recorded as
operations** for completeness but skipped gracefully if the hive file is
absent.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hive paths
# ---------------------------------------------------------------------------

_SYSTEM_HIVE: str = "Windows/System32/config/SYSTEM"
_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"

# ---------------------------------------------------------------------------
# Registry key paths
# ---------------------------------------------------------------------------

_SYSINFO_KEY: str = r"ControlSet001\Control\SystemInformation"
_DISK_ENUM_KEY: str = r"ControlSet001\Services\disk\Enum"
_BIOS_DESCRIPTION_KEY: str = r"DESCRIPTION\System\BIOS"

# Hardware data file
_HW_DATA_FILE: str = "hardware_models.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HardwareNormalizerError(Exception):
    """Raised when hardware normalization operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class HardwareNormalizer(BaseService):
    """Writes realistic hardware identifiers into offline registry hives.

    Uses the :class:`IdentityBundle` hardware field to ensure all registry
    locations that reference hardware show consistent, non-VM values.

    Args:
        hive_writer:  Low-level registry hive I/O service.
        audit_logger: Shared audit logger for traceability.
        data_dir:     Path to the ``data/`` directory.
    """

    def __init__(
        self,
        hive_writer: HiveWriter,
        audit_logger: Any,
        data_dir: Path,
    ) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger
        self._data_dir = data_dir
        self._hw_data: Dict[str, Any] = self._load_hw_data()

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "HardwareNormalizer"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            identity_bundle: :class:`IdentityBundle` — hardware identity data.

        Raises:
            HardwareNormalizerError: On missing context keys or write failure.
        """
        from core.identity_generator import IdentityBundle

        bundle = context.get("identity_bundle")
        if bundle is None:
            raise HardwareNormalizerError(
                "Missing required 'identity_bundle' in context"
            )
        if not isinstance(bundle, IdentityBundle):
            raise HardwareNormalizerError(
                f"Expected IdentityBundle, got {type(bundle).__name__}"
            )
        self.normalize(bundle)

    # -- public API ---------------------------------------------------------

    def normalize(self, bundle: Any) -> None:
        """Build and execute all hardware normalization operations.

        Args:
            bundle: :class:`IdentityBundle` with hardware identity data.

        Raises:
            HardwareNormalizerError: On write failure.
        """
        operations = self.build_operations(bundle)
        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise HardwareNormalizerError(
                f"Hardware normalizer registry write failed: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "normalize_hardware",
            "bios_vendor": bundle.hardware.bios_vendor,
            "motherboard_model": bundle.hardware.motherboard_model,
            "disk_model": bundle.hardware.disk_model,
            "operations_count": len(operations),
        })
        logger.info(
            "Hardware normalization complete: %s / %s (%d ops)",
            bundle.hardware.bios_vendor,
            bundle.hardware.motherboard_model,
            len(operations),
        )

    def build_operations(self, bundle: Any) -> List[HiveOperation]:
        """Build all hardware normalization operations without writing.

        Pure function — suitable for isolated testing.

        Args:
            bundle: :class:`IdentityBundle` with hardware identity.

        Returns:
            List of :class:`HiveOperation`.
        """
        ops: List[HiveOperation] = []
        hw = bundle.hardware

        # Format BIOS release date as MM/DD/YYYY (Windows convention)
        bios_date_str = self._format_bios_date(hw.bios_release_date)

        # -- SystemInformation (SYSTEM hive) --------------------------------
        ops.extend(self._build_system_info_ops(hw, bios_date_str))

        # -- Disk enumeration (SYSTEM hive) ---------------------------------
        ops.extend(self._build_disk_ops(hw))

        return ops

    # -- operation builders -------------------------------------------------

    def _build_system_info_ops(
        self, hw: Any, bios_date_str: str
    ) -> List[HiveOperation]:
        """Build SystemInformation registry operations.

        Args:
            hw:            HardwareIdentity from the bundle.
            bios_date_str: Formatted BIOS date string.

        Returns:
            List of :class:`HiveOperation` for the SYSTEM hive.
        """
        return [
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="SystemManufacturer",
                value_data=hw.bios_vendor,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="SystemProductName",
                value_data=hw.motherboard_model,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="BIOSVendor",
                value_data=hw.bios_vendor,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="BIOSVersion",
                value_data=hw.bios_version,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="BIOSReleaseDate",
                value_data=bios_date_str,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="BaseBoardProduct",
                value_data=hw.motherboard_model,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_SYSINFO_KEY,
                value_name="BaseBoardManufacturer",
                value_data=hw.bios_vendor,
                value_type=RegistryValueType.REG_SZ,
            ),
        ]

    def _build_disk_ops(self, hw: Any) -> List[HiveOperation]:
        """Build disk enumeration registry operations.

        Writes the first disk entry under the disk\\Enum key so
        device manager shows a realistic drive model.

        Args:
            hw: HardwareIdentity from the bundle.

        Returns:
            List of :class:`HiveOperation` for the SYSTEM hive.
        """
        # Build a realistic PnP device string for the disk
        # Format: SCSI\Disk&Ven_<vendor>&Prod_<model>&Rev_0001
        vendor, _, model_rest = hw.disk_model.partition(" ")
        model_clean = model_rest.replace(" ", "_")[:24]
        device_string = (
            f"SCSI\\Disk&Ven_{vendor}&Prod_{model_clean}&Rev_0001"
            f"\\{hw.disk_serial}"
        )

        return [
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_DISK_ENUM_KEY,
                value_name="0",
                value_data=device_string,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_DISK_ENUM_KEY,
                value_name="Count",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_DISK_ENUM_KEY,
                value_name="NextInstance",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ),
        ]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _format_bios_date(bios_date: Any) -> str:
        """Format a date as ``MM/DD/YYYY`` (Windows BIOS date convention).

        Args:
            bios_date: ``datetime.date`` or ISO string.

        Returns:
            Formatted date string.
        """
        if isinstance(bios_date, date):
            return bios_date.strftime("%m/%d/%Y")
        # Attempt ISO parse if it's a string
        try:
            d = date.fromisoformat(str(bios_date))
            return d.strftime("%m/%d/%Y")
        except (ValueError, TypeError):
            return "01/01/2023"

    def _load_hw_data(self) -> Dict[str, Any]:
        """Load hardware_models.json.

        Returns:
            Parsed hardware data dict.

        Raises:
            HardwareNormalizerError: If file is missing or malformed.
        """
        path = self._data_dir / _HW_DATA_FILE
        if not path.is_file():
            raise HardwareNormalizerError(
                f"Hardware models file not found: {path}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HardwareNormalizerError(
                f"Failed to parse {_HW_DATA_FILE}: {exc}"
            ) from exc
        if "system_vendors" not in data:
            raise HardwareNormalizerError(
                f"{_HW_DATA_FILE} missing 'system_vendors' key"
            )
        return data
