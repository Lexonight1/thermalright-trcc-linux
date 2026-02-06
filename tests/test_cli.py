"""
Tests for cli – TRCC command-line interface argument parsing and dispatch.

Tests cover:
- main() with no args (prints help, returns 0)
- --version flag
- Subcommand argument parsing (detect, select, test, send, color, info, reset, setup-udev, download, gui)
- detect() / detect(--all) with mocked device_detector
- select_device() validation
- send_color() hex parsing
- show_info() with mocked system_info
- download_themes() dispatch to theme_downloader
- _get_settings_path() / _get_selected_device() / _set_selected_device() helpers
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.cli import (
    _ensure_extracted,
    _get_selected_device,
    _get_settings_path,
    _set_selected_device,
    detect,
    download_themes,
    gui,
    main,
    reset_device,
    select_device,
    send_color,
    send_image,
    setup_udev,
    show_info,
)
# Alias to avoid pytest collecting it as a test function
from trcc.cli import test_display as cli_test_display


class TestMainEntryPoint(unittest.TestCase):
    """Test main() CLI dispatch."""

    def test_no_args_prints_help(self):
        """No subcommand → print help, return 0."""
        with patch('sys.argv', ['trcc']):
            result = main()
        self.assertEqual(result, 0)

    def test_version_flag(self):
        """--version prints version and exits."""
        with patch('sys.argv', ['trcc', '--version']):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

    def test_detect_dispatches(self):
        """'detect' subcommand calls detect()."""
        with patch('sys.argv', ['trcc', 'detect']), \
             patch('trcc.cli.detect', return_value=0) as mock_detect:
            result = main()
            mock_detect.assert_called_once_with(show_all=False)
            self.assertEqual(result, 0)

    def test_detect_all_flag(self):
        """'detect --all' passes show_all=True."""
        with patch('sys.argv', ['trcc', 'detect', '--all']), \
             patch('trcc.cli.detect', return_value=0) as mock_detect:
            result = main()
            mock_detect.assert_called_once_with(show_all=True)

    def test_select_dispatches(self):
        """'select 2' dispatches with number=2."""
        with patch('sys.argv', ['trcc', 'select', '2']), \
             patch('trcc.cli.select_device', return_value=0) as mock_sel:
            result = main()
            mock_sel.assert_called_once_with(2)

    def test_color_dispatches(self):
        """'color ff0000' passes hex and device."""
        with patch('sys.argv', ['trcc', 'color', 'ff0000']), \
             patch('trcc.cli.send_color', return_value=0) as mock_color:
            result = main()
            mock_color.assert_called_once_with('ff0000', device=None)

    def test_info_dispatches(self):
        """'info' subcommand dispatches to show_info."""
        with patch('sys.argv', ['trcc', 'info']), \
             patch('trcc.cli.show_info', return_value=0) as mock_info:
            result = main()
            mock_info.assert_called_once()

    def test_gui_dispatches(self):
        """'gui' subcommand dispatches to gui()."""
        with patch('sys.argv', ['trcc', 'gui']), \
             patch('trcc.cli.gui', return_value=0) as mock_gui:
            result = main()
            mock_gui.assert_called_once()

    def test_download_list(self):
        """'download --list' dispatches with show_list=True."""
        with patch('sys.argv', ['trcc', 'download', '--list']), \
             patch('trcc.cli.download_themes', return_value=0) as mock_dl:
            result = main()
            mock_dl.assert_called_once_with(
                pack=None, show_list=True, force=False, show_info=False
            )

    def test_download_pack(self):
        with patch('sys.argv', ['trcc', 'download', 'themes-320', '--force']), \
             patch('trcc.cli.download_themes', return_value=0) as mock_dl:
            result = main()
            mock_dl.assert_called_once_with(
                pack='themes-320', show_list=False, force=True, show_info=False
            )


class TestDetect(unittest.TestCase):
    """Test detect() command."""

    def _make_device(self, path='/dev/sg0', name='LCD'):
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        return dev

    @patch('trcc.cli.detect_devices', create=True)
    def test_no_devices(self, mock_detect_devs):
        """No devices → returns 1."""
        # detect() imports detect_devices inside the function body
        with patch('trcc.cli._get_selected_device', return_value=None):
            with patch.dict('sys.modules', {}):
                # Must mock at the import point inside detect()
                mock_mod = MagicMock()
                mock_mod.detect_devices.return_value = []
                with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
                    result = detect(show_all=False)
        self.assertEqual(result, 1)

    def test_detect_with_device(self):
        """Single device → returns 0 and prints path."""
        dev = self._make_device()
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev]

        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg0'):
            result = detect(show_all=False)
        self.assertEqual(result, 0)


class TestSettingsHelpers(unittest.TestCase):
    """Test CLI settings persistence helpers."""

    def test_settings_path(self):
        path = _get_settings_path()
        self.assertTrue(path.endswith('settings.json'))

    def test_get_selected_no_file(self):
        """Returns None when no settings file."""
        with patch('trcc.cli._get_settings_path', return_value='/nonexistent/settings.json'):
            self.assertIsNone(_get_selected_device())

    def test_set_and_get_selected(self):
        """Round-trip: set then get selected device."""
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, 'settings.json')
            with patch('trcc.cli._get_settings_path', return_value=settings_path):
                _set_selected_device('/dev/sg1')
                result = _get_selected_device()
            self.assertEqual(result, '/dev/sg1')

    def test_set_preserves_other_keys(self):
        """set_selected_device preserves existing settings keys."""
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, 'settings.json')
            # Pre-populate with another key
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            with open(settings_path, 'w') as f:
                json.dump({'theme': 'dark'}, f)

            with patch('trcc.cli._get_settings_path', return_value=settings_path):
                _set_selected_device('/dev/sg2')

            with open(settings_path) as f:
                data = json.load(f)
            self.assertEqual(data['theme'], 'dark')
            self.assertEqual(data['selected_device'], '/dev/sg2')


class TestSendColor(unittest.TestCase):
    """Test send_color() hex parsing and dispatch."""

    def test_invalid_hex_short(self):
        """Too-short hex → returns 1."""
        result = send_color('fff')
        self.assertEqual(result, 1)

    def test_invalid_hex_long(self):
        result = send_color('ff00ff00')
        self.assertEqual(result, 1)

    def test_valid_hex_with_hash(self):
        """Hex with leading '#' is stripped."""
        mock_driver = MagicMock()
        mock_driver.create_solid_color.return_value = b'\x00' * 100
        mock_mod = MagicMock()
        mock_mod.LCDDriver.return_value = mock_driver

        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg0'), \
             patch('trcc.cli._ensure_extracted'):
            result = send_color('#ff0000')
        self.assertEqual(result, 0)


class TestShowInfo(unittest.TestCase):
    """Test show_info() metrics display."""

    def test_show_info_success(self):
        """Successful metrics fetch returns 0."""
        mock_mod = MagicMock()
        mock_mod.get_all_metrics.return_value = {
            'cpu_temp': 65, 'cpu_percent': 30, 'mem_percent': 45
        }
        mock_mod.format_metric.side_effect = lambda k, v: f"{v}"

        with patch.dict('sys.modules', {'trcc.system_info': mock_mod}):
            result = show_info()
        self.assertEqual(result, 0)


class TestDownloadThemes(unittest.TestCase):
    """Test download_themes() dispatch."""

    def test_list_mode(self):
        """show_list=True calls list_available."""
        mock_mod = MagicMock()
        with patch.dict('sys.modules', {'trcc.theme_downloader': mock_mod}):
            result = download_themes(pack=None, show_list=True, force=False, show_info=False)
        self.assertEqual(result, 0)

    def test_download_dispatches(self):
        """Pack name dispatches to download_pack."""
        mock_mod = MagicMock()
        mock_mod.download_pack.return_value = 0
        with patch.dict('sys.modules', {'trcc.theme_downloader': mock_mod}):
            result = download_themes(pack='themes-320', show_list=False,
                                     force=True, show_info=False)
        self.assertEqual(result, 0)


# ── gui() ────────────────────────────────────────────────────────────────────

class TestGui(unittest.TestCase):
    """Test gui() command."""

    def test_gui_generic_exception(self):
        """Non-import exception → returns 1."""
        mock_qt = MagicMock()
        mock_qt.run_mvc_app.side_effect = RuntimeError('display error')
        with patch.dict('sys.modules', {'trcc.qt_components.qt_app_mvc': mock_qt}):
            result = gui()
        self.assertEqual(result, 1)

    def test_gui_success(self):
        """Successful launch returns run_mvc_app's value."""
        mock_qt = MagicMock()
        mock_qt.run_mvc_app.return_value = 0
        with patch.dict('sys.modules', {'trcc.qt_components.qt_app_mvc': mock_qt}):
            result = gui()
        self.assertEqual(result, 0)


