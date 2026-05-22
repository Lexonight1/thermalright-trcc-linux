"""Tests for scsi_device – SCSI frame chunking, header building, CRC.

Tests use real ScsiDevice with an injected FakeScsiTransport — same DI
flow as production. No patching of private methods, no subprocess mocks.
The transport is the port; we test logic above it by controlling what
the port returns.
"""

import binascii
import struct
import unittest
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.adapters.device.linux.detector import find_lcd_devices
from trcc.legacy.adapters.device.scsi import (
    _BOOT_MAX_RETRIES,
    _BOOT_SIGNATURE,
    _BOOT_WAIT_SECONDS,
    _CHUNK_SIZE_LARGE,
    _CHUNK_SIZE_SMALL,
    _FRAME_CMD_BASE,
    _POST_INIT_DELAY,
    ScsiDevice,
    ScsiTransport,
)
from trcc.legacy.core.models import FBL_PROFILES

_p320 = FBL_PROFILES[100]   # 320×320 canonical profile


# =========================================================================
# FakeScsiTransport — test double at the port boundary.
# Scripts read responses + records writes. No subprocess, no USB.
# =========================================================================

class FakeScsiTransport(ScsiTransport):
    """Test transport: scripted reads, recorded writes."""

    def __init__(self, reads=None):
        self.reads = list(reads or [])
        self.sends: list[tuple[bytes, bytes]] = []  # (cdb, data)
        self.read_calls: list[tuple[bytes, int]] = []  # (cdb, length)
        self._open = False

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        self.sends.append((cdb, data))
        return True

    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        self.read_calls.append((cdb, length))
        if self.reads:
            return self.reads.pop(0)
        return b'\x00' * length


def _make_device(transport=None, width=320, height=320):
    """Build a ScsiDevice with the given transport (fake by default)."""
    return ScsiDevice(
        '/dev/sg0', transport or FakeScsiTransport(),
        width=width, height=height, vid=0x0402, pid=0x3922,
    )


class TestBootConstants(unittest.TestCase):
    """Verify boot state constants match USBLCD.exe protocol."""

    def test_boot_signature(self):
        self.assertEqual(_BOOT_SIGNATURE, b'\xa1\xa2\xa3\xa4')

    def test_boot_wait_seconds(self):
        self.assertEqual(_BOOT_WAIT_SECONDS, 3.0)

    def test_boot_max_retries(self):
        self.assertGreaterEqual(_BOOT_MAX_RETRIES, 3)

    def test_post_init_delay(self):
        self.assertGreater(_POST_INIT_DELAY, 0)
        self.assertLessEqual(_POST_INIT_DELAY, 1.0)


class TestCRC32(unittest.TestCase):
    """CRC32 against known values."""

    def test_empty(self):
        self.assertEqual(ScsiDevice._crc32(b''), binascii.crc32(b'') & 0xFFFFFFFF)

    def test_known_value(self):
        self.assertEqual(ScsiDevice._crc32(b'hello'), binascii.crc32(b'hello') & 0xFFFFFFFF)

    def test_unsigned(self):
        """Result is always unsigned 32-bit."""
        result = ScsiDevice._crc32(b'\xff' * 100)
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 2**32)


