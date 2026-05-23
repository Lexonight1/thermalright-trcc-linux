"""Contract tests for WindowsPlatform — verifies it fulfils Platform.

Runs on Linux. Tests verify the interface exists — OS-specific methods
that need Windows APIs are checked for existence, not called.
"""
from __future__ import annotations

from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
from trcc.legacy.core.ports import Platform


class TestWindowsPlatformIsPlatform:
    def test_is_os_platform(self):
        assert isinstance(WindowsPlatform(), Platform)


class TestWindowsPlatformContract:
    def setup_method(self):
        self._p = WindowsPlatform()

    def test_detect_devices_exists(self):
        assert callable(self._p.detect_devices)

    def test_create_sensor_enumerator_exists(self):
        assert callable(self._p.create_sensor_enumerator)

    def test_autostart_methods_exist(self):
        assert callable(self._p.autostart_enable)
        assert callable(self._p.autostart_disable)
        assert callable(self._p.autostart_enabled)

    def test_get_memory_info_exists(self):
        assert callable(self._p.get_memory_info)

    def test_get_disk_info_exists(self):
        assert callable(self._p.get_disk_info)

    def test_config_dir_returns_string(self):
        assert isinstance(self._p.config_dir(), str)

    def test_distro_name_returns_string(self):
        assert isinstance(self._p.distro_name(), str)
