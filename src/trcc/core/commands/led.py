"""LED Commands — colors, modes, zones, segment displays, clock."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from ..errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    TransportError,
)
from ..events import (
    ErrorOccurred,
    HddEnabledChanged,
    LedColorsChanged,
)
from ..led_models import (
    LED_SELECT_ALL_STYLES,
    LEDMode,
    LedPayload,
    LedRuntimeState,
)
from ..results import (
    ClockFormatResult,
    DiskIndexResult,
    HddEnabledResult,
    LedColorsResult,
    LedModesListResult,
    LedSnapshotResult,
    LedStyleEntry,
    LedStylesListResult,
    MemoryRatioResult,
    WeekStartResult,
)
from ._base import Command
from ._helpers import (
    _publish_if_disconnect,
    _publish_led_settings_changed,
)
from .device import (
    ConnectDevice,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SetLedColors(Command[LedColorsResult]):
    """Set LED color array + on/off + brightness on a connected Led device."""
    key: str
    colors: list[tuple[int, int, int]]
    global_on: bool = True
    brightness: int = 100

    def execute(self, app: App) -> LedColorsResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if not device.is_led:
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=f"{self.key} is not an LED device",
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        payload = LedPayload(
            colors=list(self.colors),
            global_on=self.global_on,
            brightness=self.brightness,
        )
        try:
            ok = device.send(payload)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            _publish_if_disconnect(app, self.key, e)
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if ok:
            app.events.publish(LedColorsChanged(
                key=self.key, color_count=len(self.colors),
            ))
        return LedColorsResult(
            ok=ok, key=self.key, colors=list(self.colors),
            message=(f"Sent {len(self.colors)} LED color(s)"
                     if ok else "LED send returned False"),
        )

@dataclass(frozen=True, slots=True)
class InitializeLed(Command[LedColorsResult]):
    """Connect + render one initial LED frame in a single dispatch.

    Convenience for the LED boot path — equivalent to ``ConnectDevice``
    followed by ``RenderLed``, but wrapped so headless callers (CLI
    autorestore, daemon startup) don't have to chain two Commands and
    handle the intermediate Result.  Failures at either step surface
    as the LED Result so the caller only inspects one shape.

    Distinct from :class:`RenderLed` (assumes already-connected) and
    :class:`ConnectDevice` (handshakes but doesn't render).  Use this
    on app start; use the individual Commands when you need finer
    control over each step.
    """
    key: str

    def execute(self, app: App) -> LedColorsResult:
        log.info("InitializeLed: key=%s", self.key)
        connect_result = ConnectDevice(key=self.key).execute(app)
        if not connect_result.ok:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"connect failed: {connect_result.message}",
            )
        return RenderLed(key=self.key).execute(app)

@dataclass(frozen=True, slots=True)
class RenderLed(Command[LedColorsResult]):
    """Compute one LED frame from current settings + sensors and send it.

    Drives both branches in one Command:

      * **Segment-display styles** (most LED panels) — ``compute_mask``
        gives the per-segment on/off pattern from the live sensor
        snapshot; the effects engine fills in the color for every lit
        segment.
      * **Non-segment styles** (LF13, LC2) — no mask; the engine
        directly fills ``style.led_count`` colors.

    Mode comes from ``Settings.for_led(key).mode`` (persisted) unless
    the caller passes an explicit ``color`` override, in which case the
    Command short-circuits to STATIC behavior at *that* color (used by
    the CLI ``led color <key> <hex>`` diagnostic).

    Transient counters live on ``app.led_runtime[key]`` — the engine
    advances them as a side effect so consecutive ``RenderLed``
    dispatches phase forward.

    Per-tick: logged at DEBUG so a default INFO run isn't drowned.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str
    color: tuple[int, int, int] | None = None    # None = use Settings.led.color
    phase: int = 0

    def execute(self, app: App) -> LedColorsResult:
        from ...services.led_segment import (
            LegacyMetricsView,
            compute_mask,
            get_display,
        )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=str(e),
            )

        if not device.is_led:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"{self.key} is not an LED device",
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        if device.led_handshake is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"{self.key} handshake incomplete — no style resolved",
            )

        style = device.led_handshake.style
        if style is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=(f"{self.key} firmware PM unknown — no style "
                         "resolved; use SetLedColors instead"),
            )

        led_settings = app.settings.for_led(self.key)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        device_settings = app.settings.for_device(self.key)

        # Build the flat sensor dict the engine consumes.  Two shapes
        # coexist: the dotted IDs we already produce for SensorReading,
        # and the legacy view used by compute_mask().
        enum = app.platform.sensors()
        descriptors = enum.discover()
        current = enum.read_all()
        from ..models import SensorReading
        readings = {
            d.sensor_id: SensorReading(
                sensor_id=d.sensor_id, category=d.category,
                value=current.get(d.sensor_id, 0.0),
                unit=d.unit, label=d.label,
            )
            for d in descriptors
        }
        metrics = LegacyMetricsView(readings)

        # If the caller passed an explicit color, treat it as a STATIC
        # diagnostic at full brightness (same shape RenderLed has always
        # offered — "show this color as bright as the LEDs can go").
        # Otherwise the engine reads everything off LedDeviceSettings.
        explicit_color = self.color
        effective_settings = (
            replace(led_settings,
                    mode=LEDMode.STATIC,
                    color=explicit_color,
                    brightness=100,
                    test_mode=False)
            if explicit_color is not None else led_settings
        )

        display = get_display(style)
        if display is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=(f"style {style.value} has no segment display — "
                         "use SetLedColors instead"),
            )

        # ── Segment phase (legacy ``_seg_phase``) ──
        # On a multi-zone device the phase that drives ``compute_mask`` is
        # the active zone, not the caller's ``self.phase``.  Zone-sync on a
        # circulate style (NOT a select-all style) advances the carousel
        # through the enabled zones; otherwise the phase is the selected
        # zone.  Single-zone devices keep ``self.phase`` (API override).
        phase = self.phase
        if effective_settings.zones:
            if (effective_settings.zone_sync
                    and style.value not in LED_SELECT_ALL_STYLES):
                runtime.zone_sync_ticks += 1
                if (runtime.zone_sync_ticks
                        >= effective_settings.zone_sync_interval_ticks):
                    runtime.zone_sync_ticks = 0
                    runtime.zone_sync_current = app.led_effects.next_sync_zone(
                        effective_settings.zone_sync_zones,
                        runtime.zone_sync_current,
                    )
                phase = runtime.zone_sync_current
                log.debug("RenderLed %s: zone-sync carousel phase=%d",
                          self.key, phase)
            else:
                phase = effective_settings.selected_zone
                log.debug("RenderLed %s: selected-zone phase=%d",
                          self.key, phase)

        mask = compute_mask(
            style, metrics, phase=phase,
            temp_unit=device_settings.temp_unit,
            is_24h=(device_settings.time_format == "24h"),
        )
        segment_count = len(mask)

        # ── Colors ──
        # Multi-zone styles (PA120 / LF10 — those with a ``zone_led_map``)
        # render each zone's own mode/color/brightness onto its mapped LED
        # indices.  All other styles fill one global color list.
        zone_map = display.zone_led_map
        if zone_map is not None and effective_settings.zones:
            log.debug("RenderLed %s: multi-zone fill (%d zones)",
                      self.key, len(zone_map))
            colors = app.led_effects.tick_multi_zone(
                effective_settings, runtime, current,
                zone_map=zone_map,
                metric_sources=display.zone_metric_sources,
                led_count=segment_count,
            )
        else:
            colors = app.led_effects.tick(
                effective_settings, runtime, current,
                led_count=segment_count,
            )

        payload = LedPayload(
            colors=colors,
            is_on=mask,
            global_on=effective_settings.global_on,
            brightness=effective_settings.brightness,
        )
        try:
            ok = device.send(payload)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            _publish_if_disconnect(app, self.key, e)
            return LedColorsResult(
                ok=False, key=self.key, colors=colors,
                message=str(e),
            )

        if ok:
            app.events.publish(LedColorsChanged(
                key=self.key, color_count=len(colors),
            ))
        return LedColorsResult(
            ok=ok, key=self.key, colors=colors,
            message=(f"Rendered {style.value} {effective_settings.mode.name} "
                     f"({sum(mask)}/{len(mask)} LEDs on)"
                     if ok else "LED send returned False"),
        )

