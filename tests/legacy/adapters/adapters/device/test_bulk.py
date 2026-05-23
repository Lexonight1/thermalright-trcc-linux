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

# Phase 9: load factory first so bulk_protocol is fully imported (factory.py
# imports BulkProtocol at module bottom; without this, direct imports of
# bulk_protocol hit a partially-initialized module).
import os
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

import trcc.legacy.adapters.device.factory  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.legacy.adapters.device.bulk import (
    _HANDSHAKE_PAYLOAD,
    _HANDSHAKE_READ_SIZE,
    _HANDSHAKE_TIMEOUT_MS,
    BulkDevice,
)
from trcc.legacy.core.models import HandshakeResult


class _FakeUSBError(Exception):
    """Stand-in for usb.core.USBError — real class needed for except clauses."""

    def __init__(self, msg: str = "", errno: int | None = None):
        super().__init__(msg)
        self.errno = errno


def _make_handshake_response(pm: int = 100, sub: int = 0, length: int = 1024,
                              model_name: bytes = b'') -> bytes:
    """Build a fake handshake response with PM at byte[24], SUB at byte[36],
    and optional ASCII model code at offset 4-12."""
    resp = bytearray(length)
    if length > 24:
        resp[24] = pm
    if length > 36:
        resp[36] = sub
    if model_name and length >= 12:
        slot = model_name[:8].ljust(8, b'\x00')
        resp[4:12] = slot
    return bytes(resp)


class TestBulkParseModelName(unittest.TestCase):
    """_parse_model_name() — extracts ASCII handshake fingerprint."""

    def test_parses_sscrm_v3(self):
        """Reporter #149 raw bytes carry 'SSCRM-V3' at offset 4-12."""
        from trcc.legacy.adapters.device.bulk import _parse_model_name
        hex_str = ('12345678535343524d2d56330000000000000000'
                   '904b909540000000200000000f00000003000000')
        self.assertEqual(_parse_model_name(bytes.fromhex(hex_str), 4, 8),
                          'SSCRM-V3')

    def test_short_response_returns_empty(self):
        from trcc.legacy.adapters.device.bulk import _parse_model_name
        self.assertEqual(_parse_model_name(b'abc', 4, 8), '')

    def test_pure_nul_returns_empty(self):
        from trcc.legacy.adapters.device.bulk import _parse_model_name
        self.assertEqual(_parse_model_name(bytes(16), 4, 8), '')

    def test_stops_at_first_nul(self):
        from trcc.legacy.adapters.device.bulk import _parse_model_name
        # offset 4-12 = b'ABCD\x00EFGH' → 'ABCD'
        self.assertEqual(
            _parse_model_name(b'\x00\x00\x00\x00ABCD\x00EFGH', 4, 8), 'ABCD')

    def test_strips_non_printable(self):
        from trcc.legacy.adapters.device.bulk import _parse_model_name
        # ASCII control chars at offset 4-12 — only the printable 'AB' survives
        self.assertEqual(
            _parse_model_name(b'\x00\x00\x00\x00AB\x01\x02CDEF', 4, 8), 'ABCDEF')


