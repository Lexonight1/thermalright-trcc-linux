"""
Tests for bulk_device — raw USB bulk handler for USBLCDNew-type devices.

Tests cover:
- BulkDevice construction and defaults
- Handshake protocol (64-byte write, 1024-byte read, PM/SUB extraction)
- Resolution mapping from PM byte
- Frame send (header + RGB565 data + ZLP logic)
- Close / resource cleanup
- HandshakeResult usage
- Integration with device_factory.BulkProtocol
"""

import os
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.device_base import HandshakeResult
from trcc.device_bulk import (
    _HANDSHAKE_PAYLOAD,
    _HANDSHAKE_READ_SIZE,
    _HANDSHAKE_TIMEOUT_MS,
    BulkDevice,
)


def _make_handshake_response(pm: int = 100, sub: int = 0, length: int = 1024) -> bytes:
    """Build a fake handshake response with PM at byte[24] and SUB at byte[36]."""
    resp = bytearray(length)
    if length > 24:
        resp[24] = pm
    if length > 36:
        resp[36] = sub
    return bytes(resp)


class TestBulkDeviceConstants(unittest.TestCase):
    """Test module-level constants."""

    def test_handshake_payload_length(self):
        self.assertEqual(len(_HANDSHAKE_PAYLOAD), 64)

    def test_handshake_payload_magic(self):
        self.assertEqual(_HANDSHAKE_PAYLOAD[0], 0x12)
        self.assertEqual(_HANDSHAKE_PAYLOAD[1], 0x34)
        self.assertEqual(_HANDSHAKE_PAYLOAD[2], 0x56)
        self.assertEqual(_HANDSHAKE_PAYLOAD[3], 0x78)

    def test_handshake_payload_byte56(self):
        """Byte 56 = 0x01 (from USBLCDNew ThreadSendDeviceData)."""
        self.assertEqual(_HANDSHAKE_PAYLOAD[56], 0x01)

    def test_handshake_read_size(self):
        self.assertEqual(_HANDSHAKE_READ_SIZE, 1024)


class TestBulkDeviceInit(unittest.TestCase):
    """Test BulkDevice construction."""

    def test_defaults(self):
        bd = BulkDevice(0x87AD, 0x70DB)
        self.assertEqual(bd.vid, 0x87AD)
        self.assertEqual(bd.pid, 0x70DB)
        self.assertEqual(bd.usb_path, "")
        self.assertIsNone(bd._dev)
        self.assertIsNone(bd._ep_out)
        self.assertIsNone(bd._ep_in)
        self.assertEqual(bd.pm, 0)
        self.assertEqual(bd.sub_type, 0)
        self.assertEqual(bd.width, 0)
        self.assertEqual(bd.height, 0)
        self.assertEqual(bd._raw_handshake, b"")

    def test_with_usb_path(self):
        bd = BulkDevice(0x87AD, 0x70DB, usb_path="2-1.4")
        self.assertEqual(bd.usb_path, "2-1.4")

    def test_has_handshake_method(self):
        """BulkDevice must have a handshake() method."""
        bd = BulkDevice(0x87AD, 0x70DB)
        self.assertTrue(callable(getattr(bd, 'handshake', None)))


