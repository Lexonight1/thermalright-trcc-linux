"""
Tests for ly — LY USB bulk handler for Trofeo Vision 9.16 LCD (0416:5408/5409).

Tests cover:
- LyDevice construction and defaults
- Handshake protocol (2048-byte write, 512-byte read, validation)
- PM extraction (LY: 64+resp[20], LY1: 50+resp[36])
- Frame chunking (512-byte blocks, 16-byte headers)
- Frame send (4096-byte batches + ACK read)
- Close / resource cleanup
- LyProtocol factory routing
"""

import os
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.legacy.adapters.device.ly import (
    _CHUNK_DATA_SIZE,
    _CHUNK_HEADER_SIZE,
    _CHUNK_SIZE,
    _HANDSHAKE_PAYLOAD,
    _PID_LY,
    _PID_LY1,
    LyDevice,
)


def _make_ly_response(pm_byte_20: int = 1, pm_byte_22: int = 0,
                      pm_byte_36: int = 0, length: int = 512) -> bytes:
    """Build a valid LY handshake response."""
    resp = bytearray(length)
    resp[0] = 3       # validation
    resp[1] = 0xFF    # validation
    resp[8] = 1       # validation
    resp[20] = pm_byte_20
    resp[22] = pm_byte_22
    resp[36] = pm_byte_36
    return bytes(resp)


class TestLyDeviceConstants(unittest.TestCase):
    def test_handshake_payload_length(self):
        self.assertEqual(len(_HANDSHAKE_PAYLOAD), 2048)

    def test_handshake_payload_header(self):
        self.assertEqual(_HANDSHAKE_PAYLOAD[0], 0x02)
        self.assertEqual(_HANDSHAKE_PAYLOAD[1], 0xFF)
        self.assertEqual(_HANDSHAKE_PAYLOAD[8], 0x01)

    def test_handshake_payload_padding_is_zero(self):
        self.assertTrue(all(b == 0 for b in _HANDSHAKE_PAYLOAD[16:]))

    def test_chunk_sizes(self):
        self.assertEqual(_CHUNK_SIZE, 512)
        self.assertEqual(_CHUNK_HEADER_SIZE, 16)
        self.assertEqual(_CHUNK_DATA_SIZE, 496)
        self.assertEqual(_CHUNK_SIZE, _CHUNK_HEADER_SIZE + _CHUNK_DATA_SIZE)

    def test_pids(self):
        self.assertEqual(_PID_LY, 0x5408)
        self.assertEqual(_PID_LY1, 0x5409)


class TestLyDeviceInit(unittest.TestCase):
    def test_ly_defaults(self):
        d = LyDevice(0x0416, _PID_LY)
        self.assertEqual(d.vid, 0x0416)
        self.assertEqual(d.pid, _PID_LY)
        self.assertEqual(d._chunk_cmd, 1)
        self.assertIsNone(d._dev)
        self.assertEqual(d.pm, 0)

    def test_ly1_chunk_cmd(self):
        d = LyDevice(0x0416, _PID_LY1)
        self.assertEqual(d._chunk_cmd, 2)