class TestBulkHandshakeModelName(unittest.TestCase):
    """Bulk handshake extracts model_name into HandshakeResult."""

    def test_handshake_returns_model_name(self):
        from trcc.legacy.adapters.device.bulk import BulkDevice
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._dev = MagicMock()
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()
        bd._ep_in.read.return_value = _make_handshake_response(
            pm=1, sub=1, model_name=b'SSCRM-V3')
        result = bd.handshake()
        self.assertEqual(result.model_name, 'SSCRM-V3')

    def test_handshake_empty_model_name_when_slot_zero(self):
        from trcc.legacy.adapters.device.bulk import BulkDevice
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._dev = MagicMock()
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()
        bd._ep_in.read.return_value = _make_handshake_response(pm=1, sub=1)
        result = bd.handshake()
        self.assertEqual(result.model_name, '')


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
    """Test _open() USB enumeration.

    Patches the actual seams used by ``open_usb_device`` and
    ``BulkFrameDevice._open``: ``_pyusb_find.find`` for device discovery
    and ``usb.util`` for endpoint / interface helpers. ``usb.core.USBError``
    is swapped for ``_FakeUSBError`` for the duration of each error test.

    Earlier tests replaced ``sys.modules['usb']`` with a MagicMock, which
    broke pyusb's internal imports (``import usb.backend.libusb1``) when
    the real ``_pyusb_find._find`` (bound at module load) was invoked.
    Patching the seam directly avoids the package-vs-mock collision.
    """

    @staticmethod
    def _patch_open(*, find_dev, usb_util_mock=None):
        """Return a context manager patching find + usb.util together."""
        from contextlib import ExitStack

        import trcc.legacy.adapters.device._pyusb_find as pyusb_find
        import trcc.legacy.adapters.device._usb_helpers as helpers

        util = usb_util_mock or MagicMock()
        if not hasattr(util, 'ENDPOINT_OUT'):
            util.ENDPOINT_OUT = 0x00
            util.ENDPOINT_IN = 0x80
            util.ENDPOINT_TYPE_BULK = 0x02

        stack = ExitStack()
        stack.enter_context(patch.object(pyusb_find, 'find', return_value=find_dev))
        stack.enter_context(patch.object(helpers, 'usb_util', util, create=True))
        # ``open_usb_device`` does ``import usb.util`` inside the function;
        # patch the actual binding at the call sites.
        stack.enter_context(patch('usb.util.claim_interface', util.claim_interface))
        stack.enter_context(patch('usb.util.find_descriptor', util.find_descriptor))
        stack.enter_context(patch('usb.util.endpoint_direction', util.endpoint_direction))
        stack.enter_context(patch('usb.util.endpoint_type', util.endpoint_type))
        stack.enter_context(patch('usb.core.USBError', _FakeUSBError))
        return stack, util

    def test_open_success(self):
        """Successful USB open: find device, detach drivers, find endpoints."""
        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.bNumInterfaces = 1
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_dev.is_kernel_driver_active.return_value = False
        mock_intf = MagicMock()
        mock_intf.bInterfaceClass = 255
        mock_intf.bInterfaceNumber = 0
        mock_cfg.__iter__ = MagicMock(return_value=iter([mock_intf]))
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)

        ep_out = MagicMock()
        ep_out.bEndpointAddress = 0x01
        ep_in = MagicMock()
        ep_in.bEndpointAddress = 0x81

        util = MagicMock()
        util.ENDPOINT_OUT = 0x00
        util.ENDPOINT_IN = 0x80
        util.ENDPOINT_TYPE_BULK = 0x02
        util.find_descriptor.side_effect = [ep_out, ep_in]
        util.endpoint_direction.side_effect = lambda addr: addr & 0x80
        util.endpoint_type.return_value = util.ENDPOINT_TYPE_BULK

        stack, _ = self._patch_open(find_dev=mock_dev, usb_util_mock=util)
        with stack:
            bd = BulkDevice(0x87AD, 0x70DB)
            bd._open()

        self.assertEqual(bd._dev, mock_dev)
        self.assertEqual(bd._ep_out, ep_out)
        self.assertEqual(bd._ep_in, ep_in)

    # The pre-10E ``test_open_disables_autosuspend`` was deleted with the
    # ``_disable_autosuspend`` runtime override.  Autosuspend is now tuned
    # by the udev rule (``power/autosuspend_delay_ms=10000``) — see the
    # tests in tests/adapters/system/linux/test_platform.py for that path.

    def test_open_device_not_found(self):
        stack, _ = self._patch_open(find_dev=None)
        with stack:
            bd = BulkDevice(0x87AD, 0x70DB)
            with self.assertRaises(RuntimeError):
                bd._open()

    def test_open_no_endpoints(self):
        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.bNumInterfaces = 0
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_intf = MagicMock()
        mock_intf.bInterfaceClass = 255
        mock_intf.bInterfaceNumber = 0
        mock_cfg.__iter__ = MagicMock(return_value=iter([mock_intf]))
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)

        util = MagicMock()
        util.ENDPOINT_OUT = 0x00
        util.ENDPOINT_IN = 0x80
        util.ENDPOINT_TYPE_BULK = 0x02
        util.find_descriptor.return_value = None

        stack, _ = self._patch_open(find_dev=mock_dev, usb_util_mock=util)
        with stack:
            bd = BulkDevice(0x87AD, 0x70DB)
            with self.assertRaises(RuntimeError):
                bd._open()

    def test_open_selinux_blocked_detach_error_sets_flag(self):
        """detach_kernel_driver raises USBError + driver stays active →
        SELinux-flavored claim_interface EBUSY error."""
        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_intf = MagicMock()
        mock_intf.bInterfaceClass = 255
        mock_intf.bInterfaceNumber = 0
        mock_cfg.__iter__ = MagicMock(return_value=iter([mock_intf]))

        # Kernel driver is active, detach raises USBError, driver stays active
        mock_dev.is_kernel_driver_active.return_value = True
        mock_dev.detach_kernel_driver.side_effect = _FakeUSBError("Resource busy", errno=16)

        util = MagicMock()
        util.claim_interface.side_effect = _FakeUSBError("Resource busy", errno=16)

        stack, _ = self._patch_open(find_dev=mock_dev, usb_util_mock=util)
        with stack:
            bd = BulkDevice(0x87AD, 0x70DB)
            with self.assertRaises(RuntimeError) as ctx:
                bd._open()
        self.assertIn("SELinux", str(ctx.exception))
        self.assertIn("setup-selinux", str(ctx.exception))

    def test_open_claim_ebusy_raises_without_reset(self):
        """claim_interface EBUSY → RuntimeError, no device reset (issue #82)."""
        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_dev.is_kernel_driver_active.return_value = False
        mock_intf = MagicMock()
        mock_intf.bInterfaceClass = 255
        mock_intf.bInterfaceNumber = 0
        mock_cfg.__iter__ = MagicMock(return_value=iter([mock_intf]))

        util = MagicMock()
        util.claim_interface.side_effect = _FakeUSBError("Resource busy", errno=16)

        stack, _ = self._patch_open(find_dev=mock_dev, usb_util_mock=util)
        with stack:
            bd = BulkDevice(0x87AD, 0x70DB)
            with self.assertRaises(RuntimeError) as ctx:
                bd._open()

        mock_dev.reset.assert_not_called()
        self.assertIn("in use by another process", str(ctx.exception))


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
        self.assertEqual(result.model_id, 72)  # bulk default FBL, not PM=FBL
        self.assertEqual(bd.pm, 1)
        self.assertEqual(bd.sub_type, 0)
        self.assertEqual(bd.width, 480)
        self.assertEqual(bd.height, 480)

    def test_handshake_pm1_sub1_grandvision_fbl72(self):
        """PM=1 SUB=1 (GrandVision 360, issue #78) → FBL=72, 480x480."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=1, sub=1)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (480, 480))
        self.assertEqual(result.model_id, 72)
        self.assertTrue(bd.use_jpeg)

    def test_handshake_pm5_mjolnir_320x240(self):
        """PM=5 (Mjolnir Vision) → FBL=50 → 320x240."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=5)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.resolution, (320, 240))
        self.assertEqual(bd.pm, 5)
        self.assertTrue(bd.use_jpeg)

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

    def test_handshake_use_jpeg_default(self):
        """All bulk PMs use JPEG (cmd=2) except PM=32."""
        bd = self._setup_device()
        for pm in (1, 5, 7, 9, 10, 11, 12, 64, 65, 99):
            resp = _make_handshake_response(pm=pm)
            bd._ep_in.read.return_value = resp
            bd.handshake()
            self.assertTrue(bd.use_jpeg, f"PM={pm} should use JPEG")

    def test_handshake_pm32_uses_rgb565(self):
        """PM=32 is the only bulk PM that uses RGB565 (cmd=3)."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=32)
        bd._ep_in.read.return_value = resp
        bd.handshake()
        self.assertFalse(bd.use_jpeg)

    def test_handshake_pm_byte_and_sub_byte_on_result(self):
        """HandshakeResult carries raw PM + SUB for button image lookup (#69)."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=7, sub=1)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.pm_byte, 7)
        self.assertEqual(result.sub_byte, 1)
        # model_id is FBL (64), NOT the raw PM
        self.assertEqual(result.model_id, 64)

    def test_handshake_pm_byte_differs_from_fbl(self):
        """PM=7 → FBL=64 — pm_byte must be the raw PM, not FBL (#69)."""
        bd = self._setup_device()
        resp = _make_handshake_response(pm=7, sub=0)
        bd._ep_in.read.return_value = resp

        result = bd.handshake()

        self.assertEqual(result.pm_byte, 7)
        self.assertNotEqual(result.pm_byte, result.model_id)

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

    def _setup_device(self, width=320, height=320, use_jpeg=True):
        bd = BulkDevice(0x87AD, 0x70DB)
        bd._dev = MagicMock()
        bd._ep_out = MagicMock()
        bd._ep_in = MagicMock()
        bd.width = width
        bd.height = height
        bd.pm = 100
        bd.use_jpeg = use_jpeg
        return bd

    def test_send_frame_jpeg_header(self):
        """JPEG mode (default): cmd=2 in header."""
        bd = self._setup_device(width=480, height=480, use_jpeg=True)
        data = b'\x00' * 1000
        bd.send_frame(data)

        # Single write: 64-byte header + payload
        frame = bd._ep_out.write.call_args_list[0][0][0]
        self.assertEqual(len(frame), 64 + 1000)
        header = frame[:64]
        self.assertEqual(header[0:4], b'\x12\x34\x56\x78')
        # Cmd = 2 (JPEG)
        self.assertEqual(struct.unpack_from("<I", header, 4)[0], 2)
        self.assertEqual(struct.unpack_from("<I", header, 8)[0], 480)
        self.assertEqual(struct.unpack_from("<I", header, 12)[0], 480)
        self.assertEqual(struct.unpack_from("<I", header, 56)[0], 2)
        self.assertEqual(struct.unpack_from("<I", header, 60)[0], 1000)

    def test_send_frame_rgb565_header(self):
        """RGB565 mode (PM=32): cmd=3 in header."""
        bd = self._setup_device(width=320, height=320, use_jpeg=False)
        data = b'\x00' * 1000
        bd.send_frame(data)

        # Single write: 64-byte header + payload
        frame = bd._ep_out.write.call_args_list[0][0][0]
        self.assertEqual(len(frame), 64 + 1000)
        header = frame[:64]
        self.assertEqual(header[0:4], b'\x12\x34\x56\x78')
        # Cmd = 3 (raw RGB565)
        self.assertEqual(struct.unpack_from("<I", header, 4)[0], 3)
        self.assertEqual(struct.unpack_from("<I", header, 8)[0], 320)
        self.assertEqual(struct.unpack_from("<I", header, 12)[0], 320)
        self.assertEqual(struct.unpack_from("<I", header, 56)[0], 2)
        self.assertEqual(struct.unpack_from("<I", header, 60)[0], 1000)

    def test_send_frame_small_payload_single_chunk(self):
        """Small payloads (< 16 KiB) are sent in one chunk."""
        bd = self._setup_device()
        data = b'\xAB\xCD' * 500  # 1000 bytes — well under 16 KiB
        bd.send_frame(data)

        calls = bd._ep_out.write.call_args_list
        # Single chunk (header + payload), no ZLP (1064 % 512 != 0)
        self.assertEqual(len(calls), 1)
        frame = calls[0][0][0]
        self.assertEqual(frame[64:], data)

    def test_send_frame_large_payload_chunked(self):
        """Large payloads are split into 16 KiB chunks."""
        bd = self._setup_device()
        # 3 full 16 KiB chunks + remainder
        data = b'\xAB' * (3 * 16 * 1024 + 500)
        bd.send_frame(data)

        calls = bd._ep_out.write.call_args_list
        # header (64) + data split into 16 KiB chunks
        total = 64 + len(data)
        import math
        expected_chunks = math.ceil(total / (16 * 1024))
        self.assertEqual(len(calls), expected_chunks)
        # Reassemble and verify header + payload intact
        reassembled = b''.join(c[0][0] for c in calls)
        self.assertEqual(reassembled[64:], data)

    def test_send_frame_zlp_when_512_aligned(self):
        """ZLP sent only when total frame size is 512-aligned."""
        bd = self._setup_device()
        # 512 - 64 = 448 bytes of payload → total 512, 512-aligned
        data = b'\x00' * 448
        bd.send_frame(data)

        calls = bd._ep_out.write.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0][0], b"")

    def test_send_frame_no_zlp_when_not_aligned(self):
        """No ZLP when total frame size is not 512-aligned."""
        bd = self._setup_device()
        data = b'\x00' * 100
        bd.send_frame(data)

        # Only 1 write (no ZLP needed)
        calls = bd._ep_out.write.call_args_list
        self.assertEqual(len(calls), 1)

    def test_send_frame_returns_true_on_success(self):
        bd = self._setup_device()
        result = bd.send_frame(b'\x00' * 100)
        self.assertTrue(result)

    def test_send_frame_returns_false_on_persistent_error(self):
        bd = self._setup_device()
        bd.close = MagicMock()
        bd.handshake = MagicMock()
        bd._ep_out.write.side_effect = OSError("USB error")
        result = bd.send_frame(b'\x00' * 100)
        self.assertFalse(result)

    def test_send_frame_retries_on_first_error(self):
        """First send fails, reconnect + retry succeeds."""
        bd = self._setup_device()
        bd.close = MagicMock()
        bd.handshake = MagicMock()
        call_count = 0

        def fail_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("USB disconnect")

        bd._ep_out.write.side_effect = fail_once
        result = bd.send_frame(b'\x00' * 100)
        self.assertTrue(result)
        bd.close.assert_called_once()
        bd.handshake.assert_called_once()

    def test_send_frame_retry_calls_close_then_handshake(self):
        """On USB error, close() is called before handshake()."""
        bd = self._setup_device()
        call_order = []
        bd.close = MagicMock(side_effect=lambda: call_order.append('close'))
        bd.handshake = MagicMock(side_effect=lambda: call_order.append('handshake'))
        bd._ep_out.write.side_effect = [OSError("USB error"), None]
        bd.send_frame(b'\x00' * 100)
        self.assertEqual(call_order, ['close', 'handshake'])

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
        from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol
        from trcc.legacy.adapters.device.factory import DeviceProtocolFactory

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
        from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol

        proto = BulkProtocol(0x87AD, 0x70DB)
        info = proto.get_info()
        self.assertEqual(info.protocol, "bulk")
        self.assertEqual(info.device_type, 4)
        self.assertIn("Bulk", info.protocol_display)
        proto.close()

    def test_is_not_led(self):
        from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol

        proto = BulkProtocol(0x87AD, 0x70DB)
        self.assertFalse(proto.is_led)
        proto.close()


