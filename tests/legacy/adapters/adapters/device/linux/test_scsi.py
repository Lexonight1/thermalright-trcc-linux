"""Tests for Linux SG_IO SCSI transport (mocked — no real device needed)."""
from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.adapters.device.linux.scsi import (
    _SG_DXFER_FROM_DEV,
    _SG_DXFER_TO_DEV,
    _SG_IO,
    LinuxScsiTransport,
    _SgIoHdr,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def transport() -> LinuxScsiTransport:
    """Closed LinuxScsiTransport pointing at a fake device path."""
    return LinuxScsiTransport('/dev/sg0')


@pytest.fixture
def open_transport(transport: LinuxScsiTransport):
    """LinuxScsiTransport with a fake open file descriptor."""
    transport._fd = 42
    return transport


# ── _SgIoHdr structure ────────────────────────────────────────────────────────


class TestSgIoHdr:
    """Verify the SG_IO header structure layout matches the kernel ABI."""

    def test_has_required_fields(self):
        hdr = _SgIoHdr()
        for field in ('interface_id', 'dxfer_direction', 'cmd_len', 'dxfer_len',
                      'dxferp', 'cmdp', 'sbp', 'timeout', 'status', 'resid'):
            assert hasattr(hdr, field), f"missing field: {field}"

    def test_interface_id_accepts_S_byte(self):
        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        assert hdr.interface_id == ord('S')

    def test_sg_io_ioctl_constant(self):
        assert _SG_IO == 0x2285

    def test_dxfer_direction_constants(self):
        assert _SG_DXFER_TO_DEV == -2
        assert _SG_DXFER_FROM_DEV == -3

    def test_size_is_nonzero(self):
        assert ctypes.sizeof(_SgIoHdr) > 0


# ── LinuxScsiTransport — lifecycle ────────────────────────────────────────────


class TestLinuxScsiTransportInit:

    def test_stores_device_path(self, transport):
        assert transport._path == '/dev/sg0'

    def test_fd_starts_closed(self, transport):
        assert transport._fd is None

    def test_write_buf_cache_starts_empty(self, transport):
        assert transport._write_bufs == {}


class TestLinuxScsiTransportOpen:

    def test_open_calls_os_open_with_rdwr_nonblock(self, transport):
        import os
        with patch('os.open', return_value=5) as mock_open:
            result = transport.open()

        mock_open.assert_called_once_with('/dev/sg0', os.O_RDWR | os.O_NONBLOCK)
        assert result is True
        assert transport._fd == 5

    def test_open_returns_true_when_already_open(self, open_transport):
        with patch('os.open') as mock_open:
            result = open_transport.open()

        mock_open.assert_not_called()
        assert result is True

    def test_open_returns_false_on_oserror(self, transport):
        with patch('os.open', side_effect=OSError("Permission denied")):
            result = transport.open()

        assert result is False
        assert transport._fd is None


class TestLinuxScsiTransportClose:

    def test_close_calls_os_close(self, open_transport):
        with patch('os.close') as mock_close:
            open_transport.close()

        mock_close.assert_called_once_with(42)
        assert open_transport._fd is None

    def test_close_clears_write_buf_cache(self, open_transport):
        open_transport._write_bufs[512] = ('some', 'buffers')
        with patch('os.close'):
            open_transport.close()

        assert open_transport._write_bufs == {}

    def test_close_noop_when_not_open(self, transport):
        transport.close()  # must not raise
        assert transport._fd is None

    def test_close_swallows_oserror(self, open_transport):
        with patch('os.close', side_effect=OSError("bad fd")):
            open_transport.close()  # must not propagate

        assert open_transport._fd is None


class TestLinuxScsiTransportContextManager:

    def test_enter_calls_open(self, transport):
        with patch.object(transport, 'open', return_value=True) as m_open, \
             patch.object(transport, 'close') as m_close:
            with transport:
                m_open.assert_called_once()
            m_close.assert_called_once()

    def test_exit_calls_close_on_exception(self, transport):
        with patch.object(transport, 'open', return_value=True), \
             patch.object(transport, 'close') as m_close:
            try:
                with transport:
                    raise ValueError("test error")
            except ValueError:
                pass
            m_close.assert_called_once()


# ── LinuxScsiTransport — send_cdb ─────────────────────────────────────────────


class TestLinuxScsiTransportSendCdb:

    def test_raises_when_not_open(self, transport):
        with pytest.raises(OSError, match="not open"):
            transport.send_cdb(b'\xef\x01', b'\x00' * 512)

    def test_calls_fcntl_ioctl_with_sg_io(self, open_transport):
        mock_ioctl = MagicMock()
        with patch('fcntl.ioctl', mock_ioctl):
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 512)

        assert mock_ioctl.called
        ioctl_code = mock_ioctl.call_args[0][1]
        assert ioctl_code == _SG_IO, (
            f"send_cdb must use SG_IO (0x2285), got {ioctl_code:#x}"
        )

    def test_uses_to_dev_direction(self, open_transport):
        captured_hdr: list[_SgIoHdr] = []

        def capture_ioctl(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            captured_hdr.append(hdr)

        with patch('fcntl.ioctl', side_effect=capture_ioctl):
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 4)

        assert captured_hdr[0].dxfer_direction == _SG_DXFER_TO_DEV

    def test_returns_true_when_status_zero(self, open_transport):
        def ioctl_ok(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            hdr.status = 0
            ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))

        with patch('fcntl.ioctl', side_effect=ioctl_ok):
            result = open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 4)

        assert result is True

    def test_returns_false_when_status_nonzero(self, open_transport):
        def ioctl_fail(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            hdr.status = 2
            ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))

        with patch('fcntl.ioctl', side_effect=ioctl_fail):
            result = open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 4)

        assert result is False

    def test_caches_write_buffers_per_data_length(self, open_transport):
        with patch('fcntl.ioctl'):
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 512)
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 512)

        assert 512 in open_transport._write_bufs

    def test_different_data_lengths_get_separate_cache_entries(self, open_transport):
        with patch('fcntl.ioctl'):
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 512)
            open_transport.send_cdb(b'\xef' + b'\x00' * 15, b'\x00' * 256)

        assert 512 in open_transport._write_bufs
        assert 256 in open_transport._write_bufs


