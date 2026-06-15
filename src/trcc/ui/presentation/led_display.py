"""LED display-selector model — what button1-4 mean for each LED style.

Toolkit-free (no Qt): the single source of "given an LED style, what does the
control panel's selector row (the C# ``button1-4`` / ``buttonN1-4`` row) do".
The original Windows app overloads that one button row per device family
(verified against the 2.1.6 decompile):

* **PAGE** — the device has *one* small numeric display; the buttons pick which
  metric it shows (radio when carousel is off, rotate when on).  The color is
  global.  Styles: AX120(1), AK120(3), LC1(4), LF8(5), LF12(6), CZ1(8),
  LF11(10), LF15(11).
* **ZONE** — the device has independently-colored RGB zones; the buttons pick
  which zone the color wheel edits, and the display shows every metric at once.
  This is the ONLY case the C# ``ucColor1Delegate`` writes per-zone color
  (gated ``if nowLedStyle == 2 || nowLedStyle == 7``).  Styles: PA120(2),
  LF10(7) — exactly the two ``led_segment`` displays that carry a
  ``zone_led_map``.
* **NONE** — no selector: LC2(9) is a clock, LF13(12) is a solid-color panel.

The cutover modelled *every* multi-button style as "zones", which is why the
metric-page styles wrongly offered per-zone colour and why picking a metric
wasn't discoverable.  This model restores the C# distinction; both graphical
front-ends render their selector row from it.

``tests/test_led_display_model.py`` cross-checks the page counts against the
``led_segment`` render (``phase_count`` / ``zone_led_map``) and the ZONE set
against the decompile, so a drift surfaces as a failing guard.

Unit-testable with plain ``pytest`` — there is no toolkit here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LedSelector(Enum):
    """What the panel's button row selects for a given LED style."""
    NONE = "none"   # no selector (clock / solid-colour panel)
    PAGE = "page"   # which metric the single numeric display shows
    ZONE = "zone"   # which RGB zone the colour wheel edits


@dataclass(frozen=True, slots=True)
class LedDisplayModel:
    """The selector behaviour for one LED style.

    ``selector``: PAGE / ZONE / NONE (see module docstring).
    ``slot_count``: number of metric pages (PAGE) or RGB zones (ZONE); 0 for
    NONE.
    ``page_labels``: human label per metric page (PAGE only; empty otherwise) —
    drives the selector-button tooltips/labels in the UI.
    """
    style_id: int
    selector: LedSelector
    slot_count: int
    page_labels: tuple[str, ...]


# Legacy style id (LEGACY_STYLE_ID) → display-selector definition.  Page labels
# follow the C# GetVal slot order (the metric fed into each LunBo page).
_DISPLAY: dict[int, LedDisplayModel] = {
    1:  LedDisplayModel(1, LedSelector.PAGE, 4,
                        ("CPU Temp", "CPU %", "GPU Temp", "GPU %")),       # AX120
    2:  LedDisplayModel(2, LedSelector.ZONE, 4, ()),                       # PA120
    3:  LedDisplayModel(3, LedSelector.PAGE, 2, ("CPU", "GPU")),           # AK120
    4:  LedDisplayModel(4, LedSelector.PAGE, 3,
                        ("Memory Temp", "Memory Clock", "Memory Used")),   # LC1
    5:  LedDisplayModel(5, LedSelector.PAGE, 2, ("CPU", "GPU")),           # LF8
    6:  LedDisplayModel(6, LedSelector.PAGE, 2, ("CPU", "GPU")),           # LF12
    7:  LedDisplayModel(7, LedSelector.ZONE, 3, ()),                       # LF10
    8:  LedDisplayModel(8, LedSelector.PAGE, 4,
                        ("CPU Temp", "CPU %", "GPU Temp", "GPU %")),       # CZ1
    9:  LedDisplayModel(9, LedSelector.NONE, 0, ()),                       # LC2 (clock)
    10: LedDisplayModel(10, LedSelector.PAGE, 4,
                        ("Disk Temp", "Disk Use", "Disk Read", "Disk Write")),  # LF11
    11: LedDisplayModel(11, LedSelector.PAGE, 2, ("CPU", "GPU")),          # LF15
    12: LedDisplayModel(12, LedSelector.NONE, 0, ()),                      # LF13 (solid)
}

_DEFAULT = LedDisplayModel(0, LedSelector.NONE, 0, ())


def led_display_for(style_id: int) -> LedDisplayModel:
    """Map a legacy LED ``style_id`` (1-12) to its display-selector behaviour.

    Unknown ids fall back to NONE (no selector).
    """
    return _DISPLAY.get(style_id, _DEFAULT)
