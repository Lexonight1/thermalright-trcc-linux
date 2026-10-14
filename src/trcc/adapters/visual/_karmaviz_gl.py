"""KarmaViz's drawing: its frame shader on OpenGL, the way KarmaViz runs it.

KarmaViz (https://github.com/karmatripping/KarmaViz) is MIT, (c) 2025 Jason
Swint; its GLSL is under ``assets/shaders/karmaviz/`` with the licence beside
its data (``assets/karmaviz/LICENSE``).

**One frame shader, three passes, as KarmaViz draws.**  Its frame shader
samples the last frame, warps it, mirrors it, fades it and draws the waveform
on it -- and KarmaViz runs it three times a frame
(``modules/karmaviz.py`` ``render``), each blended SRC_ALPHA over what is there:

1. the last frame -> a cleared buffer;
2. that buffer -> itself, NOT cleared.  KarmaViz samples the framebuffer it is
   drawing into, which OpenGL leaves undefined; here it is read as it stood
   before the draw (copied first), which is what drivers return in practice.
   This is the frame fed back;
3. the fed-back frame -> a cleared buffer: what the panel shows.

Passes 1 and 2 are a step (:meth:`step`); pass 3 is the picture
(:meth:`picture`), so a caller can step more often than it shows.

The shader catalogue lives here too: a waveform is either whole
(``waveforms/``) or a thesaurus entry (``lines/``) completed by
``line.glsl``; a warp map is ``warps/``.
"""
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

import numpy as np