class TestBulkDeviceOpen(unittest.TestCase):
    """Test _open() USB enumeration."""

    @patch.dict("sys.modules", {"usb": MagicMock(), "usb.core": MagicMock(), "usb.util": MagicMock()})
    def test_open_success(self):
        """Successful USB open: find device, detach drivers, find endpoints."""
        import usb.core
        import usb.util

        mock_dev = MagicMock()
        usb.core.find.return_value = mock_dev
        mock_cfg = MagicMock()
        mock_cfg.bNumInterfaces = 1
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_dev.is_kernel_driver_active.return_value = False
        mock_intf = MagicMock()
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)

        ep_out = MagicMock()
        ep_out.bEndpointAddress = 0x01
        ep_in = MagicMock()
        ep_in.bEndpointAddress = 0x81
        usb.util.find_descriptor.side_effect = [ep_out, ep_in]
        usb.util.endpoint_direction.side_effect = lambda addr: addr & 0x80
        usb.util.ENDPOINT_OUT = 0x00
        usb.util.ENDPOINT_IN = 0x80

        bd = BulkDevice(0x87AD, 0x70DB)
        bd._open()

        usb.core.find.assert_called_once_with(idVendor=0x87AD, idProduct=0x70DB)
        self.assertEqual(bd._dev, mock_dev)
        self.assertEqual(bd._ep_out, ep_out)
        self.assertEqual(bd._ep_in, ep_in)

    @patch.dict("sys.modules", {"usb": MagicMock(), "usb.core": MagicMock(), "usb.util": MagicMock()})
    def test_open_device_not_found(self):
        import usb.core
        usb.core.find.return_value = None

        bd = BulkDevice(0x87AD, 0x70DB)
        with self.assertRaises(RuntimeError):
            bd._open()

    @patch.dict("sys.modules", {"usb": MagicMock(), "usb.core": MagicMock(), "usb.util": MagicMock()})
    def test_open_no_endpoints(self):
        import usb.core
        import usb.util

        mock_dev = MagicMock()
        usb.core.find.return_value = mock_dev
        mock_cfg = MagicMock()
        mock_cfg.bNumInterfaces = 0
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_intf = MagicMock()
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)
        usb.util.find_descriptor.return_value = None

        bd = BulkDevice(0x87AD, 0x70DB)
        with self.assertRaises(RuntimeError):
            bd._open()


class TestBulkDeviceHandshake(unittest.TestCase):
    """Test handshake protocol."""

    def _setup_device(self):
        """Create a BulkDevice with mocked USB transport."""
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._dev = MagicMock()
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()
        return bd

    def test_handshake_pm100_resolution_320x320(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=100, sub=0)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        bd._ep_out.write.assert_called_once_with(_HANDSHAKE_PAYLOAD, timeout=_HANDSHAKE_TIMEOUT_MS)
        bd._ep_in.read.assert_called_once_with(_HANDSHAKE_READ_SIZE, timeout=_HANDSHAKE_TIMEOUT_MS)
        self.assertIsInstance(result, HandshakeResult)
        self.assertEqual(result.resolution, (320, 320))
        self.assertEqual(result.model_id, 100)
        self.assertEqual(bd.pm, 100)
        self.assertEqual(bd.sub_type, 0)
        self.assertEqual(bd.width, 320)
        self.assertEqual(bd.height, 320)

    def test_handshake_pm36_resolution_240x240(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=36)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (240, 240))
        self.assertEqual(bd.pm, 36)

    def test_handshake_pm50_resolution_240x320(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=50)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (240, 320))

    def test_handshake_pm101_resolution_480x480(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=101)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (480, 480))

    def test_handshake_unknown_pm(self):
        """Unknown PM value → resolution is None."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=99)  # not in res_map
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertIsNone(result.resolution)
        self.assertEqual(bd.pm, 99)
        self.assertEqual(bd.width, 0)

    def test_handshake_sub_type_extracted(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=100, sub=5)
        bd._ep_in.read.return_value = resp

        bd.handshake()

        self.assertEqual(bd.sub_type, 5)

    def test_handshake_resp_too_short(self):
        """Response < 41 bytes → failed handshake."""
        bd = self._setup_device()
        bd._ep_in.read.return_value = bytes(40)  # too short

        result = bd.handshake()

        self.assertIsNone(result.resolution)
        self.assertEqual(result.model_id, 0)

    def test_handshake_pm_zero(self):
        """PM=0 at resp[24] → failed handshake."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=0)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertIsNone(result.resolution)

    def test_handshake_stores_raw_response(self):
        bd = self._setup_device()
        resp = _make_handshake_response(pm=100)
        bd._ep_in.read.return_value = resp

        bd.handshake()

        self.assertEqual(bd._raw_handshake, resp)
        self.assertEqual(len(bd._raw_handshake), 1024)

    def test_handshake_opens_device_if_needed(self):
        """If _dev is None, handshake calls _open() first."""
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._open = MagicMock()

        # After _open, mock the endpoints
        def setup_after_open():
            bd._dev = MagicMock()
            bd._ep_out = MagicMock()
            bd._ep_in = MagicMock()
            bd._ep_in.read.return_value = _make_handshake_response(pm=100)
        bd._open.side_effect = setup_after_open

        bd.handshake()
        bd._open.assert_called_once()


