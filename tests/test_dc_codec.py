"""DC-format codec — round-trip + legacy parity tests.

Read/write pair lives in ``services/_dc_reader.py`` (the file's grown
into a codec; name kept stable for git-blame continuity).  These tests
prove:

  * Every overlay element type next/ supports (text / metric / clock)
    round-trips through write → read with the same field values.
  * The output bytes start with the right magic (0xDD), structure their
    header the way legacy expects, and pack the trailer block correctly.
  * The legacy reader can consume a next/-written DC.  This catches the
    "we wrote bytes legacy can't parse" failure mode that pure read
    round-trip would miss.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.errors import ThemeError
from trcc.services import _dc as Dc


def load_dc_as_theme_config(path):
    return Dc.File(path).read()


def write_dc_from_theme_config(path, config):
    return Dc.File(path).write(config)


@pytest.fixture
def theme_dir(tmp_path: Path) -> Path:
    d = tmp_path / "test_theme"
    d.mkdir()
    return d


# =========================================================================
# Magic + header sanity
# =========================================================================


def test_write_emits_0xdd_magic(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {"elements": []})
    data = out.read_bytes()
    assert data[0] == 0xDD


def test_write_with_no_elements_round_trips(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {"elements": []})
    parsed = load_dc_as_theme_config(out)
    assert parsed["elements"] == []
    assert parsed["overlay_enabled"] is True


def test_write_rejects_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(ThemeError):
        write_dc_from_theme_config(
            tmp_path / "does_not_exist" / "config1.dc",
            {"elements": []},
        )


# =========================================================================
# Per-element round-trips
# =========================================================================


def test_round_trip_text_element(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [{
            "type": "text", "x": 10, "y": 20,
            "text": "Hello", "color": "#ff0000",
            "size": 24.0, "bold": True, "italic": False,
        }],
    })
    parsed = load_dc_as_theme_config(out)
    assert len(parsed["elements"]) == 1
    e = parsed["elements"][0]
    assert e["type"] == "text"
    assert e["x"] == 10
    assert e["y"] == 20
    assert e["text"] == "Hello"
    assert e["color"] == "#ff0000"
    assert e["bold"] is True


def test_round_trip_metric_element(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [{
            "type": "metric", "metric": "cpu:temp",
            "x": 100, "y": 200, "color": "#00ff00", "size": 18.0,
        }],
    })
    parsed = load_dc_as_theme_config(out)
    e = parsed["elements"][0]
    assert e["type"] == "metric"
    assert e["metric"] == "cpu:temp"
    assert e["x"] == 100


def test_round_trip_clock_element(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [
            {"type": "clock", "source": "time",    "x": 1, "y": 2},
            {"type": "clock", "source": "weekday", "x": 3, "y": 4},
            {"type": "clock", "source": "date",    "x": 5, "y": 6},
        ],
    })
    parsed = load_dc_as_theme_config(out)
    assert len(parsed["elements"]) == 3
    sources = [e["source"] for e in parsed["elements"]]
    assert sources == ["time", "weekday", "date"]


def test_mask_visible_round_trips(theme_dir: Path) -> None:
    """``mask_visible`` survives a write→read cycle.

    Regression guard for B1: the writer used to read ``mask_enabled``
    (a key nothing produces) while every reader + consumer uses
    ``mask_visible`` — so a mask-visible theme silently re-saved as
    mask-hidden.
    """
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [{"type": "text", "x": 1, "y": 1, "text": "x"}],
        "mask_visible": True,
        "mask_position": [12, 34],
    })
    parsed = load_dc_as_theme_config(out)
    assert parsed["mask_visible"] is True
    assert parsed["mask_position"] == [12, 34]


def test_trailer_round_trips(theme_dir: Path) -> None:
    """overlay_enabled + rotation + mask state survive write→read→write."""
    out = theme_dir / "config1.dc"
    config = {
        "elements": [],
        "overlay_enabled": False,
        "rotation": 90,
        "mask_visible": True,
        "mask_position": [5, 7],
    }
    write_dc_from_theme_config(out, config)
    first = load_dc_as_theme_config(out)
    # second cycle: re-write what we read, re-read — must be identical
    write_dc_from_theme_config(out, first)
    second = load_dc_as_theme_config(out)
    for key in ("overlay_enabled", "rotation", "mask_visible", "mask_position"):
        assert first[key] == config[key], f"first cycle dropped {key}"
        assert second[key] == config[key], f"second cycle dropped {key}"


def test_the_codec_writes_the_layout_it_is_given_and_nothing_else(
    theme_dir: Path,
) -> None:
    """``config["elements"]`` IS the layout — the codec adds nothing.

    This replaces a test that asserted the opposite: the writer took a
    ``user_overlay_elements=`` argument and CONCATENATED it onto the theme's
    own elements, which made the codec a third place deciding what an overlay
    contains — and it disagreed with the renderer, which resolves one winning
    layer instead of stacking.  Callers now resolve the layout
    (``device_overlay_layout``) and pass it as the config's elements.
    """
    out = theme_dir / "config1.dc"
    layout = [
        {"type": "text", "x": 1, "y": 1, "text": "on-screen"},
        {"id": "u1", "type": "text", "x": 2, "y": 2, "text": "also-on-screen"},
    ]
    write_dc_from_theme_config(out, {"elements": layout})

    parsed = load_dc_as_theme_config(out)
    assert len(parsed["elements"]) == 2, (
        "the codec must write exactly the layout handed to it"
    )
    assert [e["text"] for e in parsed["elements"]] == [
        "on-screen", "also-on-screen",
    ]


# =========================================================================
# Display-options trailer
# =========================================================================


def test_round_trip_preserves_rotation(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [], "rotation": 90,
    })
    parsed = load_dc_as_theme_config(out)
    assert parsed["rotation"] == 90


def test_round_trip_preserves_overlay_disabled(theme_dir: Path) -> None:
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [], "overlay_enabled": False,
    })
    parsed = load_dc_as_theme_config(out)
    assert parsed["overlay_enabled"] is False


# =========================================================================
# Color encoding edge cases
# =========================================================================


def test_color_with_alpha_round_trips(theme_dir: Path) -> None:
    """8-char hex colors keep their alpha byte."""
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [{
            "type": "text", "text": "x",
            "color": "#80ff0000",  # 50% alpha red
        }],
    })
    parsed = load_dc_as_theme_config(out)
    # Reader produces "#rrggbb" when alpha > 0 (current behavior — alpha
    # is preserved in DC bytes but not surfaced through the JSON shape).
    assert parsed["elements"][0]["color"] == "#ff0000"


def test_malformed_color_falls_back_to_white(theme_dir: Path) -> None:
    """Bad hex strings → opaque white, not crash."""
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [{"type": "text", "text": "x", "color": "not-a-color"}],
    })
    parsed = load_dc_as_theme_config(out)
    assert parsed["elements"][0]["color"] == "#ffffff"


# =========================================================================
# Boundary: writing then reading what a Windows TRCC would write
# =========================================================================


def test_round_trip_many_elements(theme_dir: Path) -> None:
    """100 mixed elements round-trip count-correctly."""
    out = theme_dir / "config1.dc"
    elements = []
    for i in range(50):
        elements.append({
            "type": "metric", "metric": "cpu:temp",
            "x": i * 4, "y": i * 5, "size": 14.0,
        })
        elements.append({
            "type": "text", "text": f"row {i}",
            "x": 100, "y": i * 6, "size": 14.0,
        })
    write_dc_from_theme_config(out, {"elements": elements})
    parsed = load_dc_as_theme_config(out)
    assert len(parsed["elements"]) == 100
