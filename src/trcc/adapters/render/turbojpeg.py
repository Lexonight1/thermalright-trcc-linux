"""JPEG frames at full colour resolution -- libjpeg-turbo's TurboJPEG API.

Qt writes JPEG through libjpeg too, but chooses the colour sampling itself:
4:4:4 at quality 91 and up, 4:2:0 at 90 and below (measured on Qt 6.12), with
no option to ask otherwise.  ``encode_jpeg`` lowers the quality exactly when a
frame would not fit the panel's 450 KB ceiling, so a busy frame lost its
colour resolution and a saturated edge -- a red gauge over a live theme --
fringed on the panel.  Found and measured by Alex Shutov (#336).

TurboJPEG is libjpeg-turbo's own small C API: one call compresses at a chosen
quality AND sampling.  It is the same engine Pillow wraps (same bytes, same
~2 ms on a 1920x462 frame), reached through ``ctypes`` instead of a Python
package -- "less is more".  Where the library is missing (pip on a bare
system, Windows and macOS installers today) :func:`load` answers None and the
caller keeps Qt's encoder.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
import threading
from functools import cache
from typing import Any

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: TurboJPEG's chroma sampling values (``TJSAMP_*``).
SAMP_444, SAMP_420 = 0, 2
#: The pixel layout of a Qt ``Format_RGB32`` image in memory -- a 32-bit
#: 0xffRRGGBB word, so B,G,R,X bytes on a little-endian machine
#: (``TJPF_BGRX``) and X,R,G,B on a big-endian one (``TJPF_XRGB``).
_PIXEL_FORMAT = 3 if sys.byteorder == "little" else 5
#: The library's full path, for a package that knows it.  NixOS has no
#: ldconfig, gcc or ld for ``find_library`` to ask, so it answers None there
#: even with LD_LIBRARY_PATH set; flake.nix rewrites this line to the store
#: path at build time (``--replace-fail``: renaming it breaks that build).
LIBRARY: str | None = None


class TurboJpeg:
    """One TurboJPEG compressor.  Encodes are serialised: a TurboJPEG handle
    is not shared between threads, and panels and the preview encode on
    their own threads."""

    def __init__(self, lib: Any) -> None:
        self._lib = lib
        lib.tjInitCompress.restype = ctypes.c_void_p
        lib.tjCompress2.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_int, ctypes.c_int,
            ctypes.c_int]
        lib.tjCompress2.restype = ctypes.c_int
        lib.tjFree.argtypes = [ctypes.c_void_p]
        lib.tjGetErrorStr2.argtypes = [ctypes.c_void_p]
        lib.tjGetErrorStr2.restype = ctypes.c_char_p
        self._handle = lib.tjInitCompress()
        if not self._handle:
            raise OSError("tjInitCompress returned no handle")
        self._lock = threading.Lock()
        log.info("TurboJpeg: compressor ready")

    def encode(self, surface: Any, quality: int, sampling: int = SAMP_444) -> bytes:
        """*surface* (a QImage) as JPEG bytes at *quality* and *sampling*.

        Converted to ``Format_RGB32`` first, as ``encode_rgb565`` does: a
        premultiplied surface handed over raw comes out darkened wherever
        something was painted with transparency."""
        from PySide6.QtGui import QImage

        if surface.format() != QImage.Format.Format_RGB32:
            surface = surface.convertToFormat(QImage.Format.Format_RGB32)
        pixels = bytes(surface.constBits())
        width, height, pitch = surface.width(), surface.height(), surface.bytesPerLine()
        out, size = ctypes.c_void_p(), ctypes.c_ulong(0)
        with self._lock:
            rc = self._lib.tjCompress2(self._handle, pixels, width, pitch, height,
                                       _PIXEL_FORMAT, ctypes.byref(out),
                                       ctypes.byref(size), sampling, quality, 0)
            if rc != 0:
                why = (self._lib.tjGetErrorStr2(self._handle) or b"").decode(errors="replace")
                raise OSError(f"tjCompress2 failed: {why}")
        try:
            data = ctypes.string_at(out, size.value)
        finally:
            self._lib.tjFree(out)
        frame_log.debug("TurboJpeg.encode: %dx%d q=%d samp=%d -> %d bytes",
                        width, height, quality, sampling, len(data))
        return data


@cache
def load() -> TurboJpeg | None:
    """This system's TurboJPEG, made once -- None when there is none, and
    JPEG frames stay on Qt's encoder (said once, in the log)."""
    name = LIBRARY or ctypes.util.find_library("turbojpeg")
    log.info("turbojpeg: %s", f"packaged path {LIBRARY}" if LIBRARY
             else f"find_library answered {name}")
    if name is None:
        log.info("turbojpeg: no libturbojpeg on this system -- JPEG frames use "
                 "Qt's encoder (4:2:0 below quality 91)")
        return None
    try:
        encoder = TurboJpeg(ctypes.CDLL(name))
    except (OSError, AttributeError) as e:
        log.warning("turbojpeg: %s found but not usable (%s) -- JPEG frames use "
                    "Qt's encoder", name, e)
        return None
    log.info("turbojpeg: %s -- JPEG frames keep full colour (4:4:4)", name)
    return encoder
