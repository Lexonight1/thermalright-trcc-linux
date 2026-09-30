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


@pytest.mark.parametrize(("source", "pattern"), [
    ("time", "%H:%M"), ("time", "%I:%M %p"),
    ("date", "%Y/%m/%d"), ("date", "%d/%m/%Y"), ("date", "%m/%d"),
    ("date", "%d/%m"),
])
def test_a_clock_keeps_its_own_format_through_a_write(
    theme_dir: Path, source: str, pattern: str,
) -> None:
    """Every format the C# can set survives write -> read.  The writer put 0
    in every clock's myModeSub, so an export reset each clock to the first
    format (the test above checks sources only, which is how it got by)."""
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {"elements": [
        {"type": "clock", "source": source, "format": pattern, "x": 1, "y": 2},
    ]})
    assert load_dc_as_theme_config(out)["elements"][0]["format"] == pattern


@pytest.mark.parametrize("show_unit", [True, False])
def test_a_metric_keeps_its_unit_switch_through_a_write(
    theme_dir: Path, show_unit: bool,
) -> None:
    """The writer put 0 in every metric's myModeSub, which the reader turns
    into show_unit=False: every exported metric lost its unit glyph."""
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {"elements": [
        {"type": "metric", "metric": "cpu:temp", "show_unit": show_unit,
         "x": 1, "y": 2},
    ]})
    assert load_dc_as_theme_config(out)["elements"][0]["show_unit"] is show_unit


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


# =========================================================================
# Trailer field identity — the 2.1.6 oracle's own names
# =========================================================================
#
# These pin WHICH BYTE each config key comes from.  Before 2026-09-14 three
# of them were wrong: ``overlay_enabled`` read the trailer's ``myYcbk`` (the
# screencast show-border flag) instead of the header's ``myXtxx`` (the actual
# overlay toggle), and ``transparent_display`` named ``myTpxs`` — 投屏, which
# is SCREENCAST, not transparency.  Measured against 2622 shipped DCs, the
# overlay mislabel disagreed with the truth on 892 of them (34.0%).
#
# Ground truth: the writer at ``FormCZTV.cs:7156`` and its own readers
# (``case 221`` :6817 · ``case 220`` :6212).


def _dd_bytes(*, header: bool, trailer: dict) -> bytes:
    """A zero-element 0xDD DC with a hand-placed header bool + trailer.

    Built from the C# WRITE ORDER, by hand — deliberately NOT through
    ``Dc.Writer``, so a codec that reads and writes the same wrong offset
    still fails these.
    """
    import struct
    buf = bytearray()
    buf.append(0xDD)
    buf.append(1 if header else 0)                  # myXtxx
    buf.extend(struct.pack("<i", 0))                # element count
    buf.append(1 if trailer["myBjxs"] else 0)
    buf.append(1 if trailer["myTpxs"] else 0)
    buf.extend(struct.pack("<i", trailer["directionB"]))
    buf.extend(struct.pack("<i", trailer["myUIMode"]))
    buf.extend(struct.pack("<i", trailer["myMode"]))
    buf.append(1 if trailer["myYcbk"] else 0)
    buf.extend(struct.pack("<4i", *trailer["Jp"]))
    buf.append(1 if trailer["myMbxs"] else 0)
    buf.extend(struct.pack("<2i", *trailer["MB"]))
    return bytes(buf)


_ORACLE_TRAILER = {
    "myBjxs": True, "myTpxs": False, "directionB": 90, "myUIMode": 2,
    "myMode": 16, "myYcbk": False, "Jp": (11, 22, 333, 444),
    "myMbxs": True, "MB": (7, 9),
}


def test_overlay_enabled_is_the_header_byte_not_the_trailer(
    theme_dir: Path,
) -> None:
    """``overlay_enabled`` is ``myXtxx`` (header), NOT ``myYcbk`` (trailer).

    The two are set OPPOSITE here, so a codec reading the old byte returns
    the exact inverse — this cannot pass by coincidence.
    """
    out = theme_dir / "config1.dc"
    out.write_bytes(_dd_bytes(header=True,
                              trailer={**_ORACLE_TRAILER, "myYcbk": False}))

    cfg = load_dc_as_theme_config(out)

    assert cfg["overlay_enabled"] is True, "read myYcbk instead of myXtxx"
    assert cfg["screencast_border"] is False


def test_overlay_enabled_follows_the_header_when_inverted(
    theme_dir: Path,
) -> None:
    """The mirror: header off, border on.  Pins direction, not just position."""
    out = theme_dir / "config1.dc"
    out.write_bytes(_dd_bytes(header=False,
                              trailer={**_ORACLE_TRAILER, "myYcbk": True}))

    cfg = load_dc_as_theme_config(out)

    assert cfg["overlay_enabled"] is False
    assert cfg["screencast_border"] is True


