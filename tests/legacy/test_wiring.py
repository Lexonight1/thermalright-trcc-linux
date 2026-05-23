"""Wiring tests — real protocols, real devices, fake USB.

Mock at the transport boundary, nowhere else. Every layer from transport
up is production code. If a method signature changes, a handshake byte
offset shifts, or the factory wiring breaks — this catches it.

Cycles through all 4 OS platforms × all protocol types.
"""
from __future__ import annotations

import pytest
from noop_transports import (
    NoopScsiTransport,
    NoopUsbTransport,
    build_hid_type2_response,
    build_hid_type3_response,
    build_led_response,
)

from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
from trcc.legacy.core.builder import ControllerBuilder
from trcc.legacy.core.models import (
    HID_LCD_DEVICES,
    LED_DEVICES,
    SCSI_DEVICES,
    DetectedDevice,
    fbl_to_resolution,
)
from trcc.legacy.core.ports import Platform

ALL_PLATFORMS = [LinuxPlatform, WindowsPlatform, MacOSPlatform, BSDPlatform]


# ═════════════════════════════════════════════════════════════════════════════
# Helpers — build DetectedDevice from registry entries
# ═════════════════════════════════════════════════════════════════════════════


def _first_device(registry: dict) -> tuple[tuple[int, int], object]:
    """Return first (vid, pid), entry from a device registry."""
    vid_pid = next(iter(registry))
    return vid_pid, registry[vid_pid]


def _make_detected(vid: int, pid: int, entry, path: str = "/dev/sg0") -> DetectedDevice:
    """Build a DetectedDevice from a registry entry."""
    return DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor,
        product_name=entry.product,
        usb_path=path,
        scsi_device=path if entry.protocol == "scsi" else None,
        protocol=entry.protocol,
        device_type=entry.device_type,
        implementation=entry.implementation,
        model=entry.model,
        button_image=entry.button_image,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Platform ABC contract — every OS implements what it must
# ═════════════════════════════════════════════════════════════════════════════


class TestPlatformContract:
    """Every Platform subclass can be instantiated and has all abstract methods."""

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_is_platform(self, platform_cls):
        p = platform_cls()
        assert isinstance(p, Platform)

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_has_scsi_transport(self, platform_cls):
        p = platform_cls()
        assert callable(p.create_scsi_transport)

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_has_detect_devices(self, platform_cls):
        p = platform_cls()
        assert callable(p.detect_devices)

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_has_sensor_enumerator(self, platform_cls):
        p = platform_cls()
        e = p.create_sensor_enumerator()
        assert hasattr(e, 'discover')
        assert hasattr(e, 'read_all')

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_shared_paths_work(self, platform_cls):
        p = platform_cls()
        assert p.config_dir().endswith('.trcc')
        assert 'data' in p.data_dir()

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_screen_capture_format(self, platform_cls):
        p = platform_cls()
        fmt = p._screen_capture_format()
        assert fmt in ('x11grab', 'gdigrab', 'avfoundation')


# ═════════════════════════════════════════════════════════════════════════════
# SCSI wiring — real ScsiProtocol → real ScsiDevice → noop transport
# ═════════════════════════════════════════════════════════════════════════════


class TestScsiWiring:
    """SCSI handshake through real protocol + device code, noop transport."""

    def _inject_noop_scsi(self, fbl: int = 100):
        """Inject a noop SCSI transport factory into the protocol factory."""
        def _noop_factory(path, vid=0, pid=0, **_ignore):
            return NoopScsiTransport(fbl=fbl)
        DeviceProtocolFactory.set_scsi_transport(_noop_factory)

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_scsi_handshake_wiring(self, platform_cls):
        """Factory → ScsiProtocol → ScsiDevice → transport, all real."""
        self._inject_noop_scsi(fbl=100)
        (vid, pid), entry = _first_device(SCSI_DEVICES)
        detected = _make_detected(vid, pid, entry)

        protocol = DeviceProtocolFactory.create_protocol(detected)
        result = protocol.handshake()

        assert result is not None
        assert result.resolution == fbl_to_resolution(100)

    @pytest.mark.parametrize("vid_pid,entry", list(SCSI_DEVICES.items()),
                             ids=lambda vp: f"{vp[0]:04X}:{vp[1]:04X}"
                             if isinstance(vp, tuple) else str(vp))
    def test_all_scsi_devices(self, vid_pid, entry):
        """Every registered SCSI device can handshake through real wiring."""
        vid, pid = vid_pid
        fbl = entry.fbl
        self._inject_noop_scsi(fbl=fbl)
        detected = _make_detected(vid, pid, entry)

        DeviceProtocolFactory._protocols.clear()
        protocol = DeviceProtocolFactory.create_protocol(detected)
        result = protocol.handshake()

        assert result is not None
        assert result.resolution == fbl_to_resolution(fbl)

    def test_scsi_frame_send(self):
        """Frame send goes through real ScsiDevice.send_frame_via_transport."""
        self._inject_noop_scsi(fbl=100)
        (vid, pid), entry = _first_device(SCSI_DEVICES)
        detected = _make_detected(vid, pid, entry)

        DeviceProtocolFactory._protocols.clear()
        protocol = DeviceProtocolFactory.create_protocol(detected)
        protocol.handshake()

        # 320x320 RGB565 = 204,800 bytes
        fake_frame = b'\x00' * (320 * 320 * 2)
        result = protocol.send_data(fake_frame, 320, 320)
        assert result is True