# ── select_device() ──────────────────────────────────────────────────────────

class TestSelectDevice(unittest.TestCase):
    """Test select_device() command."""

    def _make_device(self, path='/dev/sg0', name='LCD'):
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        return dev

    def test_no_devices(self):
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = []
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = select_device(1)
        self.assertEqual(result, 1)

    def test_invalid_number_too_low(self):
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [self._make_device()]
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = select_device(0)
        self.assertEqual(result, 1)

    def test_invalid_number_too_high(self):
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [self._make_device()]
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = select_device(5)
        self.assertEqual(result, 1)

    def test_valid_selection(self):
        dev = self._make_device('/dev/sg1', 'Frost Commander')
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev]
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.cli._set_selected_device') as mock_set:
            result = select_device(1)
        self.assertEqual(result, 0)
        mock_set.assert_called_once_with('/dev/sg1')


# ── test_display() ───────────────────────────────────────────────────────────

class TestTestDisplay(unittest.TestCase):
    """Test test_display() command."""

    def test_display_success(self):
        """Cycles through colors and returns 0."""
        mock_driver = MagicMock()
        mock_driver.device_path = '/dev/sg0'
        mock_driver.create_solid_color.return_value = b'\x00' * 100
        mock_lcd = MagicMock()
        mock_lcd.LCDDriver.return_value = mock_driver

        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_lcd}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg0'), \
             patch('trcc.cli._ensure_extracted'), \
             patch('time.sleep'):
            result = cli_test_display(device='/dev/sg0', loop=False)
        self.assertEqual(result, 0)
        # 7 colors displayed
        self.assertEqual(mock_driver.send_frame.call_count, 7)

    def test_display_error(self):
        """Exception returns 1."""
        mock_mod = MagicMock()
        mock_mod.LCDDriver.side_effect = RuntimeError('no device')
        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value=None):
            result = cli_test_display()
        self.assertEqual(result, 1)


