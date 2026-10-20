"""``GdiScreenCapture`` -- Windows' screen copy, driven on any OS with GDI stubbed.

The shared App's Qt runs offscreen and every ``QScreen.grabWindow`` came back
blank on the win11 VM (2026-10-20); the C# copies the screen with GDI
(``CopyFromScreen``).  These pin our side of GDI's contract: what is copied
from where, the DIB's layout, the colour order, and that every object made
for a grab is released.  Whether Windows paints those pixels is the VM's to
show.
"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.screencast import gdi
from trcc.adapters.screencast.gdi import GdiScreenCapture


class _FakeGdi:
    """user32 / gdi32 for one 2x2 screen region, recorded."""

    SCREEN, MEMORY, BITMAP, ORIGINAL = 11, 22, 33, 44
    #: Row-major, top row first, BGRA: blue-ish, green, red, white.
    PIXELS = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255])

    def __init__(self, blt_ok: bool = True) -> None:
        self.blt_ok = blt_ok
        self.calls: list[tuple[Any, ...]] = []
        self.selected: int | None = self.ORIGINAL
        self.released: list[str] = []

    def api(self) -> Any:
        return gdi._Gdi(
            get_dc=lambda hwnd: self.SCREEN,
            release_dc=lambda hwnd, dc: self.released.append("screen"),
            create_compatible_dc=lambda dc: self.MEMORY,
            create_compatible_bitmap=lambda dc, w, h: self.BITMAP,
            select_object=self._select,
            bit_blt=self._blt,
            get_di_bits=self._get_di_bits,
            delete_object=lambda obj: self.released.append("bitmap"),
            delete_dc=lambda dc: self.released.append("memory"),
            bitmap_info=lambda: SimpleNamespace(bmiHeader=SimpleNamespace()),
            last_error=lambda: 6,
        )

    def _select(self, dc: int, obj: int) -> int:
        previous, self.selected = self.selected, obj
        return previous or 0

    def _blt(self, *args: Any) -> int:
        self.calls.append(("BitBlt", *args))
        return 1 if self.blt_ok else 0

    def _get_di_bits(self, dc: int, bitmap: int, start: int, rows: int, address: int,
                     info: Any, usage: int) -> int:
        h = info.bmiHeader
        self.calls.append(("GetDIBits", bitmap, rows, self.selected, h.biWidth,
                           h.biHeight, h.biBitCount))
        ctypes.memmove(address, self.PIXELS, len(self.PIXELS))
        return rows


def _capture(monkeypatch: pytest.MonkeyPatch, fake: _FakeGdi) -> GdiScreenCapture:
    monkeypatch.setattr(gdi, "_gdi", fake.api)
    return GdiScreenCapture()


def test_a_grab_copies_the_region_and_hands_over_rgb(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTATION CHECK: keep BGRA order -- the first pixel reads (255, 0, 0)."""
    fake = _FakeGdi()

    frame = _capture(monkeypatch, fake).grab_region(100, 200, 2, 2)

    assert (frame.width, frame.height) == (2, 2)
    assert frame.data == bytes([0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255])
    assert fake.calls == [
        ("BitBlt", fake.MEMORY, 0, 0, 2, 2, fake.SCREEN, 100, 200, 0x00CC0020),
        # The bitmap is back OUT of the DC (ORIGINAL selected), a top-down 32-bit DIB.
        ("GetDIBits", fake.BITMAP, 2, fake.ORIGINAL, 2, -2, 32),
    ]
    assert sorted(fake.released) == ["bitmap", "memory", "screen"]


def test_a_failed_copy_raises_and_still_releases_everything(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTATION CHECK: drop the ``finally`` -- the DC and bitmap leak, per tick."""
    fake = _FakeGdi(blt_ok=False)

    with pytest.raises(OSError, match="BitBlt"):
        _capture(monkeypatch, fake).grab_region(0, 0, 2, 2)

    assert sorted(fake.released) == ["bitmap", "memory", "screen"]


def test_windows_captures_with_gdi_not_qt() -> None:
    """The shared App's Qt is offscreen: Windows must not hand capture to it.

    MUTATION CHECK: drop WindowsPlatform._build_screen_capture -- Qt's grab."""
    from trcc.adapters.system import windows

    assert type(windows.WindowsPlatform()._build_screen_capture()) is GdiScreenCapture