class TestBuildHeader(unittest.TestCase):
    """20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""

    def test_length(self):
        header = ScsiDevice._build_header(0xF5, 0xE100)
        self.assertEqual(len(header), 20)

    def test_cmd_field(self):
        header = ScsiDevice._build_header(0xF5, 0xE100)
        cmd = struct.unpack('<I', header[:4])[0]
        self.assertEqual(cmd, 0xF5)

    def test_zero_padding(self):
        header = ScsiDevice._build_header(0xF5, 0xE100)
        self.assertEqual(header[4:12], b'\x00' * 8)

    def test_size_field(self):
        header = ScsiDevice._build_header(0x1F5, 0xE100)
        size = struct.unpack('<I', header[12:16])[0]
        self.assertEqual(size, 0xE100)

    def test_crc_matches_payload(self):
        header = ScsiDevice._build_header(0xF5, 0xE100)
        payload = header[:16]
        expected_crc = binascii.crc32(payload) & 0xFFFFFFFF
        actual_crc = struct.unpack('<I', header[16:20])[0]
        self.assertEqual(actual_crc, expected_crc)

    def test_different_cmds_different_headers(self):
        h1 = ScsiDevice._build_header(0xF5, 0xE100)
        h2 = ScsiDevice._build_header(0x1F5, 0xE100)
        self.assertNotEqual(h1, h2)


class TestGetFrameChunks(unittest.TestCase):
    """Frame chunk calculation for various resolutions."""

    def test_320x320_total_bytes(self):
        chunks = ScsiDevice._get_frame_chunks(320, 320)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 320 * 320 * 2)  # 204,800

    def test_320x320_chunk_count(self):
        chunks = ScsiDevice._get_frame_chunks(320, 320)
        self.assertEqual(len(chunks), 4)  # 3x64K + 8K

    def test_480x480_total_bytes(self):
        chunks = ScsiDevice._get_frame_chunks(480, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 480 * 480 * 2)  # 460,800

    def test_480x480_chunk_count(self):
        chunks = ScsiDevice._get_frame_chunks(480, 480)
        self.assertEqual(len(chunks), 8)  # 7x64K + 2K

    def test_640x480_total_bytes(self):
        chunks = ScsiDevice._get_frame_chunks(640, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 640 * 480 * 2)  # 614,400

    def test_240x240_total_bytes(self):
        chunks = ScsiDevice._get_frame_chunks(240, 240)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 240 * 240 * 2)  # 115,200

    def test_320x240_uses_small_chunks(self):
        """FBL 50 (320x240) uses 0xE100 chunks like Windows USBLCD.exe."""
        chunks = ScsiDevice._get_frame_chunks(320, 240)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 320 * 240 * 2)  # 153,600
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0][1], _CHUNK_SIZE_SMALL)  # 0xE100
        self.assertEqual(chunks[1][1], _CHUNK_SIZE_SMALL)  # 0xE100
        self.assertEqual(chunks[2][1], 153600 - 2 * _CHUNK_SIZE_SMALL)  # 0x9600

    def test_240x240_uses_small_chunks(self):
        """FBL 36 (240x240) uses 0xE100 chunks like Windows USBLCD.exe."""
        chunks = ScsiDevice._get_frame_chunks(240, 240)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][1], _CHUNK_SIZE_SMALL)
        self.assertEqual(chunks[1][1], 240 * 240 * 2 - _CHUNK_SIZE_SMALL)

    def test_chunk_sizes_within_limit(self):
        """No chunk exceeds its mode's limit."""
        for w, h in [(320, 320), (480, 480), (640, 480)]:
            for _, size in ScsiDevice._get_frame_chunks(w, h):
                self.assertLessEqual(size, _CHUNK_SIZE_LARGE)
        for w, h in [(240, 240), (320, 240)]:
            for _, size in ScsiDevice._get_frame_chunks(w, h):
                self.assertLessEqual(size, _CHUNK_SIZE_SMALL)

    def test_cmd_encodes_index(self):
        """Chunk index embedded in bits [27:24] above base command."""
        chunks = ScsiDevice._get_frame_chunks(480, 480)
        for i, (cmd, _) in enumerate(chunks):
            expected = _FRAME_CMD_BASE | (i << 24)
            self.assertEqual(cmd, expected, f"Chunk {i}: {cmd:#x} != {expected:#x}")

    def test_last_chunk_may_be_smaller(self):
        chunks = ScsiDevice._get_frame_chunks(320, 320)
        last_size = chunks[-1][1]
        self.assertEqual(last_size, 320 * 320 * 2 - 3 * _CHUNK_SIZE_LARGE)  # 8192


# -- SCSI transport I/O (via injected FakeScsiTransport) --

