"""
HR10 2280 Pro Digital — 7-segment display renderer.

Converts text + unit into a 31-LED color array for the HR10 NVMe heatsink.
The mapping was validated by physical LED-by-LED probing (2026-02-08).

Original implementation by Lcstyle (GitHub PR #9).

Physical layout (left → right):
    [Digit4] [Digit3] [Digit2] [°] [Digit1] [MB/s] [%]

Wire order per digit: c, d, e, g, b, a, f
"""

from typing import Dict, List, Optional, Set, Tuple

LED_COUNT = 31

# 7-segment encoding: which segments are ON for each character
# Segment names: a=top, b=top-right, c=bottom-right, d=bottom,
#                e=bottom-left, f=top-left, g=middle
CHAR_SEGMENTS: Dict[str, Set[str]] = {
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
    '-': {'g'},
    ' ': set(),
    'A': {'a', 'b', 'c', 'e', 'f', 'g'},
    'b': {'c', 'd', 'e', 'f', 'g'},
    'C': {'a', 'd', 'e', 'f'},
    'F': {'a', 'e', 'f', 'g'},
    'H': {'b', 'c', 'e', 'f', 'g'},
    'L': {'d', 'e', 'f'},
    'P': {'a', 'b', 'e', 'f', 'g'},
    'E': {'a', 'd', 'e', 'f', 'g'},
    'r': {'e', 'g'},
    'n': {'c', 'e', 'g'},
    'o': {'c', 'd', 'e', 'g'},
    'S': {'a', 'c', 'd', 'f', 'g'},
}

# Segment wire order within each digit: index 0-6 maps to segment name
WIRE_ORDER = ('c', 'd', 'e', 'g', 'b', 'a', 'f')

# LED indices for each digit's 7 segments (in wire order)
# Digit 1 = rightmost, Digit 4 = leftmost on the physical display
DIGIT_LEDS = (
    (2, 3, 4, 5, 6, 7, 9),         # digit 1 (rightmost) — LED 8 (°) splits a,f
    (10, 11, 12, 13, 14, 15, 16),   # digit 2
    (17, 18, 19, 20, 21, 22, 23),   # digit 3
    (24, 25, 26, 27, 28, 29, 30),   # digit 4 (leftmost)
)

# Indicator LED indices
IND_MBS = 0   # MB/s
IND_PCT = 1   # %
IND_DEG = 8   # ° degree symbol


def render_display(
    text: str,
    color: Tuple[int, int, int] = (255, 255, 255),
    indicators: Optional[Set[str]] = None,
) -> List[Tuple[int, int, int]]:
    """Render text + indicators onto a 31-LED color array.

    Args:
        text: Up to 4 characters for digits (right-aligned).
              e.g. "116", "47", "1250"
        color: RGB tuple for lit segments.
        indicators: Set of indicator names to light: 'mbs', '%', 'deg'.

    Returns:
        List of 31 (R, G, B) tuples ready for LedPacketBuilder.
    """
    off = (0, 0, 0)
    colors: List[Tuple[int, int, int]] = [off] * LED_COUNT
    indicators = indicators or set()

    # Indicator LEDs
    if 'mbs' in indicators:
        colors[IND_MBS] = color
    if '%' in indicators:
        colors[IND_PCT] = color
    if 'deg' in indicators:
        colors[IND_DEG] = color

    # Right-align text across 4 digit positions
    # pos 0 → digit 4 (leftmost), pos 3 → digit 1 (rightmost)
    padded = text.rjust(4)[:4]

    for text_pos, ch in enumerate(padded):
        if ch == ' ':
            continue
        segments_on = CHAR_SEGMENTS.get(ch, set())
        digit_idx = 3 - text_pos  # pos 0→digit[3](left), pos 3→digit[0](right)
        led_indices = DIGIT_LEDS[digit_idx]
        for wire_idx, seg_name in enumerate(WIRE_ORDER):
            if seg_name in segments_on:
                colors[led_indices[wire_idx]] = color

    return colors


def render_metric(
    value: Optional[float],
    metric: str,
    color: Tuple[int, int, int] = (255, 255, 255),
    temp_unit: str = "F",
) -> List[Tuple[int, int, int]]:
    """Render a drive metric value for the HR10 display.

    Args:
        value: Metric value (None shows "---").
        metric: One of "temp", "activity", "read", "write".
        color: RGB tuple for lit segments.
        temp_unit: "C" or "F" for temperature display.

    Returns:
        List of 31 (R, G, B) tuples.
    """
    if value is None:
        return render_display("---", color, {'deg'} if metric == 'temp' else set())

    if metric == 'temp':
        if temp_unit == 'F':
            value = value * 9 / 5 + 32
        text = f"{value:.0f}{temp_unit}"
        return render_display(text, color, {'deg'})

    elif metric == 'activity':
        text = f"{value:.0f}"
        return render_display(text, color, {'%'})

    elif metric in ('read', 'write'):
        text = f"{value:.0f}"
        return render_display(text, color, {'mbs'})

    return render_display("---", color)


def apply_animation_colors(
    digit_mask: List[bool],
    animation_colors: List[Tuple[int, int, int]],
) -> List[Tuple[int, int, int]]:
    """Apply animated colors to only the ON segments of a digit mask.

    For effects like breathing/rainbow, the animation produces varying
    colors. This function applies those colors only where digits are
    lit, keeping OFF segments dark.

    Args:
        digit_mask: 31-element list of bools — True where a segment is ON.
        animation_colors: 31 RGB tuples from the animation engine.

    Returns:
        31 RGB tuples — animation color where mask is True, black elsewhere.
    """
    off = (0, 0, 0)
    return [
        animation_colors[i] if digit_mask[i] else off
        for i in range(LED_COUNT)
    ]


def get_digit_mask(
    text: str,
    indicators: Optional[Set[str]] = None,
) -> List[bool]:
    """Get a boolean mask of which LEDs should be ON for given text.

    Args:
        text: Up to 4 characters (right-aligned).
        indicators: Set of indicator names: 'mbs', '%', 'deg'.

    Returns:
        31-element list of bools.
    """
    colors = render_display(text, (255, 255, 255), indicators)
    return [c != (0, 0, 0) for c in colors]
