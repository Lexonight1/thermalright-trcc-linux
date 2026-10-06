"""DC binary format reader.

Parses a hand-crafted byte buffer that matches the legacy Windows DC
format so we don't need real theme files to cover the reader.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import List

import pytest

from trcc.core.errors import ThemeError
from trcc.services import _dc as Dc


def load_dc_as_theme_config(path):
    return Dc.File(path).read()


def _build_dc(
    flags: List[bool] | None = None,
    positions: List[tuple[int, int]] | None = None,
    rotation: int = 0,
) -> bytes:
    """Build a minimal 0xDC-format buffer for tests."""
    if flags is None:
        flags = [True] * 8
    if positions is None:
        positions = [(i * 10, i * 20) for i in range(13)]

    buf = bytearray()
    buf.append(0xDC)                         # magic
    buf.extend(struct.pack("<ii", 2, 0))     # version + reserved
    for f in flags:                          # 8 enable flags
        buf.append(1 if f else 0)
    buf.extend(struct.pack("<i", 0))         # reserved int

    # 13 font records.  First record carries the custom text string.
    for i in range(13):
        if i == 0:
            custom = b"HELLO"
            buf.append(len(custom))
            buf.extend(custom)
        # font_name (empty)
        buf.append(0)
        buf.extend(struct.pack("<f", 24.0))   # size
        buf.extend(bytes([0, 0, 0, 255, 0xDE, 0xAD, 0xBE]))  # style+unit+charset+alpha+r+g+b

    buf.append(1)                             # background_display
    buf.append(0)                             # screencast_display (myTpxs)
    buf.extend(struct.pack("<i", rotation))
    buf.extend(struct.pack("<i", 0))          # ui_mode

    for x, y in positions:
        buf.extend(struct.pack("<ii", x, y))
    return bytes(buf)


def test_rejects_wrong_magic(tmp_path: Path) -> None:
    f = tmp_path / "bogus.dc"
    f.write_bytes(b"\x00" * 50)
    with pytest.raises(ThemeError, match="magic"):
        load_dc_as_theme_config(f)


def test_accepts_dd_cloud_format(tmp_path: Path) -> None:
    """0xDD (cloud-theme) format is now supported — parser walks the
    variable-length element list.  An all-zero payload yields a 0-element
    theme with default trailer; only a malformed/short DD file raises.
    """
    f = tmp_path / "cloud.dc"
    f.write_bytes(b"\xDD" + b"\x00" * 50)
    cfg = load_dc_as_theme_config(f)
    assert cfg["elements"] == []


def _build_dd_element(
    mode: int,
    mode_sub: int = 0,
    x: int = 0,
    y: int = 0,
    main_count: int = 0,
    sub_count: int = 0,
    custom_text: bytes = b"",
    font_size: float = 24.0,
) -> bytes:
    """Build one 0xDD element record."""
    el = bytearray()
    el.extend(struct.pack("<ii", mode, mode_sub))
    el.extend(struct.pack("<ii", x, y))
    el.extend(struct.pack("<ii", main_count, sub_count))
    # Font block — empty font_name (length 0), style/color neutral
    el.append(0)                                   # font_name length
    el.extend(struct.pack("<f", font_size))        # size
    el.extend(bytes([0, 0, 0, 255, 255, 255, 255]))  # style+unit+charset+alpha+rgb
    # custom_text — length prefix + bytes
    el.append(len(custom_text))
    el.extend(custom_text)
    return bytes(el)


def _build_dd_buffer(element_blobs: list[bytes]) -> bytes:
    """Build a minimal 0xDD theme file with the given element blobs."""
    buf = bytearray()
    buf.append(0xDD)                                  # magic
    buf.append(1)                                     # system_info flag
    buf.extend(struct.pack("<i", len(element_blobs))) # count
    for blob in element_blobs:
        buf.extend(blob)
    return bytes(buf)


def test_dd_time_weekday_date_emit_clock_elements(tmp_path: Path) -> None:
    """0xDD mode 1/2/3 must emit ``type: "clock"`` with the right source."""
    f = tmp_path / "clocks.dc"
    f.write_bytes(_build_dd_buffer([
        _build_dd_element(mode=1, x=10, y=20),  # TIME
        _build_dd_element(mode=2, x=30, y=40),  # WEEKDAY
        _build_dd_element(mode=3, x=50, y=60),  # DATE
    ]))

    cfg = load_dc_as_theme_config(f)

    assert len(cfg["elements"]) == 3
    by_source = {e["source"]: e for e in cfg["elements"]}
    assert by_source["time"]["type"] == "clock"
    assert by_source["time"]["x"] == 10
    assert by_source["time"]["y"] == 20
    assert by_source["weekday"]["type"] == "clock"
    assert by_source["weekday"]["x"] == 30
    assert by_source["date"]["type"] == "clock"
    assert by_source["date"]["x"] == 50

    # No placeholder text should leak — DD clock elements never emit "text"
    text_payloads = [e.get("text") for e in cfg["elements"] if e["type"] == "text"]
    assert "{time}" not in text_payloads
    assert "{date}" not in text_payloads


def test_dd_font_size_preserves_hero_number(tmp_path: Path) -> None:
    """A big authored font (the 001-series temperature is 128) is preserved,
    not squashed to the 24 default.  A misaligned/garbage read still falls back.
    """
    f = tmp_path / "big.dc"
    f.write_bytes(_build_dd_buffer([
        _build_dd_element(mode=0, main_count=0, sub_count=1, font_size=128.25),  # hero temp
        _build_dd_element(mode=0, main_count=0, sub_count=2, font_size=9.0),     # tiny label
        _build_dd_element(mode=0, main_count=0, sub_count=3, font_size=-5.0),    # garbage
    ]))

    cfg = load_dc_as_theme_config(f)
    by_metric = {e["metric"]: e for e in cfg["elements"] if e["type"] == "metric"}
    assert by_metric["cpu:temp"]["size"] == 128.25
    assert by_metric["cpu:usage"]["size"] == 9.0
    assert by_metric["cpu:freq"]["size"] == 24.0  # garbage → default


def test_rejects_dd_cloud_format_with_bogus_count(tmp_path: Path) -> None:
    """0xDD with element_count > 100 is rejected as malformed."""
    f = tmp_path / "cloud.dc"
    # magic + system_info_flag(1) + count(int32 = 999)
    f.write_bytes(b"\xDD" + b"\x01" + (999).to_bytes(4, "little") + b"\x00" * 200)
    with pytest.raises(ThemeError, match="0xDD element count"):
        load_dc_as_theme_config(f)


def test_rejects_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.dc"
    f.write_bytes(b"")
    with pytest.raises(ThemeError, match="Empty"):
        load_dc_as_theme_config(f)


def test_parses_all_enabled_into_elements(tmp_path: Path) -> None:
    """All 8 flags on → 13 elements produced (custom_text + 6 metric/label pairs)."""
    f = tmp_path / "Theme1" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc())

    cfg = load_dc_as_theme_config(f)

    assert cfg["name"] == "Theme1"
    assert cfg["overlay_enabled"] is True
    assert cfg["rotation"] == 0

    types = [e["type"] for e in cfg["elements"]]
    assert "metric" in types
    assert "text" in types

    # Custom text element carries the string we injected
    custom = next(e for e in cfg["elements"] if e.get("text") == "HELLO")
    assert custom["type"] == "text"
    # x/y from positions[0]
    assert (custom["x"], custom["y"]) == (0, 0)


def test_metric_labels_are_device_names_never_units(tmp_path: Path) -> None:
    """Every metric LABEL text is the device name (CPU/GPU), never a unit.

    The 0xDC format does not store these label strings — the reader supplies
    them from ``_SLOT_MAP`` to match what the Windows app draws by convention.
    Legacy (dc_parser.py:519-531) labelled every cpu_* slot "CPU" and every
    gpu_* slot "GPU"; the unit (%, MHz, °C) belongs in the metric VALUE
    format, never as a label.  The cutover mis-transcribed four label slots
    to their unit ("%"/"MHz"), so a theme's CPU-usage label rendered "%"
    instead of "CPU" (reported with screenshots 2026-06-05).  Lock the table.
    """
    f = tmp_path / "Theme1" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc())  # all metrics enabled

    cfg = load_dc_as_theme_config(f)
    labels = {
        e["text"] for e in cfg["elements"]
        if e["type"] == "text" and e.get("text") != "HELLO"  # exclude custom
    }
    assert labels == {"CPU", "GPU"}, (
        f"metric labels must be device names only, got {sorted(labels)}"
    )
    # No label may be a bare unit — the exact regression that shipped.
    for bad in ("%", "MHz", "°C"):
        assert bad not in labels, f"label {bad!r} is a unit, not a device name"


def test_metric_values_carry_their_unit_for_dynamic_render(tmp_path: Path) -> None:
    """0xDC metric VALUE elements carry the unit in their format string.

    The C# flag-template reader (case 220) sets every value element's
    ``myModeSub = 1`` → it draws number + unit; the unit is the DYNAMIC
    decorator (``_draw_metric`` swaps °C→°F on the global toggle, button0
    can hide it), NOT a baked art glyph.  Rendering the bare integer left a
    Fahrenheit user reading the F value beside a static baked "°C" — wrong.
    Lock the units into the value format so C↔F works.
    """
    f = tmp_path / "Theme1" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc())  # all metrics enabled

    cfg = load_dc_as_theme_config(f)
    fmts = {
        e["metric"]: e["format"]
        for e in cfg["elements"] if e["type"] == "metric"
    }
    assert fmts["cpu:temp"] == "{value:.0f}°C"
    assert fmts["gpu:primary:temp"] == "{value:.0f}°C"
    assert fmts["cpu:freq"] == "{value:.0f} MHz"
    assert fmts["gpu:primary:clock"] == "{value:.0f} MHz"
    assert fmts["cpu:usage"] == "{value:.0f}%"
    assert fmts["gpu:primary:usage"] == "{value:.0f}%"


def test_dd_fan_lcd_sentinel_maps_to_fan_rpm(tmp_path: Path) -> None:
    """The fan-LCD "FAN" element uses main_count 10000; the C# renders it as the
    cooler's own fan RPM.  Before the map it was unmapped → dropped → blank on
    every fan-LCD mask (38 shipped masks).
    """
    f = tmp_path / "fan.dc"
    f.write_bytes(_build_dd_buffer([
        _build_dd_element(mode=0, main_count=10000, sub_count=1, x=180, y=253),
    ]))

    cfg = load_dc_as_theme_config(f)
    metrics = [e for e in cfg["elements"] if e["type"] == "metric"]
    assert len(metrics) == 1
    assert metrics[0]["metric"] == "fan:cpu"
    assert metrics[0]["format"] == "{value:.0f} RPM"


def test_respects_disabled_flags(tmp_path: Path) -> None:
    """With all flags off, no metric elements should be emitted."""
    f = tmp_path / "off.dc"
    f.write_bytes(_build_dc(flags=[False] * 8))

    cfg = load_dc_as_theme_config(f)

    assert cfg["elements"] == []


def test_metric_keys_are_normalized(tmp_path: Path) -> None:
    """cpu_temp / gpu_temp slots must map to our normalized sensor keys."""
    f = tmp_path / "on.dc"
    f.write_bytes(_build_dc())

    cfg = load_dc_as_theme_config(f)

    metric_ids = {e["metric"] for e in cfg["elements"] if e["type"] == "metric"}
    assert "cpu:temp" in metric_ids
    assert "gpu:primary:temp" in metric_ids
    # Ensure no raw legacy names leaked
    assert "cpu_temp" not in metric_ids
    assert "gpu_temp" not in metric_ids


def test_rotation_field_passes_through(tmp_path: Path) -> None:
    f = tmp_path / "rot.dc"
    f.write_bytes(_build_dc(rotation=180))

    cfg = load_dc_as_theme_config(f)

    assert cfg["rotation"] == 180


def _build_dc_with_trailer(*, show_unit: bool) -> bytes:
    """A 0xDC buffer carrying the trailer, so ``num8`` is reachable.

    ``_build_dc`` above stops after the 13 positions, which is a valid older
    0xDC and exactly why the show-unit flag went unnoticed: every test theme
    took the default.
    """
    buf = bytearray(_build_dc())
    buf.append(0)                                  # custom-text string (empty)
    buf.append(1 if show_unit else 0)              # num8 — THE SHOW-UNIT FLAG
    buf.extend(struct.pack("<i", 0))               # myMode
    buf.append(0)                                  # myYcbk
    buf.extend(struct.pack("<4i", 0, 0, 0, 0))     # JpX JpY JpW JpH
    buf.append(1)                                  # myMbxs
    buf.extend(struct.pack("<2i", 0, 0))           # XvalMB YvalMB
    return bytes(buf)


def _font_record() -> bytes:
    """One DC font record: empty name, 24 pt, regular, opaque white."""
    return bytes([0]) + struct.pack("<f", 24.0) + bytes([0, 0, 0, 255, 255, 255, 255])


def _build_dc_with_clock(*, date_idx: int, time_idx: int) -> bytes:
    """A 0xDC buffer through its clock block: date, time and weekday on."""
    buf = bytearray(_build_dc_with_trailer(show_unit=True))
    buf.extend(bytes([1, 1, 1]))                        # master, date, time
    buf.extend(struct.pack("<2i", date_idx, time_idx))  # the two myModeSub
    buf.extend(struct.pack("<4i", 5, 6, 7, 8))          # date x,y  time x,y
    buf.extend(_font_record() + _font_record())         # date font, time font
    buf.append(1)                                       # weekday on
    buf.extend(struct.pack("<2i", 9, 10))
    buf.extend(_font_record())
    return bytes(buf)


@pytest.mark.parametrize(("time_idx", "pattern"), [(0, "%H:%M"),
                                                    (1, "%I:%M %p"),
                                                    (2, "%H:%M")])
def test_a_0xdc_time_keeps_the_themes_own_format(
    tmp_path: Path, time_idx: int, pattern: str,
) -> None:
    """The C# draws each clock element in its own myModeSub; the reader read
    the 0xDC time index and dropped it ("time stays global")."""
    f = tmp_path / "Clock" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc_with_clock(date_idx=3, time_idx=time_idx))

    clocks = {e["source"]: e for e in Dc.File(f).read()["elements"]
              if e["type"] == "clock"}

    assert clocks["time"]["format"] == pattern
    assert clocks["date"]["format"] == "%m/%d"
    assert (clocks["time"]["x"], clocks["time"]["y"]) == (7, 8)


def test_a_0xdc_mask_that_bakes_its_unit_is_drawn_bare(tmp_path: Path) -> None:
    """``num8`` False → every metric VALUE draws the number without a unit.

    The mask art already carries "°C"; drawing ours over it double-prints.
    The 0xDD side has honoured the same field per element all along
    (``myModeSub``), so this is one format catching up with the other rather
    than a new behaviour — 10 of 500 shipped 0xDC files ask for it, against
    177 of 1274 on the 0xDD side that already worked.
    """
    f = tmp_path / "Baked" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc_with_trailer(show_unit=False))

    cfg = load_dc_as_theme_config(f)

    metrics = [e for e in cfg["elements"] if e["type"] == "metric"]
    assert metrics, "no metric elements parsed — this pin proves nothing"
    assert all(e["show_unit"] is False for e in metrics), (
        "a 0xDC mask asked for the bare number and we kept the unit"
    )


def test_a_0xdc_mask_that_wants_the_unit_keeps_it(tmp_path: Path) -> None:
    """The mirror — the common case must not regress to bare numbers."""
    f = tmp_path / "Unit" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc_with_trailer(show_unit=True))

    cfg = load_dc_as_theme_config(f)

    metrics = [e for e in cfg["elements"] if e["type"] == "metric"]
    assert metrics and all(e["show_unit"] is True for e in metrics)


def test_a_0xdc_label_is_not_given_a_show_unit(tmp_path: Path) -> None:
    """``num8`` drives the six VALUE slots, not the labels beside them.

    The C# assigns it to ``arrayList5..10`` — the value elements — and a label
    is static text with no unit to strip.
    """
    f = tmp_path / "Labels" / "config1.dc"
    f.parent.mkdir()
    f.write_bytes(_build_dc_with_trailer(show_unit=False))

    cfg = load_dc_as_theme_config(f)

    texts = [e for e in cfg["elements"] if e["type"] == "text"]
    assert texts, "no text elements parsed — this pin proves nothing"
    assert all("show_unit" not in e for e in texts)


def test_an_icon_element_is_skipped_by_name(caplog: pytest.LogCaptureFixture) -> None:
    """Mode 5 is the C#'s icon element (UCShortcut), not "unknown": a report
    from a theme that has one must say what was dropped."""
    import logging

    caplog.set_level(logging.DEBUG, logger="trcc.services._dc")
    element = Dc._build_dd_element(5, 0, 12, 34, 0, 0, {}, "")

    assert element is None
    assert [r.getMessage() for r in caplog.records if "0xDD" in r.getMessage()] == [
        "0xDD: icon element (C# mode 5, UCShortcut) at (12, 34) is not "
        "supported; skipping"]

