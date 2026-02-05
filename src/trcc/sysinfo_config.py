"""
System Info dashboard panel configuration.

Persists sensor-to-panel bindings as JSON in ~/.config/trcc/sysinfo_config.json.
Replaces the Windows binary Data/config format.

Each panel has 4 sensor bindings (one per row), a category ID for background
image selection, and a user-editable name.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class SensorBinding:
    """Maps a single panel row to a sensor."""
    label: str        # Row label displayed on panel ("TEMP", "Usage", etc.)
    sensor_id: str    # SensorEnumerator ID ("hwmon:coretemp:temp1")
    unit: str         # Display unit suffix ("°C", "%", "MHz", etc.)


@dataclass
class PanelConfig:
    """Configuration for a single dashboard panel."""
    category_id: int  # 0=Custom, 1=CPU, 2=GPU, 3=Memory, 4=HDD, 5=Network, 6=Fan
    name: str         # Panel display name
    sensors: list[SensorBinding] = field(default_factory=list)


# Category ID → background image name
CATEGORY_IMAGES = {
    0: 'A自定义.png',
    1: 'Acpu.png',
    2: 'Agpu.png',
    3: 'Adram.png',
    4: 'Ahdd.png',
    5: 'Anet.png',
    6: 'Afan.png',
}

# Category ID → value text color
CATEGORY_COLORS = {
    0: '#9375FF',     # Custom: Purple
    1: '#32C5FF',     # CPU: Cyan
    2: '#44D7B6',     # GPU: Teal
    3: '#6DD401',     # Memory: Lime
    4: '#F7B501',     # HDD: Orange
    5: '#FA6401',     # Network: Red-orange
    6: '#E02020',     # Fan: Red
}


class SysInfoConfig:
    """Load/save dashboard panel configuration from JSON."""

    CONFIG_PATH = Path.home() / '.config' / 'trcc' / 'sysinfo_config.json'

    def __init__(self):
        self.panels: list[PanelConfig] = []

    def load(self) -> list[PanelConfig]:
        """Load from JSON file, or return defaults if not found."""
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
                print(f"[!] Failed to load sysinfo config: {e}")

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
        self.CONFIG_PATH.write_text(json.dumps(data, indent=2))

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
        from .sensor_enumerator import map_defaults
        defaults = map_defaults(enumerator)

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
