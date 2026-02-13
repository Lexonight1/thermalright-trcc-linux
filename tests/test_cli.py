"""
Tests for cli -- TRCC command-line interface argument parsing and dispatch.

Tests cover:
- main() with no args (prints help, returns 0)
- --version flag
- Subcommand argument parsing (detect, select, test, send, color, video, info,
  reset, setup-udev, download, gui, brightness, rotation, screencast, mask,
  overlay, theme-list, theme-load, theme-save, theme-export, theme-import,
  led-color, led-mode, led-brightness, led-off, led-sensor)
- DeviceCommands.detect() / detect(--all) with mocked device_detector
- DeviceCommands.select() validation
- DisplayCommands.send_color() hex parsing
- SystemCommands.show_info() with mocked system_info
- SystemCommands.download_themes() dispatch to theme_downloader
- conf.get_selected_device() / conf.save_selected_device() helpers
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.cli import (
    DeviceCommands,
    DiagCommands,
    DisplayCommands,
    LEDCommands,
    SystemCommands,
    ThemeCommands,
    _ensure_extracted,
    _format_device,
    _get_service,
    _probe_device,
    detect,
    download_themes,
    export_theme,
    gui,
    hid_debug,
    hr10_tempd,
    import_theme,
    install_desktop,
    led_brightness,
    led_color,
    led_debug,
    led_mode,
    led_off,
    led_sensor,
    list_themes,
    load_mask,
    load_theme,
    main,
    play_video,
    render_overlay,
    report,
    reset_device,
    resume,
    save_theme,
    screencast,
    select_device,
    send_color,
    send_image,
    set_brightness,
    set_rotation,
    setup_udev,
    show_info,
    uninstall,
)
from trcc.cli import test_display as cli_test_display
from trcc.core.models import DeviceInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device_info(
    path='/dev/sg0', name='LCD', vid=0x87CD, pid=0x70DB,
    protocol='scsi', resolution=(320, 320), **kw,
) -> DeviceInfo:
    """Create a DeviceInfo for tests."""
    return DeviceInfo(
        name=name, path=path, vid=vid, pid=pid,
        protocol=protocol, resolution=resolution, **kw,
    )


def _mock_service(device=None, devices=None):
    """Create a mock DeviceService with optional pre-selected device."""
    svc = MagicMock()
    dev = device or _make_device_info()
    svc.selected = dev
    svc.devices = devices or [dev]
    svc.detect.return_value = svc.devices
    svc.send_pil.return_value = True
    return svc


class TestMainEntryPoint(unittest.TestCase):
    """Test main() CLI dispatch."""

    def test_no_args_prints_help(self):
        """No subcommand -> print help, return 0."""
        with patch('sys.argv', ['trcc']):
            result = main()
        self.assertEqual(result, 0)

    def test_version_flag(self):
        """--version prints version and exits."""
        with patch('sys.argv', ['trcc', '--version']):
            result = main()
            self.assertEqual(result, 0)

    def test_detect_dispatches(self):
        """'detect' subcommand calls DeviceCommands.detect()."""
        with patch('sys.argv', ['trcc', 'detect']), \
             patch.object(DeviceCommands, 'detect', return_value=0) as mock_detect:
            result = main()
            mock_detect.assert_called_once_with(show_all=False)
            self.assertEqual(result, 0)

    def test_detect_all_flag(self):
        """'detect --all' passes show_all=True."""
        with patch('sys.argv', ['trcc', 'detect', '--all']), \
             patch.object(DeviceCommands, 'detect', return_value=0) as mock_detect:
            main()
            mock_detect.assert_called_once_with(show_all=True)

    def test_select_dispatches(self):
        """'select 2' dispatches with number=2."""
        with patch('sys.argv', ['trcc', 'select', '2']), \
             patch.object(DeviceCommands, 'select', return_value=0) as mock_sel:
            main()
            mock_sel.assert_called_once_with(2)

    def test_color_dispatches(self):
        """'color ff0000' passes hex and device."""
        with patch('sys.argv', ['trcc', 'color', 'ff0000']), \
             patch.object(DisplayCommands, 'send_color', return_value=0) as mock_color:
            main()
            mock_color.assert_called_once_with('ff0000', device=None)

    def test_info_dispatches(self):
        """'info' subcommand dispatches to SystemCommands.show_info."""
        with patch('sys.argv', ['trcc', 'info']), \
             patch.object(SystemCommands, 'show_info', return_value=0) as mock_info:
            main()
            mock_info.assert_called_once()

    def test_gui_dispatches(self):
        """'gui' subcommand dispatches to gui()."""
        with patch('sys.argv', ['trcc', 'gui']), \
             patch('trcc.cli.gui', return_value=0) as mock_gui:
            main()
            mock_gui.assert_called_once()

    def test_download_list(self):
        """'download --list' dispatches with show_list=True."""
        with patch('sys.argv', ['trcc', 'download', '--list']), \
             patch.object(SystemCommands, 'download_themes', return_value=0) as mock_dl:
            main()
            mock_dl.assert_called_once_with(
                pack=None, show_list=True, force=False, show_info=False
            )

    def test_download_pack(self):
        with patch('sys.argv', ['trcc', 'download', 'themes-320', '--force']), \
             patch.object(SystemCommands, 'download_themes', return_value=0) as mock_dl:
            main()
            mock_dl.assert_called_once_with(
                pack='themes-320', show_list=False, force=True, show_info=False
            )


class TestDetect(unittest.TestCase):
    """Test detect() command."""

    def _make_device(self, path='/dev/sg0', name='LCD', vid=0x87CD, pid=0x70DB, protocol='scsi'):
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        dev.vid = vid
        dev.pid = pid
        dev.protocol = protocol
        return dev

    def test_no_devices(self):
        """No devices -> returns 1."""
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = []
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = detect(show_all=False)
        self.assertEqual(result, 1)

    def test_detect_with_device(self):
        """Single device -> returns 0 and prints path."""
        dev = self._make_device()
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev]

        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.conf.get_selected_device', return_value='/dev/sg0'):
            result = detect(show_all=False)
        self.assertEqual(result, 0)


class TestSettingsHelpers(unittest.TestCase):
    """Test conf.py settings persistence helpers."""

    def test_get_selected_no_file(self):
        """Returns None when no config file."""
        with patch('trcc.conf.CONFIG_PATH', '/nonexistent/config.json'):
            from trcc.conf import get_selected_device
            self.assertIsNone(get_selected_device())

    def test_set_and_get_selected(self):
        """Round-trip: set then get selected device."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, 'config.json')
            with patch('trcc.conf.CONFIG_PATH', config_path), \
                 patch('trcc.conf.CONFIG_DIR', tmp):
                from trcc.conf import get_selected_device, save_selected_device
                save_selected_device('/dev/sg1')
                result = get_selected_device()
            self.assertEqual(result, '/dev/sg1')

    def test_set_preserves_other_keys(self):
        """save_selected_device preserves existing config keys."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, 'config.json')
            # Pre-populate with another key
            with open(config_path, 'w') as f:
                json.dump({'theme': 'dark'}, f)

            with patch('trcc.conf.CONFIG_PATH', config_path), \
                 patch('trcc.conf.CONFIG_DIR', tmp):
                from trcc.conf import save_selected_device
                save_selected_device('/dev/sg2')

            with open(config_path) as f:
                data = json.load(f)
            self.assertEqual(data['theme'], 'dark')
            self.assertEqual(data['selected_device'], '/dev/sg2')


class TestSendColor(unittest.TestCase):
    """Test send_color() hex parsing and dispatch."""

    def test_invalid_hex_short(self):
        """Too-short hex -> returns 1."""
        result = send_color('fff')
        self.assertEqual(result, 1)

    def test_invalid_hex_long(self):
        result = send_color('ff00ff00')
        self.assertEqual(result, 1)

    def test_valid_hex_with_hash(self):
        """Hex with leading '#' is stripped."""
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            result = send_color('#ff0000')
        self.assertEqual(result, 0)
        svc.send_pil.assert_called_once()


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


# -- gui() -------------------------------------------------------------------

class TestGui(unittest.TestCase):
    """Test gui() command."""

    def test_gui_generic_exception(self):
        """Non-import exception -> returns 1."""
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


# -- select_device() ---------------------------------------------------------

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
             patch('trcc.conf.save_selected_device') as mock_set:
            result = select_device(1)
        self.assertEqual(result, 0)
        mock_set.assert_called_once_with('/dev/sg1')


# -- test_display() ----------------------------------------------------------

class TestTestDisplay(unittest.TestCase):
    """Test test_display() command."""

    def test_display_success(self):
        """Cycles through colors and returns 0."""
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('time.sleep'):
            result = cli_test_display(device='/dev/sg0', loop=False)
        self.assertEqual(result, 0)
        # 7 colors displayed
        self.assertEqual(svc.send_pil.call_count, 7)

    def test_display_error(self):
        """Exception returns 1."""
        with patch.object(DeviceCommands, '_get_service',
                          side_effect=RuntimeError('no device')):
            result = cli_test_display()
        self.assertEqual(result, 1)


# -- send_image() ------------------------------------------------------------

class TestSendImage(unittest.TestCase):
    """Test send_image() command."""

    def test_file_not_found(self):
        result = send_image('/nonexistent/image.png')
        self.assertEqual(result, 1)

    def test_send_success(self):
        svc = _mock_service()
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            # Create a tiny valid PNG
            from PIL import Image
            img = Image.new('RGB', (10, 10), (255, 0, 0))
            img.save(f, format='PNG')
            tmp_path = f.name

        try:
            with patch.object(DeviceCommands, '_get_service', return_value=svc):
                result = send_image(tmp_path)
            self.assertEqual(result, 0)
            svc.send_pil.assert_called_once()
        finally:
            os.unlink(tmp_path)


# -- play_video() ------------------------------------------------------------

class TestPlayVideo(unittest.TestCase):
    """Test play_video() command."""

    def test_file_not_found(self):
        result = play_video('/nonexistent/video.mp4')
        self.assertEqual(result, 1)

    def test_no_device(self):
        svc = MagicMock()
        svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            result = play_video('/tmp/fake.mp4')
        self.assertEqual(result, 1)

    def test_play_success(self):
        """Loads video, plays frames, sends to LCD."""
        svc = _mock_service()
        mock_media = MagicMock()
        mock_media.load.return_value = True
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 3
        mock_media._state.fps = 16.0
        mock_media._state.loop = True
        mock_media.frame_interval_ms = 62
        # Simulate 3 frames then stop
        mock_frame = MagicMock()
        mock_media.is_playing = True
        call_count = [0]
        def tick_side_effect():
            call_count[0] += 1
            if call_count[0] <= 3:
                return mock_frame, True, None
            mock_media.is_playing = False
            return None, False, None
        mock_media.tick.side_effect = tick_side_effect

        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.services.MediaService', return_value=mock_media), \
             patch('time.sleep'), \
             patch('os.path.exists', return_value=True):
            result = play_video('/tmp/test.mp4', loop=False, duration=0)
        self.assertEqual(result, 0)
        self.assertEqual(svc.send_pil.call_count, 3)

    def test_play_with_progress(self):
        """Progress info is printed when available."""
        svc = _mock_service()
        mock_media = MagicMock()
        mock_media.load.return_value = True
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 1
        mock_media._state.fps = 16.0
        mock_media._state.loop = False
        mock_media.frame_interval_ms = 62
        mock_frame = MagicMock()
        mock_media.is_playing = True
        call_count = [0]
        def tick_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_frame, True, (50.0, '00:01', '00:02')
            mock_media.is_playing = False
            return None, False, None
        mock_media.tick.side_effect = tick_side_effect

        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.services.MediaService', return_value=mock_media), \
             patch('time.sleep'), \
             patch('os.path.exists', return_value=True):
            result = play_video('/tmp/test.mp4', loop=False)
        self.assertEqual(result, 0)

    def test_keyboard_interrupt(self):
        """Ctrl+C stops gracefully."""
        svc = _mock_service()
        mock_media = MagicMock()
        mock_media.load.return_value = True
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 100
        mock_media._state.fps = 16.0
        mock_media._state.loop = True
        mock_media.frame_interval_ms = 62
        mock_media.is_playing = True
        mock_media.tick.side_effect = KeyboardInterrupt

        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.services.MediaService', return_value=mock_media), \
             patch('os.path.exists', return_value=True):
            result = play_video('/tmp/test.mp4')
        self.assertEqual(result, 0)

    def test_load_failure(self):
        """Failed video load returns 1."""
        svc = _mock_service()
        mock_media = MagicMock()
        mock_media.load.return_value = False

        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.services.MediaService', return_value=mock_media), \
             patch('os.path.exists', return_value=True):
            result = play_video('/tmp/bad.mp4')
        self.assertEqual(result, 1)


# -- reset_device() ----------------------------------------------------------

class TestResetDevice(unittest.TestCase):
    """Test reset_device() command."""

    def test_reset_success(self):
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            result = reset_device()
        self.assertEqual(result, 0)
        svc.send_pil.assert_called_once()

    def test_reset_error(self):
        with patch.object(DeviceCommands, '_get_service',
                          side_effect=RuntimeError('fail')):
            result = reset_device()
        self.assertEqual(result, 1)


# -- setup_udev() ------------------------------------------------------------

class TestSetupUdev(unittest.TestCase):
    """Test setup_udev() command."""

    def test_dry_run(self):
        """dry_run=True prints rules and returns 0 without writing."""
        from trcc.device_detector import DeviceEntry
        mock_mod = MagicMock()
        mock_mod.KNOWN_DEVICES = {
            (0x87CD, 0x70DB): DeviceEntry(
                vendor='Thermalright', product='LCD',
                implementation='thermalright_lcd_v1',
            ),
        }
        mock_mod.DeviceEntry = DeviceEntry
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}):
            result = setup_udev(dry_run=True)
        self.assertEqual(result, 0)

    @patch('trcc.cli._sudo_reexec', return_value=1)
    def test_not_root(self, mock_reexec):
        """Non-root without dry_run -> sudo re-exec returns non-zero."""
        with patch('os.geteuid', return_value=1000):
            result = setup_udev(dry_run=False)
        mock_reexec.assert_called_once_with("setup-udev")
        self.assertEqual(result, 1)


# -- _ensure_extracted() -----------------------------------------------------

class TestEnsureExtracted(unittest.TestCase):
    """Test _ensure_extracted helper."""

    def test_no_implementation(self):
        """No implementation -> no-op (no error)."""
        driver = MagicMock()
        driver.implementation = None
        _ensure_extracted(driver)  # should not raise

    def test_calls_extraction(self):
        """With a valid implementation, extraction runs without error."""
        driver = MagicMock()
        driver.implementation.resolution = (320, 320)
        with patch('trcc.data_repository.DataManager.ensure_all', return_value=True):
            _ensure_extracted(driver)  # should not raise

    def test_exception_is_swallowed(self):
        """Extraction errors are non-fatal."""
        driver = MagicMock()
        driver.implementation.resolution = (320, 320)
        # Force an exception in the extraction calls
        with patch('trcc.data_repository.DataManager.ensure_all',
                   side_effect=RuntimeError('boom')):
            _ensure_extracted(driver)  # should not raise


# -- gui() additional branches -----------------------------------------------

class TestGuiExtra(unittest.TestCase):

    def test_gui_import_error(self):
        """PySide6 not importable -> returns 1."""
        with patch.dict('sys.modules', {
            'trcc.qt_components.qt_app_mvc': None,
        }):
            result = gui()
        self.assertEqual(result, 1)


# -- detect() additional branches --------------------------------------------

class TestDetectExtra(unittest.TestCase):

    def _make_device(self, path='/dev/sg0', name='LCD', vid=0x87CD, pid=0x70DB, protocol='scsi'):
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        dev.vid = vid
        dev.pid = pid
        dev.protocol = protocol
        return dev

    def test_detect_exception(self):
        """detect_devices raises -> returns 1."""
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
             patch('trcc.conf.get_selected_device', return_value='/dev/sg1'), \
             redirect_stdout(buf):
            result = detect(show_all=True)
        self.assertEqual(result, 0)
        output = buf.getvalue()
        self.assertIn('*', output)
        self.assertIn('trcc select', output)

    def test_detect_no_selected_match(self):
        """Selected device not in list -> prints first device."""
        dev = self._make_device('/dev/sg0', 'LCD')
        mock_mod = MagicMock()
        mock_mod.detect_devices.return_value = [dev]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch.dict('sys.modules', {'trcc.device_detector': mock_mod}), \
             patch('trcc.conf.get_selected_device', return_value='/dev/sg9'), \
             redirect_stdout(buf):
            result = detect(show_all=False)
        self.assertEqual(result, 0)
        self.assertIn('/dev/sg0', buf.getvalue())


# -- Settings corrupt JSON ---------------------------------------------------

class TestSettingsCorruptJSON(unittest.TestCase):

    def test_get_corrupt_json(self):
        """Corrupt JSON -> returns None."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{bad json')
            path = f.name
        try:
            with patch('trcc.conf.CONFIG_PATH', path):
                from trcc.conf import get_selected_device
                result = get_selected_device()
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_set_with_corrupt_existing(self):
        """Set device with corrupt existing file -> overwrites cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'config.json')
            with open(path, 'w') as f:
                f.write('{bad')
            with patch('trcc.conf.CONFIG_PATH', path), \
                 patch('trcc.conf.CONFIG_DIR', tmp):
                from trcc.conf import get_selected_device, save_selected_device
                save_selected_device('/dev/sg0')
                result = get_selected_device()
            self.assertEqual(result, '/dev/sg0')


# -- download_themes additional branches --------------------------------------

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
        """Exception during download -> returns 1."""
        mock_mod = MagicMock()
        mock_mod.download_pack.side_effect = RuntimeError('net error')
        with patch.dict('sys.modules', {'trcc.theme_downloader': mock_mod}):
            result = download_themes(pack='themes-320', show_list=False,
                                     force=False, show_info=False)
        self.assertEqual(result, 1)


# -- test_display KeyboardInterrupt -------------------------------------------

class TestTestDisplayExtra(unittest.TestCase):

    def test_keyboard_interrupt(self):
        svc = _mock_service()
        svc.send_pil.side_effect = KeyboardInterrupt
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            result = cli_test_display()
        self.assertEqual(result, 0)


# -- main() dispatch branches ------------------------------------------------

class TestMainDispatch(unittest.TestCase):
    """Cover main() dispatch branches for test, send, color, info, reset, setup-udev."""

    @patch.object(DisplayCommands, 'test', return_value=0)
    def test_dispatch_test(self, mock_fn):
        with patch('sys.argv', ['trcc', 'test']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(DisplayCommands, 'send_image', return_value=0)
    def test_dispatch_send(self, mock_fn):
        with patch('sys.argv', ['trcc', 'send', 'image.png']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(DisplayCommands, 'send_color', return_value=0)
    def test_dispatch_color(self, mock_fn):
        with patch('sys.argv', ['trcc', 'color', 'ff0000']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(SystemCommands, 'show_info', return_value=0)
    def test_dispatch_info(self, mock_fn):
        with patch('sys.argv', ['trcc', 'info']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(DisplayCommands, 'reset', return_value=0)
    def test_dispatch_reset(self, mock_fn):
        with patch('sys.argv', ['trcc', 'reset']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(SystemCommands, 'setup_udev', return_value=0)
    def test_dispatch_setup_udev(self, mock_fn):
        with patch('sys.argv', ['trcc', 'setup-udev', '--dry-run']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)

    @patch.object(SystemCommands, 'download_themes', return_value=0)
    def test_dispatch_download(self, mock_fn):
        with patch('sys.argv', ['trcc', 'download', '--list']):
            result = main()
        mock_fn.assert_called_once()
        self.assertEqual(result, 0)


# -- select_device exception -------------------------------------------------

class TestSelectDeviceException(unittest.TestCase):

    @patch('trcc.device_detector.detect_devices', side_effect=RuntimeError("fail"))
    def test_exception_returns_1(self, _):
        result = select_device(1)
        self.assertEqual(result, 1)


# -- send_image success ------------------------------------------------------

class TestSendImageEdge(unittest.TestCase):

    @patch('trcc.conf.get_selected_device', return_value='/dev/sg0')
    def test_send_image_exception(self, _):
        """send_image with nonexistent file -> exception -> returns 1."""
        result = send_image('/nonexistent/file.png')
        self.assertEqual(result, 1)


# -- send_color exception ----------------------------------------------------

class TestSendColorEdge(unittest.TestCase):

    def test_exception_returns_1(self):
        with patch.object(DeviceCommands, '_get_service',
                          side_effect=RuntimeError("fail")):
            result = send_color('ff0000')
        self.assertEqual(result, 1)


# -- show_info metrics display -----------------------------------------------

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


# -- setup_udev non-dry-run --------------------------------------------------

class TestSetupUdevNonDry(unittest.TestCase):

    @patch('trcc.cli.subprocess.run')
    @patch('os.path.exists', return_value=True)
    @patch('os.geteuid', return_value=0)
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_root_writes_files(self, mock_open, mock_euid, mock_exists, mock_subproc):
        result = setup_udev(dry_run=False)
        self.assertEqual(result, 0)
        # Should write udev rules and modprobe config
        self.assertGreaterEqual(mock_open.call_count, 2)
        mock_subproc.assert_any_call(["udevadm", "control", "--reload-rules"], check=False)
        mock_subproc.assert_any_call(["udevadm", "trigger"], check=False)

    @patch('trcc.cli._sudo_reexec', return_value=1)
    @patch('os.geteuid', return_value=1000)
    def test_non_root_returns_1(self, _, mock_reexec):
        result = setup_udev(dry_run=False)
        mock_reexec.assert_called_once_with("setup-udev")
        self.assertEqual(result, 1)

    @patch('trcc.cli.subprocess.run')
    @patch('os.path.exists', return_value=False)
    @patch('os.geteuid', return_value=0)
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_root_no_sysfs_quirks(self, mock_open, mock_euid, mock_exists, mock_subproc):
        """No quirks_sysfs file -> skip writing quirks."""
        result = setup_udev(dry_run=False)
        self.assertEqual(result, 0)


# -- download_themes edge paths ----------------------------------------------

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


# -- resume() ----------------------------------------------------------------

class TestResume(unittest.TestCase):
    """Test resume() command -- send last-used theme headlessly."""

    def test_no_devices(self):
        """No devices after retries -> returns 1."""
        svc = MagicMock()
        svc.detect.return_value = []
        with patch('trcc.services.DeviceService', return_value=svc), \
             patch('time.sleep'):
            result = resume()
        self.assertEqual(result, 1)

    def test_no_saved_theme(self):
        """Device with no saved theme -> returns 1."""
        dev = _make_device_info()
        svc = MagicMock()
        svc.detect.return_value = [dev]
        with patch('trcc.services.DeviceService', return_value=svc), \
             patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
             patch('trcc.conf.get_device_config', return_value={}):
            result = resume()
        self.assertEqual(result, 1)

    def test_sends_theme_from_dir(self):
        """Device with saved theme dir -> sends 00.png successfully."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a theme dir with 00.png
            theme_dir = os.path.join(tmp, 'Theme1')
            os.makedirs(theme_dir)
            from PIL import Image
            img = Image.new('RGB', (10, 10), color=(255, 0, 0))
            img.save(os.path.join(theme_dir, '00.png'))

            dev = _make_device_info()
            svc = MagicMock()
            svc.detect.return_value = [dev]
            svc.send_pil.return_value = True

            with patch('trcc.services.DeviceService', return_value=svc), \
                 patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
                 patch('trcc.conf.get_device_config', return_value={
                     'theme_path': theme_dir,
                     'brightness_level': 3,
                     'rotation': 0,
                 }):
                result = resume()
            self.assertEqual(result, 0)
            svc.send_pil.assert_called_once()

    def test_applies_brightness_and_rotation(self):
        """Resume applies brightness L1 (25%) and rotation 90."""
        with tempfile.TemporaryDirectory() as tmp:
            theme_dir = os.path.join(tmp, 'Theme1')
            os.makedirs(theme_dir)
            from PIL import Image
            img = Image.new('RGB', (10, 10), color=(0, 255, 0))
            img.save(os.path.join(theme_dir, '00.png'))

            dev = _make_device_info()
            svc = MagicMock()
            svc.detect.return_value = [dev]
            svc.send_pil.return_value = True

            with patch('trcc.services.DeviceService', return_value=svc), \
                 patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
                 patch('trcc.conf.get_device_config', return_value={
                     'theme_path': theme_dir,
                     'brightness_level': 1,
                     'rotation': 90,
                 }):
                result = resume()
            self.assertEqual(result, 0)

    def test_skips_hid_devices(self):
        """HID devices are skipped, only SCSI resumed."""
        hid_dev = _make_device_info(path='hid:0416:8001', name='LED', protocol='hid')
        svc = MagicMock()
        svc.detect.return_value = [hid_dev]
        with patch('trcc.services.DeviceService', return_value=svc):
            result = resume()
        self.assertEqual(result, 1)

    def test_theme_path_not_found(self):
        """Theme path doesn't exist on disk -> skipped."""
        dev = _make_device_info()
        svc = MagicMock()
        svc.detect.return_value = [dev]
        with patch('trcc.services.DeviceService', return_value=svc), \
             patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
             patch('trcc.conf.get_device_config', return_value={
                 'theme_path': '/nonexistent/theme/dir',
             }):
            result = resume()
        self.assertEqual(result, 1)

    def test_exception_returns_1(self):
        """Top-level exception -> returns 1."""
        with patch('trcc.services.DeviceService',
                   side_effect=RuntimeError('fail')):
            result = resume()
        self.assertEqual(result, 1)

    def test_dispatch_resume(self):
        """main() dispatches 'resume' to DisplayCommands.resume()."""
        with patch.object(DisplayCommands, 'resume', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'resume']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()

    def test_last_one_flag(self):
        """'trcc --last-one' dispatches to gui(start_hidden=True)."""
        with patch('trcc.cli.gui', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', '--last-one']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once_with(verbose=0, start_hidden=True)


# -- uninstall ---------------------------------------------------------------

class TestUninstall(unittest.TestCase):

    def test_removes_user_files(self):
        """Removes config dirs, autostart, and desktop shortcut."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "trcc"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text("{}")
            legacy_dir = home / ".trcc"
            legacy_dir.mkdir()
            (legacy_dir / "data").mkdir()
            autostart = home / ".config" / "autostart" / "trcc-linux.desktop"
            autostart.parent.mkdir(parents=True, exist_ok=True)
            autostart.write_text("[Desktop Entry]")
            desktop = home / ".local" / "share" / "applications" / "trcc-linux.desktop"
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text("[Desktop Entry]")

            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            # Root files don't exist on disk, so os.path.exists is fine unpatched
            with patch('pathlib.Path.home', return_value=home), \
                 patch('os.geteuid', return_value=1000), \
                 redirect_stdout(buf):
                result = uninstall()

            self.assertEqual(result, 0)
            self.assertFalse(config_dir.exists())
            self.assertFalse(legacy_dir.exists())
            self.assertFalse(autostart.exists())
            self.assertFalse(desktop.exists())
            self.assertIn("Removed:", buf.getvalue())

    def test_nothing_to_remove(self):
        """Clean system prints nothing-to-remove message."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with patch('pathlib.Path.home', return_value=home), \
                 patch('os.geteuid', return_value=1000), \
                 patch('os.path.exists', return_value=False), \
                 redirect_stdout(buf):
                result = uninstall()
            self.assertEqual(result, 0)
            self.assertIn("already clean", buf.getvalue())

    @patch('trcc.cli.subprocess.run')
    def test_root_removes_system_files(self, mock_subproc):
        """Root user removes udev rules and modprobe config."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # Create fake system files inside tmp to simulate /etc paths
            udev = os.path.join(tmp, "99-trcc-lcd.rules")
            modprobe = os.path.join(tmp, "trcc-lcd.conf")
            with open(udev, "w") as f:
                f.write("rules")
            with open(modprobe, "w") as f:
                f.write("options")

            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()

            # Intercept os.path.exists and os.remove to redirect /etc paths to tmp
            real_exists = os.path.exists
            removed_paths = []

            def fake_exists(p):
                if p == "/etc/udev/rules.d/99-trcc-lcd.rules":
                    return real_exists(udev)
                if p == "/etc/modprobe.d/trcc-lcd.conf":
                    return real_exists(modprobe)
                return real_exists(p)

            def fake_remove(p):
                removed_paths.append(p)

            with patch('pathlib.Path.home', return_value=home), \
                 patch('os.geteuid', return_value=0), \
                 patch('os.path.exists', side_effect=fake_exists), \
                 patch('os.remove', side_effect=fake_remove), \
                 redirect_stdout(buf):
                result = uninstall()

            self.assertEqual(result, 0)
            self.assertIn("/etc/udev/rules.d/99-trcc-lcd.rules", removed_paths)
            self.assertIn("/etc/modprobe.d/trcc-lcd.conf", removed_paths)
            # Should reload udev after removing rules
            mock_subproc.assert_any_call(["udevadm", "control", "--reload-rules"], check=False)

    def test_root_files_auto_sudo_as_user(self):
        """Non-root auto-elevates with sudo to remove root files."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            # Make os.path.exists return True for root files
            real_exists = os.path.exists
            def fake_exists(p):
                if p in ("/etc/udev/rules.d/99-trcc-lcd.rules", "/etc/modprobe.d/trcc-lcd.conf"):
                    return True
                return real_exists(p)

            mock_result = MagicMock(returncode=0)
            with patch('pathlib.Path.home', return_value=home), \
                 patch('os.geteuid', return_value=1000), \
                 patch('os.path.exists', side_effect=fake_exists), \
                 patch('trcc.cli._sudo_run', return_value=mock_result) as mock_sudo, \
                 redirect_stdout(buf):
                result = uninstall()

            self.assertEqual(result, 0)
            output = buf.getvalue()
            self.assertIn("sudo", output)
            # Verify sudo rm was called with both root files
            rm_call = mock_sudo.call_args_list[0]
            self.assertIn("rm", rm_call[0][0])

    def test_dispatch_uninstall(self):
        """main() dispatches 'uninstall' to SystemCommands.uninstall()."""
        with patch.object(SystemCommands, 'uninstall', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'uninstall']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# _probe_device / _format_device
# ---------------------------------------------------------------------------

class TestProbeDevice(unittest.TestCase):
    """Tests for _probe_device() helper."""

    def _make_dev(self, **overrides):
        from trcc.device_detector import DetectedDevice
        defaults = dict(
            vid=0x0416, pid=0x8001, vendor_name="Winbond",
            product_name="LED Controller (HID)", usb_path="1-2",
            implementation="hid_led", model="LED_DIGITAL",
            button_image="A1CZTV", protocol="hid", device_type=1,
        )
        defaults.update(overrides)
        return DetectedDevice(**defaults)

    def test_returns_empty_for_scsi(self):
        dev = self._make_dev(implementation="thermalright_lcd_v1", protocol="scsi")
        self.assertEqual(_probe_device(dev), {})

    @patch('trcc.cli.DeviceCommands._probe')
    def test_led_probe_success(self, mock_probe_method):
        # Test via the backward-compat alias which delegates to DeviceCommands._probe
        mock_info = MagicMock()
        mock_info.model_name = "AX120_DIGITAL"
        mock_info.pm = 3
        mock_info.style = MagicMock(style_id=1)
        with patch('trcc.device_led.probe_led_model', return_value=mock_info):
            result = _probe_device(self._make_dev())
        self.assertEqual(result['model'], 'AX120_DIGITAL')
        self.assertEqual(result['pm'], 3)

    def test_led_probe_exception(self):
        """Probe returns empty dict when LED probe raises."""
        with patch('trcc.device_led.probe_led_model', side_effect=Exception("usb")):
            result = _probe_device(self._make_dev())
        self.assertEqual(result, {})

    def test_hid_lcd_probe_success(self):
        """Probe resolves HID LCD device info via handshake."""
        from trcc.device_hid import HidHandshakeInfo
        mock_info = HidHandshakeInfo(
            device_type=2, mode_byte_1=100, mode_byte_2=0,
            serial="ABCDEF0123456789", resolution=(320, 320),
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = mock_info
        dev = self._make_dev(
            implementation="hid_type2", pid=0x5302, device_type=2,
        )
        with patch('trcc.device_factory.DeviceProtocolFactory.get_protocol',
                    return_value=mock_protocol):
            result = _probe_device(dev)
        self.assertEqual(result['pm'], 100)
        self.assertEqual(result['resolution'], (320, 320))
        self.assertEqual(result['serial'], "ABCDEF0123456789")

    def test_hid_lcd_probe_exception(self):
        dev = self._make_dev(implementation="hid_type2", pid=0x5302, device_type=2)
        with patch('trcc.device_factory.DeviceProtocolFactory.get_protocol',
                    side_effect=Exception("no device")):
            result = _probe_device(dev)
        self.assertEqual(result, {})

    def test_bulk_probe_success(self):
        """Probe resolves bulk device info via BulkProtocol."""
        mock_hs = MagicMock()
        mock_hs.resolution = (480, 480)
        mock_hs.model_id = 50
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = mock_hs
        dev = self._make_dev(
            vid=0x87AD, pid=0x70DB, implementation="bulk_usblcdnew",
            protocol="bulk", device_type=4,
        )
        with patch('trcc.device_factory.BulkProtocol', return_value=mock_protocol):
            result = _probe_device(dev)
        self.assertEqual(result['resolution'], (480, 480))
        self.assertEqual(result['pm'], 50)
        mock_protocol.close.assert_called_once()


class TestFormatDevice(unittest.TestCase):
    """Tests for _format_device() helper."""

    def _make_dev(self, **overrides):
        from trcc.device_detector import DetectedDevice
        defaults = dict(
            vid=0x0416, pid=0x8001, vendor_name="Winbond",
            product_name="LED Controller (HID)", usb_path="1-2",
            implementation="hid_led", model="LED_DIGITAL",
            button_image="A1CZTV", protocol="hid", device_type=1,
        )
        defaults.update(overrides)
        return DetectedDevice(**defaults)

    def test_no_probe(self):
        dev = self._make_dev(scsi_device=None)
        result = _format_device(dev, probe=False)
        # HID devices show VID:PID as path (no SCSI device)
        self.assertIn("0416:8001", result)
        self.assertIn("LED Controller (HID)", result)
        self.assertIn("[0416:8001]", result)
        self.assertIn("(HID)", result)

    def test_with_scsi_device(self):
        dev = self._make_dev(
            scsi_device="/dev/sg0", protocol="scsi",
            implementation="thermalright_lcd_v1",
        )
        result = _format_device(dev, probe=False)
        self.assertIn("/dev/sg0", result)

    def test_probe_adds_model(self):
        dev = self._make_dev()
        mock_info = MagicMock()
        mock_info.model_name = "PA120_DIGITAL"
        mock_info.pm = 16
        mock_info.style = MagicMock()
        with patch('trcc.device_led.probe_led_model', return_value=mock_info):
            result = _format_device(dev, probe=True)
        self.assertIn("model: PA120_DIGITAL", result)
        self.assertIn("PM=16", result)

    def test_probe_empty_no_extra(self):
        """No extra info appended when probe returns nothing."""
        dev = self._make_dev()
        with patch('trcc.device_led.probe_led_model', return_value=None):
            result = _format_device(dev, probe=True)
        self.assertNotIn("model:", result)


# ---------------------------------------------------------------------------
# hid_debug
# ---------------------------------------------------------------------------

class TestHidDebug(unittest.TestCase):
    """Tests for hid_debug() command."""

    @patch('trcc.device_detector.detect_devices', return_value=[])
    def test_no_hid_devices(self, _):
        result = hid_debug()
        self.assertEqual(result, 0)

    def test_exception_returns_1(self):
        with patch('trcc.device_detector.detect_devices', side_effect=Exception("fail")):
            result = hid_debug()
        self.assertEqual(result, 1)

    def test_hid_device_handshake_none(self):
        """LED device found but handshake returns None."""
        from trcc.device_detector import DetectedDevice
        dev = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond",
            product_name="LED Controller", usb_path="1-2",
            implementation="hid_led", protocol="hid", device_type=1,
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = None
        mock_protocol.last_error = None
        with patch('trcc.device_detector.detect_devices', return_value=[dev]), \
             patch('trcc.device_factory.LedProtocol', return_value=mock_protocol):
            result = hid_debug()
        self.assertEqual(result, 0)
        mock_protocol.close.assert_called_once()

    def test_hid_device_handshake_success(self):
        """LCD device found and handshake succeeds."""
        from trcc.device_detector import DetectedDevice
        from trcc.device_hid import HidHandshakeInfo
        dev = DetectedDevice(
            vid=0x0416, pid=0x5302, vendor_name="Winbond",
            product_name="USBDISPLAY (HID)", usb_path="1-2",
            implementation="hid_type2", protocol="hid", device_type=2,
        )
        info = HidHandshakeInfo(
            device_type=2, mode_byte_1=100, mode_byte_2=0,
            serial="ABCDEF0123456789", fbl=100,
            resolution=(320, 320), raw_response=bytes(64),
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = info
        with patch('trcc.device_detector.detect_devices', return_value=[dev]), \
             patch('trcc.device_factory.HidProtocol', return_value=mock_protocol):
            result = hid_debug()
        self.assertEqual(result, 0)

    def test_led_device_handshake_success(self):
        """LED device found and handshake succeeds."""
        from trcc.device_detector import DetectedDevice
        from trcc.device_led import LedDeviceStyle, LedHandshakeInfo
        dev = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond",
            product_name="LED Controller", usb_path="1-2",
            implementation="hid_led", protocol="hid", device_type=1,
        )
        style = LedDeviceStyle(1, 30, 10, 1, "AX120_DIGITAL")
        info = LedHandshakeInfo(
            pm=3, sub_type=0, style=style,
            model_name="AX120_DIGITAL", raw_response=bytes(64),
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = info
        with patch('trcc.device_detector.detect_devices', return_value=[dev]), \
             patch('trcc.device_factory.LedProtocol', return_value=mock_protocol):
            result = hid_debug()
        self.assertEqual(result, 0)

    def test_hid_device_import_error(self):
        """Import error for pyusb/hidapi shows helpful message."""
        from trcc.device_detector import DetectedDevice
        dev = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond",
            product_name="LED Controller", usb_path="1-2",
            implementation="hid_led", protocol="hid", device_type=1,
        )
        with patch('trcc.device_detector.detect_devices', return_value=[dev]), \
             patch('trcc.device_factory.LedProtocol',
                   side_effect=ImportError("No module named 'usb'")):
            result = hid_debug()
        self.assertEqual(result, 0)

    def test_dispatch_hid_debug(self):
        """main() dispatches 'hid-debug' to DiagCommands.hid_debug()."""
        with patch.object(DiagCommands, 'hid_debug', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'hid-debug']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# led_debug
# ---------------------------------------------------------------------------

class TestLedDebug(unittest.TestCase):
    """Tests for led_debug() command."""

    def test_exception_returns_1(self):
        with patch('trcc.device_factory.LedProtocol',
                   side_effect=Exception("fail")):
            result = led_debug()
        self.assertEqual(result, 1)

    def test_handshake_success(self):
        """Successful LED handshake prints device info."""
        from trcc.device_led import LedDeviceStyle, LedHandshakeInfo
        style = LedDeviceStyle(1, 30, 10, 1, "AX120_DIGITAL")
        info = LedHandshakeInfo(
            pm=3, sub_type=0, style=style,
            model_name="AX120_DIGITAL", raw_response=bytes(64),
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = info
        with patch('trcc.device_factory.LedProtocol', return_value=mock_protocol):
            result = led_debug(test=False)
        self.assertEqual(result, 0)
        mock_protocol.close.assert_called_once()

    def test_handshake_returns_none(self):
        """Handshake returns None -> returns 1."""
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = None
        mock_protocol.last_error = RuntimeError("timeout")
        with patch('trcc.device_factory.LedProtocol', return_value=mock_protocol):
            result = led_debug(test=False)
        self.assertEqual(result, 1)
        mock_protocol.close.assert_called_once()

    def test_test_colors(self):
        """test=True sends test colors via protocol.send_led_data."""
        from trcc.device_led import LedDeviceStyle, LedHandshakeInfo
        style = LedDeviceStyle(1, 30, 10, 1, "AX120_DIGITAL")
        info = LedHandshakeInfo(
            pm=3, sub_type=0, style=style,
            model_name="AX120_DIGITAL", raw_response=bytes(64),
        )
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = info
        with patch('trcc.device_factory.LedProtocol', return_value=mock_protocol), \
             patch('time.sleep'):
            result = led_debug(test=True)
        self.assertEqual(result, 0)
        # 4 colors + OFF = 5 send_led_data calls
        self.assertEqual(mock_protocol.send_led_data.call_count, 5)

    def test_dispatch_led_debug(self):
        """main() dispatches 'led-debug' to DiagCommands.led_debug()."""
        with patch.object(DiagCommands, 'led_debug', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'led-debug']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# hr10_tempd
# ---------------------------------------------------------------------------

class TestHr10Tempd(unittest.TestCase):
    """Tests for hr10_tempd() command."""

    @patch('trcc.device_led_hr10.run_hr10_daemon', return_value=0)
    def test_success(self, mock_daemon):
        result = hr10_tempd(brightness=50, drive="980", unit="F", verbose=1)
        self.assertEqual(result, 0)
        mock_daemon.assert_called_once_with(
            brightness=50, model_substr="980", unit="F", verbose=True,
        )

    @patch('trcc.device_led_hr10.run_hr10_daemon', side_effect=Exception("no device"))
    def test_exception_returns_1(self, _):
        result = hr10_tempd()
        self.assertEqual(result, 1)

    def test_dispatch_hr10_tempd(self):
        """main() dispatches 'hr10-tempd' to DiagCommands.hr10_tempd()."""
        with patch.object(DiagCommands, 'hr10_tempd', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'hr10-tempd']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# install_desktop
# ---------------------------------------------------------------------------

class TestInstallDesktop(unittest.TestCase):
    """Tests for install_desktop() command."""

    def test_installs_without_repo_root(self):
        """Succeeds even when __file__ points outside repo (pip install)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "fakehome"
            # Fake __file__ with no icons dir -- should still create .desktop
            fake_file = os.path.join(tmpdir, "site-packages", "trcc", "cli.py")
            os.makedirs(os.path.dirname(fake_file), exist_ok=True)
            Path(fake_file).touch()
            with patch('trcc.cli.__file__', fake_file), \
                 patch('pathlib.Path.home', return_value=home):
                result = install_desktop()
            self.assertEqual(result, 0)
            desktop = home / ".local" / "share" / "applications" / "trcc-linux.desktop"
            self.assertTrue(desktop.exists())
            self.assertIn("Exec=trcc gui", desktop.read_text())

    def test_installs_files(self):
        """install_desktop() creates .desktop and copies icons to home dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch('pathlib.Path.home', return_value=home):
                result = install_desktop()
            self.assertEqual(result, 0)
            desktop = home / ".local" / "share" / "applications" / "trcc-linux.desktop"
            self.assertTrue(desktop.exists())

    def test_dispatch_install_desktop(self):
        """main() dispatches 'install-desktop' to SystemCommands.install_desktop()."""
        with patch.object(SystemCommands, 'install_desktop', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'install-desktop']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

class TestReport(unittest.TestCase):
    """Tests for report() command (delegates to DebugReport)."""

    @patch('trcc.debug_report.DebugReport.collect')
    @patch('trcc.debug_report.DebugReport.__str__', return_value="mock report")
    def test_report_delegates_to_debug_report(self, mock_str, mock_collect):
        """report() creates DebugReport, collects, and prints."""
        result = report()
        self.assertEqual(result, 0)
        mock_collect.assert_called_once()

    def test_dispatch_report(self):
        """main() dispatches 'report' to SystemCommands.report()."""
        with patch.object(SystemCommands, 'report', return_value=0) as mock_fn, \
             patch('sys.argv', ['trcc', 'report']):
            result = main()
        self.assertEqual(result, 0)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# _get_service
# ---------------------------------------------------------------------------

class TestGetService(unittest.TestCase):
    """Tests for _get_service() helper."""

    def test_selects_by_path(self):
        """Explicit device_path selects matching device."""
        dev = _make_device_info(path='/dev/sg1')
        svc = MagicMock()
        svc.devices = [dev]
        svc.selected = None
        svc.detect.return_value = [dev]
        with patch('trcc.services.DeviceService', return_value=svc):
            _get_service(device_path='/dev/sg1')
        svc.select.assert_called_once_with(dev)

    def test_falls_back_to_saved(self):
        """No explicit path -> uses saved selection."""
        dev = _make_device_info(path='/dev/sg0')
        svc = MagicMock()
        svc.devices = [dev]
        svc.selected = None
        svc.detect.return_value = [dev]
        with patch('trcc.services.DeviceService', return_value=svc), \
             patch('trcc.conf.get_selected_device', return_value='/dev/sg0'):
            _get_service()
        svc.select.assert_called_once_with(dev)

    def test_no_match_selects_first(self):
        """Explicit path not found -> selects first device."""
        dev = _make_device_info(path='/dev/sg0')
        svc = MagicMock()
        svc.devices = [dev]
        svc.selected = None
        svc.detect.return_value = [dev]
        with patch('trcc.services.DeviceService', return_value=svc):
            _get_service(device_path='/dev/sg99')
        svc.select.assert_called_once_with(dev)


# ---------------------------------------------------------------------------
# Brightness / Rotation
# ---------------------------------------------------------------------------

class TestSetBrightness(unittest.TestCase):
    """Tests for DisplayCommands.set_brightness()."""

    def test_invalid_level(self):
        """Level outside 1-3 returns error."""
        self.assertEqual(DisplayCommands.set_brightness(5), 1)
        self.assertEqual(DisplayCommands.set_brightness(0), 1)

    def test_no_device(self):
        """No device returns 1."""
        svc = _mock_service()
        svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            self.assertEqual(DisplayCommands.set_brightness(2), 1)

    def test_success(self):
        """Valid level persists to config."""
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
             patch('trcc.conf.save_device_setting') as mock_save:
            result = DisplayCommands.set_brightness(2)
            self.assertEqual(result, 0)
            mock_save.assert_called_once_with('0:87cd_70db', 'brightness_level', 2)


class TestSetRotation(unittest.TestCase):
    """Tests for DisplayCommands.set_rotation()."""

    def test_invalid_degrees(self):
        """Invalid rotation returns error."""
        self.assertEqual(DisplayCommands.set_rotation(45), 1)
        self.assertEqual(DisplayCommands.set_rotation(360), 1)

    def test_no_device(self):
        """No device returns 1."""
        svc = _mock_service()
        svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            self.assertEqual(DisplayCommands.set_rotation(90), 1)

    def test_success(self):
        """Valid rotation persists to config."""
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
             patch('trcc.conf.save_device_setting') as mock_save:
            result = DisplayCommands.set_rotation(180)
            self.assertEqual(result, 0)
            mock_save.assert_called_once_with('0:87cd_70db', 'rotation', 180)


# ---------------------------------------------------------------------------
# Screencast
# ---------------------------------------------------------------------------

class TestScreencast(unittest.TestCase):
    """Tests for DisplayCommands.screencast()."""

    def test_no_device(self):
        """No device returns 1."""
        svc = _mock_service()
        svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            self.assertEqual(DisplayCommands.screencast(), 1)

    def test_keyboard_interrupt(self):
        """Ctrl+C stops cleanly."""
        svc = _mock_service()
        mock_img = MagicMock()
        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('PIL.ImageGrab.grab', side_effect=KeyboardInterrupt), \
             patch('trcc.services.ImageService.resize', return_value=mock_img):
            result = DisplayCommands.screencast()
            self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Mask
# ---------------------------------------------------------------------------

class TestLoadMask(unittest.TestCase):
    """Tests for DisplayCommands.load_mask()."""

    def test_file_not_found(self):
        """Missing path returns 1."""
        with patch('os.path.exists', return_value=False):
            self.assertEqual(DisplayCommands.load_mask('/no/such/file'), 1)

    def test_no_device(self):
        """No device returns 1."""
        svc = _mock_service()
        svc.selected = None
        with patch('os.path.exists', return_value=True), \
             patch.object(DeviceCommands, '_get_service', return_value=svc):
            self.assertEqual(DisplayCommands.load_mask('/tmp/mask.png'), 1)

    def test_success_file(self):
        """PNG file is loaded and sent."""
        svc = _mock_service()
        mock_img = MagicMock()
        mock_img.size = (320, 320)
        mock_img.mode = 'RGBA'
        mock_img.convert.return_value = mock_img
        mock_img.width = 320
        mock_img.height = 320
        with patch('os.path.exists', return_value=True), \
             patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('PIL.Image.open', return_value=mock_img), \
             patch('trcc.services.OverlayService') as MockOverlay, \
             patch('trcc.services.ImageService.solid_color', return_value=mock_img):
            overlay_inst = MockOverlay.return_value
            overlay_inst.render.return_value = mock_img
            result = DisplayCommands.load_mask('/tmp/mask.png')
            self.assertEqual(result, 0)
            svc.send_pil.assert_called_once()


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

class TestRenderOverlay(unittest.TestCase):
    """Tests for DisplayCommands.render_overlay()."""

    def test_file_not_found(self):
        """Missing path returns 1."""
        with patch('os.path.exists', return_value=False):
            self.assertEqual(DisplayCommands.render_overlay('/no/such'), 1)

    def test_info_only(self):
        """Without --send or --output, just prints info."""
        mock_img = MagicMock()
        with patch('os.path.exists', return_value=True), \
             patch('trcc.services.OverlayService') as MockOverlay, \
             patch('trcc.services.ImageService.solid_color', return_value=mock_img), \
             patch('trcc.system_info.get_all_metrics', return_value={}):
            overlay_inst = MockOverlay.return_value
            overlay_inst.load_from_dc.return_value = {}
            overlay_inst.config = {'elem1': {}}
            overlay_inst.render.return_value = mock_img
            result = DisplayCommands.render_overlay('/tmp/config1.dc')
            self.assertEqual(result, 0)

    def test_save_to_file(self):
        """--output saves rendered image."""
        mock_img = MagicMock()
        with patch('os.path.exists', return_value=True), \
             patch('trcc.services.OverlayService') as MockOverlay, \
             patch('trcc.services.ImageService.solid_color', return_value=mock_img), \
             patch('trcc.system_info.get_all_metrics', return_value={}):
            overlay_inst = MockOverlay.return_value
            overlay_inst.load_from_dc.return_value = {}
            overlay_inst.config = {}
            overlay_inst.render.return_value = mock_img
            result = DisplayCommands.render_overlay(
                '/tmp/config1.dc', output='/tmp/out.png')
            self.assertEqual(result, 0)
            mock_img.save.assert_called_once_with('/tmp/out.png')


# ---------------------------------------------------------------------------
# Theme list / load
# ---------------------------------------------------------------------------

class TestThemeList(unittest.TestCase):
    """Tests for ThemeCommands.list_themes()."""

    def test_list_local(self):
        """Lists local themes."""
        from trcc.core.models import ThemeInfo

        mock_themes = [
            ThemeInfo(name='theme_a', path=Path('/themes/a')),
            ThemeInfo(name='Custom_b', path=Path('/themes/b')),
        ]
        with patch('trcc.conf.settings') as mock_settings, \
             patch('trcc.data_repository.DataManager.ensure_all'), \
             patch('trcc.services.ThemeService.discover_local',
                   return_value=mock_themes):
            mock_settings.width = 320
            mock_settings.height = 320
            td = MagicMock()
            td.exists.return_value = True
            td.path = Path('/themes')
            mock_settings.theme_dir = td
            result = ThemeCommands.list_themes()
            self.assertEqual(result, 0)

    def test_list_cloud(self):
        """Lists cloud themes."""
        from trcc.core.models import ThemeInfo

        mock_themes = [
            ThemeInfo(name='a_cool', is_animated=True, category='a'),
        ]
        mock_web_dir = MagicMock()
        mock_web_dir.exists.return_value = True
        with patch('trcc.conf.settings') as mock_settings, \
             patch('trcc.data_repository.DataManager.ensure_all'), \
             patch('trcc.services.ThemeService.discover_cloud',
                   return_value=mock_themes):
            mock_settings.width = 320
            mock_settings.height = 320
            mock_settings.web_dir = mock_web_dir
            result = ThemeCommands.list_themes(cloud=True)
            self.assertEqual(result, 0)


class TestThemeLoad(unittest.TestCase):
    """Tests for ThemeCommands.load_theme()."""

    def test_no_device(self):
        """No device returns 1."""
        svc = _mock_service()
        svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=svc):
            self.assertEqual(ThemeCommands.load_theme('test'), 1)

    def test_theme_not_found(self):
        """Non-existent theme returns 1."""
        svc = _mock_service()
        with patch.object(DeviceCommands, '_get_service', return_value=svc), \
             patch('trcc.data_repository.DataManager.ensure_all'), \
             patch('trcc.conf.settings') as mock_settings, \
             patch('trcc.services.ThemeService.discover_local', return_value=[]):
            td = MagicMock()
            td.exists.return_value = True
            td.path = Path('/themes')
            mock_settings.theme_dir = td
            result = ThemeCommands.load_theme('nonexistent')
            self.assertEqual(result, 1)

    def test_load_success(self):
        """Found theme is loaded and sent."""
        from trcc.core.models import ThemeInfo

        svc = _mock_service()

        # Use a real temp file so Path.exists() returns True
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = Path(tmpdir) / '00.png'
            bg.write_bytes(b'')  # create empty file
            theme = ThemeInfo(
                name='test_theme', path=Path(tmpdir),
                background_path=bg)

            mock_img = MagicMock()
            with patch.object(DeviceCommands, '_get_service', return_value=svc), \
                 patch('trcc.data_repository.DataManager.ensure_all'), \
                 patch('trcc.conf.settings') as mock_settings, \
                 patch('trcc.services.ThemeService.discover_local',
                       return_value=[theme]), \
                 patch('PIL.Image.open', return_value=mock_img), \
                 patch('trcc.services.ImageService.resize', return_value=mock_img), \
                 patch('trcc.services.ImageService.apply_brightness',
                       return_value=mock_img), \
                 patch('trcc.services.ImageService.apply_rotation',
                       return_value=mock_img), \
                 patch('trcc.conf.device_config_key', return_value='0:87cd_70db'), \
                 patch('trcc.conf.get_device_config', return_value={}), \
                 patch('trcc.conf.save_device_setting'):
                td = MagicMock()
                td.exists.return_value = True
                td.path = Path(tmpdir)
                mock_settings.theme_dir = td
                mock_img.convert.return_value = mock_img
                result = ThemeCommands.load_theme('test_theme')
                self.assertEqual(result, 0)
                svc.send_pil.assert_called_once()


# ---------------------------------------------------------------------------
# LED commands
# ---------------------------------------------------------------------------

class TestLEDCommands(unittest.TestCase):
    """Tests for LEDCommands."""

    def _mock_led_svc(self):
        """Create a mock LED service with status string."""
        mock_svc = MagicMock()
        mock_svc.send_tick.return_value = True
        return mock_svc, "LED: AX120 (18 LEDs)"

    def test_set_color_invalid_hex(self):
        """Invalid hex returns 1."""
        self.assertEqual(LEDCommands.set_color('xyz'), 1)
        self.assertEqual(LEDCommands.set_color('ff'), 1)

    def test_set_color_no_device(self):
        """No LED device returns 1."""
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(None, None)):
            self.assertEqual(LEDCommands.set_color('ff0000'), 1)

    def test_set_color_success(self):
        """Valid hex sets color and sends."""
        mock_svc, status = self._mock_led_svc()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, status)):
            result = LEDCommands.set_color('00ff00')
            self.assertEqual(result, 0)
            mock_svc.set_color.assert_called_once_with(0, 255, 0)
            mock_svc.send_tick.assert_called_once()
            mock_svc.save_config.assert_called_once()

    def test_set_mode_invalid(self):
        """Unknown mode returns 1."""
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(MagicMock(), "LED")):
            # set_mode checks mode_map before using service
            pass
        self.assertEqual(LEDCommands.set_mode('explosion'), 1)

    def test_set_mode_static(self):
        """Static mode sets and sends once (no animation loop)."""
        mock_svc, status = self._mock_led_svc()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, status)):
            result = LEDCommands.set_mode('static')
            self.assertEqual(result, 0)
            mock_svc.send_tick.assert_called_once()

    def test_set_brightness_invalid(self):
        """Out of range brightness returns 1."""
        self.assertEqual(LEDCommands.set_led_brightness(-1), 1)
        self.assertEqual(LEDCommands.set_led_brightness(101), 1)

    def test_set_brightness_success(self):
        """Valid brightness sets and sends."""
        mock_svc, status = self._mock_led_svc()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, status)):
            result = LEDCommands.set_led_brightness(75)
            self.assertEqual(result, 0)
            mock_svc.set_brightness.assert_called_once_with(75)

    def test_led_off_no_device(self):
        """No LED device returns 1."""
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(None, None)):
            self.assertEqual(LEDCommands.led_off(), 1)

    def test_led_off_success(self):
        """Turn off sets global=False and sends."""
        mock_svc, status = self._mock_led_svc()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, status)):
            result = LEDCommands.led_off()
            self.assertEqual(result, 0)
            mock_svc.toggle_global.assert_called_once_with(False)
            mock_svc.send_tick.assert_called_once()


