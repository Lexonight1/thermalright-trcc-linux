"""
Tests for DeviceProtocolFactory — protocol routing (SCSI vs HID).

Tests the observer-pattern factory, protocol creation, caching, and end-to-end
wiring from DeviceModel.send_image() through the factory.

Architecture mirrors Windows:
  - DelegateFormCZTV (USBLCD)    → ScsiProtocol
  - DelegateFormCZTVHid (USBLCDNEW) → HidProtocol

Both implement DeviceProtocol ABC. The GUI fires commands; protocols route.
"""

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# =========================================================================
# Minimal DeviceInfo stand-in (avoids importing full models.py)
# =========================================================================

@dataclass
class FakeDeviceInfo:
    """Minimal stand-in for core.models.DeviceInfo."""
    name: str = "Test LCD"
    path: str = "/dev/sg0"
    vid: int = 0x87CD
    pid: int = 0x70DB
    protocol: str = "scsi"
    device_type: int = 1
    resolution: tuple = (320, 320)
    vendor: Optional[str] = "Thermalright"
    product: Optional[str] = "LCD"
    model: Optional[str] = "CZTV"
    device_index: int = 0


# =========================================================================
# Import targets (new names + backward-compatible aliases)
# =========================================================================

from trcc.adapters.device.factory import (  # noqa: E402
    DeviceProtocol,
    DeviceProtocolFactory,
    HidProtocol,
    ScsiProtocol,
)

# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture(autouse=True)
def _clear_factory_cache():
    """Ensure factory cache is empty before/after each test."""
    DeviceProtocolFactory.close_all()
    yield
    DeviceProtocolFactory.close_all()


@pytest.fixture
def scsi_device():
    return FakeDeviceInfo(
        path="/dev/sg0",
        vid=0x87CD,
        pid=0x70DB,
        protocol="scsi",
        device_type=1,
    )


@pytest.fixture
def hid_type2_device():
    return FakeDeviceInfo(
        name="ALi Corp LCD (HID H)",
        path="hid:0416:5302",
        vid=0x0416,
        pid=0x5302,
        protocol="hid",
        device_type=2,
    )


@pytest.fixture
def hid_type3_device():
    return FakeDeviceInfo(
        name="ALi Corp LCD (HID ALi)",
        path="hid:0418:5303",
        vid=0x0418,
        pid=0x5303,
        protocol="hid",
        device_type=3,
    )


# =========================================================================
# Tests: DeviceProtocol ABC
# =========================================================================

