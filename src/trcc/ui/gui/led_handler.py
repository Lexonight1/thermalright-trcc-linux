"""LEDHandler — one per LED device, wired to next/ Commands.

Holds the device key + App handle; every UI-initiated mutation
dispatches through ``self._app.dispatch(SetLed*Command)``.  The
underlying LED engine (``app.led_effects`` + per-device runtime state)
keeps animating regardless of which handler holds the window's focus.

Only the active handler updates the shared :class:`UCLedControl`
panel; ``_active`` gates every Qt slot via :meth:`_guard`.  Panel
updates ride the metrics broadcast (same cadence as the LCD overlay
text refresh).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...core.commands import (
    EnableLedTestMode,
    SelectZone,
    SetClockFormat,
    SetDiskIndex,
    SetLedBrightness,
    SetLedColor,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
    ToggleSegment,
)
from ...core.led_models import LED_STYLES, LEGACY_STYLE_ID
from .base_handler import BaseHandler
from .uc_led_control import UCLedControl

if TYPE_CHECKING:
    from ...app import App
    from ...core.models import ProductInfo

log = logging.getLogger(__name__)


class LEDHandler(BaseHandler):
    """Per-LED-device GUI handler.

    Built through ``TRCCApp._build_handler`` (the single handler chokepoint),
    which always injects ``app=self._app``.  ``app`` is REQUIRED: a None handle
    silently disabled every metrics tick (``update_metrics`` gates on it), which
    was the LED ``--`` bug — so we fail loudly at the composition root instead.
    """

    _SAVE_INTERVAL = 20  # cycles between best-effort settings flushes

    def __init__(
        self,
        device: Any,
        panel: UCLedControl,
        on_temp_unit_changed: Any,
        app: App | None = None,
    ) -> None:
        super().__init__(device, 'led')
        if app is None:
            raise RuntimeError(
                "LEDHandler requires an App handle — the composition root must "
                "pass one (a None handle silently disables metric updates)"
            )
        self._panel = panel
        self._on_temp_unit_changed = on_temp_unit_changed
        # next/ Device + key (ProductInfo.key = "vid:pid")
        self._device_key: str = device.info.key if device is not None else ''
        self._app: App = app
        self._active = False
        self._style: Any = None       # LedStyle enum
        self._style_id_int = 0        # legacy 1..12 for uc_led_control
        self._metrics_count = 0
        self._connect_signals()

    # ── Public API ────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    def show(self, info: ProductInfo | None) -> None:
        """Activate handler — initialize panel + sync from settings."""
        if info is None:
            log.warning("LEDHandler.show: no ProductInfo — cannot activate")
            return
        led_style = getattr(info, 'led_style', None)
        if led_style is None:
            log.warning(
                "LEDHandler.show: %s has no led_style — using a default",
                info.key,
            )
            from ...core.models import LedStyle
            led_style = LedStyle.AX120

        self._style = led_style
        self._style_id_int = LEGACY_STYLE_ID.get(led_style, 1)
        spec = LED_STYLES[led_style]
        self._panel.initialize(
            self._style_id_int,
            spec.segment_count,
            spec.zone_count,
            model=spec.model_name,
        )
        self._sync_panel_from_settings()
        self._active = True
        log.info(
            "LED: show key=%s style=%s, active (metrics-driven)",
            self._device_key, led_style,
        )

    def deactivate(self) -> None:
        """Pause panel updates; the LED engine keeps animating hardware."""
        log.info("LED: deactivate (was active=%s)", self._active)
        self._active = False

    def cleanup(self) -> None:
        """Lifecycle hook called on app close.

        Disconnect happens via ``app.close()`` → ``app.detach(key)``
        at the window level; per-handler cleanup just flips the active
        flag so any in-flight Qt signal becomes a no-op.
        """
        log.info("LED: cleanup")
        self._active = False

    def update_metrics(self, metrics: Any) -> None:
        """Update panel text (segment displays) on metrics tick."""
        log.debug("update_metrics")
        if not (self._active and self._app):
            return
        self._panel.update_metrics(metrics)
        self._metrics_count += 1
        if self._metrics_count >= self._SAVE_INTERVAL:
            self._metrics_count = 0  # next/ persists per-command; just throttle

    def set_temp_unit(self, unit: int) -> None:
        """Surface global temp_unit changes into per-device LED state.

        ``unit`` is the legacy int (0=C, 1=F).  next/'s SetLedTempSource
        Command operates on "cpu" / "gpu" — the temp *unit* lives in
        AppSettings + DeviceSettings, propagated by SetTempUnit at the
        window level.  This stub stays for legacy callers.
        """
        log.debug("LED: set_temp_unit %d (window-level Command handles propagation)", unit)

    # ── Internal — sync panel from persisted settings ────────────────

    def _sync_panel_from_settings(self) -> None:
        """Populate the panel from ``app.settings.for_led(key)``."""
        if self._app is None:
            return
        s = self._app.settings.for_led(self._device_key)
        # Zone[0] for multi-zone, else the global mode/color/brightness.
        if s.zones:
            z = s.zones[0]
            self._panel.load_zone_state(
                0, z.mode.value, z.color, z.brightness, z.on,
            )
        else:
            self._panel.load_zone_state(
                0, s.mode.value, s.color, s.brightness, s.global_on,
            )
        if s.zones:
            # zone_sync_interval_ticks → seconds via 150 ms tick base
            interval_secs = max(1, round(s.zone_sync_interval_ticks * 150 / 1000))
            self._panel.load_sync_state(
                s.zone_sync,
                [True] * len(s.zones),  # all zones in carousel by default
                interval_secs,
            )

    # ── Signal wiring ────────────────────────────────────────────────

    def _guard(self, fn):
        """Wrap a slot so it only runs while the handler is active."""
        def wrapper(*args, **kwargs):
            if self._active and self._app is not None:
                fn(*args, **kwargs)
        return wrapper

    def _connect_signals(self) -> None:
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
        p.carousel_interval_changed.connect(
            self._guard(self._on_carousel_interval_changed),
        )
        p.clock_format_changed.connect(self._guard(self._on_clock_format_changed))
        p.week_start_changed.connect(self._guard(self._on_week_start_changed))
        p.temp_unit_changed.connect(self._guard(self._on_temp_unit_changed_slot))
        p.disk_index_changed.connect(self._guard(self._on_disk_index_changed))
        p.memory_ratio_changed.connect(self._guard(self._on_memory_ratio_changed))
        p.test_mode_changed.connect(self._guard(self._on_test_mode_changed))
        p.temp_source_changed.connect(self._guard(self._on_temp_source_changed))
        p.load_source_changed.connect(self._guard(self._on_load_source_changed))

    # ── Command dispatch slots ───────────────────────────────────────

    def _dispatch(self, cmd: Any) -> Any:
        """Guarded dispatch — handles the ``_app is None`` case."""
        if self._app is None:
            return None
        return self._app.dispatch(cmd)

    def _on_mode_changed(self, mode: Any) -> None:
        log.info("_on_mode_changed: mode=%s", mode)
        from ...core.led_models import LEDMode
        led_mode = mode if isinstance(mode, LEDMode) else LEDMode(mode)
        self._dispatch(SetLedMode(key=self._device_key, mode=led_mode))

    def _on_temp_source_changed(self, source: str) -> None:
        log.info("LED: temp source → %s", source)
        self._dispatch(SetLedTempSource(key=self._device_key, source=source))

    def _on_load_source_changed(self, source: str) -> None:
        log.info("LED: load source → %s", source)
        self._dispatch(SetLedLoadSource(key=self._device_key, source=source))

    def _on_color_changed(self, r: int, g: int, b: int) -> None:
        log.info("_on_color_changed: r=%s g=%s b=%s", r, g, b)
        self._dispatch(SetLedColor(
            key=self._device_key, color=(r, g, b),
        ))

    def _on_brightness_changed(self, val: int) -> None:
        log.info("_on_brightness_changed: val=%s", val)
        self._dispatch(SetLedBrightness(
            key=self._device_key, percent=val,
        ))

    def _on_global_toggled(self, on: bool) -> None:
        log.info("_on_global_toggled: on=%s", on)
        self._dispatch(ToggleLed(key=self._device_key, on=on))

    def _on_segment_clicked(self, idx: int) -> None:
        # ToggleSegment toggles between on/off — the Command resolves
        # the current state internally and inverts it.
        log.info("_on_segment_clicked: idx=%s", idx)
        self._dispatch(ToggleSegment(
            key=self._device_key, index=idx, on=True,
        ))

    def _on_zone_selected(self, zone_index: int) -> None:
        log.info("_on_zone_selected: zone_index=%s", zone_index)
        result = self._dispatch(SelectZone(
            key=self._device_key, zone=zone_index,
        ))
        if result is None or not getattr(result, 'ok', False):
            return
        # Refresh the panel from the new zone's settings
        self._sync_panel_from_settings()

    def _on_zone_toggled(self, zi: int, on: bool) -> None:
        log.info("_on_zone_toggled: zi=%s on=%s", zi, on)
        self._dispatch(ToggleLed(
            key=self._device_key, on=on, zone=zi,
        ))

    def _on_carousel_changed(self, on: bool) -> None:
        log.info("_on_carousel_changed: on=%s", on)
        self._dispatch(SetLedZoneSync(
            key=self._device_key, enabled=on,
        ))

    def _on_carousel_zone_changed(self, zi: int, sel: Any) -> None:
        # next/'s zone-sync model: SetLedZoneColor for the picked zone
        log.info("_on_carousel_zone_changed: zi=%s sel=%s", zi, sel)
        del zi, sel  # Phase 7 hooks per-zone include/exclude

    def _on_carousel_interval_changed(self, secs: int) -> None:
        # secs → ticks (150 ms tick base): ticks = secs * 1000 / 150
        log.info("_on_carousel_interval_changed: secs=%s", secs)
        ticks = max(1, int(secs * 1000 / 150))
        self._dispatch(SetLedZoneSyncInterval(
            key=self._device_key, ticks=ticks,
        ))

    def _on_clock_format_changed(self, is_24h: bool) -> None:
        log.info("_on_clock_format_changed: is_24h=%s", is_24h)
        self._dispatch(SetClockFormat(
            key=self._device_key, is_24h=is_24h,
        ))

    def _on_week_start_changed(self, is_sun: bool) -> None:
        log.info("_on_week_start_changed: is_sun=%s", is_sun)
        self._dispatch(SetWeekStart(
            key=self._device_key, sunday_first=is_sun,
        ))

    def _on_temp_unit_changed_slot(self, unit: str) -> None:
        # The window handles SetTempUnit globally; forward to its callback
        # so the rest of the UI (uc_system_info etc.) updates too.
        log.info("_on_temp_unit_changed_slot: unit=%s", unit)
        if callable(self._on_temp_unit_changed):
            self._on_temp_unit_changed(unit)

    def _on_disk_index_changed(self, idx: int) -> None:
        log.info("_on_disk_index_changed: idx=%s", idx)
        self._dispatch(SetDiskIndex(
            key=self._device_key, index=idx,
        ))

    def _on_memory_ratio_changed(self, ratio: int) -> None:
        # DDR multiplier (1/2/4) straight from the GUI combo.
        log.info("_on_memory_ratio_changed: ratio=%s", ratio)
        self._dispatch(SetMemoryRatio(key=self._device_key, ratio=ratio))

    def _on_test_mode_changed(self, on: bool) -> None:
        log.info("_on_test_mode_changed: on=%s", on)
        self._dispatch(EnableLedTestMode(
            key=self._device_key, enabled=on,
        ))

    # ── Frame handling ───────────────────────────────────────────────

    def handle_frame(self, image: Any) -> None:
        """Receive tick result — update LED color display on the panel."""
        display_colors = image.get('display_colors') if isinstance(image, dict) else None
        display_mask = image.get('mask') if isinstance(image, dict) else None
        log.debug("handle_frame: active=%s colors=%s",
                  self._active, len(display_colors) if display_colors else None)
        if not self._active:
            return
        if display_colors is not None:
            self._panel.set_led_colors(display_colors, display_mask)