class TestBulkDeviceDetection(unittest.TestCase):
    """Test that 87AD:70DB is detected as bulk protocol."""

    def test_in_bulk_devices_registry(self):
        from trcc.legacy.adapters.device.detector import _BULK_DEVICES

        self.assertIn((0x87AD, 0x70DB), _BULK_DEVICES)
        info = _BULK_DEVICES[(0x87AD, 0x70DB)]
        self.assertEqual(info.protocol, "bulk")
        self.assertEqual(info.implementation, "bulk_usblcdnew")
        self.assertEqual(info.device_type, 4)

    def test_not_in_scsi_devices(self):
        from trcc.legacy.adapters.device.detector import KNOWN_DEVICES

        self.assertNotIn((0x87AD, 0x70DB), KNOWN_DEVICES)

    def test_not_in_led_devices(self):
        from trcc.legacy.adapters.device.detector import _LED_DEVICES

        self.assertNotIn((0x87AD, 0x70DB), _LED_DEVICES)

    def test_in_all_devices(self):
        from trcc.legacy.adapters.device.detector import _get_all_devices

        all_devs = _get_all_devices()
        self.assertIn((0x87AD, 0x70DB), all_devs)

    @unittest.skip("Phase 9: find_lcd_devices removed from scsi.py — discovery moved to platform.create_detect_fn() / DeviceDetector.")
    def test_find_lcd_devices_bulk(self):
        """find_lcd_devices returns bulk device with correct protocol."""
        from trcc.legacy.adapters.device.detector import DetectedDevice

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

        from trcc.legacy.adapters.device.scsi import find_lcd_devices
        devices = find_lcd_devices(detect_fn=lambda: [fake_dev])

        self.assertEqual(len(devices), 1)
        d = devices[0]
        self.assertEqual(d['protocol'], 'bulk')
        self.assertEqual(d['path'], 'bulk:87ad:70db')
        self.assertEqual(d['vid'], 0x87AD)
        self.assertEqual(d['pid'], 0x70DB)
        self.assertEqual(d['device_type'], 4)


