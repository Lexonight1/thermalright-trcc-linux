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


# (panel.category_id, row_index) → sensor_id from the aggregator.
#
# Replaces the pre-cutover ``_LEGACY_KEYS`` table that matched on the
# legacy aggregator's per-metric category strings (``"cpu_temp"`` etc.).
# next/'s ``BaselineSensors.discover`` publishes a unified vocabulary —
# categories collapsed to type names (``"temperature"``, ``"usage"``,
# ``"clock"``, ``"power"``, ``"memory"``, ``"disk_io"``, …) shared
# across subsystems, with the SUBSYSTEM encoded in the sensor id
# (``cpu:temp`` vs ``gpu:primary:temp``).  Matching by category alone
# can't disambiguate CPU temp from GPU temp; matching by sensor id
# can, since IDs are globally unique.
#
# Rows whose target id isn't published on a given box (e.g. no SMART
# disk temp source on this kernel) stay unbound, which the panel
# correctly renders as ``--`` — same as the legacy behaviour.
_PANEL_ROW_BINDINGS: dict[tuple[int, int], str] = {
    # CPU panel (category_id=1)
    (1, 0): "cpu:temp",
    (1, 1): "cpu:usage",
    (1, 2): "cpu:freq",
    (1, 3): "cpu:power",
    # GPU panel (category_id=2) — bind to ``gpu:primary:*`` aliases
    # so users with multiple GPUs see the active one without manual
    # picking.  The multi-GPU picker remains available per-row.
    (2, 0): "gpu:primary:temp",
    (2, 1): "gpu:primary:usage",
    (2, 2): "gpu:primary:clock",
    (2, 3): "gpu:primary:power",
    # Memory panel (category_id=3) — ``memory:temp`` is rare (only DDR5
    # SPD sensors expose it); leaves <unbound> on most boxes.
    (3, 0): "memory:temp",
    (3, 1): "memory:percent",
    (3, 2): "memory:used",
    (3, 3): "memory:available",
    # Disk panel (category_id=4) — ``disk:0:temp`` requires SMART; aggregator
    # currently doesn't ship a SMART source, so the row stays unbound until
    # a SmartDisk source lands.
    (4, 0): "disk:0:temp",
    (4, 1): "disk:activity",
    (4, 2): "disk:read",
    (4, 3): "disk:write",
    # Network panel (category_id=5)
    (5, 0): "net:up",
    (5, 1): "net:down",
    (5, 2): "net:total_up",
    (5, 3): "net:total_down",
    # Fan panel (category_id=6) — chassis fan IDs are hwmon-derived
    # (``fan:hwmon:nct6798:fan1:rpm``) and vary per box, so we can't
    # name them up front.  ``fan:#N:rpm`` is an indexed-pattern target
    # resolved in ``auto_map``: pick the Nth (zero-based) available
    # id matching ``fan:*:rpm``, sorted by id.  Boxes with fewer
    # chassis fans than slots leave the trailing rows <unbound>.
    (6, 0): "fan:#0:rpm",
    (6, 1): "fan:#1:rpm",
    (6, 2): "fan:#2:rpm",
    (6, 3): "fan:#3:rpm",
}


def _resolve_target(target: str, available: set[str]) -> str | None:
    """Resolve a ``_PANEL_ROW_BINDINGS`` target to a concrete sensor id.

    Two target shapes:
      * Exact id (``"cpu:temp"``) — returned iff published.
      * Indexed pattern (``"fan:#N:rpm"``) — picks the Nth (zero-based)
        sorted id whose prefix matches everything before ``#`` and
        whose suffix matches everything after the index segment.  Used
        for sensors whose ids include host-specific components
        (hwmon chip name, slot number) that aren't predictable.

    Returns ``None`` when nothing matches — caller leaves the binding
    empty and the panel renders ``--``.
    """
    if "#" not in target:
        return target if target in available else None
    prefix, _, rest = target.partition("#")
    n_str, _, suffix_after_n = rest.partition(":")
    try:
        n = int(n_str)
    except ValueError:
        log.warning(
            "auto_map: malformed indexed target %r — expected N digits "
            "after '#'", target,
        )
        return None
    suffix = f":{suffix_after_n}" if suffix_after_n else ""
    matches = sorted(
        i for i in available
        if i.startswith(prefix) and i.endswith(suffix)
    )
    return matches[n] if n < len(matches) else None


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
        """Fill every empty ``sensor_id`` from ``_PANEL_ROW_BINDINGS``.

        Walks ``enumerator.discover()`` to collect the set of sensor
        ids the host actually publishes, then for each unset binding
        looks up the canonical target id and assigns it iff the
        aggregator emits that id.  Preserves user-customised bindings
        (non-empty ``sensor_id`` is left alone).  Rows whose target
        id is not available on this host stay unbound — the panel
        renders ``--``, matching legacy behaviour.

        Single-pass; idempotent — calling again on an already-mapped
        config is a no-op.
        """
        discover = getattr(enumerator, "discover", None)
        if discover is None:
            log.warning(
                "auto_map: enumerator %r has no discover() — skipping",
                type(enumerator).__name__,
            )
            return
        try:
            readings = discover()
        except Exception as e:
            log.warning("auto_map: discover() raised %s: %s",
                        type(e).__name__, e)
            return

        available_ids: set[str] = {
            getattr(r, "sensor_id", "") for r in readings
        }
        bound = 0
        missing: list[tuple[int, int, str]] = []
        for panel in self.panels:
            for i, binding in enumerate(panel.sensors):
                if binding.sensor_id:
                    continue
                target = _PANEL_ROW_BINDINGS.get((panel.category_id, i))
                if not target:
                    continue
                resolved = _resolve_target(target, available_ids)
                if resolved is not None:
                    binding.sensor_id = resolved
                    bound += 1
                else:
                    missing.append((panel.category_id, i, target))
        log.info(
            "auto_map: bound %d row(s) across %d panel(s) "
            "(available=%d ids, %d row(s) target sensors not on this host)",
            bound, len(self.panels), len(available_ids), len(missing),
        )
        if missing:
            log.debug(
                "auto_map: targets not available on this host: %s",
                ["{}/{}={}".format(*m) for m in missing],
            )

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