from ...core.logs import per_frame
from ._gl import (
    FULL_QUAD,
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_DRAW_FRAMEBUFFER,
    GL_FLOAT,
    GL_LINEAR,
    GL_NEAREST,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_R32F,
    GL_READ_FRAMEBUFFER,
    GL_RED,
    GL_REPEAT,
    GL_SRC_ALPHA,
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_TRIANGLES,
    SHADERS,
    Framebuffer,
    GlSurface,
    c_floats,
    program,
    read_rgb,
    shader,
    texture,
    upload,
    vertex_array,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The waveform and spectrum textures' widths -- KarmaViz's ``CHUNK`` and its
#: ``fft_samples``.
WAVE_SAMPLES = 1024
SPECTRUM_SAMPLES = 256
_FOLDER = "karmaviz"


def waveform_names() -> tuple[str, ...]:
    """Every waveform, whole or a line entry -- one name each."""
    names = sorted(p.stem for folder in ("waveforms", "lines")
                   for p in (SHADERS / _FOLDER / folder).glob("*.glsl"))
    log.debug("waveform_names: %d", len(names))
    return tuple(names)


def warp_names() -> tuple[str, ...]:
    names = sorted(p.stem for p in (SHADERS / _FOLDER / "warps").glob("*.glsl"))
    log.debug("warp_names: %d", len(names))
    return tuple(names)


def frame_source(wave: str, warp: str | None) -> str:
    """The frame shader with waveform *wave* and warp map *warp* (None: no
    warp) in its placeholders, as KarmaViz's ``build_full_fragment_shader``
    fills them."""
    whole = SHADERS / _FOLDER / "waveforms" / f"{wave}.glsl"
    code = (shader(f"{_FOLDER}/waveforms/{wave}.glsl") if whole.is_file()
            else shader(f"{_FOLDER}/lines/{wave}.glsl") + shader(f"{_FOLDER}/line.glsl"))
    frame = shader(f"{_FOLDER}/frame_fragment.glsl").replace(
        "// WAVEFORM_RENDER_PLACEHOLDER", code)
    if warp is not None:
        frame = (frame.replace("// WARP_MAP_FUNCTIONS_PLACEHOLDER",
                               shader(f"{_FOLDER}/warps/{warp}.glsl"))
                 .replace("// WARP_DISPATCHER_PLACEHOLDER", "return get_pattern(pos, t);"))
    log.debug("frame_source: %s + %s", wave, warp or "no warp")
    return frame


@dataclass(frozen=True, slots=True)
class FrameValues:
    """One step's uniforms -- KarmaViz's names, its meanings."""
    time: float
    animation_speed: float
    rotation: float
    pulse_scale: float
    bounce: bool
    bounce_height: float
    colour: tuple[float, float, float]
    symmetry: int
    kaleidoscope_sections: int
    glow: float
    trail: float
    smoke: float
    warp_intensity: float
    waveform_scale: float
    warp_first: bool


_FLOATS = ("time", "animation_speed", "rotation", "pulse_scale", "bounce_height",
           "glow_intensity", "trail_intensity", "smoke_intensity", "warp_intensity",
           "waveform_scale")
_INTS = ("symmetry_mode", "kaleidoscope_sections", "bounce_enabled", "warp_first")


class KarmaVizPainter:
    """Draws KarmaViz on *surface*, *width* x *height*: :meth:`use` a
    waveform and warp map, then :meth:`step` and :meth:`picture`."""

    def __init__(self, surface: GlSurface, width: int, height: int) -> None:
        log.info("KarmaVizPainter: %dx%d", width, height)
        self._gl = gl = surface.gl
        self._w, self._h = width, height
        self._vertex = shader("quad_vertex.glsl")
        self._program = 0
        self._u: dict[str, int] = {}
        self._quad, self._quad_buffer = vertex_array(gl, (2, 2))
        upload(gl, self._quad_buffer, FULL_QUAD)
        self._wave = texture(gl, WAVE_SAMPLES, 1, GL_R32F, GL_RED, GL_FLOAT, smooth=True)
        self._spectrum = texture(gl, SPECTRUM_SAMPLES, 1, GL_R32F, GL_RED, GL_FLOAT,
                                 smooth=True)
        # first / fed back / shown.  The two that are sampled filter and wrap
        # as ModernGL's textures do by default -- linear, repeating.
        self._first, self._fed, self._shown = (Framebuffer(gl, width, height)
                                               for _ in range(3))
        for sampled in (self._first, self._fed):
            gl.glBindTexture(GL_TEXTURE_2D, sampled.colour)
            for name, value in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                                (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                                (GL_TEXTURE_WRAP_S, GL_REPEAT), (GL_TEXTURE_WRAP_T, GL_REPEAT)):
                gl.glTexParameteri(GL_TEXTURE_2D, name, value)
        for buffer in (self._first, self._fed, self._shown):
            self._clear(buffer)

    def use(self, wave: str, warp: str | None) -> None:
        """Draw waveform *wave* through warp map *warp* from now on."""
        log.info("KarmaVizPainter.use: %s + %s", wave, warp or "no warp")
        gl = self._gl
        made = program(gl, self._vertex, frame_source(wave, warp), ("corner", "uv"))
        if self._program:
            gl.glDeleteProgram(self._program)
        self._program = made
        names = (*_FLOATS, *_INTS, "texture0", "waveform_data", "fft_data",
                 "waveform_length", "waveform_enabled", "waveform_color",
                 "waveform_alpha", "resolution", "glow_radius", "active_warp_map")
        self._u = {name: gl.glGetUniformLocation(made, name.encode()) for name in names}
        gl.glUseProgram(made)
        for name, unit in (("texture0", 0), ("waveform_data", 1), ("fft_data", 2)):
            gl.glUniform1i(self._u[name], unit)
        gl.glUniform1i(self._u["waveform_length"], WAVE_SAMPLES)
        gl.glUniform1i(self._u["waveform_enabled"], 1)
        gl.glUniform1i(self._u["active_warp_map"], 0)
        gl.glUniform1f(self._u["waveform_alpha"], 1.0)
        gl.glUniform1f(self._u["glow_radius"], 0.065)
        gl.glUniform2f(self._u["resolution"], float(self._w), float(self._h))

    def sound(self, wave: np.ndarray, spectrum: np.ndarray) -> None:
        """The waveform (``WAVE_SAMPLES`` floats, -1..1) and spectrum
        (``SPECTRUM_SAMPLES``, 0..1) the next steps draw."""
        frame_log.debug("KarmaVizPainter.sound: %d + %d", wave.size, spectrum.size)
        gl = self._gl
        for name, values in ((self._wave, wave), (self._spectrum, spectrum)):
            data = np.ascontiguousarray(values, dtype=np.float32)
            gl.glBindTexture(GL_TEXTURE_2D, name)
            gl.glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, data.size, 1, 0, GL_RED, GL_FLOAT,
                            data.ctypes.data)

    def step(self, values: FrameValues) -> None:
        """One KarmaViz frame into the fed-back buffer: passes 1 and 2."""
        frame_log.debug("KarmaVizPainter.step: t=%.3f", values.time)
        gl = self._gl
        self._set(values)
        self._clear(self._first)
        self._draw(self._fed, self._first)
        gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, self._first.name)
        gl.glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self._fed.name)
        gl.glBlitFramebuffer(0, 0, self._w, self._h, 0, 0, self._w, self._h,
                             GL_COLOR_BUFFER_BIT, GL_NEAREST)
        self._draw(self._first, self._fed)

    def picture(self, values: FrameValues) -> bytes:
        """What the panel shows: pass 3 over the fed-back buffer.  RGB24,
        top row first."""
        frame_log.debug("KarmaVizPainter.picture")
        self._set(values)
        self._clear(self._shown)
        self._draw(self._fed, self._shown)
        return read_rgb(self._gl, self._shown.name, self._w, self._h)

    def close(self) -> None:
        """Free what it drew with -- the surface stays the caller's."""
        log.info("KarmaVizPainter.close")
        gl = self._gl
        for buffer in (self._first, self._fed, self._shown):
            buffer.delete(gl)
        gl.glDeleteTextures(2, (ctypes.c_uint * 2)(self._wave, self._spectrum))
        if self._program:
            gl.glDeleteProgram(self._program)
        gl.glDeleteVertexArrays(1, ctypes.byref(ctypes.c_uint(self._quad)))
        gl.glDeleteBuffers(1, ctypes.byref(ctypes.c_uint(self._quad_buffer)))

    # ── the passes ───────────────────────────────────────────────────

    def _set(self, v: FrameValues) -> None:
        frame_log.debug("KarmaVizPainter._set: symmetry %d", v.symmetry)
        gl, u = self._gl, self._u
        gl.glUseProgram(self._program)
        for name, value in zip(_FLOATS, (v.time, v.animation_speed, v.rotation,
                                         v.pulse_scale, v.bounce_height, v.glow, v.trail,
                                         v.smoke, v.warp_intensity, v.waveform_scale),
                               strict=True):
            gl.glUniform1f(u[name], value)
        for name, number in zip(_INTS, (v.symmetry, v.kaleidoscope_sections,
                                        int(v.bounce), int(v.warp_first)), strict=True):
            gl.glUniform1i(u[name], number)
        gl.glUniform3fv(u["waveform_color"], 1, c_floats(np.array(v.colour)))

    def _clear(self, buffer: Framebuffer) -> None:
        frame_log.debug("KarmaVizPainter._clear: %d", buffer.name)
        gl = self._gl
        buffer.bind(gl)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)

    def _draw(self, source: Framebuffer, target: Framebuffer) -> None:
        """The frame shader over *source*, blended onto *target*."""
        frame_log.debug("KarmaVizPainter._draw: %d -> %d", source.name, target.name)
        gl = self._gl
        target.bind(gl)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for unit, name in ((0, source.colour), (1, self._wave), (2, self._spectrum)):
            gl.glActiveTexture(GL_TEXTURE0 + unit)
            gl.glBindTexture(GL_TEXTURE_2D, name)
        gl.glActiveTexture(GL_TEXTURE0)
        gl.glBindVertexArray(self._quad)
        gl.glDrawArrays(GL_TRIANGLES, 0, 6)
        gl.glDisable(GL_BLEND)
