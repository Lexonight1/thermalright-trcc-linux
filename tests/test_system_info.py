"""Tests for system_info -- metric reading, fallbacks, and format_metric display.

The refactored SystemInfo delegates hardware reads to SensorEnumerator and
only uses subprocess-based fallbacks for niche sources.  Tests mock the
enumerator (discover / map_defaults / read_one / read_all) and fallback
subprocess calls at their correct paths.
"""

import unittest
from unittest.mock import MagicMock, PropertyMock, mock_open, patch

from trcc.constants import DATE_FORMATS, TIME_FORMATS, WEEKDAYS
from trcc.paths import read_sysfs
from trcc.system_info import (
    SystemInfo,
    find_hwmon_by_name,
    format_metric,
    get_all_metrics,
    get_cpu_frequency,
    get_cpu_temperature,
    get_cpu_usage,
    get_disk_stats,
    get_disk_temperature,
    get_fan_speeds,
    get_gpu_clock,
    get_gpu_temperature,
    get_gpu_usage,
    get_memory_available,
    get_memory_clock,
    get_memory_temperature,
    get_memory_usage,
    get_network_stats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_si(defaults: dict[str, str] | None = None,
             readings: dict[str, float] | None = None) -> SystemInfo:
    """Create a SystemInfo with a mocked SensorEnumerator.

    Args:
        defaults: Legacy-key -> sensor-ID mapping returned by map_defaults().
        readings: sensor-ID -> float mapping returned by read_all() / read_one().
    """
    si = SystemInfo()
    mock_enum = MagicMock()
    defaults = defaults or {}
    readings = readings or {}

    mock_enum.map_defaults.return_value = defaults
    mock_enum.read_all.return_value = readings
    mock_enum.read_one.side_effect = lambda sid: readings.get(sid)

    si._enumerator = mock_enum
    si._defaults = defaults
    return si


# ── read_sysfs ───────────────────────────────────────────────────────────────

class TestReadSysfs(unittest.TestCase):

    def test_returns_stripped_content(self):
        m = mock_open(read_data="  hello world  \n")
        with patch('builtins.open', m):
            self.assertEqual(read_sysfs('/fake'), 'hello world')

    def test_returns_none_on_error(self):
        self.assertIsNone(read_sysfs('/nonexistent/path/xyz'))


# ── find_hwmon_by_name ───────────────────────────────────────────────────────

class TestFindHwmon(unittest.TestCase):

    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.read_sysfs')
    def test_finds_matching_hwmon(self, mock_read, mock_exists):
        def side_effect(path):
            if 'hwmon2/name' in path:
                return 'coretemp'
            return None
        mock_read.side_effect = side_effect

        result = find_hwmon_by_name('coretemp')
        assert result is not None
        self.assertIn('hwmon2', result)

    @patch('trcc.system_info.os.path.exists', return_value=False)
    def test_returns_none_no_hwmon_dir(self, _):
        self.assertIsNone(find_hwmon_by_name('coretemp'))


class TestFindHwmonNoMatch(unittest.TestCase):

    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.read_sysfs', return_value='nct6775')
    def test_returns_none_when_no_match(self, *_):
        result = find_hwmon_by_name('nonexistent_driver_xyz')
        self.assertIsNone(result)


# ── CPU temperature via enumerator + fallback ────────────────────────────────

class TestGetCpuTemperature(unittest.TestCase):

    def test_reads_from_enumerator(self):
        """cpu_temperature delegates to _read_metric('cpu_temp')."""
        si = _make_si(
            defaults={'cpu_temp': 'hwmon:k10temp:temp1'},
            readings={'hwmon:k10temp:temp1': 45.0},
        )
        self.assertAlmostEqual(si.cpu_temperature, 45.0)

    def test_fallback_to_sensors(self):
        """When enumerator returns None, falls back to subprocess sensors."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': 'temp1_input: 52.0\n', 'returncode': 0
            })()
            temp = si.cpu_temperature
            assert temp is not None
            self.assertAlmostEqual(temp, 52.0)

    def test_fallback_sensors_exception_returns_none(self):
        """When enumerator returns None and sensors subprocess fails."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=Exception("no sensors")):
            self.assertIsNone(si.cpu_temperature)

    def test_backward_compat_alias(self):
        """Module-level get_cpu_temperature() delegates to _instance."""
        with patch.object(SystemInfo, 'cpu_temperature',
                          new_callable=PropertyMock, return_value=55.0):
            self.assertAlmostEqual(get_cpu_temperature(), 55.0)


# ── CPU usage via enumerator + fallback ──────────────────────────────────────

class TestGetCpuUsage(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'cpu_percent': 'psutil:cpu_percent'},
            readings={'psutil:cpu_percent': 42.0},
        )
        self.assertAlmostEqual(si.cpu_usage, 42.0)

    def test_fallback_loadavg(self):
        """Fallback to /proc/loadavg when enumerator returns None."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.read_sysfs',
                   return_value='2.50 1.00 0.50 1/234 5678'):
            usage = si.cpu_usage
            self.assertIsNotNone(usage)
            self.assertAlmostEqual(usage, 25.0)

    def test_fallback_loadavg_exception_returns_none(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.read_sysfs', return_value=None):
            self.assertIsNone(si.cpu_usage)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'cpu_usage',
                          new_callable=PropertyMock, return_value=30.0):
            self.assertAlmostEqual(get_cpu_usage(), 30.0)


# ── CPU frequency via enumerator + fallback ──────────────────────────────────

class TestGetCpuFrequency(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'cpu_freq': 'psutil:cpu_freq'},
            readings={'psutil:cpu_freq': 3500.0},
        )
        self.assertAlmostEqual(si.cpu_frequency, 3500.0)

    def test_fallback_proc_cpuinfo(self):
        """Fallback to /proc/cpuinfo when enumerator returns None."""
        si = _make_si(defaults={}, readings={})
        cpuinfo = "processor\t: 0\ncpu MHz\t\t: 4200.123\n"
        m = mock_open(read_data=cpuinfo)
        with patch('builtins.open', m):
            freq = si.cpu_frequency
            assert freq is not None
            self.assertAlmostEqual(freq, 4200.123)

    def test_fallback_cpuinfo_missing(self):
        si = _make_si(defaults={}, readings={})
        with patch('builtins.open', side_effect=FileNotFoundError):
            self.assertIsNone(si.cpu_frequency)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'cpu_frequency',
                          new_callable=PropertyMock, return_value=3600.0):
            self.assertAlmostEqual(get_cpu_frequency(), 3600.0)


# ── GPU temperature via enumerator ───────────────────────────────────────────

class TestGetGpuTemperature(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'gpu_temp': 'nvidia:0:temp'},
            readings={'nvidia:0:temp': 72.0},
        )
        self.assertAlmostEqual(si.gpu_temperature, 72.0)

    def test_returns_none_when_no_gpu(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.gpu_temperature)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'gpu_temperature',
                          new_callable=PropertyMock, return_value=65.0):
            self.assertAlmostEqual(get_gpu_temperature(), 65.0)

    def test_backward_compat_alias_none(self):
        with patch.object(SystemInfo, 'gpu_temperature',
                          new_callable=PropertyMock, return_value=None):
            self.assertIsNone(get_gpu_temperature())


# ── GPU usage via enumerator ─────────────────────────────────────────────────

class TestGetGpuUsage(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'gpu_usage': 'nvidia:0:gpu_util'},
            readings={'nvidia:0:gpu_util': 85.0},
        )
        self.assertAlmostEqual(si.gpu_usage, 85.0)

    def test_returns_none_when_no_gpu(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.gpu_usage)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'gpu_usage',
                          new_callable=PropertyMock, return_value=45.0):
            self.assertAlmostEqual(get_gpu_usage(), 45.0)

    def test_backward_compat_alias_none(self):
        with patch.object(SystemInfo, 'gpu_usage',
                          new_callable=PropertyMock, return_value=None):
            self.assertIsNone(get_gpu_usage())


# ── GPU clock via enumerator ─────────────────────────────────────────────────

class TestGetGpuClock(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'gpu_clock': 'nvidia:0:clock'},
            readings={'nvidia:0:clock': 1800.0},
        )
        self.assertAlmostEqual(si.gpu_clock, 1800.0)

    def test_returns_none_when_no_gpu(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.gpu_clock)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'gpu_clock',
                          new_callable=PropertyMock, return_value=1500.0):
            self.assertAlmostEqual(get_gpu_clock(), 1500.0)

    def test_backward_compat_alias_none(self):
        with patch.object(SystemInfo, 'gpu_clock',
                          new_callable=PropertyMock, return_value=None):
            self.assertIsNone(get_gpu_clock())


# ── Memory metrics via enumerator ────────────────────────────────────────────

class TestMemoryMetrics(unittest.TestCase):

    def test_memory_usage(self):
        si = _make_si(
            defaults={'mem_percent': 'psutil:mem_percent'},
            readings={'psutil:mem_percent': 50.0},
        )
        self.assertAlmostEqual(si.memory_usage, 50.0)

    def test_memory_available(self):
        si = _make_si(
            defaults={'mem_available': 'psutil:mem_available'},
            readings={'psutil:mem_available': 8000.0},
        )
        self.assertAlmostEqual(si.memory_available, 8000.0)

    def test_memory_usage_none(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.memory_usage)

    def test_memory_available_none(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.memory_available)

    def test_backward_compat_usage(self):
        with patch.object(SystemInfo, 'memory_usage',
                          new_callable=PropertyMock, return_value=50.0):
            self.assertAlmostEqual(get_memory_usage(), 50.0)

    def test_backward_compat_available(self):
        with patch.object(SystemInfo, 'memory_available',
                          new_callable=PropertyMock, return_value=8000.0):
            self.assertAlmostEqual(get_memory_available(), 8000.0)


# ── Memory temperature via enumerator + fallback ─────────────────────────────

class TestGetMemoryTemperature(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'mem_temp': 'hwmon:spd5118:temp1'},
            readings={'hwmon:spd5118:temp1': 42.0},
        )
        self.assertAlmostEqual(si.memory_temperature, 42.0)

    def test_fallback_lm_sensors(self):
        """When enumerator returns None, falls back to lm_sensors subprocess."""
        si = _make_si(defaults={}, readings={})
        sensors_output = (
            "coretemp-isa-0000\n"
            "  temp1_input: 55.000\n"
            "\n"
            "ddr5_dimm-virtual-0\n"
            "  temp1_input: 38.500\n"
        )
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': sensors_output, 'returncode': 0
            })()
            temp = si.memory_temperature
            self.assertIsNotNone(temp)
            self.assertAlmostEqual(temp, 38.5)

    def test_returns_none_when_unavailable(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError):
            self.assertIsNone(si.memory_temperature)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'memory_temperature',
                          new_callable=PropertyMock, return_value=40.0):
            self.assertAlmostEqual(get_memory_temperature(), 40.0)


# ── Memory clock fallback ────────────────────────────────────────────────────

class TestGetMemoryClock(unittest.TestCase):

    def test_dmidecode_configured_speed(self):
        si = _make_si()
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': 'Memory Device\n  Configured Memory Speed: 3200 MT/s\n',
                'returncode': 0
            })()
            clock = si.memory_clock
            self.assertAlmostEqual(clock, 3200.0)

    def test_dmidecode_speed_fallback(self):
        si = _make_si()
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': 'Memory Device\n  Speed: 2400 MHz\n',
                'returncode': 0
            })()
            clock = si.memory_clock
            self.assertAlmostEqual(clock, 2400.0)

    @patch('trcc.system_info.os.path.exists', return_value=False)
    @patch('trcc.system_info.subprocess.run')
    def test_lshw_fallback(self, mock_run, _):
        si = _make_si()
        mock_run.side_effect = [
            type('R', (), {'stdout': '', 'returncode': 1})(),
            type('R', (), {
                'stdout': '/0/33  memory  4096MB DIMM DDR5 4800 MHz\n',
                'returncode': 0
            })(),
        ]
        clock = si.memory_clock
        self.assertAlmostEqual(clock, 4800.0)

    @patch('trcc.system_info.read_sysfs', return_value='Type: DDR5\nFrequency: 5600 MHz\n')
    @patch('trcc.system_info.os.listdir', return_value=['mc0'])
    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError)
    def test_edac_fallback(self, *_):
        si = _make_si()
        clock = si.memory_clock
        self.assertAlmostEqual(clock, 5600.0)

    @patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError)
    @patch('trcc.system_info.os.path.exists', return_value=False)
    def test_returns_none_when_unavailable(self, *_):
        si = _make_si()
        self.assertIsNone(si.memory_clock)

    @patch('trcc.system_info.os.listdir', side_effect=PermissionError)
    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError)
    def test_edac_listdir_fails(self, *_):
        si = _make_si()
        self.assertIsNone(si.memory_clock)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'memory_clock',
                          new_callable=PropertyMock, return_value=5600.0):
            self.assertAlmostEqual(get_memory_clock(), 5600.0)


# ── Disk stats via enumerator ────────────────────────────────────────────────

class TestGetDiskStats(unittest.TestCase):

    def test_returns_stats_from_enumerator(self):
        si = _make_si(
            defaults={
                'disk_read': 'computed:disk_read',
                'disk_write': 'computed:disk_write',
                'disk_activity': 'computed:disk_activity',
            },
            readings={
                'computed:disk_read': 10.0,
                'computed:disk_write': 5.0,
                'computed:disk_activity': 25.0,
            },
        )
        result = si.disk_stats
        self.assertAlmostEqual(result['disk_read'], 10.0)
        self.assertAlmostEqual(result['disk_write'], 5.0)
        self.assertAlmostEqual(result['disk_activity'], 25.0)

    def test_returns_empty_when_no_data(self):
        si = _make_si(defaults={}, readings={})
        self.assertEqual(si.disk_stats, {})

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'disk_stats',
                          new_callable=PropertyMock,
                          return_value={'disk_read': 1.0}):
            self.assertIn('disk_read', get_disk_stats())


# ── Disk temperature via enumerator + fallback ───────────────────────────────

class TestGetDiskTemperature(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'disk_temp': 'hwmon:nvme:temp1'},
            readings={'hwmon:nvme:temp1': 38.0},
        )
        self.assertAlmostEqual(si.disk_temperature, 38.0)

    def test_smartctl_fallback(self):
        """When enumerator returns None, falls back to smartctl."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': ('ID# ATTRIBUTE_NAME  VALUE WORST THRESH TYPE\n'
                           '194 Temperature_Celsius  35  40  0  Old_age\n'),
                'returncode': 0
            })()
            temp = si.disk_temperature
            self.assertIsNotNone(temp)
            self.assertAlmostEqual(temp, 35.0)

    def test_returns_none_when_unavailable(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError):
            self.assertIsNone(si.disk_temperature)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'disk_temperature',
                          new_callable=PropertyMock, return_value=38.0):
            self.assertAlmostEqual(get_disk_temperature(), 38.0)


