"""LED effect algorithms — per-segment RGB color computation per tick.

Pure computation.  Reads ``LedDeviceSettings`` (persistent prefs) +
``LedRuntimeState`` (transient counters) + a sensor snapshot, advances
the counters as side effects, returns a logical color array.  No I/O,
no protocol bytes, no Qt.

Pipeline shape per RenderLed tick::

    sensors = platform.sensors().read_all()
    colors  = LEDEffectEngine().tick(settings, runtime, sensors,
                                     led_count=N)
    payload = LedPayload(colors=colors,
                        global_on=settings.global_on,
                        brightness=settings.brightness)
    device.send(payload)         # wire-remap applied inside Led.send

``LEDEffectEngine`` is stateless across instantiations — every per-tick
mutation lives on the caller-owned ``LedRuntimeState``.  That keeps the
engine safe to import from anywhere without owning state itself, and
lets the App keep one runtime dict keyed by device.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from ..core.led_models import LedDeviceSettings, LEDMode, LedRuntimeState

log = logging.getLogger(__name__)

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
    (1, 1, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1),
)
_TEST_PERIOD = 10                 # ticks per test color
_BREATHING_PERIOD = 66            # full pulse cycle
_COLORFUL_PERIOD = 168            # six 28-tick phases
_COLORFUL_PHASE_LEN = 28
_RAINBOW_STEP = 4                 # rainbow advance per tick


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
    ) -> list[tuple[int, int, int]]:
        """Run one tick and return ``led_count`` RGB tuples.

        Test mode short-circuits everything — diagnostic color cycle.
        Otherwise dispatch by ``settings.mode``.
        """
        log.debug("tick: mode=%s test=%s led_count=%d",
                  settings.mode, settings.test_mode, led_count)
        if settings.test_mode:
            return self._tick_test(runtime, led_count)

        return self._tick_mode(
            settings.mode, settings.color, runtime, sensors,
            led_count, settings.temp_source, settings.load_source,
        )

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
    ) -> list[tuple[int, int, int]]:
        match mode:
            case LEDMode.STATIC:
                return [color] * led_count
            case LEDMode.BREATHING:
                return self._tick_breathing(color, runtime, led_count)
            case LEDMode.COLORFUL:
                return self._tick_colorful(runtime, led_count)
            case LEDMode.RAINBOW:
                return self._tick_rainbow(runtime, led_count)
            case LEDMode.TEMP_LINKED:
                return self._tick_temp_linked(runtime, sensors, led_count,
                                              temp_source)
            case LEDMode.LOAD_LINKED:
                return self._tick_load_linked(runtime, sensors, led_count,
                                              load_source)
        return [(0, 0, 0)] * led_count

    def _tick_test(
        self,
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """Cycle 4 dim reference colors every ``_TEST_PERIOD`` ticks."""
        runtime.test_timer += 1
        if runtime.test_timer >= _TEST_PERIOD:
            runtime.test_timer = 0
            runtime.test_color = (runtime.test_color + 1) % len(_TEST_COLORS)
        return [_TEST_COLORS[runtime.test_color]] * led_count

    # ── Effect algorithms ────────────────────────────────────────────

    @staticmethod
    def _tick_breathing(
        color: tuple[int, int, int],
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """Pulse brightness through a ``_BREATHING_PERIOD``-tick cycle."""
        timer = runtime.rgb_timer
        half = _BREATHING_PERIOD // 2
        factor = timer / half if timer < half else (_BREATHING_PERIOD - 1 - timer) / half
        r, g, b = color
        # Floor at 20% so the LEDs never fully extinguish — matches legacy
        anim = (
            int(r * factor * 0.8 + r * 0.2),
            int(g * factor * 0.8 + g * 0.2),
            int(b * factor * 0.8 + b * 0.2),
        )
        runtime.rgb_timer = (timer + 1) % _BREATHING_PERIOD
        return [anim] * led_count

    @staticmethod
    def _tick_colorful(
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """6-phase gradient cycle with per-segment offset."""
        timer = runtime.rgb_timer
        seg_offset = _COLORFUL_PERIOD // max(led_count, 1)
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
        runtime.rgb_timer = (timer + 1) % _COLORFUL_PERIOD
        return colors

    @staticmethod
    def _tick_rainbow(
        runtime: LedRuntimeState,
        led_count: int,
    ) -> list[tuple[int, int, int]]:
        """768-entry table shift with per-segment offset."""
        table = ColorEngine.get_table()
        table_len = len(table)
        timer = runtime.rgb_timer
        stride = table_len // max(led_count, 1)
        colors = [table[(timer + i * stride) % table_len]
                  for i in range(led_count)]
        runtime.rgb_timer = (timer + _RAINBOW_STEP) % table_len
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
