"""Tests for core.vm_manager module."""

from pathlib import Path

import pytest

import core.vm_manager as vm_module
from core.vm_manager import VMManager, VMManagerError


@pytest.fixture()
def disk_image(tmp_path: Path) -> Path:
    """Create a placeholder VHD file for VMManager initialization."""
    image = tmp_path / "sample.vhd"
    image.write_text("placeholder", encoding="utf-8")
    return image


@pytest.fixture()
def manager(disk_image: Path) -> VMManager:
    """Return a VMManager bound to the temporary disk image."""
    return VMManager(str(disk_image))


def test_mount_vhdx_discovers_drive_on_first_attempt(
    manager: VMManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mount succeeds when discovery returns a drive immediately."""
    responses = iter(["", "E:\\"])
    commands: list[str] = []

    def fake_run(command: str) -> str:
        commands.append(command)
        return next(responses, "")

    monkeypatch.setattr(manager, "_run_powershell", fake_run)
    monkeypatch.setattr(vm_module.time, "sleep", lambda _: None)

    drive = manager.mount_vhdx()

    assert drive == "E:\\"
    assert manager.mounted_drive == "E:\\"
    assert commands
    assert "Mount-DiskImage" in commands[0]


def test_mount_vhdx_retries_when_first_discovery_is_empty(
    manager: VMManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mount retries discovery before succeeding."""
    responses = iter(["", "", "F:\\"])
    sleep_calls: list[float] = []

    def fake_run(_: str) -> str:
        return next(responses, "")

    monkeypatch.setattr(manager, "_run_powershell", fake_run)
    monkeypatch.setattr(vm_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(vm_module, "_MOUNT_DISCOVERY_ATTEMPTS", 3)

    drive = manager.mount_vhdx()

    assert drive == "F:\\"
    # One initial post-mount wait + one retry wait before second discovery succeeds.
    assert len(sleep_calls) == 2


def test_mount_vhdx_dismounts_when_no_drive_is_discovered(
    manager: VMManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mount failure triggers cleanup dismount and raises VMManagerError."""
    responses = iter(["", "", ""])
    dismount_called = {"value": False}

    def fake_run(_: str) -> str:
        return next(responses, "")

    def fake_dismount() -> None:
        dismount_called["value"] = True

    monkeypatch.setattr(manager, "_run_powershell", fake_run)
    monkeypatch.setattr(manager, "dismount_vhdx", fake_dismount)
    monkeypatch.setattr(vm_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(vm_module, "_MOUNT_DISCOVERY_ATTEMPTS", 2)

    with pytest.raises(VMManagerError, match="Failed to locate Windows partition"):
        manager.mount_vhdx()

    assert dismount_called["value"] is True