# ── Network stats via enumerator ─────────────────────────────────────────────

class TestGetNetworkStats(unittest.TestCase):

    def test_returns_stats_from_enumerator(self):
        si = _make_si(
            defaults={
                'net_up': 'computed:net_up',
                'net_down': 'computed:net_down',
                'net_total_up': 'computed:net_total_up',
                'net_total_down': 'computed:net_total_down',
            },
            readings={
                'computed:net_up': 100.0,
                'computed:net_down': 500.0,
                'computed:net_total_up': 200.0,
                'computed:net_total_down': 800.0,
            },
        )
        result = si.network_stats
        self.assertAlmostEqual(result['net_up'], 100.0)
        self.assertAlmostEqual(result['net_down'], 500.0)
        self.assertAlmostEqual(result['net_total_up'], 200.0)
        self.assertAlmostEqual(result['net_total_down'], 800.0)

    def test_returns_empty_when_no_data(self):
        si = _make_si(defaults={}, readings={})
        self.assertEqual(si.network_stats, {})

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'network_stats',
                          new_callable=PropertyMock,
                          return_value={'net_total_up': 100.0,
                                        'net_total_down': 500.0}):
            result = get_network_stats()
            self.assertIn('net_total_up', result)
            self.assertIn('net_total_down', result)


