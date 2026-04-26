"""Tests for PrefetchService — A11 gate validation.

A11: >=30 Prefetch files, mean file size >=15 KB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.filesystem.prefetch import PrefetchService


def _make_svc(mount_manager, timestamp_service, audit_logger):
    return PrefetchService(
        mount_manager=mount_manager,
        timestamp_service=timestamp_service,
        audit_logger=audit_logger,
    )


def _pf_files(mount_dir: Path):
    pf_dir = mount_dir / "Windows" / "Prefetch"
    if not pf_dir.exists():
        return []
    return list(pf_dir.glob("*.pf"))


class TestPrefetchBasic:

    @pytest.fixture(autouse=True)
    def _inject(self, service_ctx):
        self.ctx = service_ctx

    def test_prefetch_dir_created(self, mount_manager, timestamp_service, audit_logger):
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        pf = mount_manager.root / "Windows" / "Prefetch"
        assert pf.is_dir()

    def test_pf_files_have_scca_magic(self, mount_manager, timestamp_service, audit_logger):
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        for pf in _pf_files(mount_manager.root)[:5]:
            data = pf.read_bytes()
            assert data[4:8] == b"SCCA", f"{pf.name} missing SCCA magic at offset 4"

    def test_audit_logged(self, mount_manager, timestamp_service, audit_logger):
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        svcs = {e["service"] for e in audit_logger.entries}
        assert "PrefetchService" in svcs


class TestA11Density:
    """Gate A11: >=30 files, mean >=15 KB."""

    @pytest.fixture(autouse=True)
    def _inject(self, service_ctx):
        self.ctx = service_ctx

    def test_at_least_30_pf_files(self, mount_manager, timestamp_service, audit_logger):
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        files = _pf_files(mount_manager.root)
        assert len(files) >= 30, f"A11 fail: only {len(files)} .pf files (need >=30)"

    def test_mean_size_at_least_15kb(self, mount_manager, timestamp_service, audit_logger):
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        files = _pf_files(mount_manager.root)
        assert files, "no .pf files found"
        mean_kb = sum(f.stat().st_size for f in files) / len(files) / 1024
        assert mean_kb >= 15.0, f"A11 fail: mean size {mean_kb:.1f} KB (need >=15)"

    def test_pf_version_is_30(self, mount_manager, timestamp_service, audit_logger):
        import struct
        _make_svc(mount_manager, timestamp_service, audit_logger).apply(self.ctx)
        for pf in _pf_files(mount_manager.root)[:5]:
            data = pf.read_bytes()
            version = struct.unpack_from("<I", data, 0)[0]
            assert version == 30, f"{pf.name} has version {version}, expected 30"