# ═════════════════════════════════════════════════════════════════════════════
# HID wiring — real HidProtocol → real HidDeviceType2/3 → noop transport
# ═════════════════════════════════════════════════════════════════════════════


class TestHidWiring:
    """HID handshake through real protocol code, noop transport."""

    def _inject_noop_hid(self, pm: int, sub: int = 0, device_type: int = 2):
        """Inject a noop USB transport that returns canned HID response."""
        if device_type == 3:
            resp = build_hid_type3_response(fbl=100)
        else:
            resp = build_hid_type2_response(pm, sub)

        def _noop_factory(vid, pid, **_ignore):
            return NoopUsbTransport(resp)
        DeviceProtocolFactory.create_usb_transport = staticmethod(_noop_factory)

    @pytest.mark.parametrize("vid_pid,entry", list(HID_LCD_DEVICES.items()),
                             ids=lambda vp: f"{vp[0]:04X}:{vp[1]:04X}"
                             if isinstance(vp, tuple) else str(vp))
    def test_all_hid_devices(self, vid_pid, entry):
        """Every registered HID device can handshake through real wiring."""
        vid, pid = vid_pid
        self._inject_noop_hid(pm=32, sub=0, device_type=entry.device_type)
        detected = _make_detected(vid, pid, entry, path=f"usb:{vid:04x}:{pid:04x}")

        DeviceProtocolFactory._protocols.clear()
        protocol = DeviceProtocolFactory.create_protocol(detected)
        result = protocol.handshake()

        assert result is not None
        assert result.resolution is not None


# ═════════════════════════════════════════════════════════════════════════════
# LED wiring — real LedProtocol → real LedHidSender → noop transport
# ═════════════════════════════════════════════════════════════════════════════


class TestLedWiring:
    """LED handshake through real protocol code, noop transport."""

    def _inject_noop_led(self, pm: int, sub: int = 0):
        """Inject a noop USB transport that returns canned LED response."""
        resp = build_led_response(pm, sub)

        def _noop_factory(vid, pid, **_ignore):
            return NoopUsbTransport(resp)
        DeviceProtocolFactory.create_usb_transport = staticmethod(_noop_factory)

    @pytest.mark.parametrize("vid_pid,entry", list(LED_DEVICES.items()),
                             ids=lambda vp: f"{vp[0]:04X}:{vp[1]:04X}"
                             if isinstance(vp, tuple) else str(vp))
    def test_all_led_devices(self, vid_pid, entry):
        """Every registered LED device can handshake through real wiring."""
        vid, pid = vid_pid
        self._inject_noop_led(pm=3)  # AX120 default PM
        detected = _make_detected(vid, pid, entry, path=f"usb:{vid:04x}:{pid:04x}")

        DeviceProtocolFactory._protocols.clear()
        protocol = DeviceProtocolFactory.create_protocol(detected)
        result = protocol.handshake()

        assert result is not None
        assert result.pm > 0 or result.model_id > 0


# ═════════════════════════════════════════════════════════════════════════════
# Builder wiring — full DI chain through ControllerBuilder
# ═════════════════════════════════════════════════════════════════════════════


class TestBuilderWiring:
    """ControllerBuilder → Platform → factory → protocol → device → transport."""

    @pytest.mark.parametrize("platform_cls", ALL_PLATFORMS,
                             ids=lambda c: c.__name__)
    def test_builder_injects_scsi_transport(self, platform_cls):
        """Builder.for_current_os() wires SCSI transport into factory."""
        builder = ControllerBuilder(platform_cls())
        # build_device_svc triggers set_scsi_transport
        builder._build_device_svc()
        assert DeviceProtocolFactory._scsi_transport_fn is not None
