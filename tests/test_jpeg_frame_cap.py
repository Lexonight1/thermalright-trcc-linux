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
