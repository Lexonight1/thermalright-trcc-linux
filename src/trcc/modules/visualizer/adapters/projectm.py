"""projectM drawing an audio visual off-screen, on Linux.

projectM is a MilkDrop engine: fed sound, it draws a preset with OpenGL.  It
draws its finished picture into framebuffer 0 -- "the surface's own buffer"
-- so it needs a surface with one and no window: an EGL *pbuffer*.  Each
picture is read back with ``glReadPixels`` and handed on as RGB24, top row
first, like every other frame TRCC sends.

libprojectM 4's C API is called through ``ctypes``; it has been stable since
4.0 (``docs``: "the API will remain stable ... until the next major
release").  Every call comes from one thread: an OpenGL context belongs to
the thread that made it current.

**Credit.**  projectM (https://github.com/projectM-visualizer/projectm,
LGPL-2.1-or-later), by the projectM team (see its ``AUTHORS.txt``) -- a
reimplementation of Ryan Geiss's MilkDrop.  TRCC links to it as a library and
does not modify it.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from trcc.adapters.visual._gl import EglSurface, read_rgb
from trcc.core._safe import is_under
from trcc.core.logs import per_frame
from trcc.core.models import SOUND_CHANNELS, RawFrame

from ..core.models import VISUAL_FPS, VisualAdjustment, VisualInfo, VisualSpec
from ._base import BaseVisual

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A MilkDrop preset file.
PRESET_SUFFIX = ".milk"
#: libprojectM 4's names: what ``find_library`` resolves, then the soname.
_LIBRARY = ("projectM-4", "libprojectM-4.so.4")

def load_projectm() -> ctypes.CDLL:
    """libprojectM 4, or ``OSError`` saying it is not installed."""
    found = ctypes.util.find_library(_LIBRARY[0]) or _LIBRARY[1]
    log.info("load_projectm: %s", found)
    try:
        lib = ctypes.CDLL(found)
    except OSError as e:
        raise OSError("projectM is not installed (libprojectM 4) -- it is "
                      f"what draws audio visuals ({e})") from e
    lib.projectm_create.restype = ctypes.c_void_p
    for name, args in (
            ("projectm_destroy", [ctypes.c_void_p]),
            ("projectm_set_window_size",
             [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]),
            ("projectm_set_fps", [ctypes.c_void_p, ctypes.c_int32]),
            ("projectm_set_preset_locked", [ctypes.c_void_p, ctypes.c_bool]),
            ("projectm_set_beat_sensitivity", [ctypes.c_void_p, ctypes.c_float]),
            ("projectm_load_preset_file",
             [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]),
            ("projectm_pcm_add_float",
             [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_uint,
              ctypes.c_int]),
            ("projectm_opengl_render_frame", [ctypes.c_void_p])):
        getattr(lib, name).argtypes = args
    return lib


class MilkdropVisual(BaseVisual, key="milkdrop"):
    """A MilkDrop preset drawn by libprojectM on an EGL pbuffer; pictures
    read back as RGB24."""

    RANK = 90                     # its thousands of presets come last

    INFO = VisualInfo(
        kind="milkdrop", title="MilkDrop", category="MilkDrop",
        uses_presets=True,
        adjustments=(VisualAdjustment("beat_sensitivity", "Beat sensitivity",
                                      0.0, 5.0, 1.0),))

    #: Whether libprojectM can draw on this computer -- tried once.
    _draws: ClassVar[bool | None] = None

    @classmethod
    def available(cls) -> bool:
        """Only where libprojectM 4 is installed AND starts in the OpenGL
        this computer has -- tried once, so a library built for OpenGL ES
        (Arch's), or no OpenGL at all, hides MilkDrop rather than failing
        when the user picks it."""
        if cls._draws is None:
            cls._draws = cls._starts()
        log.debug("MilkdropVisual.available: %s", cls._draws)
        return cls._draws

    @classmethod
    def _starts(cls) -> bool:
        """Load libprojectM, make a surface, start projectM, put it away."""
        if ctypes.util.find_library(_LIBRARY[0]) is None:
            log.info("MilkdropVisual: libprojectM 4 is not installed")
            return False
        try:
            lib, surface = load_projectm(), EglSurface(16, 16, core=True)
        except OSError as e:
            log.warning("MilkdropVisual: cannot draw here -- %s", e)
            return False
        try:
            handle = lib.projectm_create()
            if handle:
                lib.projectm_destroy(handle)
        finally:
            surface.close()
        log.info("MilkdropVisual: projectM %s here",
                 "starts" if handle else "did not start")
        return bool(handle)

    @classmethod
    def presets(cls, folder: Path) -> tuple[str, ...]:
        """Every ``.milk`` file under *folder*, relative to it."""
        found = (tuple(sorted(str(p.relative_to(folder))
                              for p in folder.rglob(f"*{PRESET_SUFFIX}")))
                 if folder.is_dir() else ())
        log.info("MilkdropVisual.presets: %d under %s", len(found), folder)
        return found

    @classmethod
    def preset(cls, folder: Path, name: str) -> Path | None:
        """*name* under *folder* -- a ``.milk`` file that is there and does
        not climb out of it -- or None."""
        path = (folder / name).resolve()
        ok = (bool(name) and path.suffix == PRESET_SUFFIX and path.is_file()
              and is_under(path, folder))
        log.debug("MilkdropVisual.preset: %r -> %s", name, path if ok else None)
        return path if ok else None

    def __init__(self) -> None:
        log.debug("MilkdropVisual.__init__")
        self._lib: ctypes.CDLL | None = None
        self._surface: EglSurface | None = None
        self._handle: int | None = None
        self._size = (0, 0)

    def open(self, spec: VisualSpec) -> None:
        preset, width, height = spec.preset, spec.width, spec.height
        log.info("MilkdropVisual.open: %s at %dx%d", preset, width, height)
        if preset is None:
            raise OSError("a MilkDrop visual needs a preset file")
        self._lib = lib = load_projectm()
        self._surface = EglSurface(width, height, core=True)
        self._handle = lib.projectm_create()
        if not self._handle:
            self.close()
            raise OSError("projectM could not start")
        self._size = (width, height)
        lib.projectm_set_window_size(self._handle, width, height)
        lib.projectm_set_fps(self._handle, VISUAL_FPS)
        lib.projectm_set_preset_locked(self._handle, True)
        lib.projectm_set_beat_sensitivity(
            self._handle, float(spec.settings.get("beat_sensitivity", 1.0)))
        lib.projectm_load_preset_file(self._handle, str(preset).encode(), False)

    def adjust(self, settings: Mapping[str, float]) -> None:
        """Beat sensitivity: how hard the preset answers the beat."""
        value = float(settings.get("beat_sensitivity", 1.0))
        log.info("MilkdropVisual.adjust: beat sensitivity %.2f", value)
        if self._lib is not None and self._handle:
            self._lib.projectm_set_beat_sensitivity(self._handle, value)

    def feed(self, pcm: bytes) -> None:
        if self._lib is None or not self._handle or not pcm:
            frame_log.debug("MilkdropVisual.feed: nothing to feed")
            return
        frames = len(pcm) // (4 * SOUND_CHANNELS)
        samples = (ctypes.c_float * (frames * SOUND_CHANNELS)).from_buffer_copy(
            pcm[:frames * 4 * SOUND_CHANNELS])
        frame_log.debug("MilkdropVisual.feed: %d frame(s)", frames)
        self._lib.projectm_pcm_add_float(self._handle, samples, frames,
                                         SOUND_CHANNELS)

    def render(self) -> RawFrame:
        if self._lib is None or self._surface is None or not self._handle:
            raise OSError("projectM is not open")
        width, height = self._size
        self._lib.projectm_opengl_render_frame(self._handle)
        # projectM binds only the DRAW framebuffer to 0 for its finished
        # picture, and leaves READ on its own buffer -- the image before the
        # preset's final shader.  Reading that showed every preset without
        # its composite pass (a red test shader came back black); read_rgb
        # binds READ to 0 itself.
        data = read_rgb(self._surface.gl, 0, width, height)
        frame_log.debug("MilkdropVisual.render: %dx%d", width, height)
        return RawFrame(data=data, width=width, height=height)

    def close(self) -> None:
        log.info("MilkdropVisual.close: open=%s", self._handle is not None)
        if self._lib is not None and self._handle:
            self._lib.projectm_destroy(self._handle)
        self._handle = None
        if self._surface is not None:
            self._surface.close()
            self._surface = None