# ---------------------------------------------------------------------------
# Diagnose-driven tests — use device profile fixtures from conftest.py
# Run normally with defaults; diagnose tool injects real VID/PID/PM from report
# ---------------------------------------------------------------------------

def _make_opened_device(vid: int, pid: int, pm: int = 100, sub: int = 0) -> BulkDevice:
    bd = BulkDevice(vid, pid)
    bd._dev = MagicMock()
    bd._ep_out = MagicMock()
    bd._ep_in = MagicMock()
    bd.pm = pm
    bd.sub_type = sub
    return bd


def test_bulk_handshake_profile(device_vid, device_pid, device_pm, device_sub):
    """Handshake succeeds for the device profile from trcc report."""
    bd = _make_opened_device(device_vid, device_pid)
    bd._ep_in.read.return_value = _make_handshake_response(pm=device_pm, sub=device_sub)

    result = bd.handshake()

    assert result is not None
    assert bd.pm == device_pm
    assert bd.sub_type == device_sub
    assert bd.width > 0
    assert bd.height > 0


def test_bulk_send_frame_profile(device_vid, device_pid, device_pm, device_sub):
    """Frame send succeeds for the device profile from trcc report."""
    import pytest as _pytest

    from trcc.legacy.adapters.device.bulk import _BULK_RGB565_PMS, _bulk_resolution
    w, h = _bulk_resolution(device_pm, device_sub)
    if w == 0 or h == 0:
        _pytest.skip(f"No resolution for PM={device_pm} SUB={device_sub}")

    bd = _make_opened_device(device_vid, device_pid, pm=device_pm, sub=device_sub)
    bd.width = w
    bd.height = h
    bd.use_jpeg = device_pm not in _BULK_RGB565_PMS

    result = bd.send_frame(b'\x00' * 1000)
    assert result is True


