"""Unified segment display renderer for all LED device styles.

OOP architecture separating layout data (class attributes) from rendering
logic (methods).  Each style subclass holds its LED index mappings as data
and implements ``compute_mask()`` as business logic.

Class hierarchy::

    SegmentDisplay (ABC)     — encoding tables (data) + encoding methods (logic)
    ├── AX120Display         — style 1:  30 LEDs, 3-digit, 4-phase rotation
    ├── PA120Display         — style 2:  84 LEDs, 4 simultaneous values
    ├── AK120Display         — style 3:  69 LEDs, 2-phase CPU/GPU
    ├── LC1Display           — style 4:  38 LEDs, mode-based 3-phase
    ├── LF8Display           — style 5/11: 93 LEDs, 4-metric 2-phase
    │   └── LF12Display      — style 6:  124 LEDs = LF8 + 31 decoration
    ├── LF10Display          — style 7:  116 LEDs, 13-segment + decoration
    ├── CZ1Display           — style 8:  18 LEDs, 2-digit 4-phase
    ├── LC2Display           — style 9:  61 LEDs, clock display
    └── LF11Display          — style 10: 38 LEDs, 4-phase sensor

Reverse-engineered from UCScreenLED.cs (SetMyNumeral variants) and
FormLED.cs (GetVal rotation logic).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Base class — encoding tables (data) + encoding methods (logic)
# ═══════════════════════════════════════════════════════════════════════

class SegmentDisplay(ABC):
    """Base class for LED segment display renderers.

    **Data** lives in class attributes:
        - Encoding tables (``CHAR_7SEG``, ``CHAR_13SEG``, wire orders)
        - Subclass LED index constants (DIGITS, ALWAYS_ON, PHASES, etc.)

    **Logic** lives in methods:
        - Shared encoding helpers (``_encode_3digit``, ``_encode_2digit_partial``, etc.)
        - Subclass ``compute_mask()`` implementations
    """

    # ── 7-Segment encoding data ────────────────────────────────────

    CHAR_7SEG: Dict[str, Set[str]] = {
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

    # ── 13-Segment encoding data (style 7 / LF10) ─────────────────

    CHAR_13SEG: Dict[str, Set[str]] = {
        '0': set(),  # hundreds=0 → blank (leading zero suppression)
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

    # ── Abstract interface ─────────────────────────────────────────

    @property
    @abstractmethod
    def mask_size(self) -> int:
        """Total LED count for this style's mask."""

    @property
    @abstractmethod
    def phase_count(self) -> int:
        """Number of rotation phases."""

    @abstractmethod
    def compute_mask(
        self,
        metrics: Dict[str, Any],
        phase: int = 0,
        temp_unit: str = "C",
        **kw: Any,
    ) -> List[bool]:
        """Compute boolean LED mask from sensor metrics and rotation phase."""

    def phase_source(self, phase: int) -> str:
        """Return 'cpu', 'gpu', or 'other' for a given phase.

        Derived from the first metric key in PHASES. Styles without
        PHASES (single-phase or non-CPU/GPU) return 'other'.
        """
        phases = getattr(self, 'PHASES', None)
        if not phases or phase >= len(phases):
            return "other"
        first_key = phases[phase][0]
        if first_key.startswith("cpu"):
            return "cpu"
        if first_key.startswith("gpu"):
            return "gpu"
        return "other"

    # ── Shared encoding logic ──────────────────────────────────────

    def _encode_3digit(
        self,
        value: int,
        digit_leds: Tuple[Tuple[int, ...], ...],
        mask: List[bool],
    ) -> None:
        """Encode 0-999 into 3 seven-segment digits with leading-zero suppression."""
        v = max(0, min(999, value))
        d_h, d_t, d_o = v // 100, (v % 100) // 10, v % 10
        chars = [str(d_h), str(d_t), str(d_o)]
        if d_h == 0:
            chars[0] = ' '
            if d_t == 0:
                chars[1] = ' '
        for digit_idx, ch in enumerate(chars):
            segs = self.CHAR_7SEG.get(ch, set())
            leds = digit_leds[digit_idx]
            for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
                if seg_name in segs:
                    mask[leds[wire_idx]] = True

    def _encode_2digit_partial(
        self,
        value: int,
        digit_leds: Tuple[Tuple[int, ...], ...],
        partial_bc: Optional[Tuple[int, int]],
        mask: List[bool],
    ) -> None:
        """Encode 0-199 into 2 full digits + optional partial '1' for hundreds."""
        v = max(0, min(199, value))
        if v >= 100 and partial_bc:
            mask[partial_bc[0]] = True
            mask[partial_bc[1]] = True
            v -= 100
        d_t, d_o = v // 10, v % 10
        chars = [str(d_t) if d_t > 0 else ' ', str(d_o)]
        for digit_idx, ch in enumerate(chars):
            segs = self.CHAR_7SEG.get(ch, set())
            leds = digit_leds[digit_idx]
            for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
                if seg_name in segs:
                    mask[leds[wire_idx]] = True

    def _encode_unit(
        self,
        mode: int,
        digit_leds: Tuple[int, ...],
        mask: List[bool],
    ) -> None:
        """Encode unit symbol: 0=C, -1=F, 1=MHz('H'), 2=GB('G')."""
        ch = {0: 'C', -1: 'F', 1: 'H', 2: 'G'}.get(mode, ' ')
        segs = self.CHAR_7SEG.get(ch, set())
        for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
            if seg_name in segs:
                mask[digit_leds[wire_idx]] = True

    def _encode_2digit(
        self,
        value: int,
        digit_leds: Tuple[Tuple[int, ...], ...],
        mask: List[bool],
    ) -> None:
        """Encode 0-99 into 2 seven-segment digits."""
        v = max(0, min(99, value))
        d_t, d_o = v // 10, v % 10
        chars = [str(d_t) if d_t > 0 else ' ', str(d_o)]
        for digit_idx, ch in enumerate(chars):
            segs = self.CHAR_7SEG.get(ch, set())
            leds = digit_leds[digit_idx]
            for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
                if seg_name in segs:
                    mask[leds[wire_idx]] = True

    def _encode_clock_digit(
        self,
        value: int,
        digit_leds: Tuple[int, ...],
        mask: List[bool],
        suppress_zero: bool = False,
    ) -> None:
        """Encode single digit 0-9 for clock display."""
        if suppress_zero and value == 0:
            return
        segs = self.CHAR_7SEG.get(str(value), set())
        for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
            if seg_name in segs:
                mask[digit_leds[wire_idx]] = True

    def _encode_3digit_13seg(
        self,
        value: int,
        digits_13: Tuple[Tuple[int, ...], ...],
        mask: List[bool],
    ) -> None:
        """Encode value with 13-seg hundreds, 7-seg tens/ones."""
        v = max(0, min(999, value))
        d_h, d_t, d_o = v // 100, (v % 100) // 10, v % 10

        # Hundreds: 13-segment encoding
        segs_h = self.CHAR_13SEG.get(str(d_h), set())
        leds_h = digits_13[0]
        for wire_idx, seg_name in enumerate(self.WIRE_13SEG):
            if seg_name in segs_h:
                mask[leds_h[wire_idx]] = True

        # Tens: 7-segment with leading zero suppression
        if not (d_h == 0 and d_t == 0):
            segs_t = self.CHAR_7SEG.get(str(d_t), set())
            leds_t = digits_13[1]
            for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
                if seg_name in segs_t:
                    mask[leds_t[wire_idx]] = True

        # Ones: 7-segment (always shown)
        segs_o = self.CHAR_7SEG.get(str(d_o), set())
        leds_o = digits_13[2]
        for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
            if seg_name in segs_o:
                mask[leds_o[wire_idx]] = True


