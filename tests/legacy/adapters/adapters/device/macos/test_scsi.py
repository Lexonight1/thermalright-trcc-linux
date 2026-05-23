"""Tests for macOS SCSI transport (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

MODULE = 'trcc.legacy.adapters.device.macos.scsi'


class TestMacOSScsiTransport:

    def test_init(self):
        from trcc.legacy.adapters.device.macos.scsi import MacOSScsiTransport
        t = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        assert t._vid == 0x0416
        assert t._pid == 0x5020
        assert t._dev is None

    def test_send_cdb_fails_when_not_open(self):
        from trcc.legacy.adapters.device.macos.scsi import MacOSScsiTransport
        t = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        assert t.send_cdb(b'\xef', b'\x00' * 512) is False

    def test_close_noop_when_not_open(self):
        from trcc.legacy.adapters.device.macos.scsi import MacOSScsiTransport
        t = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        t.close()  # Should not raise

    def test_context_manager(self):
        from trcc.legacy.adapters.device.macos.scsi import MacOSScsiTransport
        t = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        with patch.object(t, 'open') as m_open, \
             patch.object(t, 'close') as m_close:
            with t:
                pass
            m_open.assert_called_once()
            m_close.assert_called_once()

    def test_cbw_constants(self):
        # CBW/CSW constants live in the shared USB BOT module (also used by
        # FreeBSD/OpenBSD); the macOS module just subclasses UsbBotScsiTransport.
        from trcc.legacy.adapters.device._usb_bot_scsi import CBW_SIGNATURE, CBW_SIZE, CSW_SIZE
        assert CBW_SIGNATURE == 0x43425355
        assert CBW_SIZE == 31
        assert CSW_SIZE == 13

    @patch('trcc.legacy.adapters.device._pyusb_find.find')
    @patch('usb.util.endpoint_direction')
    @patch('usb.util.ENDPOINT_OUT', 0x00)
    @patch('usb.util.ENDPOINT_IN', 0x80)
    def test_open_detaches_kernel_driver(self, mock_ep_dir, mock_find):
        """On macOS, open() must detach kernel driver."""
        mock_dev = MagicMock()
        mock_dev.is_kernel_driver_active.return_value = True
        mock_cfg = MagicMock()
        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02
        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x81
        mock_intf = [mock_ep_out, mock_ep_in]
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_find.return_value = mock_dev
        mock_ep_dir.side_effect = lambda addr: 0x00 if addr == 0x02 else 0x80

        from trcc.legacy.adapters.device.macos.scsi import MacOSScsiTransport
        t = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        result = t.open()

        assert result is True
        mock_dev.detach_kernel_driver.assert_called_once_with(0)
        mock_dev.set_configuration.assert_called_once()