# ── send_image() ─────────────────────────────────────────────────────────────

class TestSendImage(unittest.TestCase):
    """Test send_image() command."""

    def test_file_not_found(self):
        result = send_image('/nonexistent/image.png')
        self.assertEqual(result, 1)

    def test_send_success(self):
        mock_driver = MagicMock()
        mock_driver.device_path = '/dev/sg0'
        mock_driver.load_image.return_value = b'\x00'
        mock_mod = MagicMock()
        mock_mod.LCDDriver.return_value = mock_driver

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'\x89PNG')
            tmp_path = f.name

        try:
            with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
                 patch('trcc.cli._get_selected_device', return_value='/dev/sg0'), \
                 patch('trcc.cli._ensure_extracted'):
                result = send_image(tmp_path)
            self.assertEqual(result, 0)
        finally:
            os.unlink(tmp_path)


# ── reset_device() ───────────────────────────────────────────────────────────

class TestResetDevice(unittest.TestCase):
    """Test reset_device() command."""

    def test_reset_success(self):
        mock_driver = MagicMock()
        mock_driver.device_path = '/dev/sg0'
        mock_driver.create_solid_color.return_value = b'\x00'
        mock_mod = MagicMock()
        mock_mod.LCDDriver.return_value = mock_driver

        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg0'), \
             patch('trcc.cli._ensure_extracted'):
            result = reset_device()
        self.assertEqual(result, 0)
        mock_driver.send_frame.assert_called_once()

    def test_reset_error(self):
        mock_mod = MagicMock()
        mock_mod.LCDDriver.side_effect = RuntimeError('fail')
        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value=None):
            result = reset_device()
        self.assertEqual(result, 1)


