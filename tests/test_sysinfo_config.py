"""Tests for sysinfo_config – dashboard panel configuration persistence."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trcc.sysinfo_config import (
    CATEGORY_COLORS,
    CATEGORY_IMAGES,
    PanelConfig,
    SensorBinding,
    SysInfoConfig,
)


class TestSensorBinding(unittest.TestCase):
    """SensorBinding dataclass basics."""

    def test_fields(self):
        b = SensorBinding(label='TEMP', sensor_id='hwmon:coretemp:temp1', unit='°C')
        self.assertEqual(b.label, 'TEMP')
        self.assertEqual(b.sensor_id, 'hwmon:coretemp:temp1')
        self.assertEqual(b.unit, '°C')


class TestPanelConfig(unittest.TestCase):
    """PanelConfig dataclass basics."""

    def test_default_sensors_empty(self):
        p = PanelConfig(category_id=1, name='CPU')
        self.assertEqual(p.sensors, [])

    def test_with_sensors(self):
        p = PanelConfig(1, 'CPU', [
            SensorBinding('TEMP', 'hw:temp1', '°C'),
        ])
        self.assertEqual(len(p.sensors), 1)


class TestCategoryConstants(unittest.TestCase):
    """Constants cover all expected categories."""

    def test_images_complete(self):
        self.assertEqual(set(CATEGORY_IMAGES.keys()), {0, 1, 2, 3, 4, 5, 6})

    def test_colors_complete(self):
        self.assertEqual(set(CATEGORY_COLORS.keys()), {0, 1, 2, 3, 4, 5, 6})

    def test_colors_are_hex(self):
        for color in CATEGORY_COLORS.values():
            self.assertTrue(color.startswith('#'))
            self.assertEqual(len(color), 7)


class TestDefaults(unittest.TestCase):
    """SysInfoConfig.defaults() structure."""

    def test_returns_six_panels(self):
        panels = SysInfoConfig.defaults()
        self.assertEqual(len(panels), 6)

    def test_each_panel_has_four_sensors(self):
        for panel in SysInfoConfig.defaults():
            self.assertEqual(len(panel.sensors), 4)

    def test_sensor_ids_empty(self):
        """Defaults have empty sensor_ids (to be auto-mapped)."""
        for panel in SysInfoConfig.defaults():
            for s in panel.sensors:
                self.assertEqual(s.sensor_id, '')

    def test_category_ids_sequential(self):
        ids = [p.category_id for p in SysInfoConfig.defaults()]
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6])


class TestSaveLoad(unittest.TestCase):
    """Round-trip persistence through JSON."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = Path(self.tmpdir) / 'sysinfo_config.json'

    def tearDown(self):
        if self.config_path.exists():
            self.config_path.unlink()
        os.rmdir(self.tmpdir)

    def _patched(self):
        return patch.object(SysInfoConfig, 'CONFIG_PATH', self.config_path)

    def test_save_creates_file(self):
        with self._patched():
            cfg = SysInfoConfig()
            cfg.panels = SysInfoConfig.defaults()
            cfg.save()
            self.assertTrue(self.config_path.exists())

    def test_roundtrip(self):
        with self._patched():
            cfg = SysInfoConfig()
            cfg.panels = [
                PanelConfig(1, 'CPU', [
                    SensorBinding('TEMP', 'hwmon:coretemp:temp1', '°C'),
                    SensorBinding('Usage', 'cpu_percent', '%'),
                ]),
            ]
            cfg.save()

            cfg2 = SysInfoConfig()
            loaded = cfg2.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].category_id, 1)
            self.assertEqual(loaded[0].name, 'CPU')
            self.assertEqual(len(loaded[0].sensors), 2)
            self.assertEqual(loaded[0].sensors[0].sensor_id, 'hwmon:coretemp:temp1')

    def test_load_missing_returns_defaults(self):
        with self._patched():
            cfg = SysInfoConfig()
            panels = cfg.load()
            self.assertEqual(len(panels), 6)  # defaults

    def test_load_corrupt_returns_defaults(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text('not json{{{')
        with self._patched():
            cfg = SysInfoConfig()
            panels = cfg.load()
            self.assertEqual(len(panels), 6)  # falls back to defaults

    def test_load_empty_panels_returns_defaults(self):
        """JSON with empty panels list falls back to defaults."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({'panels': []}))
        with self._patched():
            cfg = SysInfoConfig()
            panels = cfg.load()
            self.assertEqual(len(panels), 6)

    def test_json_has_version(self):
        with self._patched():
            cfg = SysInfoConfig()
            cfg.panels = SysInfoConfig.defaults()
            cfg.save()
            data = json.loads(self.config_path.read_text())
            self.assertEqual(data['version'], 1)


class TestAutoMap(unittest.TestCase):
    """auto_map fills empty sensor_ids from sensor discovery."""

    def test_fills_empty_ids(self):
        cfg = SysInfoConfig()
        cfg.panels = [
            PanelConfig(1, 'CPU', [
                SensorBinding('TEMP', '', '°C'),
                SensorBinding('Usage', '', '%'),
                SensorBinding('Clock', '', 'MHz'),
                SensorBinding('Power', '', 'W'),
            ]),
        ]
        mock_defaults = {
            'cpu_temp': 'hwmon:coretemp:temp1',
            'cpu_percent': 'psutil:cpu_percent',
            'cpu_freq': 'psutil:cpu_freq',
            'cpu_power': 'hwmon:power:power1',
        }
        with patch('trcc.sensor_enumerator.map_defaults', return_value=mock_defaults):
            cfg.auto_map(enumerator=None)

        self.assertEqual(cfg.panels[0].sensors[0].sensor_id, 'hwmon:coretemp:temp1')
        self.assertEqual(cfg.panels[0].sensors[1].sensor_id, 'psutil:cpu_percent')

    def test_preserves_existing_ids(self):
        cfg = SysInfoConfig()
        cfg.panels = [
            PanelConfig(1, 'CPU', [
                SensorBinding('TEMP', 'custom:my_sensor', '°C'),
                SensorBinding('Usage', '', '%'),
                SensorBinding('Clock', '', 'MHz'),
                SensorBinding('Power', '', 'W'),
            ]),
        ]
        mock_defaults = {'cpu_temp': 'hwmon:coretemp:temp1', 'cpu_percent': 'auto'}
        with patch('trcc.sensor_enumerator.map_defaults', return_value=mock_defaults):
            cfg.auto_map(enumerator=None)

        # First sensor preserved (already had an ID)
        self.assertEqual(cfg.panels[0].sensors[0].sensor_id, 'custom:my_sensor')
        # Second sensor got auto-mapped
        self.assertEqual(cfg.panels[0].sensors[1].sensor_id, 'auto')


if __name__ == '__main__':
    unittest.main()
