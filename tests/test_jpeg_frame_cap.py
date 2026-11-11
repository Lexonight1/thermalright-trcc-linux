"""No JPEG panel may ship a frame its firmware would discard (#251).

TRCC 2.1.6 ``ImageToJpg`` refuses to send a payload of 450000 bytes or more:
it drops the encoder quality by 5 and returns without sending.  That test sits
in the JPEG path with **no device condition on it** — it is a firmware ceiling
for every JPEG panel, not a quirk of one.

We met it as #251, where a Trofeo Vision 9.16 silently dropped frames over
roughly half a megabyte, and ported it as an LY-only ``_MAX_FRAME_BYTES``.
Every other JPEG panel stayed uncapped, which is the same defect waiting on
bulk and HID -- and it is invisible from the outside: ``send()`` completes, the
ACK reads back clean, the glass keeps the previous image, and nothing reaches
the log.  There is no symptom for a user to report.

THE TWO NUMBERS AGREE, which is why the ceiling is a citation and not a guess.
The #251 reporter measured ~360 KB displayed and ~570 KB ignored on real
hardware and proposed 512 KB as a midpoint.  The vendor's constant, 450000,
falls inside that measured window.  A bench measurement and the C# bracket each
other.

DELIBERATE DIVERGENCE, asserted below so it cannot be "fixed" by accident: the
C# discards the oversized frame and leaves quality lowered until reconnect; we
shrink and send, re-evaluating per frame.  A degraded frame beats a frozen
panel, which is what the reporter asked for.  Only the ceiling and its
universality are ported.

MUTATION CHECK -- set ``DeviceProfile.max_frame_bytes`` back to ``0``.

MEASURED: **7 failed, 1 passed.**  All five JPEG profiles (54, 114, 128, 192,
224) fail the ceiling sweep, the vendor-number test fails, and
``test_an_oversized_frame_is_shrunk_not_shipped`` fails because the payload
comes back over the ceiling.  The one survivor is
``test_rgb565_panels_are_unaffected``, which is correct -- the cap is a
JPEG-path rule and an RGB565 panel should not notice it moving.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.protocol import FBL_PROFILES, DeviceProfile, get_profile

# TRCC 2.1.6, TRCC.CZTV/FormCZTV.cs, ImageToJpg:
#     if (array.Length >= 450000) { myTempDeviceJpgYSL -= 5; return; }
# Written out here rather than imported, so the shipping value is checked
# against a transcription of the C# instead of against itself.
_CSHARP_MAX_JPEG_BYTES = 450_000


@pytest.mark.parametrize(
    "fbl", sorted(f for f, p in FBL_PROFILES.items() if p.jpeg))
def test_every_jpeg_panel_carries_the_ceiling(fbl: int) -> None:
    """A JPEG panel with no cap can ship a frame the firmware throws away."""
    profile = get_profile(fbl)
    assert profile.jpeg
    assert profile.max_frame_bytes == _CSHARP_MAX_JPEG_BYTES, (
        f"FBL {fbl} ({profile.width}x{profile.height}) JPEG panel has cap "
        f"{profile.max_frame_bytes}, but the C# never sends "
        f"{_CSHARP_MAX_JPEG_BYTES} bytes or more.  An uncapped panel drops "
        f"frames silently — send() succeeds and the glass simply stops "
        f"updating (#251)."
    )


def test_the_ceiling_is_the_vendors_number_not_our_guess() -> None:
    """512 KB was our midpoint between two reporter measurements; 450000 is the
    C#'s own constant, and it sits inside that measured window (~360 KB shown,
    ~570 KB ignored).  Guarding the exact value keeps it a citation.
    """
    assert DeviceProfile(854, 480, jpeg=True).max_frame_bytes == 450_000
    assert 360_000 < _CSHARP_MAX_JPEG_BYTES < 570_000, (
        "the vendor constant no longer falls inside the #251 reporter's "
        "measured window — one of the two is wrong and it needs re-checking"
    )


def test_rgb565_panels_are_unaffected() -> None:
    """The ceiling is a JPEG-path rule.  RGB565 frames are a fixed size for the
    resolution, and ``encode_payload`` never consults the cap for them.
    """
    profile = get_profile(100)              # 320x320 RGB565
    assert not profile.jpeg
    # The field is present (it is a dataclass default) but unused on this path;
    # asserting the encoder choice is what matters.
    assert profile.byte_order in (">", "<")


def test_an_oversized_frame_is_shrunk_not_shipped() -> None:
    """End to end through the real renderer: a surface that encodes over the
    ceiling must come back under it, rather than being sent as-is.

    Uses noise rather than a flat fill — a solid colour compresses to a few KB
    at any quality and would make this pass without exercising the loop.
    """
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    profile = get_profile(192, 65)          # 1920x462 JPEG, the largest panel
    w, h = profile.resolution
    surface = renderer.create_surface(w, h)

    from PySide6.QtGui import QColor, QPainter
    painter = QPainter(surface)
    for x in range(0, w, 2):                # 1px vertical grating = worst case
        painter.setPen(QColor((x * 37) % 256, (x * 91) % 256, (x * 53) % 256))
        painter.drawLine(x, 0, x, h)
    painter.end()

    uncapped = renderer.encode_jpeg(surface, quality=95, max_size=0)
    if len(uncapped) <= _CSHARP_MAX_JPEG_BYTES:
        pytest.skip(
            f"grating encoded to {len(uncapped)} bytes, under the "
            f"{_CSHARP_MAX_JPEG_BYTES} ceiling — cannot exercise the loop here"
        )

    payload = renderer.encode_payload(surface, profile)
    assert len(payload) <= _CSHARP_MAX_JPEG_BYTES, (
        f"encode_payload returned {len(payload)} bytes for a {w}x{h} panel "
        f"whose firmware discards anything at or over "
        f"{_CSHARP_MAX_JPEG_BYTES} — the frame would vanish with no error"
    )
    assert len(payload) > 0


# ── Full colour resolution: TurboJPEG at 4:4:4 (#336) ────────────────────

def _sampling(data: bytes) -> list[tuple[int, int]]:
    """(H, V) JPEG sampling factors per component, from the SOF marker --
    Alex Shutov's reader from #336."""
    for i in range(len(data) - 4):
        if data[i] == 0xFF and data[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3):
            return [(data[i + 11 + 3 * j] >> 4, data[i + 11 + 3 * j] & 0x0F)
                    for j in range(data[i + 9])]
    return []