# ── setup_udev() ─────────────────────────────────────────────────────────────

class TestSetupUdev(unittest.TestCase):
    """Test setup_udev() command."""

    def test_dry_run(self):
        """dry_run=True prints rules and returns 0 without writing."""
        mock_mod = MagicMock()
        mock_mod.KNOWN_DEVICES = {
            (0x87CD, 0x70DB): {'vendor': 'Thermalright', 'product': 'LCD'},
        }
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = setup_udev(dry_run=True)
        self.assertEqual(result, 0)

    def test_not_root(self):
        """Non-root without dry_run → returns 1."""
        mock_mod = MagicMock()
        mock_mod.KNOWN_DEVICES = {
            (0x87CD, 0x70DB): {'vendor': 'Thermalright', 'product': 'LCD'},
        }
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('os.geteuid', return_value=1000):
            result = setup_udev(dry_run=False)
        self.assertEqual(result, 1)


# ── _ensure_extracted() ──────────────────────────────────────────────────────

class TestEnsureExtracted(unittest.TestCase):
    """Test _ensure_extracted helper."""

    def test_no_implementation(self):
        """No implementation → no-op (no error)."""
        driver = MagicMock()
        driver.implementation = None
        _ensure_extracted(driver)  # should not raise

    def test_calls_extraction(self):
        """With a valid implementation, extraction runs without error."""
        driver = MagicMock()
        driver.implementation.resolution = (320, 320)
        with patch('trcc.paths.ensure_themes_extracted', return_value=True), \
             patch('trcc.paths.ensure_web_extracted', return_value=True), \
             patch('trcc.paths.ensure_web_masks_extracted', return_value=True):
            _ensure_extracted(driver)  # should not raise

    def test_exception_is_swallowed(self):
        """Extraction errors are non-fatal."""
        driver = MagicMock()
        driver.implementation.resolution = (320, 320)
        # Force an exception in the extraction calls
        with patch.dict('sys.modules', {
            'trcc.paths': MagicMock(
                ensure_themes_extracted=MagicMock(side_effect=RuntimeError('boom'))
            )
        }):
            _ensure_extracted(driver)  # should not raise


# ── gui() additional branches ────────────────────────────────────────────────

class TestGuiExtra(unittest.TestCase):

    def test_gui_import_error(self):
        """PyQt6 not importable → returns 1."""
        with patch.dict('sys.modules', {
            'trcc.qt_components.qt_app_mvc': None,
        }):
            result = gui()
        self.assertEqual(result, 1)


# ── detect() additional branches ─────────────────────────────────────────────

class TestDetectExtra(unittest.TestCase):

    def _make_device(self, path='/dev/sg0', name='LCD'):
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        return dev

    def test_detect_exception(self):
        """detect_devices raises → returns 1."""
        mock_mod = MagicMock()
        mock_mod.detect_devices.side_effect = RuntimeError('oops')
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = detect()
        self.assertEqual(result, 1)

    def test_detect_show_all_multi(self):
        """show_all with multiple devices shows * marker."""
        dev1 = self._make_device('/dev/sg0', 'LCD-A')
        dev2 = self._make_device('/dev/sg1', 'LCD-B')
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev1, dev2]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg1'), \
             redirect_stdout(buf):
            result = detect(show_all=True)
        self.assertEqual(result, 0)
        output = buf.getvalue()
        self.assertIn('*', output)
        self.assertIn('trcc select', output)

    def test_detect_no_selected_match(self):
        """Selected device not in list → prints first device."""
        dev = self._make_device('/dev/sg0', 'LCD')
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg9'), \
             redirect_stdout(buf):
            result = detect(show_all=False)
        self.assertEqual(result, 0)
        self.assertIn('/dev/sg0', buf.getvalue())


