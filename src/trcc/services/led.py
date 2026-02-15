"""LED effect engine, device communication, and config persistence.

Pure Python, no Qt dependencies.
Absorbs business logic from LEDModel (effects), LEDController (HR10, protocol send),
and LEDDeviceController (config, protocol factory).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..core.models import LEDMode, LEDState, LEDZoneState

log = logging.getLogger(__name__)


class LEDService:
    """LED state management, effect computation, config persistence, device send.

    Orchestrates:
    - State mutation (set_mode, set_color, set_brightness, toggles)
    - Effect computation (tick -> per-segment colors)
    - HR10 7-segment rendering
    - Protocol send (build packet, send via LedProtocol)
    - Config save/load (serialize LEDState to conf.py)
    - Style resolution (model_name -> style_id)
    """

    def __init__(self, state: LEDState | None = None) -> None:
        self.state = state or LEDState()
        self._metrics: Dict[str, Any] = {}
        self._protocol: Any = None

        # HR10 state (style 13 — 7-segment digit rendering)
        self._hr10_mode = False
        self._hr10_display_text = "---"
        self._hr10_indicators: set = {'deg'}
        self._hr10_mask: Optional[List[bool]] = None

        # Segment display state (styles 1-11 — all digit-display LED devices)
        self._segment_mode = False
        self._segment_mask: Optional[List[bool]] = None
        self._seg_phase = 0          # Current rotation phase
        self._seg_tick_count = 0     # Ticks since last phase change
        self._seg_phase_ticks = 100  # Ticks per phase (~3s at 30ms tick interval)
        self._seg_temp_unit = "C"    # "C" or "F"
        self._seg_display: Any = None  # SegmentDisplay instance

        # Device identity (for config persistence)
        self._device_key: Optional[str] = None
        self._led_style: int = 1

    # ── Style resolution (static) ───────────────────────────────────

    @staticmethod
    def resolve_style_id(model_name: str) -> int:
        """Resolve LED style_id from device model name.

        Replaces the view-layer iteration over LED_STYLES.
        """
        from ..device_led import LED_STYLES
        for style_id, style in LED_STYLES.items():
            if style.model_name == model_name:
                return style_id
        return 1

    @staticmethod
    def get_style_info(style_id: int) -> Any:
        """Get LedDeviceStyle for a style_id."""
        from ..device_led import LED_STYLES
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

    def set_zone_mode(self, zone: int, mode: LEDMode) -> None:
        """Set mode for a specific zone."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].mode = LEDMode(mode) if not isinstance(mode, LEDMode) else mode

    def set_zone_color(self, zone: int, r: int, g: int, b: int) -> None:
        """Set color for a specific zone."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].color = (r, g, b)

    def set_zone_brightness(self, zone: int, brightness: int) -> None:
        """Set brightness for a specific zone."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].brightness = max(0, min(100, brightness))

    def set_sensor_source(self, source: str) -> None:
        """Set CPU/GPU source for temp/load linked modes and segment cycling."""
        self.state.temp_source = source
        self.state.load_source = source
        # Reset phase to first allowed phase when source changes
        if self._segment_mode and self._seg_display:
            self._seg_phase = self._first_allowed_phase()
            self._seg_tick_count = 0
            self._update_segment_mask()

    def set_seg_temp_unit(self, unit: str) -> None:
        """Set temperature unit for segment display ('C' or 'F')."""
        self._seg_temp_unit = unit
        if self._segment_mode:
            self._update_segment_mask()

    def set_clock_format(self, is_24h: bool) -> None:
        self.state.is_timer_24h = is_24h

    def set_week_start(self, is_sunday: bool) -> None:
        self.state.is_week_sunday = is_sunday

    def update_metrics(self, metrics: Dict[str, Any]) -> None:
        """Update cached sensor metrics for temp/load-linked modes."""
        self._metrics = metrics

    def configure_for_style(self, style_id: int) -> None:
        """Configure state for a specific LED device style."""
        from ..device_led import LED_STYLES
        style = LED_STYLES.get(style_id)
        if style:
            self.state.style = style.style_id
            self.state.led_count = style.led_count
            self.state.segment_count = style.segment_count
            self.state.zone_count = style.zone_count
            self.state.segment_on = [True] * style.segment_count
            if style.zone_count > 1:
                self.state.zones = [LEDZoneState() for _ in range(style.zone_count)]
            else:
                self.state.zones = []

    # ── Effect engine ───────────────────────────────────────────────

    def tick(self) -> List[Tuple[int, int, int]]:
        """Advance animation one tick and return computed per-segment colors.

        Dispatches to mode-specific algorithm. For multi-zone devices,
        divides segments among zones and computes independently.
        For segment display styles, also advances the rotation phase.
        """
        # Advance segment display rotation (all digit-display styles)
        if self._segment_mode and self._seg_display:
            self._seg_tick_count += 1
            if self._seg_tick_count >= self._seg_phase_ticks:
                self._seg_tick_count = 0
                self._seg_phase = self._next_allowed_phase()
            self._update_segment_mask()

        if self.state.zone_count > 1 and self.state.zones:
            return self._tick_multi_zone()
        return self._tick_single_mode(self.state.mode, self.state.color,
                                      self.state.segment_count)

    def _tick_single_mode(self, mode: LEDMode, color: Tuple[int, int, int],
                          seg_count: int) -> List[Tuple[int, int, int]]:
        """Compute colors for a single mode across seg_count segments."""
        if mode == LEDMode.STATIC:
            return [color] * seg_count
        elif mode == LEDMode.BREATHING:
            return self._tick_breathing_for(color, seg_count)
        elif mode == LEDMode.COLORFUL:
            return self._tick_colorful_for(seg_count)
        elif mode == LEDMode.RAINBOW:
            return self._tick_rainbow_for(seg_count)
        elif mode == LEDMode.TEMP_LINKED:
            return self._tick_temp_linked_for(seg_count)
        elif mode == LEDMode.LOAD_LINKED:
            return self._tick_load_linked_for(seg_count)
        return [(0, 0, 0)] * seg_count

    def _tick_multi_zone(self) -> List[Tuple[int, int, int]]:
        """Compute per-zone colors for multi-zone devices."""
        total = self.state.segment_count
        zone_count = len(self.state.zones)
        colors: List[Tuple[int, int, int]] = []

        for zi, zone in enumerate(self.state.zones):
            base = total // zone_count
            n_segs = base + (1 if zi < total % zone_count else 0)

            if not zone.on:
                colors.extend([(0, 0, 0)] * n_segs)
            else:
                zone_colors = self._tick_single_mode(zone.mode, zone.color, n_segs)
                if zone.brightness < 100:
                    scale = zone.brightness / 100.0
                    zone_colors = [
                        (int(r * scale), int(g * scale), int(b * scale))
                        for r, g, b in zone_colors
                    ]
                colors.extend(zone_colors)

        return colors

    # ── Effect algorithms (ported from FormLED.cs) ──────────────────

    def _tick_breathing_for(self, color: Tuple[int, int, int],
                            seg_count: int) -> List[Tuple[int, int, int]]:
        """DSHX_Timer: pulse brightness, period=66 ticks."""
        timer = self.state.rgb_timer
        period = 66
        half = period // 2

        if timer < half:
            factor = timer / half
        else:
            factor = (period - 1 - timer) / half

        r, g, b = color
        anim_r = int(r * factor * 0.8 + r * 0.2)
        anim_g = int(g * factor * 0.8 + g * 0.2)
        anim_b = int(b * factor * 0.8 + b * 0.2)

        self.state.rgb_timer = (timer + 1) % period

        return [(anim_r, anim_g, anim_b)] * seg_count

    def _tick_colorful_for(self, seg_count: int) -> List[Tuple[int, int, int]]:
        """QCJB_Timer: 6-phase color gradient cycle, period=168 ticks."""
        timer = self.state.rgb_timer
        period = 168
        phase_len = 28

        phase = timer // phase_len
        offset = timer % phase_len
        t = int(255 * offset / (phase_len - 1)) if phase_len > 1 else 0

        if phase == 0:
            r, g, b = 255, t, 0
        elif phase == 1:
            r, g, b = 255 - t, 255, 0
        elif phase == 2:
            r, g, b = 0, 255, t
        elif phase == 3:
            r, g, b = 0, 255 - t, 255
        elif phase == 4:
            r, g, b = t, 0, 255
        else:
            r, g, b = 255, 0, 255 - t

        self.state.rgb_timer = (timer + 1) % period

        return [(r, g, b)] * seg_count

    def _tick_rainbow_for(self, seg_count: int) -> List[Tuple[int, int, int]]:
        """CHMS_Timer: 768-entry RGB table with per-segment offset."""
        from ..device_led import ColorEngine
        table = ColorEngine.get_table()
        timer = self.state.rgb_timer
        table_len = len(table)

        colors = []
        for i in range(seg_count):
            idx = (timer + i * table_len // max(seg_count, 1)) % table_len
            colors.append(table[idx])

        self.state.rgb_timer = (timer + 4) % table_len

        return colors

    def _tick_temp_linked_for(self, seg_count: int) -> List[Tuple[int, int, int]]:
        """WDLD_Timer: color from temperature thresholds."""
        from ..device_led import ColorEngine

        source = self.state.temp_source
        temp = self._metrics.get(f"{source}_temp", 0)
        color = ColorEngine.color_for_value(temp, ColorEngine.TEMP_GRADIENT)
        return [color] * seg_count

    def _tick_load_linked_for(self, seg_count: int) -> List[Tuple[int, int, int]]:
        """FZLD_Timer: color from CPU/GPU load thresholds."""
        from ..device_led import ColorEngine

        source = self.state.load_source
        key = "cpu_percent" if source == "cpu" else "gpu_usage"
        load = self._metrics.get(key, 0)
        color = ColorEngine.color_for_value(load, ColorEngine.LOAD_GRADIENT)
        return [color] * seg_count

    # ── HR10 7-segment ──────────────────────────────────────────────

    def set_display_value(self, text: str, indicators: Optional[set] = None) -> None:
        """Set HR10 display text for 7-segment rendering."""
        self._hr10_display_text = text
        self._hr10_indicators = indicators or set()
        self._update_hr10_mask()

    def _update_hr10_mask(self) -> None:
        if not self._hr10_mode:
            return
        from ..device_led_hr10 import Hr10Display
        self._hr10_mask = Hr10Display.get_digit_mask(
            self._hr10_display_text, self._hr10_indicators
        )

    # ── Segment display (all styles 1-11) ────────────────────────

    def _next_allowed_phase(self) -> int:
        """Advance to next phase that matches the selected sensor source."""
        total = self._seg_display.phase_count
        for offset in range(1, total + 1):
            candidate = (self._seg_phase + offset) % total
            if self._phase_allowed(candidate):
                return candidate
        return (self._seg_phase + 1) % total

    def _first_allowed_phase(self) -> int:
        """Find the first phase matching the selected sensor source."""
        total = self._seg_display.phase_count
        for phase in range(total):
            if self._phase_allowed(phase):
                return phase
        return 0

    def _phase_allowed(self, phase: int) -> bool:
        """Check if a phase matches the current sensor source filter."""
        source = self.state.temp_source
        phase_src = self._seg_display.phase_source(phase)
        if phase_src == "other":
            return True
        return phase_src == source

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
        )

    # ── Protocol send ───────────────────────────────────────────────

    def set_protocol(self, protocol: Any) -> None:
        self._protocol = protocol

    def send_colors(self, colors: List[Tuple[int, int, int]]) -> bool:
        """Send pre-computed colors to device. Returns success."""
        if not colors or not self._protocol:
            return False

        if self._segment_mode and self._segment_mask:
            base_color = colors[0] if colors else (0, 0, 0)
            send_colors: list[Any] = [
                base_color if self._segment_mask[i] else (0, 0, 0)
                for i in range(len(self._segment_mask))
            ]
            is_on = None
        elif self._hr10_mode and self._hr10_mask:
            from ..device_led_hr10 import LED_COUNT
            base_color = colors[0] if colors else (0, 0, 0)
            send_colors = [
                base_color if self._hr10_mask[i] else (0, 0, 0)
                for i in range(LED_COUNT)
            ]
            is_on = None
        else:
            send_colors = colors
            is_on = self.state.segment_on

        try:
            return self._protocol.send_led_data(
                send_colors, is_on, self.state.global_on, self.state.brightness
            )
        except Exception as e:
            log.debug("LED send error: %s", e)
            return False

    def send_tick(self) -> bool:
        """Tick animation and send colors to device. Returns success."""
        return self.send_colors(self.tick())

    # ── Device initialization ───────────────────────────────────────

    def initialize(self, device_info: Any, led_style: int = 1) -> str:
        """Initialize for a device. Returns status message."""
        from ..conf import Settings

        self._led_style = led_style
        self._device_key = Settings.device_config_key(
            getattr(device_info, 'device_index', 0),
            device_info.vid,
            device_info.pid,
        )

        self.configure_for_style(led_style)
        self._hr10_mode = (led_style == 13)

        # Activate segment display for all digit-display styles (1-11)
        from ..device_led_segment import get_display
        self._seg_display = get_display(led_style)
        self._segment_mode = self._seg_display is not None

        if self._hr10_mode:
            self._update_hr10_mask()
        if self._segment_mode:
            self._seg_phase = 0
            self._seg_tick_count = 0
            self._update_segment_mask()

        try:
            from ..device_factory import DeviceProtocolFactory
            protocol = DeviceProtocolFactory.get_protocol(device_info)
            self.set_protocol(protocol)
        except Exception as e:
            log.error("LED protocol error: %s", e)
            return f"LED protocol error: {e}"

        self.load_config()

        from ..device_led import LED_STYLES
        style = LED_STYLES.get(led_style)
        name = style.model_name if style else f"Style {led_style}"
        led_count = style.led_count if style else 0
        return f"LED: {name} ({led_count} LEDs)"

    # ── Config persistence ──────────────────────────────────────────

    def save_config(self) -> None:
        """Serialize LEDState to config file."""
        if not self._device_key:
            return
        try:
            from ..conf import Settings

            config: Dict[str, Any] = {
                'mode': self.state.mode.value,
                'color': list(self.state.color),
                'brightness': self.state.brightness,
                'global_on': self.state.global_on,
                'segments_on': self.state.segment_on,
                'temp_source': self.state.temp_source,
                'load_source': self.state.load_source,
                'is_timer_24h': self.state.is_timer_24h,
                'is_week_sunday': self.state.is_week_sunday,
            }
            if self.state.zones:
                config['zones'] = [
                    {
                        'mode': z.mode.value,
                        'color': list(z.color),
                        'brightness': z.brightness,
                        'on': z.on,
                    }
                    for z in self.state.zones
                ]
            Settings.save_device_setting(self._device_key, 'led_config', config)
        except Exception as e:
            log.error("Failed to save LED config: %s", e)

    def load_config(self) -> None:
        """Deserialize LEDState from config file."""
        if not self._device_key:
            return
        try:
            from ..conf import Settings

            dev_config = Settings.get_device_config(self._device_key)
            led_config = dev_config.get('led_config', {})
            if not led_config:
                return

            if 'mode' in led_config:
                self.state.mode = LEDMode(led_config['mode'])
            if 'color' in led_config:
                self.state.color = tuple(led_config['color'])
            if 'brightness' in led_config:
                self.state.brightness = led_config['brightness']
            if 'global_on' in led_config:
                self.state.global_on = led_config['global_on']
            if 'segments_on' in led_config:
                self.state.segment_on = led_config['segments_on']
            if 'temp_source' in led_config:
                self.state.temp_source = led_config['temp_source']
            if 'load_source' in led_config:
                self.state.load_source = led_config['load_source']
            if 'zones' in led_config and self.state.zones:
                for i, z_config in enumerate(led_config['zones']):
                    if i < len(self.state.zones):
                        self.state.zones[i].mode = LEDMode(z_config.get('mode', 0))
                        self.state.zones[i].color = tuple(z_config.get('color', (255, 0, 0)))
                        self.state.zones[i].brightness = z_config.get('brightness', 100)
                        self.state.zones[i].on = z_config.get('on', True)
            if 'is_timer_24h' in led_config:
                self.state.is_timer_24h = led_config['is_timer_24h']
            if 'is_week_sunday' in led_config:
                self.state.is_week_sunday = led_config['is_week_sunday']
        except Exception as e:
            log.error("Failed to load LED config: %s", e)

    def cleanup(self) -> None:
        """Save config and release protocol."""
        self.save_config()
        self.set_protocol(None)