# ---------------------------------------------------------------------------
# Theme save/export/import tests
# ---------------------------------------------------------------------------

class TestThemeSave(unittest.TestCase):
    """Tests for ThemeCommands.save_theme()."""

    def test_save_no_device(self):
        """No device returns 1."""
        mock_svc = MagicMock()
        mock_svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=mock_svc):
            self.assertEqual(ThemeCommands.save_theme('MyTheme'), 1)

    def test_save_no_current_theme(self):
        """No current theme returns 1."""
        dev = _make_device_info()
        mock_svc = MagicMock()
        mock_svc.selected = dev
        with patch.object(DeviceCommands, '_get_service', return_value=mock_svc), \
             patch('trcc.conf.device_config_key', return_value='k'), \
             patch('trcc.conf.get_device_config', return_value={}):
            self.assertEqual(ThemeCommands.save_theme('MyTheme'), 1)

    def test_save_success(self):
        """Valid state saves theme."""
        dev = _make_device_info()
        mock_svc = MagicMock()
        mock_svc.selected = dev
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake theme with 00.png (ThemeDir.bg)
            from PIL import Image
            (Path(tmpdir) / '00.png').parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (320, 320), (255, 0, 0)).save(
                str(Path(tmpdir) / '00.png'))

            with patch.object(DeviceCommands, '_get_service',
                              return_value=mock_svc), \
                 patch('trcc.conf.device_config_key', return_value='k'), \
                 patch('trcc.conf.get_device_config',
                       return_value={'theme_path': tmpdir}), \
                 patch('trcc.services.theme.ThemeService.save',
                       return_value=(True, 'Saved: Custom_MyTheme')) as mock_save:
                result = ThemeCommands.save_theme('MyTheme')
                self.assertEqual(result, 0)
                mock_save.assert_called_once()


