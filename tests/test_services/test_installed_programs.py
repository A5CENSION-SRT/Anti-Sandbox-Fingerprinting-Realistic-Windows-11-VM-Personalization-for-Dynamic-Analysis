"""Tests for the InstalledPrograms registry service."""

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
from services.registry.installed_programs import (
    InstalledPrograms,
    InstalledProgramsError,
    _PROGRAM_CATALOG,
    _SOFTWARE_HIVE,
    _UNINSTALL_KEY,
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
) -> InstalledPrograms:
    """InstalledPrograms wired to mock HiveWriter and real AuditLogger."""
    return InstalledPrograms(mock_hive_writer, audit_logger)


# ---------------------------------------------------------------------------
# 1. Construction & BaseService interface
# ---------------------------------------------------------------------------

class TestInstalledProgramsInit:
    """InstalledPrograms must satisfy the BaseService contract."""

    def test_service_name(self, service: InstalledPrograms) -> None:
        assert service.service_name == "InstalledPrograms"

    def test_service_name_is_string(self, service: InstalledPrograms) -> None:
        assert isinstance(service.service_name, str)


# ---------------------------------------------------------------------------
# 2. apply() context validation
# ---------------------------------------------------------------------------

class TestApplyContextValidation:
    """apply() must validate the context before delegating."""

    def test_missing_installed_apps_raises(
        self, service: InstalledPrograms
    ) -> None:
        with pytest.raises(InstalledProgramsError, match="installed_apps"):
            service.apply({})

    def test_valid_context_accepted(self, service: InstalledPrograms) -> None:
        service.apply({"installed_apps": ["notepad"], "username": "test"})

    def test_empty_list_is_noop(
        self, service: InstalledPrograms, mock_hive_writer: MagicMock
    ) -> None:
        service.apply({"installed_apps": []})
        mock_hive_writer.execute_operations.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Operation building
# ---------------------------------------------------------------------------

class TestOperationBuilding:
    """build_operations must produce correct Uninstall entries."""

    def test_known_app_produces_nine_ops(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["notepad"])
        assert len(ops) == 9

    def test_unknown_app_skipped(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["nonexistent_app_xyz"])
        assert len(ops) == 0

    def test_mixed_known_unknown(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["notepad", "unknown_app", "vlc"])
        # 9 per known app × 2 known = 18
        assert len(ops) == 18

    def test_all_ops_target_software_hive(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["chrome", "git"])
        assert all(o.hive_path == _SOFTWARE_HIVE for o in ops)

    def test_all_ops_are_set(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["docker"])
        assert all(o.operation == "set" for o in ops)

    def test_display_name_correct(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["vscode"])
        name_ops = [o for o in ops if o.value_name == "DisplayName"]
        assert len(name_ops) == 1
        assert name_ops[0].value_data == "Microsoft Visual Studio Code"

    def test_publisher_correct(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["docker"])
        pub_ops = [o for o in ops if o.value_name == "Publisher"]
        assert len(pub_ops) == 1
        assert pub_ops[0].value_data == "Docker Inc."

    def test_estimated_size_is_dword(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["vlc"])
        size_ops = [o for o in ops if o.value_name == "EstimatedSize"]
        assert len(size_ops) == 1
        assert size_ops[0].value_type == RegistryValueType.REG_DWORD
        assert isinstance(size_ops[0].value_data, int)

    def test_username_substitution_in_paths(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["vscode"], username="jane.doe")
        loc_ops = [o for o in ops if o.value_name == "InstallLocation"]
        assert len(loc_ops) == 1
        assert "jane.doe" in loc_ops[0].value_data
        assert "{username}" not in loc_ops[0].value_data

    def test_install_date_format(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["notepad"])
        date_ops = [o for o in ops if o.value_name == "InstallDate"]
        assert len(date_ops) == 1
        assert re.fullmatch(r"\d{8}", date_ops[0].value_data)

    def test_uninstall_string_present(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["git"])
        uninstall_ops = [o for o in ops if o.value_name == "UninstallString"]
        assert len(uninstall_ops) == 1
        assert "uninstall.exe" in uninstall_ops[0].value_data

    def test_system_component_for_store_app(
        self, service: InstalledPrograms
    ) -> None:
        """System/Store apps get SystemComponent=1 instead of UninstallString."""
        ops = service.build_operations(["notepad"])
        sc_ops = [o for o in ops if o.value_name == "SystemComponent"]
        assert len(sc_ops) == 1
        assert sc_ops[0].value_data == 1
        assert sc_ops[0].value_type == RegistryValueType.REG_DWORD
        # Should NOT have an UninstallString
        uninst = [o for o in ops if o.value_name == "UninstallString"]
        assert len(uninst) == 0

    def test_regular_app_has_no_system_component(
        self, service: InstalledPrograms
    ) -> None:
        """Regular apps get UninstallString, not SystemComponent."""
        ops = service.build_operations(["chrome"])
        sc_ops = [o for o in ops if o.value_name == "SystemComponent"]
        assert len(sc_ops) == 0
        uninst = [o for o in ops if o.value_name == "UninstallString"]
        assert len(uninst) == 1

    def test_key_path_contains_uninstall_and_guid(
        self, service: InstalledPrograms
    ) -> None:
        ops = service.build_operations(["chrome"])
        for op in ops:
            assert op.key_path.startswith(_UNINSTALL_KEY + "\\")
            # Subkey should be GUID-like
            subkey = op.key_path.split("\\")[-1]
            assert subkey.startswith("{") and subkey.endswith("}")