def test_bulk_open_ebusy_no_reset(device_vid, device_pid):
    """EBUSY on claim_interface raises RuntimeError — no device reset, no logo flash.

    Issue #82: old code called dev.reset() on EBUSY causing firmware restart
    and boot logo flash on bulk devices. Fix: raise immediately instead.
    """
    import trcc.legacy.adapters.device._pyusb_find as pyusb_find

    mock_dev = MagicMock()
    mock_cfg = MagicMock()
    mock_dev.get_active_configuration.return_value = mock_cfg
    mock_dev.is_kernel_driver_active.return_value = False
    mock_intf = MagicMock()
    mock_intf.bInterfaceClass = 255
    mock_intf.bInterfaceNumber = 0
    mock_cfg.__iter__ = MagicMock(return_value=iter([mock_intf]))

    util = MagicMock()
    util.claim_interface.side_effect = _FakeUSBError("busy", errno=16)

    with (
        patch.object(pyusb_find, 'find', return_value=mock_dev),
        patch('usb.util.claim_interface', util.claim_interface),
        patch('usb.core.USBError', _FakeUSBError),
    ):
        bd = BulkDevice(device_vid, device_pid)
        with pytest.raises(RuntimeError) as exc_info:
            bd._open()

    mock_dev.reset.assert_not_called()  # No reset → no logo flash
    assert "in use by another process" in str(exc_info.value)


if __name__ == '__main__':
    unittest.main()
