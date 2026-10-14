"""KarmaViz's imported shaders, palettes and presets -- what
``dev/tools/import_karmaviz.py`` wrote, held to the rules it wrote them by.

KarmaViz (github.com/karmatripping/KarmaViz) is MIT, (c) 2025 Jason Swint.
These run with no OpenGL: compiling every shader belongs with the visual that
composes them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from trcc.adapters.visual._gl import SHADERS

GLSL = SHADERS / "karmaviz"
DATA = SHADERS.parent / "karmaviz"


def _names(folder: str) -> set[str]:
    return {p.stem for p in (GLSL / folder).glob("*.glsl")}


def _shaders() -> list[Path]:
    return sorted(p for folder in ("warps", "waveforms", "lines")
                  for p in (GLSL / folder).glob("*.glsl"))


def test_the_import_is_all_there() -> None:
    """The counts the import reported -- a re-import that loses a shader, or
    a hand edit that adds one, shows here."""
    assert (len(_names("warps")), len(_names("waveforms")), len(_names("lines"))) == (
        123, 83, 31)
    assert len(list((DATA / "palettes").glob("*.json"))) == 37
    assert len(list((DATA / "presets").glob("*.json"))) == 19


@pytest.mark.parametrize("path", _shaders(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_shader_says_where_it_came_from(path: Path) -> None:
    """MIT asks for the notice to travel with the work: every file names its
    original and points at the licence."""
    head = path.read_text(encoding="utf-8").splitlines()[:4]
    assert head[0].startswith("// KarmaViz ")
    assert re.fullmatch(r"// From KarmaViz [0-9a-f]{7,} \S+\.kvw[fm], MIT \(c\) 2025 Jason",
                        head[1])
    assert head[2] == "// Swint: see assets/karmaviz/LICENSE."


@pytest.mark.parametrize(("folder", "defines"), [
    ("warps", "vec2 get_pattern(vec2 pos, float t)"),
    ("waveforms", "float compute_waveform_intensity_at_xy(float x_coord, float y_coord)"),
    ("lines", "float waveform_value_at(float x_coord, float y_coord, float norm_x, "
              "float sample)"),
])
def test_each_folder_holds_one_kind_of_shader(folder: str, defines: str) -> None:
    """The frame shader calls ``get_pattern`` and
    ``compute_waveform_intensity_at_xy``; a line entry supplies only
    ``waveform_value_at``, and ``line.glsl`` the rest -- so an entry that
    defined the whole waveform would be defined twice."""
    for path in (GLSL / folder).glob("*.glsl"):
        text = path.read_text(encoding="utf-8")
        assert defines in text, path.name
        if folder == "lines":
            assert "compute_waveform_intensity_at_xy" not in text, path.name
    line = (GLSL / "line.glsl").read_text(encoding="utf-8")
    assert line.count("float compute_waveform_intensity_at_xy(") == 1
    assert "waveform_value_at(x_coord, y_coord, norm_x, sample)" in line


def test_every_name_is_ascii() -> None:
    """macOS stores a non-ASCII file name in another Unicode form than the one
    asked for, and packaging tools disagree about them -- KarmaViz shipped
    ``möbius_strip_topology``."""
    names = [p.name for p in GLSL.rglob("*") if p.is_file()]
    names += [p.name for p in DATA.rglob("*") if p.is_file()]
    assert [n for n in names if not n.isascii()] == []


def test_a_waveform_is_whole_or_a_line_entry_never_both() -> None:
    assert _names("waveforms") & _names("lines") == set()


@pytest.mark.parametrize("path", sorted((DATA / "presets").glob("*.json")),
                         ids=lambda p: p.stem)
def test_every_preset_names_shaders_that_exist(path: Path) -> None:
    preset = json.loads(path.read_text(encoding="utf-8"))
    wave = preset["waveform_info"]["current_waveform_name"]
    warp = preset["warp_map_info"]["active_warp_map_name"]
    assert preset["author"] == "KarmaViz"
    assert wave in _names("waveforms") | _names("lines")
    assert not warp or warp in _names("warps")
    assert not {"file_path", "thumbnail_path"} & preset.keys()   # the author's disk
    assert "available_warp_maps" not in preset["warp_map_info"]


@pytest.mark.parametrize("path", sorted((DATA / "palettes").glob("*.json")),
                         ids=lambda p: p.stem)
def test_every_palette_is_rgb(path: Path) -> None:
    palette = json.loads(path.read_text(encoding="utf-8"))
    assert palette["name"]
    assert palette["colors"]
    assert all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in palette["colors"])


def test_the_licence_travels_with_it() -> None:
    licence = (DATA / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence
    assert "Jason Swint" in licence
    assert "dev/tools/import_karmaviz.py" in (DATA / "NOTICE").read_text(encoding="utf-8")