# ── Settings corrupt JSON ────────────────────────────────────────────────────

class TestSettingsCorruptJSON(unittest.TestCase):

    def test_get_corrupt_json(self):
        """Corrupt JSON → returns None."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{bad json')
            path = f.name
        try:
            with patch('trcc.cli._get_settings_path', return_value=path):
                result = _get_selected_device()
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_set_with_corrupt_existing(self):
        """Set device with corrupt existing file → overwrites cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'settings.json')
            with open(path, 'w') as f:
                f.write('{bad')
            with patch('trcc.cli._get_settings_path', return_value=path):
                _set_selected_device('/dev/sg0')
                result = _get_selected_device()
            self.assertEqual(result, '/dev/sg0')


# ── download_themes additional branches ──────────────────────────────────────

class TestDownloadExtra(unittest.TestCase):

    def test_show_info(self):
        """show_info=True calls pack_info."""
        mock_mod = MagicMock()
        with patch.dict('sys.modules', {'trcc.theme_downloader': mock_mod}):
            result = download_themes(pack='test', show_list=False,
                                     force=False, show_info=True)
        self.assertEqual(result, 0)
        mock_mod.show_info.assert_called_once()

    def test_exception_returns_1(self):
        """Exception during download → returns 1."""
        mock_mod = MagicMock()
        mock_mod.download_pack.side_effect = RuntimeError('net error')
        with patch.dict('sys.modules', {'trcc.theme_downloader': mock_mod}):
            result = download_themes(pack='themes-320', show_list=False,
                                     force=False, show_info=False)
        self.assertEqual(result, 1)


# ── test_display KeyboardInterrupt ───────────────────────────────────────────

class TestTestDisplayExtra(unittest.TestCase):

    def test_keyboard_interrupt(self):
        mock_mod = MagicMock()
        mock_driver = MagicMock()
        mock_mod.LCDDriver.return_value = mock_driver
        mock_mod.LCDDriver.return_value.send_image.side_effect = KeyboardInterrupt

        with patch.dict('sys.modules', {'trcc.lcd_driver': mock_mod}), \
             patch('trcc.cli._get_selected_device', return_value='/dev/sg0'):
            result = cli_test_display()
        self.assertEqual(result, 0)


# ── main() dispatch branches ──────────────────────────────────────────────────

