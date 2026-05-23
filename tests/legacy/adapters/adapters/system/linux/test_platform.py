"""Contract tests for LinuxPlatform — verifies it fulfils Platform.

All methods are exercised on Linux directly (no mocking needed — the
concrete Linux adapters are available in the test environment).
"""
from __future__ import annotations

from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
from trcc.legacy.core.ports import Platform, SensorEnumerator


class TestLinuxPlatformIsPlatform:
    def test_is_os_platform(self):
        assert isinstance(LinuxPlatform(), Platform)


class TestLinuxPlatformContract:
    """Platform interface methods exist and return correct types."""

    def setup_method(self):
        self._p = LinuxPlatform()

    def test_detect_devices_returns_list(self):
        result = self._p.detect_devices()
        assert isinstance(result, list)

    def test_create_sensor_enumerator_returns_sensor_enumerator(self):
        assert isinstance(self._p.create_sensor_enumerator(), SensorEnumerator)

    def test_create_scsi_transport_returns_object(self):
        # Can't create without a real device, but method exists
        assert hasattr(self._p, 'create_scsi_transport')

    def test_get_memory_info_returns_list(self):
        assert isinstance(self._p.get_memory_info(), list)

    def test_get_disk_info_returns_list(self):
        assert isinstance(self._p.get_disk_info(), list)

    def test_autostart_methods_exist(self):
        assert callable(self._p.autostart_enable)
        assert callable(self._p.autostart_disable)
        assert callable(self._p.autostart_enabled)

    def test_config_dir_returns_string(self):
        assert isinstance(self._p.config_dir(), str)

    def test_distro_name_returns_string(self):
        assert isinstance(self._p.distro_name(), str)
        assert len(self._p.distro_name()) > 0
