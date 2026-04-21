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
from trcc.services._dc_reader import load_dc_as_theme_config


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


def test_rejects_dd_cloud_format(tmp_path: Path) -> None:
    f = tmp_path / "cloud.dc"
    f.write_bytes(b"\xDD" + b"\x00" * 50)
    with pytest.raises(ThemeError, match="Cloud-theme"):
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
