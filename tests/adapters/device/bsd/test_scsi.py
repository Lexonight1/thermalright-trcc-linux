"""Tests for BSD SCSI transport (pyusb USB BOT — mocked, runs on Linux)."""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

MODULE = 'trcc.legacy.adapters.device.bsd.scsi'

# USB BOT constants (must match the transport module)
CBW_SIGNATURE = 0x43425355
CSW_SIZE = 13


def _make_csw(tag: int = 1, status: int = 0) -> bytes:
    """Build a 13-byte Command Status Wrapper for mocking."""
    return struct.pack('<III', 0x53425355, tag, 0) + bytes([status])


class TestBSDScsiTransport:

    def test_init(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        assert t._vid == 0x0402
        assert t._pid == 0x3922
        assert t._dev is None

    def test_send_cdb_fails_when_not_open(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        assert t.send_cdb(b'\xef', b'\x00' * 512) is False

    def test_read_cdb_fails_when_not_open(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        assert t.read_cdb(b'\xef', 512) == b''

    def test_close_noop_when_not_open(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        t.close()  # Should not raise

    def test_context_manager(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        with patch.object(t, 'open') as m_open, \
             patch.object(t, 'close') as m_close:
            with t:
                pass
            m_open.assert_called_once()
            m_close.assert_called_once()

    @staticmethod
    def _make_mock_device(*, kernel_driver_active: bool) -> MagicMock:
        mock_dev = MagicMock()
        mock_dev.is_kernel_driver_active.return_value = kernel_driver_active
        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02
        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x81
        mock_intf = [mock_ep_out, mock_ep_in]
        mock_cfg = MagicMock()
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)
        mock_dev.get_active_configuration.return_value = mock_cfg
        return mock_dev

    def test_open_success(self):
        from trcc.legacy.adapters.device import _pyusb_find
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport

        mock_dev = self._make_mock_device(kernel_driver_active=False)
        with (
            patch.object(_pyusb_find, 'find', return_value=mock_dev),
            patch('usb.util.endpoint_direction', side_effect=lambda a: a & 0x80),
            patch('usb.util.ENDPOINT_OUT', 0x00),
            patch('usb.util.ENDPOINT_IN', 0x80),
        ):
            t = BSDScsiTransport(vid=0x0402, pid=0x3922)
            assert t.open() is True
            assert t._dev is mock_dev
            assert t._ep_out == 0x02
            assert t._ep_in == 0x81

    def test_open_detaches_kernel_driver(self):
        from trcc.legacy.adapters.device import _pyusb_find
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport

        mock_dev = self._make_mock_device(kernel_driver_active=True)
        with (
            patch.object(_pyusb_find, 'find', return_value=mock_dev),
            patch('usb.util.endpoint_direction', side_effect=lambda a: a & 0x80),
            patch('usb.util.ENDPOINT_OUT', 0x00),
            patch('usb.util.ENDPOINT_IN', 0x80),
        ):
            t = BSDScsiTransport(vid=0x0402, pid=0x3922)
            assert t.open() is True
            mock_dev.detach_kernel_driver.assert_called_once_with(0)

    def test_open_fails_device_not_found(self):
        from trcc.legacy.adapters.device import _pyusb_find
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport

        with patch.object(_pyusb_find, 'find', return_value=None):
            t = BSDScsiTransport(vid=0x0402, pid=0x3922)
            assert t.open() is False

    def test_send_cdb_writes_cbw_data_reads_csw(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        csw = _make_csw(tag=1, status=0)
        mock_dev.read.return_value = csw
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        cdb = b'\xef' + b'\x00' * 15
        data = b'\xAA' * 64

        assert t.send_cdb(cdb, data) is True
        assert mock_dev.write.call_count == 2  # CBW + data
        mock_dev.read.assert_called_once()

    def test_send_cdb_returns_false_on_csw_error(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        csw = _make_csw(tag=1, status=1)  # Non-zero status = error
        mock_dev.read.return_value = csw
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        assert t.send_cdb(b'\xef', b'\x00' * 64) is False

    def test_send_cdb_returns_false_on_exception(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        mock_dev.write.side_effect = OSError("USB error")
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        assert t.send_cdb(b'\xef', b'\x00' * 64) is False

    def test_read_cdb_returns_data(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        response_data = b'\x64' + b'\x00' * 63  # FBL=100
        csw = _make_csw(tag=1, status=0)
        mock_dev.read.side_effect = [response_data, csw]
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        result = t.read_cdb(b'\xef', 64)
        assert result == response_data
        assert mock_dev.write.call_count == 1  # CBW only
        assert mock_dev.read.call_count == 2   # data + CSW

    def test_read_cdb_returns_empty_on_csw_error(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        csw = _make_csw(tag=1, status=1)
        mock_dev.read.side_effect = [b'\x00' * 64, csw]
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        assert t.read_cdb(b'\xef', 64) == b''

    @patch('usb.util.dispose_resources')
    def test_close_disposes_and_reattaches(self, mock_dispose):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        t._dev = mock_dev

        t.close()

        mock_dispose.assert_called_once_with(mock_dev)
        mock_dev.attach_kernel_driver.assert_called_once_with(0)
        assert t._dev is None

    def test_send_cdb_skips_data_write_when_empty(self):
        from trcc.legacy.adapters.device.bsd.scsi import BSDScsiTransport
        t = BSDScsiTransport(vid=0x0402, pid=0x3922)
        mock_dev = MagicMock()
        csw = _make_csw(tag=1, status=0)
        mock_dev.read.return_value = csw
        t._dev = mock_dev
        t._ep_out = 0x02
        t._ep_in = 0x81

        assert t.send_cdb(b'\xef', b'') is True
        assert mock_dev.write.call_count == 1  # CBW only, no data