@dataclass(frozen=True, slots=True)
class SetLedMode(Command[LedColorsResult]):
    """Set the global animation mode for an LED device."""
    key: str
    mode: LEDMode

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_mode(self.key, self.mode)
        # Phase counters reset on mode change so animation restarts cleanly
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.rgb_timer = 0
        runtime.test_timer = 0
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED mode set to {self.mode.name}",
        )

@dataclass(frozen=True, slots=True)
class SetLedColor(Command[LedColorsResult]):
    """Set the global LED color (used in STATIC / BREATHING / COLORFUL modes)."""
    key: str
    color: tuple[int, int, int]

    def execute(self, app: App) -> LedColorsResult:
        for label, value in zip("rgb", self.color, strict=False):
            if not 0 <= value <= 255:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[],
                    message=f"{label} out of range (0-255): {value}",
                )
        app.settings.set_led_color(self.key, self.color)
        _publish_led_settings_changed(app, self.key)
        r, g, b = self.color
        return LedColorsResult(
            ok=True, key=self.key, colors=[self.color],
            message=f"LED color set to #{r:02x}{g:02x}{b:02x}",
        )

@dataclass(frozen=True, slots=True)
class SetLedBrightness(Command[LedColorsResult]):
    """Set the global LED brightness percent (0–100)."""
    key: str
    percent: int

    def execute(self, app: App) -> LedColorsResult:
        if not 0 <= self.percent <= 100:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"brightness out of range (0-100): {self.percent}",
            )
        app.settings.set_led_brightness(self.key, self.percent)
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED brightness set to {self.percent}%",
        )

