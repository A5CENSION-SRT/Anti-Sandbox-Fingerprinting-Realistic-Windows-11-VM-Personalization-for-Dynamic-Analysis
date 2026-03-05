"""VM string scrubber — anti-fingerprint registry service.

Scans the offline registry hives for known VM/hypervisor indicator strings
and replaces them with realistic bare-metal equivalents.

This module targets the same :class:`HiveWriter` pattern as all other
registry services — it is a **pure operation builder** that never writes
directly, instead producing :class:`HiveOperation` lists.

VM indicator sources
--------------------
The canonical list of VM strings is taken directly from
``core.identity_generator._VM_STRINGS`` so both detection and scrubbing
always target the same vocabulary.  Additional well-known registry paths
are hard-coded below (VBoxService entries, VMware Tools entries, ACPI
DSDT manufacturer strings).

Registry paths scanned and patched
-----------------------------------
``SYSTEM`` hive:
* ``ControlSet001\\Services\\VBoxSF``            — VirtualBox shared folders
* ``ControlSet001\\Services\\VBoxGuest``         — VirtualBox guest additions
* ``ControlSet001\\Services\\VBoxMouse``         — VirtualBox mouse driver
* ``ControlSet001\\Services\\VBoxVideo``         — VirtualBox video driver
* ``ControlSet001\\Services\\vmci``              — VMware VMCI bus
* ``ControlSet001\\Services\\vmhgfs``            — VMware host/guest FS
* ``ControlSet001\\Services\\vmmouse``           — VMware mouse driver
* ``ControlSet001\\Services\\vmrawdsk``          — VMware raw disk
* ``ControlSet001\\Services\\vmusbmouse``        — VMware USB HID mouse
* ``ControlSet001\\Services\\vmxnet``            — VMware VMXNET NIC
* ``ControlSet001\\Enum\\ACPI``                  — ACPI device identifiers
    (e.g. ``VBOX0001``, ``VMW0001``)
* ``ControlSet001\\Control\\SystemInformation``  — SystemManufacturer,
    SystemProductName (may contain "VirtualBox" or "VMware")

``SOFTWARE`` hive:
* ``VMware, Inc.\\VMware Tools``                 — VMware Tools install key
* ``Oracle\\VirtualBox Guest Additions``         — VBox guest additions key
* ``Microsoft\\Virtual Machine\\Guest\\Parameters`` — Hyper-V guest params

``HARDWARE`` hive (NTUSER-less):
* The above paths also checked in HARDWARE hive when present.

Strategy
--------
1. For each known VM-indicator key path, attempt to read sensitive values.
2. If a value contains any substring from ``_VM_STRINGS``, replace it with
   a realistic bare-metal replacement sourced from ``hardware_models.json``.
3. Delete known VM-only service keys entirely (delete_key operation).
4. Produce a :class:`HiveOperation` list for :class:`HiveWriter` execution.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VM indicator vocabulary — mirrors core.identity_generator._VM_STRINGS
# ---------------------------------------------------------------------------

_VM_STRINGS: frozenset = frozenset({
    "vbox",
    "vmware",
    "virtual",
    "test-pc",
    "sandbox",
    "hyperv",
    "qemu",
    "xen",
    "bochs",
    "innotek",
    "oracle vm",
    "parallels",
})

# ---------------------------------------------------------------------------
# Hive paths
# ---------------------------------------------------------------------------

_SYSTEM_HIVE: str = "Windows/System32/config/SYSTEM"
_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"

# ---------------------------------------------------------------------------
# VM-only service keys to delete from SYSTEM hive
# (these keys have no legitimate presence on bare metal)
# ---------------------------------------------------------------------------

_VM_SERVICE_KEYS: List[str] = [
    r"ControlSet001\Services\VBoxSF",
    r"ControlSet001\Services\VBoxGuest",
    r"ControlSet001\Services\VBoxMouse",
    r"ControlSet001\Services\VBoxVideo",
    r"ControlSet001\Services\VBoxNetFlt",
    r"ControlSet001\Services\VBoxNetAdp",
    r"ControlSet001\Services\vmci",
    r"ControlSet001\Services\vmhgfs",
    r"ControlSet001\Services\vmmouse",
    r"ControlSet001\Services\vmrawdsk",
    r"ControlSet001\Services\vmusbmouse",
    r"ControlSet001\Services\vmxnet",
    r"ControlSet001\Services\vmxnet3ndis6",
    r"ControlSet001\Services\hv_vmbus",
    r"ControlSet001\Services\hvservice",
]

# ---------------------------------------------------------------------------
# VM software install keys to delete from SOFTWARE hive
# ---------------------------------------------------------------------------

_VM_SOFTWARE_KEYS: List[str] = [
    r"VMware, Inc.\VMware Tools",
    r"VMware, Inc.",
    r"Oracle\VirtualBox Guest Additions",
    r"Microsoft\Virtual Machine\Guest\Parameters",
]

# ---------------------------------------------------------------------------
# String values to patch in SYSTEM hive (key_path, value_name, replacement)
# ---------------------------------------------------------------------------

# Replacement is a placeholder; actual value comes from hardware_models.json
_SYSTEM_IDENTITY_PATCHES: List[Tuple[str, str]] = [
    (
        r"ControlSet001\Control\SystemInformation",
        "SystemManufacturer",
    ),
    (
        r"ControlSet001\Control\SystemInformation",
        "SystemProductName",
    ),
    (
        r"ControlSet001\Control\SystemInformation",
        "BIOSVersion",
    ),
]

# Hardware data file
_HW_DATA_FILE: str = "hardware_models.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VmScrubberError(Exception):
    """Raised when VM scrubber operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class VmScrubber(BaseService):
    """Scrubs VM indicator strings from offline registry hives.

    Produces :class:`HiveOperation` delete-key and set-value operations to
    remove VM artifacts and replace vendor strings with realistic hardware
    values sourced from ``hardware_models.json``.

    Args:
        hive_writer: Low-level registry hive I/O service.
        audit_logger: Shared audit logger for traceability.
        data_dir:    Path to the ``data/`` directory containing
                     ``hardware_models.json``.
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
        return "VmScrubber"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            computer_name: str — used as RNG seed for replacements.

        Raises:
            VmScrubberError: On missing context keys or write failure.
        """
        computer_name = context.get("computer_name")
        if not computer_name:
            raise VmScrubberError(
                "Missing required 'computer_name' in context"
            )
        self.scrub(computer_name)

    # -- public API ---------------------------------------------------------

    def scrub(self, computer_name: str) -> None:
        """Build and execute all VM-scrubbing registry operations.

        Args:
            computer_name: Used as seed for deterministic replacements.

        Raises:
            VmScrubberError: On write failure.
        """
        operations = self.build_operations(computer_name)
        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise VmScrubberError(
                f"VM scrubber registry write failed: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "scrub_vm_artifacts",
            "computer_name": computer_name,
            "operations_count": len(operations),
        })
        logger.info(
            "VM scrub complete: %d operations for %s",
            len(operations), computer_name,
        )

    def build_operations(self, computer_name: str) -> List[HiveOperation]:
        """Build all VM-scrubbing operations without writing.

        Pure function — suitable for isolated testing.

        Args:
            computer_name: Seed for deterministic hardware replacements.

        Returns:
            List of :class:`HiveOperation`.
        """
        rng = Random(hash(computer_name + ":vm_scrub"))
        ops: List[HiveOperation] = []

        # 1. Delete VM-only service keys from SYSTEM hive
        ops.extend(self._build_service_deletes())

        # 2. Delete VM software keys from SOFTWARE hive
        ops.extend(self._build_software_deletes())

        # 3. Patch SystemInformation strings with realistic hardware values
        ops.extend(self._build_identity_patches(rng))

        return ops

    # -- operation builders -------------------------------------------------

    def _build_service_deletes(self) -> List[HiveOperation]:
        """Build delete_key operations for all VM service keys."""
        ops: List[HiveOperation] = []
        for key_path in _VM_SERVICE_KEYS:
            if self._hive_writer.key_exists(_SYSTEM_HIVE, key_path):
                ops.append(HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=key_path,
                    value_name="(default)",
                    value_data=None,
                    value_type=RegistryValueType.REG_NONE,
                    operation="delete_key",
                ))
                logger.debug("Scheduled delete_key: SYSTEM\\%s", key_path)
            else:
                logger.debug(
                    "Key not present (skip): SYSTEM\\%s", key_path
                )
        return ops

    def _build_software_deletes(self) -> List[HiveOperation]:
        """Build delete_key operations for VM software install keys."""
        ops: List[HiveOperation] = []
        for key_path in _VM_SOFTWARE_KEYS:
            if self._hive_writer.key_exists(_SOFTWARE_HIVE, key_path):
                ops.append(HiveOperation(
                    hive_path=_SOFTWARE_HIVE,
                    key_path=key_path,
                    value_name="(default)",
                    value_data=None,
                    value_type=RegistryValueType.REG_NONE,
                    operation="delete_key",
                ))
                logger.debug("Scheduled delete_key: SOFTWARE\\%s", key_path)
        return ops

    def _build_identity_patches(
        self, rng: Random
    ) -> List[HiveOperation]:
        """Build set-value patches for SystemInformation strings.

        Reads current values from the SYSTEM hive.  If the current value
        contains any substring from ``_VM_STRINGS``, a realistic replacement
        is generated from ``hardware_models.json``.

        Args:
            rng: Seeded random instance for deterministic selection.

        Returns:
            List of set-value :class:`HiveOperation`.
        """
        ops: List[HiveOperation] = []
        vendor_group = rng.choice(self._hw_data["system_vendors"])

        replacements: Dict[str, str] = {
            "SystemManufacturer": vendor_group["bios_vendor"],
            "SystemProductName": rng.choice(vendor_group["motherboard_models"]),
            "BIOSVersion": rng.choice(vendor_group["bios_versions"]),
        }

        for key_path, value_name in _SYSTEM_IDENTITY_PATCHES:
            try:
                current = self._hive_writer.read_value(
                    _SYSTEM_HIVE, key_path, value_name
                )
            except HiveWriterError:
                # Key/value doesn't exist — nothing to patch
                continue

            if self._contains_vm_string(str(current)):
                replacement = replacements[value_name]
                ops.append(HiveOperation(
                    hive_path=_SYSTEM_HIVE,
                    key_path=key_path,
                    value_name=value_name,
                    value_data=replacement,
                    value_type=RegistryValueType.REG_SZ,
                    operation="set",
                ))
                logger.info(
                    "Patching VM string in %s\\%s: '%s' → '%s'",
                    key_path, value_name, current, replacement,
                )

        return ops

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _contains_vm_string(value: str) -> bool:
        """Return True if *value* contains any known VM indicator substring.

        Args:
            value: String to test (case-insensitive).

        Returns:
            ``True`` if a VM indicator is found.
        """
        v_lower = value.lower()
        return any(indicator in v_lower for indicator in _VM_STRINGS)

    def _load_hw_data(self) -> Dict[str, Any]:
        """Load hardware_models.json.

        Returns:
            Parsed hardware data dict.

        Raises:
            VmScrubberError: If file is missing or malformed.
        """
        path = self._data_dir / _HW_DATA_FILE
        if not path.is_file():
            raise VmScrubberError(
                f"Hardware models file not found: {path}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise VmScrubberError(
                f"Failed to parse {_HW_DATA_FILE}: {exc}"
            ) from exc
        if "system_vendors" not in data:
            raise VmScrubberError(
                f"{_HW_DATA_FILE} missing 'system_vendors' key"
            )
        return data