# ── Fan speeds via enumerator ────────────────────────────────────────────────

class TestGetFanSpeeds(unittest.TestCase):

    def test_reads_from_enumerator(self):
        si = _make_si(
            defaults={'fan_cpu': 'hwmon:nct6798:fan1'},
            readings={'hwmon:nct6798:fan1': 1200.0},
        )
        result = si.fan_speeds
        self.assertIn('fan_cpu', result)
        self.assertEqual(result['fan_cpu'], 1200.0)

    def test_no_fans(self):
        si = _make_si(defaults={}, readings={})
        self.assertEqual(si.fan_speeds, {})

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'fan_speeds',
                          new_callable=PropertyMock,
                          return_value={'fan_cpu': 1200.0}):
            result = get_fan_speeds()
            self.assertIn('fan_cpu', result)
            self.assertEqual(result['fan_cpu'], 1200.0)


# ── format_metric ────────────────────────────────────────────────────────────

class TestFormatMetric(unittest.TestCase):
    """format_metric covers temperatures, percentages, frequencies, etc."""

    # Temperatures
    def test_temp_celsius(self):
        self.assertEqual(format_metric('cpu_temp', 65.3), '65\u00b0C')

    def test_temp_fahrenheit(self):
        result = format_metric('gpu_temp', 50.0, temp_unit=1)
        self.assertEqual(result, '122\u00b0F')

    # Percentages
    def test_percent(self):
        self.assertEqual(format_metric('cpu_percent', 88.7), '89%')

    def test_usage(self):
        self.assertEqual(format_metric('gpu_usage', 42.0), '42%')

    def test_activity(self):
        self.assertEqual(format_metric('disk_activity', 12.0), '12%')

    # Frequencies
    def test_freq_mhz(self):
        self.assertEqual(format_metric('cpu_freq', 800.0), '800MHz')

    def test_freq_ghz(self):
        self.assertEqual(format_metric('gpu_clock', 1800.0), '1.8GHz')

    # Disk I/O
    def test_disk_read(self):
        self.assertEqual(format_metric('disk_read', 1.5), '1.5MB/s')

    def test_disk_write(self):
        self.assertEqual(format_metric('disk_write', 0.3), '0.3MB/s')

    # Network
    def test_net_kbs(self):
        self.assertEqual(format_metric('net_up', 512.0), '512KB/s')

    def test_net_mbs(self):
        self.assertEqual(format_metric('net_down', 2048.0), '2.0MB/s')

    def test_net_total_mb(self):
        self.assertEqual(format_metric('net_total_up', 500.0), '500MB')

    def test_net_total_gb(self):
        self.assertEqual(format_metric('net_total_down', 2048.0), '2.0GB')

    # Fan
    def test_fan(self):
        self.assertEqual(format_metric('fan_cpu', 1200.0), '1200RPM')

    # Memory available
    def test_mem_available_mb(self):
        self.assertEqual(format_metric('mem_available', 512.0), '512MB')

    def test_mem_available_gb(self):
        self.assertEqual(format_metric('mem_available', 4096.0), '4.0GB')

    # Date / time / weekday (use frozen datetime)
    @patch('trcc.system_info.datetime')
    def test_date_format_0(self, mock_dt):
        from datetime import datetime as real_dt
        fake_now = real_dt(2026, 2, 6, 14, 30, 0)
        mock_dt.now.return_value = fake_now
        result = format_metric('date', 0, date_format=0)
        self.assertEqual(result, '2026/02/06')

    @patch('trcc.system_info.datetime')
    def test_time_format_0(self, mock_dt):
        from datetime import datetime as real_dt
        fake_now = real_dt(2026, 2, 6, 14, 5, 0)
        mock_dt.now.return_value = fake_now
        result = format_metric('time', 0, time_format=0)
        self.assertEqual(result, '14:05')

    @patch('trcc.system_info.datetime')
    def test_weekday(self, mock_dt):
        from datetime import datetime as real_dt
        fake_now = real_dt(2026, 2, 6, 0, 0, 0)  # Friday
        mock_dt.now.return_value = fake_now
        result = format_metric('weekday', 0)
        self.assertEqual(result, 'FRI')

    def test_day_of_week_index(self):
        self.assertEqual(format_metric('day_of_week', 0), 'MON')
        self.assertEqual(format_metric('day_of_week', 6), 'SUN')

    # Fallback
    def test_unknown_metric(self):
        self.assertEqual(format_metric('something', 3.14), '3.1')

    # time_/date_ prefix branch
    def test_time_hour_prefix(self):
        self.assertEqual(format_metric('time_hour', 9), '09')

    def test_date_month_prefix(self):
        self.assertEqual(format_metric('date_month', 2), '02')

    @patch('trcc.system_info.datetime')
    def test_date_format_1(self, mock_dt):
        """date_format=1 is identical to 0: yyyy/MM/dd."""
        from datetime import datetime as real_dt
        fake_now = real_dt(2026, 2, 6, 14, 30, 0)
        mock_dt.now.return_value = fake_now
        result = format_metric('date', 0, date_format=1)
        self.assertEqual(result, '2026/02/06')

    @patch('trcc.system_info.datetime')
    def test_time_format_1(self, mock_dt):
        """time_format=1 uses %-I (no leading zero on hour)."""
        from datetime import datetime as real_dt
        fake_now = real_dt(2026, 2, 6, 14, 5, 0)
        mock_dt.now.return_value = fake_now
        result = format_metric('time', 0, time_format=1)
        self.assertEqual(result, '2:05 PM')