class TestScsiReadWrite:
    """Device forwards read/write to the injected transport.

    Boundary test: ScsiDevice never talks to the kernel directly — it goes
    through `self._transport`. We assert the device's private `_scsi_read`/
    `_scsi_write` helpers simply delegate and return what the transport gave.
    """

    def test_read_delegates_to_transport(self):
        transport = FakeScsiTransport(reads=[b'\xaa\xbb\xcc'])
        dev = _make_device(transport)
        result = dev._scsi_read(b'\x01\x02\x03', 256)
        assert result == b'\xaa\xbb\xcc'
        assert transport.read_calls == [(b'\x01\x02\x03', 256)]

    def test_read_returns_empty_when_transport_has_nothing(self):
        """With no scripted reads, the transport returns zeros of length."""
        transport = FakeScsiTransport()
        dev = _make_device(transport)
        result = dev._scsi_read(b'\x01', 64)
        assert result == b'\x00' * 64

    def test_write_delegates_to_transport(self):
        """CDB sent to transport is the first 16 bytes of the header (CRC excluded)."""
        transport = FakeScsiTransport()
        dev = _make_device(transport)
        header = ScsiDevice._build_header(0x101F5, 0x10000)
        assert dev._scsi_write(header, b'\x00' * 100) is True
        assert transport.sends == [(header[:16], b'\x00' * 100)]

    def test_write_passes_exact_bytes(self):
        transport = FakeScsiTransport()
        dev = _make_device(transport)
        header = ScsiDevice._build_header(0x101F5, 10)
        payload = b'\xDE\xAD\xBE\xEF' + b'\x00' * 6
        dev._scsi_write(header, payload)
        assert transport.sends[0] == (header[:16], payload)


# -- Init device (boot signature handling) --

class TestInitDevice:
    """`_init_device()` polls until display leaves boot state, then inits."""

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_sends_poll_then_init(self, _sleep):
        """Single poll returns ready; device sends exactly one init write."""
        transport = FakeScsiTransport(reads=[b'\x64' + b'\x00' * 0xE100])  # FBL=100, no boot sig
        dev = _make_device(transport)
        dev._init_device()

        assert len(transport.read_calls) == 1
        assert transport.read_calls[0][1] == 0xE100  # poll length
        assert len(transport.sends) == 1
        assert len(transport.sends[0][1]) == 0xE100  # init payload length

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_post_init_delay(self, mock_sleep):
        """Last sleep is the post-init delay, regardless of prior sleeps."""
        transport = FakeScsiTransport(reads=[b'\x64' + b'\x00' * 15])
        dev = _make_device(transport)
        dev._init_device()
        mock_sleep.assert_called_with(_POST_INIT_DELAY)

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_boot_signature_waits_and_retries(self, mock_sleep):
        """Boot sig on first poll → wait+re-poll; ready response ends the loop."""
        booting = b'\x00' * 4 + _BOOT_SIGNATURE + b'\x00' * 8
        ready = b'\x64' + b'\x00' * 15
        transport = FakeScsiTransport(reads=[booting, ready])
        dev = _make_device(transport)

        dev._init_device()

        assert len(transport.read_calls) == 2
        sleeps = [c[0][0] for c in mock_sleep.call_args_list]
        assert sleeps == [_BOOT_WAIT_SECONDS, _POST_INIT_DELAY]

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_boot_signature_max_retries(self, _sleep):
        """Device stays in boot state → give up after _BOOT_MAX_RETRIES polls."""
        booting = b'\x00' * 4 + _BOOT_SIGNATURE + b'\x00' * 8
        transport = FakeScsiTransport(reads=[booting] * _BOOT_MAX_RETRIES)
        dev = _make_device(transport)

        dev._init_device()

        assert len(transport.read_calls) == _BOOT_MAX_RETRIES
        # Still sends init even after max retries (best effort)
        assert len(transport.sends) == 1

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_empty_poll_response_falls_back_to_registry(self, _sleep):
        """Empty poll falls back to registry FBL for the device's VID/PID."""
        transport = FakeScsiTransport(reads=[b''])
        dev = _make_device(transport)
        fbl, _resp = dev._init_device()
        # Registry FBL for 0x0402:0x3922 resolves to 100 (320×320)
        assert fbl == 100
        # Init write still happens (best effort)
        assert len(transport.sends) == 1

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_short_poll_response_no_wait(self, mock_sleep):
        """Poll response < 8 bytes skips boot-sig check."""
        transport = FakeScsiTransport(reads=[b'\x64\x00\x00\x00'])
        dev = _make_device(transport)
        dev._init_device()
        assert len(transport.read_calls) == 1
        mock_sleep.assert_called_once_with(_POST_INIT_DELAY)

    @patch('trcc.legacy.adapters.device.scsi.time.sleep')
    def test_boot_then_ready_on_second_poll(self, mock_sleep):
        """Boot sig first, ready second — exactly one boot-wait + one post-init."""
        booting = b'\x00' * 4 + _BOOT_SIGNATURE + b'\x00' * 8
        ready = b'\x64' + b'\x00' * 15
        transport = FakeScsiTransport(reads=[booting, ready])
        dev = _make_device(transport)

        dev._init_device()

        assert len(transport.read_calls) == 2
        sleeps = [c[0][0] for c in mock_sleep.call_args_list]
        assert sleeps == [_BOOT_WAIT_SECONDS, _POST_INIT_DELAY]