class TestThemeExport(unittest.TestCase):
    """Tests for ThemeCommands.export_theme()."""

    def test_export_no_themes(self):
        """No themes returns 1."""
        mock_settings = MagicMock()
        mock_settings.width = 320
        mock_settings.height = 320
        mock_settings.theme_dir = None
        with patch('trcc.conf.settings', mock_settings), \
             patch('trcc.data_repository.DataManager.ensure_all'):
            self.assertEqual(ThemeCommands.export_theme('foo', '/tmp/foo.tr'), 1)

    def test_export_theme_not_found(self):
        """Unknown theme name returns 1."""
        mock_settings = MagicMock()
        mock_settings.width = 320
        mock_settings.height = 320
        mock_td = MagicMock()
        mock_td.exists.return_value = True
        mock_td.path = '/themes'
        mock_settings.theme_dir = mock_td
        with patch('trcc.conf.settings', mock_settings), \
             patch('trcc.data_repository.DataManager.ensure_all'), \
             patch('trcc.services.theme.ThemeService.discover_local', return_value=[]):
            self.assertEqual(ThemeCommands.export_theme('nonexistent', '/tmp/x.tr'), 1)

    def test_export_success(self):
        """Valid theme exports."""
        mock_settings = MagicMock()
        mock_settings.width = 320
        mock_settings.height = 320
        mock_td = MagicMock()
        mock_td.exists.return_value = True
        mock_td.path = '/themes'
        mock_settings.theme_dir = mock_td

        theme = MagicMock()
        theme.name = 'MyTheme'
        theme.path = Path('/themes/MyTheme')

        with patch('trcc.conf.settings', mock_settings), \
             patch('trcc.data_repository.DataManager.ensure_all'), \
             patch('trcc.services.theme.ThemeService.discover_local',
                   return_value=[theme]), \
             patch('trcc.services.theme.ThemeService.export_tr',
                   return_value=(True, 'Exported: out.tr')) as mock_exp:
            result = ThemeCommands.export_theme('MyTheme', '/tmp/out.tr')
            self.assertEqual(result, 0)
            mock_exp.assert_called_once()


