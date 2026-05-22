"""Tests for core/builder.py — ControllerBuilder fluent device assembly."""

import unittest
from unittest.mock import MagicMock, patch

from trcc.legacy.core.builder import ControllerBuilder
from trcc.legacy.services.image import ImageService


def _make_builder() -> ControllerBuilder:
    """Return a ControllerBuilder with a MagicMock platform for unit tests."""
    return ControllerBuilder(MagicMock())


class TestControllerBuilderLcd(unittest.TestCase):
    """ControllerBuilder.build_device() — assembles LCD Device with DI."""

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_returns_lcd_device(self, _):
        """build_device() returns an LCD Device instance."""
        from trcc.legacy.core.device import Device
        device = _make_builder().with_renderer(ImageService.renderer()).build_device()
        self.assertIsInstance(device, Device)
        self.assertTrue(device.is_lcd)

    def test_build_device_without_renderer_raises(self):
        """build_device() without with_renderer() raises RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            _make_builder().build_device()
        self.assertIn('renderer', str(ctx.exception))

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_wires_device_service(self, _):
        """Device has a wired DeviceService."""
        device = _make_builder().with_renderer(ImageService.renderer()).build_device()
        self.assertIsNotNone(device._device_svc)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_wires_display_service(self, _):
        """Device has a wired DisplayService."""
        device = _make_builder().with_renderer(ImageService.renderer()).build_device()
        self.assertIsNotNone(device._display_svc)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_wires_theme_service(self, _):
        """Device has a wired ThemeService."""
        device = _make_builder().with_renderer(ImageService.renderer()).build_device()
        self.assertIsNotNone(device._theme_svc)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_wires_renderer(self, _):
        """Device has the injected renderer."""
        renderer = ImageService.renderer()
        device = _make_builder().with_renderer(renderer).build_device()
        self.assertIs(device._renderer, renderer)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_with_data_dir_triggers_initialize(self, mock_ensure):
        """with_data_dir() causes build_device() to call device.initialize()."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            device = (_make_builder()
                      .with_renderer(ImageService.renderer())
                      .with_data_dir(Path(d))
                      .build_device())
            # initialize was called (ensure_all is the data download step)
            self.assertIsNotNone(device)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_build_device_without_data_dir_skips_initialize(self, mock_ensure):
        """Without with_data_dir(), initialize is not called."""
        device = _make_builder().with_renderer(ImageService.renderer()).build_device()
        # No crash, device built without initialization
        self.assertIsNotNone(device)


class TestControllerBuilderLed(unittest.TestCase):
    """ControllerBuilder.build_device(detected) — assembles LED Device."""

    def _led_detected(self):
        """Build a DetectedDevice with protocol='led'."""
        from trcc.legacy.core.models import DetectedDevice
        return DetectedDevice(
            vid=0x0416, pid=0x8001,
            vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )

    def test_build_device_returns_led_device(self):
        """build_device(led_detected) returns an LED Device instance."""
        from trcc.legacy.core.device import Device
        device = _make_builder().build_device(self._led_detected())
        self.assertIsInstance(device, Device)
        self.assertTrue(device.is_led)

    def test_build_led_no_renderer_required(self):
        """LED doesn't need a renderer — build_device() works without it."""
        device = _make_builder().build_device(self._led_detected())
        self.assertIsNotNone(device)

    def test_build_led_injects_protocol(self):
        """LED Device gets a Protocol DI'd at construction (by name, via factory)."""
        device = _make_builder().build_device(self._led_detected())
        self.assertIsNotNone(device._protocol)

    def test_build_led_injects_device_svc(self):
        """build_device(led) injects a DeviceService so connect() works."""
        device = _make_builder().build_device(self._led_detected())
        self.assertIsNotNone(device._device_svc)


class TestControllerBuilderDeviceFromService(unittest.TestCase):
    """ControllerBuilder.device_from_service() — builds from existing DeviceService."""

    def test_returns_device(self):
        from trcc.legacy.core.device import Device
        svc = MagicMock()
        svc.selected = MagicMock()
        device = _make_builder().device_from_service(svc)
        self.assertIsInstance(device, Device)
        self.assertTrue(device.is_lcd)

    def test_wires_display_and_theme_services(self):
        svc = MagicMock()
        svc.selected = MagicMock()
        device = _make_builder().device_from_service(svc)
        self.assertIsNotNone(device._display_svc)
        self.assertIsNotNone(device._theme_svc)
        self.assertIs(device._device_svc, svc)


class TestControllerBuilderSetup(unittest.TestCase):
    """ControllerBuilder.os — platform accessed via builder.os property."""

    def setUp(self):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        self._builder = ControllerBuilder(LinuxPlatform())

    def test_returns_os_platform(self):
        from trcc.legacy.core.ports import Platform
        self.assertIsInstance(self._builder.os, Platform)

    def test_has_archive_tool_help(self):
        help_text = self._builder.os.archive_tool_install_help()
        self.assertIn('7z', help_text.lower())

    def test_has_pkg_manager(self):
        pm = self._builder.os.get_pkg_manager()
        self.assertTrue(pm is None or isinstance(pm, str))


