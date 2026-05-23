"""ThemeService — JSON-first, DC fallback, auto-migrate."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from trcc.core.errors import ThemeError
from trcc.services.theme import ThemeService

from .test_dc_reader import _build_dc


def test_raises_on_missing_dir(tmp_path: Path) -> None:
    svc = ThemeService()
    with pytest.raises(ThemeError, match="does not exist"):
        svc.load(tmp_path / "nonexistent")


def test_raises_on_file_path(tmp_path: Path) -> None:
    svc = ThemeService()
    (tmp_path / "afile").write_text("oops")
    with pytest.raises(ThemeError, match="not a directory"):
        svc.load(tmp_path / "afile")


def test_raises_on_dir_without_config(tmp_path: Path) -> None:
    svc = ThemeService()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ThemeError, match="No trcc.json or config1.dc"):
        svc.load(empty)


def test_loads_json_theme(tmp_path: Path) -> None:
    theme = tmp_path / "ThemeA"
    theme.mkdir()
    (theme / "trcc.json").write_text(json.dumps({
        "name": "JSON Theme",
        "overlay_enabled": True,
        "elements": [],
    }), encoding="utf-8")

    svc = ThemeService()
    t = svc.load(theme)

    assert t.name == "JSON Theme"
    assert t.config["overlay_enabled"] is True


def test_falls_back_to_dc_and_migrates(tmp_path: Path) -> None:
    theme = tmp_path / "DcTheme"
    theme.mkdir()
    (theme / "config1.dc").write_bytes(_build_dc())

    svc = ThemeService()
    t = svc.load(theme)

    # Loaded from DC — name defaults to directory name
    assert t.name == "DcTheme"

    # Migration wrote trcc.json alongside
    json_path = theme / "trcc.json"
    assert json_path.exists(), "auto-migration should have created config.json"
    migrated = json.loads(json_path.read_text(encoding="utf-8"))
    assert migrated["overlay_enabled"] is True

    # Second load reads JSON directly; no re-migration
    json_mtime_before = json_path.stat().st_mtime_ns
    svc.load(theme)
    assert json_path.stat().st_mtime_ns == json_mtime_before


def test_prefers_json_over_dc_when_both_present(tmp_path: Path) -> None:
    theme = tmp_path / "Both"
    theme.mkdir()
    (theme / "trcc.json").write_text(json.dumps({
        "name": "Wins", "elements": [], "overlay_enabled": True,
    }), encoding="utf-8")
    (theme / "config1.dc").write_bytes(_build_dc())

    svc = ThemeService()
    t = svc.load(theme)

    assert t.name == "Wins"


def test_list_finds_both_formats(tmp_path: Path) -> None:
    json_t = tmp_path / "A"
    json_t.mkdir()
    (json_t / "trcc.json").write_text('{"elements": []}')
    dc_t = tmp_path / "B"
    dc_t.mkdir()
    (dc_t / "config1.dc").write_bytes(_build_dc())
    broken = tmp_path / "C"
    broken.mkdir()    # no config — skipped silently

    svc = ThemeService()
    themes = svc.list(tmp_path)

    names = {t.name for t in themes}
    assert "A" in names
    assert "B" in names
    assert "C" not in names


def test_background_path_finds_00_png(tmp_path: Path) -> None:
    """Legacy / cloud convention: 00.png IS the rendered background.
    Theme.png is the panel thumbnail and must NEVER be a render target."""
    theme = tmp_path / "Legacy"
    theme.mkdir()
    (theme / "trcc.json").write_text('{"elements": []}')
    (theme / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")       # render target
    (theme / "Theme.png").write_bytes(b"\x89PNG\r\n\x1a\n")    # thumbnail only

    svc = ThemeService()
    t = svc.load(theme)

    assert svc.background_path(t) == theme / "00.png"
    assert svc.preview_path(t) == theme / "Theme.png"


def test_background_path_returns_none_when_only_thumbnail(
    tmp_path: Path,
) -> None:
    """Theme.png alone is not enough — rendering the thumbnail would
    ship preview-only artwork to the device."""
    theme = tmp_path / "ThumbOnly"
    theme.mkdir()
    (theme / "trcc.json").write_text('{"elements": []}')
    (theme / "Theme.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    svc = ThemeService()
    t = svc.load(theme)

    assert svc.background_path(t) is None


def test_loads_pre_cutover_filename(tmp_path: Path) -> None:
    """Themes written by pre-cutover next/ (``trcc.json``) still
    load — the rename to ``trcc.json`` doesn't strand existing themes."""
    theme = tmp_path / "OldName"
    theme.mkdir()
    (theme / "trcc.json").write_text(json.dumps({
        "name": "Pre-cutover", "elements": [], "overlay_enabled": True,
    }), encoding="utf-8")

    svc = ThemeService()
    t = svc.load(theme)

    assert t.name == "Pre-cutover"
    # Old file is left in place — rollback safety, no silent deletion.
    assert (theme / "trcc.json").exists()


