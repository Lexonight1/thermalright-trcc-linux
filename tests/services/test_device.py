"""Tests for services/device.py — device detection, selection, and frame sending.

Covers:
- Construction and strict DI
- detect() — device enumeration, LED enrichment
- select() / selected property
- scan_and_select() — priority: explicit path > saved > first
- _discover_resolution() — handshake, no-op when already known
- send_rgb565() — thread-safe, busy guard
- send_frame() — encode cache by id()
- send_rgb565_async() — persistent send worker queue
- is_busy property
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.models import (
    ALL_DEVICES,
    FBL_PROFILES,
    HID_LCD_DEVICES,
    SCSI_DEVICES,
    DetectedDevice,
    DeviceInfo,
)
from trcc.legacy.services.device import DeviceService


def _make_detected(name: str = 'LCD', vid: int = 0x0402, pid: int = 0x3922,
                   protocol: str = 'scsi', impl: str = 'scsi',
                   scsi_device: str = '/dev/sg0',
                   usb_path: str = '1-001') -> DetectedDevice:
    return DetectedDevice(
        vendor_name='Thermalright',
        product_name=name,
        model=name,
        vid=vid,
        pid=pid,
        protocol=protocol,
        device_type='lcd',
        implementation=impl,
        scsi_device=scsi_device,
        usb_path=usb_path,
    )


def _make_service(devices: list[DetectedDevice] | None = None,
                  ) -> DeviceService:
    detect_fn = MagicMock(return_value=devices or [])
    probe_led_fn = MagicMock(return_value=None)
    get_protocol = MagicMock()
    return DeviceService(
        detect_fn=detect_fn,
        probe_led_fn=probe_led_fn,
        get_protocol=get_protocol,
    )


# =========================================================================
# Construction
# =========================================================================


class TestConstruction:
    def test_strict_di_requires_all_deps(self):
        with pytest.raises(RuntimeError, match="requires"):
            DeviceService()

    def test_initial_state(self):
        svc = _make_service()
        assert svc.selected is None
        assert svc.devices == []
        assert svc.is_busy is False


# =========================================================================
# Detection
# =========================================================================


class TestDetect:
    def test_detect_returns_device_infos(self):
        raw = [_make_detected('LCD1'), _make_detected('LCD2')]
        svc = _make_service(raw)
        devices = svc.detect()
        assert len(devices) == 2
        assert devices[0].name == 'Thermalright LCD1'
        assert devices[1].name == 'Thermalright LCD2'

    def test_device_index_assigned(self):
        raw = [_make_detected('A'), _make_detected('B')]
        svc = _make_service(raw)
        devices = svc.detect()
        assert devices[0].device_index == 0
        assert devices[1].device_index == 1

    def test_detect_empty(self):
        svc = _make_service([])
        assert svc.detect() == []

    def test_led_device_enrichment(self):
        # LED family is 0x0416:0x8001 in _VARIANT_REGISTRY; the enrichment
        # path resolves the per-PM button only when the VID/PID matches.
        raw = [_make_detected('LED', vid=0x0416, pid=0x8001, impl='hid_led')]
        svc = _make_service(raw)
        probe_result = MagicMock()
        probe_result.style.style_id = 5
        probe_result.style.model_name = 'LF8'
        probe_result.pm = 48
        probe_result.sub_type = 0
        probe_result.style_sub = 0
        svc._probe_led_fn.return_value = probe_result

        devices = svc.detect()
        assert devices[0].led_style_id == 5
        assert devices[0].model == 'LF8'
        assert devices[0].pm_byte == 48
        assert devices[0].sub_byte == 0
        assert devices[0].button_image == 'A1LF8'

    def test_led_enrichment_failure_silent(self):
        raw = [_make_detected('LED', vid=0x0416, pid=0x8001, impl='hid_led')]
        svc = _make_service(raw)
        svc._probe_led_fn.side_effect = RuntimeError("probe failed")
        devices = svc.detect()
        assert len(devices) == 1  # Still detected, just not enriched

    def test_detect_copies_button_image(self):
        raw = [_make_detected('LCD')]
        raw[0].button_image = 'A1GRAND VISION'
        svc = _make_service(raw)
        devices = svc.detect()
        assert devices[0].button_image == 'A1GRAND VISION'

    def test_detect_default_button_image(self):
        raw = [_make_detected('LCD')]
        svc = _make_service(raw)
        devices = svc.detect()
        assert devices[0].button_image == 'A1CZTV'

    def test_import_error_returns_empty(self):
        svc = _make_service()
        svc._detect_fn.side_effect = ImportError("no pyusb")
        assert svc.detect() == []


# =========================================================================
# Selection
# =========================================================================


class TestSelection:
    def test_select(self):
        svc = _make_service()
        dev = DeviceInfo(name='test', path='/dev/sg0', vid=1, pid=2,
                         device_index=0)
        svc.select(dev)
        assert svc.selected is dev

    def test_scan_and_select_explicit_path(self):
        raw = [
            _make_detected('A', scsi_device='/dev/sg0'),
            _make_detected('B', scsi_device='/dev/sg1'),
        ]
        svc = _make_service(raw)
        result = svc.scan_and_select(device_path='/dev/sg1')
        assert result is not None
        assert result.path == '/dev/sg1'

    def test_scan_and_select_explicit_path_not_found_falls_back(self):
        raw = [_make_detected('A', scsi_device='/dev/sg0')]
        svc = _make_service(raw)
        result = svc.scan_and_select(device_path='/dev/sg99')
        assert result is not None
        assert result.path == '/dev/sg0'  # Falls back to first

    @patch('trcc.legacy.conf.Settings')
    def test_scan_and_select_saved_preference(self, mock_settings):
        raw = [
            _make_detected('A', scsi_device='/dev/sg0'),
            _make_detected('B', scsi_device='/dev/sg1'),
        ]
        svc = _make_service(raw)
        mock_settings.get_selected_device.return_value = '/dev/sg1'
        result = svc.scan_and_select()
        assert result.path == '/dev/sg1'

    @patch('trcc.legacy.conf.Settings')
    def test_scan_and_select_no_saved_uses_first(self, mock_settings):
        raw = [_make_detected('A', scsi_device='/dev/sg0')]
        svc = _make_service(raw)
        mock_settings.get_selected_device.return_value = None
        result = svc.scan_and_select()
        assert result.path == '/dev/sg0'

    def test_scan_and_select_no_devices(self):
        svc = _make_service([])
        result = svc.scan_and_select()
        assert result is None


# =========================================================================
# Resolution discovery
# =========================================================================


class TestDiscoverResolution:
    def test_handshake_sets_resolution(self):
        fbl = 72
        expected_res = FBL_PROFILES[fbl].resolution   # (480, 480) per the profile
        raw = [_make_detected('LCD')]
        svc = _make_service(raw)
        protocol = MagicMock()
        handshake_result = MagicMock()
        handshake_result.resolution = expected_res
        handshake_result.fbl = fbl
        protocol.handshake.return_value = handshake_result
        svc._get_protocol.return_value = protocol

        svc.detect()
        dev = svc.devices[0]
        dev.resolution = (0, 0)
        svc._discover_resolution(dev)
        assert dev.resolution == expected_res
        assert dev.fbl_code == fbl

    def test_handshake_runs_even_when_resolution_known(self):
        """Handshake always runs — we need PM/SUB for button image even if resolution is known."""
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        dev.resolution = FBL_PROFILES[100].resolution
        protocol = MagicMock()
        protocol.handshake.return_value = MagicMock(
            resolution=(320, 320), model_id=100, pm_byte=32, sub_byte=1)
        svc._get_protocol.return_value = protocol
        svc._discover_resolution(dev)
        protocol.handshake.assert_called_once()
        assert dev.pm_byte == 32
        assert dev.sub_byte == 1

    def test_handshake_failure_silent(self):
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        dev.resolution = (0, 0)
        svc._get_protocol.side_effect = RuntimeError("no device")
        svc._discover_resolution(dev)  # Should not raise
        assert dev.resolution == (0, 0)


# =========================================================================
# Send
# =========================================================================


class TestSend:
    def test_send_rgb565_no_device(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        assert svc.send_rgb565(b'\x00' * 100, w, h) is False

    def test_send_rgb565_success(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)
        protocol = MagicMock()
        protocol.send_data.return_value = True
        svc._get_protocol.return_value = protocol

        assert svc.send_rgb565(b'\x00' * 100, w, h) is True
        protocol.send_data.assert_called_once()

    def test_send_rgb565_busy_skips(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)

        svc._send_busy = True
        assert svc.send_rgb565(b'\x00', w, h) is False

    def test_send_frame_cache_hit(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        dev.resolution = (w, h)
        svc.select(dev)
        protocol = MagicMock()
        protocol.send_data.return_value = True
        svc._get_protocol.return_value = protocol

        img = MagicMock()
        cached_data = b'\x00' * 50
        svc._last_encode_key = (id(img), 0)
        svc._last_encode_data = cached_data

        svc.send_frame(img, w, h)
        protocol.send_data.assert_called_once_with(cached_data, w, h)

    def test_send_frame_callback(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)
        protocol = MagicMock()
        protocol.send_data.return_value = True
        svc._get_protocol.return_value = protocol

        callback = MagicMock()
        svc.on_frame_sent = callback

        img = MagicMock()
        svc._last_encode_key = (id(img), 0)
        svc._last_encode_data = b'\x00'
        svc.send_frame(img, w, h)
        callback.assert_called_once_with(img)

    def test_send_error_returns_false(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)
        # Phase 9: send_rgb565 catches (OSError, ValueError) — use OSError.
        svc._get_protocol.side_effect = OSError("disconnected")

        assert svc.send_rgb565(b'\x00', w, h) is False
        assert svc.is_busy is False


# =========================================================================
# Async send
# =========================================================================


class TestAsyncSend:
    def test_send_rgb565_async_queues(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)
        svc._ensure_send_worker = MagicMock()
        svc.send_rgb565_async(b'\x00', w, h)
        assert len(svc._send_queue) == 1

    def test_queue_maxlen_1_latest_wins(self):
        w, h = FBL_PROFILES[100].resolution
        svc = _make_service()
        svc._ensure_send_worker = MagicMock()
        svc.send_rgb565_async(b'\x01', w, h)
        svc.send_rgb565_async(b'\x02', w, h)
        assert len(svc._send_queue) == 1
        assert svc._send_queue[0][0] == b'\x02'


# =========================================================================
# Protocol info
# =========================================================================


class TestProtocolInfo:
    def test_no_protocol_info_fn(self):
        svc = _make_service()
        svc._get_protocol_info = None
        assert svc.get_protocol_info() is None

    def test_protocol_info_exception(self):
        svc = _make_service()
        svc._get_protocol_info = MagicMock(side_effect=RuntimeError("err"))
        assert svc.get_protocol_info() is None

    def test_protocol_info_success(self):
        svc = _make_service()
        svc._get_protocol_info = MagicMock(return_value="scsi_v2")
        dev = DeviceInfo(name='t', path='p', vid=1, pid=2, device_index=0)
        svc.select(dev)
        assert svc.get_protocol_info() == "scsi_v2"


# =========================================================================
# Model-driven: every device in ALL_DEVICES is detectable
# =========================================================================


@pytest.mark.parametrize("vid_pid,entry", list(ALL_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in ALL_DEVICES])
def test_every_known_device_in_registry(vid_pid, entry):
    """Every VID/PID in ALL_DEVICES has an implementation name — no orphan entries."""
    assert entry.implementation, \
        f"{vid_pid}: DeviceEntry has no implementation name"


@pytest.mark.parametrize("vid_pid,entry", list(SCSI_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in SCSI_DEVICES])
def test_scsi_devices_have_scsi_protocol(vid_pid, entry):
    """Every entry in SCSI_DEVICES uses scsi protocol (default on DeviceEntry)."""
    assert entry.protocol == 'scsi', \
        f"{vid_pid}: SCSI device has unexpected protocol '{entry.protocol}'"


@pytest.mark.parametrize("vid_pid,entry", list(HID_LCD_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in HID_LCD_DEVICES])
def test_hid_devices_have_hid_protocol(vid_pid, entry):
    """Every entry in HID_LCD_DEVICES uses hid protocol."""
    assert entry.protocol == 'hid', \
        f"{vid_pid}: HID device has unexpected protocol '{entry.protocol}'"


@pytest.mark.parametrize("fbl,profile", list(FBL_PROFILES.items()),
                         ids=[str(f) for f in FBL_PROFILES])
def test_every_fbl_profile_has_positive_resolution(fbl, profile):
    """Every FBL profile declares a positive width and height."""
    assert profile.width > 0, f"FBL {fbl}: width={profile.width}"
    assert profile.height > 0, f"FBL {fbl}: height={profile.height}"


@pytest.mark.parametrize("fbl,profile", list(FBL_PROFILES.items()),
                         ids=[str(f) for f in FBL_PROFILES])
def test_jpeg_and_big_endian_are_mutually_exclusive(fbl, profile):
    """No profile is both JPEG and big-endian — JPEG encoding ignores byte order."""
    assert not (profile.jpeg and profile.big_endian), \
        f"FBL {fbl}: jpeg=True and big_endian=True simultaneously"
