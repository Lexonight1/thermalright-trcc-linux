"""TRCC Pipes' painter: its tubes, balls, fire and picture, drawn by OpenGL.

TRCC Pipes works out *what* to draw each frame on the CPU -- where every
pipe reaches, how thick the beat makes it -- and hands this painter a few
dozen shapes.  Drawing them is the expensive part: on the CPU it was 88% of
a frame (measured 2026-10-13); here the graphics card does it.

Four passes a frame, all into framebuffers of their own:

1. **The shapes.**  Each tube and ball is one quad; its fragment shader
   lights the pixels it covers exactly as the CPU did -- a tube's normal
   from how far across it the pixel sits, a ball's from where on the sphere,
   the screen saver's light and materials, a depth test so the near pipes
   hide the far -- and, with the fire up, turns them toward embers.
2. **The fire** (only while it burns), at half size: heat seeded where the
   pipes and the picture are, risen, spread to its neighbours and cooled --
   the CPU ``Fire``'s rule, one frame per pass, between two buffers.
3. **The frame**: the pipes, and the flames' colours where there are none.
4. **The picture** on top, rocking and swelling where TRCC Pipes places it.

Then the frame is read back as RGB24, top row first.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Any

import numpy as np

from ...core.logs import per_frame
from ._gl import (
    FULL_QUAD,
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST,
    GL_LESS,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_RGBA,
    GL_RGBA8,
    GL_SRC_ALPHA,
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    GL_TRIANGLES,
    GL_UNSIGNED_BYTE,
    Framebuffer,
    GlSurface,
    c_floats,
    program,
    read_rgb,
    shader,
    texture,
    uniforms,
    upload,
    vertex_array,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A shape, as TRCC Pipes hands it over -- one row of floats:
#: start x, y, depth; end x, y, depth (a ball: its centre twice); radius on
#: screen at the start and the end, in pixels; radius in the scene; 1 for a
#: ball, 0 for a tube; material; then the box it covers on screen -- left,
#: top, right, bottom.  Screen pixels count right and down from the top left.
SHAPE_FIELDS = 15
_VERTEX_FLOATS = 2 + 11           # a corner, then the shape's first 11 fields
#: The quad's corners as two triangles, by the box's (x, y) column pairs.
_CORNERS = ((11, 12), (13, 12), (13, 14), (11, 12), (13, 14), (11, 14))



class PipesPainter:
    """Draws TRCC Pipes' frames on *surface*, *width* x *height*, lit with
    *materials* (each ``(ambient, diffuse, specular, shininess)``) from
    *light* with *halfway* its half-vector; depths run to *far*.  The fire
    burns by *fire_rule*: rows risen a frame, cooling exponent, how hot a
    pipe burns."""

    def __init__(self, surface: GlSurface, width: int, height: int,
                 materials: list[tuple[Any, ...]], light: np.ndarray,
                 halfway: np.ndarray, far: float,
                 fire_rule: tuple[int, float, float]) -> None:
        log.info("PipesPainter: %dx%d, %d material(s)", width, height,
                 len(materials))
        self._gl = gl = surface.gl
        self._w, self._h = width, height
        self._half = ((width + 1) // 2, (height + 1) // 2)
        self._frame = 0
        self._shapes = program(gl, shader("pipes/shapes_vertex.glsl"),
                               shader("pipes/shapes_fragment.glsl"),
                               ("corner", "start", "finish", "shape", "material"))
        quad = shader("quad_vertex.glsl")
        self._fire = program(gl, quad, shader("pipes/fire_fragment.glsl"), ("corner", "uv"))
        self._compose = program(gl, quad, shader("pipes/frame_fragment.glsl"),
                                ("corner", "uv"))
        self._paste = program(gl, quad, shader("pipes/picture_fragment.glsl"),
                              ("corner", "uv"))
        self._u = uniforms(gl, self._shapes, "canvas", "light", "halfway",
                           "ambient", "diffuse", "specular", "shininess",
                           "gain", "glow", "far")
        self._uf = uniforms(gl, self._fire, "heat", "pipes", "picture", "canvas",
                            "half_size", "frame", "lift", "bass", "pipe_heat",
                            "cooling", "placed")
        self._uc = uniforms(gl, self._compose, "pipes", "heat", "strength")
        self._up = uniforms(gl, self._paste, "picture")
        self._pipes = Framebuffer(gl, width, height, depth=True)
        self._heat = [Framebuffer(gl, *self._half, floats=True) for _ in range(2)]
        self._out = Framebuffer(gl, width, height)
        self._picture = texture(gl, 1, 1, GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE,
                                bytes(4))
        self._picture_size = (1, 1)
        self._shape_vao, self._shape_vbo = vertex_array(gl, (2, 3, 3, 4, 1))
        self._quad_vao, self._quad_vbo = vertex_array(gl, (2, 2))
        self._paste_vao, self._paste_vbo = vertex_array(gl, (2, 2))
        upload(self._gl, self._quad_vbo, FULL_QUAD)
        self._lit(materials, light, halfway, far)
        lift, cooling, pipe_heat = fire_rule
        gl.glUseProgram(self._fire)
        gl.glUniform1i(self._uf["lift"], lift)
        gl.glUniform1f(self._uf["cooling"], cooling)
        gl.glUniform1f(self._uf["pipe_heat"], pipe_heat)
        gl.glUniform2i(self._uf["half_size"], *self._half)
        for heat in self._heat:
            heat.bind(gl)
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            gl.glClear(GL_COLOR_BUFFER_BIT)

    def _lit(self, materials: list[tuple[Any, ...]], light: np.ndarray,
             halfway: np.ndarray, far: float) -> None:
        """The shapes' light, materials and depth range -- fixed for good."""
        log.debug("PipesPainter._lit: far %.1f", far)
        gl, u = self._gl, self._u
        gl.glUseProgram(self._shapes)
        gl.glUniform2f(u["canvas"], float(self._w), float(self._h))
        for name, value in (("light", light), ("halfway", halfway)):
            gl.glUniform3fv(u[name], 1, c_floats(value))
        for index, name in enumerate(("ambient", "diffuse", "specular")):
            gl.glUniform3fv(u[name], len(materials), c_floats(
                np.concatenate([m[index] for m in materials])))
        gl.glUniform1fv(u["shininess"], len(materials),
                        c_floats(np.array([m[3] for m in materials])))
        gl.glUniform1f(u["far"], far)
        gl.glUseProgram(self._fire)
        gl.glUniform2f(self._uf["canvas"], float(self._w), float(self._h))
        gl.glUniform1i(self._uf["heat"], 1)
        gl.glUniform1i(self._uf["pipes"], 0)
        gl.glUniform1i(self._uf["picture"], 2)
        gl.glUseProgram(self._compose)
        gl.glUniform1i(self._uc["pipes"], 0)
        gl.glUniform1i(self._uc["heat"], 1)
        gl.glUseProgram(self._paste)
        gl.glUniform1i(self._up["picture"], 2)

    def show_picture(self, rgba: bytes, width: int, height: int) -> None:
        """The centre picture from now on: RGBA bytes, top row first."""
        log.info("PipesPainter.show_picture: %dx%d", width, height)
        gl = self._gl
        gl.glDeleteTextures(1, ctypes.byref(ctypes.c_uint(self._picture)))
        self._picture = texture(gl, width, height, GL_RGBA8, GL_RGBA,
                                GL_UNSIGNED_BYTE, rgba, smooth=True)
        self._picture_size = (width, height)

    def paint(self, shapes: np.ndarray, gain: float, glow: float, fire: float,
              bass: float, placed: tuple[float, float] | None) -> bytes:
        """One frame: *shapes* (``SHAPE_FIELDS`` floats a row) lit and
        swollen by *gain*, turned *glow* toward embers; flames as hot as
        *fire*, fed by *bass*; the picture at *placed* (tilt, size), or
        none.  RGB24, top row first."""
        frame_log.debug("PipesPainter.paint: %d shape(s), fire %.2f",
                        len(shapes), fire)
        self._frame += 1
        self._draw_shapes(shapes, gain, glow)
        if fire > 0:
            self._burn(bass, placed)
        self._compose_frame(fire)
        if placed is not None:
            self._draw_picture(*placed)
        return read_rgb(self._gl, self._out.name, self._w, self._h)

    def _draw_shapes(self, shapes: np.ndarray, gain: float, glow: float) -> None:
        """Pass 1: every tube and ball, depth-tested, into ``_pipes``."""
        frame_log.debug("PipesPainter._draw_shapes: %d", len(shapes))
        gl = self._gl
        self._pipes.bind(gl)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not len(shapes):
            return
        corners = np.concatenate([shapes[:, list(c)] for c in _CORNERS], axis=1)
        vertices = np.concatenate(
            [corners.reshape(-1, 2),
             np.repeat(shapes[:, :11], len(_CORNERS), axis=0)], axis=1)
        upload(self._gl, self._shape_vbo, np.ascontiguousarray(vertices, np.float32))
        gl.glUseProgram(self._shapes)
        gl.glUniform1f(self._u["gain"], gain)
        gl.glUniform1f(self._u["glow"], glow)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LESS)
        gl.glBindVertexArray(self._shape_vao)
        gl.glDrawArrays(GL_TRIANGLES, 0, len(vertices))
        gl.glDisable(GL_DEPTH_TEST)

    def _burn(self, bass: float, placed: tuple[float, float] | None) -> None:
        """Pass 2: one frame of the fire, from one heat buffer to the other."""
        frame_log.debug("PipesPainter._burn: bass %.2f", bass)
        gl, u = self._gl, self._uf
        source, target = self._heat
        target.bind(gl)
        gl.glUseProgram(self._fire)
        gl.glUniform1i(u["frame"], self._frame)
        gl.glUniform1f(u["bass"], bass)
        tilt, size = placed if placed is not None else (1.0, 0.0)
        gl.glUniform4f(u["placed"], self._w / 2, self._h / 2, tilt, size)
        self._textures(self._pipes.colour, source.colour, self._picture)
        self._full_quad()
        self._heat.reverse()                        # the new heat is current

    def _compose_frame(self, fire: float) -> None:
        """Pass 3: the pipes, and the flames where there are none."""
        frame_log.debug("PipesPainter._compose_frame: fire %.2f", fire)
        gl = self._gl
        self._out.bind(gl)
        gl.glUseProgram(self._compose)
        gl.glUniform1f(self._uc["strength"], fire)
        self._textures(self._pipes.colour, self._heat[0].colour, self._picture)
        self._full_quad()

    def _draw_picture(self, tilt: float, size: float) -> None:
        """Pass 4: the picture, blended on top -- squeezed by *tilt* across,
        *size* pixels a side, in the middle."""
        frame_log.debug("PipesPainter._draw_picture: tilt %.2f size %.0f",
                        tilt, size)
        gl = self._gl
        half_x = tilt * size / 2 / self._w * 2          # in OpenGL's -1..1
        half_y = size / 2 / self._h * 2
        x0, x1, y0, y1 = -half_x, half_x, half_y, -half_y   # top is +1
        corners = np.array([x0, y0, 0, 0, x1, y0, 1, 0, x1, y1, 1, 1,
                            x0, y0, 0, 0, x1, y1, 1, 1, x0, y1, 0, 1],
                           dtype=np.float32)
        upload(self._gl, self._paste_vbo, corners)
        gl.glUseProgram(self._paste)
        self._textures(self._pipes.colour, self._heat[0].colour, self._picture)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glBindVertexArray(self._paste_vao)
        gl.glDrawArrays(GL_TRIANGLES, 0, 6)
        gl.glDisable(GL_BLEND)

    def _textures(self, *names: int) -> None:
        """Texture units 0, 1, 2 ... hold *names*, in order."""
        frame_log.debug("PipesPainter._textures: %s", names)
        gl = self._gl
        for unit, name in enumerate(names):
            gl.glActiveTexture(GL_TEXTURE0 + unit)
            gl.glBindTexture(GL_TEXTURE_2D, name)
        gl.glActiveTexture(GL_TEXTURE0)

    def _full_quad(self) -> None:
        """Every pixel of the bound framebuffer, once."""
        frame_log.debug("PipesPainter._full_quad")
        self._gl.glBindVertexArray(self._quad_vao)
        self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)

    def close(self) -> None:
        """Free what it drew with -- the surface stays the caller's."""
        log.info("PipesPainter.close")
        gl = self._gl
        for framebuffer in (self._pipes, self._out, *self._heat):
            framebuffer.delete(gl)
        gl.glDeleteTextures(1, ctypes.byref(ctypes.c_uint(self._picture)))
        for made in (self._shapes, self._fire, self._compose, self._paste):
            gl.glDeleteProgram(made)
        for vao, vbo in ((self._shape_vao, self._shape_vbo),
                         (self._quad_vao, self._quad_vbo),
                         (self._paste_vao, self._paste_vbo)):
            gl.glDeleteVertexArrays(1, ctypes.byref(ctypes.c_uint(vao)))
            gl.glDeleteBuffers(1, ctypes.byref(ctypes.c_uint(vbo)))
