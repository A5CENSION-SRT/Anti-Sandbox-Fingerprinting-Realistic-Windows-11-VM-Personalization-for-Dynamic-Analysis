"""Registry service for network profile / interface entries.

Creates realistic network adapter and profile registry entries that a
Windows installation would contain — Wi-Fi SSID history, adapter
descriptions, DHCP settings, and TCP/IP interface parameters.

This module is a **pure operation builder** — it constructs
:class:`HiveOperation` lists and delegates execution to :class:`HiveWriter`.

Target hive paths
-----------------
* ``SOFTWARE``
    * ``Microsoft\\Windows NT\\CurrentVersion\\NetworkList\\Profiles\\{guid}``
        — ProfileName, Description, Managed, Category, DateCreated,
          DateLastConnected, NameType
    * ``Microsoft\\Windows NT\\CurrentVersion\\NetworkList\\Signatures\\Unmanaged\\{guid}``
        — Description, Source, DnsSuffix, FirstNetwork, DefaultGatewayMac
* ``SYSTEM``
    * ``ControlSet001\\Services\\Tcpip\\Parameters\\Interfaces\\{guid}``
        — EnableDHCP, DhcpIPAddress, DhcpSubnetMask, DhcpDefaultGateway,
          DhcpServer, Domain, NameServer
"""

from __future__ import annotations

import hashlib
import logging
import struct
from typing import Any, Dict, List

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"
_SYSTEM_HIVE: str = "Windows/System32/config/SYSTEM"

_NETWORK_PROFILES_KEY: str = (
    r"Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles"
)
_NETWORK_SIGNATURES_KEY: str = (
    r"Microsoft\Windows NT\CurrentVersion\NetworkList\Signatures\Unmanaged"
)
_TCPIP_INTERFACES_KEY: str = (
    r"ControlSet001\Services\Tcpip\Parameters\Interfaces"
)

# Network category values (from Windows)
_CATEGORY_PUBLIC: int = 0
_CATEGORY_PRIVATE: int = 1
_CATEGORY_DOMAIN: int = 2

# NameType: 0x47 = wireless (most common for home/office)
_NAMETYPE_WIRELESS: int = 0x47
_NAMETYPE_WIRED: int = 0x06


# ---------------------------------------------------------------------------
# Default network definitions — deterministic per profile type
# ---------------------------------------------------------------------------

_HOME_NETWORKS: List[Dict[str, Any]] = [
    {
        "ssid": "HomeNetwork-5G",
        "category": _CATEGORY_PRIVATE,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "192.168.1.105",
        "subnet": "255.255.255.0",
        "gateway": "192.168.1.1",
        "dhcp_server": "192.168.1.1",
        "dns": "8.8.8.8",
    },
    {
        "ssid": "Starbucks-WiFi",
        "category": _CATEGORY_PUBLIC,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "10.0.0.42",
        "subnet": "255.255.255.0",
        "gateway": "10.0.0.1",
        "dhcp_server": "10.0.0.1",
        "dns": "10.0.0.1",
    },
]

_OFFICE_NETWORKS: List[Dict[str, Any]] = [
    {
        "ssid": "CorpNet-Secure",
        "category": _CATEGORY_DOMAIN,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "10.10.5.42",
        "subnet": "255.255.254.0",
        "gateway": "10.10.4.1",
        "dhcp_server": "10.10.4.1",
        "dns": "10.10.1.10,10.10.1.11",
    },
    {
        "ssid": "CorpNet-Guest",
        "category": _CATEGORY_PUBLIC,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "172.16.0.55",
        "subnet": "255.255.255.0",
        "gateway": "172.16.0.1",
        "dhcp_server": "172.16.0.1",
        "dns": "8.8.8.8",
    },
]

_DEVELOPER_NETWORKS: List[Dict[str, Any]] = [
    {
        "ssid": "DevLab-5G",
        "category": _CATEGORY_PRIVATE,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "192.168.10.25",
        "subnet": "255.255.255.0",
        "gateway": "192.168.10.1",
        "dhcp_server": "192.168.10.1",
        "dns": "1.1.1.1,8.8.8.8",
    },
    {
        "ssid": "CorpNet-Dev",
        "category": _CATEGORY_DOMAIN,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "10.10.5.101",
        "subnet": "255.255.254.0",
        "gateway": "10.10.4.1",
        "dhcp_server": "10.10.4.1",
        "dns": "10.10.1.10",
    },
    {
        "ssid": "CoffeeShop-Free",
        "category": _CATEGORY_PUBLIC,
        "name_type": _NAMETYPE_WIRELESS,
        "dhcp_ip": "10.0.1.88",
        "subnet": "255.255.255.0",
        "gateway": "10.0.1.1",
        "dhcp_server": "10.0.1.1",
        "dns": "10.0.1.1",
    },
]

