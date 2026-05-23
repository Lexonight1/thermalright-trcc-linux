"""Shared fixtures for device adapter tests (includes HID testing setup)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.adapters.device.detector import enable_hid_testing
from trcc.legacy.adapters.device.hid import UsbTransport


@pytest.fixture(autouse=True)
def _enable_hid_for_tests():
    """Auto-enable HID testing for every test in adapters/device/."""
    enable_hid_testing()


@pytest.fixture(autouse=True)
def _patch_hid_sleep():
    """Disable time.sleep in HID device modules for fast tests."""
    with patch("trcc.legacy.adapters.device.hid.time.sleep"), \
         patch("trcc.legacy.adapters.device.led.time.sleep"):
        yield


def make_mock_transport() -> MagicMock:
    """Create a MagicMock that satisfies the UsbTransport interface."""
    t = MagicMock(spec=UsbTransport)
    t.is_open = True
    return t


# ---------------------------------------------------------------------------
# Device profile fixtures — driven by env vars for tools/diagnose.py
#
# TRCC_DIAGNOSE_VID       Vendor ID hex (default: 87AD)
# TRCC_DIAGNOSE_PID       Product ID hex (default: 70DB)
# TRCC_DIAGNOSE_PM        PM byte from handshake (default: 100)
# TRCC_DIAGNOSE_SUB       SUB byte from handshake (default: 0)
# TRCC_DIAGNOSE_PROTOCOL  Protocol name (default: bulk)
#
# Defaults match the canonical 87AD:70DB bulk device so the normal test
# suite is unaffected when env vars are absent.
# ---------------------------------------------------------------------------

import os  # noqa: E402


@pytest.fixture
def device_vid() -> int:
    return int(os.getenv("TRCC_DIAGNOSE_VID", "87AD"), 16)


@pytest.fixture
def device_pid() -> int:
    return int(os.getenv("TRCC_DIAGNOSE_PID", "70DB"), 16)


@pytest.fixture
def device_pm() -> int:
    return int(os.getenv("TRCC_DIAGNOSE_PM", "100"))


@pytest.fixture
def device_sub() -> int:
    return int(os.getenv("TRCC_DIAGNOSE_SUB", "0"))


@pytest.fixture
def device_protocol() -> str:
    return os.getenv("TRCC_DIAGNOSE_PROTOCOL", "bulk")


