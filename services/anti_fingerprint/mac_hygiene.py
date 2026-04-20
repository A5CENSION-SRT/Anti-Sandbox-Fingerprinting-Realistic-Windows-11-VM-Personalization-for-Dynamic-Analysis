"""MAC address / NIC registry hygiene (Phase 7, ADR-011).

Sets a plausible Intel or Realtek OUI in every NIC NetworkAddress registry
value so that network-fingerprinting tools don't see a QEMU (52:54:00),
VirtualBox (08:00:27), or VMware (00:0c:29) prefix.

NIC class registry key:
    HKLM\\SYSTEM\\ControlSet001\\Control\\Class\\{4d36e972-e325-11ce-bfc1-08002be10318}\\000X

Values written per adapter:
  * ``NetworkAddress``     REG_SZ  — 12-char hex, no separators ("001B21AABBCC")
  * ``*PhysicalMediaType`` REG_DWORD — 14 (NdisMedium802_3)

MAC is derived deterministically from the disk serial (ADR-012 stability).
Adapters that already carry a non-VM MAC are left unchanged (idempotent).
"""

from __future__ import annotations

import logging
from random import Random
from typing import Any, List, Optional

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

_SYSTEM_HIVE = "Windows/System32/config/SYSTEM"
_NIC_CLASS = r"ControlSet001\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"

# Scan up to this many instance indices (0000…0015)
_MAX_ADAPTERS = 16

# Known VM OUI prefixes to replace (lowercase, no separators)
_VM_OUIS: frozenset = frozenset({
    "525400",  # QEMU / KVM (default)
    "080027",  # VirtualBox default
    "000c29",  # VMware Workstation
    "005056",  # VMware ESX
    "001c14",  # VMware (alternate)
    "000569",  # VMware (alternate)
})

# Non-VM OUI pools — pick randomly for each adapter
_INTEL_OUIS = ["001B21", "00238B", "002264", "001560", "001517", "A4C3F0", "8C8D28"]
_REALTEK_OUIS = ["00E04C", "001FC6", "D43D7E", "FC3497"]


class MacHygieneError(Exception):
    """Raised when MAC hygiene operations fail."""


class MacHygiene(BaseService):
    """Replaces VM-vendor NIC MAC OUIs with Intel/Realtek OUIs in SYSTEM hive.

    Scans all adapter subkeys under the NIC class GUID, replacing the
    ``NetworkAddress`` value whenever the current MAC carries a known VM OUI.
    Adapters with a non-VM MAC are left unchanged.

    Args:
        hive_writer:  Low-level hive I/O service.
        audit_logger: Shared audit logger.
    """

    def __init__(self, hive_writer: HiveWriter, audit_logger: Any) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "MacHygiene"

    def apply(self, ctx: "ServiceContext") -> None:
        """Write NetworkAddress overrides for all detected NIC adapters.

        Raises:
            MacHygieneError: On fatal hive write error.
        """
        hw = ctx.identity_bundle.hardware
        disk_serial: str = getattr(hw, "disk_serial", "DEFAULT-SERIAL")

        rng = (
            ctx.scheduler.child_rng("MacHygiene")
            if ctx.scheduler
            else Random(hash(disk_serial + "mac_hygiene"))
        )

        ops = self._build_ops(disk_serial, rng)
        if not ops:
            logger.info("MacHygiene: no adapter keys found or all MACs already clean")
            return

        try:
            self._hive_writer.execute_operations(ops)
        except HiveWriterError as exc:
            raise MacHygieneError(f"MAC hygiene write failed: {exc}") from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_mac_overrides",
            "disk_serial": disk_serial,
            "operations_count": len(ops),
        })
        logger.info("MacHygiene: wrote %d NIC override operations", len(ops))

    # -- operation builder --------------------------------------------------

    def _build_ops(self, disk_serial: str, rng: Random) -> List[HiveOperation]:
        ops: List[HiveOperation] = []

        for idx in range(_MAX_ADAPTERS):
            key_path = f"{_NIC_CLASS}\\{idx:04d}"

            if not self._hive_writer.key_exists(_SYSTEM_HIVE, key_path):
                continue  # adapter does not exist at this index

            # Check whether the existing NetworkAddress is already non-VM
            current_mac: Optional[str] = None
            try:
                current_mac = str(self._hive_writer.read_value(
                    _SYSTEM_HIVE, key_path, "NetworkAddress"
                ))
                if current_mac and not _is_vm_mac(current_mac):
                    logger.debug("Adapter %04d already has non-VM MAC; skip", idx)
                    continue
            except HiveWriterError:
                pass  # value absent — write fresh

            mac = _derive_mac(disk_serial, idx, rng)

            ops.append(HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="NetworkAddress",
                value_data=mac,
                value_type=RegistryValueType.REG_SZ,
                operation="set",
            ))
            ops.append(HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="*PhysicalMediaType",
                value_data=14,
                value_type=RegistryValueType.REG_DWORD,
                operation="set",
            ))
            old = current_mac or "(absent)"
            logger.info("Adapter %04d: %s → %s", idx, old, mac)

        return ops


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_vm_mac(mac: str) -> bool:
    normalized = mac.replace(":", "").replace("-", "").upper()
    return len(normalized) >= 6 and normalized[:6].lower() in _VM_OUIS


def _derive_mac(disk_serial: str, adapter_idx: int, rng: Random) -> str:
    """Derive a deterministic, realistic MAC for the given adapter index.

    Even-indexed adapters get an Intel OUI; odd-indexed get Realtek.
    The low 3 octets come from a hash of (disk_serial + adapter_idx) so
    the same persona always yields the same MAC (ADR-012).
    """
    if adapter_idx % 2 == 0:
        oui = rng.choice(_INTEL_OUIS)
    else:
        oui = rng.choice(_REALTEK_OUIS)

    h = 0
    seed_str = f"{disk_serial}-{adapter_idx}"
    for ch in seed_str:
        h = (h * 37 + ord(ch)) & 0xFFFFFF

    return f"{oui}{h:06X}"