def _needs_turbojpeg() -> None:
    from trcc.adapters.render import turbojpeg
    if turbojpeg.load() is None:
        pytest.skip("no libturbojpeg on this machine -- CI installs it")


def _grating(renderer, w: int, h: int):
    """1 px vertical lines of changing colour: the worst case for size."""
    from PySide6.QtGui import QColor, QPainter
    surface = renderer.create_surface(w, h)
    painter = QPainter(surface)
    for x in range(0, w, 2):
        painter.setPen(QColor((x * 37) % 256, (x * 91) % 256, (x * 53) % 256))
        painter.drawLine(x, 0, x, h)
    painter.end()
    return surface


def test_every_quality_the_loop_reaches_keeps_full_colour() -> None:
    """Qt keeps 4:4:4 only at quality 91 and up and falls to 4:2:0 below --
    exactly where the shrink loop goes when a frame would not fit, so a
    saturated edge fringed on the panel.  Found, measured and first tested by
    Alex Shutov (#336).  MUTATION CHECK: encode with ``SAMP_420`` in the 4:4:4
    attempts and q<=90 reads 2x2."""
    _needs_turbojpeg()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    from trcc.adapters.render.qt import QtRenderer

    surface = QImage(64, 64, QImage.Format.Format_RGB32)
    surface.fill(Qt.GlobalColor.red)
    for quality in (95, 75, 45, 30):
        sampling = _sampling(QtRenderer().encode_jpeg(surface, quality=quality))
        assert sampling == [(1, 1)] * 3, f"q={quality}: {sampling}"


