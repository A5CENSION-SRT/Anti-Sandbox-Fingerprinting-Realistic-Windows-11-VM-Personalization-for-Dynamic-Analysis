"""Tests for the SystemIdentity registry service."""

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from core.audit_logger import AuditLogger
from core.identity_generator import (
    HardwareIdentity,
    IdentityBundle,
    UserIdentity,
)
from core.mount_manager import MountManager
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)
from services.registry.system_identity import (
    SystemIdentity,
    SystemIdentityError,
    _ACTIVE_COMPUTERNAME_KEY,
    _COMPUTERNAME_KEY,
    _CRYPTOGRAPHY,
    _DEFAULT_BUILD_LAB,
    _DEFAULT_BUILD_LAB_EX,
    _DEFAULT_CURRENT_BUILD,
    _DEFAULT_CURRENT_VERSION,
    _NT_CURRENT_VERSION,
    _SOFTWARE_HIVE,
    _SYSTEM_HIVE,
    _TCPIP_PARAMS_KEY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(**overrides: Any) -> UserIdentity:
    """Build a UserIdentity with sensible defaults, applying *overrides*."""
    defaults = {
        "full_name": "Jane Doe",
        "username": "jane.doe",
        "email": "jane.doe@acmecorp.com",
        "organization": "Acme Corp",
        "computer_name": "ACME-LT-042",
    }
    defaults.update(overrides)
    return UserIdentity(**defaults)


def _make_hardware(**overrides: Any) -> HardwareIdentity:
    """Build a HardwareIdentity with sensible defaults."""
    defaults = {
        "bios_vendor": "Dell Inc.",
        "bios_version": "2.18.0",
        "bios_release_date": date(2023, 6, 15),
        "motherboard_model": "Latitude 5540",
        "disk_model": "Samsung SSD 980 PRO 1TB",
        "disk_serial": "S6B2NJ0TC12345K",
        "gpu_model": "NVIDIA GeForce RTX 3060",
    }
    defaults.update(overrides)
    return HardwareIdentity(**defaults)


def _make_bundle(**user_overrides: Any) -> IdentityBundle:
    """Build a complete IdentityBundle."""
    return IdentityBundle(
        user=_make_user(**user_overrides),
        hardware=_make_hardware(),
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
    """Mock HiveWriter — no real I/O needed for operation builder tests."""
    writer = MagicMock(spec=HiveWriter)
    writer.execute_operations = MagicMock(return_value=None)
    return writer


@pytest.fixture()
def service(
    mock_hive_writer: MagicMock, audit_logger: AuditLogger
) -> SystemIdentity:
    """SystemIdentity wired to mock HiveWriter and real AuditLogger."""
    return SystemIdentity(mock_hive_writer, audit_logger)


@pytest.fixture()
def bundle() -> IdentityBundle:
    """Default identity bundle for tests."""
    return _make_bundle()


# ---------------------------------------------------------------------------
# 1. Construction & BaseService interface
# ---------------------------------------------------------------------------

class TestSystemIdentityInit:
    """SystemIdentity must satisfy the BaseService contract."""

    def test_service_name(self, service: SystemIdentity) -> None:
        assert service.service_name == "SystemIdentity"

    def test_service_name_is_string(self, service: SystemIdentity) -> None:
        assert isinstance(service.service_name, str)


# ---------------------------------------------------------------------------
# 2. apply() context validation
# ---------------------------------------------------------------------------

class TestApplyContextValidation:
    """apply() must validate the context before delegating."""

    def test_missing_bundle_raises(self, service: SystemIdentity) -> None:
        with pytest.raises(SystemIdentityError, match="Missing.*identity_bundle"):
            service.apply({})

    def test_none_bundle_raises(self, service: SystemIdentity) -> None:
        with pytest.raises(SystemIdentityError, match="Missing.*identity_bundle"):
            service.apply({"identity_bundle": None})

    def test_wrong_type_raises(self, service: SystemIdentity) -> None:
        with pytest.raises(SystemIdentityError, match="Expected IdentityBundle"):
            service.apply({"identity_bundle": "not_a_bundle"})

    def test_valid_bundle_accepted(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        service.apply({"identity_bundle": bundle})
        # Should not raise


# ---------------------------------------------------------------------------
# 3. Operation building — SOFTWARE hive
# ---------------------------------------------------------------------------

class TestSoftwareHiveOperations:
    """build_operations must produce correct SOFTWARE hive operations."""

    def test_registered_owner(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        owner_ops = [
            o for o in ops
            if o.value_name == "RegisteredOwner"
            and o.hive_path == _SOFTWARE_HIVE
        ]
        assert len(owner_ops) == 1
        assert owner_ops[0].value_data == "Jane Doe"
        assert owner_ops[0].value_type == RegistryValueType.REG_SZ
        assert owner_ops[0].key_path == _NT_CURRENT_VERSION

    def test_registered_organization(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        org_ops = [
            o for o in ops
            if o.value_name == "RegisteredOrganization"
        ]
        assert len(org_ops) == 1
        assert org_ops[0].value_data == "Acme Corp"
        assert org_ops[0].value_type == RegistryValueType.REG_SZ

    def test_product_id_format(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        pid_ops = [o for o in ops if o.value_name == "ProductId"]
        assert len(pid_ops) == 1
        # Format: XXXXX-XXX-XXXXXXX-XXXXX (all digits)
        assert re.fullmatch(
            r"\d{5}-\d{3}-\d{7}-\d{5}", pid_ops[0].value_data
        )

    def test_install_date_is_dword_in_range(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        date_ops = [o for o in ops if o.value_name == "InstallDate"]
        assert len(date_ops) == 1
        assert date_ops[0].value_type == RegistryValueType.REG_DWORD
        epoch = date_ops[0].value_data
        assert isinstance(epoch, int)
        assert 1577836800 <= epoch < 1735689600  # 2020–2025 range

    def test_current_build(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        build_ops = [o for o in ops if o.value_name == "CurrentBuild"]
        assert len(build_ops) == 1
        assert build_ops[0].value_data == _DEFAULT_CURRENT_BUILD

    def test_current_version(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        ver_ops = [o for o in ops if o.value_name == "CurrentVersion"]
        assert len(ver_ops) == 1
        assert ver_ops[0].value_data == _DEFAULT_CURRENT_VERSION

    def test_build_lab(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        lab_ops = [o for o in ops if o.value_name == "BuildLab"]
        assert len(lab_ops) == 1
        assert lab_ops[0].value_data == _DEFAULT_BUILD_LAB

    def test_build_lab_ex(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        labex_ops = [o for o in ops if o.value_name == "BuildLabEx"]
        assert len(labex_ops) == 1
        assert labex_ops[0].value_data == _DEFAULT_BUILD_LAB_EX

    def test_machine_guid_format(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        guid_ops = [o for o in ops if o.value_name == "MachineGuid"]
        assert len(guid_ops) == 1
        # Standard GUID format: 8-4-4-4-12 hex chars
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
            r"-[0-9a-f]{4}-[0-9a-f]{12}",
            guid_ops[0].value_data,
        )
        assert guid_ops[0].key_path == _CRYPTOGRAPHY

    def test_all_software_ops_target_software_hive(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        sw_ops = [o for o in ops if o.hive_path == _SOFTWARE_HIVE]
        # 8 NT CurrentVersion + 1 Cryptography = 9
        assert len(sw_ops) == 9


# ---------------------------------------------------------------------------
# 4. Operation building — SYSTEM hive
# ---------------------------------------------------------------------------

class TestSystemHiveOperations:
    """build_operations must produce correct SYSTEM hive operations."""

    def test_computer_name_stored(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        cn_ops = [
            o for o in ops
            if o.key_path == _COMPUTERNAME_KEY
            and o.value_name == "ComputerName"
        ]
        assert len(cn_ops) == 1
        assert cn_ops[0].value_data == "ACME-LT-042"
        assert cn_ops[0].hive_path == _SYSTEM_HIVE

    def test_active_computer_name(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        acn_ops = [
            o for o in ops
            if o.key_path == _ACTIVE_COMPUTERNAME_KEY
            and o.value_name == "ComputerName"
        ]
        assert len(acn_ops) == 1
        assert acn_ops[0].value_data == "ACME-LT-042"

    def test_hostname_matches_computer_name(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        host_ops = [
            o for o in ops
            if o.value_name == "Hostname"
            and o.key_path == _TCPIP_PARAMS_KEY
        ]
        assert len(host_ops) == 1
        assert host_ops[0].value_data == bundle.user.computer_name

    def test_nv_hostname(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        nv_ops = [
            o for o in ops
            if o.value_name == "NV Hostname"
        ]
        assert len(nv_ops) == 1
        assert nv_ops[0].value_data == bundle.user.computer_name

    def test_domain_is_empty(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        domain_ops = [
            o for o in ops
            if o.value_name == "Domain"
            and o.key_path == _TCPIP_PARAMS_KEY
        ]
        assert len(domain_ops) == 1
        assert domain_ops[0].value_data == ""

    def test_all_system_ops_target_system_hive(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        sys_ops = [o for o in ops if o.hive_path == _SYSTEM_HIVE]
        # 2 ComputerName + 3 TCP/IP params = 5
        assert len(sys_ops) == 5


# ---------------------------------------------------------------------------
# 5. Total operation count
# ---------------------------------------------------------------------------

class TestOperationCount:
    """Verify total operation list size and structure."""

    def test_total_operations(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        # 9 SOFTWARE + 5 SYSTEM = 14 total
        assert len(ops) == 14

    def test_all_operations_are_set(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        assert all(o.operation == "set" for o in ops)

    def test_all_operations_are_hive_operations(
        self, service: SystemIdentity, bundle: IdentityBundle
    ) -> None:
        ops = service.build_operations(bundle)
        assert all(isinstance(o, HiveOperation) for o in ops)


# ---------------------------------------------------------------------------
# 6. Deterministic derivation helpers
# ---------------------------------------------------------------------------

class TestDeterministicDerivation:
    """Static derivation methods must be deterministic and well-formed."""

    def test_machine_guid_deterministic(self) -> None:
        guid1 = SystemIdentity._derive_machine_guid("ACME-LT-042")
        guid2 = SystemIdentity._derive_machine_guid("ACME-LT-042")
        assert guid1 == guid2

    def test_machine_guid_differs_for_different_names(self) -> None:
        guid1 = SystemIdentity._derive_machine_guid("PC-A")
        guid2 = SystemIdentity._derive_machine_guid("PC-B")
        assert guid1 != guid2

    def test_machine_guid_format(self) -> None:
        guid = SystemIdentity._derive_machine_guid("TEST-PC")
        parts = guid.split("-")
        assert len(parts) == 5
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]
        assert all(c in "0123456789abcdef-" for c in guid)

    def test_product_id_deterministic(self) -> None:
        pid1 = SystemIdentity._derive_product_id("ACME-LT-042")
        pid2 = SystemIdentity._derive_product_id("ACME-LT-042")
        assert pid1 == pid2

    def test_product_id_differs_for_different_names(self) -> None:
        pid1 = SystemIdentity._derive_product_id("PC-A")
        pid2 = SystemIdentity._derive_product_id("PC-B")
        assert pid1 != pid2

    def test_product_id_all_digits(self) -> None:
        pid = SystemIdentity._derive_product_id("TEST-PC")
        digits_only = pid.replace("-", "")
        assert digits_only.isdigit()

    def test_install_date_deterministic(self) -> None:
        d1 = SystemIdentity._derive_install_date("ACME-LT-042")
        d2 = SystemIdentity._derive_install_date("ACME-LT-042")
        assert d1 == d2

    def test_install_date_in_range(self) -> None:
        for name in ("PC-A", "PC-B", "DESKTOP-1234567", "DEV-JANE-PC"):
            epoch = SystemIdentity._derive_install_date(name)
            assert 1577836800 <= epoch < 1735689600

    def test_install_date_differs_for_different_names(self) -> None:
        d1 = SystemIdentity._derive_install_date("PC-A")
        d2 = SystemIdentity._derive_install_date("PC-B")
        assert d1 != d2


# ---------------------------------------------------------------------------
# 7. HiveWriter delegation
# ---------------------------------------------------------------------------

class TestHiveWriterDelegation:
    """write_identity must delegate all ops to HiveWriter."""

    def test_execute_operations_called_once(
        self,
        service: SystemIdentity,
        mock_hive_writer: MagicMock,
        bundle: IdentityBundle,
    ) -> None:
        service.write_identity(bundle)
        mock_hive_writer.execute_operations.assert_called_once()

    def test_execute_operations_receives_all_ops(
        self,
        service: SystemIdentity,
        mock_hive_writer: MagicMock,
        bundle: IdentityBundle,
    ) -> None:
        service.write_identity(bundle)
        args = mock_hive_writer.execute_operations.call_args[0]
        assert len(args[0]) == 14

    def test_hive_writer_error_wrapped(
        self,
        service: SystemIdentity,
        mock_hive_writer: MagicMock,
        bundle: IdentityBundle,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "hive corrupt"
        )
        with pytest.raises(SystemIdentityError, match="Failed to write"):
            service.write_identity(bundle)

    def test_apply_delegates_to_write_identity(
        self,
        service: SystemIdentity,
        mock_hive_writer: MagicMock,
        bundle: IdentityBundle,
    ) -> None:
        service.apply({"identity_bundle": bundle})
        mock_hive_writer.execute_operations.assert_called_once()


# ---------------------------------------------------------------------------
# 8. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Every successful write must produce an audit entry."""

    def test_write_identity_audited(
        self,
        service: SystemIdentity,
        audit_logger: AuditLogger,
        bundle: IdentityBundle,
    ) -> None:
        service.write_identity(bundle)
        assert len(audit_logger.entries) >= 1

    def test_audit_entry_fields(
        self,
        service: SystemIdentity,
        audit_logger: AuditLogger,
        bundle: IdentityBundle,
    ) -> None:
        service.write_identity(bundle)
        entry = audit_logger.entries[-1]
        assert entry["service"] == "SystemIdentity"
        assert entry["operation"] == "write_identity_complete"
        assert entry["computer_name"] == "ACME-LT-042"
        assert entry["registered_owner"] == "Jane Doe"
        assert entry["operations_count"] == 14
        assert "timestamp" in entry

    def test_failed_write_not_audited_as_complete(
        self,
        service: SystemIdentity,
        mock_hive_writer: MagicMock,
        audit_logger: AuditLogger,
        bundle: IdentityBundle,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "boom"
        )
        with pytest.raises(SystemIdentityError):
            service.write_identity(bundle)
        # Should not have a "complete" entry
        complete_entries = [
            e for e in audit_logger.entries
            if e.get("operation") == "write_identity_complete"
        ]
        assert len(complete_entries) == 0


# ---------------------------------------------------------------------------
# 9. Different identity bundles
# ---------------------------------------------------------------------------

class TestVariousIdentities:
    """Operations must reflect the identity bundle data faithfully."""

    def test_developer_identity(self, service: SystemIdentity) -> None:
        bundle = _make_bundle(
            full_name="Dev User",
            username="dev.user",
            computer_name="DEV-USER-WS",
            organization="DevShop Inc.",
        )
        ops = service.build_operations(bundle)
        owner = next(o for o in ops if o.value_name == "RegisteredOwner")
        assert owner.value_data == "Dev User"
        org = next(o for o in ops if o.value_name == "RegisteredOrganization")
        assert org.value_data == "DevShop Inc."
        cn = [o for o in ops if o.value_name == "ComputerName"]
        assert all(o.value_data == "DEV-USER-WS" for o in cn)

    def test_home_user_identity(self, service: SystemIdentity) -> None:
        bundle = _make_bundle(
            full_name="Home User",
            username="home.user",
            computer_name="DESKTOP-A1B2C3D",
            organization="Personal",
        )
        ops = service.build_operations(bundle)
        owner = next(o for o in ops if o.value_name == "RegisteredOwner")
        assert owner.value_data == "Home User"
        org = next(o for o in ops if o.value_name == "RegisteredOrganization")
        assert org.value_data == "Personal"

    def test_special_chars_in_name(self, service: SystemIdentity) -> None:
        bundle = _make_bundle(
            full_name="José García-López",
            organization="Ñoño & Cía S.A.",
        )
        ops = service.build_operations(bundle)
        owner = next(o for o in ops if o.value_name == "RegisteredOwner")
        assert owner.value_data == "José García-López"
        org = next(o for o in ops if o.value_name == "RegisteredOrganization")
        assert org.value_data == "Ñoño & Cía S.A."
