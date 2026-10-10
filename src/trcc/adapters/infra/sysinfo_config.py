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
* ``auto_map(readings)`` — fill empty ``sensor_id`` fields from a
                            :meth:`SensorEnumerator.discover` snapshot,
                            per legacy key.  Returns the rows bound.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ...core._safe import load_json_or_default
from ...core.models import (
    FAN_PANEL_CATEGORY,
    FAN_PANEL_SLOTS,
    PanelConfig,
    SensorBinding,
    SensorReading,
)

log = logging.getLogger(__name__)

#: 2: FAN rows are deliberate choices, not pre-v9.10.0 positional guesses.
_VERSION = 2


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
    # Disk panel (category_id=4) — ``disk:temp`` is the hottest drive's temp,
    # published by the per-OS DiskSource (Linux hwmon nvme/drivetemp, Windows
    # LHM storage).  Boxes whose drives expose no temp sensor stay unbound (--).
    (4, 0): "disk:temp",
    (4, 1): "disk:activity",
    (4, 2): "disk:read",
    (4, 3): "disk:write",
    # Network panel (category_id=5)
    (5, 0): "net:up",
    (5, 1): "net:down",
    (5, 2): "net:total_up",
    (5, 3): "net:total_down",
    # Fan panel: each row IS a fan slot -- the panel's own reading, chosen
    # by the ONE default in SensorEnumerator.fan_slots.  Rebinding a row to a
    # concrete fan pins that slot (FAN_PANEL_SLOTS, #145).  This table used
    # to run its own label scan and spinning-order fill, a second policy
    # that could disagree with what the LCD showed.
    **{(FAN_PANEL_CATEGORY, i): slot for i, slot in enumerate(FAN_PANEL_SLOTS)},
}


def _resolve_target(target: str, readings: list[SensorReading]) -> str | None:
    """*target* if this host offers it, else ``None`` (the row stays unbound
    and renders ``--``)."""
    log.debug("_resolve_target: target=%s readings=%d", target, len(readings))
    return target if any(r.sensor_id == target for r in readings) else None


class SysInfoConfig:
    """Load / save the sensor-dashboard layout."""

    def __init__(self, config_path: Path) -> None:
        """*config_path* is REQUIRED — this used to default to
        ``Path.home() / ".trcc" / "system_config.json"``.

        That is the config dir on Linux and BSD only, and it made the correct
        call (``App`` passes ``paths().config_dir()``) indistinguishable from
        no call at all.  ``load()`` RENAMES a legacy file into place, so a
        bare ``SysInfoConfig()`` in a test was one ``load()`` away from moving
        a file in the real user's config directory.  Requiring the path makes
        the bypass unrepresentable rather than merely unused.
        """
        log.debug("__init__: config_path=%s", config_path)
        self._path = config_path
        self.panels: list[PanelConfig] = []

    @property
    def path(self) -> Path:
        log.debug("path")
        return self._path

    def load(self) -> list[PanelConfig]:
        log.info("load: path=%s", self._path)
        # Honour the legacy ``sysinfo_config.json`` filename if present.
        legacy = self._path.parent / "sysinfo_config.json"
        if legacy.exists() and not self._path.exists():
            try:
                legacy.rename(self._path)
            except OSError as e:
                log.debug("Couldn't migrate legacy filename: %s", e)

        data = load_json_or_default(self._path, None)
        if isinstance(data, dict):
            try:
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
                    if int(data.get("version", 1)) < _VERSION:
                        self._unbind_guessed_fans()
                    return self.panels
            except (TypeError, AttributeError, ValueError) as e:
                log.error("Failed to parse sysinfo config %s: %s", self._path, e)

        self.panels = self.defaults()
        return self.panels

    def _unbind_guessed_fans(self) -> None:
        """A version-1 file's FAN rows are guesses, so they pin nothing (#145).

        Before v9.10.0 the gui auto-mapped and SAVED any motherboard fan into
        these rows by position; v9.10.6 made a bound FAN row pin the LCD's fan
        slot, so the guesses started driving the panel (GPUFAN showed a case
        fan).  A guess cannot be told from a choice, so the rows are unbound
        once and the file re-saved: the next auto_map binds what a fresh
        install would.
        """
        dropped = {binding.label: binding.sensor_id
                   for panel in self.panels if panel.category_id == FAN_PANEL_CATEGORY
                   for binding in panel.sensors if binding.sensor_id}
        for panel in self.panels:
            if panel.category_id == FAN_PANEL_CATEGORY:
                for binding in panel.sensors:
                    binding.sensor_id = ""
        log.info("load: a version-1 dashboard predates fan pins (#145) — "
                 "unbound its FAN rows %s", dropped or "(none were bound)")
        self.save()

    def save(self) -> None:
        log.info("save: path=%s panels=%d", self._path, len(self.panels))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _VERSION,
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

    def auto_map(self, readings: list[SensorReading]) -> int:
        """Fill every empty ``sensor_id`` from ``_PANEL_ROW_BINDINGS``.

        One pass, exact ids.  Fan rows bind to the panel's own fan SLOTS,
        which the enumerator fills by one default (name keywords, then spinning
        order -- the #145 fix moved there), so the dashboard and the LCD show
        the same fan.  A user-customised row (non-empty ``sensor_id``) is left
        alone; a target this host does not offer stays unbound and renders
        ``--``.

        Takes the ``readings`` rather than the enumerator: ``discover()`` is
        the answer for auto-mapping -- every sensor on the host, unfiltered by
        user prefs.  Returns how many rows it bound, so a caller can tell
        "already customised" from "just mapped" without diffing.
        """
        log.info("auto_map: panels=%d readings=%d",
                 len(self.panels), len(readings))
        bound = 0
        missing: list[tuple[int, int, str]] = []
        for panel in self.panels:
            for i, binding in enumerate(panel.sensors):
                target = _PANEL_ROW_BINDINGS.get((panel.category_id, i))
                if binding.sensor_id or not target:
                    continue
                if (resolved := _resolve_target(target, readings)) is not None:
                    binding.sensor_id = resolved
                    bound += 1
                else:
                    missing.append((panel.category_id, i, target))
        log.info(
            "auto_map: bound %d row(s) across %d panel(s) "
            "(available=%d readings, %d row(s) target sensors not on this host)",
            bound, len(self.panels), len(readings), len(missing),
        )
        if missing:
            log.debug(
                "auto_map: targets not available on this host: %s",
                ["{}/{}={}".format(*m) for m in missing],
            )
        return bound

    @staticmethod
    def defaults() -> list[PanelConfig]:
        log.info("defaults: called")
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
