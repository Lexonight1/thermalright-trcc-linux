"""Tests for services/system.py — SystemService.

Aggregate metric composition lives on ``Platform.metrics`` since the metrics
unification refactor; this service now owns sensor enumeration + panel
breakdowns (disk / network / fan) + formatting.

Covers:
- Construction and strict DI (platform-based)
- Sensor discovery (lazy, cached)
- Metric reading via the injected enumerator (read_all, read_one)
- Polling lifecycle (set_poll_interval, start/stop_polling)
- Panel aggregates (disk_stats, network_stats, fan_speeds)
- format_metric() — static delegation to core
- Module-level convenience API (set_instance / get_instance)
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from trcc.services.system import (
    SystemService,
    format_metric,
    get_instance,
    set_instance,
    set_poll_interval,
)


def _make_platform(enumerator: MagicMock) -> MagicMock:
    """Wrap a MagicMock enumerator behind a MagicMock platform with .sensors."""
    platform = MagicMock()
    platform.sensors = enumerator
    return platform


def _make_settings(*, hdd_enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.hdd_enabled = hdd_enabled
    return settings


# =========================================================================
# Construction
# =========================================================================


class TestConstruction:
    def test_strict_di(self):
        with pytest.raises(TypeError):
            SystemService()  # type: ignore[call-arg]

    def test_auto_discovers_on_construction(self):
        enum = MagicMock()
        enum.discover.return_value = []
        SystemService(platform=_make_platform(enum), settings=_make_settings())
        enum.discover.assert_called_once()


# =========================================================================
# Discovery
# =========================================================================


class TestDiscovery:
    def test_sensors_available_after_construction(self):
        enum = MagicMock()
        enum.discover.return_value = ['sensor1', 'sensor2']
        enum.get_sensors.return_value = ['sensor1', 'sensor2']
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.sensors == ['sensor1', 'sensor2']
        enum.discover.assert_called_once()

    def test_enumerator_property_exposes_injected_enum(self):
        enum = MagicMock()
        enum.discover.return_value = []
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.enumerator is enum


# =========================================================================
# Readings + polling lifecycle
# =========================================================================


class TestReadings:
    def test_read_all(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.read_all.return_value = {'cpu_temp': 65.0}
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.read_all() == {'cpu_temp': 65.0}

    def test_read_one(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.read_one.return_value = 42.0
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.read_one('cpu_temp') == 42.0

    def test_set_poll_interval_delegates(self):
        enum = MagicMock()
        enum.discover.return_value = []
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        svc.set_poll_interval(2.5)
        enum.set_poll_interval.assert_called_once_with(2.5)

    def test_start_stop_polling(self):
        enum = MagicMock()
        enum.discover.return_value = []
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        svc.start_polling()
        enum.start_polling.assert_called_once()
        svc.stop_polling()
        enum.stop_polling.assert_called_once()


# =========================================================================
# format_metric() — static method, pure computation
# =========================================================================


class TestFormatMetric:
    def test_temp_celsius(self):
        assert SystemService.format_metric('cpu_temp', 65.3) == "65°C"

    def test_temp_fahrenheit(self):
        assert SystemService.format_metric('cpu_temp', 149.0, temp_unit=1) == "149°F"

    def test_percent(self):
        assert SystemService.format_metric('cpu_percent', 42.7) == "43%"

    def test_usage(self):
        assert SystemService.format_metric('gpu_usage', 90.0) == "90%"

    def test_activity(self):
        assert SystemService.format_metric('disk_activity', 55.0) == "55%"

    def test_freq_mhz(self):
        assert SystemService.format_metric('cpu_freq', 800.0) == "800MHz"

    def test_freq_ghz(self):
        assert SystemService.format_metric('cpu_freq', 3600.0) == "3.6GHz"

    def test_clock_ghz(self):
        assert SystemService.format_metric('gpu_clock', 2100.0) == "2.1GHz"

    def test_disk_read(self):
        assert SystemService.format_metric('disk_read', 123.4) == "123.4MB/s"

    def test_disk_write(self):
        assert SystemService.format_metric('disk_write', 0.5) == "0.5MB/s"

    def test_net_up_kb(self):
        assert SystemService.format_metric('net_up', 512.0) == "512KB/s"

    def test_net_up_mb(self):
        assert SystemService.format_metric('net_up', 2048.0) == "2.0MB/s"

    def test_net_down_kb(self):
        assert SystemService.format_metric('net_down', 100.0) == "100KB/s"

    def test_net_total_up_mb(self):
        assert SystemService.format_metric('net_total_up', 500.0) == "500MB"

    def test_net_total_up_gb(self):
        assert SystemService.format_metric('net_total_up', 2048.0) == "2.0GB"

    def test_fan_speed(self):
        assert SystemService.format_metric('fan_cpu', 1200.0) == "1200RPM"

    def test_mem_available_mb(self):
        assert SystemService.format_metric('mem_available', 512.0) == "512MB"

    def test_mem_available_gb(self):
        assert SystemService.format_metric('mem_available', 8192.0) == "8.0GB"

    def test_time_fields_padded(self):
        assert SystemService.format_metric('time_hour', 9) == "09"
        assert SystemService.format_metric('date_month', 3) == "03"

    def test_date_format(self):
        result = SystemService.format_metric('date', 0, date_format=0)
        now = datetime.now()
        assert str(now.year) in result

    def test_time_format(self):
        result = SystemService.format_metric('time', 0, time_format=0)
        assert ':' in result

    def test_weekday(self):
        result = SystemService.format_metric('weekday', 0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_day_of_week(self):
        assert SystemService.format_metric('day_of_week', 0) == "MON"

    def test_fallback_format(self):
        assert SystemService.format_metric('unknown_metric', 3.14159) == "3.1"


# =========================================================================
# Panel aggregates — disk_stats / network_stats / fan_speeds
# =========================================================================


class TestPanelStats:
    def test_disk_stats(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.read_all.return_value = {
            'computed:disk_read': 100.0,
            'computed:disk_write': 50.0,
            'computed:disk_activity': 75.0,
        }
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        stats = svc.disk_stats
        assert stats == {
            'disk_read': 100.0,
            'disk_write': 50.0,
            'disk_activity': 75.0,
        }

    def test_disk_stats_skips_missing(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.read_all.return_value = {'computed:disk_read': 100.0}
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.disk_stats == {'disk_read': 100.0}

    def test_network_stats(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.read_all.return_value = {
            'computed:net_up': 1024.0,
            'computed:net_down': 512.0,
            'computed:net_total_up': 8.0,
            'computed:net_total_down': 16.0,
        }
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        stats = svc.network_stats
        assert stats == {
            'net_up': 1024.0,
            'net_down': 512.0,
            'net_total_up': 8.0,
            'net_total_down': 16.0,
        }

    def test_fan_speeds_from_default_map(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.map_defaults.return_value = {
            'fan_cpu': 'hwmon:fan1',
            'fan_gpu': 'hwmon:fan2',
        }
        enum.read_all.return_value = {
            'hwmon:fan1': 1200.0,
            'hwmon:fan2': 1800.0,
        }
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.fan_speeds == {'fan_cpu': 1200.0, 'fan_gpu': 1800.0}

    def test_fan_speeds_skips_unmapped(self):
        enum = MagicMock()
        enum.discover.return_value = []
        enum.map_defaults.return_value = {}
        enum.read_all.return_value = {}
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        assert svc.fan_speeds == {}


# =========================================================================
# Module-level convenience API
# =========================================================================


class TestModuleApi:
    def test_get_instance_raises_before_set(self, monkeypatch):
        # The module-level singleton is shared across tests; isolate via monkey-patch.
        monkeypatch.setattr('trcc.services.system._instance', None)
        with pytest.raises(RuntimeError, match='not initialized'):
            get_instance()

    def test_set_instance_starts_polling(self, monkeypatch):
        monkeypatch.setattr('trcc.services.system._instance', None)
        enum = MagicMock()
        enum.discover.return_value = []
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        set_instance(svc)
        enum.start_polling.assert_called_once()
        assert get_instance() is svc

    def test_module_set_poll_interval_delegates_to_singleton(self, monkeypatch):
        monkeypatch.setattr('trcc.services.system._instance', None)
        enum = MagicMock()
        enum.discover.return_value = []
        svc = SystemService(platform=_make_platform(enum), settings=_make_settings())
        set_instance(svc)
        set_poll_interval(3.0)
        enum.set_poll_interval.assert_called_once_with(3.0)

    def test_module_format_metric_matches_class_method(self):
        # Pure formatter — no singleton needed.
        assert format_metric('cpu_temp', 65.0) == SystemService.format_metric('cpu_temp', 65.0)
