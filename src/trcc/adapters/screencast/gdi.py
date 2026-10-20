"""``GdiScreenCapture`` -- Windows' own screen copy, the one the C# makes.

The C#'s screencast is ``Graphics.CopyFromScreen`` (FormCZTV.cs:3547), which is
GDI ``BitBlt`` from the screen's device context.  It needs no window and no Qt.
Qt cannot do this job in the shared App: there it runs ``-platform offscreen``
(``adapters/render/qt.py``), sees no screen, and ``QScreen.grabWindow``
returned a blank pixmap on every tick -- 11128 ERRORs in 13 minutes on the
win11 VM, and not one cast frame reached the panel (2026-10-20).  GDI has no
GUI-thread rule either, so the screencast driver's thread may call it.

``SRCCOPY`` alone, as ``CopyFromScreen``'s default ``SourceCopy`` is.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Any, NamedTuple

import numpy as np

from ...core.logs import per_frame
from ...core.models import RawFrame
from ...core.ports import ScreenCapture
from .qt import _check_region

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_SRCCOPY = 0x00CC0020
_BI_RGB = 0
_DIB_RGB_COLORS = 0


class _Gdi(NamedTuple):
    """user32 / gdi32 calls with their types declared, built once."""

    get_dc: Any
    release_dc: Any
    create_compatible_dc: Any
    create_compatible_bitmap: Any
    select_object: Any
    bit_blt: Any
    get_di_bits: Any
    delete_object: Any
    delete_dc: Any
    bitmap_info: Any
    last_error: Any


def _gdi() -> _Gdi:
    """Bind the calls -- typed, or ctypes passes 64-bit handles as 32-bit ints."""
    from ctypes import wintypes

    log.debug("_gdi: binding user32 / gdi32")

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    hdc, hgdi = wintypes.HDC, wintypes.HGDIOBJ
    signatures = (
        (user32.GetDC, [wintypes.HWND], hdc),
        (user32.ReleaseDC, [wintypes.HWND, hdc], ctypes.c_int),
        (gdi32.CreateCompatibleDC, [hdc], hdc),
        (gdi32.CreateCompatibleBitmap, [hdc, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        (gdi32.SelectObject, [hdc, hgdi], hgdi),
        (gdi32.BitBlt, [hdc, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        hdc, ctypes.c_int, ctypes.c_int, wintypes.DWORD], wintypes.BOOL),
        (gdi32.GetDIBits, [hdc, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wintypes.UINT],
         ctypes.c_int),
        (gdi32.DeleteObject, [hgdi], wintypes.BOOL),
        (gdi32.DeleteDC, [hdc], wintypes.BOOL),
    )
    for fn, argtypes, restype in signatures:
        fn.argtypes, fn.restype = argtypes, restype
    return _Gdi(user32.GetDC, user32.ReleaseDC, gdi32.CreateCompatibleDC,
                gdi32.CreateCompatibleBitmap, gdi32.SelectObject, gdi32.BitBlt,
                gdi32.GetDIBits, gdi32.DeleteObject, gdi32.DeleteDC, _BitmapInfo,
                ctypes.get_last_error)  # pyright: ignore[reportAttributeAccessIssue]


class GdiScreenCapture(ScreenCapture):
    """``BitBlt`` a region of the screen into a 32-bit DIB, then RGB24."""

    def __init__(self) -> None:
        log.info("GdiScreenCapture: Windows' own screen copy (GDI BitBlt)")
        self._api: _Gdi | None = None     # bound on the first grab, on Windows

    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        _check_region(width, height)
        frame_log.debug("GdiScreenCapture: grab (%d,%d) %dx%d", x, y, width, height)
        if self._api is None:
            self._api = _gdi()
        api = self._api
        screen = api.get_dc(None)
        if not screen:
            raise OSError(api.last_error(), "GetDC(screen) failed")
        memory = api.create_compatible_dc(screen)
        bitmap = api.create_compatible_bitmap(screen, width, height)
        try:
            if not memory or not bitmap:
                raise OSError(api.last_error(), "no device context or bitmap for the grab")
            previous = api.select_object(memory, bitmap)
            copied = api.bit_blt(memory, 0, 0, width, height, screen, x, y, _SRCCOPY)
            # GetDIBits wants the bitmap OUT of every device context.
            api.select_object(memory, previous)
            if not copied:
                raise OSError(api.last_error(), f"BitBlt of ({x},{y}) {width}x{height} failed")
            info = api.bitmap_info()
            header = info.bmiHeader
            header.biSize = 40
            header.biWidth, header.biHeight = width, -height     # negative: top row first
            header.biPlanes, header.biBitCount, header.biCompression = 1, 32, _BI_RGB
            pixels = bytearray(width * height * 4)
            address = ctypes.addressof((ctypes.c_ubyte * len(pixels)).from_buffer(pixels))
            rows = api.get_di_bits(memory, bitmap, 0, height, address, info, _DIB_RGB_COLORS)
            if rows != height:
                raise OSError(api.last_error(), f"GetDIBits read {rows} of {height} rows")
        finally:
            if bitmap:
                api.delete_object(bitmap)
            if memory:
                api.delete_dc(memory)
            api.release_dc(None, screen)
        bgra = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
        return RawFrame(data=bgra[:, :, 2::-1].tobytes(), width=width, height=height)

    def stop(self) -> None:
        """GDI holds nothing between grabs."""
        log.debug("GdiScreenCapture.stop: nothing held")
