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

from trcc.device_factory import (  # noqa: E402
    # New names
    DeviceProtocol,
    DeviceProtocolFactory,
    # Backward-compatible aliases
    DeviceSender,
    DeviceSenderFactory,
    HidProtocol,
    HidSender,
    ScsiProtocol,
    ScsiSender,
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

    def test_backward_compat_aliases(self):
        """Old class names still work."""
        assert DeviceSender is DeviceProtocol
        assert ScsiSender is ScsiProtocol
        assert HidSender is HidProtocol
        assert DeviceSenderFactory is DeviceProtocolFactory


# =========================================================================
# Tests: Observer callbacks on DeviceProtocol
# =========================================================================

class TestObserverCallbacks:
    """Test observer pattern on protocol instances."""

    @patch("trcc.scsi_device.send_image_to_device", return_value=True)
    def test_on_send_complete_fires_on_success(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        callback = MagicMock()
        s.on_send_complete = callback

        s.send_image(b'\x00' * 100, 320, 320)

        callback.assert_called_once_with(True)

    @patch("trcc.scsi_device.send_image_to_device", return_value=False)
    def test_on_send_complete_fires_on_failure(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        callback = MagicMock()
        s.on_send_complete = callback

        s.send_image(b'\x00' * 100, 320, 320)

        callback.assert_called_once_with(False)

    @patch("trcc.scsi_device.send_image_to_device", side_effect=Exception("SCSI error"))
    def test_on_error_fires_on_exception(self, mock_send):
        s = ScsiProtocol("/dev/sg0")
        error_cb = MagicMock()
        s.on_error = error_cb

        s.send_image(b'\x00', 320, 320)

        error_cb.assert_called_once()
        assert "SCSI" in error_cb.call_args[0][0]

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device", return_value=True)
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

class TestScsiProtocol:
    """Test SCSI protocol creation and send routing."""

    def test_create(self):
        s = ScsiProtocol("/dev/sg0")
        assert s.protocol_name == "scsi"
        assert "/dev/sg0" in repr(s)

    @patch("trcc.scsi_device.send_image_to_device")
    def test_send_calls_scsi_send_image(self, mock_scsi_send):
        mock_scsi_send.return_value = True
        s = ScsiProtocol("/dev/sg0")
        data = b'\xAB' * 204800
        result = s.send_image(data, 320, 320)
        assert result is True
        mock_scsi_send.assert_called_once_with("/dev/sg0", data, 320, 320)

    @patch("trcc.scsi_device.send_image_to_device")
    def test_send_returns_false_on_failure(self, mock_scsi_send):
        mock_scsi_send.return_value = False
        s = ScsiProtocol("/dev/sg0")
        result = s.send_image(b'\x00', 320, 320)
        assert result is False

    @patch("trcc.scsi_device.send_image_to_device", side_effect=Exception("hw err"))
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

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
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

    @patch("trcc.hid_device.PYUSB_AVAILABLE", False)
    @patch("trcc.hid_device.HIDAPI_AVAILABLE", True)
    @patch("trcc.hid_device.HidApiTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
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

    @patch("trcc.hid_device.PYUSB_AVAILABLE", False)
    @patch("trcc.hid_device.HIDAPI_AVAILABLE", False)
    def test_send_returns_false_when_no_backend(self):
        """No backend → error callback + returns False (not exception to caller)."""
        s = HidProtocol(0x0416, 0x5302, 2)
        error_cb = MagicMock()
        s.on_error = error_cb

        result = s.send_image(b'\x00', 320, 320)

        assert result is False
        error_cb.assert_called_once()

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
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

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
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

class TestDeviceModelFactoryWiring:
    """Test that DeviceModel.send_image() routes through the factory.

    Mirrors Windows: GUI fires delegateForm.Invoke(cmd, ...) →
    DelegateFormCZTV (SCSI) or DelegateFormCZTVHid (HID).
    """

    def _make_model(self, device_info):
        """Create a DeviceModel with a selected device."""
        from trcc.core.models import DeviceModel
        model = DeviceModel()
        # Bypass detect_devices — just set the device directly
        from trcc.core.models import DeviceInfo
        dev = DeviceInfo(
            name=device_info.name,
            path=device_info.path,
            vid=device_info.vid,
            pid=device_info.pid,
            protocol=device_info.protocol,
            device_type=device_info.device_type,
        )
        model.selected_device = dev
        return model

    @patch("trcc.scsi_device.send_image_to_device")
    def test_scsi_device_routes_to_scsi(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = True
        model = self._make_model(scsi_device)
        data = b'\x00' * 204800

        result = model.send_image(data, 320, 320)

        assert result is True
        mock_scsi_send.assert_called_once_with("/dev/sg0", data, 320, 320)

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
    def test_hid_type2_routes_to_hid(self, mock_hid_send, MockPyUsb, hid_type2_device):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_hid_send.return_value = True
        model = self._make_model(hid_type2_device)
        data = b'\xFF' * 5000

        result = model.send_image(data, 320, 320)

        assert result is True
        mock_hid_send.assert_called_once_with(mock_transport, data, 2)

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.PyUsbTransport")
    @patch("trcc.hid_device.send_image_to_hid_device")
    def test_hid_type3_routes_to_hid(self, mock_hid_send, MockPyUsb, hid_type3_device):
        mock_transport = MagicMock()
        MockPyUsb.return_value = mock_transport
        mock_hid_send.return_value = True
        model = self._make_model(hid_type3_device)
        data = b'\xAB' * 204800

        result = model.send_image(data, 320, 320)

        assert result is True
        mock_hid_send.assert_called_once_with(mock_transport, data, 3)

    @patch("trcc.scsi_device.send_image_to_device")
    def test_send_returns_false_on_failure(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = False
        model = self._make_model(scsi_device)
        result = model.send_image(b'\x00', 320, 320)
        assert result is False

    def test_send_returns_false_when_no_device(self):
        from trcc.core.models import DeviceModel
        model = DeviceModel()
        result = model.send_image(b'\x00', 320, 320)
        assert result is False

    def test_send_returns_false_when_busy(self, scsi_device):
        model = self._make_model(scsi_device)
        model._send_busy = True
        result = model.send_image(b'\x00', 320, 320)
        assert result is False

    @patch("trcc.scsi_device.send_image_to_device")
    def test_callback_on_success(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = True
        model = self._make_model(scsi_device)
        callback = MagicMock()
        model.on_send_complete = callback

        model.send_image(b'\x00' * 100, 320, 320)

        callback.assert_called_once_with(True)

    @patch("trcc.scsi_device.send_image_to_device")
    def test_callback_on_failure(self, mock_scsi_send, scsi_device):
        mock_scsi_send.return_value = False
        model = self._make_model(scsi_device)
        callback = MagicMock()
        model.on_send_complete = callback

        model.send_image(b'\x00', 320, 320)

        callback.assert_called_once_with(False)

    @patch("trcc.scsi_device.send_image_to_device", side_effect=Exception("SCSI error"))
    def test_exception_clears_busy_flag(self, mock_scsi_send, scsi_device):
        model = self._make_model(scsi_device)
        result = model.send_image(b'\x00', 320, 320)
        assert result is False
        assert model._send_busy is False


# =========================================================================
# Tests: Device detection includes protocol field
# =========================================================================

class TestDeviceDetectorProtocol:
    """Verify KNOWN_DEVICES entries carry protocol/device_type."""

    def test_scsi_devices_have_scsi_protocol(self):
        from trcc.device_detector import KNOWN_DEVICES
        scsi_pids = [(0x87CD, 0x70DB), (0x0416, 0x5406), (0x0402, 0x3922)]
        for vid_pid in scsi_pids:
            info = KNOWN_DEVICES[vid_pid]
            # SCSI devices default to "scsi" protocol (may not have explicit key)
            assert info.get("protocol", "scsi") == "scsi"

    def test_hid_type2_in_known_devices(self):
        from trcc.device_detector import _HID_LCD_DEVICES
        info = _HID_LCD_DEVICES[(0x0416, 0x5302)]
        assert info["protocol"] == "hid"
        assert info["device_type"] == 2
        assert "vendor" in info

    def test_hid_type3_in_known_devices(self):
        from trcc.device_detector import _HID_LCD_DEVICES
        info = _HID_LCD_DEVICES[(0x0418, 0x5303)]
        assert info["protocol"] == "hid"
        assert info["device_type"] == 3
        assert "vendor" in info

    def test_detected_device_has_protocol_field(self):
        from trcc.device_detector import DetectedDevice
        dev = DetectedDevice(
            vid=0x0416, pid=0x5302,
            vendor_name="ALi Corp", product_name="LCD (HID)",
            usb_path="1-2", protocol="hid", device_type=2,
        )
        assert dev.protocol == "hid"
        assert dev.device_type == 2

    def test_detected_device_defaults_to_scsi(self):
        from trcc.device_detector import DetectedDevice
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

class TestFindLcdDevicesHid:
    """Verify find_lcd_devices() returns HID devices with protocol info."""

    @patch("trcc.device_detector.detect_devices")
    def test_hid_device_included_without_scsi_path(self, mock_detect):
        from trcc.device_detector import DetectedDevice
        mock_detect.return_value = [
            DetectedDevice(
                vid=0x0416, pid=0x5302,
                vendor_name="ALi Corp", product_name="LCD (HID H)",
                usb_path="1-3",
                protocol="hid", device_type=2,
            )
        ]
        from trcc.scsi_device import find_lcd_devices
        devices = find_lcd_devices()
        assert len(devices) == 1
        assert devices[0]['protocol'] == 'hid'
        assert devices[0]['device_type'] == 2
        assert devices[0]['path'] == 'hid:0416:5302'

    @patch("trcc.device_detector.detect_devices")
    def test_scsi_device_needs_scsi_path(self, mock_detect):
        from trcc.device_detector import DetectedDevice
        mock_detect.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-1",
                scsi_device=None,  # No SCSI path found
            )
        ]
        from trcc.scsi_device import find_lcd_devices
        devices = find_lcd_devices()
        assert len(devices) == 0  # SCSI device without path is excluded

    @patch("trcc.device_detector.detect_devices")
    def test_mixed_scsi_and_hid(self, mock_detect):
        from trcc.device_detector import DetectedDevice
        mock_detect.return_value = [
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
        from trcc.scsi_device import find_lcd_devices

        # Patch LCDDriver to avoid real SCSI access
        with patch("trcc.lcd_driver.LCDDriver", side_effect=Exception("no hw")):
            devices = find_lcd_devices()

        assert len(devices) == 2

        scsi_dev = next(d for d in devices if d['protocol'] == 'scsi')
        hid_dev = next(d for d in devices if d['protocol'] == 'hid')

        assert scsi_dev['path'] == '/dev/sg0'
        assert hid_dev['path'] == 'hid:0418:5303'
        assert hid_dev['device_type'] == 3

    @patch("trcc.device_detector.detect_devices")
    def test_device_index_assigned_across_protocols(self, mock_detect):
        from trcc.device_detector import DetectedDevice
        mock_detect.return_value = [
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
        from trcc.scsi_device import find_lcd_devices

        with patch("trcc.lcd_driver.LCDDriver", side_effect=Exception("no hw")):
            devices = find_lcd_devices()

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

    @patch("trcc.core.models.DeviceModel.detect_devices")
    def test_detect_passes_protocol_to_device_info(self, mock_detect):
        """Simulate what happens when detect_devices finds HID hardware."""
        from trcc.core.models import DeviceInfo, DeviceModel

        # Create model and manually set devices (simulating detection)
        model = DeviceModel()
        model.devices = [
            DeviceInfo(
                name="HID ALi", path="hid:0418:5303",
                vid=0x0418, pid=0x5303,
                protocol="hid", device_type=3,
            )
        ]
        model.selected_device = model.devices[0]

        assert model.selected_device.protocol == "hid"
        assert model.selected_device.device_type == 3


# =========================================================================
# Tests: ProtocolInfo API
# =========================================================================

class TestProtocolInfo:
    """Test the get_protocol_info() GUI API."""

    def test_protocol_info_scsi_device(self, scsi_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(scsi_device)
        assert info.protocol == "scsi"
        assert info.device_type == 1
        assert info.is_scsi is True
        assert info.is_hid is False
        assert "SCSI" in info.protocol_display
        assert "sg_raw" in info.active_backend or info.active_backend == "none"

    def test_protocol_info_hid_type2(self, hid_type2_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type2_device)
        assert info.protocol == "hid"
        assert info.device_type == 2
        assert info.is_hid is True
        assert info.is_scsi is False
        assert "HID" in info.protocol_display
        assert "Type 2" in info.device_type_display

    def test_protocol_info_hid_type3(self, hid_type3_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type3_device)
        assert info.device_type == 3
        assert "Type 3" in info.device_type_display
        assert "ALi" in info.device_type_display

    def test_protocol_info_no_device(self):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(None)
        assert info.protocol == "none"
        assert info.protocol_display == "No device"
        assert info.active_backend == "none"

    def test_protocol_info_has_backends_dict(self, scsi_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(scsi_device)
        assert "sg_raw" in info.backends
        assert "pyusb" in info.backends
        assert "hidapi" in info.backends
        assert all(isinstance(v, bool) for v in info.backends.values())

    def test_has_backend_scsi(self, scsi_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(scsi_device)
        # has_backend depends on sg_raw being installed
        assert info.has_backend == info.backends["sg_raw"]

    @patch("trcc.hid_device.PYUSB_AVAILABLE", True)
    @patch("trcc.hid_device.HIDAPI_AVAILABLE", False)
    def test_hid_active_backend_pyusb(self, hid_type2_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type2_device)
        assert info.active_backend == "pyusb"
        assert info.has_backend is True

    @patch("trcc.hid_device.PYUSB_AVAILABLE", False)
    @patch("trcc.hid_device.HIDAPI_AVAILABLE", True)
    def test_hid_active_backend_hidapi(self, hid_type3_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type3_device)
        assert info.active_backend == "hidapi"
        assert info.has_backend is True

    @patch("trcc.hid_device.PYUSB_AVAILABLE", False)
    @patch("trcc.hid_device.HIDAPI_AVAILABLE", False)
    def test_hid_no_backend(self, hid_type2_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type2_device)
        assert info.active_backend == "none"
        assert info.has_backend is False

    def test_get_backend_availability(self):
        from trcc.device_factory import get_backend_availability
        avail = get_backend_availability()
        assert "sg_raw" in avail
        assert "pyusb" in avail
        assert "hidapi" in avail

    def test_transport_open_false_by_default(self, hid_type2_device):
        from trcc.device_factory import get_protocol_info
        info = get_protocol_info(hid_type2_device)
        assert info.transport_open is False

    @patch("trcc.scsi_device.send_image_to_device", return_value=True)
    def test_cached_protocol_delegates_get_info(self, mock_send, scsi_device):
        """When a protocol is cached, get_protocol_info delegates to proto.get_info()."""
        from trcc.device_factory import get_protocol_info
        # Create and cache a protocol
        DeviceProtocolFactory.get_protocol(scsi_device)
        info = get_protocol_info(scsi_device)
        assert info.protocol == "scsi"
        assert info.is_scsi is True


# =========================================================================
# Tests: DeviceController.get_protocol_info()
# =========================================================================

class TestDeviceControllerProtocolInfo:
    """Test the controller API the GUI calls."""

    def test_no_device_selected(self):
        from trcc.core.controllers import DeviceController
        ctrl = DeviceController()
        info = ctrl.get_protocol_info()
        assert info is not None
        assert info.protocol == "none"

    def test_scsi_device_selected(self):
        from trcc.core.controllers import DeviceController
        from trcc.core.models import DeviceInfo
        ctrl = DeviceController()
        ctrl.model.selected_device = DeviceInfo(
            name="LCD", path="/dev/sg0",
            protocol="scsi", device_type=1,
        )
        info = ctrl.get_protocol_info()
        assert info.protocol == "scsi"
        assert info.is_scsi is True

    def test_hid_device_selected(self):
        from trcc.core.controllers import DeviceController
        from trcc.core.models import DeviceInfo
        ctrl = DeviceController()
        ctrl.model.selected_device = DeviceInfo(
            name="HID LCD", path="hid:0416:5302",
            vid=0x0416, pid=0x5302,
            protocol="hid", device_type=2,
        )
        info = ctrl.get_protocol_info()
        assert info.protocol == "hid"
        assert info.is_hid is True
        assert info.device_type == 2
