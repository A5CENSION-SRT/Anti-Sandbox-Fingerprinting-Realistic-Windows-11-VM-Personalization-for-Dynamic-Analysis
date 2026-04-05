"""Tests for the NetworkProfiles registry service."""

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.audit_logger import AuditLogger
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)
from services.registry.network_profiles import (
    NetworkProfiles,
    NetworkProfilesError,
    _HOME_NETWORKS,
    _OFFICE_NETWORKS,
    _DEVELOPER_NETWORKS,
    _NETWORK_PROFILES_KEY,
    _NETWORK_SIGNATURES_KEY,
    _SOFTWARE_HIVE,
    _SYSTEM_HIVE,
    _TCPIP_INTERFACES_KEY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def audit_logger() -> AuditLogger:
    """Shared AuditLogger instance."""
    return AuditLogger()


@pytest.fixture()
def mock_hive_writer() -> MagicMock:
    """Mock HiveWriter — no real I/O needed."""
    writer = MagicMock(spec=HiveWriter)
    writer.execute_operations = MagicMock(return_value=None)
    return writer


@pytest.fixture()
def service(
    mock_hive_writer: MagicMock, audit_logger: AuditLogger
) -> NetworkProfiles:
    """NetworkProfiles wired to mock HiveWriter and real AuditLogger."""
    return NetworkProfiles(mock_hive_writer, audit_logger)


# ---------------------------------------------------------------------------
# 1. Construction & BaseService interface
# ---------------------------------------------------------------------------

class TestNetworkProfilesInit:
    """NetworkProfiles must satisfy the BaseService contract."""

    def test_service_name(self, service: NetworkProfiles) -> None:
        assert service.service_name == "NetworkProfiles"

    def test_service_name_is_string(self, service: NetworkProfiles) -> None:
        assert isinstance(service.service_name, str)


# ---------------------------------------------------------------------------
# 2. apply() context validation
# ---------------------------------------------------------------------------

class TestApplyContextValidation:
    """apply() must validate the context before delegating."""

    def test_missing_profile_type_raises(
        self, service: NetworkProfiles
    ) -> None:
        with pytest.raises(NetworkProfilesError, match="profile_type"):
            service.apply({})

    def test_invalid_profile_type_raises(
        self, service: NetworkProfiles
    ) -> None:
        with pytest.raises(NetworkProfilesError, match="Unknown profile"):
            service.apply({"profile_type": "gamer"})

    def test_valid_profile_accepted(
        self, service: NetworkProfiles
    ) -> None:
        service.apply({"profile_type": "home"})


# ---------------------------------------------------------------------------
# 3. Operation building — structure
# ---------------------------------------------------------------------------

class TestOperationStructure:
    """build_operations must produce correct hive/key structures."""

    def test_home_network_count(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        # 2 networks × (5 profile + 5 signature + 7 interface) = 34
        assert len(ops) == 2 * (5 + 5 + 7)

    def test_office_network_count(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_OFFICE_NETWORKS)
        assert len(ops) == 2 * (5 + 5 + 7)

    def test_developer_network_count(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_DEVELOPER_NETWORKS)
        assert len(ops) == 3 * (5 + 5 + 7)

    def test_all_ops_are_set(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        assert all(o.operation == "set" for o in ops)

    def test_profile_ops_target_software(
        self, service: NetworkProfiles
    ) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        profile_ops = [
            o for o in ops
            if _NETWORK_PROFILES_KEY in o.key_path
        ]
        assert all(o.hive_path == _SOFTWARE_HIVE for o in profile_ops)

    def test_signature_ops_target_software(
        self, service: NetworkProfiles
    ) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        sig_ops = [
            o for o in ops
            if _NETWORK_SIGNATURES_KEY in o.key_path
        ]
        assert all(o.hive_path == _SOFTWARE_HIVE for o in sig_ops)

    def test_interface_ops_target_system(
        self, service: NetworkProfiles
    ) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        iface_ops = [
            o for o in ops
            if _TCPIP_INTERFACES_KEY in o.key_path
        ]
        assert all(o.hive_path == _SYSTEM_HIVE for o in iface_ops)


# ---------------------------------------------------------------------------
# 4. Profile-specific values
# ---------------------------------------------------------------------------

class TestProfileValues:
    """Verify correct values for each profile type."""

    def test_home_ssids(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        ssid_ops = [
            o for o in ops
            if o.value_name == "ProfileName"
        ]
        ssids = {o.value_data for o in ssid_ops}
        assert "HomeNetwork-5G" in ssids
        assert "Starbucks-WiFi" in ssids

    def test_dhcp_ip_present(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_OFFICE_NETWORKS)
        dhcp_ops = [o for o in ops if o.value_name == "DhcpIPAddress"]
        assert len(dhcp_ops) == 2
        ips = {o.value_data for o in dhcp_ops}
        assert "10.10.5.42" in ips

    def test_enable_dhcp_is_dword(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        dhcp_enable = [o for o in ops if o.value_name == "EnableDHCP"]
        assert all(o.value_type == RegistryValueType.REG_DWORD for o in dhcp_enable)
        assert all(o.value_data == 1 for o in dhcp_enable)

    def test_gateway_mac_is_binary(self, service: NetworkProfiles) -> None:
        ops = service.build_operations(_HOME_NETWORKS)
        mac_ops = [o for o in ops if o.value_name == "DefaultGatewayMac"]
        assert all(o.value_type == RegistryValueType.REG_BINARY for o in mac_ops)
        for op in mac_ops:
            assert isinstance(op.value_data, bytes)
            assert len(op.value_data) == 6


# ---------------------------------------------------------------------------
# 5. Deterministic helpers
# ---------------------------------------------------------------------------

class TestDeterministicHelpers:
    """Static derivation methods must be deterministic."""

    def test_guid_deterministic(self) -> None:
        g1 = NetworkProfiles._derive_guid("TestSSID")
        g2 = NetworkProfiles._derive_guid("TestSSID")
        assert g1 == g2

    def test_guid_differs_per_ssid(self) -> None:
        g1 = NetworkProfiles._derive_guid("Net-A")
        g2 = NetworkProfiles._derive_guid("Net-B")
        assert g1 != g2

    def test_guid_format(self) -> None:
        guid = NetworkProfiles._derive_guid("MyWifi")
        assert re.fullmatch(
            r"\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
            r"-[0-9a-f]{4}-[0-9a-f]{12}\}",
            guid,
        )

    def test_mac_deterministic(self) -> None:
        m1 = NetworkProfiles._derive_mac("TestSSID")
        m2 = NetworkProfiles._derive_mac("TestSSID")
        assert m1 == m2

    def test_mac_length(self) -> None:
        mac = NetworkProfiles._derive_mac("TestSSID")
        assert len(mac) == 6

    def test_mac_locally_administered(self) -> None:
        mac = NetworkProfiles._derive_mac("Any-SSID")
        assert mac[0] & 0x02 == 0x02  # locally administered bit


# ---------------------------------------------------------------------------
# 6. HiveWriter delegation
# ---------------------------------------------------------------------------

class TestHiveWriterDelegation:
    """write_network_profiles must delegate to HiveWriter."""

    def test_execute_called(
        self,
        service: NetworkProfiles,
        mock_hive_writer: MagicMock,
    ) -> None:
        service.write_network_profiles("home")
        mock_hive_writer.execute_operations.assert_called_once()

    def test_hive_writer_error_wrapped(
        self,
        service: NetworkProfiles,
        mock_hive_writer: MagicMock,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "fail"
        )
        with pytest.raises(NetworkProfilesError, match="Failed"):
            service.write_network_profiles("office")


# ---------------------------------------------------------------------------
# 7. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Successful writes must produce audit entries."""

    def test_audit_on_success(
        self, service: NetworkProfiles, audit_logger: AuditLogger
    ) -> None:
        service.write_network_profiles("developer")
        assert len(audit_logger.entries) >= 1
        entry = audit_logger.entries[-1]
        assert entry["service"] == "NetworkProfiles"
        assert entry["operation"] == "write_network_profiles_complete"
        assert entry["profile_type"] == "developer"
        assert entry["networks_count"] == 3
        assert "timestamp" in entry
