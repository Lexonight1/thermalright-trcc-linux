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

from ...core._safe import load_json_or_default
from ...core.models import PanelConfig, SensorBinding, SensorReading

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
    # Fan panel (category_id=6) — chassis fan IDs are hwmon-derived
    # (``fan:hwmon:nct6798:fan1:rpm``) and vary per box, AND the
    # mapping "CPUFAN slot wants the CPU cooler's fan" needs to
    # respect the fan's label, not its enumeration order.  Targets
    # are label-keyword patterns; ``auto_map`` scans fan readings'
    # ``label`` field for keyword hits.  Slots whose keyword scan
    # finds no match fall back to positional fill (legacy parity).
    (6, 0): "fan:label:cpu",
    (6, 1): "fan:label:gpu",
    (6, 2): "fan:label:ssd|nvme|m.2",
    (6, 3): "fan:label:sys|chassis|case|pump",
}


def _resolve_target(
    target: str,
    readings: list[SensorReading],
    *,
    already_bound: set[str] = frozenset(),  # type: ignore[assignment]
) -> str | None:
    """Resolve a ``_PANEL_ROW_BINDINGS`` target to a concrete sensor id.

    Two target shapes:

      * **Exact id** (``"cpu:temp"``) — returned iff the id appears
        in ``readings``.

      * **Label keyword pattern** (``"fan:label:cpu"``, or
        ``"fan:label:ssd|nvme|m.2"``) — scans every reading whose
        sensor_id starts with ``fan:`` and ends with ``:rpm`` for
        the first one whose ``label`` (case-insensitive) contains
        any of the pipe-separated keywords.  Used for hwmon-derived
        fan ids whose subsystem (CPU / GPU / SSD / chassis) is
        identified by label, not by enumeration order.  Ports
        legacy's ``SensorEnumerator._map_fans`` keyword scan to the
        panel-config layer.

        ``already_bound`` lets the caller exclude readings already
        consumed by an earlier row's keyword match so a single fan
        doesn't land in two slots.

    Returns ``None`` when nothing matches — caller leaves the binding
    empty and falls back to its own logic (positional fill for fans,
    ``--`` rendering for other panels).
    """
    log.debug("_resolve_target: target=%s readings=%d", target, len(readings))
    if target.startswith("fan:label:"):
        keywords = [
            kw.strip().lower()
            for kw in target.removeprefix("fan:label:").split("|")
            if kw.strip()
        ]
        if not keywords:
            log.warning("auto_map: empty keyword list in target %r", target)
            return None
        for r in readings:
            if r.sensor_id in already_bound:
                continue
            if not r.sensor_id.startswith("fan:") or not r.sensor_id.endswith(":rpm"):
                continue
            label = (r.label or r.sensor_id).lower()
            if any(kw in label for kw in keywords):
                return r.sensor_id
        return None
    # Exact-id match (every non-fan target uses this path).
    for r in readings:
        if r.sensor_id == target:
            return r.sensor_id
    return None


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
                    return self.panels
            except (TypeError, AttributeError, ValueError) as e:
                log.error("Failed to parse sysinfo config %s: %s", self._path, e)

        self.panels = self.defaults()
        return self.panels

    def save(self) -> None:
        log.info("save: path=%s panels=%d", self._path, len(self.panels))
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

        Two passes:

          1. **Label / exact-id resolution.**  For each unset binding,
             look up the target in ``_PANEL_ROW_BINDINGS`` and call
             :func:`_resolve_target`.  Exact-id targets bind to the
             matching id; label-keyword targets scan fan readings'
             labels.  An already-bound fan id is excluded from
             subsequent slots so a single fan doesn't land twice.

          2. **Positional fan fallback.**  Rows whose label scan
             returned ``None`` (no fan name contains the slot's
             keywords — common when hwmon labels are generic "fan1",
             "fan2") fill in enumeration order from the still-
             unconsumed ``fan:*:rpm`` ids.  Matches legacy
             ``SensorEnumerator._map_fans`` semantics: label-first,
             positional second.

        Preserves user-customised bindings (non-empty ``sensor_id``
        is left alone).  Non-fan rows whose target id is not
        available on this host stay unbound — the panel renders
        ``--``.
        """
        log.info("auto_map: panels=%d", len(self.panels))
        discover = getattr(enumerator, "discover", None)
        if discover is None:
            log.warning(
                "auto_map: enumerator %r has no discover() — skipping",
                type(enumerator).__name__,
            )
            return
        try:
            readings = list(discover())
        except Exception as e:
            log.warning("auto_map: discover() raised %s: %s",
                        type(e).__name__, e)
            return

        bound = 0
        missing: list[tuple[int, int, str]] = []
        # First pass: label-aware + exact-id resolution.  Track
        # already-bound fan ids so the second slot's keyword scan
        # can't pick the same fan as the first.
        bound_fan_ids: set[str] = set()
        for panel in self.panels:
            for i, binding in enumerate(panel.sensors):
                if binding.sensor_id:
                    if binding.sensor_id.startswith("fan:") and binding.sensor_id.endswith(":rpm"):
                        bound_fan_ids.add(binding.sensor_id)
                    continue
                target = _PANEL_ROW_BINDINGS.get((panel.category_id, i))
                if not target:
                    continue
                resolved = _resolve_target(
                    target, readings, already_bound=bound_fan_ids,
                )
                if resolved is not None:
                    binding.sensor_id = resolved
                    bound += 1
                    if resolved.startswith("fan:") and resolved.endswith(":rpm"):
                        bound_fan_ids.add(resolved)
                else:
                    missing.append((panel.category_id, i, target))

        # Second pass: positional fan fallback.  Any unmatched Fan
        # panel slot fills from remaining fan:*:rpm ids in
        # enumeration order — matches legacy ``_map_fans`` behaviour
        # where label-less fans backfill the empty slots.
        fan_panel = next(
            (p for p in self.panels if p.category_id == 6), None,
        )
        if fan_panel is not None:
            unbound_slots = [
                (i, b) for i, b in enumerate(fan_panel.sensors)
                if not b.sensor_id
            ]
            leftover_fan_ids = sorted(
                r.sensor_id for r in readings
                if r.sensor_id.startswith("fan:")
                and r.sensor_id.endswith(":rpm")
                and r.sensor_id not in bound_fan_ids
            )
            for (slot_idx, binding), fan_id in zip(
                unbound_slots, leftover_fan_ids, strict=False,
            ):
                binding.sensor_id = fan_id
                bound += 1
                bound_fan_ids.add(fan_id)
                # Drop this slot from the "missing" list now that
                # the positional fallback filled it.
                missing = [
                    m for m in missing if m[:2] != (6, slot_idx)
                ]

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
