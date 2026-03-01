"""Registry-based system identity service.

Populates the Windows registry keys that define the machine's identity —
computer name, registered owner, product ID, hardware GUIDs, and related
values.  All data is sourced from the :class:`IdentityBundle` produced by
:class:`IdentityGenerator`; no values are invented here.

Every write is delegated to :class:`HiveWriter`, so this module never
touches hive bytes directly.  It is a pure **operation builder**.

Target hive paths and key locations
------------------------------------
* ``SOFTWARE``
    * ``Microsoft\\Windows NT\\CurrentVersion``
        — RegisteredOwner, RegisteredOrganization, ProductId, InstallDate,
          BuildLab, BuildLabEx, CurrentBuild, CurrentVersion
    * ``Microsoft\\Cryptography``
        — MachineGuid
* ``SYSTEM``
    * ``ControlSet001\\Control\\ComputerName\\ComputerName``
        — ComputerName
    * ``ControlSet001\\Control\\ComputerName\\ActiveComputerName``
        — ComputerName
    * ``ControlSet001\\Services\\Tcpip\\Parameters``
        — Hostname, Domain, NV Hostname
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Sequence

from core.identity_generator import IdentityBundle
from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — hive relative paths and registry key paths
# ---------------------------------------------------------------------------

_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"
_SYSTEM_HIVE: str = "Windows/System32/config/SYSTEM"

# SOFTWARE hive keys
_NT_CURRENT_VERSION: str = r"Microsoft\Windows NT\CurrentVersion"
_CRYPTOGRAPHY: str = r"Microsoft\Cryptography"

# SYSTEM hive keys
_COMPUTERNAME_KEY: str = (
    r"ControlSet001\Control\ComputerName\ComputerName"
)
_ACTIVE_COMPUTERNAME_KEY: str = (
    r"ControlSet001\Control\ComputerName\ActiveComputerName"
)
_TCPIP_PARAMS_KEY: str = r"ControlSet001\Services\Tcpip\Parameters"

# Windows build metadata (realistic but static — not from profile)
_DEFAULT_CURRENT_BUILD: str = "19045"
_DEFAULT_CURRENT_VERSION: str = "6.3"
_DEFAULT_BUILD_LAB: str = "19041.vb_release.191206-1406"
_DEFAULT_BUILD_LAB_EX: str = (
    "19041.1.amd64fre.vb_release.191206-1406"
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SystemIdentityError(Exception):
    """Raised when system identity operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SystemIdentity(BaseService):
    """Writes system-level identity values into offline registry hives.

    This service is a **pure operation builder** — it constructs a list of
    :class:`HiveOperation` objects and delegates execution to the injected
    :class:`HiveWriter`.

    Dependencies (injected):
        hive_writer: The low-level hive read/write service.
        audit_logger: Shared audit logger for traceability.

    Args:
        hive_writer: ``HiveWriter`` instance for offline hive I/O.
        audit_logger: ``AuditLogger`` instance for recording operations.
    """

    def __init__(self, hive_writer: HiveWriter, audit_logger: Any) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "SystemIdentity"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            identity_bundle: :class:`IdentityBundle` — the identity data.

        Raises:
            SystemIdentityError: If the bundle is missing or writes fail.
        """
        bundle = context.get("identity_bundle")
        if bundle is None:
            raise SystemIdentityError(
                "Missing required 'identity_bundle' in context"
            )
        if not isinstance(bundle, IdentityBundle):
            raise SystemIdentityError(
                f"Expected IdentityBundle, got {type(bundle).__name__}"
            )
        self.write_identity(bundle)

    # -- public API ---------------------------------------------------------

    def write_identity(self, bundle: IdentityBundle) -> None:
        """Build and execute all system identity registry operations.

        Constructs the full list of :class:`HiveOperation` objects from the
        identity bundle, then delegates to ``HiveWriter.execute_operations``.

        Args:
            bundle: The identity data to write.

        Raises:
            SystemIdentityError: On any write failure.
        """
        operations = self.build_operations(bundle)

        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise SystemIdentityError(
                f"Failed to write system identity: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_identity_complete",
            "computer_name": bundle.user.computer_name,
            "registered_owner": bundle.user.full_name,
            "operations_count": len(operations),
        })
        logger.info(
            "System identity written: computer=%s, owner=%s (%d operations)",
            bundle.user.computer_name,
            bundle.user.full_name,
            len(operations),
        )

    def build_operations(
        self, bundle: IdentityBundle
    ) -> List[HiveOperation]:
        """Build the full list of registry operations from an identity bundle.

        This is a pure function — it returns the list without side effects,
        making it easy to test in isolation.

        Args:
            bundle: The identity data to derive operations from.

        Returns:
            List of :class:`HiveOperation` ready for execution.
        """
        ops: List[HiveOperation] = []
        ops.extend(self._build_software_ops(bundle))
        ops.extend(self._build_system_ops(bundle))
        return ops

    # -- operation builders -------------------------------------------------

    def _build_software_ops(
        self, bundle: IdentityBundle
    ) -> List[HiveOperation]:
        """Build SOFTWARE hive operations.

        Populates:
            * NT\\CurrentVersion — owner, org, product ID, build strings
            * Cryptography — MachineGuid
        """
        user = bundle.user
        machine_guid = self._derive_machine_guid(user.computer_name)
        install_date = self._derive_install_date(user.computer_name)

        return [
            # -- NT CurrentVersion --
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="RegisteredOwner",
                value_data=user.full_name,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="RegisteredOrganization",
                value_data=user.organization,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="ProductId",
                value_data=self._derive_product_id(user.computer_name),
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="InstallDate",
                value_data=install_date,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="CurrentBuild",
                value_data=_DEFAULT_CURRENT_BUILD,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="CurrentVersion",
                value_data=_DEFAULT_CURRENT_VERSION,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="BuildLab",
                value_data=_DEFAULT_BUILD_LAB,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_NT_CURRENT_VERSION,
                value_name="BuildLabEx",
                value_data=_DEFAULT_BUILD_LAB_EX,
                value_type=RegistryValueType.REG_SZ,
            ),
            # -- Cryptography --
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=_CRYPTOGRAPHY,
                value_name="MachineGuid",
                value_data=machine_guid,
                value_type=RegistryValueType.REG_SZ,
            ),
        ]

    def _build_system_ops(
        self, bundle: IdentityBundle
    ) -> List[HiveOperation]:
        """Build SYSTEM hive operations.

        Populates:
            * ComputerName — active and stored
            * TCP/IP parameters — hostname and domain
        """
        computer = bundle.user.computer_name

        return [
            # -- ComputerName --
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_COMPUTERNAME_KEY,
                value_name="ComputerName",
                value_data=computer,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_ACTIVE_COMPUTERNAME_KEY,
                value_name="ComputerName",
                value_data=computer,
                value_type=RegistryValueType.REG_SZ,
            ),
            # -- TCP/IP parameters --
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_TCPIP_PARAMS_KEY,
                value_name="Hostname",
                value_data=computer,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_TCPIP_PARAMS_KEY,
                value_name="NV Hostname",
                value_data=computer,
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=_TCPIP_PARAMS_KEY,
                value_name="Domain",
                value_data="",
                value_type=RegistryValueType.REG_SZ,
            ),
        ]

    # -- deterministic derivation helpers -----------------------------------

    @staticmethod
    def _derive_machine_guid(computer_name: str) -> str:
        """Derive a deterministic MachineGuid from the computer name.

        Produces a standard UUID-format string (8-4-4-4-12) from a SHA-256
        hash so the same computer name always yields the same GUID.

        Args:
            computer_name: The VM's computer name.

        Returns:
            GUID string, e.g. ``"a1b2c3d4-e5f6-7890-abcd-ef1234567890"``.
        """
        digest = hashlib.sha256(
            computer_name.encode("utf-8")
        ).hexdigest()
        return (
            f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}"
            f"-{digest[16:20]}-{digest[20:32]}"
        )

    @staticmethod
    def _derive_product_id(computer_name: str) -> str:
        """Derive a deterministic Windows Product ID.

        Produces a 5-group format ``XXXXX-XXX-XXXXXXX-XXXXX`` from a hash
        so the same identity always generates the same product ID.

        Args:
            computer_name: The VM's computer name.

        Returns:
            Product ID string.
        """
        digest = hashlib.sha256(
            (computer_name + ":product_id").encode("utf-8")
        ).hexdigest()
        # Convert hex chars to decimal digit strings
        digits = "".join(str(int(c, 16) % 10) for c in digest[:23])
        return f"{digits[:5]}-{digits[5:8]}-{digits[8:15]}-{digits[15:20]}"

    @staticmethod
    def _derive_install_date(computer_name: str) -> int:
        """Derive a deterministic Windows install date as Unix epoch.

        Returns a timestamp in the range [2020-01-01, 2024-12-31], derived
        from a hash of the computer name.

        Args:
            computer_name: The VM's computer name.

        Returns:
            Unix epoch integer.
        """
        digest = hashlib.sha256(
            (computer_name + ":install_date").encode("utf-8")
        ).hexdigest()
        # Range: 2020-01-01 to ~2024-12-31
        epoch_start = 1577836800   # 2020-01-01 UTC
        epoch_end = 1735689600     # 2025-01-01 UTC
        span = epoch_end - epoch_start
        offset = int(digest[:8], 16) % span
        return epoch_start + offset