class TestThemeImport(unittest.TestCase):
    """Tests for ThemeCommands.import_theme()."""

    def test_import_no_device(self):
        """No device returns 1."""
        mock_svc = MagicMock()
        mock_svc.selected = None
        with patch.object(DeviceCommands, '_get_service', return_value=mock_svc):
            self.assertEqual(ThemeCommands.import_theme('/tmp/t.tr'), 1)

    def test_import_success(self):
        """Valid .tr file imports theme."""
        dev = _make_device_info()
        mock_svc = MagicMock()
        mock_svc.selected = dev

        imported = MagicMock()
        imported.name = 'Imported_Theme'

        with patch.object(DeviceCommands, '_get_service',
                          return_value=mock_svc), \
             patch('trcc.services.theme.ThemeService.import_tr',
                   return_value=(True, imported)):
            result = ThemeCommands.import_theme('/tmp/theme.tr')
            self.assertEqual(result, 0)

    def test_import_failure(self):
        """Failed import returns 1."""
        dev = _make_device_info()
        mock_svc = MagicMock()
        mock_svc.selected = dev

        with patch.object(DeviceCommands, '_get_service',
                          return_value=mock_svc), \
             patch('trcc.services.theme.ThemeService.import_tr',
                   return_value=(False, 'Import failed: bad format')):
            result = ThemeCommands.import_theme('/tmp/bad.tr')
            self.assertEqual(result, 1)


