"""LED effect algorithms — RGB color computation per tick.

Pure computation: reads LEDState + HardwareMetrics, advances animation
timers, returns per-segment color lists. No I/O, no protocol, no config.
"""
from __future__ import annotations

from ..core.models import HardwareMetrics, LEDMode, LEDState


class LEDEffectEngine:
    """Compute per-segment RGB colors for LED animation effects.

    Holds references to LEDState and HardwareMetrics. Mutates state timers
    (rgb_timer, test_timer, zone_sync_ticks) as side effects of computation.
    """

    _TEST_COLORS = [(1, 1, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

    def __init__(self, state: LEDState, metrics: HardwareMetrics) -> None:
        self._state = state
        self._metrics = metrics
        # Last color held for sensor-linked modes — used when the chosen
        # source disappears from HardwareMetrics._populated (e.g. Windows
        # VM with no thermal source).  Returning the held color keeps the
        # LED visually stable instead of dropping to the gradient bottom.
        self._last_temp_color: tuple[int, int, int] = (0, 0, 0)
        self._last_load_color: tuple[int, int, int] = (0, 0, 0)

    @property
    def metrics(self) -> HardwareMetrics:
        return self._metrics

    @metrics.setter
    def metrics(self, value: HardwareMetrics) -> None:
        self._metrics = value

    # ── Main dispatch ────────────────────────────────────────────────

    def _tick_single_mode(
        self, mode: LEDMode, color: tuple[int, int, int],
        seg_count: int,
        metric_source: tuple[str, str] | None = None,
    ) -> list[tuple[int, int, int]]:
        """Compute colors for a single mode across seg_count segments.

        If the device has a decoration ring (state.ring_count > 0), ring
        colors are appended after the segment colors.  Rainbow mode gives
        each ring LED a per-position phase offset (C# CHMS_Timer5 for
        ledVal5_1); all other modes fill the ring uniformly.
        """
        match mode:
            case LEDMode.STATIC:
                colors = [color] * seg_count
            case LEDMode.BREATHING:
                colors = self._tick_breathing_for(color, seg_count)
            case LEDMode.COLORFUL:
                colors = self._tick_colorful_for(seg_count)
            case LEDMode.RAINBOW:
                colors = self._tick_rainbow_for(seg_count)
            case LEDMode.TEMP_LINKED:
                colors = self._tick_temp_linked_for(seg_count, metric_source)
            case LEDMode.LOAD_LINKED:
                colors = self._tick_load_linked_for(seg_count, metric_source)
            case _:
                colors = [(0, 0, 0)] * seg_count

        # Decoration ring LEDs (e.g. LF25: 77 ring LEDs after 93 segments)
        ring_count = self._state.ring_count
        if ring_count > 0:
            if mode == LEDMode.RAINBOW:
                colors.extend(self._tick_ring_rainbow(ring_count))
            else:
                # All other modes: ring gets same color as segments
                ring_color = colors[0] if colors else (0, 0, 0)
                colors.extend([ring_color] * ring_count)

        return colors

    def _tick_test_mode(self) -> list[tuple[int, int, int]]:
        """C# checkBox1 test mode: cycle 4 colors every 10 ticks at min brightness."""
        st = self._state
        st.test_timer += 1
        if st.test_timer >= 10:
            st.test_timer = 0
            st.test_color = (st.test_color + 1) % 4
        color = self._TEST_COLORS[st.test_color]
        return [color] * st.led_count

    def _tick_multi_zone(
        self, zone_map: tuple[tuple[int, ...], ...],
        metric_sources: tuple[tuple[str, str], ...] | None = None,
    ) -> list[tuple[int, int, int]]:
        """Compute per-zone colors using physical LED index mapping.

        Each zone's LEDs are placed at their mapped indices.
        Zone map comes from SegmentDisplay.zone_led_map.
        metric_sources maps zone index → (device, kind) for TEMP/LOAD_LINKED.
        """
        st = self._state
        zones = st.zones
        total = st.led_count
        colors: list[tuple[int, int, int]] = [(0, 0, 0)] * total
        for zi, led_indices in enumerate(zone_map):
            if zi >= len(zones):
                break
            z = zones[zi]
            if not z.on:
                continue
            n = len(led_indices)
            src = metric_sources[zi] if metric_sources and zi < len(metric_sources) else None
            zc = self._tick_single_mode(z.mode, z.color, n, metric_source=src)
            if z.brightness < 100:
                scale = z.brightness / 100.0
                zc = [(int(r * scale), int(g * scale), int(b * scale))
                      for r, g, b in zc]
            for i, idx in enumerate(led_indices):
                if idx < total:
                    colors[idx] = zc[i]
        return colors

    def _next_sync_zone(self, current: int) -> int:
        """Find next enabled zone in sync rotation, wrapping around."""
        zones = self._state.zone_sync_zones
        n = len(zones)
        for offset in range(1, n + 1):
            candidate = (current + offset) % n
            if zones[candidate]:
                return candidate
        return 0

    # ── Effect algorithms (ported from FormLED.cs) ──────────────────

    def _tick_breathing_for(self, color: tuple[int, int, int],
                            seg_count: int) -> list[tuple[int, int, int]]:
        """DSHX_Timer: pulse brightness, period=66 ticks."""
        timer = self._state.rgb_timer
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

        self._state.rgb_timer = (timer + 1) % period

        return [(anim_r, anim_g, anim_b)] * seg_count

    def _tick_colorful_for(self, seg_count: int) -> list[tuple[int, int, int]]:
        """QCJB_Timer: 6-phase color gradient cycle with per-segment offset, period=168 ticks.

        Each segment gets a different position in the 168-tick cycle, spread
        evenly — same approach as _tick_rainbow_for across the 768-entry table.
        """
        timer = self._state.rgb_timer
        period = 168
        phase_len = 28
        seg_offset = period // max(seg_count, 1)

        colors: list[tuple[int, int, int]] = []
        for i in range(seg_count):
            t_i = (timer + i * seg_offset) % period
            phase = t_i // phase_len
            off = t_i % phase_len
            t = int(255 * off / (phase_len - 1)) if phase_len > 1 else 0
            if phase == 0:
                colors.append((255, t, 0))
            elif phase == 1:
                colors.append((255 - t, 255, 0))
            elif phase == 2:
                colors.append((0, 255, t))
            elif phase == 3:
                colors.append((0, 255 - t, 255))
            elif phase == 4:
                colors.append((t, 0, 255))
            else:
                colors.append((255, 0, 255 - t))

        self._state.rgb_timer = (timer + 1) % period
        return colors

    def _tick_rainbow_for(self, seg_count: int) -> list[tuple[int, int, int]]:
        """CHMS_Timer: 768-entry RGB table with per-segment offset."""
        from ..core.color import ColorEngine
        table = ColorEngine.get_table()
        timer = self._state.rgb_timer
        table_len = len(table)

        colors = []
        for i in range(seg_count):
            idx = (timer + i * table_len // max(seg_count, 1)) % table_len
            colors.append(table[idx])

        self._state.rgb_timer = (timer + 4) % table_len

        return colors

    def _tick_ring_rainbow(self, ring_count: int) -> list[tuple[int, int, int]]:
        """C# CHMS_Timer5 for ledVal5_1: per-LED rainbow with reversed index.

        Each ring LED gets a phase offset based on position, and the ring
        is filled in reverse order (77-j-1) to create the animation flow.
        Uses the same rgb_timer as the segment rainbow (already advanced).
        """
        from ..core.color import ColorEngine
        table = ColorEngine.get_table()
        table_len = len(table)
        # rgb_timer was already advanced by _tick_rainbow_for, so subtract
        # the increment to use the same timer value as the segments.
        timer = (self._state.rgb_timer - 4) % table_len

        colors: list[tuple[int, int, int]] = [(0, 0, 0)] * ring_count
        for j in range(ring_count):
            idx = (timer + j * table_len // ring_count) % table_len
            colors[ring_count - j - 1] = table[idx]
        return colors

    def _tick_temp_linked_for(
        self, seg_count: int,
        metric_source: tuple[str, str] | None = None,
    ) -> list[tuple[int, int, int]]:
        """WDLD_Timer: color from temperature thresholds.

        Holds the previous color when ``{source}_temp`` isn't populated
        (no thermal sensor on this OS) so the LED stays visually stable
        instead of jumping to the gradient bottom.
        """
        from ..core.color import ColorEngine

        source = metric_source[0] if metric_source and metric_source[0] else self._state.temp_source
        field = f"{source}_temp"
        if field in self._metrics._populated:
            temp = getattr(self._metrics, field, 0)
            self._last_temp_color = ColorEngine.color_for_value(temp, ColorEngine.TEMP_GRADIENT)
        return [self._last_temp_color] * seg_count

    def _tick_load_linked_for(
        self, seg_count: int,
        metric_source: tuple[str, str] | None = None,
    ) -> list[tuple[int, int, int]]:
        """FZLD_Timer: color from CPU/GPU load thresholds.

        Same _populated gate as ``_tick_temp_linked_for`` — held color
        when the OS provides no usage sensor for the chosen source.
        """
        from ..core.color import ColorEngine

        source = metric_source[0] if metric_source and metric_source[0] else self._state.load_source
        field = 'cpu_percent' if source == 'cpu' else 'gpu_usage'
        if field in self._metrics._populated:
            load = self._metrics.cpu_percent if source == 'cpu' else self._metrics.gpu_usage
            self._last_load_color = ColorEngine.color_for_value(load, ColorEngine.LOAD_GRADIENT)
        return [self._last_load_color] * seg_count
