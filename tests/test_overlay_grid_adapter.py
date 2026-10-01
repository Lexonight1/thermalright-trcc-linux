"""Grid cell → App element fields — the colour/drag persist fix.

The legacy-style overlay grid used to emit the legacy keyed shape (nested
font, ``metric:"time"``, NO id), which the bus rejected — so colour/font/drag
edits never persisted.  And it mapped metrics by legacy name (``cpu_temp``)
while themes carry next/ ids (``cpu:temp``), so metric elements were dropped
from the editable grid (you couldn't drag them).  A cell now becomes the
fields ``AddOverlayElement`` takes; these lock that mapping.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import FakePlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import AddOverlayElement
from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.services import _dc as Dc
from trcc.ui.presentation.overlay_serialization import config_fields


def test_hardware_metric_accessors_round_trip() -> None:
    assert Dc.hardware_metric(0, 1) == ("cpu:temp", "{value:.0f}°C")
    assert Dc.hardware_metric(1, 2) == ("gpu:primary:usage", "{value:.0f}%")
    assert Dc.metric_to_hardware("cpu:temp") == (0, 1)
    assert Dc.metric_to_hardware("gpu:primary:usage") == (1, 2)
    assert Dc.hardware_metric(9, 9) is None
    assert Dc.metric_to_hardware("not:a:sensor") is None


def test_every_sensor_is_written_as_its_own_pair() -> None:
    """The reverse map is the canonical pairs, never an alias.

    The Fan-LCD sentinel ``(10000, 1)`` aliases ``fan:cpu`` for READING; when
    the reverse map inverted every pair it won, and every saved CPU-fan
    element was written as the cooler's own fan.
    """
    from trcc.core.models import METRICS

    for pair in METRICS:
        assert Dc.metric_to_hardware(METRICS[pair].sensor_id) == pair, pair
    assert Dc.metric_to_hardware("fan:cpu") == (5, 1)
    assert Dc.hardware_metric(10000, 1)[0] == "fan:cpu"      # still reads


def test_a_cpu_fan_element_is_saved_as_the_cpu_fan() -> None:
    mode, _, main, sub, _ = Dc._element_to_legacy(       # pyright: ignore[reportPrivateUsage]
        {"type": "metric", "metric": "fan:cpu"})
    assert (main, sub) == (5, 1)


def test_cells_convert_to_fields_the_app_accepts(tmp_path: Path) -> None:
    """Every converted cell is accepted by the real ``AddOverlayElement``, and
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
    out = [config_fields(c) for c in configs]
    assert None not in out

    app = App(platform=FakePlatform(tmp_path))
    app.set_renderer(QtRenderer())
    for fields in out:
        assert fields is not None
        result = app.dispatch(AddOverlayElement(key="0402:3922", **fields))
        assert result.ok, f"the App refused {fields}: {result.message}"

    metric, text, date = (f for f in out if f is not None)
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
    assert [config_fields(c) for c in configs] == [None]