# ── Additional format_metric branches ────────────────────────────────────────

class TestFormatMetricExtra(unittest.TestCase):
    """Cover date_format 2/3/4, time_format 2, and invalid format fallbacks."""

    @patch('trcc.system_info.datetime')
    def test_date_format_2_dd_mm_yyyy(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 0, 0, 0)
        self.assertEqual(format_metric('date', 0, date_format=2), '15/03/2026')

    @patch('trcc.system_info.datetime')
    def test_date_format_3_mm_dd(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 0, 0, 0)
        self.assertEqual(format_metric('date', 0, date_format=3), '03/15')

    @patch('trcc.system_info.datetime')
    def test_date_format_4_dd_mm(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 0, 0, 0)
        self.assertEqual(format_metric('date', 0, date_format=4), '15/03')

    @patch('trcc.system_info.datetime')
    def test_date_format_invalid_falls_back(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 0, 0, 0)
        # Invalid format key falls back to format 0
        self.assertEqual(format_metric('date', 0, date_format=99), '2026/03/15')

    @patch('trcc.system_info.datetime')
    def test_time_format_2(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 9, 5, 0)
        result = format_metric('time', 0, time_format=2)
        self.assertEqual(result, '09:05')

    @patch('trcc.system_info.datetime')
    def test_time_format_invalid_falls_back(self, mock_dt):
        from datetime import datetime as real_dt
        mock_dt.now.return_value = real_dt(2026, 3, 15, 14, 30, 0)
        result = format_metric('time', 0, time_format=99)
        self.assertEqual(result, '14:30')