# ═══════════════════════════════════════════════════════════════════════
# Style 1 — AX120_DIGITAL (30 LEDs, 3 digits, 4-phase rotation)
# ═══════════════════════════════════════════════════════════════════════

class AX120Display(SegmentDisplay):
    """Style 1 — AX120 Digital: 30-LED, 3-digit, 4-phase sensor rotation.

    Layout: [always-on(2)][CPU(2)][GPU(2)][C][F][%][Digit1(7)][Digit2(7)][Digit3(7)]
    Phases: CPU temp -> CPU usage -> GPU temp -> GPU usage
    """

    # ── Layout data ────────────────────────────────────────────────
    ALWAYS_ON = (0, 1)
    CELSIUS = 6
    FAHRENHEIT = 7
    PERCENT = 8
    DIGITS: Tuple[Tuple[int, ...], ...] = (
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

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 30

    @property
    def phase_count(self) -> int:
        return 4

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
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

        self._encode_3digit(int(metrics.get(metric_key, 0)), self.DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 2 — PA120_DIGITAL (84 LEDs, simultaneous 4-value, remap)
# ═══════════════════════════════════════════════════════════════════════

class PA120Display(SegmentDisplay):
    """Style 2 — PA120 Digital: 84-LED, simultaneous CPU/GPU temp+usage.

    Shows all 4 metrics at once (no rotation).  Remapped style.
    Layout: [indicators(10)][cpuTemp 3d][cpuUse 2d+partial][gap][gpuTemp 3d][gpuUse 1d+partial+partial]
    """

    # ── Layout data ────────────────────────────────────────────────
    CPU1, CPU2, GPU1, GPU2 = 0, 1, 2, 3
    SSD, HSD = 4, 5         # °C, °F (CPU side)
    BFB = 6                  # % (CPU side)
    SSD1, HSD1 = 7, 8       # °C, °F (GPU side)
    BFB1 = 9                 # % (GPU side)

    CPU_TEMP_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
    )
    CPU_USE_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (31, 32, 33, 34, 35, 36, 37),
        (38, 39, 40, 41, 42, 43, 44),
    )
    CPU_USE_PARTIAL = (46, 47)

    GPU_TEMP_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (52, 53, 54, 55, 56, 57, 58),
        (59, 60, 61, 62, 63, 64, 65),
        (66, 67, 68, 69, 70, 71, 72),
    )
    GPU_USE_TENS: Tuple[Tuple[int, ...], ...] = (
        (73, 74, 75, 76, 77, 78, 79),
    )
    GPU_USE_ONES_BC = (80, 81)
    GPU_USE_PARTIAL = (82, 83)

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 84

    @property
    def phase_count(self) -> int:
        return 1

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 84

        # Always-on indicators
        for idx in (self.CPU1, self.CPU2, self.GPU1, self.GPU2,
                    self.BFB, self.BFB1):
            mask[idx] = True

        # Temperature unit indicators
        if temp_unit == "C":
            mask[self.SSD] = True
            mask[self.SSD1] = True
        else:
            mask[self.HSD] = True
            mask[self.HSD1] = True

        # All 4 metrics simultaneously
        self._encode_3digit(
            int(metrics.get('cpu_temp', 0)), self.CPU_TEMP_DIGITS, mask,
        )
        self._encode_2digit_partial(
            int(metrics.get('cpu_percent', 0)),
            self.CPU_USE_DIGITS, self.CPU_USE_PARTIAL, mask,
        )
        self._encode_3digit(
            int(metrics.get('gpu_temp', 0)), self.GPU_TEMP_DIGITS, mask,
        )

        # GPU usage: tens (full) + ones (B,C only) + hundreds (partial)
        gu = max(0, min(199, int(metrics.get('gpu_usage', 0))))
        if gu >= 100:
            mask[self.GPU_USE_PARTIAL[0]] = True
            mask[self.GPU_USE_PARTIAL[1]] = True
            gu -= 100
        d_t, d_o = gu // 10, gu % 10
        # Tens → full 7-seg digit
        segs_t = self.CHAR_7SEG.get(str(d_t) if d_t > 0 else ' ', set())
        for wire_idx, seg_name in enumerate(self.WIRE_7SEG):
            if seg_name in segs_t:
                mask[self.GPU_USE_TENS[0][wire_idx]] = True
        # Ones → partial (B, C segments only)
        segs_o = self.CHAR_7SEG.get(str(d_o), set())
        if 'b' in segs_o:
            mask[self.GPU_USE_ONES_BC[0]] = True
        if 'c' in segs_o:
            mask[self.GPU_USE_ONES_BC[1]] = True

        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 3 — AK120_DIGITAL (64 LEDs, 2-phase CPU/GPU, remap)
