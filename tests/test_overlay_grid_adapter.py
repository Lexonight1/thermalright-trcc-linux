"""Grid cell → App element fields — the colour/drag persist fix.

The legacy-style overlay grid used to emit the legacy keyed shape (nested
font, ``metric:"time"``, NO id), which the bus rejected — so colour/font/drag
edits never persisted.  And it mapped metrics by legacy name (``cpu_temp``)
while themes carry next/ ids (``cpu:temp``), so metric elements were dropped
from the editable grid (you couldn't drag them).  A cell now becomes the
fields ``AddOverlayElement`` takes; these lock that mapping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.conftest import FakePlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import AddOverlayElement
from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.services import _dc as Dc
from trcc.ui.presentation.overlay_serialization import config_fields

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))
from core.csharp import DECOMPILE_ROOT  # pyright: ignore[reportMissingImports]


def test_hardware_metric_accessors_round_trip() -> None:
    assert Dc.hardware_metric(0, 1) == ("cpu:temp", "{value:.0f}°C")
    assert Dc.hardware_metric(1, 2) == ("gpu:primary:usage", "{value:.0f}%")
    assert Dc.metric_to_hardware("cpu:temp") == (0, 1)
    assert Dc.metric_to_hardware("gpu:primary:usage") == (1, 2)
    assert Dc.hardware_metric(9, 9) is None
    assert Dc.metric_to_hardware("not:a:sensor") is None


# A vendor theme's own pairs, and what the vendor app draws for them.
# Thermalright's s001 (zt800480) lays out CPU/GPU/RAM columns of (x,1) over
# (x,2), and its Theme.png -- rendered by the Windows app -- shows the RAM
# column as "35°C" over "24%".  We read that pair as memory % over memory
# clock until 2026-10-07: every Windows theme showed the wrong RAM, disk and
# network values here.
@pytest.mark.parametrize(("pair", "sensor"), [
    ((0, 1), "cpu:temp"), ((0, 2), "cpu:usage"),
    ((2, 1), "memory:temp"), ((2, 2), "memory:percent"),
    ((3, 1), "disk:temp"), ((3, 3), "disk:read"),
    ((4, 1), "net:up"), ((4, 2), "net:down"),
])
def test_a_pair_reads_the_csharp_dashboard_row(
    pair: tuple[int, int], sensor: str,
) -> None:
    assert Dc.hardware_metric(*pair)[0] == sensor


_CSHARP_ROWS = (DECOMPILE_ROOT / "TRCC" / "UCSystemInfoOptions.cs")


@pytest.mark.skipif(not _CSHARP_ROWS.exists(),
                    reason=f"C# oracle not present at {_CSHARP_ROWS}")
def test_every_pair_is_the_csharp_dashboard_row() -> None:
    """A DC pair is "panel main, row sub" (UCXiTongXianShiSub.cs:221-245),
    and the rows are InitUCSystemInfoOptionsOneVal's defaults, case main+1.
    Parsed from the C#, so the table cannot drift from it again."""
    import re

    from trcc.core.models import METRICS

    source = _CSHARP_ROWS.read_text(encoding="utf-8")
    body = source[source.index("void InitUCSystemInfoOptionsOneVal"):]
    body = body[:body.index("configArrayList.Add(arrayList);")]
    csharp: dict[int, list[str]] = {}
    for case, block in re.findall(r"case (\d+):(.*?)break;", body, re.S):
        adds = re.findall(r"arrayList\.Add\((.*?)\);", block)
        # [panel, (label, sensor, flag) x 4] -- the label is each triple's first.
        csharp[int(case) - 1] = [a.strip('"') for a in adds[1::3]]
    ours = {main: [METRICS[(main, sub)].label for sub in range(1, 5)]
            for main in range(6)}

    assert len(csharp) == 6
    assert ours == csharp


#: What each C# row name reads -- the last part of our sensor id.
_ROW_READS = {
    "TEMP": "temp", "Usage": ("usage", "percent"), "Clock": ("freq", "clock"),
    "Power": "power", "Available": "available", "Activity": "activity",
    "Read": "read", "Write": "write", "UP rate": "up", "DL rate": "down",
    "Total UP": "total_up", "Total DL": "total_down", "CPUFAN": "cpu",
    "GPUFAN": "gpu", "FAN1": "ssd", "FAN2": "sys2",
}


def test_every_row_name_reads_its_own_kind_of_sensor() -> None:
    """The names alone match the C# even if two sensors swap rows; this pins
    each row's sensor to what its name says."""
    from trcc.core.models import METRICS

    for pair, metric in METRICS.by_dc_pair.items():
        reads = _ROW_READS[metric.label]
        assert metric.sensor_id.rsplit(":", 1)[-1] in (
            reads if isinstance(reads, tuple) else (reads,)), (pair, metric)


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
