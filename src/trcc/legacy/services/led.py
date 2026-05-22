"""LED effect engine and device communication.

Pure Python, no Qt dependencies.
Business logic: effects, protocol send, config persistence.
LEDDevice (core/led_device.py) delegates to this service.
"""
from __future__ import annotations

import logging
from typing import Any

from ..core.models import (
    LED_SELECT_ALL_STYLES,
    LED_STYLES,
    HardwareMetrics,
    LEDMode,
    LEDState,
    LEDZoneState,
)
from .led_effects import LEDEffectEngine

log = logging.getLogger(__name__)

# LED animation tick period (ms) — matches trcc_app.py LEDHandler._timer.
# C# Timer_event runs at ~167ms; our QTimer runs at 150ms.
_LED_TICK_MS = 150


class LEDService:
    """LED state management, effect computation, config persistence, device send.

    Orchestrates:
    - State mutation (set_mode, set_color, set_brightness, toggles)
    - Effect computation (tick -> per-segment colors)
    - Protocol send (build packet, send via LedProtocol)
    - Config save/load (serialize LEDState to conf.py)
    - Style resolution (model_name -> style_id)
    """

    # Styles where the checkbox means "Select all" (sync all zones)
    # instead of "Circulate" (timer rotation). C# FormLED.cs buttonLB_Click.
    SELECT_ALL_STYLES = LED_SELECT_ALL_STYLES

    # Methods delegated to LEDEffectEngine via __getattr__
    _ENGINE_METHODS = frozenset({
        '_tick_single_mode', '_tick_test_mode', '_tick_multi_zone',
        '_tick_breathing_for', '_tick_colorful_for', '_tick_rainbow_for',
        '_tick_temp_linked_for', '_tick_load_linked_for', '_next_sync_zone',
    })

    def __init__(self, state: LEDState | None = None,
                 protocol: Any = None,
                 led_config: Any = None) -> None:
        self.state = state or LEDState()
        self._metrics: HardwareMetrics = HardwareMetrics()
        self._engine = LEDEffectEngine(self.state, self._metrics)
        self._protocol: Any = protocol  # DI'd by the owning LEDDevice
        self._led_config = led_config

        # Segment display state (styles 1-11 — all digit-display LED devices)
        self._segment_mode = False
        self._segment_mask: list[bool] | None = None
        self._seg_phase = 0          # Current rotation phase
        self._seg_tick_count = 0     # Ticks since last phase change
        self._seg_phase_ticks = self.state.carousel_interval
        self._seg_temp_unit = "C"    # "C" or "F"
        self._seg_display: Any = None  # SegmentDisplay instance

        # Device identity (for config persistence)
        self._device_key: str | None = None
        self._led_style: int = 1

    # ── Style resolution (static) ───────────────────────────────────

    @staticmethod
    def resolve_style_id(model_name: str) -> int:
        """Resolve LED style_id from device model name."""
        return LED_STYLES.by_name(model_name)

    @staticmethod
    def get_style_info(style_id: int) -> Any:
        """Get LedDeviceStyle for a style_id, or None if unknown."""
        return LED_STYLES.get(style_id)

    # ── State mutators ──────────────────────────────────────────────

    def set_mode(self, mode: LEDMode) -> None:
        """Set LED effect mode."""
        self.state.mode = LEDMode(mode) if not isinstance(mode, LEDMode) else mode
        self.state.rgb_timer = 0
        return None

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set global LED color."""
        self.state.color = (r, g, b)

    def set_brightness(self, brightness: int) -> None:
        """Set global brightness (0-100)."""
        self.state.brightness = max(0, min(100, brightness))

    def toggle_global(self, on: bool) -> None:
        """Set global on/off."""
        self.state.global_on = on

    def toggle_segment(self, index: int, on: bool) -> None:
        """Toggle a single LED segment."""
        if 0 <= index < len(self.state.segment_on):
            self.state.segment_on[index] = on

    def toggle_zone(self, zone: int, on: bool) -> None:
        """Toggle a specific zone on/off (C# myOnOff1-4)."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].on = on

    def set_test_mode(self, enabled: bool) -> None:
        """Toggle LED test mode (C# checkBox1).

        Cycles white/red/green/blue at brightness=1 every 10 ticks.
        """
        self.state.test_mode = enabled
        self.state.test_timer = 0
        self.state.test_color = 0

    def set_selected_zone(self, zone: int) -> None:
        """Set the currently selected zone (drives segment display phase).

        Also syncs zone_sync_zones: C# radio-select sets clicked zone's
        LunBo=true and others false. When circulate enables, rotation
        starts from whichever zones are in zone_sync_zones.
        """
        self.state.selected_zone = zone
        for i in range(len(self.state.zone_sync_zones)):
            self.state.zone_sync_zones[i] = (i == zone)

    def set_zone_sync(self, enabled: bool) -> None:
        """Enable/disable zone sync (C# isLunBo).

        One checkbox, one flag. Style determines behavior:
        Styles 2/7: "Select all" — sync all zones to selected zone's settings.
        Other styles: "Circulate" — timer-based rotation through enabled zones.

        C# does NOT auto-enable zones. Radio-select mode keeps only the
        selected zone in zone_sync_zones. User adds more zones by clicking
        zone buttons while circulate is active.
        """
        self.state.zone_sync = enabled
        if enabled:
            if self._led_style in self.SELECT_ALL_STYLES:
                self._sync_all_zones_to_selected()
            else:
                self.state.zone_sync_ticks = 0
                self.state.zone_sync_current = self._next_sync_zone(-1)

    def set_zone_sync_zone(self, zone: int, selected: bool) -> None:
        """Toggle a zone's participation in sync rotation (C# LunBo1-4)."""
        if 0 <= zone < len(self.state.zone_sync_zones):
            if not selected:
                active = sum(self.state.zone_sync_zones)
                if active <= 1:
                    return
            self.state.zone_sync_zones[zone] = selected

    def set_zone_sync_interval(self, seconds: int) -> None:
        """Set sync interval in seconds (C# textBoxTimer).

        C# uses ``6 * seconds`` for its ~167ms tick. We compute from our
        actual tick period for accuracy: 1 second = round(1000/150) = 7 ticks.
        """
        self.state.zone_sync_interval = max(1, round(seconds * 1000 / _LED_TICK_MS))

    @property
    def _select_all_active(self) -> bool:
        """True when zone_sync is on AND style uses select-all behavior."""
        return self.state.zone_sync and self._led_style in self.SELECT_ALL_STYLES

    def _apply_to_zones(self, zone: int, field: str, value: object) -> None:
        """Set *field* on one zone, or all zones when select-all is active."""
        if self._select_all_active:
            for z in self.state.zones:
                setattr(z, field, value)
        elif 0 <= zone < len(self.state.zones):
            setattr(self.state.zones[zone], field, value)

    def set_zone_mode(self, zone: int, mode: LEDMode) -> None:
        """Set mode for a specific zone. Propagates to all if select-all active."""
        self._apply_to_zones(zone, "mode", LEDMode(mode) if not isinstance(mode, LEDMode) else mode)

    def set_zone_color(self, zone: int, r: int, g: int, b: int) -> None:
        """Set color for a specific zone. Propagates to all if select-all active."""
        self._apply_to_zones(zone, "color", (r, g, b))

    def set_zone_brightness(self, zone: int, brightness: int) -> None:
        """Set brightness for a specific zone. Propagates to all if select-all active."""
        self._apply_to_zones(zone, "brightness", max(0, min(100, brightness)))

    def set_disk_index(self, index: int) -> None:
        """Set which disk to monitor (C# hardDiskCount, 0-based)."""
        self.state.disk_index = max(0, index)

    def set_memory_ratio(self, ratio: int) -> None:
        """Set DDR memory multiplier (C# memoryRatio: 1, 2, or 4)."""
        self.state.memory_ratio = ratio if ratio in (1, 2, 4) else 2

    def set_sensor_source(self, source: str) -> None:
        """Set CPU/GPU source for temp/load linked modes and segment cycling."""
        self.state.temp_source = source
        self.state.load_source = source

    def set_seg_temp_unit(self, unit: str) -> None:
        """Set temperature unit for segment display ('C' or 'F')."""
        self._seg_temp_unit = unit
        if self._segment_mode:
            self._update_segment_mask()

    def set_clock_format(self, is_24h: bool) -> None:
        self.state.is_timer_24h = is_24h

    def set_week_start(self, is_sunday: bool) -> None:
        self.state.is_week_sunday = is_sunday

    def update_metrics(self, metrics: HardwareMetrics) -> None:
        """Update cached sensor metrics and recompute segment mask.

        Segment mask only changes when metrics change — not every tick.
        """
        self._metrics = metrics
        self._engine.metrics = metrics
        if self._segment_mode and self._seg_display:
            self._update_segment_mask()

    def configure_for_style(self, style_id: int, style_sub: int = 0) -> None:
        """Configure state for a specific LED device style.

        Sets up LED segment counts/zones from the style registry and
        activates segment display rotation for digit-display styles (1-11).
        ``style_sub`` selects wire remap variants (e.g. LF25 = style 5, sub 1)
        which may add decoration ring LEDs.
        """
        from ..core.led_segment import get_display
        from ..core.models import LED_REMAP_SUB_TABLES, LED_STYLES

        self._led_style = style_id
        style = LED_STYLES[style_id]
        self.state.style = style.style_id
        self.state.sub_style = style_sub
        self.state.led_count = style.led_count
        self.state.segment_count = style.segment_count
        self.state.zone_count = style.zone_count
        self.state.segment_on = [True] * style.segment_count

        # Decoration ring: sub-variant remap tables may be longer than
        # the base led_count — extra entries are ring LEDs.
        sub_table = LED_REMAP_SUB_TABLES.get((style_id, style_sub))
        self.state.ring_count = (
            len(sub_table) - style.led_count if sub_table else 0)
        if style.zone_count > 1:
            self.state.zones = [LEDZoneState() for _ in range(style.zone_count)]
            # Must also size zone_sync_zones — __post_init__ ran with
            # default zone_count=1 which leaves it empty.
            if len(self.state.zone_sync_zones) != style.zone_count:
                self.state.zone_sync_zones = (
                    [True] + [False] * (style.zone_count - 1))
        else:
            self.state.zones = []
            self.state.zone_sync_zones = []

        self._seg_display = get_display(style_id)
        self._segment_mode = self._seg_display is not None

        if self._segment_mode:
            self._seg_phase = 0
            self._seg_tick_count = 0
            self._update_segment_mask()

    # ── Effect engine ───────────────────────────────────────────────

    def tick(self) -> list[tuple[int, int, int]]:
        """Advance animation one tick and return computed per-segment colors.

        Dispatches to mode-specific algorithm. For multi-zone devices,
        divides segments among zones and computes independently.
        For segment display styles, also advances the rotation phase.
        """
        # Test mode: cycle white/red/green/blue at brightness=1
        if self.state.test_mode:
            return self._tick_test_mode()

        # Segment display phase = active zone (for circulate animation)
        # Mask is cached — only recomputed in update_metrics(), not every tick.
        if self._segment_mode and self._seg_display:
            if self.state.zone_sync and self._led_style not in self.SELECT_ALL_STYLES:
                self.state.zone_sync_ticks += 1
                if self.state.zone_sync_ticks >= self.state.zone_sync_interval:
                    self.state.zone_sync_ticks = 0
                    self.state.zone_sync_current = self._next_sync_zone(
                        self.state.zone_sync_current)
                self._seg_phase = self.state.zone_sync_current
            else:
                self._seg_phase = self.state.selected_zone

        # Multi-zone segment displays
        if self._segment_mode and self.state.zones and self._seg_display:
            zone_map = self._seg_display.zone_led_map
            if zone_map:
                # Styles 2/7 (PA120/LF10): physical zones with per-zone
                # color/mode — each zone colors its own mapped LED indices.
                return self._tick_multi_zone(
                    zone_map, self._seg_display.zone_metric_sources)
            # Non-2/7 styles: C# uses global rgbR1/G1/B1 and myLedMode.
            # Zones only drive segment display data rotation (CPU/GPU),
            # not LED color. Fall through to global tick below.

        return self._tick_single_mode(self.state.mode, self.state.color,
                                      self.state.segment_count)

    # ── Segment display (all styles 1-11) ────────────────────────

    def _sync_all_zones_to_selected(self) -> None:
        """Copy selected zone's mode/color/brightness to all zones (Select all)."""
        zones = self.state.zones
        sel = self.state.selected_zone
        if not zones or sel < 0 or sel >= len(zones):
            return
        src = zones[sel]
        for z in zones:
            z.mode = src.mode
            z.color = src.color
            z.brightness = src.brightness

    def _next_sync_zone(self, current: int) -> int:
        """Find next enabled zone in carousel, wrapping around."""
        zones = self.state.zone_sync_zones
        n = len(zones)
        for offset in range(1, n + 1):
            candidate = (current + offset) % n
            if zones[candidate]:
                return candidate
        return current

    def _update_segment_mask(self) -> None:
        """Recompute segment mask from current metrics + rotation phase.

        Delegates to the unified SegmentDisplay class hierarchy in
        device_led_segment.py.  Each style handles its own phase/metric
        mapping internally.
        """
        if not self._seg_display:
            return
        self._segment_mask = self._seg_display.compute_mask(
            self._metrics,
            self._seg_phase,
            self._seg_temp_unit,
            is_24h=self.state.is_timer_24h,
            week_sunday=self.state.is_week_sunday,
            sub_style=self.state.sub_style,
            memory_ratio=self.state.memory_ratio,
        )

    # ── Display-ready colors ────────────────────────────────────────

    def apply_mask(self, colors: list[tuple[int, int, int]]
                   ) -> list[tuple[int, int, int]]:
        """Apply segment mask to produce per-LED color array.

        Returns the same masked array used for both hardware send and
        preview rendering — one entry per physical LED position.
        """
        if self._segment_mode and self._segment_mask:
            n = len(self._segment_mask)
            if len(colors) == n:
                # Per-LED colors (physical zone mapping)
                return [
                    colors[i] if self._segment_mask[i] else (0, 0, 0)
                    for i in range(n)
                ]
            base = colors[0] if colors else (0, 0, 0)
            return [
                base if self._segment_mask[i] else (0, 0, 0)
                for i in range(n)
            ]
        return colors

    # ── Protocol send ───────────────────────────────────────────────

    @property
    def has_protocol(self) -> bool:
        return self._protocol is not None

    def set_protocol(self, protocol: Any) -> None:
        self._protocol = protocol

    def send_colors(self, colors: list[tuple[int, int, int]]) -> bool:
        """Send pre-computed colors to device. Returns success."""
        if not colors or not self._protocol:
            log.debug("send_colors: skipped (colors=%d, protocol=%s)",
                       len(colors) if colors else 0, self._protocol is not None)
            return False

        send_colors = self.apply_mask(colors)
        is_on = None if self._segment_mode else self.state.segment_on

        try:
            return self._protocol.send_data(
                send_colors, is_on, self.state.global_on, self.state.brightness
            )
        except Exception as e:
            log.warning("send_colors: failed: %s", e)
            return False

    def send_tick(self) -> bool:
        """Tick animation and send colors to device. Returns success."""
        return self.send_colors(self.tick())

    @staticmethod
    def zones_to_ansi(colors: list[tuple[int, int, int]]) -> str:
        """Render LED zone colors as ANSI true-color blocks for terminal preview."""
        if not colors:
            return ''
        parts = [
            f'\033[48;2;{r};{g};{b}m  \033[0m' for r, g, b in colors
        ]
        return ''.join(parts)

    # ── Device initialization ───────────────────────────────────────

    def initialize(self, device_info: Any, led_style: int = 1) -> str:
        """Initialize for a device. Handshake determines identity from PM.

        The handshake is authoritative — PM byte resolves the exact device
        style, same way LCD handshake resolves resolution/FBL. The led_style
        param is a fallback if handshake fails or returns None.
        """
        self._device = device_info
        if self._led_config:
            self._device_key = self._led_config.device_key(device_info)

        # Handshake first — PM byte is the source of truth for device identity.
        # The protocol was DI'd at construction (Phase 4) — no lazy fetch needed.
        try:
            if self._protocol is None:
                return "LED protocol not injected"
            hs = self._protocol.handshake()

            if hs and getattr(hs, 'style', None):
                led_style = hs.style.style_id
                style_sub = getattr(hs, 'style_sub', 0)
                device_info.led_style_id = led_style
                device_info.led_style_sub = style_sub
                device_info.model = getattr(hs, 'model_name', device_info.model)
                log.info("LED handshake: PM=%d → style=%d sub=%d model=%s",
                         getattr(hs, 'pm', 0), led_style, style_sub,
                         device_info.model)
        except Exception as e:
            log.error("LED protocol error: %s", e)
            return f"LED protocol error: {e}"

        self._led_style = led_style
        style_sub = getattr(device_info, 'led_style_sub', 0)
        self.configure_for_style(led_style, style_sub)
        self.load_config()

        from ..core.models import LED_STYLES
        style = LED_STYLES[led_style]
        return f"LED: {style.model_name} ({style.led_count} LEDs)"

    # ── Config persistence (delegates to led_config.py) ────────────

    def save_config(self) -> None:
        """Serialize LEDState to config file."""
        if self._device and self._led_config:
            self._led_config.save_state(self._device, self.state)

    def load_config(self) -> None:
        """Deserialize LEDState from config file."""
        if self._device and self._led_config:
            self._led_config.load_state(self._device, self.state)

    def cleanup(self) -> None:
        """Save config and release protocol."""
        self.save_config()
        self.set_protocol(None)

    # ── Effect engine delegation ─────────────────────────────────

    def __getattr__(self, name: str):
        if name in self._ENGINE_METHODS:
            try:
                engine = object.__getattribute__(self, '_engine')
            except AttributeError:
                raise AttributeError(name) from None
            return getattr(engine, name)
        raise AttributeError(f'{type(self).__name__!r} has no attribute {name!r}')
