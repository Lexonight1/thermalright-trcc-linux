"""LEDCommands — every LED user capability, one method each.

UI-facing command surface for LED devices. GUI handlers, CLI subcommands,
and API endpoints all call these methods.

Device's existing `set_*` / `toggle_*` methods already return uniform
result dicts (with IPC proxy routing via `@_forward_to_proxy`). This
phase wraps them in typed dataclasses at the boundary. Persistence is
already inside Device for LED — no move needed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._command import command
from ._device_commands import DeviceCommands
from .events import Topic
from .models.led import LEDMode
from .results import DiskInfo, LEDResult, LEDSnapshot, LEDStyleInfo, OpResult

if TYPE_CHECKING:
    from .device.led import LEDDevice  # noqa: F401  (used in generic str ref)

log = logging.getLogger(__name__)


class LEDCommands(DeviceCommands['LEDDevice']):
    """Command surface for LED devices."""

    _KIND = 'LED'

    # ── Color / mode / brightness ────────────────────────────────────

    @command(
        result_cls=LEDResult, topic=Topic.LED_COLOR,
        publish_kwargs=('zone',), extras_rename={'colors': 'display_colors'},
    )
    def set_color(self, led: int, r: int, g: int, b: int,
                  *, zone: int | None = None):
        if (dev := self._get(led)) is None:
            return self._missing(led, LEDResult)
        return dev.set_zone_color(zone, r, g, b) if zone is not None \
            else dev.set_color(r, g, b)

    @command(
        result_cls=LEDResult, topic=Topic.LED_MODE,
        publish_kwargs=('zone',), extras_rename={'colors': 'display_colors'},
    )
    def set_mode(self, led: int, mode: LEDMode | str | int,
                 *, zone: int | None = None):
        if (dev := self._get(led)) is None:
            return self._missing(led, LEDResult)
        return dev.set_zone_mode(zone, mode) if zone is not None \
            else dev.set_mode(mode)

    @command(
        result_cls=LEDResult, topic=Topic.LED_BRIGHTNESS,
        publish_kwargs=('zone',), extras_rename={'colors': 'display_colors'},
    )
    def set_brightness(self, led: int, percent: int,
                       *, zone: int | None = None):
        if (dev := self._get(led)) is None:
            return self._missing(led, LEDResult)
        return dev.set_zone_brightness(zone, percent) if zone is not None \
            else dev.set_brightness(percent)

    @command(
        result_cls=LEDResult, topic=Topic.LED_TOGGLED,
        publish_kwargs=('zone',),
    )
    def toggle(self, led: int, on: bool, *, zone: int | None = None):
        if (dev := self._get(led)) is None:
            return self._missing(led, LEDResult)
        return dev.toggle_zone(zone, on) if zone is not None \
            else dev.toggle_global(on)

    @command(result_cls=LEDResult)
    def toggle_segment(self, led: int, index: int, on: bool):
        if (dev := self._get(led)) is None:
            return self._missing(led, LEDResult)
        return dev.toggle_segment(index, on)

    # ── Zones ────────────────────────────────────────────────────────

    @command(result_cls=OpResult)
    def select_zone(self, led: int, zone: int):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_selected_zone(zone)

    @command(result_cls=OpResult, topic=Topic.LED_ZONE_SYNC)
    def set_zone_sync(self, led: int, enabled: bool,
                      *, zones: list[int] | None = None,
                      interval_s: int | None = None):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        if zones is not None:
            for idx, z in enumerate(zones):
                dev.set_zone_sync_zone(z, True)
                log.debug('zone_sync include zone=%d (iter=%d)', z, idx)
        return dev.set_zone_sync(enabled, interval_s)

    # ── Display modes ────────────────────────────────────────────────

    @command(result_cls=OpResult, topic=Topic.LED_CLOCK)
    def set_clock_format(self, led: int, is_24h: bool):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_clock_format(is_24h)

    @command(result_cls=OpResult)
    def set_week_start(self, led: int, sunday: bool):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_week_start(sunday)

    @command(result_cls=OpResult)
    def set_memory_ratio(self, led: int, ratio: int):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_memory_ratio(ratio)

    @command(result_cls=OpResult)
    def set_disk_index(self, led: int, index: int):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_disk_index(index)

    @command(result_cls=OpResult)
    def set_test_mode(self, led: int, enabled: bool):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_test_mode(enabled)

    @command(result_cls=OpResult, topic=Topic.LED_SENSOR)
    def set_sensor_source(self, led: int, source: str):
        if (dev := self._get(led)) is None:
            return self._missing(led, OpResult)
        return dev.set_sensor_source(source)

    # ── Listing ──────────────────────────────────────────────────────

    def list_styles(self) -> list[LEDStyleInfo]:
        from .models.led import LED_STYLES
        return [
            LEDStyleInfo(
                style_id=sid,
                name=info.model_name,
                segment_count=info.segment_count,
                zone_count=info.zone_count,
                supported_modes=[m.name.lower() for m in LEDMode],
            )
            for sid, info in LED_STYLES.items()
        ]

    def list_modes(self, led: int) -> list[str]:
        dev = self._get(led)
        if dev is None:
            return []
        return [m.name.lower() for m in LEDMode]

    def list_disks(self) -> list[DiskInfo]:
        # Phase 5: delegate to Platform.get_disk_info() via ControlCenterCommands.
        log.debug('list_disks (phase-3 stub)')
        return []

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(self, led: int) -> LEDSnapshot:
        dev = self._get(led)
        if dev is None or dev.state is None:
            return LEDSnapshot(
                connected=False, style_id=0, mode=0, color=(0, 0, 0),
                brightness=0, global_on=False, zones=[], zone_sync=False,
                zone_sync_interval=0, selected_zone=0, segment_on=[],
                clock_24h=True, week_sunday=False, memory_ratio=1,
                disk_index=0, test_mode=False,
            )
        s = dev.state
        info = dev.device_info
        return LEDSnapshot(
            connected=dev.connected,
            style_id=getattr(info, 'led_style_id', 0) or 0,
            mode=s.mode.value if hasattr(s.mode, 'value') else int(s.mode),
            color=tuple(s.color) if s.color else (0, 0, 0),
            brightness=s.brightness,
            global_on=s.global_on,
            zones=[{'mode': z.mode.value, 'color': list(z.color),
                    'brightness': z.brightness, 'on': z.on} for z in s.zones],
            zone_sync=s.zone_sync,
            zone_sync_interval=s.zone_sync_interval,
            selected_zone=getattr(s, 'selected_zone', 0),
            segment_on=list(s.segment_on),
            clock_24h=getattr(s, 'is_timer_24h', True),
            week_sunday=getattr(s, 'is_week_sunday', False),
            memory_ratio=getattr(s, 'memory_ratio', 1),
            disk_index=getattr(s, 'disk_index', 0),
            test_mode=getattr(s, 'test_mode', False),
        )