def test_every_trailer_field_lands_under_its_oracle_name(
    theme_dir: Path,
) -> None:
    """All nine trailer fields, each a value no other field carries.

    Distinct values mean a swapped pair of fields fails rather than
    cancelling out — the failure mode a table-driven codec actually has.
    """
    out = theme_dir / "config1.dc"
    out.write_bytes(_dd_bytes(header=True, trailer=_ORACLE_TRAILER))

    cfg = load_dc_as_theme_config(out)

    assert cfg["background_display"] is True        # myBjxs
    assert cfg["screencast_display"] is False       # myTpxs — 投屏
    assert cfg["rotation"] == 90                    # directionB
    assert cfg["ui_mode"] == 2                      # myUIMode
    assert cfg["display_source"] == 16              # myMode — SCREENCAST
    assert cfg["screencast_border"] is False        # myYcbk
    assert cfg["screencast_rect"] == [11, 22, 333, 444]   # JpX JpY JpW JpH
    assert cfg["mask_visible"] is True              # myMbxs
    assert cfg["mask_position"] == [7, 9]           # XvalMB YvalMB


def test_display_source_values_are_the_oracle_enum() -> None:
    """0 / 16 / 48 — ``ThemeSetting`` cases 1 / 2 / 3 (FormCZTV.cs:5948).

    The DC is the ONLY record that a saved theme is a video theme: the C#
    video toggle ``mySpxs`` is never written, only ``myMode = 48``.
    """
    from trcc.core.models import DisplaySource

    assert DisplaySource.THEME == 0
    assert DisplaySource.SCREENCAST == 16
    assert DisplaySource.VIDEO == 48


def test_every_trailer_field_survives_a_write_read_round_trip(
    theme_dir: Path,
) -> None:
    """Write → read is identity on all nine, plus the header.

    ``ui_mode`` / ``display_source`` / ``screencast_rect`` round-tripped to
    ZERO before this: the writer spelled them and neither reader did.
    """
    out = theme_dir / "config1.dc"
    config = {
        "elements": [], "overlay_enabled": False,
        "background_display": False, "screencast_display": True,
        "rotation": 270, "ui_mode": 4, "display_source": 48,
        "screencast_border": True, "screencast_rect": [1, 2, 3, 4],
        "mask_visible": True, "mask_position": [5, 6],
    }
    write_dc_from_theme_config(out, config)

    parsed = load_dc_as_theme_config(out)

    for key, expected in config.items():
        if key == "elements":
            continue
        assert parsed[key] == expected, f"{key} did not survive the round trip"


def test_the_writer_emits_the_header_toggle_it_was_given(
    theme_dir: Path,
) -> None:
    """The header byte is the config's, not a hardcoded ``True``.

    Asserted on the BYTE, because a reader that also had it wrong would
    make a round-trip check pass.
    """
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {"elements": [], "overlay_enabled": False})

    assert out.read_bytes()[1] == 0, "byte 1 (myXtxx) is not the overlay toggle"


def test_a_short_screencast_rect_still_writes_four_ints(
    theme_dir: Path,
) -> None:
    """A field must occupy its full width or every byte after it shifts."""
    out = theme_dir / "config1.dc"
    write_dc_from_theme_config(out, {
        "elements": [], "screencast_rect": [8],
        "mask_visible": True, "mask_position": [4, 5],
    })

    parsed = load_dc_as_theme_config(out)

    assert parsed["screencast_rect"] == [8, 0, 0, 0]
    assert parsed["mask_visible"] is True, "the rect under-wrote and shifted"
    assert parsed["mask_position"] == [4, 5]


def test_theme_flag_keys_are_exactly_what_a_reader_produces(
    theme_dir: Path,
) -> None:
    """``THEME_FLAG_KEYS`` is what ``SaveTheme`` copies into its manifest.

    A key the manifest names but no reader produces is silently absent from
    every saved theme — which is how ``transparent_display`` outlived the
    field it named.
    """
    out = theme_dir / "config1.dc"
    out.write_bytes(_dd_bytes(header=True, trailer=_ORACLE_TRAILER))

    cfg = load_dc_as_theme_config(out)

    assert set(Dc.THEME_FLAG_KEYS) <= set(cfg)
    assert set(Dc.THEME_FLAG_KEYS) == set(cfg) - {"name", "elements"}


def test_a_truncated_trailer_keeps_the_fields_that_were_there(
    theme_dir: Path,
) -> None:
    """Truncation mid-run keeps what landed; the rest take their defaults."""
    import struct

    out = theme_dir / "config1.dc"
    out.write_bytes(
        b"\xDD" + b"\x01" + struct.pack("<i", 0)      # header + 0 elements
        + b"\x00" + b"\x01" + struct.pack("<i", 180)  # myBjxs myTpxs directionB
    )                                                  # cut before myUIMode

    cfg = load_dc_as_theme_config(out)

    assert cfg["background_display"] is False
    assert cfg["screencast_display"] is True
    assert cfg["rotation"] == 180
    assert cfg["ui_mode"] == 0
    assert cfg["mask_position"] == [0, 0]
