"""Tests for resilient VHDX mount and dismount behavior."""

from pathlib import Path

import pytest

from core.vm_manager import VMManager, VMManagerError


def _make_image(tmp_path: Path) -> Path:
    """Create a dummy VHDX file path for VMManager initialization."""
    image = tmp_path / "test.vhdx"
    image.write_bytes(b"dummy")
    return image


def test_mount_reuses_already_attached_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the image is already attached, mount should not call Mount-DiskImage."""
    manager = VMManager(str(_make_image(tmp_path)))
    commands: list[str] = []

    monkeypatch.setattr(manager, "_is_image_attached", lambda: True)
    monkeypatch.setattr(manager, "_discover_windows_drive", lambda: "E:\\")

    def _fake_run(command: str) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(manager, "_run_powershell", _fake_run)

    drive = manager.mount_vhdx()

    assert drive == "E:\\"
    assert manager.mounted_drive == "E:\\"
    assert commands == []


def test_mount_tolerates_in_use_race_when_image_becomes_attached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient mount failure should be tolerated if the image is attached afterward."""
    manager = VMManager(str(_make_image(tmp_path)))
    attached_states = iter([False, True])

    monkeypatch.setattr(manager, "_is_image_attached", lambda: next(attached_states))
    monkeypatch.setattr(manager, "_discover_windows_drive", lambda: "F:\\")

    def _fake_run(_command: str) -> str:
        raise VMManagerError("PowerShell error: file in use")

    monkeypatch.setattr(manager, "_run_powershell", _fake_run)

    drive = manager.mount_vhdx()

    assert drive == "F:\\"
    assert manager.mounted_drive == "F:\\"


def test_dismount_skips_when_image_already_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dismount should no-op without issuing a PowerShell command when detached."""
    manager = VMManager(str(_make_image(tmp_path)))
    manager.mounted_drive = "Z:\\"
    run_called = {"value": False}

    monkeypatch.setattr(manager, "_is_image_attached", lambda: False)

    def _fake_run(_command: str) -> str:
        run_called["value"] = True
        return ""

    monkeypatch.setattr(manager, "_run_powershell", _fake_run)

    manager.dismount_vhdx()

    assert run_called["value"] is False
    assert manager.mounted_drive is None
