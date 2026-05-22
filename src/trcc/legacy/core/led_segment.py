"""Unified segment display renderer for all LED device styles.

Each style class holds LED index mappings as class-level data and
implements ``compute_mask()`` for its rendering logic.  Encoding
tables and helpers live on the base class.

Class hierarchy::

    SegmentDisplay       — encoding tables (data) + encoding methods (logic)
    ├── AX120Display     — style 1:  30 LEDs, 3-digit, 4-phase rotation
    ├── PA120Display     — style 2:  84 LEDs, 4 simultaneous values
    ├── AK120Display     — style 3:  64 LEDs, 2-phase CPU/GPU
    ├── LC1Display       — style 4:  31 LEDs, mode-based 3-phase
    ├── LF8Display       — style 5/11: 93 LEDs, 4-metric 2-phase
    │   └── LF12Display  — style 6:  124 LEDs = LF8 + 31 decoration
    ├── LF10Display      — style 7:  116 LEDs, 13-segment + decoration
    ├── CZ1Display       — style 8:  18 LEDs, 2-digit 4-phase
    ├── LC2Display       — style 9:  61 LEDs, clock display
    └── LF11Display      — style 10: 38 LEDs, 4-phase sensor
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import HardwareMetrics

log = logging.getLogger(__name__)


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
        '0': {'a', 'b', 'c', 'd', 'e', 'f'},
        '1': {'b', 'c'},
        '2': {'a', 'b', 'd', 'e', 'g'},
        '3': {'a', 'b', 'c', 'd', 'g'},
        '4': {'b', 'c', 'f', 'g'},
        '5': {'a', 'c', 'd', 'f', 'g'},
        '6': {'a', 'c', 'd', 'e', 'f', 'g'},
        '7': {'a', 'b', 'c'},
        '8': {'a', 'b', 'c', 'd', 'e', 'f', 'g'},
        '9': {'a', 'b', 'c', 'd', 'f', 'g'},
        ' ': set(),
        'C': {'a', 'd', 'e', 'f'},
        'F': {'a', 'e', 'f', 'g'},
        'H': {'b', 'c', 'e', 'f', 'g'},
        'G': {'a', 'b', 'c', 'd', 'f', 'g'},
    }
    WIRE_7SEG = ('a', 'b', 'c', 'd', 'e', 'f', 'g')

    # ── 13-Segment encoding (LF10) ─────────────────────────────────
    CHAR_13SEG: dict[str, set[str]] = {
        '0': {'a', 'b', 'c', 'd', 'e', 'f', 'h', 'i', 'j', 'k', 'l'},
        '1': {'c', 'd', 'e', 'f', 'g'},
        '2': {'a', 'b', 'c', 'd', 'e', 'g', 'h', 'i', 'j', 'k', 'm'},
        '3': {'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'k', 'm'},
        '4': {'a', 'c', 'd', 'e', 'f', 'g', 'k', 'l', 'm'},
        '5': {'a', 'b', 'c', 'e', 'f', 'g', 'h', 'i', 'k', 'l', 'm'},
        '6': {'a', 'b', 'c', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm'},
        '7': {'a', 'b', 'c', 'd', 'e', 'f', 'g'},
        '8': {'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm'},
        '9': {'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'k', 'l', 'm'},
        ' ': set(),
    }
    WIRE_13SEG = ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm')

    # ── Subclass contract (enforced, not abstract) ──────────────────
    mask_size: int = 0
    phase_count: int = 0
    zone_led_map: tuple[tuple[int, ...], ...] | None = None
    # Per-zone metric source: (device, kind) per zone index.
    # e.g. PA120: zone 0=("cpu","temp"), zone 1=("cpu","load"), ...
    # None = use global temp_source/load_source (single-zone devices).
    zone_metric_sources: tuple[tuple[str, str], ...] | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mask_size == 0 and 'mask_size' not in cls.__dict__:
            return  # intermediate base (e.g. LF8Display before LF12)
        if not cls.mask_size:
            raise TypeError(f"{cls.__name__} must define mask_size > 0")

    def compute_mask(
        self, metrics: HardwareMetrics, phase: int = 0,
        temp_unit: str = "C", **kw: Any,
    ) -> list[bool]:
        raise NotImplementedError

    # ── Temperature conversion ──────────────────────────────────────

    @staticmethod
    def _to_display_temp(value: float, temp_unit: str) -> int:
        """Truncate pre-converted temperature to int for segment display.

        Metrics are already converted to the target unit by
        HardwareMetrics.with_temp_unit — no conversion here.
        """
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
        self, value: int, max_val: int, digit_count: int,
        digit_leds: tuple[tuple[int, ...], ...], mask: list[bool],
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
                if chars[i] == '0':
                    chars[i] = ' '
                else:
                    break
        for idx, ch in enumerate(chars):
            self._encode_7seg(ch, digit_leds[idx], mask)

    def _encode_3digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...],
        mask: list[bool],
    ) -> None:
        self._encode_digits(value, 999, 3, digit_leds, mask)

    def _encode_4digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...],
        mask: list[bool],
    ) -> None:
        self._encode_digits(value, 9999, 4, digit_leds, mask)

    def _encode_5digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...],
        mask: list[bool],
    ) -> None:
        self._encode_digits(value, 99999, 5, digit_leds, mask)

    def _encode_2digit(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...],
        mask: list[bool],
    ) -> None:
        self._encode_digits(value, 99, 2, digit_leds, mask)

    def _encode_2digit_partial(
        self, value: int, digit_leds: tuple[tuple[int, ...], ...],
        partial_bc: tuple[int, int] | None, mask: list[bool],
    ) -> None:
        """Encode 0-199: 2 full digits + optional partial '1' for hundreds."""
        v = max(0, min(199, value))
        if v >= 100 and partial_bc:
            mask[partial_bc[0]] = True
            mask[partial_bc[1]] = True
            v -= 100
            # Hundreds present — don't suppress tens zero (100 → "00" not " 0")
            self._encode_digits(v, 99, 2, digit_leds, mask,
                                suppress_leading_zeros=False)
        else:
            self._encode_2digit(v, digit_leds, mask)

    def _encode_unit(
        self, mode: int, digit_leds: tuple[int, ...], mask: list[bool],
    ) -> None:
        """Encode unit symbol: 0=C, -1=F, 1=MHz('H'), 2=GB('G')."""
        ch = {0: 'C', -1: 'F', 1: 'H', 2: 'G'}.get(mode, ' ')
        self._encode_7seg(ch, digit_leds, mask)

    def _encode_clock_digit(
        self, value: int, digit_leds: tuple[int, ...],
        mask: list[bool], suppress_zero: bool = False,
    ) -> None:
        if suppress_zero and value == 0:
            return
        self._encode_7seg(str(value), digit_leds, mask)

    def _encode_3digit_13seg(
        self, value: int, digits_13: tuple[tuple[int, ...], ...],
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
        ('cpu_temp', (2, 3), True),
        ('cpu_percent', (2, 3), False),
        ('gpu_temp', (4, 5), True),
        ('gpu_usage', (4, 5), False),
    )

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
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
# Style 2 — PA120_DIGITAL (84 LEDs, simultaneous 4-value, remap)
# ═══════════════════════════════════════════════════════════════════════

class PA120Display(SegmentDisplay):
    mask_size = 84
    phase_count = 1
    # C# UCScreenLED: Cpu1=0, Cpu2=1, Gpu1=2, Gpu2=3,
    # SSD=4(°C), HSD=5(°F), BFB=6(%), SSD1=7, HSD1=8, BFB1=9
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

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 84
        for idx in (self.CPU1, self.CPU2, self.GPU1, self.GPU2,
                    self.BFB, self.BFB1):
            mask[idx] = True
        if temp_unit == "C":
            mask[self.SSD] = mask[self.SSD1] = True
        else:
            mask[self.HSD] = mask[self.HSD1] = True
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, 'cpu_temp', 0), temp_unit),
            self.CPU_TEMP_DIGITS, mask)
        self._encode_2digit_partial(
            int(getattr(metrics, 'cpu_percent', 0)),
            self.CPU_USE_DIGITS, self.CPU_USE_PARTIAL, mask)
        self._encode_3digit(
            self._to_display_temp(getattr(metrics, 'gpu_temp', 0), temp_unit),
            self.GPU_TEMP_DIGITS, mask)
        self._encode_2digit_partial(
            int(getattr(metrics, 'gpu_usage', 0)),
            self.GPU_USE_DIGITS, self.GPU_USE_PARTIAL, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 3 — AK120_DIGITAL (64 LEDs, 2-phase CPU/GPU, remap)
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
        ('cpu_temp', 'cpu_percent', 'cpu_power', CPU1),
        ('gpu_temp', 'gpu_usage', 'gpu_power', GPU1),
    )

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 64
        mask[self.WATT] = mask[self.BFB] = True
        temp_key, use_key, watt_key, source_idx = self.PHASES[phase % 2]
        mask[source_idx] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True
        self._encode_3digit(int(getattr(metrics, watt_key, 0)), self.WATT_DIGITS, mask)
        self._encode_3digit(self._to_display_temp(getattr(metrics, temp_key, 0), temp_unit), self.TEMP_DIGITS, mask)
        self._encode_2digit_partial(
            int(getattr(metrics, use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 4 — LC1 (31 LEDs, mode-based 3-phase, remap)
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
        ('mem_temp', 0, SSD),
        ('mem_clock', 1, MTNO),
        ('mem_used', 2, GNO),
    )
    PHASES_DISK = (
        ('disk_temp', 0, SSD),
        ('disk_read', 1, MTNO),
        ('disk_activity', 2, GNO),
    )

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 31
        sub_style = kw.get('sub_style', 0)
        memory_ratio = kw.get('memory_ratio', 2)
        phases = self.PHASES_DISK if sub_style == 1 else self.PHASES_MEM
        metric_key, mode, indicator_idx = phases[phase % 3]
        mask[indicator_idx] = True
        value = int(getattr(metrics, metric_key, 0))
        if mode == 0:
            self._encode_3digit(value, self.DIGITS, mask)
            self._encode_unit(-1 if temp_unit == "F" else 0, self.UNIT_DIGIT, mask)
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
        ('cpu_temp', 'cpu_power', 'cpu_freq', 'cpu_percent', CPU1),
        ('gpu_temp', 'gpu_power', 'gpu_clock', 'gpu_usage', GPU1),
    )

    def _compute_digits(self, metrics: HardwareMetrics, phase: int,
                        temp_unit: str, mask: list[bool]) -> None:
        """Shared digit computation for LF8 and LF12."""
        mask[self.WATT] = mask[self.MHZ] = mask[self.BFB] = True
        temp_key, watt_key, mhz_key, use_key, src = self.PHASES[phase % 2]
        mask[src] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True
        self._encode_3digit(self._to_display_temp(getattr(metrics, temp_key, 0), temp_unit), self.TEMP_DIGITS, mask)
        self._encode_3digit(int(getattr(metrics, watt_key, 0)), self.WATT_DIGITS, mask)
        self._encode_4digit(int(getattr(metrics, mhz_key, 0)), self.MHZ_DIGITS, mask)
        self._encode_2digit_partial(
            int(getattr(metrics, use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask)

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * self.mask_size
        self._compute_digits(metrics, phase, temp_unit, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 6 — LF12 (124 LEDs = LF8 + 31 decoration)
# ═══════════════════════════════════════════════════════════════════════

class LF12Display(LF8Display):
    mask_size = 124
    DECORATION = tuple(range(93, 124))

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
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

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 116
        mask[self.CPU1] = mask[self.GPU1] = True
        if temp_unit == "C":
            mask[self.SSD] = mask[self.SSD1] = True
        else:
            mask[self.HSD] = mask[self.HSD1] = True
        self._encode_3digit_13seg(
            self._to_display_temp(getattr(metrics, 'cpu_temp', 0), temp_unit),
            self.DIGIT_LEDS_13[0:3], mask)
        self._encode_3digit_13seg(
            self._to_display_temp(getattr(metrics, 'gpu_temp', 0), temp_unit),
            self.DIGIT_LEDS_13[3:6], mask)
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
        ('cpu_temp', (CPU1,)),
        ('cpu_percent', (CPU2,)),
        ('gpu_temp', (GPU1,)),
        ('gpu_usage', (GPU2,)),
    )

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 18
        metric_key, indicator_on = self.PHASES[phase % 4]
        for idx in indicator_on:
            mask[idx] = True
        value = int(getattr(metrics, metric_key, 0))
        if 'temp' in metric_key:
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

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 61
        is_24h = kw.get('is_24h', True)
        week_sunday = kw.get('week_sunday', False)
        now = datetime.now()

        for idx in self.COLON_AND_SEP:
            mask[idx] = True

        hour = now.hour
        if not is_24h:
            hour = hour % 12 or 12

        self._encode_clock_digit(hour // 10, self.DIGITS[0], mask,
                                 suppress_zero=(not is_24h))
        self._encode_clock_digit(hour % 10, self.DIGITS[1], mask)
        self._encode_clock_digit(now.minute // 10, self.DIGITS[2], mask)
        self._encode_clock_digit(now.minute % 10, self.DIGITS[3], mask)

        m_tens = now.month // 10
        if m_tens == 1:
            mask[self.MONTH_TENS_BC[0]] = True
            mask[self.MONTH_TENS_BC[1]] = True
        self._encode_clock_digit(now.month % 10, self.DIGITS[4], mask)
        self._encode_clock_digit(now.day // 10, self.DIGITS[5], mask,
                                 suppress_zero=True)
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
        ('disk_temp', 0),
        ('disk_activity', 1),
        ('disk_read', 2),
        ('disk_write', 2),
    )

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> list[bool]:
        mask = [False] * 38
        metric_key, mode = self.PHASES[phase % 4]
        value = int(getattr(metrics, metric_key, 0))
        if mode == 0:
            mask[self.SSD] = True
            value = self._to_display_temp(value, temp_unit)
            self._encode_3digit(value, self.DIGITS[0:3], mask)
            self._encode_unit(-1 if temp_unit == "F" else 0, self.DIGITS[3], mask)
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

DISPLAYS: dict[int, SegmentDisplay] = {
    1: AX120Display(),
    2: PA120Display(),
    3: AK120Display(),
    4: LC1Display(),
    5: LF8Display(),
    6: LF12Display(),
    7: LF10Display(),
    8: CZ1Display(),
    9: LC2Display(),
    10: LF11Display(),
    11: LF8Display(),   # LF15 = same layout as LF8
    # 12: LF13 — pure RGB, no digit display
}


def compute_mask(
    style_id: int, metrics: HardwareMetrics, phase: int = 0,
    temp_unit: str = "C", is_24h: bool = True, week_sunday: bool = False,
) -> list[bool]:
    """Compute LED on/off mask for any supported style."""
    display = DISPLAYS.get(style_id)
    if display is None:
        return []
    return display.compute_mask(
        metrics, phase, temp_unit, is_24h=is_24h, week_sunday=week_sunday)


def get_display(style_id: int) -> SegmentDisplay | None:
    """Get the SegmentDisplay instance for a style, or None."""
    return DISPLAYS.get(style_id)


def has_segment_display(style_id: int) -> bool:
    """Whether this style has digit display support."""
    return style_id in DISPLAYS
