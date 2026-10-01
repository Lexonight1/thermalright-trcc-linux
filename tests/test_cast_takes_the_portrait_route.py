"""A cast or a picture at 90/270 is sent the way a portrait theme is.

The C# sizes a supplied picture's canvas to the orientation (``GIFSize``,
FormCZTV.cs:3100-3557: 240x320 at 90/270 on a 320x240 panel) and puts it in
the background slot (``bitmapBGK``), so on the wire it IS a portrait frame.
Ours composed it landscape and rode ``wire_angle`` -- a route neither theme
path takes -- so on the base-90 panels (FBL 50 51 52 53 58 64) a cast at
90/270 reached the glass sideways.

The oracle here is not arithmetic but our own portrait-theme route, which
ships (``Theme240320``, ``Theme480640``) and was confirmed on glass (#234):
the same picture as a portrait theme's background and as a cast must put the
same bytes on the wire.

MUTATION CHECK -- in ``core.geometry.oriented_canvas`` pass ``False`` to
``plan_orientation`` again and every 90/270 case here fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.geometry import plan_orientation
from trcc.core.models import Kind, ProductInfo, RawFrame, Theme, Wire
from trcc.core.protocol import get_profile

_BASE_90 = (50, 51, 52, 53, 58, 64)


def _info(native: tuple[int, int]) -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="Thermalright",
        product=f"LCD {native[0]}x{native[1]}", wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=0, native_resolution=native,
        orientations=(0, 90, 180, 270),
    )


def _display(tmp_home: Path) -> object:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.adapters.theme.filesystem import FileContentStore
    from trcc.services.background import BackgroundSlot
    from trcc.services.display import DisplayService
    from trcc.services.media import MediaService
    from trcc.services.overlay import OverlayService
    from trcc.services.settings import Settings

    from .conftest import FakePaths
    paths = FakePaths(tmp_home)
    renderer = QtRenderer()
    return DisplayService(
        renderer=renderer, themes=FileContentStore(),
        overlay=OverlayService(renderer), settings=Settings(paths),
        media=MediaService(), backgrounds=BackgroundSlot(), paths=paths,
    )


def _picture(path: Path, size: tuple[int, int]) -> RawFrame:
    """An asymmetric gradient -- any turn or flip changes its bytes."""
    from PySide6.QtGui import QColor, QImage

    w, h = size
    image = QImage(w, h, QImage.Format.Format_RGB888)
    for y in range(h):
        for x in range(w):
            image.setPixelColor(x, y, QColor((x * 7) % 256, (y * 5) % 256,
                                             (x * y) % 256))
    path.parent.mkdir(parents=True)
    assert image.save(str(path))
    data = bytes(image.constBits())[: w * h * 3]
    assert image.bytesPerLine() == w * 3, "rows must be unpadded for RawFrame"
    return RawFrame(data=data, width=w, height=h)


@pytest.mark.parametrize("fbl", _BASE_90)
@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
def test_a_cast_and_a_picture_send_what_a_portrait_theme_sends(
    fbl: int, orientation: int, tmp_home: Path,
) -> None:
    profile = get_profile(fbl)          # what a handshake hands the App
    canvas = plan_orientation(profile, orientation, True).canvas
    w, h = profile.resolution
    folder = tmp_home / f"theme{min(w, h)}{max(w, h)}" / "T"   # the portrait catalog
    raw = _picture(folder / "00.png", canvas)
    display = _display(tmp_home)
    info = _info(profile.resolution)
    display._settings.set_orientation(info.key, orientation)   # type: ignore[attr-defined]

    theme = Theme(path=folder, name="T", resolution=canvas, config={"elements": []})
    as_theme = display.build_frame(info=info, theme=theme, sensors={},  # type: ignore[attr-defined]
                                   profile=profile)
    as_cast = display.build_screencast_frame(info=info, frame=raw,  # type: ignore[attr-defined]
                                             profile=profile)
    as_image = display.build_image_frame(info=info, path=folder / "00.png",  # type: ignore[attr-defined]
                                         profile=profile)

    assert as_cast == as_theme, f"fbl {fbl} at {orientation}: the cast took another route"
    assert as_image == as_theme, f"fbl {fbl} at {orientation}: the picture took another route"
