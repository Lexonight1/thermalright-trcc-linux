"""Drawing with OpenGL off-screen: shaders, textures, framebuffers -- the
calls the Audio Visualizer's engines draw with, on the context the OS gives
it (``host.gpu.surface()``, a :class:`~trcc.core.ports.GlSurface`).

Every helper takes that context's ``gl``: OpenGL's calls, bound the first
time each is used.  A visual draws into its own :class:`Framebuffer` and
reads the picture back with :func:`read_rgb`.

Shaders are written for GLSL 1.30, the oldest any OpenGL 3 has -- a Windows
VM's software OpenGL refused 3.30 (measured) -- and :func:`program` puts 1.50
in front instead where only a core profile is offered (macOS).
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import c_char_p, c_int, c_uint
from typing import Any

import numpy as np

from trcc.core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# ── OpenGL's numbers, the ones the engines use ───────────────────────────
GL_TRIANGLES = 0x0004
GL_UNSIGNED_BYTE, GL_FLOAT = 0x1401, 0x1406
GL_RGB, GL_RGBA, GL_RED = 0x1907, 0x1908, 0x1903
GL_RGBA8, GL_R32F, GL_DEPTH_COMPONENT24 = 0x8058, 0x822E, 0x81A6
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_DEPTH_TEST, GL_BLEND, GL_LESS = 0x0B71, 0x0BE2, 0x0201
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_TEXTURE_2D, GL_TEXTURE0 = 0x0DE1, 0x84C0
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER = 0x2801, 0x2800
GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T = 0x2802, 0x2803
GL_NEAREST, GL_LINEAR, GL_CLAMP_TO_EDGE = 0x2600, 0x2601, 0x812F
GL_REPEAT = 0x2901
GL_ARRAY_BUFFER, GL_STREAM_DRAW = 0x8892, 0x88E0
GL_FRAMEBUFFER, GL_READ_FRAMEBUFFER = 0x8D40, 0x8CA8
GL_DRAW_FRAMEBUFFER = 0x8CA9
GL_RENDERBUFFER, GL_COLOR_ATTACHMENT0 = 0x8D41, 0x8CE0
GL_DEPTH_ATTACHMENT, GL_FRAMEBUFFER_COMPLETE = 0x8D00, 0x8CD5
GL_PACK_ALIGNMENT, GL_UNPACK_ALIGNMENT = 0x0D05, 0x0CF5
GL_VERTEX_SHADER, GL_FRAGMENT_SHADER = 0x8B31, 0x8B30
GL_COMPILE_STATUS, GL_LINK_STATUS = 0x8B81, 0x8B82

#: GLSL 1.30, the oldest OpenGL 3; 1.50 core where nothing older is offered
#: (macOS).  The fragment shader writes ``FRAG_COLOUR`` in either.
_HEADERS = (
    ("#version 130\n", "#version 130\n#define FRAG_COLOUR gl_FragColor\n"),
    ("#version 150\n#define attribute in\n",
     "#version 150\nout vec4 trcc_colour;\n#define FRAG_COLOUR trcc_colour\n"),
)


def _compiled(gl: Any, kind: int, source: str) -> tuple[int, str]:
    """A compiled shader, or 0 and why not."""
    shader = gl.glCreateShader(kind)
    text = c_char_p(source.encode())
    gl.glShaderSource(shader, 1, ctypes.byref(text), None)
    gl.glCompileShader(shader)
    status = c_int()
    gl.glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(status))
    if status.value:
        log.debug("_compiled: shader %d", shader)
        return shader, ""
    why = ctypes.create_string_buffer(1024)
    gl.glGetShaderInfoLog(shader, 1024, None, why)
    gl.glDeleteShader(shader)
    log.debug("_compiled: refused -- %s", why.value[:200])
    return 0, why.value.decode(errors="replace")


def program(gl: Any, vertex: str, fragment: str,
            attributes: tuple[str, ...]) -> int:
    """A linked shader program from GLSL bodies with no ``#version`` line:
    tried as 1.30, then as 1.50 core.  *attributes* get locations 0, 1, ...
    in order.  ``OSError`` saying why when this OpenGL takes neither."""
    reasons = []
    for vertex_head, fragment_head in _HEADERS:
        vs, why = _compiled(gl, GL_VERTEX_SHADER, vertex_head + vertex)
        fs, why_f = _compiled(gl, GL_FRAGMENT_SHADER, fragment_head + fragment) \
            if vs else (0, "")
        if vs and fs:
            made = gl.glCreateProgram()
            gl.glAttachShader(made, vs)
            gl.glAttachShader(made, fs)
            for location, name in enumerate(attributes):
                gl.glBindAttribLocation(made, location, name.encode())
            gl.glLinkProgram(made)
            gl.glDeleteShader(vs)
            gl.glDeleteShader(fs)
            linked = c_int()
            gl.glGetProgramiv(made, GL_LINK_STATUS, ctypes.byref(linked))
            if linked.value:
                log.info("program: linked as %s", vertex_head.split("\n")[0])
                return made
            why_l = ctypes.create_string_buffer(1024)
            gl.glGetProgramInfoLog(made, 1024, None, why_l)
            gl.glDeleteProgram(made)
            reasons.append(why_l.value.decode(errors="replace"))
            continue
        if vs:
            gl.glDeleteShader(vs)
        reasons.append(why or why_f)
    log.warning("program: this OpenGL takes neither GLSL 1.30 nor 1.50 -- %s",
                reasons)
    raise OSError(f"this OpenGL cannot draw TRCC's shaders: {reasons[-1][:200]}")


def uniforms(gl: Any, made: int, *names: str) -> dict[str, int]:
    """Where each named uniform of program *made* is (-1: unused there)."""
    found = {name: gl.glGetUniformLocation(made, name.encode()) for name in names}
    log.debug("uniforms: %s", found)
    return found


def texture(gl: Any, width: int, height: int, internal: int, layout: int,
            kind: int, pixels: Any = None, smooth: bool = False) -> int:
    """A 2D texture of *width* x *height*, clamped at its edges."""
    name = c_uint()
    gl.glGenTextures(1, ctypes.byref(name))
    gl.glBindTexture(GL_TEXTURE_2D, name.value)
    filtering = GL_LINEAR if smooth else GL_NEAREST
    gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filtering)
    gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filtering)
    gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
    gl.glTexImage2D(GL_TEXTURE_2D, 0, internal, width, height, 0, layout,
                    kind, pixels)
    log.debug("texture: %d, %dx%d", name.value, width, height)
    return name.value


class Framebuffer:
    """Somewhere to draw off-screen: a colour texture of *width* x *height*
    (8-bit RGBA, or one 32-bit float channel), with a depth buffer if
    asked."""

    def __init__(self, gl: Any, width: int, height: int, depth: bool = False,
                 floats: bool = False) -> None:
        log.debug("Framebuffer: %dx%d depth=%s floats=%s", width, height,
                  depth, floats)
        self.width, self.height = width, height
        self.colour = (texture(gl, width, height, GL_R32F, GL_RED, GL_FLOAT)
                       if floats else
                       texture(gl, width, height, GL_RGBA8, GL_RGBA,
                               GL_UNSIGNED_BYTE))
        name = c_uint()
        gl.glGenFramebuffers(1, ctypes.byref(name))
        self.name = name.value
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.name)
        gl.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                  GL_TEXTURE_2D, self.colour, 0)
        self._depth = 0
        if depth:
            buffer = c_uint()
            gl.glGenRenderbuffers(1, ctypes.byref(buffer))
            self._depth = buffer.value
            gl.glBindRenderbuffer(GL_RENDERBUFFER, self._depth)
            gl.glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24,
                                     width, height)
            gl.glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                         GL_RENDERBUFFER, self._depth)
        status = gl.glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise OSError(f"OpenGL could not draw off-screen (status {status:#x})")

    def bind(self, gl: Any) -> None:
        """Draw into this, all of it, from now on."""
        frame_log.debug("Framebuffer.bind: %d", self.name)
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.name)
        gl.glViewport(0, 0, self.width, self.height)

    def delete(self, gl: Any) -> None:
        log.debug("Framebuffer.delete: %d", self.name)
        gl.glDeleteFramebuffers(1, ctypes.byref(c_uint(self.name)))
        gl.glDeleteTextures(1, ctypes.byref(c_uint(self.colour)))
        if self._depth:
            gl.glDeleteRenderbuffers(1, ctypes.byref(c_uint(self._depth)))


#: Every pixel, as two triangles: OpenGL's corner (-1..1), then the
#: picture's (0..1) -- what a full-frame pass draws.
FULL_QUAD = np.array([-1, -1, 0, 0, 1, -1, 1, 0, 1, 1, 1, 1,
                      -1, -1, 0, 0, 1, 1, 1, 1, -1, 1, 0, 1], dtype=np.float32)


def vertex_array(gl: Any, sizes: tuple[int, ...]) -> tuple[int, int]:
    """A vertex array whose vertices are *sizes* floats after floats --
    attribute 0, 1, ... in order -- over a buffer of its own."""
    vao, vbo = c_uint(), c_uint()
    gl.glGenVertexArrays(1, ctypes.byref(vao))
    gl.glBindVertexArray(vao.value)
    gl.glGenBuffers(1, ctypes.byref(vbo))
    gl.glBindBuffer(GL_ARRAY_BUFFER, vbo.value)
    stride, offset = sum(sizes) * 4, 0
    for location, size in enumerate(sizes):
        gl.glVertexAttribPointer(location, size, GL_FLOAT, 0, stride,
                                 ctypes.c_void_p(offset))
        gl.glEnableVertexAttribArray(location)
        offset += size * 4
    log.debug("vertex_array: %s -> %d", sizes, vao.value)
    return vao.value, vbo.value


def upload(gl: Any, buffer: int, floats: np.ndarray) -> None:
    """*floats* into vertex buffer *buffer*."""
    frame_log.debug("upload: %d float(s)", floats.size)
    gl.glBindBuffer(GL_ARRAY_BUFFER, buffer)
    gl.glBufferData(GL_ARRAY_BUFFER, floats.nbytes, floats.ctypes.data,
                    GL_STREAM_DRAW)


def c_floats(values: np.ndarray) -> Any:
    """*values* as a C array of floats, for a ``glUniform*v``."""
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    frame_log.debug("c_floats: %d", flat.size)
    return (ctypes.c_float * flat.size)(*flat.tolist())


def read_rgb(gl: Any, framebuffer: int, width: int, height: int) -> bytes:
    """*framebuffer*'s picture as RGB24, top row first -- OpenGL's first
    row is the bottom one, a panel's the top."""
    gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer)
    gl.glPixelStorei(GL_PACK_ALIGNMENT, 1)
    rows = np.empty((height, width * 3), dtype=np.uint8)
    gl.glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE,
                    rows.ctypes.data)
    frame_log.debug("read_rgb: %d, %dx%d", framebuffer, width, height)
    return rows[::-1].tobytes()
