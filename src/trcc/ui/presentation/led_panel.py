"""LED panel composition model — which sections a device's panel shows.

Toolkit-free (no Qt): the single source of "given an LED style, what sections
does its control panel show" — sensor gauges, the LC1 memory panel, the LF11
disk panel, the LC2 clock/week panel.  Both graphical front-ends (``ui/gui``,
``ui/qtgui``) render their own widgets from this one contract instead of each
re-deriving ``if style_id in (...)`` visibility inline.

The data is the C#'s, extracted from ``FormLEDInit`` by
``dev/tools/audit_csharp.py`` (the ``PANELS — LED composition`` dimension):
default shows the M1-M6 sensor gauges; LC1 (style 4) swaps them for the memory
panel; LF11 (style 10) swaps them for the disk panel; LC2 (style 9) adds the
clock/week panel.  ``tests/test_led_panel_model.py`` cross-checks this against
the audit parser whenever the decompile is present, so a vendor change surfaces
as a failing guard rather than silent drift.

Unit-testable with plain ``pytest`` — there is no toolkit here.
"""
from __future__ import annotations

from dataclasses import dataclass

# Legacy style ids (match ``core.led_models.LEGACY_STYLE_ID``):
_LC1 = 4    # memory panel  (C# FormLEDInit NO 128: hide gauges, show memory)
_LC2 = 9    # clock/week    (C# NO 112: gauges + week buttons)
_LF11 = 10  # disk panel    (C# NO 129: hide gauges, show disk)


@dataclass(frozen=True, slots=True)
class LedPanelModel:
    """What sections an LED control panel shows, for one style.

    ``show_sensor_gauges``: the M1-M6 CPU/GPU gauges (default; off for the
    memory/disk styles that reuse that space).
    ``show_memory_panel`` / ``show_disk_panel`` / ``show_clock_panel``: the
    LC1 / LF11 / LC2 device-specific sub-panels (mutually exclusive with each
    other; memory and disk also replace the gauges).
    """
    style_id: int
    show_sensor_gauges: bool
    show_memory_panel: bool
    show_disk_panel: bool
    show_clock_panel: bool


def led_panel_for(style_id: int) -> LedPanelModel:
    """Map a legacy LED ``style_id`` (1-12) to its panel composition.

    Mirrors the C# ``FormLEDInit`` section visibility (see module docstring).
    """
    return LedPanelModel(
        style_id=style_id,
        show_sensor_gauges=style_id not in (_LC1, _LF11),
        show_memory_panel=style_id == _LC1,
        show_disk_panel=style_id == _LF11,
        show_clock_panel=style_id == _LC2,
    )
