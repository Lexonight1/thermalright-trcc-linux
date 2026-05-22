"""LEDHandler — one per LED device.

Self-contained handler for a single LED device. Owns an LEDDevice,
signal wiring, and GUI state sync. TRCCApp creates one LEDHandler
per connected LED device.

Only the active handler updates the shared panel — `_active` bool
gates all signal handlers via `_guard()`. Panel updates are driven
by the metrics signal (same refresh rate as LCD overlay), not a
separate timer.

Hardware sends happen in the background metrics loop via device.tick().
"""
from __future__ import annotations

import logging
from typing import Any

from ..._boot import trcc as _trcc
from ...core.device.led import LEDDevice
from ...core.models import LED_STYLES, DeviceInfo
from .base_handler import BaseHandler
from .uc_led_control import UCLedControl

log = logging.getLogger(__name__)


class LEDHandler(BaseHandler):
    """Handler for a single LED device.

    Config-driven: device object holds all state, handler is just
    the runtime instance for UI manipulation. Active handler gets
    panel interaction and metrics updates; inactive ones keep running
    their last state on hardware.
    """

    _SAVE_INTERVAL = 20  # save config every N metrics updates

    def handle_frame(self, image: Any) -> None:
        """Receive tick result from background loop — update LED color display."""
        display_colors = image.get('display_colors') if isinstance(image, dict) else None
        if display_colors is not None:
            self._panel.set_led_colors(display_colors)

    def __init__(
        self,
        led: LEDDevice,
        panel: UCLedControl,
        on_temp_unit_changed: Any,
    ) -> None:
        super().__init__(led, 'led')
        self._panel = panel
        self._on_temp_unit_changed = on_temp_unit_changed
        self._led = led
        self._active = False
        self._style_id = 0
        self._metrics_count = 0
        self._connect_signals()

    def cleanup(self) -> None:
        """Save config and release device resources."""
        log.info("LED: cleanup")
        self._active = False
        if self._led:
            self._led.save_config()
            self._led.cleanup()

    def update_metrics(self, metrics: Any) -> None:
        """Update panel text (segment displays). Colors arrive via FRAME_RENDERED."""
        if not (self._led and self._active):
            return
        self._panel.update_metrics(metrics)
        self._metrics_count += 1
        if self._metrics_count >= self._SAVE_INTERVAL:
            self._metrics_count = 0
            self._led.save_config()

    # ── Public API ───────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def has_controller(self) -> bool:
        return self._led is not None

    @property
    def led_port(self) -> LEDDevice | None:
        return self._led

    def show(self, device: DeviceInfo) -> None:
        """Activate handler — initialize device, sync panel from device state."""
        model = device.model or ''
        led_style = device.led_style_id or LED_STYLES.by_name(model)

        self._led.initialize_led(device, led_style)
        self._style_id = led_style

        style_info = LED_STYLES[led_style]
        self._panel.initialize(
            led_style, style_info.segment_count, style_info.zone_count,
            model=model,
            )
        self._panel.set_memory_ratio(self._led.state.memory_ratio)
        self._sync_ui_from_state()

        self._led.set_temp_unit(_trcc().settings.temp_unit)

        self._active = True
        log.info("LED: show model=%s style=%d, active (metrics-driven)", model, led_style)

    def deactivate(self) -> None:
        """Pause handler — stop panel updates, save config. LEDDevice keeps running."""
        log.info("LED: deactivate (was active=%s)", self._active)
        self._active = False
        if self._led:
            self._led.save_config()

    def set_temp_unit(self, unit: int) -> None:
        if self._led:
            log.debug("LED: temp_unit=%d", unit)
            self._led.set_temp_unit(unit)

    # ── Private ──────────────────────────────────────────────────────

    def _sync_ui_from_state(self) -> None:
        if not self._led:
            return
        state = self._led.state
        if state.zones:
            z = state.zones[0]
            self._panel.load_zone_state(0, z.mode.value, z.color, z.brightness, z.on)
        else:
            self._panel.load_zone_state(
                0, state.mode.value, state.color, state.brightness, state.global_on)

        # Restore carousel/sync state (zone_sync_zones empty = single-zone device)
        if state.zone_sync_zones:
            interval_secs = max(1, round(state.zone_sync_interval * 150 / 1000))
            self._panel.load_sync_state(
                state.zone_sync, state.zone_sync_zones, interval_secs)

        log.debug("LED: synced UI from state (zones=%d, sync=%s)",
                  len(state.zones), state.zone_sync)

    def _guard(self, fn):
        """Wrap a slot so it only fires when this handler is active with a device."""
        def wrapper(*args, **kwargs):
            if self._led and self._active:
                fn(*args, **kwargs)
        return wrapper

    def _connect_signals(self) -> None:
        if not self._led:
            return
        p = self._panel
        p.mode_changed.connect(self._guard(self._on_mode_changed))
        p.color_changed.connect(self._guard(self._on_color_changed))
        p.brightness_changed.connect(self._guard(self._on_brightness_changed))
        p.global_toggled.connect(self._guard(self._on_global_toggled))
        p.segment_clicked.connect(self._guard(self._on_segment_clicked))
        p.zone_selected.connect(self._guard(self._on_zone_selected))
        p.zone_toggled.connect(self._guard(self._on_zone_toggled))
        p.carousel_changed.connect(self._guard(self._on_carousel_changed))
        p.carousel_zone_changed.connect(self._guard(self._on_carousel_zone_changed))
        p.carousel_interval_changed.connect(self._guard(self._on_carousel_interval_changed))
        p.clock_format_changed.connect(self._guard(self._on_clock_format_changed))
        p.week_start_changed.connect(self._guard(self._on_week_start_changed))
        p.temp_unit_changed.connect(self._guard(self._on_temp_unit_changed))
        p.disk_index_changed.connect(self._guard(self._on_disk_index_changed))
        p.memory_ratio_changed.connect(self._guard(self._on_memory_ratio_changed))
        p.test_mode_changed.connect(self._guard(self._on_test_mode_changed))

    def _on_mode_changed(self, mode: Any) -> None:
        self._led.update_mode(mode)
        if self._led.state.zones:
            self._led.update_zone_mode(self._panel.selected_zone, mode)
        self._metrics_count = self._SAVE_INTERVAL  # force save on next update

    def _on_color_changed(self, r: int, g: int, b: int) -> None:
        self._led.update_color(r, g, b)
        if self._led.state.zones:
            self._led.update_zone_color(self._panel.selected_zone, r, g, b)

    def _on_brightness_changed(self, val: int) -> None:
        self._led.update_brightness(val)
        if self._led.state.zones:
            self._led.update_zone_brightness(self._panel.selected_zone, val)

    def _on_global_toggled(self, on: bool) -> None:
        self._led.update_global_on(on)

    def _on_segment_clicked(self, idx: int) -> None:
        if 0 <= idx < len(self._led.state.segment_on):
            self._led.update_segment(idx, not self._led.state.segment_on[idx])

    def _on_zone_selected(self, zone_index: int) -> None:
        if not self._led.state.zones:
            return
        self._led.update_selected_zone(zone_index)
        zones = self._led.state.zones
        if 0 <= zone_index < len(zones):
            z = zones[zone_index]
            self._panel.load_zone_state(zone_index, z.mode.value, z.color, z.brightness, z.on)

    def _on_zone_toggled(self, zi: int, on: bool) -> None:
        self._led.update_zone_on(zi, on)

    def _on_carousel_changed(self, on: bool) -> None:
        self._led.update_zone_sync(on)

    def _on_carousel_zone_changed(self, zi: int, sel: Any) -> None:
        self._led.update_zone_sync_zone(zi, sel)

    def _on_carousel_interval_changed(self, secs: int) -> None:
        self._led.update_zone_sync_interval(secs)

    def _on_clock_format_changed(self, is_24h: bool) -> None:
        self._led.update_clock_format(is_24h)

    def _on_week_start_changed(self, is_sun: bool) -> None:
        self._led.update_week_start(is_sun)

    def _on_disk_index_changed(self, idx: int) -> None:
        self._led.update_disk_index(idx)

    def _on_memory_ratio_changed(self, ratio: int) -> None:
        self._led.update_memory_ratio(ratio)

    def _on_test_mode_changed(self, on: bool) -> None:
        self._led.update_test_mode(on)