# ═══════════════════════════════════════════════════════════════════════

class AK120Display(SegmentDisplay):
    """Style 3 — AK120 Digital: 69-LED mask, 2-phase CPU/GPU rotation.

    Shows watt + temp + usage per phase (CPU or GPU).  Remapped style.
    """

    # ── Layout data ────────────────────────────────────────────────
    CPU1 = 0
    WATT = 1
    SSD = 2       # °C
    HSD = 3       # °F
    BFB = 4       # %
    GPU1 = 5

    WATT_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (6, 7, 8, 9, 10, 11, 12),
        (13, 14, 15, 16, 17, 18, 19),
        (20, 21, 22, 23, 24, 25, 26),
    )
    TEMP_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (27, 28, 29, 30, 31, 32, 33),
        (34, 35, 36, 37, 38, 39, 40),
        (41, 42, 43, 44, 45, 46, 47),
    )
    USE_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (48, 49, 50, 51, 52, 53, 54),
        (55, 56, 57, 58, 59, 60, 61),
    )
    USE_PARTIAL = (62, 63)

    PHASES = (
        ('cpu_temp', 'cpu_percent', 'cpu_watt', CPU1),
        ('gpu_temp', 'gpu_usage', 'gpu_watt', GPU1),
    )

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 69

    @property
    def phase_count(self) -> int:
        return 2

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 69

        mask[self.WATT] = True
        mask[self.BFB] = True

        temp_key, use_key, watt_key, source_idx = self.PHASES[phase % 2]
        mask[source_idx] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True

        self._encode_3digit(int(metrics.get(watt_key, 0)), self.WATT_DIGITS, mask)
        self._encode_3digit(int(metrics.get(temp_key, 0)), self.TEMP_DIGITS, mask)
        self._encode_2digit_partial(
            int(metrics.get(use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask,
        )
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 4 — LC1 (31 LEDs, mode-based 3-phase, remap)
# ═══════════════════════════════════════════════════════════════════════

class LC1Display(SegmentDisplay):
    """Style 4 — LC1: 38-LED mask, mode-based 3-phase (temp/MHz/GB).

    NVMe memory device — displays different metrics with unit symbol.
    Remapped style.
    """

    # ── Layout data ────────────────────────────────────────────────
    SSD = 0       # °C/°F indicator
    MTNO = 1      # MHz indicator
    GNO = 2       # GB indicator

    DIGITS: Tuple[Tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
    )
    UNIT_DIGIT = (24, 25, 26, 27, 28, 29, 30)

    PHASES = (
        ('mem_temp', 0, SSD),     # temperature
        ('mem_clock', 1, MTNO),   # MHz
        ('mem_used', 2, GNO),     # GB
    )

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 38

    @property
    def phase_count(self) -> int:
        return 3

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 38

        metric_key, mode, indicator_idx = self.PHASES[phase % 3]
        actual_mode = mode
        if mode == 0 and temp_unit == "F":
            actual_mode = -1

        mask[indicator_idx] = True

        value = int(metrics.get(metric_key, 0))
        if actual_mode == -1:
            value = value * 9 // 5 + 32
        self._encode_3digit(value, self.DIGITS, mask)
        self._encode_unit(actual_mode, self.UNIT_DIGIT, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 5/11 — LF8/LF15 (93 LEDs, 4-metric 2-phase CPU/GPU)
# ═══════════════════════════════════════════════════════════════════════

class LF8Display(SegmentDisplay):
    """Style 5/11 — LF8/LF15: 93-LED, 4-metric 2-phase CPU/GPU.

    Shows temp + watt + MHz + usage per phase (CPU or GPU).
    Style 11 (LF15) uses identical layout.
    """

    # ── Layout data ────────────────────────────────────────────────
    CPU1 = 0
    GPU1 = 1
    SSD = 2
    HSD = 3
    WATT = 4
    MHZ = 5
    BFB = 6

    TEMP_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (7, 8, 9, 10, 11, 12, 13),
        (14, 15, 16, 17, 18, 19, 20),
        (21, 22, 23, 24, 25, 26, 27),
    )
    WATT_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (28, 29, 30, 31, 32, 33, 34),
        (35, 36, 37, 38, 39, 40, 41),
        (42, 43, 44, 45, 46, 47, 48),
    )
    MHZ_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (49, 50, 51, 52, 53, 54, 55),
        (56, 57, 58, 59, 60, 61, 62),
        (63, 64, 65, 66, 67, 68, 69),
    )
    USE_DIGITS: Tuple[Tuple[int, ...], ...] = (
        (70, 71, 72, 73, 74, 75, 76),
        (77, 78, 79, 80, 81, 82, 83),
    )
    USE_PARTIAL = (91, 92)

    PHASES = (
        ('cpu_temp', 'cpu_watt', 'cpu_mhz', 'cpu_percent', CPU1),
        ('gpu_temp', 'gpu_watt', 'gpu_mhz', 'gpu_usage', GPU1),
    )

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 93

    @property
    def phase_count(self) -> int:
        return 2

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * self.mask_size

        mask[self.WATT] = True
        mask[self.MHZ] = True
        mask[self.BFB] = True

        temp_key, watt_key, mhz_key, use_key, src = self.PHASES[phase % 2]
        mask[src] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True

        self._encode_3digit(int(metrics.get(temp_key, 0)), self.TEMP_DIGITS, mask)
        self._encode_3digit(int(metrics.get(watt_key, 0)), self.WATT_DIGITS, mask)
        self._encode_3digit(int(metrics.get(mhz_key, 0)), self.MHZ_DIGITS, mask)
        self._encode_2digit_partial(
            int(metrics.get(use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask,
        )
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 6 — LF12 (124 LEDs = LF8 + 31 decoration)
# ═══════════════════════════════════════════════════════════════════════

class LF12Display(LF8Display):
    """Style 6 — LF12: 124-LED = LF8 digits + 31 always-on decoration LEDs."""

    # ── Additional layout data ─────────────────────────────────────
    DECORATION = tuple(range(93, 124))

    @property
    def mask_size(self) -> int:
        return 124

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        # Compute digit mask using parent's logic on a 124-element mask
        mask = [False] * 124

        mask[self.WATT] = True
        mask[self.MHZ] = True
        mask[self.BFB] = True

        temp_key, watt_key, mhz_key, use_key, src = self.PHASES[phase % 2]
        mask[src] = True
        mask[self.SSD if temp_unit == "C" else self.HSD] = True

        self._encode_3digit(int(metrics.get(temp_key, 0)), self.TEMP_DIGITS, mask)
        self._encode_3digit(int(metrics.get(watt_key, 0)), self.WATT_DIGITS, mask)
        self._encode_3digit(int(metrics.get(mhz_key, 0)), self.MHZ_DIGITS, mask)
        self._encode_2digit_partial(
            int(metrics.get(use_key, 0)), self.USE_DIGITS, self.USE_PARTIAL, mask,
        )

        # Decoration LEDs — always on
        for idx in self.DECORATION:
            mask[idx] = True
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 7 — LF10 (116 LEDs, 13-segment, simultaneous CPU+GPU temp)
# ═══════════════════════════════════════════════════════════════════════

class LF10Display(SegmentDisplay):
    """Style 7 — LF10: 116-LED, 13-segment CPU+GPU temp + 32 decoration.

    6 digits x 13 LEDs each.  Hundreds use 13-segment encoding,
    tens/ones use standard 7-segment.
    """

    # ── Layout data ────────────────────────────────────────────────
    CPU1 = 0
    SSD = 1       # °C (CPU)
    HSD = 2       # °F (CPU)
    GPU1 = 3
    SSD1 = 4      # °C (GPU)
    HSD1 = 5      # °F (GPU)

    DIGIT_LEDS_13: Tuple[Tuple[int, ...], ...] = (
        tuple(range(6, 19)),     # Digit 1: CPU temp hundreds (13 LEDs)
        tuple(range(19, 32)),    # Digit 2: CPU temp tens
        tuple(range(32, 45)),    # Digit 3: CPU temp ones
        tuple(range(45, 58)),    # Digit 4: GPU temp hundreds
        tuple(range(58, 71)),    # Digit 5: GPU temp tens
        tuple(range(71, 84)),    # Digit 6: GPU temp ones
    )

    DECORATION = tuple(range(84, 116))

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 116

    @property
    def phase_count(self) -> int:
        return 1

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 116

        mask[self.CPU1] = True
        mask[self.GPU1] = True

        if temp_unit == "C":
            mask[self.SSD] = True
            mask[self.SSD1] = True
        else:
            mask[self.HSD] = True
            mask[self.HSD1] = True

        # CPU temp (digits 1-3)
        self._encode_3digit_13seg(
            int(metrics.get('cpu_temp', 0)), self.DIGIT_LEDS_13[0:3], mask,
        )
        # GPU temp (digits 4-6)
        self._encode_3digit_13seg(
            int(metrics.get('gpu_temp', 0)), self.DIGIT_LEDS_13[3:6], mask,
        )

        for idx in self.DECORATION:
            mask[idx] = True
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 8 — CZ1 (18 LEDs, 2 digits, 4-phase rotation)
# ═══════════════════════════════════════════════════════════════════════

class CZ1Display(SegmentDisplay):
    """Style 8 — CZ1: 18-LED, 2-digit, 4-phase sensor rotation.

    Smallest digit display.  4 indicator LEDs + 2 x 7-segment digits.
    """

    # ── Layout data ────────────────────────────────────────────────
    CPU1 = 0
    GPU1 = 1
    CPU2 = 2
    GPU2 = 3

    DIGITS: Tuple[Tuple[int, ...], ...] = (
        (4, 5, 6, 7, 8, 9, 10),
        (11, 12, 13, 14, 15, 16, 17),
    )

    PHASES = (
        ('cpu_temp', (CPU1,)),
        ('cpu_percent', (CPU2,)),
        ('gpu_temp', (GPU1,)),
        ('gpu_usage', (GPU2,)),
    )

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 18

    @property
    def phase_count(self) -> int:
        return 4

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 18

        metric_key, indicator_on = self.PHASES[phase % 4]
        for idx in indicator_on:
            mask[idx] = True

        self._encode_2digit(int(metrics.get(metric_key, 0)), self.DIGITS, mask)
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 9 — LC2 (61 LEDs, clock display, 7 decoration)
# ═══════════════════════════════════════════════════════════════════════

class LC2Display(SegmentDisplay):
    """Style 9 — LC2: 61-LED clock display (HH:MM + MM/DD + decoration).

    Uses datetime instead of sensor metrics.  Indices 0-2 are reserved.
    """

    # ── Layout data ────────────────────────────────────────────────
    DIGITS: Tuple[Tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),        # Hour tens
        (10, 11, 12, 13, 14, 15, 16),  # Hour ones
        (17, 18, 19, 20, 21, 22, 23),  # Minute tens
        (24, 25, 26, 27, 28, 29, 30),  # Minute ones
        (31, 32, 33, 34, 35, 36, 37),  # Month tens
        (38, 39, 40, 41, 42, 43, 44),  # Month ones
        (45, 46, 47, 48, 49, 50, 51),  # Day tens
    )
    DAY_ONES_BC = (52, 53)
    DECORATION = tuple(range(54, 61))

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 61

    @property
    def phase_count(self) -> int:
        return 1

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 61

        is_24h = kw.get('is_24h', True)
        now = datetime.now()

        hour = now.hour
        if not is_24h:
            hour = hour % 12
            if hour == 0:
                hour = 12

        # HH:MM (suppress hour tens leading zero in 12h mode)
        self._encode_clock_digit(hour // 10, self.DIGITS[0], mask,
                                 suppress_zero=(not is_24h))
        self._encode_clock_digit(hour % 10, self.DIGITS[1], mask)
        self._encode_clock_digit(now.minute // 10, self.DIGITS[2], mask)
        self._encode_clock_digit(now.minute % 10, self.DIGITS[3], mask)

        # Month
        self._encode_clock_digit(now.month // 10, self.DIGITS[4], mask)
        self._encode_clock_digit(now.month % 10, self.DIGITS[5], mask)

        # Day: tens (full) + ones (partial B,C)
        self._encode_clock_digit(now.day // 10, self.DIGITS[6], mask)
        d_ones = now.day % 10
        segs = self.CHAR_7SEG.get(str(d_ones), set())
        if 'b' in segs:
            mask[self.DAY_ONES_BC[0]] = True
        if 'c' in segs:
            mask[self.DAY_ONES_BC[1]] = True

        for idx in self.DECORATION:
            mask[idx] = True
        return mask


# ═══════════════════════════════════════════════════════════════════════
# Style 10 — LF11 (38 LEDs, 4-phase sensor rotation)
# ═══════════════════════════════════════════════════════════════════════

class LF11Display(SegmentDisplay):
    """Style 10 — LF11: 38-LED, 4-phase sensor rotation with unit symbol.

    Similar to AX120 rotation but with 5 digit positions and unit display.
    """

    # ── Layout data ────────────────────────────────────────────────
    SSD = 0       # °C/°F
    BFB = 1       # %
    MHZ_IND = 2   # MHz

    DIGITS: Tuple[Tuple[int, ...], ...] = (
        (3, 4, 5, 6, 7, 8, 9),
        (10, 11, 12, 13, 14, 15, 16),
        (17, 18, 19, 20, 21, 22, 23),
        (24, 25, 26, 27, 28, 29, 30),
        (31, 32, 33, 34, 35, 36, 37),
    )

    PHASES = (
        ('cpu_temp', True),
        ('cpu_percent', False),
        ('gpu_temp', True),
        ('gpu_usage', False),
    )

    # ── Interface ──────────────────────────────────────────────────

    @property
    def mask_size(self) -> int:
        return 38

    @property
    def phase_count(self) -> int:
        return 4

    def compute_mask(self, metrics: Dict[str, Any], phase: int = 0,
                     temp_unit: str = "C", **kw: Any) -> List[bool]:
        mask = [False] * 38

        metric_key, is_temp = self.PHASES[phase % 4]

        if is_temp:
            mask[self.SSD] = True
        else:
            mask[self.BFB] = True

        value = int(metrics.get(metric_key, 0))
        self._encode_3digit(value, self.DIGITS[0:3], mask)

        if is_temp:
            mode = -1 if temp_unit == "F" else 0
            self._encode_unit(mode, self.DIGITS[3], mask)

        return mask


# ═══════════════════════════════════════════════════════════════════════
# Display registry — style_id → SegmentDisplay instance
# ═══════════════════════════════════════════════════════════════════════

DISPLAYS: Dict[int, SegmentDisplay] = {
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
    # 13: HR10 — handled by device_led_hr10.py
}


# ═══════════════════════════════════════════════════════════════════════
# Module-level convenience API
# ═══════════════════════════════════════════════════════════════════════

def compute_mask(
    style_id: int,
    metrics: Dict[str, Any],
    phase: int = 0,
    temp_unit: str = "C",
    is_24h: bool = True,
    week_sunday: bool = False,
) -> List[bool]:
    """Compute LED on/off mask for any supported style.

    Args:
        style_id: LED device style (1-11).
        metrics: Sensor readings dict (cpu_temp, cpu_percent, etc.).
        phase: Current rotation phase (0-based).
        temp_unit: "C" or "F".
        is_24h: 24-hour clock mode (style 9 only).
        week_sunday: Week starts on Sunday (style 9 only).

    Returns:
        Boolean mask in logical index space, or empty list if style
        has no digit display (12) or is handled elsewhere (13).
    """
    display = DISPLAYS.get(style_id)
    if display is None:
        return []
    return display.compute_mask(
        metrics, phase, temp_unit,
        is_24h=is_24h, week_sunday=week_sunday,
    )


def get_display(style_id: int) -> Optional[SegmentDisplay]:
    """Get the SegmentDisplay instance for a style, or None."""
    return DISPLAYS.get(style_id)


def has_segment_display(style_id: int) -> bool:
    """Whether this style has digit display support."""
    return style_id in DISPLAYS
