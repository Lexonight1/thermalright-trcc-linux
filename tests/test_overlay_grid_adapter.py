"""Grid ↔ next/ OverlayElement interchange — the colour/drag persist fix.

The legacy-style overlay grid used to emit the legacy keyed shape (nested
font, ``metric:"time"``, NO id), which ``SetOverlayConfig`` rejected — so
colour/font/drag edits never persisted.  And it mapped metrics by legacy
name (``cpu_temp``) while themes carry next/ ids (``cpu:temp``), so metric
elements were dropped from the editable grid (you couldn't drag them).
These lock both directions.
"""
from __future__ import annotations

from trcc.core.models import OverlayElement, OverlayElementConfig, OverlayMode
from trcc.services import _dc as Dc
from trcc.ui.gui._overlay_grid_adapter import configs_to_next_elements


def test_hardware_metric_accessors_round_trip() -> None:
    assert Dc.hardware_metric(0, 1) == ("cpu:temp", "{value:.0f}°C")
    assert Dc.hardware_metric(1, 2) == ("gpu:primary:usage", "{value:.0f}%")
    assert Dc.metric_to_hardware("cpu:temp") == (0, 1)
    assert Dc.metric_to_hardware("gpu:primary:usage") == (1, 2)
    assert Dc.hardware_metric(9, 9) is None
    assert Dc.metric_to_hardware("not:a:sensor") is None


def test_configs_convert_to_setoverlayconfig_ready_elements() -> None:
    """Every converted element carries an id + valid type → accepted, and
    colour/size/bold survive the conversion (the edit that 'did nothing')."""
    configs = [
        OverlayElementConfig(
            mode=OverlayMode.HARDWARE, main_count=0, sub_count=1,
            x=10, y=20, color="#ff8800", font_size=24, font_style=1,
        ),
        OverlayElementConfig(
            mode=OverlayMode.CUSTOM, text="HELLO", x=5, y=6, color="#abcdef",
        ),
        OverlayElementConfig(
            mode=OverlayMode.DATE, mode_sub=3, x=1, y=2, color="#ffffff",
        ),
    ]
    out = configs_to_next_elements(configs)

    # SetOverlayConfig.execute guards: every element needs a non-empty id and
    # a type in {text, metric, clock}.  Run each through the same from_dict.
    for d in out:
        el = OverlayElement.from_dict(d)
        assert el.id, f"element has no id (SetOverlayConfig would reject): {d}"
        assert el.type in ("text", "metric", "clock")

    metric, text, date = out
    # Metric: mapped to the next/ id (not dropped), colour + bold preserved.
    assert metric["type"] == "metric"
    assert metric["metric"] == "cpu:temp"
    assert metric["color"] == "#ff8800"
    assert metric["size"] == 24
    assert metric["bold"] is True
    # Custom text.
    assert text["type"] == "text"
    assert text["text"] == "HELLO"
    assert text["color"] == "#abcdef"
    # Clock date carries its format (mode_sub 3 → %m/%d).
    assert date["type"] == "clock"
    assert date["source"] == "date"
    assert date["format"] == "%m/%d"


def test_unmapped_hardware_is_skipped_not_crashed() -> None:
    configs = [
        OverlayElementConfig(mode=OverlayMode.HARDWARE,
                             main_count=9, sub_count=9, x=0, y=0),
    ]
    assert configs_to_next_elements(configs) == []
