"""OpenGL off-screen, on every OS: a context a visual draws on, and the calls
it draws with.

A visual that draws with OpenGL gets its context from the platform -- EGL on
Linux and the BSDs, WGL behind a hidden window on Windows, CGL on macOS
(:func:`~trcc.adapters.visual._base.drawn_on`).  None needs a display or a
window on screen, so the App draws the same way with a desktop open or
headless.  The context is current on the thread that made it, and every call
comes from that thread: OpenGL's rule, and the visual reader's.  A visual
draws into its own framebuffer object (:class:`Framebuffer`) and reads the
picture back (:func:`read_rgb`).

Shaders are written for GLSL 1.30, the oldest any OpenGL 3 has -- a Windows
VM's software OpenGL refused 3.30 (measured) -- and :func:`program` puts 1.50
in front instead where only a core profile is offered (macOS).

**Credit.**  How to make each context without a window follows glcontext,
the context library under moderngl (https://github.com/moderngl/glcontext,
MIT, (c) 2021 ModernGL Contributors): its ``egl.cpp``, ``wgl.cpp`` and
``darwin.cpp`` -- the hidden window and pixel format on Windows, the CGL
pixel-format fallbacks on macOS, ``wglGetProcAddress`` before the DLL's own
exports.  Reimplemented here in ``ctypes``; no code is copied.
"""
from __future__ import annotations

import ctypes
import functools
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from ctypes import (
    POINTER,
    c_char_p,
    c_float,
    c_int,
    c_ssize_t,
    c_uint,
    c_void_p,
)
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# ── OpenGL's numbers, the ones TRCC uses ─────────────────────────────────
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
GL_VENDOR, GL_RENDERER, GL_VERSION = 0x1F00, 0x1F01, 0x1F02