# ---------------------------------------------------------------------------
# 4. Deterministic derivation
# ---------------------------------------------------------------------------

class TestDeterministicDerivation:
    """Static helpers must be deterministic and well-formed."""

    def test_subkey_deterministic(self) -> None:
        k1 = InstalledPrograms._derive_subkey("chrome")
        k2 = InstalledPrograms._derive_subkey("chrome")
        assert k1 == k2

    def test_subkey_differs_per_app(self) -> None:
        k1 = InstalledPrograms._derive_subkey("chrome")
        k2 = InstalledPrograms._derive_subkey("git")
        assert k1 != k2

    def test_subkey_guid_format(self) -> None:
        key = InstalledPrograms._derive_subkey("notepad")
        assert re.fullmatch(
            r"\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
            r"-[0-9a-f]{4}-[0-9a-f]{12}\}",
            key,
        )

    def test_install_date_deterministic(self) -> None:
        d1 = InstalledPrograms._derive_install_date("vlc")
        d2 = InstalledPrograms._derive_install_date("vlc")
        assert d1 == d2

    def test_install_date_valid(self) -> None:
        date_str = InstalledPrograms._derive_install_date("chrome")
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        assert 2021 <= year <= 2024
        assert 1 <= month <= 12
        assert 1 <= day <= 28

    def test_install_date_all_catalog_apps_in_range(self) -> None:
        """Every catalog app must produce a date in [2021, 2024]."""
        for app_key in _PROGRAM_CATALOG:
            date_str = InstalledPrograms._derive_install_date(app_key)
            year = int(date_str[:4])
            assert 2021 <= year <= 2024, (
                f"{app_key} produced year={year} from date={date_str}"
            )


# ---------------------------------------------------------------------------
# 5. HiveWriter delegation
# ---------------------------------------------------------------------------

class TestHiveWriterDelegation:
    """write_programs must delegate to HiveWriter correctly."""

    def test_execute_called_for_known_apps(
        self,
        service: InstalledPrograms,
        mock_hive_writer: MagicMock,
    ) -> None:
        service.write_programs(["notepad"])
        mock_hive_writer.execute_operations.assert_called_once()

    def test_execute_not_called_for_empty(
        self,
        service: InstalledPrograms,
        mock_hive_writer: MagicMock,
    ) -> None:
        service.write_programs([])
        mock_hive_writer.execute_operations.assert_not_called()

    def test_hive_writer_error_wrapped(
        self,
        service: InstalledPrograms,
        mock_hive_writer: MagicMock,
    ) -> None:
        mock_hive_writer.execute_operations.side_effect = HiveWriterError(
            "boom"
        )
        with pytest.raises(InstalledProgramsError, match="Failed"):
            service.write_programs(["notepad"])


# ---------------------------------------------------------------------------
# 6. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    """Successful writes must produce audit entries."""

    def test_audit_on_success(
        self, service: InstalledPrograms, audit_logger: AuditLogger
    ) -> None:
        service.write_programs(["notepad", "vlc"])
        assert len(audit_logger.entries) >= 1
        entry = audit_logger.entries[-1]
        assert entry["service"] == "InstalledPrograms"
        assert entry["operation"] == "write_programs_complete"
        assert entry["programs_count"] == 2
        assert "timestamp" in entry


# ---------------------------------------------------------------------------
# 7. Catalog coverage
# ---------------------------------------------------------------------------

class TestCatalogCoverage:
    """Verify that all profile apps map to catalog entries."""

    def test_office_apps_in_catalog(self) -> None:
        for app in ["outlook", "teams", "excel", "word"]:
            assert app in _PROGRAM_CATALOG, f"{app} missing from catalog"

    def test_developer_apps_in_catalog(self) -> None:
        for app in ["vscode", "docker", "git", "terminal"]:
            assert app in _PROGRAM_CATALOG, f"{app} missing from catalog"

    def test_home_apps_in_catalog(self) -> None:
        for app in ["spotify", "vlc", "chrome"]:
            assert app in _PROGRAM_CATALOG, f"{app} missing from catalog"

    def test_base_apps_in_catalog(self) -> None:
        for app in ["notepad", "calculator"]:
            assert app in _PROGRAM_CATALOG, f"{app} missing from catalog"
