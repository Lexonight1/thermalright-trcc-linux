"""LED effect algorithms — per-segment RGB color computation per tick.

Pure computation.  Reads ``LedDeviceSettings`` (persistent prefs) +
``LedRuntimeState`` (transient counters) + a sensor snapshot, advances
the counters as side effects, returns a logical color array.  No I/O,
no protocol bytes, no Qt.

Pipeline shape per RenderLed tick::

    sensors = platform.sensors().read_all()
    colors  = LEDEffectEngine().tick(settings, runtime, sensors,
                                     led_count=N)
    colors  = apply_brightness(colors, settings.brightness)  # one writer
    payload = LedPayload(colors=colors, global_on=settings.global_on)
    device.send(payload)         # wire-remap applied inside Led.send

Global brightness is baked into ``colors`` HERE (the single writer), so
both observers of the rendered signal — the device wire and the GUI
preview — show the same thing.  The wire applies only its hardware
``_COLOR_SCALE`` (a perceptual constant, like RGB565 quantization), never
brightness; ``LedPayload`` carries no brightness field.

``LEDEffectEngine`` is stateless across instantiations — every per-tick
mutation lives on the caller-owned ``LedRuntimeState``.  That keeps the
engine safe to import from anywhere without owning state itself, and
lets the App keep one runtime dict keyed by device.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from ..core.led_models import LedDeviceSettings, LEDMode, LedRuntimeState
from ..core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


def apply_brightness(
    colors: list[tuple[int, int, int]], percent: int,
) -> list[tuple[int, int, int]]:
    """Scale a logical color array by a 0–100 % brightness.

    The single conversion point for global LED brightness: ``RenderLed``
    and ``SetLedColors`` bake it into the rendered signal so the device
    wire and the GUI preview observe one identical color list.  100 % is
    the identity, so callers may always route through this.
    """
    scale = max(0, min(100, percent)) / 100.0
    if scale == 1.0:
        return list(colors)
    return [(int(r * scale), int(g * scale), int(b * scale))
            for r, g, b in colors]


# =========================================================================
# ColorEngine — rainbow lookup table + gradient mapping
# =========================================================================


class ColorEngine:
    """Cached 768-entry rainbow table + sensor-value → gradient color.

    Pure constants and pure functions — no instance state.  ``get_table``
    builds the table lazily on first call and caches it as a class attr.
    Matches the legacy FormLED.cs RGBTable byte-for-byte.
    """

    # Gradient stops keyed by sensor value (°C or %).  Linearly
    # interpolated between adjacent stops; clamps to the endpoints
    # outside the range.
    TEMP_GRADIENT: ClassVar[tuple[tuple[float, tuple[int, int, int]], ...]] = (
        (30.0, (  0, 255, 255)),  # cyan
        (50.0, (  0, 255,   0)),  # green
        (70.0, (255, 255,   0)),  # yellow
        (90.0, (255, 110,   0)),  # orange
        (100.0, (255,   0,   0)), # red
    )
    LOAD_GRADIENT: ClassVar[tuple[tuple[float, tuple[int, int, int]], ...]] = (
        TEMP_GRADIENT  # same shape for both
    )

    _TABLE: ClassVar[tuple[tuple[int, int, int], ...] | None] = None

    @classmethod
    def get_table(cls) -> tuple[tuple[int, int, int], ...]:
        """768-entry rainbow lookup table (cached after first call)."""
        if cls._TABLE is None:
            cls._TABLE = tuple(cls._generate_table())
        return cls._TABLE

    @staticmethod
    def _generate_table() -> list[tuple[int, int, int]]:
        """Build the 768-entry HSV rainbow cycle, six 128-step phases."""
        table: list[tuple[int, int, int]] = []
        phase_len = 128
        for i in range(768):
            phase = i // phase_len
            offset = i % phase_len
            t = int(255 * offset / (phase_len - 1)) if phase_len > 1 else 0
            match phase:
                case 0:                   # Red → Yellow
                    table.append((255, t, 0))
                case 1:                   # Yellow → Green
                    table.append((255 - t, 255, 0))
                case 2:                   # Green → Cyan
                    table.append((0, 255, t))
                case 3:                   # Cyan → Blue
                    table.append((0, 255 - t, 255))
                case 4:                   # Blue → Magenta
                    table.append((t, 0, 255))
                case _:                   # Magenta → Red
                    table.append((255, 0, 255 - t))
        return table

    @staticmethod
    def color_for_value(
        value: float,
        gradient: tuple[tuple[float, tuple[int, int, int]], ...],
    ) -> tuple[int, int, int]:
        """Map *value* to an RGB color via linear interpolation between stops."""
        if value <= gradient[0][0]:
            return gradient[0][1]
        if value >= gradient[-1][0]:
            return gradient[-1][1]
        for i in range(len(gradient) - 1):
            lo_val, lo_color = gradient[i]
            hi_val, hi_color = gradient[i + 1]
            if lo_val <= value <= hi_val:
                t = (value - lo_val) / (hi_val - lo_val)
                return (
                    int(lo_color[0] + (hi_color[0] - lo_color[0]) * t),
                    int(lo_color[1] + (hi_color[1] - lo_color[1]) * t),
                    int(lo_color[2] + (hi_color[2] - lo_color[2]) * t),
                )
        return gradient[-1][1]


# =========================================================================
# LEDEffectEngine — dispatches by LEDMode, mutates LedRuntimeState
# =========================================================================


_TEST_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
)
_TEST_PERIOD = 10                 # ticks per test color
_BREATHING_PERIOD = 66            # full pulse cycle
_COLORFUL_PERIOD = 168            # six 28-tick phases
_COLORFUL_PHASE_LEN = 28
_RAINBOW_STEP = 4                 # rainbow advance per tick

# Hue step between adjacent COLOUR GROUPS (one group = one digit).  A strip
# wraps the whole spectrum around its LEDs (``table_len // led_count``); a
# segment number instead wants each digit a single color with only a GENTLE
# gradient between digits — the look the Windows app produces.  These fixed
# steps give that subtle spread regardless of how many digits a zone has
# (≈ one-sixth of the wheel across ten groups), so the rainbow never smears
# across a single digit's seven segments (#193).
_RAINBOW_GROUP_STEP = 768 // 10 // 6        # ≈ 12 / 768  ≈ 1.6 % of the wheel
_COLORFUL_GROUP_STEP = _COLORFUL_PERIOD // 10 // 6   # ≈ 2 / 168


class LEDEffectEngine:
    """Compute one tick's worth of per-segment colors for an LED device.

    Side-effect-free w.r.t. the engine itself — every counter mutation
    happens on the caller-owned ``LedRuntimeState``.  Sensor input is a
    flat ``dict[str, float]`` so the engine never depends on a specific
    SensorEnumerator implementation; ``RenderLed`` builds the dict from
    ``Platform.sensors()`` before calling here.
    """

    def tick(
        self,
        settings: LedDeviceSettings,
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        *,
        led_count: int,
        color_groups: tuple[tuple[int, ...], ...] | None = None,
    ) -> list[tuple[int, int, int]]:
        """Run one tick and return ``led_count`` RGB tuples.

        Test mode short-circuits everything — diagnostic color cycle.
        ``color_groups`` (segment-number displays) colors each digit as one
        cohesive unit; without it the spatial effects render per raw LED,
        which is right for RGB strips but smears a rainbow across a digit's
        segments (#193).  Otherwise dispatch by ``settings.mode``.
        """
        log.debug("tick: mode=%s test=%s led_count=%d groups=%s",
                  settings.mode, settings.test_mode, led_count,
                  len(color_groups) if color_groups else 0)
        if settings.test_mode:
            return self._tick_test(runtime, led_count)

        if color_groups is not None:
            colors = self._color_groups(
                settings.mode, settings.color, runtime, sensors,
                color_groups, led_count,
                settings.temp_source, settings.load_source,
            )
        else:
            colors = self._tick_mode(
                settings.mode, settings.color, runtime, sensors,
                led_count, settings.temp_source, settings.load_source,
            )
        self.advance_phases(runtime)
        return colors

    # ── Multi-zone (PA120 / LF10 — styles with a zone_led_map) ────────

    def tick_multi_zone(
        self,
        settings: LedDeviceSettings,
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        *,
        zone_map: tuple[tuple[int, ...], ...],
        metric_sources: tuple[tuple[str, str], ...] | None,
        led_count: int,
        zone_color_groups: tuple[tuple[tuple[int, ...], ...], ...] | None = None,
    ) -> list[tuple[int, int, int]]:
        """Per-zone colors placed at each zone's physical LED indices.

        Ports legacy ``LEDEffectEngine._tick_multi_zone``: every zone runs
        its own mode + color + brightness over its mapped LED indices
        (``SegmentDisplay.zone_led_map``).  ``metric_sources`` maps zone
        index → (device, kind) so a sensor-linked zone reads its own
        source.  ``zone_color_groups[zi]`` (when present) groups a zone's
        LEDs by digit so each digit is one cohesive color — otherwise the
        spatial effects smear a rainbow across a digit's segments (#193).

        Every zone reads the SAME phase and none of them advances it — the
        frame's single ``advance_phases`` at the end does.  Previously each
        zone's per-mode tick advanced one shared counter, so N animated zones
        stepped it N× per frame: animation ran N-times fast, zones sharing a
        mode came out one step apart, and zones in different modes corrupted
        each other (a ``% 66`` breathe write read back as a ``% 768`` rainbow
        phase).  The C# keeps one counter per effect and advances each once per
        tick (FormLED.cs:4136), because a style-2/7 device has four independent
        per-zone modes reading the same planes.
        """
        # Test mode overrides the whole frame, page AND zone styles alike —
        # the C# ``SendHidVal`` cycles the reference colours across all LEDs
        # regardless of device style, so a zone device must honour it too.
        if settings.test_mode:
            log.debug("tick_multi_zone: test_mode → cycling reference colours")
            return self._tick_test(runtime, led_count)

        colors: list[tuple[int, int, int]] = [(0, 0, 0)] * led_count
        zones = settings.zones
        log.debug("tick_multi_zone: %d zones, led_count=%d grouped=%s",
                  len(zone_map), led_count, zone_color_groups is not None)
        for zi, led_indices in enumerate(zone_map):
            if zi >= len(zones):
                break
            zone = zones[zi]
            if not zone.on:
                log.debug("tick_multi_zone: zone %d off — skipped", zi)
                continue
            src = (
                metric_sources[zi]
                if metric_sources is not None and zi < len(metric_sources)
                else None
            )
            # Zone's device ("cpu"/"gpu") drives both temp + load sources;
            # the zone's mode decides which one applies.  Empty → "cpu".
            zone_source = src[0] if src and src[0] else "cpu"
            groups = (
                zone_color_groups[zi]
                if zone_color_groups is not None and zi < len(zone_color_groups)
                else None
            )
            if groups is not None:
                # One color per digit-group, gentle gradient between digits.
                group_colors = self._scale_brightness(
                    self._tick_mode(
                        zone.mode, zone.color, runtime, sensors,
                        len(groups), zone_source, zone_source, cohesive=True,
                    ),
                    zone.brightness,
                )
                for gi, group_leds in enumerate(groups):
                    for idx in group_leds:
                        if idx < led_count:
                            colors[idx] = group_colors[gi]
                continue
            zone_colors = self._scale_brightness(
                self._tick_mode(
                    zone.mode, zone.color, runtime, sensors,
                    len(led_indices), zone_source, zone_source,
                ),
                zone.brightness,
            )
            for i, idx in enumerate(led_indices):
                if idx < led_count:
                    colors[idx] = zone_colors[i]
        # ONE advance for the whole frame, after every zone has read its phase.
        self.advance_phases(runtime)
        return colors

    @staticmethod
    def next_sync_zone(zone_sync_zones: list[bool], current: int) -> int:
        """Next enabled zone in the sync carousel, wrapping around.

        Ports legacy ``_next_sync_zone``.  Returns 0 when no zone
        participates (defensive — the caller gates on ``zone_sync``).
        """
        n = len(zone_sync_zones)
        if n == 0:
            return 0
        for offset in range(1, n + 1):
            candidate = (current + offset) % n
            if zone_sync_zones[candidate]:
                return candidate
        return 0

    # ── Internal dispatch ────────────────────────────────────────────

    def _tick_mode(
        self,
        mode: LEDMode,
        color: tuple[int, int, int],
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        led_count: int,
        temp_source: str,
        load_source: str,
        *,
        cohesive: bool = False,
    ) -> list[tuple[int, int, int]]:
        """Compute ``led_count`` colors for one logical strip.

        ``cohesive`` is set when ``led_count`` counts COLOUR GROUPS (digits),
        not raw LEDs: the spatial effects then use a gentle fixed hue step so
        each digit reads as one color, instead of wrapping a full rainbow
        across the group (#193).  Non-spatial modes ignore it.
        """
        match mode:
            case LEDMode.STATIC:
                return [color] * led_count
            case LEDMode.BREATHING:
                return self._tick_breathing(color, runtime, led_count)
            case LEDMode.COLORFUL:
                return self._tick_colorful(
                    runtime, led_count,
                    _COLORFUL_GROUP_STEP if cohesive else None)
            case LEDMode.RAINBOW:
                return self._tick_rainbow(
                    runtime, led_count,
                    _RAINBOW_GROUP_STEP if cohesive else None)
            case LEDMode.TEMP_LINKED:
                return self._tick_temp_linked(runtime, sensors, led_count,
                                              temp_source)
            case LEDMode.LOAD_LINKED:
                return self._tick_load_linked(runtime, sensors, led_count,
                                              load_source)
        return [(0, 0, 0)] * led_count

    @staticmethod
    def cohere_digit_groups(
        colors: list[tuple[int, int, int]],
        groups: tuple[tuple[int, ...], ...],
    ) -> list[tuple[int, int, int]]:
        """Collapse each digit group to one color (its first LED's), in place
        on a copy.

        For styles that pair segment digits with a decoration STRIP (LF10/
        LF12): the strip must keep the spatial rainbow the effect already gave
        it, so we don't group it — we only flatten the digits here, after the
        effect, leaving every other LED on its per-LED spread (#193).
        """
        out = list(colors)
        for group in groups:
            if not group:
                continue
            head = group[0]
            if head >= len(out):
                continue
            color = out[head]
            for led in group:
                if led < len(out):
                    out[led] = color
        return out

    @staticmethod
    def _scale_brightness(
        colors: list[tuple[int, int, int]], brightness: int,
    ) -> list[tuple[int, int, int]]:
        """Scale a color list by a 0–100 % zone brightness (no-op at 100)."""
        if brightness >= 100:
            return colors
        scale = brightness / 100.0
        return [(int(r * scale), int(g * scale), int(b * scale))
                for r, g, b in colors]

    def _color_groups(
        self,
        mode: LEDMode,
        color: tuple[int, int, int],
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        groups: tuple[tuple[int, ...], ...],
        led_count: int,
        temp_source: str,
        load_source: str,
    ) -> list[tuple[int, int, int]]:
        """One color per group, written to every LED the group covers.

        Each group is a digit (its seven segments), so the whole digit reads
        as a single color and only a gentle gradient separates digits — the
        Windows look, without the C#'s hand-mapped ``ledVal`` slots (#193).
        Out-of-range indices are skipped defensively.
        """
        group_colors = self._tick_mode(
            mode, color, runtime, sensors, len(groups),
            temp_source, load_source, cohesive=True,
        )
        out: list[tuple[int, int, int]] = [(0, 0, 0)] * led_count
        for gi, leds in enumerate(groups):
            gc = group_colors[gi]
            for led in leds:
                if 0 <= led < led_count:
                    out[led] = gc
        return out

    def _tick_test(
        self,
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """Cycle 4 bright reference colours every ``_TEST_PERIOD`` ticks."""
        runtime.test_timer += 1
        if runtime.test_timer >= _TEST_PERIOD:
            runtime.test_timer = 0
            runtime.test_color = (runtime.test_color + 1) % len(_TEST_COLORS)
        return [_TEST_COLORS[runtime.test_color]] * led_count

    # ── Phase advance ────────────────────────────────────────────────

    @staticmethod
    def advance_phases(runtime: LedRuntimeState) -> None:
        """Advance every animated effect's phase by ONE tick.

        Called once per rendered frame, AFTER the frame's colours are computed
        — so a frame renders at the phase it started with, exactly as the
        read-then-increment inside each effect used to do, and frame 0 still
        renders at phase 0.

        Every phase advances whether or not anything is currently showing that
        effect, which is the C# shape: `MyTimer_Event` (FormLED.cs:4136)
        computes all six planes for a style-2/7 device on every tick regardless
        of which of `myLedMode1..4` each zone selected.  The alternative —
        advancing only the modes in use — makes an effect's speed depend on how
        many zones happen to be showing it.
        """
        table_len = len(ColorEngine.get_table())
        runtime.breathe_phase = (runtime.breathe_phase + 1) % _BREATHING_PERIOD
        runtime.colorful_phase = (runtime.colorful_phase + 1) % _COLORFUL_PERIOD
        runtime.rainbow_phase = (runtime.rainbow_phase + _RAINBOW_STEP) % table_len
        frame_log.debug("advance_phases: breathe=%d colorful=%d rainbow=%d",
                        runtime.breathe_phase, runtime.colorful_phase,
                        runtime.rainbow_phase)

    # ── Effect algorithms ────────────────────────────────────────────

    @staticmethod
    def _tick_breathing(
        color: tuple[int, int, int],
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """Pulse brightness through a ``_BREATHING_PERIOD``-tick cycle."""
        timer = runtime.breathe_phase
        half = _BREATHING_PERIOD // 2
        factor = timer / half if timer < half else (_BREATHING_PERIOD - 1 - timer) / half
        r, g, b = color
        # Floor at 20% so the LEDs never fully extinguish — matches legacy
        anim = (
            int(r * factor * 0.8 + r * 0.2),
            int(g * factor * 0.8 + g * 0.2),
            int(b * factor * 0.8 + b * 0.2),
        )
        return [anim] * led_count

    @staticmethod
    def _tick_colorful(
        runtime: LedRuntimeState,
        led_count: int,
        step: int | None = None,
    ) -> list[tuple[int, int, int]]:
        """6-phase gradient cycle.  ``step`` overrides the per-element hue
        offset (groups pass a small fixed step; a strip wraps the spectrum)."""
        timer = runtime.colorful_phase
        seg_offset = step if step is not None else _COLORFUL_PERIOD // max(led_count, 1)
        colors: list[tuple[int, int, int]] = []
        for i in range(led_count):
            t_i = (timer + i * seg_offset) % _COLORFUL_PERIOD
            phase = t_i // _COLORFUL_PHASE_LEN
            off = t_i % _COLORFUL_PHASE_LEN
            t = int(255 * off / (_COLORFUL_PHASE_LEN - 1))
            match phase:
                case 0:
                    colors.append((255, t, 0))
                case 1:
                    colors.append((255 - t, 255, 0))
                case 2:
                    colors.append((0, 255, t))
                case 3:
                    colors.append((0, 255 - t, 255))
                case 4:
                    colors.append((t, 0, 255))
                case _:
                    colors.append((255, 0, 255 - t))
        return colors

    @staticmethod
    def _tick_rainbow(
        runtime: LedRuntimeState,
        led_count: int,
        step: int | None = None,
    ) -> list[tuple[int, int, int]]:
        """768-entry table shift.  ``step`` overrides the per-element table
        offset (groups pass a small fixed step; a strip wraps the spectrum)."""
        table = ColorEngine.get_table()
        table_len = len(table)
        timer = runtime.rainbow_phase
        stride = step if step is not None else table_len // max(led_count, 1)
        colors = [table[(timer + i * stride) % table_len]
                  for i in range(led_count)]
        return colors

    @staticmethod
    def _tick_temp_linked(
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        led_count: int,
        source: str,
    ) -> list[tuple[int, int, int]]:
        """Color from a temperature gradient.  Holds previous color if no sensor."""
        sensor_id = f"{source}:temp" if source == "cpu" else "gpu:primary:temp"
        value = sensors.get(sensor_id)
        if value is not None:
            runtime.last_temp_color = ColorEngine.color_for_value(
                value, ColorEngine.TEMP_GRADIENT,
            )
        return [runtime.last_temp_color] * led_count

    @staticmethod
    def _tick_load_linked(
        runtime: LedRuntimeState,
        sensors: dict[str, float],
        led_count: int,
        source: str,
    ) -> list[tuple[int, int, int]]:
        """Color from CPU/GPU load gradient.  Holds previous color if no sensor."""
        sensor_id = "cpu:usage" if source == "cpu" else "gpu:primary:usage"
        value = sensors.get(sensor_id)
        if value is not None:
            runtime.last_load_color = ColorEngine.color_for_value(
                value, ColorEngine.LOAD_GRADIENT,
            )
        return [runtime.last_load_color] * led_count