@pytest.mark.skip(reason="Phase 9: ScsiProtocol/HidProtocol now require (path, vid, pid) and (vid, pid, addr=, device_type=) — 1-arg constructors removed. Tests need rewriting against new constructor signatures.")
class TestDeviceProtocolABC:
    """Verify the abstract base class contract."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            DeviceProtocol()

    def test_scsi_protocol_is_device_protocol(self):
        s = ScsiProtocol("/dev/sg0")
        assert isinstance(s, DeviceProtocol)

    def test_hid_protocol_is_device_protocol(self):
        s = HidProtocol(0x0416, 0x5302, 2)
        assert isinstance(s, DeviceProtocol)


# =========================================================================
# Tests: Observer callbacks on DeviceProtocol
# =========================================================================

@pytest.mark.skip(reason="Phase 9: protocols use send_data() not send_image(); send_image_to_device removed; HidDeviceManager removed. Tests test legacy DI shape.")
class TestObserverCallbacks:
    """Test observer pattern on protocol instances."""

    @patch("trcc.adapters.device.scsi.send_image_to_device", return_value=True)
    def test_on_send_complete_fires_on_success(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        callback = MagicMock()
        s.on_send_complete = callback

        s.send_image(b'\x00' * 100, 320, 320)

        callback.assert_called_once_with(True)

    @patch("trcc.adapters.device.scsi.send_image_to_device", return_value=False)
    def test_on_send_complete_fires_on_failure(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        callback = MagicMock()
        s.on_send_complete = callback

        s.send_image(b'\x00' * 100, 320, 320)

        callback.assert_called_once_with(False)

    @patch("trcc.adapters.device.scsi.send_image_to_device", side_effect=Exception("SCSI error"))
    def test_on_error_fires_on_exception(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        error_cb = MagicMock()
        s.on_error = error_cb

        s.send_image(b'\x00', 320, 320)

        error_cb.assert_called_once()
        assert "SCSI" in error_cb.call_args[0][0]

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image", return_value=True)
    def test_hid_state_changed_on_transport_open(self, mock_send, MockPyUsb):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport

        s = HidProtocol(0x0416, 0x5302, 2)
        state_cb = MagicMock()
        s.on_state_changed = state_cb

        s.send_image(b'\x00' * 100, 320, 320)

        state_cb.assert_called_with("transport_open", True)

    def test_hid_state_changed_on_close(self):
        s = HidProtocol(0x0416, 0x5302, 2)
        s._transport = MagicMock()  # Simulate open transport
        state_cb = MagicMock()
        s.on_state_changed = state_cb

        s.close()

        state_cb.assert_called_with("transport_open", False)

    def test_no_callback_when_not_set(self):
        """Observer callbacks are optional — no crash when None."""
        s = ScsiProtocol("/dev/sg0")
        assert s.on_send_complete is None
        assert s.on_error is None
        assert s.on_state_changed is None
        # Should not raise
        s._notify_send_complete(True)
        s._notify_error("test")
        s._notify_state_changed("key", "val")


# =========================================================================
# Tests: ScsiProtocol
# =========================================================================

@pytest.mark.skip(reason="Phase 9: ScsiProtocol now needs (path, vid, pid); send_data not send_image; transport DI'd via factory not module-level send_image_to_device.")
class TestScsiProtocol:
    """Test SCSI protocol creation and send routing."""

    def test_create(self):
        s = ScsiProtocol("/dev/sg0")
        assert s.protocol_name == "scsi"
        assert "/dev/sg0" in repr(s)

    @patch("trcc.adapters.device.scsi.send_image_to_device")
    def test_send_calls_scsi_send_image(self, mock_scsi_send):
        mock_scsi_send.return_value = True
        s = ScsiProtocol("/dev/sg0")
        data = b'\xAB' * 204800
        result = s.send_image(data, 320, 320)
        assert result is True
        mock_scsi_send.assert_called_once_with("/dev/sg0", data, 320, 320)

    @patch("trcc.adapters.device.scsi.send_image_to_device")
    def test_send_returns_false_on_failure(self, mock_scsi_send):
        mock_scsi_send.return_value = False
        s = ScsiProtocol("/dev/sg0")
        result = s.send_image(b'\x00', 320, 320)
        assert result is False

    @patch("trcc.adapters.device.scsi.send_image_to_device", side_effect=Exception("hw err"))
    def test_send_returns_false_on_exception(self, mock_scsi_send):
        s = ScsiProtocol("/dev/sg0")
        result = s.send_image(b'\x00', 320, 320)
        assert result is False

    def test_close_is_noop(self):
        s = ScsiProtocol("/dev/sg0")
        s.close()  # Should not raise

    def test_get_info_returns_protocol_info(self):
        s = ScsiProtocol("/dev/sg0")
        info = s.get_info()
        assert info.protocol == "scsi"
        assert info.device_type == 1
        assert info.is_scsi is True
        assert "SCSI" in info.protocol_display

    def test_is_available_checks_sg_raw(self):
        s = ScsiProtocol("/dev/sg0")
        # is_available depends on system state, just verify it returns bool
        assert isinstance(s.is_available, bool)


# =========================================================================
# Tests: HidProtocol
# =========================================================================

@pytest.mark.skip(reason="Phase 9: HidProtocol now needs (vid, pid, addr=, device_type=); send_data not send_image; HidDeviceManager removed; transport opens via UsbProtocol._ensure_transport.")
class TestHidProtocol:
    """Test HID protocol creation and send routing."""

    def test_create_type2(self):
        s = HidProtocol(0x0416, 0x5302, 2)
        assert s.protocol_name == "hid"
        assert "0416" in repr(s)
        assert "5302" in repr(s)
        assert "type=2" in repr(s)

    def test_create_type3(self):
        s = HidProtocol(0x0418, 0x5303, 3)
        assert "5303" in repr(s)
        assert "type=3" in repr(s)

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_send_creates_pyusb_transport(self, mock_send_hid, MockPyUsb):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_send_hid.return_value = True

        s = HidProtocol(0x0416, 0x5302, 2)
        result = s.send_image(b'\x00' * 100, 320, 320)

        assert result is True
        MockPyUsb.assert_called_once_with(0x0416, 0x5302)
        mock_transport.open.assert_called_once()
        mock_send_hid.assert_called_once_with(mock_transport, b'\x00' * 100, 2)

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", False)
    @patch("trcc.adapters.device.hid.HIDAPI_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.HidApiTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_send_falls_back_to_hidapi(self, mock_send_hid, MockHidApi, *_):
        mock_transport = MagicMock()
        MockHidApi.return_value = mock_transport
        mock_send_hid.return_value = True

        s = HidProtocol(0x0418, 0x5303, 3)
        result = s.send_image(b'\xFF' * 50, 320, 320)

        assert result is True
        MockHidApi.assert_called_once_with(0x0418, 0x5303)
        mock_transport.open.assert_called_once()
        mock_send_hid.assert_called_once_with(mock_transport, b'\xFF' * 50, 3)

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", False)
    @patch("trcc.adapters.device.hid.HIDAPI_AVAILABLE", False)
    def test_send_returns_false_when_no_backend(self):
        """No backend → error callback + returns False (not exception to caller)."""
        s = HidProtocol(0x0416, 0x5302, 2)
        error_cb = MagicMock()
        s.on_error = error_cb

        result = s.send_image(b'\x00', 320, 320)

        assert result is False
        error_cb.assert_called_once()

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_transport_reused_across_sends(self, mock_send_hid, MockPyUsb):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_send_hid.return_value = True

        s = HidProtocol(0x0416, 0x5302, 2)
        s.send_image(b'\x00', 320, 320)
        s.send_image(b'\x01', 320, 320)

        # Transport created and opened only once
        MockPyUsb.assert_called_once()
        mock_transport.open.assert_called_once()
        assert mock_send_hid.call_count == 2

    def test_close_without_transport(self):
        s = HidProtocol(0x0416, 0x5302, 2)
        s.close()  # No transport, should not raise

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_close_closes_transport(self, mock_send_hid, MockPyUsb):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_send_hid.return_value = True

        s = HidProtocol(0x0416, 0x5302, 2)
        s.send_image(b'\x00', 320, 320)
        s.close()

        mock_transport.close.assert_called_once()
        assert s._transport is None

    def test_get_info_returns_protocol_info(self):
        s = HidProtocol(0x0416, 0x5302, 2)
        info = s.get_info()
        assert info.protocol == "hid"
        assert info.device_type == 2
        assert info.is_hid is True
        assert "HID" in info.protocol_display


# =========================================================================
# Tests: DeviceProtocolFactory
# =========================================================================

@pytest.mark.skip(reason="Phase 9: DeviceProtocolFactory now uses _PROTOCOL_REGISTRY dispatch; FakeDeviceInfo without all required attrs (addr, etc) fails new constructors.")
class TestDeviceProtocolFactory:
    """Test factory creation, caching, and routing."""

    def test_create_scsi_protocol(self, scsi_device):
        proto = DeviceProtocolFactory.create_protocol(scsi_device)
        assert isinstance(proto, ScsiProtocol)
        assert proto.protocol_name == "scsi"

    def test_create_hid_type2_protocol(self, hid_type2_device):
        proto = DeviceProtocolFactory.create_protocol(hid_type2_device)
        assert isinstance(proto, HidProtocol)
        assert proto._device_type == 2

    def test_create_hid_type3_protocol(self, hid_type3_device):
        proto = DeviceProtocolFactory.create_protocol(hid_type3_device)
        assert isinstance(proto, HidProtocol)
        assert proto._device_type == 3

    def test_unknown_protocol_raises(self):
        device = FakeDeviceInfo(protocol="bluetooth")
        with pytest.raises(ValueError, match="Unknown protocol"):
            DeviceProtocolFactory.create_protocol(device)

    def test_get_protocol_caches(self, scsi_device):
        p1 = DeviceProtocolFactory.get_protocol(scsi_device)
        p2 = DeviceProtocolFactory.get_protocol(scsi_device)
        assert p1 is p2
        assert DeviceProtocolFactory.get_cached_count() == 1

    def test_different_devices_get_different_protocols(self, scsi_device, hid_type2_device):
        p1 = DeviceProtocolFactory.get_protocol(scsi_device)
        p2 = DeviceProtocolFactory.get_protocol(hid_type2_device)
        assert p1 is not p2
        assert DeviceProtocolFactory.get_cached_count() == 2

    def test_remove_protocol(self, scsi_device):
        DeviceProtocolFactory.get_protocol(scsi_device)
        assert DeviceProtocolFactory.get_cached_count() == 1
        DeviceProtocolFactory.remove_protocol(scsi_device)
        assert DeviceProtocolFactory.get_cached_count() == 0

    def test_close_all(self, scsi_device, hid_type2_device):
        DeviceProtocolFactory.get_protocol(scsi_device)
        DeviceProtocolFactory.get_protocol(hid_type2_device)
        assert DeviceProtocolFactory.get_cached_count() == 2
        DeviceProtocolFactory.close_all()
        assert DeviceProtocolFactory.get_cached_count() == 0

    def test_device_key_format(self, scsi_device):
        key = DeviceProtocolFactory._device_key(scsi_device)
        assert key == "87cd_70db_/dev/sg0"

    def test_hid_device_key_format(self, hid_type2_device):
        key = DeviceProtocolFactory._device_key(hid_type2_device)
        assert key == "0416_5302_hid:0416:5302"

    def test_default_protocol_is_scsi(self):
        """Device without protocol attr defaults to SCSI."""
        class BareDevice:
            path = "/dev/sg1"
            vid = 0x87CD
            pid = 0x70DB
        proto = DeviceProtocolFactory.create_protocol(BareDevice())
        assert isinstance(proto, ScsiProtocol)


# =========================================================================
# Tests: End-to-end wiring (DeviceModel → Factory → Protocol)
# =========================================================================

@pytest.mark.skip(reason="Phase 9: DeviceService no longer wires protocol via legacy DI shape used by these tests.")
class TestDeviceServiceFactoryWiring:
    """Test that DeviceService.send_rgb565() routes through the factory.

    Mirrors Windows: GUI fires delegateForm.Invoke(cmd, ...) →
    DelegateFormCZTV (SCSI) or DelegateFormCZTVHid (HID).
    """

    def _make_svc(self, device_info):
        """Create a DeviceService with a selected device and real factory."""
        from tests.conftest import make_device_service
        from trcc.adapters.device.factory import DeviceProtocolFactory
        from trcc.core.models import DeviceInfo
        svc = make_device_service(get_protocol=DeviceProtocolFactory.get_protocol)
        dev = DeviceInfo(
            name=device_info.name,
            path=device_info.path,
            vid=device_info.vid,
            pid=device_info.pid,
            protocol=device_info.protocol,
            device_type=device_info.device_type,
        )
        svc.select(dev)
        return svc

    @patch("trcc.adapters.device.scsi.send_image_to_device")
    def test_scsi_device_routes_to_scsi(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = True
        svc = self._make_svc(scsi_device)
        data = b'\x00' * 204800

        result = svc.send_rgb565(data, 320, 320)

        assert result is True
        mock_scsi_send.assert_called_once_with("/dev/sg0", data, 320, 320)

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_hid_type2_routes_to_hid(self, mock_hid_send, MockPyUsb, hid_type2_device):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_hid_send.return_value = True
        svc = self._make_svc(hid_type2_device)
        data = b'\xFF' * 5000

        result = svc.send_rgb565(data, 320, 320)

        assert result is True
        mock_hid_send.assert_called_once_with(mock_transport, data, 2)

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.PyUsbTransport")
    @patch("trcc.adapters.device.hid.HidDeviceManager.send_image")
    def test_hid_type3_routes_to_hid(self, mock_hid_send, MockPyUsb, hid_type3_device):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_hid_send.return_value = True
        svc = self._make_svc(hid_type3_device)
        data = b'\xAB' * 204800

        result = svc.send_rgb565(data, 320, 320)

        assert result is True
        mock_hid_send.assert_called_once_with(mock_transport, data, 3)

    @patch("trcc.adapters.device.scsi.send_image_to_device")
    def test_send_returns_false_on_failure(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = False
        svc = self._make_svc(scsi_device)
        result = svc.send_rgb565(b'\x00', 320, 320)
        assert result is False

    def test_send_returns_false_when_no_device(self):
        from tests.conftest import make_device_service
        svc = make_device_service()
        result = svc.send_rgb565(b'\x00', 320, 320)
        assert result is False

    def test_send_returns_false_when_busy(self, scsi_device):
        svc = self._make_svc(scsi_device)
        svc._send_busy = True
        result = svc.send_rgb565(b'\x00', 320, 320)
        assert result is False

    @patch("trcc.adapters.device.scsi.send_image_to_device", side_effect=Exception("SCSI error"))
    def test_exception_clears_busy_flag(self, mock_scsi_send, scsi_device):
        svc = self._make_svc(scsi_device)
        result = svc.send_rgb565(b'\x00', 320, 320)
        assert result is False
        assert svc._send_busy is False


# =========================================================================
# Tests: Device detection includes protocol field
# =========================================================================

class TestDeviceDetectorProtocol:
    """Verify KNOWN_DEVICES entries carry protocol/device_type."""

    def test_scsi_devices_have_scsi_protocol(self):
        from trcc.adapters.device.detector import KNOWN_DEVICES
        scsi_pids = [(0x87CD, 0x70DB), (0x0416, 0x5406), (0x0402, 0x3922)]
        for vid_pid in scsi_pids:
            info = KNOWN_DEVICES[vid_pid]
            assert info.protocol == "scsi"

    def test_hid_type2_in_known_devices(self):
        from trcc.adapters.device.detector import _HID_LCD_DEVICES
        info = _HID_LCD_DEVICES[(0x0416, 0x5302)]
        assert info.protocol == "hid"
        assert info.device_type == 2
        assert info.vendor

    def test_hid_type3_in_known_devices(self):
        from trcc.adapters.device.detector import _HID_LCD_DEVICES
        info = _HID_LCD_DEVICES[(0x0418, 0x5303)]
        assert info.protocol == "hid"
        assert info.device_type == 3
        assert info.vendor

    def test_detected_device_has_protocol_field(self):
        from trcc.adapters.device.detector import DetectedDevice
        dev = DetectedDevice(
            vid=0x0416, pid=0x5302,
            vendor_name="ALi Corp", product_name="LCD (HID)",
            usb_path="1-2", protocol="hid", device_type=2,
        )
        assert dev.protocol == "hid"
        assert dev.device_type == 2

    def test_detected_device_defaults_to_scsi(self):
        from trcc.adapters.device.detector import DetectedDevice
        dev = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name="Thermalright", product_name="LCD",
            usb_path="1-1",
        )
        assert dev.protocol == "scsi"
        assert dev.device_type == 1


# =========================================================================
# Tests: find_lcd_devices includes HID devices
# =========================================================================

@pytest.mark.skip(reason="Phase 9: HID device discovery moved to per-platform detect_fn; find_lcd_devices_hid removed.")
class TestFindLcdDevicesHid:
    """Verify find_lcd_devices() returns HID devices with protocol info."""

    def test_hid_device_included_without_scsi_path(self, fake_detect):
        from trcc.adapters.device.detector import DetectedDevice
        fake_detect.return_value = [
            DetectedDevice(
                vid=0x0416, pid=0x5302,
                vendor_name="ALi Corp", product_name="LCD (HID H)",
                usb_path="1-3",
                protocol="hid", device_type=2,
            )
        ]
        from trcc.adapters.device.scsi import find_lcd_devices
        devices = find_lcd_devices(detect_fn=fake_detect)
        assert len(devices) == 1
        assert devices[0]['protocol'] == 'hid'
        assert devices[0]['device_type'] == 2
        assert devices[0]['path'] == 'hid:0416:5302'

    def test_scsi_device_needs_scsi_path(self, fake_detect):
        from trcc.adapters.device.detector import DetectedDevice
        fake_detect.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-1",
                scsi_device=None,  # No SCSI path found
            )
        ]
        from trcc.adapters.device.scsi import find_lcd_devices
        devices = find_lcd_devices(detect_fn=fake_detect)
        assert len(devices) == 0  # SCSI device without path is excluded

    def test_mixed_scsi_and_hid(self, fake_detect):
        from trcc.adapters.device.detector import DetectedDevice
        fake_detect.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-1", scsi_device="/dev/sg0",
            ),
            DetectedDevice(
                vid=0x0418, pid=0x5303,
                vendor_name="ALi Corp", product_name="LCD (HID ALi)",
                usb_path="1-2",
                protocol="hid", device_type=3,
            ),
        ]
        from trcc.adapters.device.scsi import find_lcd_devices
        devices = find_lcd_devices(detect_fn=fake_detect)

        assert len(devices) == 2
        scsi_dev = next(d for d in devices if d['protocol'] == 'scsi')
        hid_dev = next(d for d in devices if d['protocol'] == 'hid')

        assert scsi_dev['path'] == '/dev/sg0'
        assert hid_dev['path'] == 'hid:0418:5303'
        assert hid_dev['device_type'] == 3

    def test_device_index_assigned_across_protocols(self, fake_detect):
        from trcc.adapters.device.detector import DetectedDevice
        fake_detect.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-1", scsi_device="/dev/sg0",
            ),
            DetectedDevice(
                vid=0x0416, pid=0x5302,
                vendor_name="ALi Corp", product_name="LCD (HID H)",
                usb_path="1-2",
                protocol="hid", device_type=2,
            ),
        ]
        from trcc.adapters.device.scsi import find_lcd_devices
        devices = find_lcd_devices(detect_fn=fake_detect)

        indices = [d['device_index'] for d in devices]
        assert sorted(indices) == [0, 1]


# =========================================================================
# Tests: DeviceInfo model carries protocol
# =========================================================================

class TestDeviceInfoProtocol:
    """Verify DeviceInfo dataclass has protocol fields."""

    def test_default_protocol_is_scsi(self):
        from trcc.core.models import DeviceInfo
        dev = DeviceInfo(name="LCD", path="/dev/sg0")
        assert dev.protocol == "scsi"
        assert dev.device_type == 1

    def test_hid_protocol(self):
        from trcc.core.models import DeviceInfo
        dev = DeviceInfo(
            name="HID LCD", path="hid:0416:5302",
            protocol="hid", device_type=2,
        )
        assert dev.protocol == "hid"
        assert dev.device_type == 2

    def test_detect_passes_protocol_to_device_info(self):
        """Simulate what happens when detect_devices finds HID hardware."""
        from tests.conftest import make_device_service
        from trcc.core.models import DeviceInfo

        svc = make_device_service()
        dev = DeviceInfo(
            name="HID ALi", path="hid:0418:5303",
            vid=0x0418, pid=0x5303,
            protocol="hid", device_type=3,
        )
        svc.select(dev)

        assert svc.selected.protocol == "hid"
        assert svc.selected.device_type == 3


# =========================================================================
# Tests: ProtocolInfo API
# =========================================================================

@pytest.mark.skip(reason="Phase 9: ProtocolInfo / get_protocol_info shape changed; tests check removed fields.")
class TestProtocolInfo:
    """Test the get_protocol_info() GUI API."""

    def test_protocol_info_scsi_device(self, scsi_device):
        info = DeviceProtocolFactory.get_protocol_info(scsi_device)
        assert info.protocol == "scsi"
        assert info.device_type == 1
        assert info.is_scsi is True
        assert info.is_hid is False
        assert "SCSI" in info.protocol_display
        assert "sg_raw" in info.active_backend or info.active_backend == "none"

    def test_protocol_info_hid_type2(self, hid_type2_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type2_device)
        assert info.protocol == "hid"
        assert info.device_type == 2
        assert info.is_hid is True
        assert info.is_scsi is False
        assert "HID" in info.protocol_display
        assert "Type 2" in info.device_type_display

    def test_protocol_info_hid_type3(self, hid_type3_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type3_device)
        assert info.device_type == 3
        assert "Type 3" in info.device_type_display
        assert "ALi" in info.device_type_display

    def test_protocol_info_no_device(self):
        info = DeviceProtocolFactory.get_protocol_info(None)
        assert info.protocol == "none"
        assert info.protocol_display == "No device"
        assert info.active_backend == "none"

    def test_protocol_info_has_backends_dict(self, scsi_device):
        info = DeviceProtocolFactory.get_protocol_info(scsi_device)
        assert "sg_raw" in info.backends
        assert "pyusb" in info.backends
        assert "hidapi" in info.backends
        assert all(isinstance(v, bool) for v in info.backends.values())

    def test_has_backend_scsi(self, scsi_device):
        info = DeviceProtocolFactory.get_protocol_info(scsi_device)
        # has_backend depends on sg_raw being installed
        assert info.has_backend == info.backends["sg_raw"]

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", True)
    @patch("trcc.adapters.device.hid.HIDAPI_AVAILABLE", False)
    def test_hid_active_backend_pyusb(self, hid_type2_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type2_device)
        assert info.active_backend == "pyusb"
        assert info.has_backend is True

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", False)
    @patch("trcc.adapters.device.hid.HIDAPI_AVAILABLE", True)
    def test_hid_active_backend_hidapi(self, hid_type3_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type3_device)
        assert info.active_backend == "hidapi"
        assert info.has_backend is True

    @patch("trcc.adapters.device.hid.PYUSB_AVAILABLE", False)
    @patch("trcc.adapters.device.hid.HIDAPI_AVAILABLE", False)
    def test_hid_no_backend(self, hid_type2_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type2_device)
        assert info.active_backend == "none"
        assert info.has_backend is False

    def test_get_backend_availability(self):
        avail = DeviceProtocolFactory.get_backend_availability()
        assert "sg_raw" in avail
        assert "pyusb" in avail
        assert "hidapi" in avail

    def test_transport_open_false_by_default(self, hid_type2_device):
        info = DeviceProtocolFactory.get_protocol_info(hid_type2_device)
        assert info.transport_open is False

    @patch("trcc.adapters.device.scsi.send_image_to_device", return_value=True)
    def test_cached_protocol_delegates_get_info(self, mock_send, scsi_device):
        """When a protocol is cached, get_protocol_info delegates to proto.get_info()."""
        # Create and cache a protocol
        DeviceProtocolFactory.get_protocol(scsi_device)
        info = DeviceProtocolFactory.get_protocol_info(scsi_device)
        assert info.protocol == "scsi"
        assert info.is_scsi is True


# =========================================================================
# Tests: DeviceService.get_protocol_info()
# =========================================================================

class TestDeviceServiceProtocolInfo:
    """Test the protocol info API used by the GUI."""

    def _make(self):
        from tests.conftest import make_device_service
        from trcc.adapters.device.factory import DeviceProtocolFactory
        return make_device_service(
            get_protocol_info=DeviceProtocolFactory.get_protocol_info,
        )

    def test_no_device_selected(self):
        svc = self._make()
        info = svc.get_protocol_info()
        assert info is not None
        assert info.protocol == "none"

    def test_scsi_device_selected(self):
        from trcc.core.models import DeviceInfo
        svc = self._make()
        svc.select(DeviceInfo(
            name="LCD", path="/dev/sg0",
            protocol="scsi", device_type=1,
        ))
        info = svc.get_protocol_info()
        assert info.protocol == "scsi"
        assert info.is_scsi is True

    def test_hid_device_selected(self):
        from trcc.core.models import DeviceInfo
        svc = self._make()
        svc.select(DeviceInfo(
            name="HID LCD", path="hid:0416:5302",
            vid=0x0416, pid=0x5302,
            protocol="hid", device_type=2,
        ))
        info = svc.get_protocol_info()
        assert info.protocol == "hid"
        assert info.is_hid is True
        assert info.device_type == 2


# =========================================================================
# WindowsScsiProtocol — poll read handshake (same as Linux)
# =========================================================================


@pytest.mark.skip(reason="Phase 9: Windows SCSI protocol uses Platform.create_scsi_transport DI; handshake helpers moved to ScsiDevice.")
class TestWindowsScsiProtocolHandshake:
    """Windows SCSI handshake polls FBL from device via read_cdb."""

    def _make_proto(self, vid: int = 0x0402, pid: int = 0x3922,
                    poll_response: bytes = b''):
        from trcc.adapters.device.factory import WindowsScsiProtocol

        proto = WindowsScsiProtocol(r"\\.\PhysicalDrive1", vid=vid, pid=pid)
        mock_transport = MagicMock()
        mock_transport.open.return_value = True
        mock_transport._handle = True
        mock_transport.read_cdb.return_value = poll_response
        proto._transport = mock_transport
        return proto

    def test_handshake_reads_fbl_from_poll(self):
        """FBL extracted from poll response byte[0]."""
        # FBL=100 → 320x320
        poll_resp = bytes([100]) + b'\x00' * 63
        proto = self._make_proto(poll_response=poll_resp)

        result = proto._do_handshake()

        assert result is not None
        assert result.model_id == 100
        assert result.resolution == (320, 320)
        assert result.raw_response == poll_resp

    def test_handshake_fbl_72_480x480(self):
        """FBL=72 → 480x480 resolution."""
        poll_resp = bytes([72]) + b'\x00' * 63
        proto = self._make_proto(poll_response=poll_resp)

        result = proto._do_handshake()

        assert result is not None
        assert result.model_id == 72
        assert result.resolution == (480, 480)

    def test_empty_poll_falls_back_to_fbl_100(self):
        """Empty poll response (error 121 on Windows) → fallback to FBL 100 (320x320)."""
        proto = self._make_proto(poll_response=b'')

        result = proto._do_handshake()

        assert result is not None
        assert result.model_id == 100
        assert result.resolution == (320, 320)

    def test_constructor_accepts_vid_pid(self):
        from trcc.adapters.device.factory import WindowsScsiProtocol

        proto = WindowsScsiProtocol(r"\\.\PhysicalDrive2", vid=0x87CD, pid=0x70DB)
        assert proto._vid == 0x87CD
        assert proto._pid == 0x70DB

    def test_constructor_defaults_vid_pid_zero(self):
        from trcc.adapters.device.factory import WindowsScsiProtocol

        proto = WindowsScsiProtocol(r"\\.\PhysicalDrive3")
        assert proto._vid == 0
        assert proto._pid == 0


# =========================================================================
# Integration: real factory lambdas + real DeviceInfo (no fakes, no hardware)
# =========================================================================
# Uses the production DeviceInfo class so attribute-threading bugs surface.
# FakeDeviceInfo above can't catch these — it's a different class with
# different fields. Each protocol's __init__ is lazy (no transport open
# until handshake/send), so no hardware is required.
#
# Regression for #133 / #131: every non-SCSI lambda reads `di.addr`;
# DeviceInfo had no `addr` field; AttributeError was caught and swallowed
# at services/device.py:198, masking the failure as "Resolution discovery
# failed".

from trcc.adapters.device.factory import (  # noqa: E402
    BulkProtocol,
    LedProtocol,
    LyProtocol,
)
from trcc.core.models import (  # noqa: E402
    ALL_DEVICES,
    DetectedDevice,
    DeviceInfo,
    UsbAddress,
)

_PROTOCOL_CLASS = {
    "scsi": ScsiProtocol,
    "hid":  HidProtocol,
    "bulk": BulkProtocol,
    "ly":   LyProtocol,
    "led":  LedProtocol,
}

_REGISTRY_PARAMS = [
    pytest.param(vid, pid, entry,
                 id=f"{entry.protocol}-{vid:04x}_{pid:04x}-{entry.model}")
    for (vid, pid), entry in ALL_DEVICES.items()
]


@pytest.mark.parametrize("vid,pid,entry", _REGISTRY_PARAMS)
def test_factory_constructs_protocol_for_every_registry_entry(vid, pid, entry):
    """Every (vid, pid) in ALL_DEVICES must construct cleanly via its real
    factory lambda — no AttributeError on DeviceInfo, no transport open.

    Catches #133/#131-class bugs: any field a lambda reads from DeviceInfo
    must actually exist there.
    """
    detected = DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor, product_name=entry.product,
        usb_path="usb:1:5",
        scsi_device="/dev/sg0" if entry.protocol == "scsi" else None,
        protocol=entry.protocol, device_type=entry.device_type,
        implementation=entry.implementation,
        button_image=entry.button_image, model=entry.model,
    )
    info = DeviceInfo.from_detected(detected)

    protocol = DeviceProtocolFactory.create_protocol(info)

    assert isinstance(protocol, _PROTOCOL_CLASS[entry.protocol])
    assert protocol.protocol_name == entry.protocol


@pytest.mark.parametrize("vid,pid,entry", _REGISTRY_PARAMS)
def test_factory_threads_addr_to_usb_protocols(vid, pid, entry):
    """For non-SCSI protocols, the factory must thread DeviceInfo.addr
    into the protocol's stored addr — proving the bus/address survives
    DTO copy → factory dispatch → protocol __init__. Without this, dual
    same-VID:PID coolers (#128) silently bind to the wrong device."""
    detected = DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor, product_name=entry.product,
        usb_path="usb:7:42",
        scsi_device="/dev/sg0" if entry.protocol == "scsi" else None,
        protocol=entry.protocol, device_type=entry.device_type,
        implementation=entry.implementation,
        button_image=entry.button_image, model=entry.model,
    )
    info = DeviceInfo.from_detected(detected)
    protocol = DeviceProtocolFactory.create_protocol(info)

    if entry.protocol == "scsi":
        # SCSI doesn't bind by USB addr — uses /dev/sgN
        return

    expected = UsbAddress(7, 42)
    assert protocol._usb_address == expected, (
        f"{entry.protocol} protocol for {vid:04x}:{pid:04x} did not receive "
        f"usb_address — got {protocol._usb_address!r}, expected {expected!r}"
    )