_UINTS = POINTER(c_uint)
#: Every OpenGL call TRCC makes, with its C signature: (result, arguments).
_PROTOTYPES: dict[str, tuple[Any, ...]] = {
    "glGetString": (c_char_p, c_uint),
    "glGetError": (c_uint,),
    "glViewport": (None, c_int, c_int, c_int, c_int),
    "glClearColor": (None, c_float, c_float, c_float, c_float),
    "glClear": (None, c_uint),
    "glEnable": (None, c_uint),
    "glDisable": (None, c_uint),
    "glDepthFunc": (None, c_uint),
    "glBlendFunc": (None, c_uint, c_uint),
    "glPixelStorei": (None, c_uint, c_int),
    "glReadPixels": (None, c_int, c_int, c_int, c_int, c_uint, c_uint,
                     c_void_p),
    "glDrawArrays": (None, c_uint, c_int, c_int),
    "glCreateShader": (c_uint, c_uint),
    "glShaderSource": (None, c_uint, c_int, POINTER(c_char_p), c_void_p),
    "glCompileShader": (None, c_uint),
    "glGetShaderiv": (None, c_uint, c_uint, POINTER(c_int)),
    "glGetShaderInfoLog": (None, c_uint, c_int, c_void_p, c_void_p),
    "glDeleteShader": (None, c_uint),
    "glCreateProgram": (c_uint,),
    "glAttachShader": (None, c_uint, c_uint),
    "glBindAttribLocation": (None, c_uint, c_uint, c_char_p),
    "glLinkProgram": (None, c_uint),
    "glGetProgramiv": (None, c_uint, c_uint, POINTER(c_int)),
    "glGetProgramInfoLog": (None, c_uint, c_int, c_void_p, c_void_p),
    "glUseProgram": (None, c_uint),
    "glDeleteProgram": (None, c_uint),
    "glGetUniformLocation": (c_int, c_uint, c_char_p),
    "glUniform1i": (None, c_int, c_int),
    "glUniform1f": (None, c_int, c_float),
    "glUniform2f": (None, c_int, c_float, c_float),
    "glUniform2i": (None, c_int, c_int, c_int),
    "glUniform4f": (None, c_int, c_float, c_float, c_float, c_float),
    "glUniform3fv": (None, c_int, c_int, POINTER(c_float)),
    "glUniform1fv": (None, c_int, c_int, POINTER(c_float)),
    "glUniformMatrix3fv": (None, c_int, c_int, ctypes.c_ubyte,
                           POINTER(c_float)),
    "glGenVertexArrays": (None, c_int, _UINTS),
    "glBindVertexArray": (None, c_uint),
    "glDeleteVertexArrays": (None, c_int, _UINTS),
    "glGenBuffers": (None, c_int, _UINTS),
    "glBindBuffer": (None, c_uint, c_uint),
    "glBufferData": (None, c_uint, c_ssize_t, c_void_p, c_uint),
    "glDeleteBuffers": (None, c_int, _UINTS),
    "glVertexAttribPointer": (None, c_uint, c_int, c_uint, ctypes.c_ubyte,
                              c_int, c_void_p),
    "glEnableVertexAttribArray": (None, c_uint),
    "glGenTextures": (None, c_int, _UINTS),
    "glBindTexture": (None, c_uint, c_uint),
    "glActiveTexture": (None, c_uint),
    "glTexImage2D": (None, c_uint, c_int, c_int, c_int, c_int, c_int, c_uint,
                     c_uint, c_void_p),
    "glTexParameteri": (None, c_uint, c_uint, c_int),
    "glDeleteTextures": (None, c_int, _UINTS),
    "glGenFramebuffers": (None, c_int, _UINTS),
    "glBindFramebuffer": (None, c_uint, c_uint),
    "glFramebufferTexture2D": (None, c_uint, c_uint, c_uint, c_uint, c_int),
    "glCheckFramebufferStatus": (c_uint, c_uint),
    "glBlitFramebuffer": (None, c_int, c_int, c_int, c_int, c_int, c_int,
                          c_int, c_int, c_uint, c_uint),
    "glDeleteFramebuffers": (None, c_int, _UINTS),
    "glGenRenderbuffers": (None, c_int, _UINTS),
    "glBindRenderbuffer": (None, c_uint, c_uint),
    "glRenderbufferStorage": (None, c_uint, c_uint, c_int, c_int),
    "glFramebufferRenderbuffer": (None, c_uint, c_uint, c_uint, c_uint),
    "glDeleteRenderbuffers": (None, c_int, _UINTS),
}


class Gl:
    """OpenGL's calls on one context, typed from ``_PROTOTYPES`` and bound
    the first time each is used: ``gl.glClear(GL_COLOR_BUFFER_BIT)``."""

    def __init__(self, surface: GlSurface) -> None:
        log.debug("Gl: on %s", type(surface).__name__)
        self._surface = surface

    def __getattr__(self, name: str) -> Any:
        if name not in _PROTOTYPES:
            raise AttributeError(f"TRCC does not call {name}")
        restype, *argtypes = _PROTOTYPES[name]
        address = self._surface.address(name)
        log.debug("Gl: binding %s at %#x", name, address or 0)
        if not address:
            raise OSError(f"this OpenGL has no {name}")
        bound = ctypes.CFUNCTYPE(restype, *argtypes)(address)
        setattr(self, name, bound)           # bound once
        return bound

    def string(self, name: int) -> str:
        """``glGetString`` as text."""
        raw = self.glGetString(name) or b"?"
        log.debug("Gl.string: %#x", name)
        return raw.decode(errors="replace")


