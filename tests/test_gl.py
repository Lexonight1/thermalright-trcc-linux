"""OpenGL for the audio visuals: each OS's context, the reading back, and
what a computer with no OpenGL is told.

The drawing tests run on this host's own context -- the graphics card here,
Mesa's software OpenGL in CI -- the one the App would draw on.
"""
from __future__ import annotations

import ctypes
import random
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.system import host_platform_class
from trcc.adapters.visual import VISUALS, GlVisual, drawn_on
from trcc.adapters.visual import _gl as gl_module
from trcc.adapters.visual.cavalier import CavalierVisual
from trcc.adapters.visual.pipes import TrccPipes
from trcc.app import App
from trcc.core.commands import ConnectDevice, ListVisuals, PlayVisual, VisualTile
from trcc.core.models import SOUND_RATE, VISUAL_FPS, VisualSpec

_HOST_PIPES = host_platform_class().VISUALS["trcc-pipes"]
_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}


def _surface() -> gl_module.GlSurface:
    """This host's context, as the App would open it."""
    surface = _HOST_PIPES.SURFACE                    # type: ignore[attr-defined]
    return surface.open()


# ── binding a context to the visuals ────────────────────────────────

def test_an_os_binds_its_context_to_the_opengl_visuals_only() -> None:
    """Every key stays; the OpenGL visuals get the OS's context, each in a
    subclass of its own; the rest are the registry's own classes."""
    bound = drawn_on(gl_module.EglSurface)
    assert set(bound) == set(VISUALS)
    pipes = bound["trcc-pipes"]
    assert issubclass(pipes, TrccPipes) and pipes is not TrccPipes
    assert pipes.SURFACE is gl_module.EglSurface
    assert bound["cavalier-bars"] is VISUALS["cavalier-bars"]
    assert TrccPipes.SURFACE is None                 # the registry is untouched


def test_every_os_names_a_context() -> None:
    """Linux and the BSDs draw on EGL, Windows on WGL, macOS on CGL."""
    from trcc.adapters.system.bsd import BsdOS
    from trcc.adapters.system.linux import LinuxOS
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    named = {os_class.__name__: os_class.VISUALS["trcc-pipes"].SURFACE  # type: ignore[attr-defined]
             for os_class in (LinuxOS, BsdOS, WindowsPlatform, MacOSPlatform)}
    assert named == {"LinuxOS": gl_module.EglSurface,
                     "BsdOS": gl_module.EglSurface,
                     "WindowsPlatform": gl_module.WglSurface,
                     "MacOSPlatform": gl_module.CglSurface}


class _NoOpenGL(gl_module.GlSurface):
    """A computer whose OpenGL will not open."""

    def __init__(self) -> None:
        raise OSError("GDI Generic, OpenGL 1.1.0 is too old")

    def address(self, name: str) -> int:
        return 0

    def close(self) -> None:
        pass


def test_no_opengl_shows_pipes_with_the_reason_and_refuses_to_play(
        tmp_path: Path) -> None:
    """TRCC Pipes stays in the gallery, saying why it cannot draw; playing
    it, or its tile, is refused with the same reason.  MUTATION CHECK:
    drop the class problem from ``_resolve`` and PlayVisual tries to draw."""
    class NoGl(MockPlatform):
        VISUALS = {**drawn_on(_NoOpenGL)}

    app = App(NoGl([_SPEC], tmp_path), renderer=QtRenderer())
    try:
        listed = {v.kind: v.problem for v in app.dispatch(ListVisuals()).visuals}
        assert listed["trcc-pipes"] == ("TRCC Pipes needs OpenGL 3: GDI "
                                        "Generic, OpenGL 1.1.0 is too old")
        assert listed["cavalier-bars"] == ""
        app.dispatch(ConnectDevice(key=_KEY))
        played = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
        assert not played.ok and played.message == listed["trcc-pipes"]
        tile = app.dispatch(VisualTile(visual="trcc-pipes", size=32))
        assert not tile.ok and tile.message == listed["trcc-pipes"]
    finally:
        app.close()


