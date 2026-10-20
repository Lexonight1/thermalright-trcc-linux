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


# ── the visual ───────────────────────────────────────────────────────

def _music(frame: int, rate: int, fps: int) -> bytes:
    """A 120 BPM kick over a chord, stereo float32 -- one panel frame's worth."""
    import numpy as np

    n = rate // fps
    t = (np.arange(n) + frame * n) / rate
    kick = np.exp(-((t * 2.0) % 1.0) * 8.0)
    sound = (0.7 * kick * np.sin(2 * np.pi * 50 * t) + 0.2 * np.sin(2 * np.pi * 220 * t)
             + 0.15 * np.sin(2 * np.pi * 330 * t) + 0.1 * np.sin(2 * np.pi * 1760 * t))
    return np.repeat(sound.astype("<f4"), 2).tobytes()


def test_every_shader_compiles_in_the_frame_it_is_drawn_in() -> None:
    """All 237, each composed into the ported frame shader exactly as the
    visual composes it, through the one program() and its header rule --
    on this host's OpenGL (Mesa's software one in CI)."""
    from trcc.adapters.system import host_platform_class
    from trcc.adapters.visual._gl import program, shader
    from trcc.adapters.visual._karmaviz_gl import frame_source, warp_names, waveform_names

    surface = host_platform_class().VISUALS["karmaviz"].SURFACE.open()  # pyright: ignore[reportAttributeAccessIssue]
    gl, vertex = surface.gl, shader("quad_vertex.glsl")
    refused = []
    try:
        for wave, warp in [(w, None) for w in waveform_names()] + [
                ("sine_wave", w) for w in warp_names()]:
            try:
                gl.glDeleteProgram(program(gl, vertex, frame_source(wave, warp), ("corner", "uv")))
            except OSError as e:
                refused.append(f"{wave} + {warp}: {e}")
    finally:
        surface.close()
    assert (len(waveform_names()), len(warp_names()), refused) == (114, 123, [])


@pytest.mark.parametrize("preset", sorted(p.name for p in (DATA / "presets").glob("*.json")),
                         ids=lambda n: n.removesuffix(".json"))
def test_every_preset_draws(preset: str) -> None:
    """Two seconds of music through the real engine, on this host's OpenGL:
    a picture of the panel's size, and not a black one."""
    import random

    import numpy as np

    from trcc.adapters.system import host_platform_class
    from trcc.core.models import SOUND_RATE, VISUAL_FPS, VisualSpec

    engine_class = host_platform_class().VISUALS["karmaviz"]
    random.seed(3)
    engine = engine_class()
    engine.open(VisualSpec("karmaviz", 96, 96, preset=engine_class.preset(Path(), preset)))
    try:
        for i in range(2 * VISUAL_FPS):
            engine.feed(_music(i, SOUND_RATE, VISUAL_FPS))
            frame = engine.render()
    finally:
        engine.close()
    pixels = np.frombuffer(bytes(frame.data), np.uint8).reshape(96, 96, 3)
    assert (frame.width, frame.height) == (96, 96)
    assert (pixels.max(axis=2) > 24).mean() > 0.002          # something is drawn


def test_the_ear_hears_each_kick_of_a_120_bpm_beat() -> None:
    """KarmaViz's beat detector, chunk by chunk: a 120 BPM kick is a beat on
    every half second -- all but the first, while its adaptive threshold
    settles.  (A steady chord under the kick hides most of them: KarmaViz's
    decibel scale tops out at 0 dB, so loud tones fill the bass bands.  That
    is its arithmetic, kept.)"""
    import numpy as np

    from trcc.adapters.visual.karmaviz import Ear
    from trcc.core.models import SOUND_RATE, VISUAL_FPS

    ear = Ear()
    n = SOUND_RATE // VISUAL_FPS
    heard = []
    for i in range(10 * VISUAL_FPS):
        t = (np.arange(n) + i * n) / SOUND_RATE
        kick = 0.7 * np.exp(-((t * 2.0) % 1.0) * 8.0) * np.sin(2 * np.pi * 50 * t)
        ear.hear(np.repeat(kick.astype("<f4"), 2).tobytes())
        heard += [i / VISUAL_FPS] * ear.take_beats()
    assert heard == [k / 2 for k in range(1, 20)]


class _Painter:
    """What KarmaViz asks its painter to draw -- no OpenGL."""

    def __init__(self) -> None:
        self.looks: list[tuple[str, str | None]] = []

    def use(self, wave: str, warp: str | None) -> None:
        self.looks.append((wave, warp))