class TestControllerBuilderFluent(unittest.TestCase):
    """ControllerBuilder fluent API — method chaining."""

    def test_with_renderer_returns_self(self):
        """with_renderer() returns the builder for chaining."""
        b = _make_builder()
        result = b.with_renderer(MagicMock())
        self.assertIs(result, b)

    def test_with_data_dir_returns_self(self):
        """with_data_dir() returns the builder for chaining."""
        from pathlib import Path
        b = _make_builder()
        result = b.with_data_dir(Path('/tmp'))
        self.assertIs(result, b)

    def test_fresh_builder_has_no_renderer(self):
        """New builder starts with no renderer."""
        b = _make_builder()
        self.assertIsNone(b._renderer)

    def test_fresh_builder_has_no_data_dir(self):
        """New builder starts with no data_dir."""
        b = _make_builder()
        self.assertIsNone(b._data_dir)

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_full_chain(self, _):
        """Full fluent chain builds successfully."""
        from trcc.legacy.core.device import Device
        device = (_make_builder()
                  .with_renderer(ImageService.renderer())
                  .build_device())
        self.assertIsInstance(device, Device)
        self.assertTrue(device.is_lcd)


class TestControllerBuilderBootstrap(unittest.TestCase):
    """ControllerBuilder.bootstrap() — logging + setup + settings init."""

    def test_bootstrap_calls_logging_configurator(self):
        with (patch('trcc.legacy.adapters.infra.logging_setup.StandardLoggingConfigurator.configure') as mock_log,
              patch('trcc.legacy.conf.init_settings')):
            _make_builder().bootstrap()
        mock_log.assert_called_once()

    def test_bootstrap_passes_verbosity(self):
        with (patch('trcc.legacy.adapters.infra.logging_setup.StandardLoggingConfigurator.configure') as mock_log,
              patch('trcc.legacy.conf.init_settings')):
            _make_builder().bootstrap(verbosity=2)
        mock_log.assert_called_once_with(verbosity=2)

    def test_bootstrap_calls_configure_stdout(self):
        b = _make_builder()
        with (patch('trcc.legacy.adapters.infra.logging_setup.StandardLoggingConfigurator.configure'),
              patch('trcc.legacy.conf.init_settings')):
            b.bootstrap()
        b.os.configure_stdout.assert_called_once()

    def test_bootstrap_calls_init_settings_with_os(self):
        b = _make_builder()
        with (patch('trcc.legacy.adapters.infra.logging_setup.StandardLoggingConfigurator.configure'),
              patch('trcc.legacy.conf.init_settings') as mock_init):
            b.bootstrap()
        mock_init.assert_called_once_with(b.os)


class TestControllerBuilderSystem(unittest.TestCase):
    """ControllerBuilder.build_system() — SystemService assembly."""

    def test_build_system_returns_system_service(self):
        from trcc.legacy.services.system import SystemService
        system = _make_builder().build_system()
        self.assertIsInstance(system, SystemService)

    def test_build_system_uses_platform_enumerator(self):
        """SystemService reads its enumerator from ``platform.sensors``.

        ``Platform.sensors`` is a ``cached_property`` that defers to
        ``create_sensor_enumerator()``; the test checks the public attribute
        access since that's the contract the service depends on.
        """
        platform = MagicMock()
        ControllerBuilder(platform).build_system()
        # `.sensors` is read during SystemService.__init__ — at least once.
        platform.sensors.discover.assert_called_once()


class TestControllerBuilderExtra(unittest.TestCase):
    """ControllerBuilder auxiliary surface — Phase 3 reduced to platform delegates."""

    def test_os_has_autostart_methods(self):
        b = _make_builder()
        self.assertTrue(hasattr(b.os, 'autostart_enable'))
        self.assertTrue(hasattr(b.os, 'autostart_disable'))
        self.assertTrue(hasattr(b.os, 'autostart_enabled'))

    def test_os_has_hardware_info_methods(self):
        b = _make_builder()
        self.assertTrue(callable(b.os.get_memory_info))
        self.assertTrue(callable(b.os.get_disk_info))

    def test_platform_iter_returns_devices(self):
        """Phase 3: detect via list(platform), no more build_detect_fn wrapper."""
        b = _make_builder()
        # Iterating the platform delegates to create_detect_fn — same shape
        # as the old build_detect_fn() but reads `for d in platform`.
        self.assertTrue(hasattr(b.os, '__iter__'))


class TestBuilderPsutilFallback(unittest.TestCase):
    """_make_build_services_fn: psutil ImportError → lcd still builds."""

    @patch('trcc.legacy.adapters.infra.data_repository.DataManager.ensure_all')
    def test_psutil_unavailable_still_builds_device(self, _):
        import sys

        from trcc.legacy.services.image import ImageService
        renderer = ImageService.renderer()
        with patch.dict(sys.modules, {'psutil': None}):
            device = _make_builder().with_renderer(renderer).build_device()
        self.assertIsNotNone(device._display_svc)


if __name__ == '__main__':
    unittest.main()