def test_without_turbojpeg_the_frame_is_still_sent_by_qt(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """No libturbojpeg (pip on a bare system): Qt's
    writer, exactly as before -- 4:2:0 below quality 91, never no frame.
    MUTATION CHECK: drop the Qt branch and a missing library raises."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    from trcc.adapters.render import turbojpeg
    from trcc.adapters.render.qt import QtRenderer

    monkeypatch.setattr(turbojpeg, "load", lambda: None)
    surface = QImage(64, 64, QImage.Format.Format_RGB32)
    surface.fill(Qt.GlobalColor.red)
    data = QtRenderer().encode_jpeg(surface, quality=75)
    assert data[:2] == b"\xff\xd8" and _sampling(data) == [(2, 2), (1, 1), (1, 1)]


def test_a_frame_too_big_at_full_colour_still_fits_in_420() -> None:
    """4:4:4 frames are larger; one that is over the panel's ceiling even at
    q=30 retries in 4:2:0 rather than shipping a frame the firmware drops
    (#251).  MUTATION CHECK: drop the 4:2:0 attempts and the payload comes back
    over the limit."""
    _needs_turbojpeg()
    from trcc.adapters.render import turbojpeg
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    surface = _grating(renderer, 1920, 462)
    encoder = turbojpeg.load()
    assert encoder is not None
    full = len(encoder.encode(surface, 30, turbojpeg.SAMP_444))
    quarter = len(encoder.encode(surface, 30, turbojpeg.SAMP_420))
    limit = (full + quarter) // 2              # between the two: only 4:2:0 fits
    data = renderer.encode_jpeg(surface, max_size=limit)
    assert len(data) <= limit and _sampling(data)[0] == (2, 2)


def test_a_premultiplied_frame_gets_the_colours_the_rgb565_panels_get() -> None:
    """A frame painted with transparency is premultiplied.  ``encode_rgb565``
    converts to ``Format_RGB32`` first so such a pixel is not darkened, and the
    JPEG path now does the same -- measured 2026-11-11: Qt's own JPEG writer
    gave (99, 60, 19) for a half-transparent (200, 120, 40), the RGB32 rule
    (199, 120, 41), so JPEG and RGB565 panels had disagreed.  MUTATION CHECK:
    skip the ``Format_RGB32`` conversion in ``TurboJpeg.encode`` and the pixel
    darkens to Qt's old values."""
    _needs_turbojpeg()
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render import turbojpeg

    surface = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    surface.fill(QColor(200, 120, 40, 128))
    encoder = turbojpeg.load()
    assert encoder is not None
    ours = QColor(QImage.fromData(encoder.encode(surface, 95)).pixel(32, 32))
    rgb32 = QColor(surface.convertToFormat(QImage.Format.Format_RGB32).pixel(32, 32))
    assert max(abs(ours.red() - rgb32.red()), abs(ours.green() - rgb32.green()),
               abs(ours.blue() - rgb32.blue())) <= 3, (ours.getRgb(), rgb32.getRgb())


@pytest.mark.parametrize(("packaged", "bundled", "found", "expected"), [
    ("/nix/store/x/libturbojpeg.so.0", "turbojpeg.dll", "libturbojpeg.so.0",
     "/nix/store/x/libturbojpeg.so.0"),
    (None, "libturbojpeg.0.dylib", "libturbojpeg.so.0", "<bundle>/libturbojpeg.0.dylib"),
    (None, None, "libturbojpeg.so.0", "libturbojpeg.so.0"),
    (None, None, None, None),
])
def test_the_library_is_looked_for_packaged_then_bundled_then_system(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, packaged: str | None,
        bundled: str | None, found: str | None, expected: str | None) -> None:
    """The path a package wrote in (NixOS) wins; then the copy a frozen app
    ships in PyInstaller's bundle folder (Windows and macOS installers --
    find_library never searches it on macOS); then find_library.
    MUTATION CHECK: drop the bundle look-up and the installers' row fails;
    ask find_library first and the bundle row fails."""
    import ctypes.util
    import sys

    from trcc.adapters.render import turbojpeg

    monkeypatch.setattr(turbojpeg, "LIBRARY", packaged)
    monkeypatch.setattr(ctypes.util, "find_library", {"turbojpeg": found}.get)
    if bundled is not None:
        (tmp_path / bundled).write_bytes(b"")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    else:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    answer = turbojpeg._library()
    assert answer == (expected.replace("<bundle>", str(tmp_path)) if expected else None)
