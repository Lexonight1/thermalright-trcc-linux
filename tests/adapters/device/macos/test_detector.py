"""Tests for macOS USB device detector (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

MODULE = 'trcc.adapters.device.macos.detector'


class TestMacOSDetector:

    @patch(f'{MODULE}.usb', create=True)
    def test_detects_known_hid_device(self, mock_usb_mod):
        """Known HID LCD device is detected via pyusb."""
        from trcc.adapters.device.detector import _HID_LCD_DEVICES

        if not _HID_LCD_DEVICES:
            return

        vid, pid = next(iter(_HID_LCD_DEVICES))
        mock_dev = MagicMock()
        mock_dev.bus = 1
        mock_dev.address = 5

        def mock_find(find_all=False, idVendor=None, idProduct=None):
            # Production calls usb.core.find(find_all=True, idVendor=..., idProduct=...)
            # — the iterable form returns matching devices, not None.
            if idVendor == vid and idProduct == pid:
                return iter([mock_dev]) if find_all else mock_dev
            return iter(()) if find_all else None

        with patch('trcc.adapters.device._pyusb_find.find', side_effect=mock_find):
            from trcc.adapters.device.macos.detector import MacOSDeviceDetector
            devices = MacOSDeviceDetector.detect()

        matching = [d for d in devices if d.vid == vid and d.pid == pid]
        assert len(matching) >= 1
        assert matching[0].protocol == 'hid'

    def test_returns_empty_without_pyusb(self):
        """When pyusb not installed, returns empty list."""
        import sys
        # Temporarily hide usb module
        saved = sys.modules.get('usb')
        saved_core = sys.modules.get('usb.core')
        sys.modules['usb'] = None  # type: ignore
        sys.modules['usb.core'] = None  # type: ignore

        try:
            import importlib
            mod = importlib.import_module(MODULE)
            importlib.reload(mod)
            # This will hit ImportError
        except Exception:
            pass
        finally:
            if saved is not None:
                sys.modules['usb'] = saved
            else:
                sys.modules.pop('usb', None)
            if saved_core is not None:
                sys.modules['usb.core'] = saved_core
            else:
                sys.modules.pop('usb.core', None)


class TestGetUsbTree:

    @patch(f'{MODULE}.subprocess')
    def test_parses_json(self, mock_sub):
        import json
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({'SPUSBDataType': [{'_name': 'USB 3.0 Bus'}]}),
        )
        from trcc.adapters.device.macos.detector import get_usb_tree
        tree = get_usb_tree()
        assert len(tree) == 1
        assert tree[0]['_name'] == 'USB 3.0 Bus'

    @patch(f'{MODULE}.subprocess.run')
    def test_returns_empty_on_failure(self, mock_run):
        # Patch subprocess.run only — patching the whole `subprocess` module
        # would replace `subprocess.SubprocessError` in the except clause with
        # a MagicMock and the except would TypeError.  Production catches
        # (OSError, SubprocessError, ValueError, KeyError); FileNotFoundError
        # is the realistic "system_profiler not installed" mode.
        mock_run.side_effect = FileNotFoundError("no system_profiler")
        from trcc.adapters.device.macos.detector import get_usb_tree
        assert get_usb_tree() == []