# ── Format dictionaries ─────────────────────────────────────────────────────

class TestFormatConstants(unittest.TestCase):

    def test_time_formats_keys(self):
        self.assertEqual(set(TIME_FORMATS.keys()), {0, 1, 2})

    def test_date_formats_keys(self):
        self.assertEqual(set(DATE_FORMATS.keys()), {0, 1, 2, 3, 4})

    def test_weekdays_length(self):
        self.assertEqual(len(WEEKDAYS), 7)
        self.assertEqual(WEEKDAYS[0], 'MON')
        self.assertEqual(WEEKDAYS[6], 'SUN')


# ── get_all_metrics via enumerator ───────────────────────────────────────────

class TestGetAllMetrics(unittest.TestCase):

    def test_basic_metrics_from_enumerator(self):
        """all_metrics reads from enumerator and includes date/time fields."""
        si = _make_si(
            defaults={
                'cpu_temp': 'hwmon:coretemp:temp1',
                'cpu_percent': 'psutil:cpu_percent',
                'cpu_freq': 'psutil:cpu_freq',
                'mem_percent': 'psutil:mem_percent',
                'mem_available': 'psutil:mem_available',
            },
            readings={
                'hwmon:coretemp:temp1': 55.0,
                'psutil:cpu_percent': 25.0,
                'psutil:cpu_freq': 3500.0,
                'psutil:mem_percent': 50.0,
                'psutil:mem_available': 8000.0,
            },
        )
        m = si.all_metrics

        # Always present: date/time fields
        self.assertIn('date', m)
        self.assertIn('time', m)
        self.assertIn('weekday', m)
        self.assertIn('day_of_week', m)

        # CPU metrics from enumerator
        self.assertIn('cpu_temp', m)
        self.assertAlmostEqual(m['cpu_temp'], 55.0)
        self.assertIn('cpu_percent', m)
        self.assertIn('cpu_freq', m)

        # Memory
        self.assertIn('mem_percent', m)
        self.assertIn('mem_available', m)

        # GPU not mapped -- keys absent
        self.assertNotIn('gpu_temp', m)

    def test_all_none_returns_minimal(self):
        """When enumerator has no readings, only date/time/weekday present."""
        si = _make_si(defaults={}, readings={})
        # Patch all fallbacks to return None
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError), \
             patch('trcc.system_info.read_sysfs', return_value=None), \
             patch('builtins.open', side_effect=FileNotFoundError):
            m = si.all_metrics
            self.assertIn('date', m)
            self.assertIn('time', m)
            self.assertIn('weekday', m)
            self.assertNotIn('cpu_temp', m)
            self.assertNotIn('gpu_temp', m)
            self.assertNotIn('mem_percent', m)

    def test_all_values_present(self):
        """All sensor keys present when enumerator provides them."""
        si = _make_si(
            defaults={
                'cpu_temp': 'hwmon:coretemp:temp1',
                'cpu_percent': 'psutil:cpu_percent',
                'cpu_freq': 'psutil:cpu_freq',
                'gpu_temp': 'nvidia:0:temp',
                'gpu_usage': 'nvidia:0:gpu_util',
                'gpu_clock': 'nvidia:0:clock',
                'mem_percent': 'psutil:mem_percent',
                'mem_available': 'psutil:mem_available',
                'mem_temp': 'hwmon:spd5118:temp1',
                'disk_temp': 'hwmon:nvme:temp1',
                'disk_read': 'computed:disk_read',
                'fan_cpu': 'hwmon:nct6798:fan1',
            },
            readings={
                'hwmon:coretemp:temp1': 55.0,
                'psutil:cpu_percent': 25.0,
                'psutil:cpu_freq': 3500.0,
                'nvidia:0:temp': 65.0,
                'nvidia:0:gpu_util': 75.0,
                'nvidia:0:clock': 2100.0,
                'psutil:mem_percent': 50.0,
                'psutil:mem_available': 8000.0,
                'hwmon:spd5118:temp1': 38.0,
                'hwmon:nvme:temp1': 40.0,
                'computed:disk_read': 50.0,
                'hwmon:nct6798:fan1': 1200.0,
            },
        )
        m = si.all_metrics
        self.assertAlmostEqual(m['gpu_temp'], 65.0)
        self.assertAlmostEqual(m['gpu_usage'], 75.0)
        self.assertAlmostEqual(m['gpu_clock'], 2100.0)
        self.assertAlmostEqual(m['mem_temp'], 38.0)
        self.assertAlmostEqual(m['disk_temp'], 40.0)
        self.assertIn('fan_cpu', m)

    def test_backward_compat_alias(self):
        with patch.object(SystemInfo, 'all_metrics',
                          new_callable=PropertyMock,
                          return_value={'date': 0, 'time': 0}):
            result = get_all_metrics()
            self.assertIn('date', result)