class GlSurface(ABC):
    """An OpenGL context with nowhere on screen to draw: current on the
    thread that made it, until :meth:`close`.  Draw into a
    :class:`Framebuffer`; read back with :func:`read_rgb`."""

    def __init__(self) -> None:
        log.debug("%s.__init__", type(self).__name__)
        self.gl = Gl(self)

    @classmethod
    def open(cls) -> GlSurface:
        """A context of this kind, as the platform opens one for a visual."""
        log.debug("%s.open", cls.__name__)
        return cls()

    def _ready(self) -> None:
        """Refuse an OpenGL older than 3 -- Windows' own "GDI Generic" 1.1
        has no shaders at all -- saying which one it was."""
        version = self.gl.string(GL_VERSION)
        major = int(version.split(".")[0]) if version[:1].isdigit() else 0
        log.info("%s: %s", type(self).__name__, self.describe())
        if major < 3:
            text = self.describe()
            self.close()
            raise OSError(f"{text} is too old to draw audio visuals "
                          "(they need OpenGL 3)")

    def describe(self) -> str:
        """Which OpenGL this is -- for the log and a refusal."""
        gl = self.gl
        text = f"{gl.string(GL_RENDERER)}, OpenGL {gl.string(GL_VERSION)}"
        log.debug("%s.describe: %s", type(self).__name__, text)
        return text

    @abstractmethod
    def address(self, name: str) -> int:
        """Where OpenGL function *name* is; 0 when this OpenGL lacks it."""

    @abstractmethod
    def close(self) -> None:
        """Let go of the context (idempotent)."""


def _symbol(library: Any, name: str) -> int:
    """*name*'s address in *library*'s own exports, or 0."""
    try:
        address = ctypes.cast(getattr(library, name), c_void_p).value or 0
    except AttributeError:
        address = 0
    log.debug("_symbol: %s -> %#x", name, address)
    return address


def _ints(*values: int) -> ctypes.Array[c_int]:
    log.debug("_ints: %d value(s)", len(values))
    return (c_int * len(values))(*values)


# ── EGL: Linux and the BSDs ──────────────────────────────────────────────
_EGL_PBUFFER_BIT, _EGL_OPENGL_BIT, _EGL_OPENGL_API = 0x1, 0x8, 0x30A2
_EGL_SURFACE_TYPE, _EGL_RENDERABLE_TYPE = 0x3033, 0x3040
_EGL_RED, _EGL_GREEN, _EGL_BLUE, _EGL_ALPHA = 0x3024, 0x3023, 0x3022, 0x3021
_EGL_DEPTH, _EGL_WIDTH, _EGL_HEIGHT, _EGL_NONE = 0x3025, 0x3057, 0x3056, 0x3038
_EGL_MAJOR, _EGL_MINOR, _EGL_PROFILE, _EGL_CORE = 0x3098, 0x30FB, 0x30FD, 0x1
#: Mesa's display that needs no display server -- what a headless box has.
_EGL_PLATFORM_SURFACELESS_MESA = 0x31DD


#: Where OpenGL's own calls are, without GLX: glvnd's ``libOpenGL``; else its
#: ``libGL``, which dispatches to the current EGL context the same way and is
#: on every TRCC install -- Qt links it.  Ubuntu ships ``libOpenGL.so.0`` in
#: ``libopengl0``, which neither ``libgl1`` nor ``libegl1`` pulls in: CI's
#: runner had none, and every OpenGL test failed (2026-10-13).
_GL_LIBRARIES = ("libOpenGL.so.0", "libGL.so.1")


def _opengl_library() -> Any:
    """The first of ``_GL_LIBRARIES`` that loads; ``OSError`` naming both."""
    failures = []
    for name in _GL_LIBRARIES:
        try:
            library = ctypes.CDLL(name)
        except OSError as e:
            failures.append(str(e))
            continue
        log.info("_opengl_library: %s", name)
        return library
    raise OSError(f"OpenGL (EGL) is not available: {'; '.join(failures)}")


