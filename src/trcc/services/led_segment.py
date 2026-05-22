"""Segment-display renderers for LED styles 1-11 (port of legacy `core/led_segment.py`).

Each style class declares its layout as class-level data (mask size,
phase count, digit-LED indices) and implements ``compute_mask()`` that
returns the on/off mask for one tick.

Class hierarchy::

    SegmentDisplay      — 7-seg + 13-seg encoding tables + helpers
    ├── AX120Display    — style 1:  30 LEDs, 3-digit, 4-phase rotation
    ├── PA120Display    — style 2:  84 LEDs, 4 simultaneous values
    ├── AK120Display    — style 3:  64 LEDs, 2-phase CPU/GPU
    ├── LC1Display      — style 4:  31 LEDs, mode-based 3-phase
    ├── LF8Display      — style 5/11: 93 LEDs, 4-metric 2-phase
    │   └── LF12Display — style 6:  124 LEDs = LF8 + 31 decoration
    ├── LF10Display     — style 7:  116 LEDs, 13-segment + decoration
    ├── CZ1Display      — style 8:  18 LEDs, 2-digit 4-phase
    ├── LC2Display      — style 9:  61 LEDs, clock display
    └── LF11Display     — style 10: 38 LEDs, 4-phase sensor

Metric input is a ``MetricsLike`` — any object exposing the legacy
attribute names (``cpu_temp``, ``gpu_usage``, …) via ``getattr``.  Use
``LegacyMetricsView`` to wrap next/'s ``dict[str, SensorReading]``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import (
    Any,
    ClassVar,
    Protocol,
)

from ..core.models import LedStyle, SensorReading

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Metric source — any object exposing legacy attribute names.
# ═══════════════════════════════════════════════════════════════════════


class MetricsLike(Protocol):
    """Anything exposing ``cpu_temp`` / ``gpu_usage`` / … via attribute lookup.

    The segment displays only ever ``getattr(m, key, 0)``, so a minimal
    duck type is enough — the legacy ``HardwareMetrics`` dataclass and
    next/'s :class:`LegacyMetricsView` both satisfy it.
    """

    def __getattr__(self, name: str) -> float: ...


class LegacyMetricsView:
    """Wrap next/'s ``dict[str, SensorReading]`` so legacy attribute
    lookups (``cpu_temp``, ``gpu_usage``, …) just work.

    The 11 :class:`SegmentDisplay` subclasses port verbatim from legacy
    because every metric access goes through ``getattr(m, name, 0)``,
    which lands in ``__getattr__`` here and translates to a colon-
    namespaced sensor_id lookup against the underlying dict.
    """

    __slots__ = ("_readings",)

    # Legacy attribute name → next/ sensor_id.  Anything not in this
    # map (or absent from the dict) reads as 0.0.
    _LEGACY_TO_NEXT: ClassVar[Mapping[str, str]] = {
        # CPU
        "cpu_temp": "cpu:temp",
        "cpu_percent": "cpu:usage",
        "cpu_power": "cpu:power",
        "cpu_freq": "cpu:freq",
        # GPU (uses GPU 0; multi-GPU selection is a separate concern)
        "gpu_temp": "gpu:0:temp",
        "gpu_usage": "gpu:0:usage",
        "gpu_power": "gpu:0:power",
        "gpu_clock": "gpu:0:clock",
        "gpu_fan": "gpu:0:fan",
        "gpu_vram_used": "gpu:0:vram_used",
        # Memory
        "mem_used": "memory:used",
        "mem_percent": "memory:percent",
        "mem_temp": "memory:temp",
        "mem_clock": "memory:clock",
        # Disk (first disk)
        "disk_temp": "disk:0:temp",
        "disk_read": "disk:0:read",
        "disk_write": "disk:0:write",
        "disk_activity": "disk:0:activity",
    }

    def __init__(self, readings: Mapping[str, SensorReading]) -> None:
        self._readings = readings

    def __getattr__(self, name: str) -> float:
        # __getattr__ only fires when normal lookup fails, so it's safe
        # to do dict work here without recursion concerns.
        sensor_id = self._LEGACY_TO_NEXT.get(name)
        if sensor_id is None:
            return 0.0
        if (reading := self._readings.get(sensor_id)) is None:
            return 0.0
        return float(reading.value)

    def __repr__(self) -> str:
        return f"LegacyMetricsView({len(self._readings)} readings)"


# ═══════════════════════════════════════════════════════════════════════
# Base class — encoding tables + helpers
# ═══════════════════════════════════════════════════════════════════════


class SegmentDisplay:
    """Base for LED segment display renderers.

    Subclasses declare layout data as class attributes (mask_size,
    phase_count, zone_led_map, digit indices) and implement compute_mask().
    """

    # ── 7-Segment encoding ──────────────────────────────────────────
    CHAR_7SEG: dict[str, set[str]] = {
        "0": {"a", "b", "c", "d", "e", "f"},
        "1": {"b", "c"},
        "2": {"a", "b", "d", "e", "g"},
        "3": {"a", "b", "c", "d", "g"},
        "4": {"b", "c", "f", "g"},
        "5": {"a", "c", "d", "f", "g"},
        "6": {"a", "c", "d", "e", "f", "g"},
        "7": {"a", "b", "c"},
        "8": {"a", "b", "c", "d", "e", "f", "g"},
        "9": {"a", "b", "c", "d", "f", "g"},
        " ": set(),
        "C": {"a", "d", "e", "f"},
        "F": {"a", "e", "f", "g"},
        "H": {"b", "c", "e", "f", "g"},
        "G": {"a", "b", "c", "d", "f", "g"},
    }
    WIRE_7SEG = ("a", "b", "c", "d", "e", "f", "g")

    # ── 13-Segment encoding (LF10) ─────────────────────────────────
    CHAR_13SEG: dict[str, set[str]] = {
        "0": {"a", "b", "c", "d", "e", "f", "h", "i", "j", "k", "l"},
        "1": {"c", "d", "e", "f", "g"},
        "2": {"a", "b", "c", "d", "e", "g", "h", "i", "j", "k", "m"},
        "3": {"a", "b", "c", "d", "e", "f", "g", "h", "i", "k", "m"},
        "4": {"a", "c", "d", "e", "f", "g", "k", "l", "m"},
        "5": {"a", "b", "c", "e", "f", "g", "h", "i", "k", "l", "m"},
        "6": {"a", "b", "c", "e", "f", "g", "h", "i", "j", "k", "l", "m"},
        "7": {"a", "b", "c", "d", "e", "f", "g"},
        "8": {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m"},
        "9": {"a", "b", "c", "d", "e", "f", "g", "h", "i", "k", "l", "m"},
        " ": set(),
    }
    WIRE_13SEG = ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m")

    # ── Subclass contract (enforced, not abstract) ──────────────────
    mask_size: int = 0
    phase_count: int = 0
    zone_led_map: tuple[tuple[int, ...], ...] | None = None
    # Per-zone metric source: (device, kind) per zone index.
    zone_metric_sources: tuple[tuple[str, str], ...] | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mask_size == 0 and "mask_size" not in cls.__dict__:
            return  # intermediate base (e.g. LF8Display before LF12)
        if not cls.mask_size:
            raise TypeError(f"{cls.__name__} must define mask_size > 0")

    def compute_mask(
        self,
        metrics: MetricsLike,
        phase: int = 0,
        temp_unit: str = "C",
        **kw: Any,
    ) -> list[bool]:
        raise NotImplementedError

    # ── Temperature conversion ──────────────────────────────────────

    @staticmethod
    def _to_display_temp(value: float, temp_unit: str) -> int:
        """Truncate pre-converted temperature to int for segment display."""
        return int(value)

    # ── Encoding helpers ────────────────────────────────────────────

    def _encode_7seg(
        self, ch: str, leds: tuple[int, ...], mask: list[bool],
    ) -> None:
        """Encode a single character into 7-segment LEDs."""
        segs = self.CHAR_7SEG.get(ch, set())
        for wi, seg in enumerate(self.WIRE_7SEG):
            if seg in segs:
                mask[leds[wi]] = True

    def _encode_digits(
        self,
        value: int,
        max_val: int,
        digit_count: int,
        digit_leds: tuple[tuple[int, ...], ...],
        mask: list[bool],
        suppress_leading_zeros: bool = True,
    ) -> None:
        """Encode N-digit value with optional leading-zero suppression."""
        v = max(0, min(max_val, value))
        chars: list[str] = []
        for i in range(digit_count - 1, -1, -1):
            d = (v // (10 ** i)) % 10
            chars.append(str(d))
        if suppress_leading_zeros:
            for i in range(digit_count - 1):
                if chars[i] == "0":
                    chars[i] = " "
                else:
                    break
        for idx, ch in enumerate(chars):
            self._encode_7seg(ch, digit_leds[idx], mask)

    def _encode_3digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...], mask: list[bool],
    ) -> None:
        self._encode_digits(value, 999, 3, digit_leds, mask)

    def _encode_4digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...], mask: list[bool],
    ) -> None:
        self._encode_digits(value, 9999, 4, digit_leds, mask)

    def _encode_5digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...], mask: list[bool],
    ) -> None:
        self._encode_digits(value, 99999, 5, digit_leds, mask)

    def _encode_2digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...], mask: list[bool],
    ) -> None:
        self._encode_digits(value, 99, 2, digit_leds, mask)

    def _encode_2digit_partial(
        self,
        value: int,
        digit_leds: tuple[tuple[int, ...], ...],
        partial_bc: tuple[int, int] | None,
        mask: list[bool],
    ) -> None:
        """Encode 0-199: 2 full digits + optional partial '1' for hundreds."""
        v = max(0, min(199, value))
        if v >= 100 and partial_bc:
            mask[partial_bc[0]] = True
            mask[partial_bc[1]] = True
            v -= 100
            self._encode_digits(
                v, 99, 2, digit_leds, mask, suppress_leading_zeros=False,
            )
        else:
            self._encode_2digit(v, digit_leds, mask)

    def _encode_unit(
        self, mode: int, digit_leds: tuple[int, ...], mask: list[bool],
    ) -> None:
        """Encode unit symbol: 0=C, -1=F, 1=MHz('H'), 2=GB('G')."""
        ch = {0: "C", -1: "F", 1: "H", 2: "G"}.get(mode, " ")
        self._encode_7seg(ch, digit_leds, mask)

    def _encode_clock_digit(
        self,
        value: int,
        digit_leds: tuple[int, ...],
        mask: list[bool],
        suppress_zero: bool = False,
    ) -> None:
        if suppress_zero and value == 0:
            return
        self._encode_7seg(str(value), digit_leds, mask)

    def _encode_3digit_13seg(
        self,
        value: int,
        digits_13: tuple[tuple[int, ...], ...],
        mask: list[bool],
    ) -> None:
        """Encode value with 13-segment encoding for 3 digits."""
        v = max(0, min(999, value))
        d_h, d_t, d_o = v // 100, (v % 100) // 10, v % 10
        for digit_val, leds, suppress in (
            (d_h, digits_13[0], True),
            (d_t, digits_13[1], d_h == 0),
            (d_o, digits_13[2], False),
        ):
            if suppress and digit_val == 0:
                continue
            segs = self.CHAR_13SEG.get(str(digit_val), set())
            for wi, seg in enumerate(self.WIRE_13SEG):
                if seg in segs:
                    mask[leds[wi]] = True


# ═══════════════════════════════════════════════════════════════════════
# Style 1 — AX120_DIGITAL (30 LEDs, 3 digits, 4-phase rotation)
# ═══════════════════════════════════════════════════════════════════════


class AX120Display(SegmentDisplay):
    mask_size = 30
    phase_count = 4
    ALWAYS_ON = (0, 1)
    CELSIUS = 6
    FAHRENHEIT = 7
    PERCENT = 8
    DIGITS: tuple[tuple[int, ...], ...] = (
        (9, 10, 11, 12, 13, 14, 15),
        (16, 17, 18, 19, 20, 21, 22),
        (23, 24, 25, 26, 27, 28, 29),
    )
    PHASES = (
        ("cpu_temp", (2, 3), True),
        ("cpu_percent", (2, 3), False),
        ("gpu_temp", (4, 5), True),
        ("gpu_usage", (4, 5), False),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 30
        for idx in self.ALWAYS_ON:
            mask[idx] = True
        metric_key, source_leds, is_temp = self.PHASES[phase % 4]
        for idx in source_leds:
            mask[idx] = True
        if is_temp:
            mask[self.FAHRENHEIT if temp_unit == "F" else self.CELSIUS] = True
        else:
            mask[self.PERCENT] = True
        value = int(getattr(metrics, metric_key, 0))
        if is_temp:
            value = self._to_display_temp(value, temp_unit)
        self._encode_3digit(value, self.DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 2 — PA120_DIGITAL (84 LEDs, simultaneous 4-value)
# ═══════════════════════════════════════════════════════════════════════


class PA120Display(SegmentDisplay):
    mask_size = 84
    phase_count = 1
    CPU1, CPU2 = 0, 1
    GPU1, GPU2 = 2, 3
    SSD, HSD = 4, 5
    BFB = 6
    SSD1, HSD1, BFB1 = 7, 8, 9
    CPU_TEMP_DIGITS: tuple[tuple[int, ...], ...] = (
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
    )
    CPU_USE_DIGITS: tuple[tuple[int, ...], ...] = (
        (31, 32, 33, 34, 35, 36, 37),
        (38, 39, 40, 41, 42, 43, 44),
    )
    CPU_USE_PARTIAL = (80, 81)
    GPU_TEMP_DIGITS: tuple[tuple[int, ...], ...] = (
        (45, 46, 47, 48, 49, 50, 51),
        (52, 53, 54, 55, 56, 57, 58),
        (59, 60, 61, 62, 63, 64, 65),
    )
    GPU_USE_DIGITS: tuple[tuple[int, ...], ...] = (
        (66, 67, 68, 69, 70, 71, 72),
        (73, 74, 75, 76, 77, 78, 79),
    )
    GPU_USE_PARTIAL = (82, 83)
    ZONE_LEDS: tuple[tuple[int, ...], ...] = (
        (CPU1, CPU2, SSD, HSD, *tuple(range(10, 31))),
        (BFB, *tuple(range(31, 45)), 80, 81),
        (GPU1, GPU2, SSD1, HSD1, *tuple(range(45, 66))),
        (BFB1, *tuple(range(66, 80)), 82, 83),
    )
    zone_led_map = ZONE_LEDS
    zone_metric_sources: tuple[tuple[str, str], ...] = (
        ("cpu", "temp"), ("cpu", "load"), ("gpu", "temp"), ("gpu", "load"),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 84
        for idx in (self.CPU1, self.CPU2, self.GPU1, self.GPU2, self.BFB, self.BFB1):
            mask[idx] = True
        if temp_unit == "C":
            mask[self.SSD] = mask[self.SSD1] = True
        else:
            mask[self.HSD] = mask[self.HSD1] = True
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, "cpu_temp", 0), temp_unit),
            self.CPU_TEMP_DIGITS, mask,
        )
        self._encode_2digit_partial(
            int(getattr(metrics, "cpu_percent", 0)),
            self.CPU_USE_DIGITS, self.CPU_USE_PARTIAL, mask,
        )
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, "gpu_temp", 0), temp_unit),
            self.GPU_TEMP_DIGITS, mask,
        )
        self._encode_2digit_partial(
            int(getattr(metrics, "gpu_usage", 0)),
            self.GPU_USE_DIGITS, self.GPU_USE_PARTIAL, mask,
        )
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 3 — AK120_DIGITAL (64 LEDs, 2-phase CPU/GPU)
# ═══════════════════════════════════════════════════════════════════════


class AK120Display(SegmentDisplay):
    mask_size = 64
    phase_count = 2
    CPU1, WATT, SSD, HSD, BFB, GPU1 = 0, 1, 2, 3, 4, 5
    WATT_DIGITS: tuple[tuple[int, ...], ...] = (
        (6, 7, 8, 9, 10, 11, 12),
        (13, 14, 15, 16, 17, 18, 19),
        (20, 21, 22, 23, 24, 25, 26),
    )
    TEMP_DIGITS: tuple[tuple[int, ...], ...] = (
        (27, 28, 29, 30, 31, 32, 33),
        (34, 35, 36, 37, 38, 39, 40),
        (41, 42, 43, 44, 45, 46, 47),
    )
    USE_DIGITS: tuple[tuple[int, ...], ...] = (
        (48, 49, 50, 51, 52, 53, 54),
        (55, 56, 57, 58, 59, 60, 61),
    )
    USE_PARTIAL = (62, 63)
    PHASES = (
        ("cpu_temp", "cpu_percent", "cpu_power", CPU1),
        ("gpu_temp", "gpu_usage", "gpu_power", GPU1),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 64
        mask[self.WATT] = mask[self.BFB] = True
        temp_key, use_key, watt_key, source_idx = self.PHASES[phase % 2]
        mask[source_idx] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True
        self._encode_3digit(
            int(getattr(metrics, watt_key, 0)), self.WATT_DIGITS, mask,
        )
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, temp_key, 0), temp_unit),
            self.TEMP_DIGITS, mask,
        )
        self._encode_2digit_partial(
            int(getattr(metrics, use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask,
        )
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 4 — LC1 (31 LEDs, mode-based 3-phase)
# ═══════════════════════════════════════════════════════════════════════


class LC1Display(SegmentDisplay):
    mask_size = 31
    phase_count = 3
    SSD, MTNO, GNO = 0, 1, 2
    DIGITS: tuple[tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
    )
    UNIT_DIGIT = (24, 25, 26, 27, 28, 29, 30)
    ALL_DIGITS: tuple[tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
    )
    PHASES_MEM = (
        ("mem_temp", 0, SSD),
        ("mem_clock", 1, MTNO),
        ("mem_used", 2, GNO),
    )
    PHASES_DISK = (
        ("disk_temp", 0, SSD),
        ("disk_read", 1, MTNO),
        ("disk_activity", 2, GNO),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 31
        sub_style = kw.get("sub_style", 0)
        memory_ratio = kw.get("memory_ratio", 2)
        phases = self.PHASES_DISK if sub_style == 1 else self.PHASES_MEM
        metric_key, mode, indicator_idx = phases[phase % 3]
        mask[indicator_idx] = True
        value = int(getattr(metrics, metric_key, 0))
        if mode == 0:
            self._encode_3digit(value, self.DIGITS, mask)
            self._encode_unit(
                -1 if temp_unit == "F" else 0, self.UNIT_DIGIT, mask,
            )
        else:
            if sub_style == 0 and mode == 1:
                value *= memory_ratio
            self._encode_4digit(value, self.ALL_DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 5/11 — LF8/LF15 (93 LEDs, 4-metric 2-phase CPU/GPU)
# ═══════════════════════════════════════════════════════════════════════


class LF8Display(SegmentDisplay):
    mask_size = 93
    phase_count = 2
    CPU1, GPU1, SSD, HSD, WATT, MHZ, BFB = 0, 1, 2, 3, 4, 5, 6
    TEMP_DIGITS: tuple[tuple[int, ...], ...] = (
        (7, 8, 9, 10, 11, 12, 13),
        (14, 15, 16, 17, 18, 19, 20),
        (21, 22, 23, 24, 25, 26, 27),
    )
    WATT_DIGITS: tuple[tuple[int, ...], ...] = (
        (28, 29, 30, 31, 32, 33, 34),
        (35, 36, 37, 38, 39, 40, 41),
        (42, 43, 44, 45, 46, 47, 48),
    )
    MHZ_DIGITS: tuple[tuple[int, ...], ...] = (
        (49, 50, 51, 52, 53, 54, 55),
        (56, 57, 58, 59, 60, 61, 62),
        (63, 64, 65, 66, 67, 68, 69),
        (70, 71, 72, 73, 74, 75, 76),
    )
    USE_DIGITS: tuple[tuple[int, ...], ...] = (
        (77, 78, 79, 80, 81, 82, 83),
        (84, 85, 86, 87, 88, 89, 90),
    )
    USE_PARTIAL = (91, 92)
    PHASES = (
        ("cpu_temp", "cpu_power", "cpu_freq", "cpu_percent", CPU1),
        ("gpu_temp", "gpu_power", "gpu_clock", "gpu_usage", GPU1),
    )

    def _compute_digits(
        self, metrics: MetricsLike, phase: int, temp_unit: str, mask: list[bool],
    ) -> None:
        """Shared digit computation for LF8 and LF12."""
        mask[self.WATT] = mask[self.MHZ] = mask[self.BFB] = True
        temp_key, watt_key, mhz_key, use_key, src = self.PHASES[phase % 2]
        mask[src] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, temp_key, 0), temp_unit),
            self.TEMP_DIGITS, mask,
        )
        self._encode_3digit(
            int(getattr(metrics, watt_key, 0)), self.WATT_DIGITS, mask,
        )
        self._encode_4digit(
            int(getattr(metrics, mhz_key, 0)), self.MHZ_DIGITS, mask,
        )
        self._encode_2digit_partial(
            int(getattr(metrics, use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask,
        )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * self.mask_size
        self._compute_digits(metrics, phase, temp_unit, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 6 — LF12 (124 LEDs = LF8 + 31 decoration)
# ═══════════════════════════════════════════════════════════════════════


class LF12Display(LF8Display):
    mask_size = 124
    DECORATION = tuple(range(93, 124))

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 124
        self._compute_digits(metrics, phase, temp_unit, mask)
        for idx in self.DECORATION:
            mask[idx] = True
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 7 — LF10 (116 LEDs, 13-segment, simultaneous CPU+GPU temp)
# ═══════════════════════════════════════════════════════════════════════


class LF10Display(SegmentDisplay):
    mask_size = 116
    phase_count = 1
    CPU1, SSD, HSD, GPU1, SSD1, HSD1 = 0, 1, 2, 3, 4, 5
    DIGIT_LEDS_13: tuple[tuple[int, ...], ...] = (
        tuple(range(6, 19)), tuple(range(19, 32)), tuple(range(32, 45)),
        tuple(range(45, 58)), tuple(range(58, 71)), tuple(range(71, 84)),
    )
    DECORATION = tuple(range(84, 116))
    ZONE_LEDS: tuple[tuple[int, ...], ...] = (
        (CPU1, SSD, HSD, *tuple(range(6, 45)), *tuple(range(84, 94))),
        (GPU1, SSD1, HSD1, *tuple(range(45, 84)), *tuple(range(94, 104))),
        tuple(range(104, 116)),
    )
    zone_led_map = ZONE_LEDS
    zone_metric_sources: tuple[tuple[str, str], ...] = (
        ("cpu", "temp"), ("gpu", "temp"), ("", ""),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 116
        mask[self.CPU1] = mask[self.GPU1] = True
        if temp_unit == "C":
            mask[self.SSD] = mask[self.SSD1] = True
        else:
            mask[self.HSD] = mask[self.HSD1] = True
        self._encode_3digit_13seg(
            self._to_display_temp(getattr(metrics, "cpu_temp", 0), temp_unit),
            self.DIGIT_LEDS_13[0:3], mask,
        )
        self._encode_3digit_13seg(
            self._to_display_temp(getattr(metrics, "gpu_temp", 0), temp_unit),
            self.DIGIT_LEDS_13[3:6], mask,
        )
        for idx in self.DECORATION:
            mask[idx] = True
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 8 — CZ1 (18 LEDs, 2 digits, 4-phase rotation)
# ═══════════════════════════════════════════════════════════════════════


class CZ1Display(SegmentDisplay):
    mask_size = 18
    phase_count = 4
    CPU1, GPU1, CPU2, GPU2 = 0, 1, 2, 3
    DIGITS: tuple[tuple[int, ...], ...] = (
        (4, 5, 6, 7, 8, 9, 10),
        (11, 12, 13, 14, 15, 16, 17),
    )
    PHASES = (
        ("cpu_temp", (CPU1,)),
        ("cpu_percent", (CPU2,)),
        ("gpu_temp", (GPU1,)),
        ("gpu_usage", (GPU2,)),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 18
        metric_key, indicator_on = self.PHASES[phase % 4]
        for idx in indicator_on:
            mask[idx] = True
        value = int(getattr(metrics, metric_key, 0))
        if "temp" in metric_key:
            value = self._to_display_temp(value, temp_unit)
        self._encode_2digit(value, self.DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 9 — LC2 (61 LEDs, clock display, 7 decoration)
# ═══════════════════════════════════════════════════════════════════════


class LC2Display(SegmentDisplay):
    mask_size = 61
    phase_count = 1
    COLON_AND_SEP = (0, 1, 2)
    DIGITS: tuple[tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
        (31, 32, 33, 34, 35, 36, 37),
        (38, 39, 40, 41, 42, 43, 44),
        (45, 46, 47, 48, 49, 50, 51),
    )
    MONTH_TENS_BC = (52, 53)
    DECORATION = tuple(range(54, 61))

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 61
        is_24h = kw.get("is_24h", True)
        week_sunday = kw.get("week_sunday", False)
        now = datetime.now()

        for idx in self.COLON_AND_SEP:
            mask[idx] = True

        hour = now.hour
        if not is_24h:
            hour = hour % 12 or 12

        self._encode_clock_digit(
            hour // 10, self.DIGITS[0], mask, suppress_zero=(not is_24h),
        )
        self._encode_clock_digit(hour % 10, self.DIGITS[1], mask)
        self._encode_clock_digit(now.minute // 10, self.DIGITS[2], mask)
        self._encode_clock_digit(now.minute % 10, self.DIGITS[3], mask)

        m_tens = now.month // 10
        if m_tens == 1:
            mask[self.MONTH_TENS_BC[0]] = True
            mask[self.MONTH_TENS_BC[1]] = True
        self._encode_clock_digit(now.month % 10, self.DIGITS[4], mask)
        self._encode_clock_digit(
            now.day // 10, self.DIGITS[5], mask, suppress_zero=True,
        )
        self._encode_clock_digit(now.day % 10, self.DIGITS[6], mask)

        py_wd = now.weekday()
        w = (py_wd + 1) % 7 if week_sunday else py_wd
        for i, idx in enumerate(self.DECORATION):
            mask[idx] = (i == 0) or (w > i - 1)

        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 10 — LF11 (38 LEDs, 4-phase sensor rotation)
# ═══════════════════════════════════════════════════════════════════════


class LF11Display(SegmentDisplay):
    mask_size = 38
    phase_count = 4
    SSD, BFB, MHZ_IND = 0, 1, 2
    DIGITS: tuple[tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
        (31, 32, 33, 34, 35, 36, 37),
    )
    PHASES = (
        ("disk_temp", 0),
        ("disk_activity", 1),
        ("disk_read", 2),
        ("disk_write", 2),
    )

    def compute_mask(
        self, metrics: MetricsLike, phase: int = 0, temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        mask = [False] * 38
        metric_key, mode = self.PHASES[phase % 4]
        value = int(getattr(metrics, metric_key, 0))
        if mode == 0:
            mask[self.SSD] = True
            value = self._to_display_temp(value, temp_unit)
            self._encode_3digit(value, self.DIGITS[0:3], mask)
            self._encode_unit(
                -1 if temp_unit == "F" else 0, self.DIGITS[3], mask,
            )
        elif mode == 1:
            mask[self.BFB] = True
            self._encode_5digit(value, self.DIGITS, mask)
        else:
            mask[self.MHZ_IND] = True
            self._encode_5digit(value, self.DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Display registry — style_id → SegmentDisplay instance
# ═══════════════════════════════════════════════════════════════════════


DISPLAYS: dict[LedStyle, SegmentDisplay] = {
    LedStyle.AX120: AX120Display(),
    LedStyle.PA120: PA120Display(),
    LedStyle.AK120: AK120Display(),
    LedStyle.LC1:   LC1Display(),
    LedStyle.LF8:   LF8Display(),
    LedStyle.LF12:  LF12Display(),
    LedStyle.LF10:  LF10Display(),
    LedStyle.CZ1:   CZ1Display(),
    LedStyle.LC2:   LC2Display(),
    LedStyle.LF11:  LF11Display(),
    LedStyle.LF15:  LF8Display(),   # LF15 = same layout as LF8
    # LedStyle.LF13 — pure RGB, no digit display
}


def compute_mask(
    style: LedStyle | None,
    metrics: MetricsLike,
    phase: int = 0,
    temp_unit: str = "C",
    is_24h: bool = True,
    week_sunday: bool = False,
) -> list[bool]:
    """Compute LED on/off mask for any supported style.

    Returns an empty list when ``style`` is None or has no segment
    display (e.g. LF13 is pure RGB).
    """
    if style is None:
        return []
    display = DISPLAYS.get(style)
    if display is None:
        return []
    return display.compute_mask(
        metrics, phase, temp_unit, is_24h=is_24h, week_sunday=week_sunday,
    )


def get_display(style: LedStyle | None) -> SegmentDisplay | None:
    """Get the SegmentDisplay instance for a style, or None."""
    if style is None:
        return None
    return DISPLAYS.get(style)


def has_segment_display(style: LedStyle | None) -> bool:
    """Whether this style has digit display support."""
    return style is not None and style in DISPLAYS


# ═══════════════════════════════════════════════════════════════════════
# Wire-order remap tables
# ═══════════════════════════════════════════════════════════════════════
#
# Each per-style table maps physical-wire index → logical color index.
# For physical LED ``i``, the color sent is ``logical_colors[table[i]]``.
# Byte-for-byte port of legacy ``core/models/led.py`` LED_REMAP_TABLES
# and LED_REMAP_SUB_TABLES — same indices, same lengths.
#
# Styles without a remap table (AX120, CZ1, LC2 in some variants) pass
# colors through unchanged.

_REMAP_STYLE_PA120: tuple[int, ...] = (
    1, 0, 15, 10, 11, 16, 14, 13, 12,
    22, 17, 18, 23, 21, 20, 19,
    29, 24, 25, 30, 28, 27, 26,
    4, 5, 81, 80,
    36, 31, 32, 37, 35, 34, 33,
    43, 38, 39, 44, 42, 41, 40,
    6, 9,
    75, 76, 77, 79, 74, 73, 78,
    68, 69, 70, 72, 67, 66, 71,
    82, 83, 7, 8,
    61, 62, 63, 65, 60, 59, 64,
    54, 55, 56, 58, 53, 52, 57,
    47, 48, 49, 51, 46, 45, 50,
    2, 3,
)

_REMAP_STYLE_AK120: tuple[int, ...] = (
    1, 22, 23, 24, 26, 21, 20, 25,
    14, 0, 13, 18, 19, 15, 16, 17,
    7, 6, 11, 12, 8, 9, 10,
    32, 27, 28, 33, 31, 30, 29,
    39, 34, 35, 40, 38, 37, 36,
    46, 41, 42, 47, 45, 44, 43,
    2, 3, 4,
    57, 58, 59, 61, 56, 55, 60,
    50, 5, 51, 52, 54, 49, 48, 53,
    62, 63,
)

_REMAP_STYLE_LC1: tuple[int, ...] = (
    2, 1, 26, 27, 28, 30, 25, 24, 0,
    29, 19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_LF8: tuple[int, ...] = (
    6, 86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    11, 10, 9, 13, 12, 7, 0, 8,
    18, 17, 16, 20, 19, 14, 1, 15,
    25, 24, 23, 27, 26, 21, 22,
    3, 2,
    32, 31, 30, 34, 33, 28, 29,
    39, 38, 37, 41, 40, 35, 36,
    46, 45, 44, 48, 47, 42, 43,
    4, 92,
)

_REMAP_STYLE_LF8_SUB1: tuple[int, ...] = (
    4,
    43, 42, 47, 48, 44, 45, 46,
    36, 35, 40, 41, 37, 38, 39,
    29, 28, 33, 34, 30, 31, 32,
    3, 2,
    22, 21, 26, 27, 23, 24, 25,
    15, 1, 14, 19, 20, 16, 17, 18,
    8, 0, 7, 12, 13, 9, 10, 11,
    54, 49, 50, 55, 53, 52, 51,
    61, 56, 57, 62, 60, 59, 58,
    68, 63, 64, 69, 67, 66, 65,
    75, 70, 71, 76, 74, 73, 72,
    5, 92, 91,
    82, 77, 78, 83, 81, 80, 79,
    89, 84, 85, 90, 88, 87, 86,
    6,
    96, 95, 94, 93,
    169, 168, 167, 166, 165, 164, 163, 162, 161, 160,
    159, 158, 157, 156, 155, 154, 153, 152, 151, 150,
    149, 148, 147, 146, 145, 144, 143, 142, 141, 140,
    139, 138, 137, 136, 135, 134, 133, 132, 131, 130,
    129, 128, 127, 126, 125, 124, 123, 122, 121, 120,
    119, 118, 117, 116, 115, 114, 113, 112, 111, 110,
    109, 108, 107, 106, 105, 104, 103, 102, 101, 100,
    99, 98, 97,
)

_REMAP_STYLE_LF12: tuple[int, ...] = (
    119, 120, 121, 122, 123,
    122, 121, 120, 119,
    6, 6,
    86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    92, 91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
    4,
    44, 45, 46, 48, 43, 42, 47,
    37, 38, 39, 41, 36, 35, 40,
    30, 31, 32, 34, 29, 28, 33,
    2, 3,
    23, 24, 25, 27, 22, 21, 1, 26,
    16, 17, 18, 20, 15, 14, 19,
    9, 10, 11, 13, 8, 7, 12,
    0,
    93, 93, 94, 94, 95, 95, 96, 96, 97, 97, 98, 98,
    99, 99, 100, 100, 101, 101, 102, 102, 103, 103, 104, 104,
)

_REMAP_STYLE_LF10: tuple[int, ...] = (
    115, 114, 113, 112, 111, 110,
    110, 111, 112, 113, 114, 115,
    103, 102, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85, 84,
    104, 105, 106, 107, 108, 109,
    0, 0,
    17, 6, 7, 7, 8, 9, 10, 18, 18, 16, 15, 14, 13, 13, 12, 11,
    28, 27, 26, 26, 25, 24, 23, 31, 31, 29, 30, 19, 20, 20, 21, 22,
    43, 32, 33, 33, 34, 35, 36, 44, 44, 42, 41, 40, 39, 39, 38, 37,
    2, 2, 1, 1, 3, 3,
    56, 45, 46, 46, 47, 48, 49, 57, 57, 55, 54, 53, 52, 52, 51, 50,
    67, 66, 65, 65, 64, 63, 62, 70, 70, 68, 69, 58, 59, 59, 60, 61,
    82, 71, 72, 72, 73, 74, 75, 83, 83, 81, 80, 79, 78, 78, 77, 76,
    5, 5, 4, 4,
)

_REMAP_STYLE_LC2: tuple[int, ...] = (
    60, 59, 58, 57, 56, 55, 54,
    53, 52,
    36, 31, 32, 37, 35, 34, 33,
    2, 2, 2,
    43, 38, 39, 44, 42, 41, 40,
    50, 45, 46, 51, 49, 48, 47,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    0, 1,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_LF11: tuple[int, ...] = (
    2, 1,
    33, 34, 35, 37, 32, 31, 0, 36,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)


LED_REMAP_TABLES: dict[LedStyle, tuple[int, ...]] = {
    LedStyle.PA120: _REMAP_STYLE_PA120,
    LedStyle.AK120: _REMAP_STYLE_AK120,
    LedStyle.LC1:   _REMAP_STYLE_LC1,
    LedStyle.LF8:   _REMAP_STYLE_LF8,
    LedStyle.LF12:  _REMAP_STYLE_LF12,
    LedStyle.LF10:  _REMAP_STYLE_LF10,
    LedStyle.LC2:   _REMAP_STYLE_LC2,
    LedStyle.LF11:  _REMAP_STYLE_LF11,
}

LED_REMAP_SUB_TABLES: dict[tuple[LedStyle, int], tuple[int, ...]] = {
    (LedStyle.LF8, 1): _REMAP_STYLE_LF8_SUB1,   # LF25 variant
}


def remap_led_colors(
    colors: list[tuple[int, int, int]],
    style: LedStyle | None,
    style_sub: int = 0,
) -> list[tuple[int, int, int]]:
    """Reorder a logical color array to the physical wire order.

    Pure data transform — given ``colors`` keyed by logical (UI) index,
    return a new list keyed by physical (wire) index, using the per-style
    remap table. Styles without a table (AX120, CZ1, unknown) are passed
    through unchanged — caller gets the same list back by identity.

    Sub-tables (e.g. LF25 = LF8 with sub=1) take precedence over the
    base style table when both exist.
    """
    if style is None:
        return colors
    table = LED_REMAP_SUB_TABLES.get((style, style_sub))
    if table is None:
        table = LED_REMAP_TABLES.get(style)
    if table is None:
        return colors
    black = (0, 0, 0)
    return [colors[idx] if idx < len(colors) else black for idx in table]


__all__ = [
    "DISPLAYS",
    "LED_REMAP_SUB_TABLES",
    "LED_REMAP_TABLES",
    "AK120Display",
    "AX120Display",
    "CZ1Display",
    "LC1Display",
    "LC2Display",
    "LF8Display",
    "LF10Display",
    "LF11Display",
    "LF12Display",
    "LegacyMetricsView",
    "MetricsLike",
    "PA120Display",
    "SegmentDisplay",
    "compute_mask",
    "get_display",
    "has_segment_display",
    "remap_led_colors",
]
