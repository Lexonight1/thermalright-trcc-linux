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

from trcc.core.models import HandshakeResult
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

    def test_handshake_pm1_grandvision_480x480(self):
        """PM=1 (GrandVision) → default FBL=72 → 480x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=1, sub=0)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        bd._ep_out.write.assert_called_once_with(_HANDSHAKE_PAYLOAD, timeout=_HANDSHAKE_TIMEOUT_MS)
        bd._ep_in.read.assert_called_once_with(_HANDSHAKE_READ_SIZE, timeout=_HANDSHAKE_TIMEOUT_MS)
        self.assertIsInstance(result, HandshakeResult)
        self.assertEqual(result.resolution, (480, 480))
        self.assertEqual(result.model_id, 1)
        self.assertEqual(bd.pm, 1)
        self.assertEqual(bd.sub_type, 0)
        self.assertEqual(bd.width, 480)
        self.assertEqual(bd.height, 480)

    def test_handshake_pm5_mjolnir_480x480(self):
        """PM=5 (Mjolnir Vision) → default FBL=72 → 480x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=5)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (480, 480))
        self.assertEqual(bd.pm, 5)

    def test_handshake_pm7_640x480(self):
        """PM=7 → FBL override to 64 → 640x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=7)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (640, 480))

    def test_handshake_pm32_320x320(self):
        """PM=32 → FBL override to 100 → 320x320."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=32)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (320, 320))

    def test_handshake_pm64_1600x720(self):
        """PM=64 → FBL override to 114 → 1600x720."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=64)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (1600, 720))

    def test_handshake_pm1_sub48_1600x720(self):
        """PM=1 + SUB=48 → FBL override to 114 → 1600x720."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=1, sub=48)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (1600, 720))

    def test_handshake_pm65_1920x462(self):
        """PM=65 → FBL override to 192 → 1920x462."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=65)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (1920, 462))

    def test_handshake_pm1_sub49_1920x462(self):
        """PM=1 + SUB=49 → FBL override to 192 → 1920x462."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=1, sub=49)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (1920, 462))

    def test_handshake_pm9_854x480(self):
        """PM=9 → FBL override to 224 → 854x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=9)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (854, 480))

    def test_handshake_pm10_960x540(self):
        """PM=10 → 960x540."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=10)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (960, 540))

    def test_handshake_pm12_800x480(self):
        """PM=12 → 800x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=12)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (800, 480))

    def test_handshake_default_pm_480x480(self):
        """Any unrecognized PM defaults to 480x480 (FBL=72)."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=99)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (480, 480))
        self.assertEqual(bd.pm, 99)
        self.assertEqual(bd.width, 480)

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
        """Header has magic, cmd, dimensions, mode, and payload length."""
        bd = self._setup_device(width=320, height=320)
        data = b'\x00' * 1000
        bd.send_frame(data)

        # First write is the 64-byte header
        header = bd._ep_out.write.call_args_list[0][0][0]
        self.assertEqual(len(header), 64)
        # Magic
        self.assertEqual(header[0:4], b'\x12\x34\x56\x78')
        # Cmd = 3 (raw RGB565)
        self.assertEqual(struct.unpack_from("<I", header, 4)[0], 3)
        # Width / height
        self.assertEqual(struct.unpack_from("<I", header, 8)[0], 320)
        self.assertEqual(struct.unpack_from("<I", header, 12)[0], 320)
        # Mode = 2
        self.assertEqual(struct.unpack_from("<I", header, 56)[0], 2)
        # Payload length
        self.assertEqual(struct.unpack_from("<I", header, 60)[0], 1000)

    def test_send_frame_payload_chunked(self):
        """Pixel data is sent in chunks after the header."""
        bd = self._setup_device()
        data = b'\xAB\xCD' * 500  # 1000 bytes
        bd.send_frame(data)

        calls = bd._ep_out.write.call_args_list
        # calls: header + 1 data chunk (< 16 KiB) + ZLP
        self.assertEqual(calls[1][0][0], data)

    def test_send_frame_zlp_always_sent(self):
        """ZLP frame delimiter is always sent after pixel data."""
        bd = self._setup_device()
        data = b'\x00' * 100
        bd.send_frame(data)

        # Last write should be ZLP
        last_call = bd._ep_out.write.call_args_list[-1]
        self.assertEqual(last_call[0][0], b"")

    def test_send_frame_large_data_chunked(self):
        """Data larger than 16 KiB is sent in multiple chunks."""
        bd = self._setup_device()
        data = b'\x00' * (16 * 1024 + 100)  # 16484 bytes
        bd.send_frame(data)

        calls = bd._ep_out.write.call_args_list
        # header + 2 data chunks + ZLP = 4 writes
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(calls[1][0][0]), 16 * 1024)
        self.assertEqual(len(calls[2][0][0]), 100)

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
            product_name="GrandVision 360 AIO",
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