# -- Send frame (chunk counting) --

class TestSendFrame:
    """`_send_frame_data` splits image into chunks sized per resolution."""

    def test_sends_all_chunks_320x320(self):
        """320×320 (≥76,800 px) → 64KiB chunks → 320×320×2 bytes = 4 chunks."""
        transport = FakeScsiTransport()
        dev = _make_device(transport, width=320, height=320)
        data = b'\x00' * (320 * 320 * 2)
        dev._send_frame_data(data)
        assert len(transport.sends) == 4

    def test_pads_short_data(self):
        """Short data is zero-padded up to full image size."""
        transport = FakeScsiTransport()
        dev = _make_device(transport, width=320, height=320)
        dev._send_frame_data(b'\x00' * 100)
        total_sent = sum(len(data) for _cdb, data in transport.sends)
        assert total_sent == 320 * 320 * 2

    def test_custom_resolution_480x480(self):
        """480×480 → 64KiB chunks → 480×480×2 bytes = 8 chunks."""
        transport = FakeScsiTransport()
        dev = _make_device(transport, width=480, height=480)
        data = b'\x00' * (480 * 480 * 2)
        dev._send_frame_data(data)
        assert len(transport.sends) == 8

    def test_small_display_uses_small_chunks(self):
        """≤320×240 (76,800 px) uses 0xE100 chunks (USBLCD.exe Mode 1/2)."""
        transport = FakeScsiTransport()
        dev = _make_device(transport, width=320, height=240)
        data = b'\x00' * (320 * 240 * 2)
        dev._send_frame_data(data)
        # 320×240×2 = 153,600 bytes; ceil(153600 / 0xE100) = 3 chunks
        assert len(transport.sends) == 3


# -- find_lcd_devices --

class TestFindLCDDevices:

    def test_returns_device_dicts(self, fake_detect):
        dev = MagicMock()
        dev.scsi_device = '/dev/sg0'
        dev.vendor_name = 'Thermalright'
        dev.product_name = 'LCD'
        dev.model = 'USBLCD'
        dev.button_image = 'btn.png'
        dev.vid = 0x87CD
        dev.pid = 0x70DB
        dev.protocol = 'scsi'
        dev.device_type = 1
        fake_detect.return_value = [dev]

        devices = find_lcd_devices(detect_fn=fake_detect)
        assert len(devices) == 1
        assert devices[0]['name'] == 'Thermalright LCD'
        assert devices[0]['path'] == '/dev/sg0'
        assert devices[0]['resolution'] == (0, 0)
        assert devices[0]['device_index'] == 0

    def test_skips_devices_without_scsi(self, fake_detect):
        dev = MagicMock()
        dev.scsi_device = None
        dev.protocol = 'scsi'
        dev.device_type = 1
        # Phase 9: device_config_key formats vid/pid as hex — give them ints.
        dev.vid = 0
        dev.pid = 0
        fake_detect.return_value = [dev]
        assert find_lcd_devices(detect_fn=fake_detect) == []

    def test_driver_error_uses_unresolved_resolution(self, fake_detect):
        dev = MagicMock()
        dev.scsi_device = '/dev/sg0'
        dev.vendor_name = 'Test'
        dev.product_name = 'LCD'
        dev.model = 'X'
        dev.button_image = None
        dev.vid = 1
        dev.pid = 2
        dev.protocol = 'scsi'
        dev.device_type = 1
        fake_detect.return_value = [dev]

        devices = find_lcd_devices(detect_fn=fake_detect)
        assert devices[0]['resolution'] == (0, 0)