# ── CPU temperature fallback branches ────────────────────────────────────────

class TestCpuTempFallbacks(unittest.TestCase):

    def test_lm_sensors_tctl(self):
        """Fallback to sensors -u with Tctl match."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': 'k10temp-isa-0000\n  Tctl:\n    tctl_input: 63.500\n',
                'returncode': 0
            })()
            temp = si.cpu_temperature
            if temp is not None:
                self.assertGreater(temp, 0)

    def test_sensors_fails_returns_none(self):
        """sensors subprocess fails -> None."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=Exception("no sensors")):
            self.assertIsNone(si.cpu_temperature)


# ── CPU usage fallback branches ──────────────────────────────────────────────

class TestCpuUsageFallbacks(unittest.TestCase):

    def test_loadavg_fallback(self):
        """Fallback to /proc/loadavg."""
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.read_sysfs',
                   return_value='2.50 1.00 0.50 1/234 5678'):
            usage = si.cpu_usage
            self.assertIsNotNone(usage)
            self.assertAlmostEqual(usage, 25.0)

    def test_both_fail_returns_none(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.read_sysfs', return_value=None):
            self.assertIsNone(si.cpu_usage)


# ── CPU frequency fallback branches ──────────────────────────────────────────

class TestCpuFreqFallback(unittest.TestCase):

    def test_proc_cpuinfo_fallback(self):
        si = _make_si(defaults={}, readings={})
        m = mock_open(read_data='processor\t: 0\ncpu MHz\t\t: 3600.123\n')
        with patch('builtins.open', m):
            result = si.cpu_frequency
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result, 3600.123, places=2)

    def test_proc_cpuinfo_missing(self):
        si = _make_si(defaults={}, readings={})
        with patch('builtins.open', side_effect=FileNotFoundError):
            self.assertIsNone(si.cpu_frequency)


# ── Memory temperature lm_sensors fallback ───────────────────────────────────

