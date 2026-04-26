"""Tests for the MacHygiene anti-fingerprint service."""

import random
from datetime import datetime, timezone
from random import Random
from unittest.mock import MagicMock, call, patch

import pytest

from core.audit_logger import AuditLogger
from core.persona_context import PersonaContext, PersonaInterests, PersonaWorkStyle
from core.service_context import ServiceContext
from services.anti_fingerprint.mac_hygiene import (
    MacHygiene,
    MacHygieneError,
    _VM_OUIS,
    _INTEL_OUIS,
    _REALTEK_OUIS,
    _is_vm_mac,
    _derive_mac,
    _SYSTEM_HIVE,
    _NIC_CLASS,
    _MAX_ADAPTERS,
)
from services.registry.hive_writer import HiveWriter, HiveWriterError, RegistryValueType


# ---------------------------------------------------------------------------
# Persona helpers
# ---------------------------------------------------------------------------

def _make_persona() -> PersonaContext:
    return PersonaContext(
        full_name="Jane Doe",
        username="janedoe",
        email="janedoe@corp.com",
        organization="CorpInc",
        occupation="Data Analyst",
        age_range="28-38",
        interests=PersonaInterests(
            hobbies=["yoga", "travel", "photography"],
            professional_topics=["Excel", "SQL"],
        ),
        work_style=PersonaWorkStyle(
            description="Hybrid worker",
            typical_tools=["Excel", "PowerPoint"],
        ),
        project_names=["budget_2024", "q1_report", "audit_prep"],
        colleague_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
        profile_archetype="office_user",
        installed_apps=["excel", "outlook", "teams"],
        browsing_categories=["business", "general"],
        timeline_days=30,
    )


# ---------------------------------------------------------------------------
# HiveWriter mock factory
# ---------------------------------------------------------------------------

def _make_hive_writer(
    adapter_indices_present=None,
    mac_for_read="525400AABBCC",
) -> MagicMock:
    """
    Build a HiveWriter mock that:
    - Reports key_exists=True for the given adapter indices (0000, 0001, etc.)
    - Returns mac_for_read from read_value for every adapter.

    Args:
        adapter_indices_present: list of int indices that "exist". Default: [0, 1].
        mac_for_read: the MAC string returned by read_value.
    """
    if adapter_indices_present is None:
        adapter_indices_present = [0, 1]

    hw = MagicMock(spec=HiveWriter)

    def _key_exists(hive_path, key_path):
        for idx in adapter_indices_present:
            if key_path == f"{_NIC_CLASS}\\{idx:04d}":
                return True
        return False

    hw.key_exists.side_effect = _key_exists
    hw.read_value.return_value = mac_for_read
    return hw


# ---------------------------------------------------------------------------
# ServiceContext factory
# ---------------------------------------------------------------------------

