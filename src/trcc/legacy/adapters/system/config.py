"""
System Info dashboard panel configuration.

Persists sensor-to-panel bindings as JSON in ~/.trcc/system_config.json.
Replaces the Windows binary Data/config format.

Each panel has 4 sensor bindings (one per row), a category ID for background
image selection, and a user-editable name.
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

from trcc.legacy.core.models import (  # noqa: F401
    CATEGORY_COLORS,
    CATEGORY_IMAGES,
    PanelConfig,
    SensorBinding,
)

log = logging.getLogger(__name__)


class SysInfoConfig:
    """Load/save dashboard panel configuration from JSON."""

    CONFIG_PATH = Path.home() / '.trcc' / 'system_config.json'

    def __init__(self):
        self.panels: list[PanelConfig] = []

    def load(self) -> list[PanelConfig]:
        """Load from JSON file, or return defaults if not found."""
        # Migrate legacy filename
        legacy = self.CONFIG_PATH.parent / 'sysinfo_config.json'
        if legacy.exists() and not self.CONFIG_PATH.exists():
            legacy.rename(self.CONFIG_PATH)

        if self.CONFIG_PATH.exists():
            try:
                data = json.loads(self.CONFIG_PATH.read_text())
                self.panels = []
                for p in data.get('panels', []):
                    sensors = [
                        SensorBinding(
                            label=s.get('label', ''),
                            sensor_id=s.get('sensor_id', ''),
                            unit=s.get('unit', ''),
                        )
                        for s in p.get('sensors', [])
                    ]
                    self.panels.append(PanelConfig(
                        category_id=p.get('category_id', 0),
                        name=p.get('name', 'Custom'),
                        sensors=sensors,
                    ))
                if self.panels:
                    return self.panels
            except Exception as e:
                log.error("Failed to load sysinfo config: %s", e)

        # Default panels
        self.panels = self.defaults()
        return self.panels

    def save(self):
        """Save current panel config to JSON file."""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'version': 1,
            'panels': [asdict(p) for p in self.panels],
        }
        from trcc.legacy.core.io import atomic_write_json
        atomic_write_json(self.CONFIG_PATH, data)

    @staticmethod
    def defaults() -> list[PanelConfig]:
        """Return 6 default panels with empty sensor_ids (to be auto-mapped)."""
        return [
            PanelConfig(1, 'CPU', [
                SensorBinding('TEMP', '', '°C'),
                SensorBinding('Usage', '', '%'),
                SensorBinding('Clock', '', 'MHz'),
                SensorBinding('Power', '', 'W'),
            ]),
            PanelConfig(2, 'GPU', [
                SensorBinding('TEMP', '', '°C'),
                SensorBinding('Usage', '', '%'),
                SensorBinding('Clock', '', 'MHz'),
                SensorBinding('Power', '', 'W'),
            ]),
            PanelConfig(3, 'Memory', [
                SensorBinding('TEMP', '', '°C'),
                SensorBinding('Usage', '', '%'),
                SensorBinding('Clock', '', 'MHz'),
                SensorBinding('Available', '', 'MB'),
            ]),
            PanelConfig(4, 'HDD', [
                SensorBinding('TEMP', '', '°C'),
                SensorBinding('Activity', '', '%'),
                SensorBinding('Read', '', 'MB/s'),
                SensorBinding('Write', '', 'MB/s'),
            ]),
            PanelConfig(5, 'Network', [
                SensorBinding('UP rate', '', 'KB/s'),
                SensorBinding('DL rate', '', 'KB/s'),
                SensorBinding('Total UP', '', 'MB'),
                SensorBinding('Total DL', '', 'MB'),
            ]),
            PanelConfig(6, 'Fan', [
                SensorBinding('CPUFAN', '', 'RPM'),
                SensorBinding('GPUFAN', '', 'RPM'),
                SensorBinding('SSDFAN', '', 'RPM'),
                SensorBinding('FAN2', '', 'RPM'),
            ]),
        ]

    def auto_map(self, enumerator) -> None:
        """Fill empty sensor_ids with best-guess defaults from sensor discovery.

        Only fills sensors where sensor_id is empty (preserves user customizations).
        """
        defaults = enumerator.map_defaults()

        # Map category_id + row index to legacy metric key
        _LEGACY_KEYS = {
            (1, 0): 'cpu_temp',    (1, 1): 'cpu_percent', (1, 2): 'cpu_freq',    (1, 3): 'cpu_power',
            (2, 0): 'gpu_temp',    (2, 1): 'gpu_usage',   (2, 2): 'gpu_clock',   (2, 3): 'gpu_power',
            (3, 0): 'mem_temp',    (3, 1): 'mem_percent',  (3, 2): 'mem_clock',   (3, 3): 'mem_available',
            (4, 0): 'disk_temp',   (4, 1): 'disk_activity', (4, 2): 'disk_read',  (4, 3): 'disk_write',
            (5, 0): 'net_up',      (5, 1): 'net_down',     (5, 2): 'net_total_up', (5, 3): 'net_total_down',
            (6, 0): 'fan_cpu',     (6, 1): 'fan_gpu',      (6, 2): 'fan_ssd',     (6, 3): 'fan_sys2',
        }

        for panel in self.panels:
            for i, binding in enumerate(panel.sensors):
                if binding.sensor_id:
                    continue  # Already assigned
                legacy_key = _LEGACY_KEYS.get((panel.category_id, i))
                if legacy_key and legacy_key in defaults:
                    binding.sensor_id = defaults[legacy_key]