@dataclass(frozen=True, slots=True)
class EnableLedTestMode(Command[LedColorsResult]):
    """Enable / disable the 4-color diagnostic test cycle."""
    key: str
    enabled: bool

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_test_mode(self.key, self.enabled)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.test_timer = 0
        runtime.test_color = 0
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED test mode {'enabled' if self.enabled else 'disabled'}",
        )

@dataclass(frozen=True, slots=True)
class SetLedTempSource(Command[LedColorsResult]):
    """Pick the sensor source for TEMP_LINKED mode (``'cpu'`` or ``'gpu'``)."""
    key: str
    source: str

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_temp_source(self.key, self.source)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED temp source set to {self.source}",
        )

@dataclass(frozen=True, slots=True)
class ToggleLed(Command[LedColorsResult]):
    """Toggle an LED device on/off — global, or one zone if ``zone`` is given.

    Mirrors legacy ``LedCommands.toggle(led, on, zone=None)``.  Global
    toggle flips ``LedDeviceSettings.global_on``; per-zone toggle flips
    the zone's ``on`` flag (used by zone-aware styles to mute a single
    fan/strip without disturbing the others).
    """
    key: str
    on: bool
    zone: int | None = None

    def execute(self, app: App) -> LedColorsResult:
        if self.zone is None:
            app.settings.set_led_global_on(self.key, self.on)
            target = "global"
        else:
            try:
                app.settings.set_led_zone(self.key, self.zone, on=self.on)
            except IndexError as e:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[], message=str(e),
                )
            target = f"zone {self.zone}"
        _publish_led_settings_changed(app, self.key)
        state = "on" if self.on else "off"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED {target} turned {state}",
        )

@dataclass(frozen=True, slots=True)
class SetLedLoadSource(Command[LedColorsResult]):
    """Pick the sensor source for LOAD_LINKED mode (``'cpu'`` or ``'gpu'``)."""
    key: str
    source: str

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_load_source(self.key, self.source)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED load source set to {self.source}",
        )

