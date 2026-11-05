"""The audio spectrum is a FEATURE, not a gui decoration.

`screencast_region`'s fifth element is the audio flag, and it has always been
universal: the CLI, the API and qtgui can all set it, `StartScreencast` carries
it, `ScreencastStarted` publishes it, and it is persisted and baked into saved
themes.  What was NOT universal was drawing anything — `_draw_spectrum` was a
`QPainter` block inside `ui/gui`'s capture tick, so three of the four faces set
the flag and never produced a bar.

The bars now go into the WIRE frame, which is the only place all four faces
share.  These tests pin the two halves that can silently rot:

* **off must cost nothing** — a byte-identical frame, or every non-audio
  screencast in the world just changed;
* **on must reach the wire** — asserted on the returned BYTES, not on a
  preview surface and not on a call count, because "the compositor was asked"
  is exactly what a mis-ordered or no-op draw satisfies.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.media.ffmpeg import FfmpegTool
from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import Kind, ProductInfo, RawFrame, Theme, Wire
from trcc.core.protocol import DeviceProfile
from trcc.services.background import BackgroundSlot
from trcc.services.display import DisplayService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

from .conftest import FakePaths

SIZE = (320, 320)
PROFILE = DeviceProfile(320, 320, big_endian=True)
#: Mid-band peaks, so bars land away from the edges and the ramp is exercised.
LEVELS = (0.0, 0.2, 0.55, 0.95, 1.0, 0.4, 0.1, 0.0)


def _info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi", product="320x320",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=SIZE, orientations=(0, 90, 180, 270),
    )


@pytest.fixture
def renderer() -> QtRenderer:
    return QtRenderer()


@pytest.fixture
def display(renderer: QtRenderer, tmp_home: Path) -> DisplayService:
    paths = FakePaths(tmp_home)
    return DisplayService(
        renderer=renderer, themes=FileContentStore(paths),
        overlay=OverlayService(renderer), settings=Settings(paths),
        media=MediaService(video_tool=FfmpegTool()), backgrounds=BackgroundSlot(), paths=paths,
    )


def _capture() -> RawFrame:
    """A uniform dark-blue grab — any bar drawn on it is unmistakable."""
    w, h = SIZE
    return RawFrame(data=bytes([10, 10, 60] * (w * h)), width=w, height=h)


def _theme(tmp_home: Path) -> Theme:
    d = tmp_home / "t"
    d.mkdir(parents=True, exist_ok=True)
    return Theme(path=d, name="t", resolution=SIZE, config={"elements": []})


# ── off costs nothing ──────────────────────────────────────────────────────

def test_no_audio_is_byte_identical(
    display: DisplayService, tmp_home: Path,
) -> None:
    info, theme = _info(), _theme(tmp_home)
    baseline = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme, profile=PROFILE)
    display.invalidate_all()
    with_empty = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme, spectrum=(),
        profile=PROFILE)
    assert with_empty == baseline


# ── on reaches the wire ────────────────────────────────────────────────────

def test_spectrum_changes_the_wire_bytes(
    display: DisplayService, tmp_home: Path,
) -> None:
    info, theme = _info(), _theme(tmp_home)
    silent = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme, profile=PROFILE)
    display.invalidate_all()
    loud = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme, spectrum=LEVELS,
        profile=PROFILE)
    assert loud != silent, "the spectrum never reached the wire frame"


def test_different_levels_give_different_frames(
    display: DisplayService, tmp_home: Path,
) -> None:
    """A meter that does not move with the signal is a picture of a meter."""
    info, theme = _info(), _theme(tmp_home)
    quiet = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme,
        spectrum=(0.1,) * 8, profile=PROFILE)
    display.invalidate_all()
    loud = display.build_screencast_frame(
        info=info, frame=_capture(), theme=theme,
        spectrum=(0.9,) * 8, profile=PROFILE)
    assert quiet != loud


# ── the drawing itself ─────────────────────────────────────────────────────

def test_bars_occupy_the_bottom_quarter_only(renderer: QtRenderer) -> None:
    """Geometry, on real pixels — ported from the gui's ``_draw_spectrum``."""
    w, h = 320, 320
    surface = renderer.create_surface(w, h, color=(0, 0, 0, 255))
    renderer.draw_spectrum(surface, (1.0,) * 16)
    px = renderer.get_pixels_rgb(surface, 8, 8)
    # Row 0..5 of an 8-row sample sit above the bottom quarter.
    assert all(px[row][col] == (0, 0, 0) for row in range(5) for col in range(8)), (
        "spectrum painted above the bottom quarter")
    assert any(px[7][col] != (0, 0, 0) for col in range(8)), (
        "nothing painted at the bottom")


def test_level_drives_bar_height(renderer: QtRenderer) -> None:
    w, h = 320, 320
    tall = renderer.create_surface(w, h, color=(0, 0, 0, 255))
    renderer.draw_spectrum(tall, (1.0,) * 16)
    short = renderer.create_surface(w, h, color=(0, 0, 0, 255))
    renderer.draw_spectrum(short, (0.05,) * 16)

    def lit(surface: object) -> int:
        rows = renderer.get_pixels_rgb(surface, 16, 16)
        return sum(1 for r in rows for p in r if p != (0, 0, 0))

    assert lit(tall) > lit(short), "bar height does not follow the level"


def test_empty_spectrum_paints_nothing(renderer: QtRenderer) -> None:
    surface = renderer.create_surface(64, 64, color=(0, 0, 0, 255))
    before = renderer.encode_png(surface)
    renderer.draw_spectrum(surface, ())
    assert renderer.encode_png(surface) == before


def test_levels_outside_the_range_are_clamped(renderer: QtRenderer) -> None:
    """``get_spectrum`` is FFT output; a transient can overshoot 1.0.

    Unclamped it produces a bar taller than the frame and a negative colour
    channel, which raises deep inside the toolkit rather than here.
    """
    surface = renderer.create_surface(64, 64, color=(0, 0, 0, 255))
    renderer.draw_spectrum(surface, (-0.5, 1.8, 0.5))   # must not raise
