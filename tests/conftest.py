#!/usr/bin/env python3
"""Pytest configuration and fixtures for ARC tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock system packages that may not be available
sys.modules['guestfs'] = MagicMock()
sys.modules['hivex'] = MagicMock()


@pytest.fixture
def mock_mount_manager():
    """Fixture for mock MountManager."""
    mock = MagicMock()
    mock.resolve.return_value = Path("/mnt/arc/test")
    return mock


@pytest.fixture
def mock_audit_logger():
    """Fixture for mock AuditLogger."""
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_service_context():
    """Fixture for mock ServiceContext."""
    mock = MagicMock()
    mock.identity_bundle.user.username = "testuser"
    mock.scheduler.events = []
    return mock


@pytest.fixture
def sample_vhdx_path(tmp_path):
    """Fixture for sample VHDX path."""
    vhdx = tmp_path / "test.vhdx"
    vhdx.write_bytes(b"VHD" + b"\x00" * 100)  # Mock VHDX header
    return vhdx