class EglSurface(GlSurface):
    """An EGL *pbuffer*: a surface with a framebuffer of its own and no
    window.  On the default display first; with none (no X, no Wayland --
    a headless box), on Mesa's surfaceless one, which the default display
    refused there (measured 2026-10-13).  *core* asks for OpenGL 3.3 core,
    as projectM needs; otherwise whatever OpenGL the driver offers."""

    def __init__(self, width: int = 1, height: int = 1, core: bool = False) -> None:
        log.info("EglSurface: %dx%d core=%s", width, height, core)
        super().__init__()
        try:
            egl = ctypes.CDLL("libEGL.so.1")
        except OSError as e:
            raise OSError(f"OpenGL (EGL) is not available: {e}") from e
        self._libgl = _opengl_library()
        for name in ("eglGetDisplay", "eglCreatePbufferSurface",
                     "eglCreateContext", "eglGetProcAddress"):
            getattr(egl, name).restype = c_void_p
        egl.eglGetDisplay.argtypes = [c_void_p]
        egl.eglGetProcAddress.argtypes = [c_char_p]
        egl.eglInitialize.argtypes = [c_void_p] + [c_void_p] * 2
        egl.eglChooseConfig.argtypes = [
            c_void_p, POINTER(c_int), POINTER(c_void_p), c_int, POINTER(c_int)]
        egl.eglCreatePbufferSurface.argtypes = [c_void_p, c_void_p, POINTER(c_int)]
        egl.eglCreateContext.argtypes = [c_void_p, c_void_p, c_void_p,
                                         POINTER(c_int)]
        egl.eglMakeCurrent.argtypes = [c_void_p] * 4
        egl.eglDestroySurface.argtypes = [c_void_p] * 2
        egl.eglDestroyContext.argtypes = [c_void_p] * 2
        egl.eglTerminate.argtypes = [c_void_p]
        self._egl = egl
        self._surface = self._context = None
        self._display, config = self._display_and_config()
        self._surface = egl.eglCreatePbufferSurface(
            self._display, config,
            _ints(_EGL_WIDTH, width, _EGL_HEIGHT, height, _EGL_NONE))
        attributes = (_ints(_EGL_MAJOR, 3, _EGL_MINOR, 3, _EGL_PROFILE,
                            _EGL_CORE, _EGL_NONE) if core else _ints(_EGL_NONE))
        self._context = egl.eglCreateContext(self._display, config, None,
                                             attributes)
        if not (self._surface and self._context and egl.eglMakeCurrent(
                self._display, self._surface, self._surface, self._context)):
            self.close()
            raise OSError("OpenGL 3.3 is not available" if core
                          else "OpenGL (EGL) could not make a context")
        self._ready()

    def _display_and_config(self) -> tuple[Any, Any]:
        """The default display, else Mesa's surfaceless one -- whichever
        initialises and offers an OpenGL pbuffer."""
        egl = self._egl
        for name, display in self._displays():
            config, count = c_void_p(), c_int()
            if (display and egl.eglInitialize(display, None, None)
                    and egl.eglChooseConfig(
                        display,
                        _ints(_EGL_SURFACE_TYPE, _EGL_PBUFFER_BIT,
                              _EGL_RENDERABLE_TYPE, _EGL_OPENGL_BIT, _EGL_RED, 8,
                              _EGL_GREEN, 8, _EGL_BLUE, 8, _EGL_ALPHA, 8,
                              _EGL_DEPTH, 24, _EGL_NONE),
                        ctypes.byref(config), 1, ctypes.byref(count))
                    and count.value and egl.eglBindAPI(_EGL_OPENGL_API)):
                log.info("EglSurface: drawing on the %s display", name)
                return display, config
            log.info("EglSurface: the %s display offers no pbuffer", name)
            if display:
                egl.eglTerminate(display)
        raise OSError("OpenGL (EGL) found no off-screen surface to draw on")

    def _displays(self) -> Iterator[tuple[str, Any]]:
        """The default display; then, only if that one would not do, Mesa's
        surfaceless one."""
        egl = self._egl
        log.debug("EglSurface._displays")
        yield "default", egl.eglGetDisplay(None)
        platform_display = egl.eglGetProcAddress(b"eglGetPlatformDisplayEXT")
        if platform_display:
            get = ctypes.CFUNCTYPE(c_void_p, c_uint, c_void_p, c_void_p)(
                platform_display)
            yield "surfaceless", get(_EGL_PLATFORM_SURFACELESS_MESA, None, None)

    def address(self, name: str) -> int:
        address = (_symbol(self._libgl, name)
                   or self._egl.eglGetProcAddress(name.encode()) or 0)
        log.debug("EglSurface.address: %s -> %#x", name, address)
        return address

    def close(self) -> None:
        log.debug("EglSurface.close: open=%s", self._context is not None)
        egl, display = self._egl, getattr(self, "_display", None)
        if display is None:
            return
        egl.eglMakeCurrent(display, None, None, None)
        if self._context:
            egl.eglDestroyContext(display, self._context)
        if self._surface:
            egl.eglDestroySurface(display, self._surface)
        egl.eglTerminate(display)
        self._context = self._surface = self._display = None


