"""Persistence for the legacy-style sensor-dashboard panel layout.

The Windows UCSystemInfoOptions screen lets users build a grid of
4-row sensor panels (CPU / GPU / Memory / HDD / Network / Fan +
custom).  Legacy persisted the layout as ``~/.trcc/system_config.json``;
this port keeps the same on-disk shape so users can migrate without
losing their dashboards.

API:

* ``load()``      — read the JSON file, replacing :attr:`panels`.
                    Falls back to :meth:`defaults` if missing/corrupt.
* ``save()``      — atomic-write ``self.panels`` back.
* ``auto_map(enumerator)`` — fill empty ``sensor_id`` fields by asking
                              the platform's :class:`SensorEnumerator`
                              for its best-guess default per legacy key.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ...core.models import PanelConfig, SensorBinding

log = logging.getLogger(__name__)


_LEGACY_KEYS: dict[tuple[int, int], str] = {
    (1, 0): "cpu_temp",   (1, 1): "cpu_percent", (1, 2): "cpu_freq",   (1, 3): "cpu_power",
    (2, 0): "gpu_temp",   (2, 1): "gpu_usage",   (2, 2): "gpu_clock",  (2, 3): "gpu_power",
    (3, 0): "mem_temp",   (3, 1): "mem_percent", (3, 2): "mem_clock",  (3, 3): "mem_available",
    (4, 0): "disk_temp",  (4, 1): "disk_activity", (4, 2): "disk_read", (4, 3): "disk_write",
    (5, 0): "net_up",     (5, 1): "net_down",    (5, 2): "net_total_up", (5, 3): "net_total_down"  ,
    (6, 0): "fan_cpu",    (6, 1): "fan_gpu",     (6, 2): "fan_ssd",    (6, 3): "fan_sys2",
}


class SysInfoConfig:
    """Load / save the sensor-dashboard layout."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = (
            config_path or Path.home() / ".trcc" / "system_config.json"
        )
        self.panels: list[PanelConfig] = []

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[PanelConfig]:
        # Honour the legacy ``sysinfo_config.json`` filename if present.
        legacy = self._path.parent / "sysinfo_config.json"
        if legacy.exists() and not self._path.exists():
            try:
                legacy.rename(self._path)
            except OSError as e:
                log.debug("Couldn't migrate legacy filename: %s", e)

        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                panels: list[PanelConfig] = []
                for p in data.get("panels", []):
                    sensors = [
                        SensorBinding(
                            label=str(s.get("label", "")),
                            sensor_id=str(s.get("sensor_id", "")),
                            unit=str(s.get("unit", "")),
                        )
                        for s in p.get("sensors", [])
                    ]
                    panels.append(PanelConfig(
                        category_id=int(p.get("category_id", 0)),
                        name=str(p.get("name", "Custom")),
                        sensors=sensors,
                    ))
                if panels:
                    self.panels = panels
                    return self.panels
            except (OSError, json.JSONDecodeError, TypeError) as e:
                log.error("Failed to load sysinfo config %s: %s", self._path, e)

        self.panels = self.defaults()
        return self.panels

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "panels": [asdict(p) for p in self.panels],
        }
        # Atomic write — write to a sibling tempfile + rename.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as e:
            log.error("Failed to save sysinfo config %s: %s", self._path, e)

    def auto_map(self, enumerator) -> None:
        """Best-guess fill of empty ``sensor_id`` fields.

        Walks ``enumerator.discover()`` (which returns SensorReadings
        with normalized ``category`` strings like ``"cpu_temp"``) and
        picks the first reading whose category matches the legacy key
        for each ``(category_id, row_index)``.  Preserves whatever the
        user already picked.
        """
        discover = getattr(enumerator, "discover", None)
        if discover is None:
            return
        try:
            readings = discover()
        except Exception as e:
            log.debug("enumerator.discover() raised: %s", e)
            return

        # category string → first sensor_id we see with that category
        by_category: dict[str, str] = {}
        for r in readings:
            cat = getattr(r, "category", "")
            if cat and cat not in by_category:
                by_category[cat] = getattr(r, "sensor_id", "")

        for panel in self.panels:
            for i, binding in enumerate(panel.sensors):
                if binding.sensor_id:
                    continue
                legacy_key = _LEGACY_KEYS.get((panel.category_id, i))
                if legacy_key and legacy_key in by_category:
                    binding.sensor_id = by_category[legacy_key]

    @staticmethod
    def defaults() -> list[PanelConfig]:
        return [
            PanelConfig(1, "CPU", [
                SensorBinding("TEMP", "", "°C"),
                SensorBinding("Usage", "", "%"),
                SensorBinding("Clock", "", "MHz"),
                SensorBinding("Power", "", "W"),
            ]),
            PanelConfig(2, "GPU", [
                SensorBinding("TEMP", "", "°C"),
                SensorBinding("Usage", "", "%"),
                SensorBinding("Clock", "", "MHz"),
                SensorBinding("Power", "", "W"),
            ]),
            PanelConfig(3, "Memory", [
                SensorBinding("TEMP", "", "°C"),
                SensorBinding("Usage", "", "%"),
                SensorBinding("Clock", "", "MHz"),
                SensorBinding("Available", "", "MB"),
            ]),
            PanelConfig(4, "HDD", [
                SensorBinding("TEMP", "", "°C"),
                SensorBinding("Activity", "", "%"),
                SensorBinding("Read", "", "MB/s"),
                SensorBinding("Write", "", "MB/s"),
            ]),
            PanelConfig(5, "Network", [
                SensorBinding("UP rate", "", "KB/s"),
                SensorBinding("DL rate", "", "KB/s"),
                SensorBinding("Total UP", "", "MB"),
                SensorBinding("Total DL", "", "MB"),
            ]),
            PanelConfig(6, "Fan", [
                SensorBinding("CPUFAN", "", "RPM"),
                SensorBinding("GPUFAN", "", "RPM"),
                SensorBinding("SSDFAN", "", "RPM"),
                SensorBinding("FAN2", "", "RPM"),
            ]),
        ]