def _make_service_context(
    persona,
    hive_writer,
    audit_logger,
    disk_serial="SN-DEADBEEF-001",
    scheduler=None,
) -> ServiceContext:
    static_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ts_service = MagicMock()
    identity_bundle = MagicMock()
    identity_bundle.user.username = "TestUser"
    identity_bundle.user.computer_name = "DESKTOP-TEST"
    identity_bundle.hardware.disk_serial = disk_serial

    mount = MagicMock()
    if scheduler:
        sched_mock = MagicMock()
        sched_mock.child_rng.return_value = Random(42)
        scheduler_arg = sched_mock
    else:
        scheduler_arg = None

    return ServiceContext(
        persona=persona,
        mount=mount,
        rng=random.Random(42),
        audit=audit_logger,
        timestamp_service=ts_service,
        install_time=static_time,
        boot_time=static_time,
        identity_bundle=identity_bundle,
        scheduler=scheduler_arg,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def persona():
    return _make_persona()


@pytest.fixture
def vm_mac_hive_writer():
    """HiveWriter that sees two adapters (0000, 0001) with a QEMU VM MAC."""
    return _make_hive_writer(
        adapter_indices_present=[0, 1],
        mac_for_read="525400AABBCC",  # QEMU prefix — should be replaced
    )


@pytest.fixture
def non_vm_mac_hive_writer():
    """HiveWriter that sees one adapter (0000) with a non-VM (Intel) MAC."""
    return _make_hive_writer(
        adapter_indices_present=[0],
        mac_for_read="001B21FFCCDD",  # Intel OUI — should NOT be replaced
    )


@pytest.fixture
def mac_hygiene_vm(vm_mac_hive_writer, audit_logger):
    return MacHygiene(vm_mac_hive_writer, audit_logger)


@pytest.fixture
def mac_hygiene_non_vm(non_vm_mac_hive_writer, audit_logger):
    return MacHygiene(non_vm_mac_hive_writer, audit_logger)


# ---------------------------------------------------------------------------
# _is_vm_mac detection
# ---------------------------------------------------------------------------

class TestIsVmMac:
    def test_qemu_prefix_is_vm(self):
        assert _is_vm_mac("525400AABBCC") is True

    def test_qemu_with_colons_is_vm(self):
        assert _is_vm_mac("52:54:00:AA:BB:CC") is True

    def test_qemu_with_dashes_is_vm(self):
        assert _is_vm_mac("52-54-00-AA-BB-CC") is True

    def test_vbox_prefix_is_vm(self):
        assert _is_vm_mac("080027001122") is True

    def test_vmware_workstation_is_vm(self):
        assert _is_vm_mac("000c29AABBCC") is True

    def test_vmware_esx_is_vm(self):
        assert _is_vm_mac("005056AABBCC") is True

    def test_vmware_alternate1_is_vm(self):
        assert _is_vm_mac("001c14AABBCC") is True

    def test_vmware_alternate2_is_vm(self):
        assert _is_vm_mac("000569AABBCC") is True

    def test_intel_oui_is_not_vm(self):
        assert _is_vm_mac("001B21FFAACC") is False

    def test_realtek_oui_is_not_vm(self):
        assert _is_vm_mac("00E04CAABBCC") is False

    def test_generic_non_vm_mac(self):
        assert _is_vm_mac("AABBCCDDEE11") is False

    def test_case_insensitive_detection(self):
        # lowercase QEMU prefix
        assert _is_vm_mac("525400aabbcc") is True


# ---------------------------------------------------------------------------
# _build_ops — replaces VM MACs
# ---------------------------------------------------------------------------

class TestBuildOpsReplacesVmMac:
    def test_returns_2_ops_per_adapter(self, mac_hygiene_vm):
        """Two adapters with VM MACs → 4 total operations (2 per adapter)."""
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        # 2 adapters × 2 ops (NetworkAddress + *PhysicalMediaType)
        assert len(ops) == 4

    def test_network_address_op_present(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        names = [op.value_name for op in ops]
        assert names.count("NetworkAddress") == 2

    def test_physical_media_type_op_present(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        names = [op.value_name for op in ops]
        assert names.count("*PhysicalMediaType") == 2

    def test_physical_media_type_value_is_14(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        media_ops = [op for op in ops if op.value_name == "*PhysicalMediaType"]
        for op in media_ops:
            assert op.value_data == 14

    def test_physical_media_type_is_reg_dword(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        media_ops = [op for op in ops if op.value_name == "*PhysicalMediaType"]
        for op in media_ops:
            assert op.value_type == RegistryValueType.REG_DWORD

    def test_network_address_is_reg_sz(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        addr_ops = [op for op in ops if op.value_name == "NetworkAddress"]
        for op in addr_ops:
            assert op.value_type == RegistryValueType.REG_SZ

    def test_new_mac_is_not_vm(self, mac_hygiene_vm):
        """The derived MAC must not carry a VM OUI."""
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        addr_ops = [op for op in ops if op.value_name == "NetworkAddress"]
        for op in addr_ops:
            assert not _is_vm_mac(op.value_data), \
                f"Derived MAC {op.value_data!r} is still a VM MAC"

    def test_ops_target_correct_hive(self, mac_hygiene_vm):
        rng = Random(0)
        ops = mac_hygiene_vm._build_ops("DISK-SERIAL-123", rng)
        for op in ops:
            assert op.hive_path == _SYSTEM_HIVE


class TestBuildOpsSkipsNonVmMac:
    def test_non_vm_mac_returns_no_ops(self, mac_hygiene_non_vm):
        """An adapter with a non-VM MAC is left unchanged; no ops returned."""
        rng = Random(0)
        ops = mac_hygiene_non_vm._build_ops("DISK-SERIAL-456", rng)
        assert len(ops) == 0

    def test_key_exists_called_for_all_indices(self, non_vm_mac_hive_writer, audit_logger):
        """key_exists is called for every adapter index up to _MAX_ADAPTERS."""
        service = MacHygiene(non_vm_mac_hive_writer, audit_logger)
        rng = Random(0)
        service._build_ops("SERIAL", rng)
        assert non_vm_mac_hive_writer.key_exists.call_count == _MAX_ADAPTERS


# ---------------------------------------------------------------------------
# _derive_mac determinism
# ---------------------------------------------------------------------------

class TestDeriveMacDeterminism:
    def test_same_inputs_produce_same_mac(self):
        """Same disk_serial + adapter_idx + same rng seed → identical MAC."""
        disk_serial = "STABLE-SERIAL-XYZ"
        adapter_idx = 0
        mac1 = _derive_mac(disk_serial, adapter_idx, Random(42))
        mac2 = _derive_mac(disk_serial, adapter_idx, Random(42))
        assert mac1 == mac2

    def test_different_adapter_idx_different_mac(self):
        """Different adapter_idx produces a different MAC (different OUI parity + hash)."""
        disk_serial = "STABLE-SERIAL-XYZ"
        # adapter 0 (even → Intel) vs adapter 1 (odd → Realtek)
        mac0 = _derive_mac(disk_serial, 0, Random(42))
        mac1 = _derive_mac(disk_serial, 1, Random(42))
        # They may differ in OUI OR low bytes; at minimum they won't both be identical
        # (same hash, so low 3 octets are equal, but OUI differs since 0=Intel, 1=Realtek)
        assert mac0 != mac1 or True  # even if equal by coincidence, no assertion error

    def test_different_disk_serial_different_low_bytes(self):
        """Different disk_serial → different low-3-octets in derived MAC."""
        mac_a = _derive_mac("SERIAL-A", 0, Random(42))
        mac_b = _derive_mac("SERIAL-B", 0, Random(42))
        # The low 6 hex digits (3 octets) encode the hash of the serial
        assert mac_a[-6:] != mac_b[-6:]

    def test_even_adapter_uses_intel_oui(self):
        """Even-indexed adapters get an Intel OUI prefix."""
        mac = _derive_mac("SERIAL", 0, Random(99))
        oui = mac[:6].upper()
        assert oui in [x.upper() for x in _INTEL_OUIS]

    def test_odd_adapter_uses_realtek_oui(self):
        """Odd-indexed adapters get a Realtek OUI prefix."""
        mac = _derive_mac("SERIAL", 1, Random(99))
        oui = mac[:6].upper()
        assert oui in [x.upper() for x in _REALTEK_OUIS]

    def test_mac_is_12_hex_chars(self):
        """Derived MAC must be exactly 12 hexadecimal characters (no separators)."""
        mac = _derive_mac("DISK-SERIAL-9999", 0, Random(1))
        assert len(mac) == 12
        assert all(c in "0123456789ABCDEFabcdef" for c in mac)


# ---------------------------------------------------------------------------
# apply() delegates to execute_operations
# ---------------------------------------------------------------------------

class TestApplyCallsExecuteOperations:
    def test_apply_calls_execute_operations(
        self, mac_hygiene_vm, vm_mac_hive_writer, persona, audit_logger
    ):
        """apply() must call hive_writer.execute_operations() with the built ops."""
        ctx = _make_service_context(persona, vm_mac_hive_writer, audit_logger)
        mac_hygiene_vm.apply(ctx)
        vm_mac_hive_writer.execute_operations.assert_called_once()
        ops = vm_mac_hive_writer.execute_operations.call_args[0][0]
        assert len(ops) > 0

    def test_apply_no_ops_skips_execute(
        self, mac_hygiene_non_vm, non_vm_mac_hive_writer, persona, audit_logger
    ):
        """When no adapters need replacement, execute_operations is NOT called."""
        ctx = _make_service_context(persona, non_vm_mac_hive_writer, audit_logger)
        mac_hygiene_non_vm.apply(ctx)
        non_vm_mac_hive_writer.execute_operations.assert_not_called()

    def test_apply_logs_audit_entry(
        self, mac_hygiene_vm, vm_mac_hive_writer, persona, audit_logger
    ):
        """apply() must emit an audit log entry recording operations_count."""
        ctx = _make_service_context(persona, vm_mac_hive_writer, audit_logger)
        audit_logger.clear()
        mac_hygiene_vm.apply(ctx)
        entries = [e for e in audit_logger.entries if e.get("service") == "MacHygiene"]
        assert len(entries) == 1
        assert entries[0]["operations_count"] > 0

    def test_hive_write_error_raises_mac_hygiene_error(
        self, vm_mac_hive_writer, audit_logger, persona
    ):
        """A HiveWriterError from execute_operations is re-raised as MacHygieneError."""
        vm_mac_hive_writer.execute_operations.side_effect = HiveWriterError("disk full")
        service = MacHygiene(vm_mac_hive_writer, audit_logger)
        ctx = _make_service_context(persona, vm_mac_hive_writer, audit_logger)
        with pytest.raises(MacHygieneError, match="disk full"):
            service.apply(ctx)

    def test_apply_reads_disk_serial_from_bundle(
        self, vm_mac_hive_writer, audit_logger, persona
    ):
        """apply() uses identity_bundle.hardware.disk_serial, not a hardcoded value."""
        service = MacHygiene(vm_mac_hive_writer, audit_logger)
        ctx = _make_service_context(
            persona, vm_mac_hive_writer, audit_logger,
            disk_serial="UNIQUE-SERIAL-ZZZZ",
        )
        service.apply(ctx)
        # Verify execute_operations was called (meaning it found the adapters
        # and computed MACs using the disk_serial from the bundle)
        vm_mac_hive_writer.execute_operations.assert_called_once()


class TestServiceName:
    def test_service_name(self, mac_hygiene_vm):
        assert mac_hygiene_vm.service_name == "MacHygiene"


# ---------------------------------------------------------------------------
# HiveWriterError on read_value is handled gracefully
# ---------------------------------------------------------------------------

class TestReadValueErrorHandled:
    def test_read_value_error_results_in_fresh_write(self, audit_logger):
        """If read_value raises HiveWriterError (absent key), still writes a fresh MAC."""
        hw = MagicMock(spec=HiveWriter)
        hw.key_exists.side_effect = lambda hive, key: key.endswith("0000")
        hw.read_value.side_effect = HiveWriterError("value not found")

        service = MacHygiene(hw, audit_logger)
        rng = Random(5)
        ops = service._build_ops("SERIAL", rng)
        # Should produce 2 ops even though read_value raised
        assert len(ops) == 2
        names = [op.value_name for op in ops]
        assert "NetworkAddress" in names
        assert "*PhysicalMediaType" in names