@dataclass(frozen=True, slots=True)
class SetLedZoneColor(Command[LedColorsResult]):
    """Set one zone's persistent color — mirrors legacy zone-aware setters."""
    key: str
    zone: int
    color: tuple[int, int, int]

    def execute(self, app: App) -> LedColorsResult:
        for label, value in zip("rgb", self.color, strict=False):
            if not 0 <= value <= 255:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[],
                    message=f"{label} out of range (0-255): {value}",
                )
        try:
            app.settings.set_led_zone(self.key, self.zone, color=self.color)
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        r, g, b = self.color
        return LedColorsResult(
            ok=True, key=self.key, colors=[self.color],
            message=f"Zone {self.zone} color set to #{r:02x}{g:02x}{b:02x}",
        )

@dataclass(frozen=True, slots=True)
class SetLedZoneMode(Command[LedColorsResult]):
    """Set one zone's persistent LED mode.

    Per-zone variant of :class:`SetLedMode` — mirrors legacy
    ``POST /led/zones/{zone}/mode``.  ``mode`` is the integer
    :class:`LEDMode` value; clients that send a name should resolve
    it at the API edge before dispatch.
    """
    key: str
    zone: int
    mode: LEDMode

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_zone(self.key, self.zone, mode=self.mode)
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone {self.zone} mode set to {self.mode.name}",
        )

@dataclass(frozen=True, slots=True)
class SetLedZoneBrightness(Command[LedColorsResult]):
    """Set one zone's persistent brightness (0-100).

    Per-zone variant of :class:`SetLedBrightness` — mirrors legacy
    ``POST /led/zones/{zone}/brightness``.  Clamped server-side via
    :meth:`Settings.set_led_zone`.
    """
    key: str
    zone: int
    percent: int

    def execute(self, app: App) -> LedColorsResult:
        if not 0 <= self.percent <= 100:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"brightness out of range (0-100): {self.percent}",
            )
        try:
            app.settings.set_led_zone(
                self.key, self.zone, brightness=self.percent,
            )
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone {self.zone} brightness set to {self.percent}%",
        )

@dataclass(frozen=True, slots=True)
class SetLedZoneSync(Command[LedColorsResult]):
    """Enable/disable the zone-sync carousel for a device."""
    key: str
    enabled: bool

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_zone_sync(self.key, self.enabled)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.zone_sync_ticks = 0
        runtime.zone_sync_current = 0
        _publish_led_settings_changed(app, self.key)
        state = "enabled" if self.enabled else "disabled"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone-sync {state}",
        )

@dataclass(frozen=True, slots=True)
class SetLedZoneSyncInterval(Command[LedColorsResult]):
    """Set how many ticks between zone-sync rotations."""
    key: str
    ticks: int

    def execute(self, app: App) -> LedColorsResult:
        if self.ticks < 1:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"interval must be >= 1, got {self.ticks}",
            )
        app.settings.set_led_zone_sync_interval(self.key, self.ticks)
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone-sync interval set to {self.ticks} tick(s)",
        )

@dataclass(frozen=True, slots=True)
class SelectZone(Command[LedColorsResult]):
    """Pick the active zone (UI selection state)."""
    key: str
    zone: int

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_selected_zone(self.key, self.zone)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Selected zone {self.zone}",
        )

@dataclass(frozen=True, slots=True)
class ToggleSegment(Command[LedColorsResult]):
    """Flip one segment's on/off state (segment-display devices)."""
    key: str
    index: int
    on: bool

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_segment_on(self.key, self.index, self.on)
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        state = "on" if self.on else "off"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Segment {self.index} turned {state}",
        )

@dataclass(frozen=True, slots=True)
class SetClockFormat(Command[ClockFormatResult]):
    """12h/24h clock display for LC2-style LED segment devices."""
    key: str
    is_24h: bool

    def execute(self, app: App) -> ClockFormatResult:
        app.settings.set_led_clock_24h(self.key, self.is_24h)
        _publish_led_settings_changed(app, self.key)
        fmt = "24h" if self.is_24h else "12h"
        return ClockFormatResult(
            ok=True, key=self.key, is_24h=self.is_24h,
            message=f"Clock format set to {fmt}",
        )

@dataclass(frozen=True, slots=True)
class SetWeekStart(Command[WeekStartResult]):
    """Week-start convention: ``True`` = Sunday-first, ``False`` = Monday-first."""
    key: str
    sunday_first: bool

    def execute(self, app: App) -> WeekStartResult:
        app.settings.set_led_week_start(self.key, self.sunday_first)
        _publish_led_settings_changed(app, self.key)
        which = "Sunday" if self.sunday_first else "Monday"
        return WeekStartResult(
            ok=True, key=self.key, sunday_first=self.sunday_first,
            message=f"Week starts on {which}",
        )