_PROFILE_NETWORK_MAP: Dict[str, List[Dict[str, Any]]] = {
    "home": _HOME_NETWORKS,
    "office": _OFFICE_NETWORKS,
    "developer": _DEVELOPER_NETWORKS,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NetworkProfilesError(Exception):
    """Raised when network profile operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class NetworkProfiles(BaseService):
    """Writes network profile and interface registry entries.

    Produces Wi-Fi SSID history, adapter descriptions, and TCP/IP
    interface configuration under both SOFTWARE and SYSTEM hives.

    Dependencies (injected):
        hive_writer: Low-level hive read/write service.
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
        return "NetworkProfiles"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type: str — one of "home", "office", "developer".

        Raises:
            NetworkProfilesError: If profile_type is missing or invalid.
        """
        profile_type = context.get("profile_type")
        if profile_type is None:
            raise NetworkProfilesError(
                "Missing required 'profile_type' in context"
            )
        self.write_network_profiles(profile_type)

    # -- public API ---------------------------------------------------------

    def write_network_profiles(self, profile_type: str) -> None:
        """Build and execute network registry operations.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.

        Raises:
            NetworkProfilesError: On invalid profile type or write failure.
        """
        networks = self._get_networks(profile_type)
        operations = self.build_operations(networks)

        try:
            self._hive_writer.execute_operations(operations)
        except HiveWriterError as exc:
            raise NetworkProfilesError(
                f"Failed to write network profiles: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_network_profiles_complete",
            "profile_type": profile_type,
            "networks_count": len(networks),
            "operations_count": len(operations),
        })
        logger.info(
            "Written %d network profiles (%d ops) for '%s'",
            len(networks),
            len(operations),
            profile_type,
        )

    def build_operations(
        self, networks: List[Dict[str, Any]]
    ) -> List[HiveOperation]:
        """Build all network registry operations.

        Args:
            networks: List of network definition dicts.

        Returns:
            List of :class:`HiveOperation` ready for execution.
        """
        ops: List[HiveOperation] = []
        for network in networks:
            guid = self._derive_guid(network["ssid"])
            ops.extend(self._build_profile_ops(network, guid))
            ops.extend(self._build_signature_ops(network, guid))
            ops.extend(self._build_interface_ops(network, guid))
        return ops

    # -- operation builders -------------------------------------------------

    def _build_profile_ops(
        self,
        network: Dict[str, Any],
        guid: str,
    ) -> List[HiveOperation]:
        """Build NetworkList\\Profiles entries for one network."""
        key_path = rf"{_NETWORK_PROFILES_KEY}\{guid}"
        return [
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="ProfileName",
                value_data=network["ssid"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Description",
                value_data=network["ssid"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Managed",
                value_data=0,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Category",
                value_data=network["category"],
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="NameType",
                value_data=network["name_type"],
                value_type=RegistryValueType.REG_DWORD,
            ),
        ]

    def _build_signature_ops(
        self,
        network: Dict[str, Any],
        guid: str,
    ) -> List[HiveOperation]:
        """Build NetworkList\\Signatures entries for one network."""
        key_path = rf"{_NETWORK_SIGNATURES_KEY}\{guid}"
        gateway_mac = self._derive_mac(network["ssid"])
        return [
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Description",
                value_data=network["ssid"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="Source",
                value_data=8,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="DnsSuffix",
                value_data="",
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="FirstNetwork",
                value_data=network["ssid"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SOFTWARE_HIVE,
                key_path=key_path,
                value_name="DefaultGatewayMac",
                value_data=gateway_mac,
                value_type=RegistryValueType.REG_BINARY,
            ),
        ]

    def _build_interface_ops(
        self,
        network: Dict[str, Any],
        guid: str,
    ) -> List[HiveOperation]:
        """Build Tcpip\\Interfaces entries for one network."""
        key_path = rf"{_TCPIP_INTERFACES_KEY}\{guid}"
        return [
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="EnableDHCP",
                value_data=1,
                value_type=RegistryValueType.REG_DWORD,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="DhcpIPAddress",
                value_data=network["dhcp_ip"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="DhcpSubnetMask",
                value_data=network["subnet"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="DhcpDefaultGateway",
                value_data=network["gateway"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="DhcpServer",
                value_data=network["dhcp_server"],
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="Domain",
                value_data="",
                value_type=RegistryValueType.REG_SZ,
            ),
            HiveOperation(
                hive_path=_SYSTEM_HIVE,
                key_path=key_path,
                value_name="NameServer",
                value_data=network["dns"],
                value_type=RegistryValueType.REG_SZ,
            ),
        ]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _get_networks(profile_type: str) -> List[Dict[str, Any]]:
        """Look up network definitions for a profile type.

        Args:
            profile_type: One of ``"home"``, ``"office"``, ``"developer"``.
                ``*_user`` aliases are accepted and normalized.

        Returns:
            List of network definition dicts.

        Raises:
            NetworkProfilesError: If profile type is unknown.
        """
        profile_key = profile_type.lower().removesuffix("_user")
        networks = _PROFILE_NETWORK_MAP.get(profile_key)
        if networks is None:
            valid = ", ".join(sorted(_PROFILE_NETWORK_MAP.keys()))
            raise NetworkProfilesError(
                f"Unknown profile type '{profile_type}'. "
                f"Valid types: {valid}"
            )
        return networks

    @staticmethod
    def _derive_guid(ssid: str) -> str:
        """Derive a deterministic GUID from the SSID.

        Args:
            ssid: Network SSID.

        Returns:
            GUID string in standard format.
        """
        digest = hashlib.sha256(ssid.encode("utf-8")).hexdigest()
        return (
            f"{{{digest[:8]}-{digest[8:12]}-{digest[12:16]}"
            f"-{digest[16:20]}-{digest[20:32]}}}"
        )

    @staticmethod
    def _derive_mac(ssid: str) -> bytes:
        """Derive a deterministic 6-byte MAC address from the SSID.

        The first byte has the locally-administered bit set (0x02)
        to avoid collisions with real OUIs.

        Args:
            ssid: Network SSID.

        Returns:
            6-byte MAC address.
        """
        digest = hashlib.sha256(
            (ssid + ":mac").encode("utf-8")
        ).digest()
        mac = bytearray(digest[:6])
        mac[0] = (mac[0] & 0xFE) | 0x02  # locally administered
        return bytes(mac)
