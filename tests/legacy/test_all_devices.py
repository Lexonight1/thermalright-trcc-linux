"""Integration tests — every device through the real app flow.

MockPlatform at the USB boundary, real builder, real services.
Parametrized over ALL_DEVICES, FBL_PROFILES, and PmRegistry.
Tests what users actually do: connect, send, brightness, rotate, tick.
"""
from __future__ import annotations

import os

import pytest
from mock_platform import MockPlatform

from trcc.legacy.core.models import (
    ALL_DEVICES,
    FBL_PROFILES,
    PROTOCOL_TRAITS,
    DeviceEntry,
    PmRegistry,
)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


# ═════════════════════════════════════════════════════════════════════════════
# Shared setup — one place, DI through the real flow
# ═════════════════════════════════════════════════════════════════════════════


def _connect_lcd(spec: dict, tmp_path):
    """Build + connect an LCD device through the real flow."""
    platform = MockPlatform([spec], root=tmp_path / '.trcc')
    (tmp_path / '.trcc').mkdir(exist_ok=True)
    (tmp_path / '.trcc' / 'data').mkdir(exist_ok=True)

    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.builder import ControllerBuilder

    init_settings(platform)
    builder = ControllerBuilder(platform).with_renderer(QtRenderer())
    detected = platform.detect_devices()[0]
    device = builder.build_device(detected)
    result = device.connect(detected)
    return device, result


def _connect_led(spec: dict, tmp_path):
    """Build + connect an LED device through the real flow."""
    platform = MockPlatform([spec], root=tmp_path / '.trcc')
    (tmp_path / '.trcc').mkdir(exist_ok=True)
    (tmp_path / '.trcc' / 'data').mkdir(exist_ok=True)

    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.builder import ControllerBuilder

    init_settings(platform)
    builder = ControllerBuilder(platform)
    detected = platform.detect_devices()[0]
    device = builder.build_device(detected)
    result = device.connect(detected)
    return device, result


def _lcd_spec(vid=0x0402, pid=0x3922, fbl=100, w=320, h=320, name="Test LCD"):
    return {"type": "lcd", "vid": f"{vid:04x}", "pid": f"{pid:04x}",
            "name": name, "resolution": f"{w}x{h}", "pm": fbl}


def _led_spec(pm=3, model="AX120_DIGITAL", name="Test LED"):
    return {"type": "led", "vid": "0416", "pid": "8001",
            "name": name, "model": model, "pm": pm}


# ═════════════════════════════════════════════════════════════════════════════
# ALL_DEVICES — every VID:PID connects
# ═════════════════════════════════════════════════════════════════════════════

_DEVICE_IDS = [
    f"{vid:04x}:{pid:04x}_{entry.implementation}"
    for (vid, pid), entry in ALL_DEVICES.items()
]


def _make_spec_from_entry(vid: int, pid: int, entry: DeviceEntry) -> dict:
    is_led = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi']).is_led
    if is_led:
        return _led_spec(model=entry.model, name=f"{entry.vendor} {entry.product}")
    from trcc.legacy.core.models import FBL_TO_RESOLUTION
    res = FBL_TO_RESOLUTION.get(entry.fbl, (320, 320))
    return _lcd_spec(vid, pid, entry.fbl, res[0], res[1],
                     f"{entry.vendor} {entry.product}")


@pytest.mark.parametrize("vid_pid,entry", ALL_DEVICES.items(), ids=_DEVICE_IDS)
class TestAllDevices:
    """Every device in ALL_DEVICES connects and works."""

    def test_connects(self, vid_pid, entry, tmp_path, tmp_config):
        spec = _make_spec_from_entry(*vid_pid, entry)
        is_led = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi']).is_led
        device, result = _connect_led(spec, tmp_path) if is_led else _connect_lcd(spec, tmp_path)
        assert result["success"]
        assert device.connected
        assert device.device_info is not None
        assert device.device_info.vid == vid_pid[0]
        assert device.device_info.pid == vid_pid[1]
        assert device.is_led == is_led
        assert device.is_lcd == (not is_led)
        device.cleanup()


# ═════════════════════════════════════════════════════════════════════════════
# FBL_PROFILES — every LCD resolution, full user session
# ═════════════════════════════════════════════════════════════════════════════

_FBL_IDS = [f"fbl{fbl}_{p.width}x{p.height}" for fbl, p in FBL_PROFILES.items()]


@pytest.mark.parametrize("fbl,profile", FBL_PROFILES.items(), ids=_FBL_IDS)
class TestLCDUserSession:
    """What a real LCD user does — for every resolution."""

    def test_connect(self, fbl, profile, tmp_path, tmp_config):
        device, result = _connect_lcd(
            _lcd_spec(fbl=fbl, w=profile.width, h=profile.height), tmp_path)
        assert result["success"]
        assert device.is_lcd
        w, h = device.lcd_size
        assert w > 0 and h > 0

    def test_send_color(self, fbl, profile, tmp_path, tmp_config):
        device, _ = _connect_lcd(
            _lcd_spec(fbl=fbl, w=profile.width, h=profile.height), tmp_path)
        result = device.send_color(255, 0, 0)
        assert result["success"]
        assert result["image"] is not None

    def test_set_brightness(self, fbl, profile, tmp_path, tmp_config):
        device, _ = _connect_lcd(
            _lcd_spec(fbl=fbl, w=profile.width, h=profile.height), tmp_path)
        result = device.set_brightness(50)
        assert result["success"]

    def test_tick(self, fbl, profile, tmp_path, tmp_config):
        device, _ = _connect_lcd(
            _lcd_spec(fbl=fbl, w=profile.width, h=profile.height), tmp_path)
        device.tick()  # no crash

    def test_cleanup(self, fbl, profile, tmp_path, tmp_config):
        device, _ = _connect_lcd(
            _lcd_spec(fbl=fbl, w=profile.width, h=profile.height), tmp_path)
        device.cleanup()  # no crash


# ═════════════════════════════════════════════════════════════════════════════
# PmRegistry — every LED product, full user session
# ═════════════════════════════════════════════════════════════════════════════

_PM_ENTRIES = list(PmRegistry)
_PM_IDS = [f"pm{pm}_{entry.model_name}" for pm, entry in _PM_ENTRIES]


@pytest.mark.parametrize("pm,pm_entry", _PM_ENTRIES, ids=_PM_IDS)
class TestLEDUserSession:
    """What a real LED user does — for every product."""

    def test_connect(self, pm, pm_entry, tmp_path, tmp_config):
        device, result = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        assert result["success"]
        assert device.is_led
        assert device.state is not None
        assert device.status is not None

    def test_set_color(self, pm, pm_entry, tmp_path, tmp_config):
        device, _ = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        result = device.set_color(255, 0, 0)
        assert result["success"]
        assert result["colors"]

    def test_set_mode(self, pm, pm_entry, tmp_path, tmp_config):
        device, _ = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        result = device.set_mode("static")
        assert result["success"]

    def test_set_brightness(self, pm, pm_entry, tmp_path, tmp_config):
        device, _ = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        result = device.set_brightness(50)
        assert result["success"]

    def test_tick_returns_colors(self, pm, pm_entry, tmp_path, tmp_config):
        device, _ = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        result = device.tick()
        assert result is not None
        assert "colors" in result
        assert len(result["colors"]) > 0

    def test_cleanup(self, pm, pm_entry, tmp_path, tmp_config):
        device, _ = _connect_led(
            _led_spec(pm=pm, model=pm_entry.model_name), tmp_path)
        device.cleanup()  # no crash