def test_a_visual_with_no_context_named_says_so() -> None:
    assert TrccPipes.problem() == "TRCC Pipes cannot draw on this computer yet"
    assert issubclass(TrccPipes, GlVisual)
    assert not issubclass(CavalierVisual, GlVisual)


# ── drawing and reading back ────────────────────────────────────────

_TOP_RED = ("in vec2 corner;\nin vec2 uv;\nout vec2 v_uv;\n"
            "void main() { v_uv = uv; gl_Position = vec4(corner, 0.0, 1.0); }\n",
            "in vec2 v_uv;\nvoid main() {\n"
            "  FRAG_COLOUR = v_uv.y > 0.5 ? vec4(1.0, 0.0, 0.0, 1.0)\n"
            "                             : vec4(0.0, 0.0, 1.0, 1.0);\n}\n")


@pytest.mark.parametrize("libraries", [
    pytest.param(gl_module._GL_LIBRARIES, id="libOpenGL"),
    # Ubuntu without libopengl0 -- CI's runner: glvnd's libGL dispatches to
    # the EGL context the same way.  MUTATION CHECK: drop "libGL.so.1".
    pytest.param(("libOpenGL-absent.so.0", "libGL.so.1"), id="libGL-only"),
])
def test_a_picture_reads_back_top_row_first(
        monkeypatch: pytest.MonkeyPatch, libraries: tuple[str, ...]) -> None:
    """OpenGL counts rows from the bottom, a panel from the top: what is
    drawn at the top of OpenGL's picture is the first row read back.
    MUTATION CHECK: drop the row flip in read_rgb."""
    if _HOST_PIPES.SURFACE is not gl_module.EglSurface:  # type: ignore[attr-defined]
        libraries = gl_module._GL_LIBRARIES      # the choice is EGL's alone
    monkeypatch.setattr(gl_module, "_GL_LIBRARIES", libraries)
    surface = _surface()
    gl = surface.gl
    try:
        made = gl_module.program(gl, *_TOP_RED, ("corner", "uv"))
        target = gl_module.Framebuffer(gl, 8, 6)
        vao, vbo = ctypes.c_uint(), ctypes.c_uint()
        gl.glGenVertexArrays(1, ctypes.byref(vao))
        gl.glBindVertexArray(vao.value)
        gl.glGenBuffers(1, ctypes.byref(vbo))
        gl.glBindBuffer(gl_module.GL_ARRAY_BUFFER, vbo.value)
        quad = np.array([-1, -1, 0, 0, 1, -1, 1, 0, 1, 1, 1, 1,
                         -1, -1, 0, 0, 1, 1, 1, 1, -1, 1, 0, 1], np.float32)
        gl.glBufferData(gl_module.GL_ARRAY_BUFFER, quad.nbytes,
                        quad.ctypes.data, gl_module.GL_STREAM_DRAW)
        for location in (0, 1):
            gl.glVertexAttribPointer(location, 2, gl_module.GL_FLOAT, 0, 16,
                                     ctypes.c_void_p(location * 8))
            gl.glEnableVertexAttribArray(location)
        target.bind(gl)
        gl.glUseProgram(made)
        gl.glDrawArrays(gl_module.GL_TRIANGLES, 0, 6)
        rows = np.frombuffer(gl_module.read_rgb(gl, target.name, 8, 6),
                             np.uint8).reshape(6, 8, 3)
        assert rows[0].tolist() == [[255, 0, 0]] * 8           # top: red
        assert rows[-1].tolist() == [[0, 0, 255]] * 8          # bottom: blue
        assert gl.glGetError() == 0
    finally:
        surface.close()


def test_an_opengl_older_than_3_is_refused_by_name() -> None:
    """Windows' own GDI Generic is OpenGL 1.1, without shaders: refused,
    saying which OpenGL it was, not with a missing function's name."""
    strings = {gl_module.GL_RENDERER: ctypes.create_string_buffer(b"GDI Generic"),
               gl_module.GL_VERSION: ctypes.create_string_buffer(b"1.1.0")}

    def string_at(name: int) -> int:
        return ctypes.addressof(strings[name])

    get_string = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint)(string_at)

    class Old(gl_module.GlSurface):
        def __init__(self) -> None:
            super().__init__()
            self._ready()

        def address(self, name: str) -> int:
            return (ctypes.cast(get_string, ctypes.c_void_p).value or 0
                    if name == "glGetString" else 0)

        def close(self) -> None:
            pass

    with pytest.raises(OSError, match=r"^GDI Generic, OpenGL 1\.1\.0 is too "
                                      r"old to draw audio visuals"):
        Old()