class TestBulkDeviceSendFrame(unittest.TestCase):
    """Test frame send protocol."""

    def _setup_device(self, width=320, height=320):
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._dev = MagicMock()
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()
        bd.width = width
        bd.height = height
        bd.pm = 100
        return bd

    def test_send_frame_header_format(self):
        """Header is 64 bytes with data size as uint32 LE at offset 60."""
        bd = self._setup_device()
        data = b'\x00' * 1000
        bd.send_frame(data)

        args = bd._ep_out.write.call_args
        payload = args[0][0]
        # First 64 bytes are the header
        header = payload[:64]
        data_size = struct.unpack_from("<I", header, 60)[0]
        self.assertEqual(data_size, 1000)

    def test_send_frame_payload_includes_data(self):
        bd = self._setup_device()
        data = b'\xAB\xCD' * 500
        bd.send_frame(data)

        args = bd._ep_out.write.call_args
        payload = args[0][0]
        self.assertEqual(payload[64:], data)
        self.assertEqual(len(payload), 64 + 1000)

    def test_send_frame_zlp_when_512_aligned(self):
        """Send ZLP if total payload is 512-byte aligned."""
        bd = self._setup_device()
        # 64 (header) + 448 (data) = 512, which is 512-byte aligned
        data = b'\x00' * 448
        bd.send_frame(data)

        # Should have 2 writes: payload + ZLP
        self.assertEqual(bd._ep_out.write.call_count, 2)
        second_call = bd._ep_out.write.call_args_list[1]
        self.assertEqual(second_call[0][0], b"")

    def test_send_frame_no_zlp_when_not_aligned(self):
        """No ZLP when total is not 512-byte aligned."""
        bd = self._setup_device()
        # 64 + 100 = 164, not 512-aligned
        data = b'\x00' * 100
        bd.send_frame(data)

        self.assertEqual(bd._ep_out.write.call_count, 1)

    def test_send_frame_returns_true_on_success(self):
        bd = self._setup_device()
        result = bd.send_frame(b'\x00' * 100)
        self.assertTrue(result)

    def test_send_frame_returns_false_on_error(self):
        bd = self._setup_device()
        bd._ep_out.write.side_effect = Exception("USB error")
        result = bd.send_frame(b'\x00' * 100)
        self.assertFalse(result)

    def test_send_frame_triggers_handshake_if_not_open(self):
        """If _dev is None, send_frame calls handshake() first."""
        bd = BulkDevice(0x87AD, 0x70DB)
        bd.handshake = MagicMock()

        # After handshake, mock the device
        def setup_after_hs():
            bd._dev = MagicMock()
            bd._ep_out = MagicMock()
        bd.handshake.side_effect = setup_after_hs

        bd.send_frame(b'\x00' * 100)
        bd.handshake.assert_called_once()


class TestBulkDeviceClose(unittest.TestCase):
    """Test close/cleanup."""

    @patch.dict("sys.modules", {"usb": MagicMock(), "usb.core": MagicMock(), "usb.util": MagicMock()})
    def test_close_releases_resources(self):
        import usb.util

        bd = BulkDevice(0x87AD, 0x70DB)
        mock_dev = MagicMock()
        bd._dev = mock_dev
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()

        bd.close()

        usb.util.dispose_resources.assert_called_once_with(mock_dev)
        self.assertIsNone(bd._dev)
        self.assertIsNone(bd._ep_out)
        self.assertIsNone(bd._ep_in)

    def test_close_noop_when_not_open(self):
        """close() on a never-opened device should not raise."""
        bd = BulkDevice(0x87AD, 0x70DB)
        bd.close()  # Should not raise


