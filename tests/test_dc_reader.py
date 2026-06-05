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
from trcc.ui.gui._overlay_grid_adapter import dc_as_legacy_overlay_config


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
    buf.append(0)                             # transparent_display
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
) -> bytes:
    """Build one 0xDD element record."""
    el = bytearray()
    el.extend(struct.pack("<ii", mode, mode_sub))
    el.extend(struct.pack("<ii", x, y))
    el.extend(struct.pack("<ii", main_count, sub_count))
    # Font block — empty font_name (length 0), size 24, neutral style/color
    el.append(0)                                   # font_name length
    el.extend(struct.pack("<f", 24.0))             # size
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


# =========================================================================
# dc_as_legacy_overlay_config — legacy GUI shape (dict keyed by metric)
# =========================================================================


def test_legacy_overlay_returns_empty_when_no_dc(tmp_path: Path) -> None:
    """A theme dir with no config1.dc yields an empty overlay config."""
    assert dc_as_legacy_overlay_config(tmp_path) == {}


def test_legacy_overlay_reads_trcc_json_elements(tmp_path: Path) -> None:
    """A reference-manifest theme keeps its overlay layout in trcc.json's
    ``elements`` (no config1.dc) — SaveTheme's new format.  The adapter
    must surface it, else the GUI grid empties + overlay toggles off on
    reload of a just-saved theme."""
    import json
    (tmp_path / "trcc.json").write_text(json.dumps({
        "name": "t", "width": 320, "height": 320,
        "elements": [
            {"type": "clock", "source": "time", "x": 10, "y": 20,
             "color": "#ffffff", "size": 24, "bold": False, "italic": False},
            {"type": "text", "text": "HI", "x": 5, "y": 15,
             "color": "#ff8800", "size": 18, "bold": True, "italic": False},
        ],
    }), encoding="utf-8")

    cfg = dc_as_legacy_overlay_config(tmp_path)

    assert set(cfg.keys()) == {"time", "custom_text"}
    assert cfg["time"]["x"] == 10
    assert cfg["time"]["metric"] == "time"
    assert cfg["custom_text"]["text"] == "HI"


def test_legacy_overlay_prefers_trcc_json_over_dc(tmp_path: Path) -> None:
    """When both exist, trcc.json wins — matches ThemeService._load_config."""
    import json
    (tmp_path / "trcc.json").write_text(json.dumps({
        "elements": [{"type": "text", "text": "JSON", "x": 1, "y": 2,
                      "color": "#fff", "size": 12,
                      "bold": False, "italic": False}],
    }), encoding="utf-8")
    (tmp_path / "config1.dc").write_bytes(_build_dd_buffer([
        _build_dd_element(mode=4, x=9, y=9, custom_text=b"DC"),
    ]))

    cfg = dc_as_legacy_overlay_config(tmp_path)

    assert cfg["custom_text"]["text"] == "JSON"


def test_legacy_overlay_skips_corrupt_dc(tmp_path: Path) -> None:
    """A DC with bad magic doesn't raise — just empty overlay."""
    (tmp_path / "config1.dc").write_bytes(b"\xff\x00\x00")
    assert dc_as_legacy_overlay_config(tmp_path) == {}


def test_legacy_overlay_dd_clocks_keyed_by_source(tmp_path: Path) -> None:
    """0xDD time/weekday/date elements key on their source name with the
    legacy font dict + position fields the overlay grid expects."""
    (tmp_path / "config1.dc").write_bytes(_build_dd_buffer([
        _build_dd_element(mode=1, x=10, y=20),
        _build_dd_element(mode=2, x=30, y=40),
        _build_dd_element(mode=3, x=50, y=60),
    ]))

    cfg = dc_as_legacy_overlay_config(tmp_path)

    assert set(cfg.keys()) == {"time", "weekday", "date"}
    assert cfg["time"]["x"] == 10
    assert cfg["time"]["y"] == 20
    assert cfg["time"]["metric"] == "time"
    assert cfg["time"]["time_format"] == 0
    assert cfg["date"]["date_format"] == 0
    # Font dict is the legacy shape overlay_grid consumes.
    assert "font" in cfg["time"]
    assert {"name", "size", "style"} <= set(cfg["time"]["font"])


def test_legacy_overlay_disambiguates_duplicates(tmp_path: Path) -> None:
    """Two TIME elements get distinct keys ("time" then "time_1")."""
    (tmp_path / "config1.dc").write_bytes(_build_dd_buffer([
        _build_dd_element(mode=1, x=10, y=20),
        _build_dd_element(mode=1, x=30, y=40),
    ]))

    cfg = dc_as_legacy_overlay_config(tmp_path)

    assert set(cfg.keys()) == {"time", "time_1"}
    assert cfg["time"]["x"] == 10
    assert cfg["time_1"]["x"] == 30


def test_legacy_overlay_custom_text(tmp_path: Path) -> None:
    """A 0xDD CUSTOM element surfaces as a custom_text entry with the
    raw string preserved."""
    (tmp_path / "config1.dc").write_bytes(_build_dd_buffer([
        _build_dd_element(mode=4, x=5, y=15, custom_text=b"GPU"),
    ]))

    cfg = dc_as_legacy_overlay_config(tmp_path)

    assert list(cfg.keys()) == ["custom_text"]
    assert cfg["custom_text"]["text"] == "GPU"
    assert cfg["custom_text"]["x"] == 5