# ---------------------------------------------------------------------------
# Diagnose profile tests — driven by tools/diagnose.py via TRCC_DIAGNOSE_* env vars
# ---------------------------------------------------------------------------

@patch('trcc.legacy.adapters.device.scsi.time.sleep')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_write')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_read')
def test_scsi_handshake_profile(mock_read, mock_write, mock_sleep, device_vid, device_pid, device_pm):
    """Handshake succeeds for the device profile from trcc report."""
    from trcc.legacy.core.models import SCSI_DEVICES, fbl_to_resolution
    entry = SCSI_DEVICES.get((device_vid, device_pid))
    if entry is None:
        pytest.skip(
            f"Device {device_vid:04X}:{device_pid:04X} not in SCSI_DEVICES — "
            "wrong protocol in report or unknown device"
        )
    mock_read.return_value = bytes([device_pm]) + b'\x00' * 15
    sd = ScsiDevice('/dev/sg0', device_vid, device_pid)
    result = sd.handshake()
    assert result is not None
    assert (sd.width, sd.height) == fbl_to_resolution(device_pm)


@patch('trcc.legacy.adapters.device.scsi.time.sleep')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_write')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_read')
def test_scsi_send_frame_profile(mock_read, mock_write, mock_sleep, device_vid, device_pid, device_pm):
    """Frame send succeeds for the device profile from trcc report."""
    from trcc.legacy.core.models import SCSI_DEVICES
    entry = SCSI_DEVICES.get((device_vid, device_pid))
    if entry is None:
        pytest.skip(
            f"Device {device_vid:04X}:{device_pid:04X} not in SCSI_DEVICES — "
            "wrong protocol in report or unknown device"
        )
    mock_read.return_value = bytes([device_pm]) + b'\x00' * 15
    sd = ScsiDevice('/dev/sg0', device_vid, device_pid)
    sd.handshake()
    result = sd.send_frame(b'\x00' * (sd.width * sd.height * 2))
    assert result is True


# ---------------------------------------------------------------------------
# All-devices profile tests — parametrised over every entry in SCSI_DEVICES
# ---------------------------------------------------------------------------

from trcc.legacy.core.models import FBL_TO_RESOLUTION, SCSI_DEVICES  # noqa: E402

_SCSI_PARAMS = [
    pytest.param(vid, pid, entry.fbl, id=f"{vid:04X}:{pid:04X}")
    for (vid, pid), entry in SCSI_DEVICES.items()
]


@pytest.mark.parametrize("vid,pid,fbl", _SCSI_PARAMS)
@patch('trcc.legacy.adapters.device.scsi.time.sleep')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_write')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_read')
def test_scsi_handshake_all_devices(mock_read, mock_write, mock_sleep, vid, pid, fbl):
    """Handshake resolves correct resolution for every SCSI device in the registry."""
    mock_read.return_value = bytes([fbl]) + b'\x00' * 15
    sd = ScsiDevice('/dev/sg0', vid, pid)
    result = sd.handshake()
    assert result is not None
    assert (sd.width, sd.height) == FBL_TO_RESOLUTION[fbl]


@pytest.mark.parametrize("vid,pid,fbl", _SCSI_PARAMS)
@patch('trcc.legacy.adapters.device.scsi.time.sleep')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_write')
@patch('trcc.legacy.adapters.device.scsi.ScsiDevice._scsi_read')
def test_scsi_send_frame_all_devices(mock_read, mock_write, mock_sleep, vid, pid, fbl):
    """Frame send works for every SCSI device in the registry."""
    mock_read.return_value = bytes([fbl]) + b'\x00' * 15
    sd = ScsiDevice('/dev/sg0', vid, pid)
    sd.handshake()
    w, h = FBL_TO_RESOLUTION[fbl]
    result = sd.send_frame(b'\x00' * (w * h * 2))
    assert result is True


if __name__ == '__main__':
    unittest.main()