# ── TRCC Pipes on the graphics card ─────────────────────────────────

def _music(engine: object, frames: int) -> object:
    n = SOUND_RATE // VISUAL_FPS
    frame = None
    for i in range(frames):
        t = (np.arange(n) + i * n) / SOUND_RATE
        beat = 1.0 if i % 8 < 2 else 0.25
        sig = sum(np.sin(2 * np.pi * f * t) * a
                  for f, a in ((60, beat), (700, .5), (2500, .3)))
        engine.feed(np.repeat(sig.astype("<f4"), 2).tobytes())  # type: ignore[attr-defined]
        frame = engine.render()                                  # type: ignore[attr-defined]
    return frame


def test_burning_pipes_flame_in_fire_colours(tmp_path: Path) -> None:
    """With the picture burning, the flames off the pipes are fire: on bare
    black, red leads green and green leads blue -- the palette the CPU fire
    uses.  MUTATION CHECK: swap the palette's red and blue in the shader."""
    picture = tmp_path / "white.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    image.save(str(picture))

    def frame(fire: float) -> np.ndarray:
        random.seed(5)                   # the same pipes, hot and cold
        engine = _HOST_PIPES()
        engine.open(VisualSpec("trcc-pipes", 160, 160, center=picture,
                               center_fire=fire))
        try:
            drawn = _music(engine, 40)
        finally:
            engine.close()
        return np.frombuffer(drawn.data, np.uint8).reshape(160, 160, 3).astype(int)  # type: ignore[attr-defined]

    cold, hot = frame(0.0), frame(1.0)
    flames = np.all(cold == 0, axis=2) & (hot.max(axis=2) > 30)
    assert flames.sum() > 200
    assert (hot[flames][:, 0] >= hot[flames][:, 1]).all()
    assert (hot[flames][:, 1] >= hot[flames][:, 2]).all()


class _CoreProfile(gl_module.EglSurface):
    """macOS's kind of context, on EGL: a core profile, nothing older."""

    def __init__(self) -> None:
        super().__init__(core=True)


@pytest.mark.skipif(_HOST_PIPES.SURFACE is not gl_module.EglSurface,  # type: ignore[attr-defined]
                    reason="a core profile on demand is EGL's")
def test_pipes_draw_the_same_in_macos_glsl_on_a_core_profile(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macOS offers OpenGL only as a core profile, which takes no GLSL
    1.30: there the shaders compile as 1.50.  Here, on EGL's core profile
    with only the 1.50 headers allowed, TRCC Pipes -- tubes, balls, fire
    and picture -- draws the frame it draws everywhere else.  The macOS
    context itself (CGL) needs a Mac; this is everything after it.  What it
    proves is that the 1.50 dialect draws -- not that a Mac refuses 1.30:
    Linux core profiles take 1.30 and ``gl_FragColor`` too (NVIDIA and
    Mesa, measured), Apple's do not.  MUTATION CHECK: leave the 1.50
    header's ``trcc_colour`` undeclared -- both drivers refuse it."""
    picture = tmp_path / "white.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    image.save(str(picture))

    def frame(pipes: type) -> np.ndarray:
        random.seed(5)
        engine = pipes()
        engine.open(VisualSpec("trcc-pipes", 96, 96, center=picture,
                               center_fire=1.0))
        try:
            drawn = _music(engine, 24)
        finally:
            engine.close()
        return np.frombuffer(drawn.data, np.uint8).reshape(96, 96, 3).astype(int)  # type: ignore[attr-defined]

    usual = frame(_HOST_PIPES)
    monkeypatch.setattr(gl_module, "_HEADERS", gl_module._HEADERS[1:])
    core = frame(drawn_on(_CoreProfile)["trcc-pipes"])
    assert (usual.max(axis=2) > 30).sum() > 500           # it drew something
    assert np.abs(usual - core).max() <= 1