# ---------------------------------------------------------------------------
# LED sensor source tests
# ---------------------------------------------------------------------------

class TestLEDSensorSource(unittest.TestCase):
    """Tests for LEDCommands.set_sensor_source()."""

    def test_invalid_source(self):
        """Invalid source returns 1."""
        self.assertEqual(LEDCommands.set_sensor_source('memory'), 1)

    def test_no_device(self):
        """No LED device returns 1."""
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(None, None)):
            self.assertEqual(LEDCommands.set_sensor_source('cpu'), 1)

    def test_cpu_success(self):
        """Set CPU source succeeds."""
        mock_svc = MagicMock()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, "LED: AX120")):
            result = LEDCommands.set_sensor_source('cpu')
            self.assertEqual(result, 0)
            mock_svc.set_sensor_source.assert_called_once_with('cpu')
            mock_svc.save_config.assert_called_once()

    def test_gpu_success(self):
        """Set GPU source succeeds."""
        mock_svc = MagicMock()
        with patch.object(LEDCommands, '_get_led_service',
                          return_value=(mock_svc, "LED: AX120")):
            result = LEDCommands.set_sensor_source('GPU')
            self.assertEqual(result, 0)
            mock_svc.set_sensor_source.assert_called_once_with('gpu')


# ---------------------------------------------------------------------------
# Mask --clear tests
# ---------------------------------------------------------------------------

