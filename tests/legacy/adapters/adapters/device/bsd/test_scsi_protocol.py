"""Tests for BSD SCSI protocol (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.legacy.adapters.device.bsd.scsi_protocol'


@pytest.mark.skip(reason="Phase 9: BSDScsiProtocol class removed; BSD now uses unified ScsiProtocol with platform-injected transport via ControllerBuilder. Tests need rewriting.")
class TestBSDScsiProtocol:

    def test_init(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        assert p._vid == 0x0402
        assert p._pid == 0x3922
        assert p._transport is None

    def test_protocol_name(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        assert p.protocol_name == "scsi"

    def test_repr(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        assert "BSDScsiProtocol" in repr(p)
        assert "0x0402" in repr(p)
        assert "0x3922" in repr(p)

    def test_get_info(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        info = p.get_info()
        assert info.protocol == "scsi"
        assert info.active_backend == "pyusb"
        assert "FreeBSD" in info.protocol_display

    def test_is_available_true_with_pyusb(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        with patch.dict('sys.modules', {
            'usb': MagicMock(),
            'usb.core': MagicMock(),
        }):
            assert p.is_available is True

    def test_close_releases_transport(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        mock_transport = MagicMock()
        p._transport = mock_transport
        p.close()
        mock_transport.close.assert_called_once()
        assert p._transport is None

    def test_close_noop_without_transport(self):
        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        p.close()  # Should not raise

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_handshake_polls_and_inits(self, mock_get_transport):
        """Handshake must poll for FBL and send init command."""
        mock_transport = MagicMock()
        # Poll returns FBL=100 (320x320) in byte[0]
        response = bytes([100]) + b'\x00' * 0xE0FF
        mock_transport.read_cdb.return_value = response
        mock_transport.send_cdb.return_value = True
        mock_get_transport.return_value = mock_transport

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        result = p._do_handshake()

        assert result is not None
        assert result.model_id == 100
        assert result.resolution == (320, 320)
        # Verify poll (read_cdb) and init (send_cdb) were called
        mock_transport.read_cdb.assert_called_once()
        mock_transport.send_cdb.assert_called_once()

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_handshake_returns_none_when_no_transport(self, mock_get_transport):
        mock_get_transport.return_value = None

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        assert p._do_handshake() is None

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_handshake_empty_response_uses_registry_fbl(self, mock_get_transport):
        """When poll returns empty, fall back to FBL from device registry."""
        mock_transport = MagicMock()
        mock_transport.read_cdb.return_value = b''
        mock_transport.send_cdb.return_value = True
        mock_get_transport.return_value = mock_transport

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        result = p._do_handshake()

        assert result is not None
        # Should use FBL from SCSI_DEVICES registry for 0402:3922

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_send_image_writes_chunks(self, mock_get_transport):
        """send_image must write frame data in SCSI chunks."""
        mock_transport = MagicMock()
        mock_transport.send_cdb.return_value = True
        mock_get_transport.return_value = mock_transport

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)

        # 320x320 RGB565 = 204,800 bytes = 4 chunks
        image_data = b'\x00' * (320 * 320 * 2)
        result = p.send_image(image_data, 320, 320)

        assert result is True
        assert mock_transport.send_cdb.call_count == 4  # 4 chunks for 320x320

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_send_image_returns_false_on_chunk_failure(self, mock_get_transport):
        mock_transport = MagicMock()
        mock_transport.send_cdb.return_value = False
        mock_get_transport.return_value = mock_transport

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)

        result = p.send_image(b'\x00' * (320 * 320 * 2), 320, 320)
        assert result is False

    @patch(f'{MODULE}.BSDScsiProtocol._get_transport')
    def test_send_image_returns_false_without_transport(self, mock_get_transport):
        mock_get_transport.return_value = None

        from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
        p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
        assert p.send_image(b'\x00' * 100, 320, 320) is False

    def test_get_transport_creates_and_opens(self):
        """_get_transport must create BSDScsiTransport and call open()."""
        mock_transport = MagicMock()
        mock_transport.open.return_value = True

        with patch(
            'trcc.legacy.adapters.device.bsd.scsi.BSDScsiTransport',
            return_value=mock_transport,
        ) as MockTransport:
            from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
            p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
            result = p._get_transport()

        MockTransport.assert_called_once_with(0x0402, 0x3922)
        mock_transport.open.assert_called_once()
        assert result is mock_transport

    def test_get_transport_returns_none_on_open_failure(self):
        mock_transport = MagicMock()
        mock_transport.open.return_value = False

        with patch(
            'trcc.legacy.adapters.device.bsd.scsi.BSDScsiTransport',
            return_value=mock_transport,
        ):
            from trcc.legacy.adapters.device.bsd.scsi_protocol import BSDScsiProtocol
            p = BSDScsiProtocol(vid=0x0402, pid=0x3922)
            result = p._get_transport()

        assert result is None
        assert p._transport is None