def test_loads_legacy_config_json(tmp_path: Path) -> None:
    """Themes saved by legacy Windows/Linux TRCC use ``config.json``
    with a dict-of-elements shape under ``dc``.  Loading translates
    each entry into next/'s element list shape."""
    theme = tmp_path / "Custom_Legacy"
    theme.mkdir()
    (theme / "config.json").write_text(json.dumps({
        "background": str(theme / "Theme.png"),
        "mask": "",
        "dc": {
            "time": {
                "x": 50, "y": 100, "color": "#80ffff",
                "font": {"size": 32, "name": "DejaVu Sans", "style": "bold"},
                "enabled": True, "metric": "time", "time_format": 0,
            },
            "cpu:temp": {
                "x": 120, "y": 200, "color": "#ffffff",
                "font": {"size": 24, "name": "DejaVu Sans", "style": "regular"},
                "enabled": True, "metric": "cpu:temp", "mode_sub": 0,
            },
            "off_field": {
                "x": 0, "y": 0, "color": "#000", "font": {"size": 12},
                "enabled": False, "metric": "weekday",
            },
        },
    }), encoding="utf-8")

    svc = ThemeService()
    t = svc.load(theme)

    assert t.name == "Custom_Legacy"
    elements = t.config["elements"]
    # 2 enabled, 1 disabled → 2 elements
    assert len(elements) == 2
    by_marker = {
        (e.get("type"), e.get("source") or e.get("metric")): e for e in elements
    }
    time_el = by_marker[("clock", "time")]
    assert time_el["x"] == 50
    assert time_el["y"] == 100
    assert time_el["color"] == "#80ffff"
    assert time_el["size"] == 32
    assert time_el["bold"] is True
    assert time_el["name"] == "DejaVu Sans"
    cpu_el = by_marker[("metric", "cpu:temp")]
    assert cpu_el["bold"] is False


def test_list_finds_legacy_config_json_themes(tmp_path: Path) -> None:
    """``list()`` discovers themes with legacy ``config.json``."""
    theme = tmp_path / "LegacyOne"
    theme.mkdir()
    (theme / "config.json").write_text('{"dc": {}}', encoding="utf-8")

    themes = ThemeService().list(tmp_path)

    assert {t.name for t in themes} == {"LegacyOne"}


def test_list_finds_pre_cutover_themes(tmp_path: Path) -> None:
    """``list()`` recognises pre-cutover ``trcc.json`` as a marker."""
    theme = tmp_path / "OldStill"
    theme.mkdir()
    (theme / "trcc.json").write_text('{"elements": []}', encoding="utf-8")

    themes = ThemeService().list(tmp_path)

    assert {t.name for t in themes} == {"OldStill"}


def test_background_path_prefers_video_over_static(tmp_path: Path) -> None:
    """When both Theme.mp4 (video) and 00.png (static) exist, the video
    is the background — matches legacy ``td.video or td.bg`` preference."""
    theme = tmp_path / "Both"
    theme.mkdir()
    (theme / "trcc.json").write_text('{"elements": []}')
    (theme / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (theme / "Theme.mp4").write_bytes(b"\x00" * 16)

    svc = ThemeService()
    t = svc.load(theme)

    assert svc.background_path(t) == theme / "Theme.mp4"


# Keep the unused `struct` import alive even if tests don't use it directly —
# it's there for future parametrization of binary DC buffers.
_ = struct