class TestMaskClear(unittest.TestCase):
    """Tests for mask --clear flag."""

    def test_mask_clear_dispatches_to_send_color(self):
        """'mask --clear' sends solid black."""
        with patch('sys.argv', ['trcc', 'mask', '--clear']), \
             patch.object(DisplayCommands, 'send_color',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('#000000', device=None)

    def test_mask_no_args_errors(self, capsys=None):
        """'mask' with no path and no --clear prints error."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch('sys.argv', ['trcc', 'mask']), redirect_stdout(buf):
            main()
        self.assertIn('Error: Provide a mask path or use --clear', buf.getvalue())


# ---------------------------------------------------------------------------
# Typer dispatch tests for new commands
# ---------------------------------------------------------------------------

class TestNewCommandDispatch(unittest.TestCase):
    """Verify Typer wrappers dispatch to correct methods."""

    def test_brightness_dispatches(self):
        """'brightness 2' calls DisplayCommands.set_brightness(2)."""
        with patch('sys.argv', ['trcc', 'brightness', '2']), \
             patch.object(DisplayCommands, 'set_brightness',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(2, device=None)

    def test_rotation_dispatches(self):
        """'rotation 90' calls DisplayCommands.set_rotation(90)."""
        with patch('sys.argv', ['trcc', 'rotation', '90']), \
             patch.object(DisplayCommands, 'set_rotation',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(90, device=None)

    def test_theme_list_dispatches(self):
        """'theme-list' calls ThemeCommands.list_themes()."""
        with patch('sys.argv', ['trcc', 'theme-list']), \
             patch.object(ThemeCommands, 'list_themes',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(cloud=False, category=None)

    def test_theme_load_dispatches(self):
        """'theme-load myTheme' calls ThemeCommands.load_theme()."""
        with patch('sys.argv', ['trcc', 'theme-load', 'myTheme']), \
             patch.object(ThemeCommands, 'load_theme',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('myTheme', device=None)

    def test_led_color_dispatches(self):
        """'led-color ff0000' calls LEDCommands.set_color()."""
        with patch('sys.argv', ['trcc', 'led-color', 'ff0000']), \
             patch.object(LEDCommands, 'set_color',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('ff0000')

    def test_led_mode_dispatches(self):
        """'led-mode rainbow' calls LEDCommands.set_mode()."""
        with patch('sys.argv', ['trcc', 'led-mode', 'rainbow']), \
             patch.object(LEDCommands, 'set_mode',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('rainbow')

    def test_led_brightness_dispatches(self):
        """'led-brightness 50' calls LEDCommands.set_led_brightness()."""
        with patch('sys.argv', ['trcc', 'led-brightness', '50']), \
             patch.object(LEDCommands, 'set_led_brightness',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(50)

    def test_led_off_dispatches(self):
        """'led-off' calls LEDCommands.led_off()."""
        with patch('sys.argv', ['trcc', 'led-off']), \
             patch.object(LEDCommands, 'led_off',
                          return_value=0) as mock:
            main()
            mock.assert_called_once()

    def test_screencast_dispatches(self):
        """'screencast' calls DisplayCommands.screencast()."""
        with patch('sys.argv', ['trcc', 'screencast']), \
             patch.object(DisplayCommands, 'screencast',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(
                device=None, x=0, y=0, w=0, h=0, fps=10)

    def test_mask_dispatches(self):
        """'mask /tmp/m.png' calls DisplayCommands.load_mask()."""
        with patch('sys.argv', ['trcc', 'mask', '/tmp/m.png']), \
             patch.object(DisplayCommands, 'load_mask',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('/tmp/m.png', device=None)

    def test_overlay_dispatches(self):
        """'overlay /tmp/dc' calls DisplayCommands.render_overlay()."""
        with patch('sys.argv', ['trcc', 'overlay', '/tmp/dc']), \
             patch.object(DisplayCommands, 'render_overlay',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with(
                '/tmp/dc', device=None, send=False, output=None)

    def test_theme_save_dispatches(self):
        """'theme-save MyTheme' calls ThemeCommands.save_theme()."""
        with patch('sys.argv', ['trcc', 'theme-save', 'MyTheme']), \
             patch.object(ThemeCommands, 'save_theme',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('MyTheme', device=None, video=None)

    def test_theme_export_dispatches(self):
        """'theme-export Foo /tmp/out.tr' calls ThemeCommands.export_theme()."""
        with patch('sys.argv', ['trcc', 'theme-export', 'Foo', '/tmp/out.tr']), \
             patch.object(ThemeCommands, 'export_theme',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('Foo', '/tmp/out.tr')

    def test_theme_import_dispatches(self):
        """'theme-import /tmp/t.tr' calls ThemeCommands.import_theme()."""
        with patch('sys.argv', ['trcc', 'theme-import', '/tmp/t.tr']), \
             patch.object(ThemeCommands, 'import_theme',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('/tmp/t.tr', device=None)

    def test_led_sensor_dispatches(self):
        """'led-sensor cpu' calls LEDCommands.set_sensor_source()."""
        with patch('sys.argv', ['trcc', 'led-sensor', 'cpu']), \
             patch.object(LEDCommands, 'set_sensor_source',
                          return_value=0) as mock:
            main()
            mock.assert_called_once_with('cpu')


# ---------------------------------------------------------------------------
# Backward-compat alias tests
# ---------------------------------------------------------------------------

class TestNewAliases(unittest.TestCase):
    """Verify backward-compat aliases exist."""

    def test_display_aliases(self):
        self.assertIs(set_brightness, DisplayCommands.set_brightness)
        self.assertIs(set_rotation, DisplayCommands.set_rotation)
        self.assertIs(screencast, DisplayCommands.screencast)
        self.assertIs(load_mask, DisplayCommands.load_mask)
        self.assertIs(render_overlay, DisplayCommands.render_overlay)

    def test_theme_aliases(self):
        self.assertIs(list_themes, ThemeCommands.list_themes)
        self.assertIs(load_theme, ThemeCommands.load_theme)
        self.assertIs(save_theme, ThemeCommands.save_theme)
        self.assertIs(export_theme, ThemeCommands.export_theme)
        self.assertIs(import_theme, ThemeCommands.import_theme)

    def test_led_aliases(self):
        self.assertIs(led_color, LEDCommands.set_color)
        self.assertIs(led_mode, LEDCommands.set_mode)
        self.assertIs(led_brightness, LEDCommands.set_led_brightness)
        self.assertIs(led_off, LEDCommands.led_off)
        self.assertIs(led_sensor, LEDCommands.set_sensor_source)


if __name__ == '__main__':
    unittest.main()