@dataclass(frozen=True, slots=True)
class SetMemoryRatio(Command[MemoryRatioResult]):
    """Pick the memory display mode: ratio (percentage) or absolute (GB)."""
    key: str
    ratio_mode: bool

    def execute(self, app: App) -> MemoryRatioResult:
        app.settings.set_led_memory_ratio(self.key, self.ratio_mode)
        _publish_led_settings_changed(app, self.key)
        mode = "ratio (%)" if self.ratio_mode else "absolute (GB)"
        return MemoryRatioResult(
            ok=True, key=self.key, ratio_mode=self.ratio_mode,
            message=f"Memory display set to {mode}",
        )

@dataclass(frozen=True, slots=True)
class SetDiskIndex(Command[DiskIndexResult]):
    """Pick which disk to surface read/write stats for."""
    key: str
    index: int

    def execute(self, app: App) -> DiskIndexResult:
        try:
            app.settings.set_led_disk_index(self.key, self.index)
        except ValueError as e:
            return DiskIndexResult(
                ok=False, key=self.key, index=self.index, message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return DiskIndexResult(
            ok=True, key=self.key, index=self.index,
            message=f"Disk index set to {self.index}",
        )

@dataclass(frozen=True, slots=True)
class SetHddEnabled(Command[HddEnabledResult]):
    """Toggle HDD metrics inclusion in sensor broadcasts."""
    enabled: bool

    def execute(self, app: App) -> HddEnabledResult:
        app.settings.set_hdd_enabled(self.enabled)
        # Wake subscribers (MetricsLoop) so the broadcast refreshes
        # with the new HDD-filter state immediately, not after a full
        # refresh interval.  Same event-driven pattern SetTempUnit
        # and SetRefreshInterval use.
        app.events.publish(HddEnabledChanged(enabled=self.enabled))
        state = "enabled" if self.enabled else "disabled"
        return HddEnabledResult(
            ok=True, enabled=self.enabled,
            message=f"HDD metrics {state}",
        )

@dataclass(frozen=True, slots=True)
class ListLedStyles(Command[LedStylesListResult]):
    """Enumerate every LED style the PM registry can resolve."""

    def execute(self, app: App) -> LedStylesListResult:
        del app
        from ..led_protocol import _PM_REGISTRY
        styles = [
            LedStyleEntry(
                style=entry.style.value,
                model_name=entry.model_name,
                pm_byte=pm,
                style_sub=entry.style_sub,
            )
            for pm, entry in sorted(_PM_REGISTRY.items())
        ]
        return LedStylesListResult(
            ok=True, styles=styles,
            message=f"{len(styles)} style entry(ies)",
        )

@dataclass(frozen=True, slots=True)
class ListLedModes(Command[LedModesListResult]):
    """Enumerate the LEDMode enum names (STATIC, BREATHING, RAINBOW, …)."""

    def execute(self, app: App) -> LedModesListResult:
        del app
        modes = [m.name for m in LEDMode]
        return LedModesListResult(
            ok=True, modes=modes,
            message=f"{len(modes)} mode(s)",
        )

@dataclass(frozen=True, slots=True)
class LedSnapshot(Command[LedSnapshotResult]):
    """Per-device LED state snapshot.

    Polled by UIs to refresh state — logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> LedSnapshotResult:
        s = app.settings.for_led(self.key)
        return LedSnapshotResult(
            ok=True, key=self.key,
            mode=s.mode.name,
            color=s.color,
            brightness=s.brightness,
            global_on=s.global_on,
            test_mode=s.test_mode,
            temp_source=s.temp_source,
            load_source=s.load_source,
            zone_sync=s.zone_sync,
            zone_sync_interval_ticks=s.zone_sync_interval_ticks,
            selected_zone=s.selected_zone,
            zone_count=len(s.zones),
            segment_count=len(s.segment_on),
            message=f"LED snapshot for {self.key}",
        )