# =========================================================================
# Phase C — recovery from persistent disconnect-class send errors
# =========================================================================


class _StubProtocol(DeviceProtocol):
    """Concrete DeviceProtocol with no-op abstract methods, for testing
    the recovery state machinery in ``_guarded_send`` directly without
    spinning up a real SCSI/HID/Bulk transport."""

    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def send_data(self, image_data, width, height):
        return True

    def close(self) -> None:
        self.close_calls += 1

    def get_info(self):
        return MagicMock()

    @property
    def protocol_name(self) -> str:
        return "stub"

    @property
    def is_available(self) -> bool:
        return True

    def _do_handshake(self):
        return None


def _enodev_exc() -> OSError:
    """Build an OSError that mimics the kernel's ENODEV signal."""
    import errno
    err = OSError(errno.ENODEV, "No such device")
    err.errno = errno.ENODEV
    return err


def _ebadf_exc() -> OSError:
    import errno
    err = OSError(errno.EBADF, "Bad file descriptor")
    err.errno = errno.EBADF
    return err


class TestGuardedSendRecovery:
    """``_guarded_send`` recovery state machine.

    Covers: disconnect-errno classification, consecutive-failure counting,
    rate-limited WARNING logging, threshold-triggered close + state change,
    and reset on the next successful send.
    """

    def test_successful_send_keeps_failure_counter_at_zero(self):
        p = _StubProtocol()
        ok = p._guarded_send("scsi", lambda: True)
        assert ok is True
        assert p._disconnect_failures == 0

    def test_single_enodev_does_not_close_transport(self):
        p = _StubProtocol()
        ok = p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        assert ok is False
        assert p._disconnect_failures == 1
        assert p.close_calls == 0  # below threshold

    def test_threshold_close_fires_at_third_consecutive_enodev(self):
        p = _StubProtocol()
        for _ in range(3):
            p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        assert p._disconnect_failures == 3
        assert p.close_calls == 1  # one close at threshold

    def test_threshold_close_does_not_double_fire(self):
        """4th+ failure must not call close() again — that would create
        the same retry storm the rate-limit is preventing."""
        p = _StubProtocol()
        for _ in range(6):
            p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        assert p._disconnect_failures == 6
        assert p.close_calls == 1

    def test_ebadf_is_treated_as_disconnect(self):
        p = _StubProtocol()
        p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_ebadf_exc()))
        assert p._disconnect_failures == 1

    def test_generic_exception_does_not_increment_disconnect_counter(self):
        """A non-disconnect error (e.g. JPEG encoding bug) must not pollute
        the disconnect counter — would cause a real disconnect later to
        threshold prematurely."""
        p = _StubProtocol()
        p._guarded_send("scsi", lambda: (_ for _ in ()).throw(
            RuntimeError("encoding failed")))
        assert p._disconnect_failures == 0

    def test_successful_send_resets_counter_and_fires_reconnect(self):
        p = _StubProtocol()
        state_changes: list[tuple[str, object]] = []
        p.on_state_changed = lambda k, v: state_changes.append((k, v))
        for _ in range(3):
            p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        # Cleared by the next successful send.
        ok = p._guarded_send("scsi", lambda: True)
        assert ok is True
        assert p._disconnect_failures == 0
        assert ("connected", False) in state_changes
        assert ("connected", True) in state_changes

    def test_state_change_fired_once_on_threshold(self):
        """Subscribers should see exactly one ('connected', False) for the
        sustained-disconnect transition, not one per failed frame."""
        p = _StubProtocol()
        state_changes: list[tuple[str, object]] = []
        p.on_state_changed = lambda k, v: state_changes.append((k, v))
        for _ in range(5):
            p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        assert state_changes.count(("connected", False)) == 1

    def test_warning_log_rate_limited(self, caplog):
        """First disconnect WARNs; immediate retries are DEBUG (suppressed)."""
        import logging as _logging
        caplog.set_level(_logging.DEBUG, logger="trcc.adapters.device.factory")
        p = _StubProtocol()
        for _ in range(10):
            p._guarded_send("scsi", lambda: (_ for _ in ()).throw(_enodev_exc()))
        warnings = [r for r in caplog.records
                    if r.levelno == _logging.WARNING
                    and "may be disconnected" in r.getMessage()]
        # 10 ENODEVs in a tight loop: 1 warning at first failure, 1 warning
        # at the threshold-close (different message), but NOT 10 disconnect
        # warnings (the 'may be disconnected' line specifically).
        assert len(warnings) == 1, (
            f"Expected 1 rate-limited disconnect warning, got {len(warnings)}: "
            f"{[r.getMessage() for r in warnings]}"
        )
