"""Tests for TypingHistory registry service (A10 density gate)."""

from __future__ import annotations

import random
from unittest.mock import MagicMock, call

import pytest

from services.registry.typing_history import (
    TypingHistory,
    _TYPED_URLS_KEY,
    _TYPED_PATHS_KEY,
    _WORD_WHEEL_KEY,
)
from services.registry.hive_writer import RegistryValueType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_hive_writer():
    hw = MagicMock()
    hw.execute_operations = MagicMock()
    return hw


@pytest.fixture
def mock_audit():
    a = MagicMock()
    a.entries = []
    return a


@pytest.fixture
def service(mock_hive_writer, mock_audit):
    return TypingHistory(mock_hive_writer, mock_audit)


def _make_ctx(timeline_days=360, daily=50, cats=None, apps=None, scheduler=None):
    ctx = MagicMock()
    ctx.persona.timeline_days = timeline_days
    ctx.persona.daily_avg_sites = daily
    ctx.persona.browsing_categories = cats or ["general", "tech"]
    ctx.persona.installed_apps = apps or ["chrome", "vscode", "outlook"]
    ctx.identity_bundle.user.username = "testuser"
    ctx.scheduler = scheduler
    return ctx


# ---------------------------------------------------------------------------
# Tests: service identity
# ---------------------------------------------------------------------------

class TestServiceIdentity:
    def test_service_name(self, service):
        assert service.service_name == "TypingHistory"


# ---------------------------------------------------------------------------
# Tests: data collection (no scheduler)
# ---------------------------------------------------------------------------

class TestCollectionNoScheduler:
    def test_typed_urls_meet_target(self, service):
        ctx = _make_ctx(timeline_days=360, daily=50)
        rng = random.Random(42)
        urls = service._collect_typed_urls(ctx, rng)
        # target = min(2000, 50*360//4=4500) = 2000
        assert len(urls) == 2000

    def test_typed_urls_are_valid_strings(self, service):
        ctx = _make_ctx()
        rng = random.Random(42)
        urls = service._collect_typed_urls(ctx, rng)
        for u in urls[:10]:
            assert isinstance(u, str)
            assert u.startswith("https://")

    def test_typed_paths_scale_with_timeline(self, service):
        ctx_short = _make_ctx(timeline_days=30)
        ctx_long  = _make_ctx(timeline_days=360)
        rng = random.Random(42)
        short_paths = service._collect_typed_paths(ctx_short, rng)
        long_paths  = service._collect_typed_paths(ctx_long, rng)
        assert len(long_paths) > len(short_paths)

    def test_typed_paths_include_base_windows_paths(self, service):
        ctx = _make_ctx()
        rng = random.Random(42)
        paths = service._collect_typed_paths(ctx, rng)
        joined = "\n".join(paths)
        assert "C:\\" in joined

    def test_word_wheel_scale_with_timeline(self, service):
        ctx_short = _make_ctx(timeline_days=30)
        ctx_long  = _make_ctx(timeline_days=360)
        rng = random.Random(42)
        short_q = service._collect_word_wheel(ctx_short, rng)
        long_q  = service._collect_word_wheel(ctx_long, rng)
        assert len(long_q) > len(short_q)

    def test_word_wheel_contains_app_names(self, service):
        ctx = _make_ctx(apps=["vscode", "docker", "firefox"])
        rng = random.Random(42)
        queries = service._collect_word_wheel(ctx, rng)
        query_str = "\n".join(queries).lower()
        assert "vscode" in query_str or "chrome" in query_str or "firefox" in query_str


# ---------------------------------------------------------------------------
# Tests: operation building
# ---------------------------------------------------------------------------

class TestOperationBuilding:
    def test_typed_url_ops_are_reg_sz(self, service):
        ops = service._build_typed_url_ops("NTUSER.DAT", ["https://a.com", "https://b.com"])
        assert all(op.value_type == RegistryValueType.REG_SZ for op in ops)

    def test_typed_url_ops_key_path(self, service):
        ops = service._build_typed_url_ops("NTUSER.DAT", ["https://a.com"])
        assert ops[0].key_path == _TYPED_URLS_KEY

    def test_typed_url_value_names_are_indexed(self, service):
        ops = service._build_typed_url_ops("NTUSER.DAT", ["https://a.com", "https://b.com"])
        names = [op.value_name for op in ops]
        assert "url1" in names
        assert "url2" in names

    def test_word_wheel_ops_include_mrulistex(self, service):
        ops = service._build_word_wheel_ops("NTUSER.DAT", ["hello", "world"])
        mru_ops = [op for op in ops if op.value_name == "MRUListEx"]
        assert len(mru_ops) == 1
        assert mru_ops[0].value_type == RegistryValueType.REG_BINARY

    def test_word_wheel_ops_include_query_values(self, service):
        ops = service._build_word_wheel_ops("NTUSER.DAT", ["hello", "world"])
        query_ops = [op for op in ops if op.value_name.isdigit()]
        assert len(query_ops) == 2


# ---------------------------------------------------------------------------
# Tests: A10 gate — combined ops from full 360-day persona
# ---------------------------------------------------------------------------

class TestA10Density:
    def test_total_ops_exceed_2000_without_scheduler(self, service):
        ctx = _make_ctx(timeline_days=360, daily=50)
        rng = random.Random(42)
        urls  = service._collect_typed_urls(ctx, rng)
        paths = service._collect_typed_paths(ctx, rng)
        queries = service._collect_word_wheel(ctx, rng)
        total_ops = len(urls) + len(paths) + len(queries) + 1  # +1 for MRUListEx
        # TypingHistory alone should contribute 2000+ ops toward A10
        assert total_ops >= 2000, f"Expected ≥2000 ops, got {total_ops}"

    def test_apply_calls_execute_operations(self, service, mock_hive_writer):
        ctx = _make_ctx(timeline_days=360)
        service.apply(ctx)
        mock_hive_writer.execute_operations.assert_called_once()

    def test_apply_logs_audit_entry(self, service, mock_audit):
        ctx = _make_ctx(timeline_days=360)
        service.apply(ctx)
        mock_audit.log.assert_called_once()
        entry = mock_audit.log.call_args[0][0]
        assert entry["service"] == "TypingHistory"
        assert "total_ops" in entry
        assert entry["total_ops"] >= 2000