# ── WGL: Windows ─────────────────────────────────────────────────────────
_CS_OWNDC = 0x0020
_PFD_FLAGS = 0x0004 | 0x0020           # draw to window, OpenGL
_PFD_GENERIC = 0x0040                   # Microsoft's own software OpenGL 1.1


class _WndClass(ctypes.Structure):
    _fields_ = [("style", c_uint), ("lpfnWndProc", c_void_p),
                ("cbClsExtra", c_int), ("cbWndExtra", c_int),
                ("hInstance", c_void_p), ("hIcon", c_void_p),
                ("hCursor", c_void_p), ("hbrBackground", c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


class _PixelFormat(ctypes.Structure):
    """``PIXELFORMATDESCRIPTOR``."""
    _fields_ = [("nSize", ctypes.c_ushort), ("nVersion", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint32), ("iPixelType", ctypes.c_ubyte),
                ("cColorBits", ctypes.c_ubyte), ("_colours", ctypes.c_ubyte * 12),
                ("cDepthBits", ctypes.c_ubyte), ("cStencilBits", ctypes.c_ubyte),
                ("_rest", ctypes.c_ubyte * 15)]


class WglSurface(GlSurface):
    """A WGL context on a hidden window -- the window is never shown, and
    exists only because WGL needs a device context to start from.  No Qt
    platform is involved, so the App draws headless too: Qt's offscreen
    platform offers no OpenGL on Windows at all (measured 2026-10-13).

    *library* is an ``opengl32.dll`` of its own -- a software OpenGL such as
    Mesa's -- which sets its pixel formats itself; by default, Windows' own,
    which hands the formats to the graphics driver."""

    _CLASS = "trcc-gl"
    _registered: ClassVar[bool] = False

    def __init__(self, library: Path | None = None) -> None:
        log.info("WglSurface: opening on %s", library or "the graphics driver")
        super().__init__()
        windll = ctypes.WinDLL  # pyright: ignore[reportAttributeAccessIssue]
        self._user32, self._gdi32 = windll("user32"), windll("gdi32")
        self._opengl32 = windll(str(library) if library else "opengl32")
        self._kernel32 = windll("kernel32")
        self._own_formats = library is not None
        pixel_api = self._opengl32 if self._own_formats else self._gdi32
        prefix = "wgl" if self._own_formats else ""
        self._describe_format = getattr(pixel_api, f"{prefix}DescribePixelFormat")
        self._set_format = getattr(pixel_api, f"{prefix}SetPixelFormat")
        self._typed()
        self._hwnd = self._hdc = self._context = None
        instance = self._kernel32.GetModuleHandleW(None)
        self._register(instance)
        self._hwnd = self._user32.CreateWindowExW(
            0, self._CLASS, "", 0, 0, 0, 1, 1, None, None, instance, None)
        self._hdc = self._user32.GetDC(self._hwnd) if self._hwnd else None
        if not self._hdc:
            self.close()
            raise OSError("OpenGL (WGL) could not make its hidden window")
        chosen, pixels = self._pixel_format()
        if not (chosen and self._set_format(self._hdc, chosen,
                                            ctypes.byref(pixels))):
            self.close()
            raise OSError("OpenGL (WGL) found no pixel format to draw with")
        self._context = self._opengl32.wglCreateContext(self._hdc)
        if not (self._context and self._opengl32.wglMakeCurrent(
                self._hdc, self._context)):
            self.close()
            raise OSError("OpenGL (WGL) could not make a context")
        self._ready()

    @classmethod
    def open(cls) -> GlSurface:
        """On the graphics driver; where it offers no OpenGL 3 -- a VM, a
        Remote Desktop session, a box without its graphics driver -- on the
        software OpenGL Qt ships for the same case (``opengl32sw.dll``,
        Mesa), as Qt itself falls back to it.  That is what drew in a
        Windows VM whose driver had only GDI Generic 1.1 (measured
        2026-10-13)."""
        try:
            return cls()
        except OSError as e:
            import PySide6
            software = Path(PySide6.__file__).parent / "opengl32sw.dll"
            log.info("WglSurface.open: the driver will not do (%s); software "
                     "OpenGL %s %s", e, "at" if software.is_file() else
                     "not at", software)
            if not software.is_file():
                raise
            return cls(software)

    def _pixel_format(self) -> tuple[int, _PixelFormat]:
        """The first RGBA OpenGL format the graphics driver provides itself.
        ``ChoosePixelFormat`` answered with Microsoft's generic one -- GDI
        Generic, OpenGL 1.1, no shaders -- in a VM whose driver offers
        OpenGL 3 (measured 2026-10-13); Qt walks the list the same way."""
        size = ctypes.sizeof(_PixelFormat)
        pixels = _PixelFormat()
        count = self._describe_format(self._hdc, 1, size, None)
        fallback = 0
        for index in range(1, count + 1):
            self._describe_format(self._hdc, index, size, ctypes.byref(pixels))
            if (pixels.dwFlags & _PFD_FLAGS != _PFD_FLAGS
                    or pixels.iPixelType != 0 or pixels.cColorBits < 24):
                continue
            if self._own_formats or not pixels.dwFlags & _PFD_GENERIC:
                log.info("WglSurface: pixel format %d of %d (%s, %d-bit "
                         "colour, %d-bit depth)", index, count,
                         "its own" if self._own_formats else "the driver's",
                         pixels.cColorBits, pixels.cDepthBits)
                return index, pixels
            fallback = fallback or index
        log.info("WglSurface: the driver offers no format of its own; "
                 "format %d of %d is Windows' generic one", fallback, count)
        if fallback:
            self._describe_format(self._hdc, fallback, size,
                                  ctypes.byref(pixels))
        return fallback, pixels

    def _typed(self) -> None:
        """Handles are pointers: say so, or 64-bit Windows truncates them."""
        log.debug("WglSurface._typed")
        user32, opengl32 = self._user32, self._opengl32
        self._kernel32.GetModuleHandleW.restype = c_void_p
        self._kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        user32.RegisterClassW.argtypes = [POINTER(_WndClass)]
        user32.CreateWindowExW.restype = c_void_p
        user32.CreateWindowExW.argtypes = [
            c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, c_uint, c_int, c_int,
            c_int, c_int, c_void_p, c_void_p, c_void_p, c_void_p]
        user32.GetDC.restype = c_void_p
        user32.GetDC.argtypes = [c_void_p]
        user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
        user32.DestroyWindow.argtypes = [c_void_p]
        self._describe_format.argtypes = [c_void_p, c_int, c_uint, c_void_p]
        self._set_format.argtypes = [c_void_p, c_int, POINTER(_PixelFormat)]
        opengl32.wglCreateContext.restype = c_void_p
        opengl32.wglCreateContext.argtypes = [c_void_p]
        opengl32.wglMakeCurrent.argtypes = [c_void_p, c_void_p]
        opengl32.wglDeleteContext.argtypes = [c_void_p]
        opengl32.wglGetProcAddress.restype = c_void_p
        opengl32.wglGetProcAddress.argtypes = [c_char_p]

    def _register(self, instance: Any) -> None:
        """The hidden window's class, once a process: Windows' own default
        window procedure, so no Python runs for its messages."""
        log.debug("WglSurface._register: registered=%s", WglSurface._registered)
        if WglSurface._registered:
            return
        procedure = ctypes.cast(self._user32.DefWindowProcW, c_void_p).value
        window_class = _WndClass(style=_CS_OWNDC, lpfnWndProc=procedure,
                                 hInstance=instance, lpszClassName=self._CLASS)
        if not self._user32.RegisterClassW(ctypes.byref(window_class)):
            raise OSError("OpenGL (WGL) could not register its window class")
        WglSurface._registered = True

    def address(self, name: str) -> int:
        # wglGetProcAddress answers for OpenGL past 1.1; 1.1 itself is
        # opengl32.dll's own exports.  It fails as 0, 1, 2, 3 or -1.
        found = self._opengl32.wglGetProcAddress(name.encode()) or 0
        address = (found if found not in (1, 2, 3, 2 ** 64 - 1, 2 ** 32 - 1)
                   else 0) or _symbol(self._opengl32, name)
        log.debug("WglSurface.address: %s -> %#x", name, address)
        return address

    def close(self) -> None:
        log.debug("WglSurface.close: open=%s", self._context is not None)
        if self._context:
            self._opengl32.wglMakeCurrent(None, None)
            self._opengl32.wglDeleteContext(self._context)
        if self._hdc:
            self._user32.ReleaseDC(self._hwnd, self._hdc)
        if self._hwnd:
            self._user32.DestroyWindow(self._hwnd)
        self._context = self._hdc = self._hwnd = None


# ── CGL: macOS ───────────────────────────────────────────────────────────
_CGL_PROFILE = 99                                   # kCGLPFAOpenGLProfile
#: OpenGL 4 core, then 3.2 core, then whatever is offered -- glcontext's
#: order.  macOS offers no OpenGL 3 that is not a core profile.
_CGL_PROFILES = (0x4100, 0x3200, None)
_OPENGL_FRAMEWORK = "/System/Library/Frameworks/OpenGL.framework/OpenGL"


class CglSurface(GlSurface):
    """A CGL context with no drawable at all: macOS lets a context draw
    into framebuffer objects with nothing on screen."""

    def __init__(self) -> None:
        log.info("CglSurface: opening")
        super().__init__()
        try:
            cgl = ctypes.CDLL(_OPENGL_FRAMEWORK)
        except OSError as e:
            raise OSError(f"OpenGL (CGL) is not available: {e}") from e
        cgl.CGLChoosePixelFormat.argtypes = [POINTER(c_int), POINTER(c_void_p),
                                             POINTER(c_int)]
        cgl.CGLCreateContext.argtypes = [c_void_p, c_void_p, POINTER(c_void_p)]
        cgl.CGLDestroyPixelFormat.argtypes = [c_void_p]
        cgl.CGLSetCurrentContext.argtypes = [c_void_p]
        cgl.CGLDestroyContext.argtypes = [c_void_p]
        self._cgl = cgl
        pixels, count = c_void_p(), c_int()
        for profile in _CGL_PROFILES:
            wanted = (_ints(_CGL_PROFILE, profile, 0) if profile is not None
                      else _ints(0))
            cgl.CGLChoosePixelFormat(wanted, ctypes.byref(pixels),
                                     ctypes.byref(count))
            log.info("CglSurface: profile %s -> %s",
                     hex(profile) if profile else "any", bool(pixels.value))
            if pixels.value:
                break
        if not pixels.value:
            raise OSError("OpenGL (CGL) found no pixel format to draw with")
        self._context = c_void_p()
        cgl.CGLCreateContext(pixels, None, ctypes.byref(self._context))
        cgl.CGLDestroyPixelFormat(pixels)
        if not self._context.value:
            raise OSError("OpenGL (CGL) could not make a context")
        cgl.CGLSetCurrentContext(self._context)
        self._ready()

    def address(self, name: str) -> int:
        address = _symbol(self._cgl, name)
        log.debug("CglSurface.address: %s -> %#x", name, address)
        return address

    def close(self) -> None:
        log.debug("CglSurface.close: open=%s", bool(self._context.value))
        if self._context.value:
            self._cgl.CGLSetCurrentContext(None)
            self._cgl.CGLDestroyContext(self._context)
            self._context = c_void_p()


# ── drawing helpers ──────────────────────────────────────────────────────
#: Every visual's GLSL, one ``.glsl`` file per shader -- the one home, so a
#: shader is read, packaged and reviewed the same way whichever visual draws
#: it.  Bodies only: :func:`program` puts the ``#version`` header on.
SHADERS = Path(__file__).resolve().parents[2] / "assets" / "shaders"


@functools.cache
def shader(name: str) -> str:
    """The GLSL in ``SHADERS/name`` -- read once, then kept."""
    text = (SHADERS / name).read_text(encoding="utf-8")
    log.debug("shader: %s, %d characters", name, len(text))
    return text


#: GLSL 1.30, the oldest OpenGL 3; 1.50 core where nothing older is offered
#: (macOS).  The fragment shader writes ``FRAG_COLOUR`` in either.
_HEADERS = (
    ("#version 130\n", "#version 130\n#define FRAG_COLOUR gl_FragColor\n"),
    ("#version 150\n#define attribute in\n",
     "#version 150\nout vec4 trcc_colour;\n#define FRAG_COLOUR trcc_colour\n"),
)


def _compiled(gl: Gl, kind: int, source: str) -> tuple[int, str]:
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


def program(gl: Gl, vertex: str, fragment: str,
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


def uniforms(gl: Gl, made: int, *names: str) -> dict[str, int]:
    """Where each named uniform of program *made* is (-1: unused there)."""
    found = {name: gl.glGetUniformLocation(made, name.encode()) for name in names}
    log.debug("uniforms: %s", found)
    return found


def texture(gl: Gl, width: int, height: int, internal: int, layout: int,
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

    def __init__(self, gl: Gl, width: int, height: int, depth: bool = False,
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

    def bind(self, gl: Gl) -> None:
        """Draw into this, all of it, from now on."""
        frame_log.debug("Framebuffer.bind: %d", self.name)
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.name)
        gl.glViewport(0, 0, self.width, self.height)

    def delete(self, gl: Gl) -> None:
        log.debug("Framebuffer.delete: %d", self.name)
        gl.glDeleteFramebuffers(1, ctypes.byref(c_uint(self.name)))
        gl.glDeleteTextures(1, ctypes.byref(c_uint(self.colour)))
        if self._depth:
            gl.glDeleteRenderbuffers(1, ctypes.byref(c_uint(self._depth)))


#: Every pixel, as two triangles: OpenGL's corner (-1..1), then the
#: picture's (0..1) -- what a full-frame pass draws.
FULL_QUAD = np.array([-1, -1, 0, 0, 1, -1, 1, 0, 1, 1, 1, 1,
                      -1, -1, 0, 0, 1, 1, 1, 1, -1, 1, 0, 1], dtype=np.float32)


def vertex_array(gl: Gl, sizes: tuple[int, ...]) -> tuple[int, int]:
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


def upload(gl: Gl, buffer: int, floats: np.ndarray) -> None:
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


def read_rgb(gl: Gl, framebuffer: int, width: int, height: int) -> bytes:
    """*framebuffer*'s picture as RGB24, top row first -- OpenGL's first
    row is the bottom one, a panel's the top."""
    gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer)
    gl.glPixelStorei(GL_PACK_ALIGNMENT, 1)
    rows = np.empty((height, width * 3), dtype=np.uint8)
    gl.glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE,
                    rows.ctypes.data)
    frame_log.debug("read_rgb: %d, %dx%d", framebuffer, width, height)
    return rows[::-1].tobytes()