class TestMemoryTempSensors(unittest.TestCase):

    def test_lm_sensors_memory_section(self):
        si = _make_si(defaults={}, readings={})
        sensors_output = (
            "coretemp-isa-0000\n"
            "  temp1_input: 55.000\n"
            "\n"
            "ddr5_dimm-virtual-0\n"
            "  temp1_input: 38.500\n"
        )
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': sensors_output, 'returncode': 0
            })()
            temp = si.memory_temperature
            self.assertIsNotNone(temp)
            self.assertAlmostEqual(temp, 38.5)

    def test_sensors_raises_returns_none(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError):
            self.assertIsNone(si.memory_temperature)


# ── Memory clock fallbacks ───────────────────────────────────────────────────

class TestMemoryClockFallbacks(unittest.TestCase):

    @patch('trcc.system_info.os.path.exists', return_value=False)
    @patch('trcc.system_info.subprocess.run')
    def test_lshw_fallback(self, mock_run, _):
        si = _make_si()
        mock_run.side_effect = [
            type('R', (), {'stdout': '', 'returncode': 1})(),
            type('R', (), {
                'stdout': '/0/33  memory  4096MB DIMM DDR5 4800 MHz\n',
                'returncode': 0
            })(),
        ]
        clock = si.memory_clock
        self.assertAlmostEqual(clock, 4800.0)

    @patch('trcc.system_info.read_sysfs', return_value='Type: DDR5\nFrequency: 5600 MHz\n')
    @patch('trcc.system_info.os.listdir', return_value=['mc0'])
    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError)
    def test_edac_fallback(self, *_):
        si = _make_si()
        clock = si.memory_clock
        self.assertAlmostEqual(clock, 5600.0)


# ── Disk temperature fallbacks ───────────────────────────────────────────────

class TestDiskTempFallbacks(unittest.TestCase):

    def test_smartctl_fallback(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {
                'stdout': ('ID# ATTRIBUTE_NAME  VALUE WORST THRESH TYPE\n'
                           '194 Temperature_Celsius  35  40  0  Old_age\n'),
                'returncode': 0
            })()
            temp = si.disk_temperature
            self.assertIsNotNone(temp)
            self.assertAlmostEqual(temp, 35.0)

    def test_smartctl_fails_returns_none(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError):
            self.assertIsNone(si.disk_temperature)


# ── Memory usage / available edge cases ──────────────────────────────────────

class TestMemoryUsageExcept(unittest.TestCase):
    """Enumerator returns None for memory metrics when unavailable."""

    def test_memory_usage_none(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.memory_usage)

    def test_memory_available_none(self):
        si = _make_si(defaults={}, readings={})
        self.assertIsNone(si.memory_available)


# ── Memory temperature fallback edge cases ───────────────────────────────────

class TestMemoryTempEdgeCases(unittest.TestCase):

    def test_sensors_exception_returns_none(self):
        si = _make_si(defaults={}, readings={})
        with patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError):
            self.assertIsNone(si.memory_temperature)


# ── SystemInfo class API ─────────────────────────────────────────────────────

class TestSystemInfoClass(unittest.TestCase):
    """Test the OOP class API directly (not via backward-compat aliases)."""

    def test_instance_lazy_enumerator(self):
        si = SystemInfo()
        self.assertIsNone(si._enumerator)
        self.assertIsNone(si._defaults)

    def test_independent_instances(self):
        """Each instance has its own enumerator reference."""
        a = SystemInfo()
        b = SystemInfo()
        a._enumerator = MagicMock()
        self.assertIsNone(b._enumerator)

    def test_cpu_temperature_property(self):
        si = _make_si(
            defaults={'cpu_temp': 'hwmon:k10temp:temp1'},
            readings={'hwmon:k10temp:temp1': 55.0},
        )
        self.assertAlmostEqual(si.cpu_temperature, 55.0)

    def test_gpu_temperature_property(self):
        si = _make_si(
            defaults={'gpu_temp': 'nvidia:0:temp'},
            readings={'nvidia:0:temp': 70.0},
        )
        self.assertAlmostEqual(si.gpu_temperature, 70.0)

    def test_all_metrics_property(self):
        si = _make_si(
            defaults={
                'cpu_temp': 'hwmon:coretemp:temp1',
                'cpu_percent': 'psutil:cpu_percent',
            },
            readings={
                'hwmon:coretemp:temp1': 60.0,
                'psutil:cpu_percent': 30.0,
            },
        )
        m = si.all_metrics
        self.assertAlmostEqual(m['cpu_temp'], 60.0)
        self.assertAlmostEqual(m['cpu_percent'], 30.0)
        self.assertNotIn('gpu_temp', m)
        self.assertIn('date', m)

    def test_format_metric_static(self):
        """format_metric is usable without an instance."""
        self.assertEqual(SystemInfo.format_metric('cpu_temp', 65.0), '65\u00b0C')
        self.assertEqual(SystemInfo.format_metric('cpu_temp', 65.0, temp_unit=1), '149\u00b0F')
        self.assertIn('%', SystemInfo.format_metric('cpu_percent', 50.0))

    def test_singleton_exists(self):
        """Module-level _instance is a SystemInfo."""
        import trcc.system_info as mod
        self.assertIsInstance(mod._instance, SystemInfo)