class TestMainDispatch(unittest.TestCase):
    """Cover main() dispatch branches for test, send, color, info, reset, setup-udev."""

    @patch('trcc.cli.test_display', return_value=0)
    def test_dispatch_test(self, mock_fn):
        with patch('sys.argv', ['trcc', 'test']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.send_image', return_value=0)
    def test_dispatch_send(self, mock_fn):
        with patch('sys.argv', ['trcc', 'send', 'image.png']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.send_color', return_value=0)
    def test_dispatch_color(self, mock_fn):
        with patch('sys.argv', ['trcc', 'color', 'ff0000']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.show_info', return_value=0)
    def test_dispatch_info(self, mock_fn):
        with patch('sys.argv', ['trcc', 'info']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.reset_device', return_value=0)
    def test_dispatch_reset(self, mock_fn):
        with patch('sys.argv', ['trcc', 'reset']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.setup_udev', return_value=0)
    def test_dispatch_setup_udev(self, mock_fn):
        with patch('sys.argv', ['trcc', 'setup-udev', '--dry-run']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch('trcc.cli.download_themes', return_value=0)
    def test_dispatch_download(self, mock_fn):
        with patch('sys.argv', ['trcc', 'download', '--list']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)


# ── select_device exception ──────────────────────────────────────────────────

class TestSelectDeviceException(unittest.TestCase):

    @patch('trcc.device_detector.detect_devices', side_effect=RuntimeError("fail"))
    def test_exception_returns_1(self, _):
        result = select_device(1)
        self.assertEqual(result, 1)


# ── send_image success ────────────────────────────────────────────────────────

class TestSendImageEdge(unittest.TestCase):

    @patch('trcc.cli._get_selected_device', return_value='/dev/sg0')
    def test_send_image_exception(self, _):
        """send_image with nonexistent file → exception → returns 1."""
        result = send_image('/nonexistent/file.png')
        self.assertEqual(result, 1)


# ── send_color exception ─────────────────────────────────────────────────────

class TestSendColorEdge(unittest.TestCase):

    @patch('trcc.lcd_driver.LCDDriver', side_effect=RuntimeError("fail"))
    @patch('trcc.cli._get_selected_device', return_value='/dev/sg0')
    def test_exception_returns_1(self, _, __):
        result = send_color('ff0000')
        self.assertEqual(result, 1)


# ── show_info metrics display ─────────────────────────────────────────────────

class TestShowInfoMetrics(unittest.TestCase):

    @patch('trcc.system_info.format_metric', side_effect=lambda k, v: str(v))
    @patch('trcc.system_info.get_all_metrics')
    def test_shows_gpu_and_memory(self, mock_metrics, _):
        mock_metrics.return_value = {
            'cpu_temp': 65.0,
            'cpu_percent': 42.0,
            'cpu_freq': 3600,
            'gpu_temp': 70.0,
            'gpu_usage': 80.0,
            'gpu_clock': 1800,
            'mem_percent': 55.0,
            'mem_used': 8192,
            'mem_total': 16384,
            'date': '2025-01-01',
            'time': '12:00',
            'weekday': 'Monday',
        }
        result = show_info()
        self.assertEqual(result, 0)

    @patch('trcc.system_info.format_metric', side_effect=lambda k, v: str(v))
    @patch('trcc.system_info.get_all_metrics')
    def test_shows_partial_metrics(self, mock_metrics, _):
        """Handles missing keys gracefully."""
        mock_metrics.return_value = {'cpu_temp': 65.0}
        result = show_info()
        self.assertEqual(result, 0)


# ── setup_udev non-dry-run ───────────────────────────────────────────────────

class TestSetupUdevNonDry(unittest.TestCase):

    @patch('os.system')
    @patch('os.path.exists', return_value=True)
    @patch('os.geteuid', return_value=0)
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_root_writes_files(self, mock_open, mock_euid, mock_exists, mock_system):
        result = setup_udev(dry_run=False)
        self.assertEqual(result, 0)
        # Should write udev rules and modprobe config
        self.assertGreaterEqual(mock_open.call_count, 2)
        mock_system.assert_any_call("udevadm control --reload-rules")
        mock_system.assert_any_call("udevadm trigger")

    @patch('os.geteuid', return_value=1000)
    def test_non_root_returns_1(self, _):
        result = setup_udev(dry_run=False)
        self.assertEqual(result, 1)

    @patch('os.system')
    @patch('os.path.exists', return_value=False)
    @patch('os.geteuid', return_value=0)
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_root_no_sysfs_quirks(self, mock_open, mock_euid, mock_exists, mock_system):
        """No quirks_sysfs file → skip writing quirks."""
        result = setup_udev(dry_run=False)
        self.assertEqual(result, 0)


# ── download_themes edge paths ───────────────────────────────────────────────

class TestDownloadThemesEdge(unittest.TestCase):

    @patch('trcc.theme_downloader.show_info')
    def test_show_info_mode(self, mock_info):
        result = download_themes(pack='320x320', show_info=True)
        mock_info.assert_called_once_with('320x320')
        self.assertEqual(result, 0)

    @patch('trcc.theme_downloader.download_pack', return_value=0)
    def test_download_pack_call(self, mock_dl):
        result = download_themes(pack='320x320')
        mock_dl.assert_called_once_with('320x320', force=False)
        self.assertEqual(result, 0)

    @patch('trcc.theme_downloader.download_pack', side_effect=RuntimeError("net error"))
    def test_exception_returns_1(self, _):
        result = download_themes(pack='320x320')
        self.assertEqual(result, 1)


if __name__ == '__main__':
    unittest.main()