class TestLyDeviceHandshake(unittest.TestCase):
    def _setup(self, pid=_PID_LY):
        d = LyDevice(0x0416, pid)
        d._dev = MagicMock()
        d._ep_out = MagicMock()
        d._ep_in = MagicMock()
        return d

    def test_handshake_ly_pm65_1920x462(self):
        """LY: resp[20]=1 → PM=64+1=65 → FBL=192 → 1920x462."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_20=1)
        result = d.handshake()

        d._ep_out.write.assert_called_once()
        self.assertEqual(result.resolution, (1920, 462))
        self.assertEqual(d.pm, 65)
        self.assertEqual(result.model_id, 192)  # FBL via pm_to_fbl(65)

    def test_handshake_ly_clamp_min(self):
        """LY: resp[20]=0 → clamped to 1 → PM=65."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_20=0)
        d.handshake()
        self.assertEqual(d.pm, 65)

    def test_handshake_ly_clamp_3(self):
        """LY: resp[20]=3 → clamped to 1 → PM=65."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_20=3)
        d.handshake()
        self.assertEqual(d.pm, 65)

    def test_handshake_ly_no_clamp_4(self):
        """LY: resp[20]=4 → not clamped → PM=68."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_20=4)
        d.handshake()
        self.assertEqual(d.pm, 68)

    def test_handshake_ly_sub_extracted(self):
        """LY: SUB = resp[22] + 1."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_22=5)
        d.handshake()
        self.assertEqual(d.sub_type, 6)

    def test_handshake_ly1_pm(self):
        """LY1: PM = 50 + resp[36]."""
        d = self._setup(pid=_PID_LY1)
        d._ep_in.read.return_value = _make_ly_response(pm_byte_36=15)
        d.handshake()
        self.assertEqual(d.pm, 65)

    def test_handshake_validation_byte0(self):
        """Validation fails if resp[0] != 3."""
        d = self._setup()
        resp = bytearray(_make_ly_response())
        resp[0] = 0
        d._ep_in.read.return_value = bytes(resp)
        result = d.handshake()
        self.assertIsNone(result.resolution)

    def test_handshake_validation_byte1(self):
        """Validation fails if resp[1] != 0xFF."""
        d = self._setup()
        resp = bytearray(_make_ly_response())
        resp[1] = 0
        d._ep_in.read.return_value = bytes(resp)
        result = d.handshake()
        self.assertIsNone(result.resolution)

    def test_handshake_validation_byte8(self):
        """Validation fails if resp[8] != 1."""
        d = self._setup()
        resp = bytearray(_make_ly_response())
        resp[8] = 0
        d._ep_in.read.return_value = bytes(resp)
        result = d.handshake()
        self.assertIsNone(result.resolution)

    def test_handshake_stores_raw(self):
        d = self._setup()
        resp = _make_ly_response()
        d._ep_in.read.return_value = resp
        d.handshake()
        self.assertEqual(d._raw_handshake, resp)

    def test_handshake_use_jpeg(self):
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response()
        d.handshake()
        self.assertTrue(d.use_jpeg)

    def test_handshake_pm_byte_and_sub_byte(self):
        """HandshakeResult carries raw PM + SUB for button image lookup (#69)."""
        d = self._setup()
        d._ep_in.read.return_value = _make_ly_response(pm_byte_20=1, pm_byte_22=2)
        result = d.handshake()

        self.assertEqual(result.pm_byte, 65)   # 64 + 1
        self.assertEqual(result.sub_byte, 3)   # resp[22]+1
        self.assertEqual(result.model_id, 192)  # FBL


class TestLyDeviceSendFrame(unittest.TestCase):
    def _setup(self, pid=_PID_LY):
        d = LyDevice(0x0416, pid)
        d._dev = MagicMock()
        d._ep_out = MagicMock()
        d._ep_in = MagicMock()
        d.width = 1920
        d.height = 462
        d.pm = 65
        return d

    def test_chunk_header_format(self):
        """First chunk header: 01 FF [total_size LE32] [data_len LE16] [cmd] [num_chunks LE16] [idx LE16]."""
        d = self._setup()
        data = b'\xAB' * 100  # Small payload: 1 chunk
        d.send_frame(data)

        # Get the buffer that was written
        write_calls = d._ep_out.write.call_args_list
        buf = write_calls[0][0][0]  # First write's first arg

        # Check chunk header
        self.assertEqual(buf[0], 0x01)
        self.assertEqual(buf[1], 0xFF)
        total_size = struct.unpack_from("<I", buf, 2)[0]
        self.assertEqual(total_size, 100)
        data_len = struct.unpack_from("<H", buf, 6)[0]
        self.assertEqual(data_len, 100)
        self.assertEqual(buf[8], 1)  # LY cmd
        num_chunks = struct.unpack_from("<H", buf, 9)[0]
        self.assertEqual(num_chunks, 1)
        chunk_idx = struct.unpack_from("<H", buf, 11)[0]
        self.assertEqual(chunk_idx, 0)

    def test_chunk_header_ly1_cmd(self):
        """LY1: chunk header byte[8] = 2."""
        d = self._setup(pid=_PID_LY1)
        d.send_frame(b'\x00' * 100)
        buf = d._ep_out.write.call_args_list[0][0][0]
        self.assertEqual(buf[8], 2)

    def test_multi_chunk_count(self):
        """Payload > 496 bytes splits into multiple chunks."""
        d = self._setup()
        data = b'\x00' * 1000  # 1000 / 496 + 1 = 3 chunks
        d.send_frame(data)
        # 3 chunks, padded to 4 (multiple of 4 for LY) = 4 * 512 = 2048 bytes
        # Sent in one USB write (2048 < 4096)
        self.assertTrue(d._ep_out.write.called)

    def test_ack_read_after_frame(self):
        """Device reads 512-byte ACK after sending frame."""
        d = self._setup()
        d.send_frame(b'\x00' * 100)
        d._ep_in.read.assert_called_once()

    def test_send_returns_true_on_success(self):
        d = self._setup()
        self.assertTrue(d.send_frame(b'\x00' * 100))

    def test_send_returns_false_on_persistent_error(self):
        d = self._setup()
        d.close = MagicMock()
        d.handshake = MagicMock()
        d._ep_out.write.side_effect = OSError("USB error")
        self.assertFalse(d.send_frame(b'\x00' * 100))

    def test_send_retries_on_first_error(self):
        """First send fails, reconnect + retry succeeds."""
        d = self._setup()
        d.close = MagicMock()
        d.handshake = MagicMock()
        call_count = 0

        def fail_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("USB disconnect")

        d._ep_out.write.side_effect = fail_once
        self.assertTrue(d.send_frame(b'\x00' * 100))
        d.close.assert_called_once()
        d.handshake.assert_called_once()

    def test_data_payload_in_chunk(self):
        """Image data appears at offset 16 in chunk."""
        d = self._setup()
        data = b'\xAB' * 50
        d.send_frame(data)
        buf = d._ep_out.write.call_args_list[0][0][0]
        self.assertEqual(buf[16:16 + 50], data)


class TestLyDeviceClose(unittest.TestCase):
    @patch.dict("sys.modules", {"usb": MagicMock(), "usb.core": MagicMock(), "usb.util": MagicMock()})
    def test_close_releases_resources(self):
        d = LyDevice(0x0416, _PID_LY)
        d._dev = MagicMock()
        d._ep_out = MagicMock()
        d._ep_in = MagicMock()
        d.close()
        self.assertIsNone(d._dev)

    def test_close_noop_when_not_open(self):
        d = LyDevice(0x0416, _PID_LY)
        d.close()  # Should not raise


class TestLyProtocol(unittest.TestCase):
    def test_create_via_factory(self):
        from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
        from trcc.legacy.adapters.device.ly_protocol import LyProtocol

        device_info = MagicMock()
        device_info.protocol = 'ly'
        device_info.vid = 0x0416
        device_info.pid = _PID_LY
        device_info.path = 'ly:0416:5408'
        device_info.implementation = 'ly_bulk'

        proto = DeviceProtocolFactory.create_protocol(device_info)
        self.assertIsInstance(proto, LyProtocol)
        self.assertEqual(proto.protocol_name, "ly")
        proto.close()

    def test_protocol_info(self):
        from trcc.legacy.adapters.device.ly_protocol import LyProtocol

        proto = LyProtocol(0x0416, _PID_LY)
        info = proto.get_info()
        self.assertEqual(info.protocol, "ly")
        self.assertEqual(info.device_type, 5)
        proto.close()


class TestLyDeviceDetection(unittest.TestCase):
    def test_ly_in_registry(self):
        from trcc.legacy.adapters.device.detector import _LY_DEVICES
        self.assertIn((0x0416, 0x5408), _LY_DEVICES)
        self.assertIn((0x0416, 0x5409), _LY_DEVICES)
        info = _LY_DEVICES[(0x0416, 0x5408)]
        self.assertEqual(info.protocol, "ly")
        self.assertEqual(info.device_type, 5)

    def test_ly_in_all_registries(self):
        from trcc.legacy.adapters.device.detector import DeviceDetector
        all_devs = DeviceDetector._get_all_registries()
        self.assertIn((0x0416, 0x5408), all_devs)
        self.assertIn((0x0416, 0x5409), all_devs)


if __name__ == '__main__':
    unittest.main()