class TestBulkProtocol(unittest.TestCase):
    """Test device_factory.BulkProtocol integration."""

    def test_create_via_factory(self):
        """Factory routes protocol='bulk' to BulkProtocol."""
        from trcc.device_factory import BulkProtocol, DeviceProtocolFactory

        device_info = MagicMock()
        device_info.protocol = 'bulk'
        device_info.vid = 0x87AD
        device_info.pid = 0x70DB
        device_info.path = 'bulk:87ad:70db'
        device_info.implementation = 'bulk_usblcdnew'

        proto = DeviceProtocolFactory.create_protocol(device_info)
        self.assertIsInstance(proto, BulkProtocol)
        self.assertEqual(proto.protocol_name, "bulk")
        proto.close()

    def test_protocol_info(self):
        from trcc.device_factory import BulkProtocol

        proto = BulkProtocol(0x87AD, 0x70DB)
        info = proto.get_info()
        self.assertEqual(info.protocol, "bulk")
        self.assertEqual(info.device_type, 4)
        self.assertIn("Bulk", info.protocol_display)
        proto.close()

    def test_is_not_led(self):
        from trcc.device_factory import BulkProtocol

        proto = BulkProtocol(0x87AD, 0x70DB)
        self.assertFalse(proto.is_led)
        proto.close()


class TestBulkDeviceDetection(unittest.TestCase):
    """Test that 87AD:70DB is detected as bulk protocol."""

    def test_in_bulk_devices_registry(self):
        from trcc.device_detector import _BULK_DEVICES

        self.assertIn((0x87AD, 0x70DB), _BULK_DEVICES)
        info = _BULK_DEVICES[(0x87AD, 0x70DB)]
        self.assertEqual(info.protocol, "bulk")
        self.assertEqual(info.implementation, "bulk_usblcdnew")
        self.assertEqual(info.device_type, 4)

    def test_not_in_scsi_devices(self):
        from trcc.device_detector import KNOWN_DEVICES

        self.assertNotIn((0x87AD, 0x70DB), KNOWN_DEVICES)

    def test_not_in_led_devices(self):
        from trcc.device_detector import _LED_DEVICES

        self.assertNotIn((0x87AD, 0x70DB), _LED_DEVICES)

    def test_in_all_devices(self):
        from trcc.device_detector import _get_all_devices

        all_devs = _get_all_devices()
        self.assertIn((0x87AD, 0x70DB), all_devs)

    def test_find_lcd_devices_bulk(self):
        """find_lcd_devices returns bulk device with correct protocol."""
        from trcc.device_detector import DetectedDevice

        fake_dev = DetectedDevice(
            vid=0x87AD, pid=0x70DB,
            vendor_name="ChiZhu Tech",
            product_name="GrandVision 360 AIO (Bulk USB)",
            usb_path="2-1",
            implementation="bulk_usblcdnew",
            model="GRAND_VISION",
            button_image="A1CZTV",
            protocol="bulk",
            device_type=4,
        )

        with patch("trcc.device_detector.detect_devices", return_value=[fake_dev]):
            from trcc.device_scsi import find_lcd_devices
            devices = find_lcd_devices()

        self.assertEqual(len(devices), 1)
        d = devices[0]
        self.assertEqual(d['protocol'], 'bulk')
        self.assertEqual(d['path'], 'bulk:87ad:70db')
        self.assertEqual(d['vid'], 0x87AD)
        self.assertEqual(d['pid'], 0x70DB)
        self.assertEqual(d['device_type'], 4)


if __name__ == '__main__':
    unittest.main()
