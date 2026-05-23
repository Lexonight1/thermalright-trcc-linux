"""Tests for ControllerBuilder routing — per-OS platform contract and per-device.

Design:
  - Platform builder fixtures (linux_builder, windows_builder, etc.) inject real
    Platform directly — no patching of module-level OS flags.
  - Per-device tests parametrize over ALL_DEVICES — adding a device to the
    registry automatically adds coverage here.
  - for_current_os() is tested as a smoke test only — which platform it picks
    is OS-dependent; the per-platform contract is verified via the fixtures.
"""
from __future__ import annotations

import pytest

from trcc.legacy.core.builder import ControllerBuilder
from trcc.legacy.core.models import (
    ALL_DEVICES,
    LED_DEVICES,
    PROTOCOL_TRAITS,
    SCSI_DEVICES,
    DetectedDevice,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _detected_for(vid: int, pid: int, entry) -> DetectedDevice:
    """Build a DetectedDevice exactly as the detector would for a given registry entry."""
    return DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor,
        product_name=entry.product,
        usb_path="2-1",
        protocol=entry.protocol,
        device_type=entry.device_type,
        implementation=entry.implementation,
    )


# ── for_current_os() — smoke test ─────────────────────────────────────────────

def test_for_current_os_returns_controller_builder():
    """for_current_os() must return a ControllerBuilder on any supported OS."""
    ControllerBuilder.__new__(ControllerBuilder)
    actual = ControllerBuilder.for_current_os()
    assert isinstance(actual, ControllerBuilder)


# ── Platform contract — each builder carries the right Platform ────────────

class TestLinuxBuilderContract:
    def test_has_os_platform(self, linux_builder):
        from trcc.legacy.core.ports import Platform
        assert isinstance(linux_builder.os, Platform)

    def test_platform_iterable(self, linux_builder):
        """Phase 3: detect via list(platform) instead of build_detect_fn wrapper."""
        from unittest.mock import patch
        with patch("trcc.legacy.adapters.device.detector.DeviceDetector.make_detect_fn",
                   return_value=lambda: []):
            assert list(linux_builder.os) == []

    def test_build_device_led_returns_led(self, linux_builder):
        from trcc.legacy.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestWindowsBuilderContract:
    def test_has_os_platform(self, windows_builder):
        from trcc.legacy.core.ports import Platform
        assert isinstance(windows_builder.os, Platform)

    def test_build_device_led_returns_led(self, windows_builder):
        from trcc.legacy.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = windows_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestMacOSBuilderContract:
    def test_has_os_platform(self, macos_builder):
        from trcc.legacy.core.ports import Platform
        assert isinstance(macos_builder.os, Platform)

    def test_build_device_led_returns_led(self, macos_builder):
        from trcc.legacy.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = macos_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestBSDBuilderContract:
    def test_has_os_platform(self, bsd_builder):
        from trcc.legacy.core.ports import Platform
        assert isinstance(bsd_builder.os, Platform)

    def test_build_device_led_returns_led(self, bsd_builder):
        from trcc.legacy.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = bsd_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


# ── build_device() — per-device protocol routing ─────────────────────────────

@pytest.mark.parametrize("vid_pid,entry", list(ALL_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in ALL_DEVICES])
def test_build_device_routes_to_correct_type(vid_pid, entry, linux_builder):
    from trcc.legacy.core.device import Device
    from trcc.legacy.services.image import ImageService

    detected = _detected_for(*vid_pid, entry)
    trait = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi'])

    if trait.is_led:
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device) and device.is_led
    else:
        linux_builder._renderer = ImageService.renderer()
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device) and device.is_lcd


@pytest.mark.parametrize("vid_pid,entry", list(ALL_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in ALL_DEVICES])
def test_build_device_wires_device_service(vid_pid, entry, linux_builder):
    from trcc.legacy.services.image import ImageService

    detected = _detected_for(*vid_pid, entry)
    trait = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi'])

    if not trait.is_led:
        linux_builder._renderer = ImageService.renderer()

    device = linux_builder.build_device(detected)
    assert device._device_svc is not None


@pytest.mark.parametrize("vid_pid,entry", list(LED_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in LED_DEVICES])
def test_led_devices_inject_protocol(vid_pid, entry, linux_builder):
    detected = _detected_for(*vid_pid, entry)
    device = linux_builder.build_device(detected)
    assert device._protocol is not None


def test_build_device_no_detected_builds_lcd(linux_builder):
    from trcc.legacy.core.device import Device
    from trcc.legacy.services.image import ImageService

    linux_builder._renderer = ImageService.renderer()
    device = linux_builder.build_device(None)
    assert isinstance(device, Device)
    assert device.is_lcd


def test_build_device_lcd_without_renderer_raises(linux_builder):
    vid, pid = next(iter(SCSI_DEVICES))
    entry = SCSI_DEVICES[(vid, pid)]
    detected = _detected_for(vid, pid, entry)
    linux_builder._renderer = None

    with pytest.raises(RuntimeError, match="renderer"):
        linux_builder.build_device(detected)


# ── PROTOCOL_TRAITS completeness ─────────────────────────────────────────────

def test_all_devices_have_known_protocol():
    unknown = [
        f"{v:04X}:{p:04X} ({e.protocol!r})"
        for (v, p), e in ALL_DEVICES.items()
        if e.protocol not in PROTOCOL_TRAITS
    ]
    assert not unknown, f"Unknown protocols in ALL_DEVICES: {unknown}"


def test_scsi_devices_are_not_led():
    for (v, p), entry in SCSI_DEVICES.items():
        trait = PROTOCOL_TRAITS[entry.protocol]
        assert not trait.is_led


def test_led_devices_classify_as_led():
    for (v, p), entry in LED_DEVICES.items():
        trait = PROTOCOL_TRAITS.get(entry.protocol)
        assert trait is not None
        assert trait.is_led


def test_lcd_and_led_have_distinct_protocols():
    from trcc.legacy.core.models import HID_LCD_DEVICES
    hid_lcd_protocols = {e.protocol for e in HID_LCD_DEVICES.values()}
    led_protocols = {e.protocol for e in LED_DEVICES.values()}
    assert hid_lcd_protocols.isdisjoint(led_protocols)