# ── Memory clock EDAC edge ───────────────────────────────────────────────────

class TestMemoryClock(unittest.TestCase):

    @patch('trcc.system_info.subprocess.run')
    def test_dmidecode_configured_speed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='Memory Device\n\tConfigured Memory Speed: 5600 MT/s\n')
        si = _make_si()
        result = si.memory_clock
        self.assertAlmostEqual(result, 5600.0)

    @patch('trcc.system_info.os.path.exists', return_value=True)
    @patch('trcc.system_info.os.listdir', return_value=['mc0'])
    @patch('trcc.system_info.read_sysfs', return_value='rank0: 4800 MHz')
    @patch('trcc.system_info.subprocess.run', side_effect=FileNotFoundError)
    def test_edac_fallback(self, *_):
        si = _make_si()
        result = si.memory_clock
        self.assertAlmostEqual(result, 4800.0)


# ── Disk stats partial results ───────────────────────────────────────────────

class TestDiskStatsPartial(unittest.TestCase):

    def test_only_read_write_no_activity(self):
        """Enumerator may provide read/write but not activity."""
        si = _make_si(
            defaults={
                'disk_read': 'computed:disk_read',
                'disk_write': 'computed:disk_write',
                'disk_activity': 'computed:disk_activity',
            },
            readings={
                'computed:disk_read': 10.0,
                'computed:disk_write': 5.0,
                # activity not in readings (first call, no delta)
            },
        )
        result = si.disk_stats
        self.assertIn('disk_read', result)
        self.assertIn('disk_write', result)
        self.assertNotIn('disk_activity', result)


# ── Network stats partial results ────────────────────────────────────────────

class TestNetworkStatsPartial(unittest.TestCase):

    def test_totals_without_rates(self):
        """First call may have totals but not rates."""
        si = _make_si(
            defaults={
                'net_up': 'computed:net_up',
                'net_down': 'computed:net_down',
                'net_total_up': 'computed:net_total_up',
                'net_total_down': 'computed:net_total_down',
            },
            readings={
                'computed:net_total_up': 200.0,
                'computed:net_total_down': 800.0,
                # rates not available on first call
            },
        )
        result = si.network_stats
        self.assertIn('net_total_up', result)
        self.assertIn('net_total_down', result)
        self.assertNotIn('net_up', result)
        self.assertNotIn('net_down', result)


# ── Fan speeds multiple fans ─────────────────────────────────────────────────

class TestFanSpeedsMultiple(unittest.TestCase):

    def test_multiple_fans(self):
        si = _make_si(
            defaults={
                'fan_cpu': 'hwmon:nct6798:fan1',
                'fan_gpu': 'hwmon:nct6798:fan2',
            },
            readings={
                'hwmon:nct6798:fan1': 1200.0,
                'hwmon:nct6798:fan2': 900.0,
            },
        )
        result = si.fan_speeds
        self.assertEqual(result['fan_cpu'], 1200.0)
        self.assertEqual(result['fan_gpu'], 900.0)

    def test_fan_not_in_readings(self):
        """Fan mapped but no reading available."""
        si = _make_si(
            defaults={'fan_cpu': 'hwmon:nct6798:fan1'},
            readings={},  # sensor mapped but no value
        )
        result = si.fan_speeds
        self.assertEqual(result, {})


# ── all_metrics fallback integration ─────────────────────────────────────────

class TestAllMetricsFallbacks(unittest.TestCase):

    def test_fallback_fills_missing_keys(self):
        """all_metrics calls fallbacks for keys not in enumerator readings."""
        si = _make_si(defaults={}, readings={})
        # Only cpu_temp fallback returns a value (via subprocess)
        with patch.object(si, '_fallback_cpu_temp', return_value=65.0), \
             patch.object(si, '_fallback_cpu_usage', return_value=None), \
             patch.object(si, '_fallback_cpu_freq', return_value=None), \
             patch.object(si, '_fallback_mem_temp', return_value=None), \
             patch.object(si, '_fallback_mem_clock', return_value=None), \
             patch.object(si, '_fallback_disk_temp', return_value=None):
            m = si.all_metrics
            self.assertIn('cpu_temp', m)
            self.assertAlmostEqual(m['cpu_temp'], 65.0)
            self.assertNotIn('cpu_percent', m)

    def test_enumerator_data_not_overwritten_by_fallback(self):
        """Fallbacks only run for keys NOT already in metrics from enumerator."""
        si = _make_si(
            defaults={'cpu_temp': 'hwmon:coretemp:temp1'},
            readings={'hwmon:coretemp:temp1': 55.0},
        )
        with patch.object(si, '_fallback_cpu_temp', return_value=99.0) as fb:
            m = si.all_metrics
            # Enumerator provided cpu_temp=55, fallback should NOT be called
            fb.assert_not_called()
            self.assertAlmostEqual(m['cpu_temp'], 55.0)


if __name__ == '__main__':
    unittest.main()