@pytest.mark.parametrize(("every", "changes"), [(0, 0), (4, 3), (6, 2)])
def test_change_every_n_beats(every: int, changes: int) -> None:
    """KarmaViz's beat transitions: a new waveform and warp map on every Nth
    beat -- and none at 0, the default, so a tile keeps its look."""
    from trcc.adapters.visual.karmaviz import Colours, KarmaViz, Look

    visual = KarmaViz()
    visual._look = Look.read(DATA / "presets" / "groovy_lamp.json", visual._palettes)
    visual._colours = Colours(visual._palettes, None)
    painter = _Painter()
    visual._painter = painter  # type: ignore[assignment]
    visual._wave, visual._warp = "led_matrix", "thirds_vortex"
    visual.adjust({"change_beats": float(every)})
    for _ in range(12):
        visual._step(1)
    assert len(painter.looks) == changes
    assert all(wave in _names("waveforms") | _names("lines") and warp in _names("warps")
               for wave, warp in painter.looks)
    assert all(a != b for a, b in zip([("led_matrix", "thirds_vortex"), *painter.looks],
                                      painter.looks, strict=False))


def test_moving_the_change_slider_waits_its_beats_before_changing() -> None:
    """The maintainer, 2026-10-16: "changing settings changes the visual".
    The count was beats since the visual STARTED, so a new N that divided it
    changed the look at once -- his log: N 9 at beat 162, N 13 at beat 13,
    N 35 at beat 140, each a jump the moment the slider moved.  Beats are
    now counted since the last change, and moving this slider starts over.

    MUTATION CHECK: count beats since the start again."""
    from trcc.adapters.visual.karmaviz import Colours, KarmaViz, Look

    visual = KarmaViz()
    visual._look = Look.read(DATA / "presets" / "groovy_lamp.json", visual._palettes)
    visual._colours = Colours(visual._palettes, None)
    painter = _Painter()
    visual._painter = painter  # type: ignore[assignment]
    visual._wave, visual._warp = "led_matrix", "thirds_vortex"
    for _ in range(161):
        visual._step(1)
    visual.adjust({"change_beats": 9.0})
    visual._step(1)
    assert painter.looks == []                      # beat 162: not yet
    for _ in range(8):
        visual._step(1)
    assert len(painter.looks) == 1                  # the 9th beat since


def test_another_slider_does_not_restart_the_count() -> None:
    from trcc.adapters.visual.karmaviz import Colours, KarmaViz, Look

    visual = KarmaViz()
    visual._look = Look.read(DATA / "presets" / "groovy_lamp.json", visual._palettes)
    visual._colours = Colours(visual._palettes, None)
    painter = _Painter()
    visual._painter = painter  # type: ignore[assignment]
    visual._wave, visual._warp = "led_matrix", "thirds_vortex"
    visual.adjust({"change_beats": 4.0})
    for _ in range(3):
        visual._step(1)
    visual.adjust({"change_beats": 4.0, "warp": 1.5})
    visual._step(1)
    assert len(painter.looks) == 1


@pytest.mark.parametrize("name", ["", "../presets/groovy_lamp.json", "/etc/passwd",
                                  "groovy_lamp", "no_such_preset.json"])
def test_a_preset_is_one_of_its_own_files_or_nothing(name: str) -> None:
    from trcc.adapters.visual.karmaviz import KarmaViz

    assert KarmaViz.preset(Path(), name) is None


def test_the_presets_are_its_own_whatever_the_folder(tmp_path: Path) -> None:
    from trcc.adapters.visual.karmaviz import KarmaViz

    (tmp_path / "mine.json").write_text("{}")
    listed = KarmaViz.presets(tmp_path)
    assert (len(listed), listed[0], "mine.json" in listed) == (19, "cosmic_weedwhacker.json",
                                                               False)
    assert KarmaViz.preset(tmp_path, "groovy_lamp.json") == DATA / "presets" / "groovy_lamp.json"


def test_the_sliders() -> None:
    from trcc.adapters.visual.karmaviz import KarmaViz

    visual = KarmaViz()
    visual.adjust({"speed": 2.0, "warp": 0.5, "beat_sensitivity": 3.0, "change_beats": 7.6})
    assert (visual._speed, visual._warp_scale, visual._ear.sensitivity,
            visual._change_beats) == (2.0, 0.5, 3.0, 8)
    visual.adjust({})
    assert (visual._speed, visual._warp_scale, visual._ear.sensitivity,
            visual._change_beats) == (1.0, 1.0, 1.5, 0)


def test_the_frame_carries_the_waveform_and_warp_it_was_asked_for() -> None:
    """Without the warp the frame still compiles and draws -- its dispatcher
    falls back to no warp -- so only this can tell."""
    from trcc.adapters.visual._karmaviz_gl import frame_source

    warp = (GLSL / "warps" / "spiral.glsl").read_text(encoding="utf-8")
    line = (GLSL / "lines" / "sine_wave.glsl").read_text(encoding="utf-8")
    warped = frame_source("sine_wave", "spiral")
    assert (warp in warped, line in warped, "return get_pattern(pos, t);" in warped) == (
        True, True, True)
    assert "get_pattern" not in frame_source("sine_wave", None)