# ── LinuxScsiTransport — read_cdb ─────────────────────────────────────────────


class TestLinuxScsiTransportReadCdb:

    def test_raises_when_not_open(self, transport):
        with pytest.raises(OSError, match="not open"):
            transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

    def test_calls_fcntl_ioctl_with_sg_io(self, open_transport):
        mock_ioctl = MagicMock()
        with patch('fcntl.ioctl', mock_ioctl):
            open_transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        ioctl_code = mock_ioctl.call_args[0][1]
        assert ioctl_code == _SG_IO, (
            f"read_cdb must use SG_IO (0x2285), got {ioctl_code:#x}"
        )

    def test_uses_from_dev_direction(self, open_transport):
        captured_hdr: list[_SgIoHdr] = []

        def capture_ioctl(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            captured_hdr.append(hdr)

        with patch('fcntl.ioctl', side_effect=capture_ioctl):
            open_transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        assert captured_hdr[0].dxfer_direction == _SG_DXFER_FROM_DEV

    def test_returns_empty_bytes_on_nonzero_status(self, open_transport):
        def ioctl_fail(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            hdr.status = 1
            ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))

        with patch('fcntl.ioctl', side_effect=ioctl_fail):
            result = open_transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        assert result == b''

    def test_respects_resid_for_actual_byte_count(self, open_transport):
        """hdr.resid indicates unread bytes — actual = length - resid."""
        actual_data = b'\x64' + b'\x00' * 63  # FBL=100 in first byte

        def ioctl_partial(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            hdr.status = 0
            hdr.resid = 0  # all 64 bytes read
            # Write actual_data into the dxferp buffer
            ctypes.memmove(hdr.dxferp, actual_data, 64)
            ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))

        with patch('fcntl.ioctl', side_effect=ioctl_partial):
            result = open_transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        assert len(result) == 64
        assert result[0] == 0x64  # FBL=100

    def test_partial_read_via_resid(self, open_transport):
        """When resid=32, only 32 bytes of a 64-byte buffer are returned."""
        def ioctl_partial(fd, code, buf):
            hdr = _SgIoHdr()
            ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))
            hdr.status = 0
            hdr.resid = 32  # 32 bytes unread → actual = 64 - 32 = 32
            ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))

        with patch('fcntl.ioctl', side_effect=ioctl_partial):
            result = open_transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        assert len(result) == 32
