"""SendColor Command + DisplayService.build_solid_color_frame tests.

End-to-end coverage of the smallest device-touching path: a solid-color
frame from a hex code on the wire. Verifies:

  1. ``DisplayService.build_solid_color_frame`` honors profile rotation
     and encoding.
  2. ``SendColor`` Command validates RGB range, looks up the device,
     refuses pre-handshake, dispatches into the display + transport,
     publishes ``FrameSent`` on success.
  3. CLI hex parsing accepts ``ff0000`` and ``#ff0000`` and rejects
     malformed input.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.next.app import App
from trcc.next.core.commands import ConnectDevice, SendColor
from trcc.next.core.events import FrameSent
from trcc.next.core.models import Kind, ProductInfo, Wire
from trcc.next.core.ports import Renderer
from trcc.next.core.protocol import get_profile
from trcc.next.services.display import DisplayService
from trcc.next.services.media import MediaService
from trcc.next.services.overlay import OverlayService
from trcc.next.services.settings import Settings
from trcc.next.services.theme import ThemeService
from trcc.next.ui.cli.display import _parse_hex_color

from .conftest import FakePaths, FakePlatform

# ── Recording renderer (same shape as test_display_rotation) ──────────


class _Surface:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h


class RecordingRenderer(Renderer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        self._record("create_surface", width, height, color)
        return _Surface(width, height)

    def open_image(self, path: Path) -> Any:
        return _Surface(100, 100)

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        self._record("rotate", surface, degrees)
        if degrees % 180 == 90:
            return _Surface(surface.h, surface.w)
        return _Surface(surface.w, surface.h)

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        self._record("apply_brightness", surface, percent)
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        pass

    def encode_rgb565(self, surface: Any) -> bytes:
        self._record("encode_rgb565", surface)
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        self._record("encode_jpeg", surface, quality)
        return b"\xff\xd8" + b"\x00" * 100

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _Surface(frame.width, frame.height)


class _StubOverlay(OverlayService):
    def render(self, canvas: Any, config: Any, sensors: dict[str, float],
               clock: dict[str, str] | None = None,
               user_elements: list[dict[str, Any]] | None = None) -> Any:
        del config, sensors, clock, user_elements
        return canvas


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def renderer() -> RecordingRenderer:
    return RecordingRenderer()


@pytest.fixture
def display(renderer: RecordingRenderer, tmp_home: Path) -> DisplayService:
    return DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=_StubOverlay(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=MediaService(),
    )


def _scsi_info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="Frozen Warframe LCD 320x320",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )


def _hid2_info(native: tuple[int, int] = (240, 320)) -> ProductInfo:
    return ProductInfo(
        vid=0x0416, pid=0x5302,
        vendor="Winbond", product="USB Display (HID Type 2)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=2, native_resolution=native,
        orientations=(0, 90, 180, 270),
    )


# ── DisplayService.build_solid_color_frame ────────────────────────────


def test_build_solid_color_frame_uses_profile_resolution(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """Canvas is created at profile.resolution, not info.native_resolution."""
    info = _hid2_info(native=(240, 320))     # registry: portrait
    profile = get_profile(58)                 # PM=58 → (320, 240) landscape

    frame = display.build_solid_color_frame(
        info=info, color=(255, 0, 0), profile=profile,
    )

    create_calls = [c for c in renderer.calls if c[0] == "create_surface"]
    _, args = create_calls[0]
    assert (args[0], args[1]) == (320, 240), \
        "Canvas dims come from profile.resolution"
    # Color passed to create_surface should include the RGB + alpha=255
    assert args[2] == (255, 0, 0, 255)
    # Encoder result has rotated dims (240, 320) → 153,600 bytes
    assert len(frame) == 240 * 320 * 2


def test_build_solid_color_frame_applies_device_rotation(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """profile.rotate=True triggers the 90° rotation before encode."""
    info = _hid2_info()
    profile = get_profile(58)
    assert profile.rotate is True

    display.build_solid_color_frame(
        info=info, color=(0, 255, 0), profile=profile,
    )

    rotates = [c for c in renderer.calls if c[0] == "rotate"]
    angles = [c[1][1] for c in rotates]
    assert 90 in angles, f"Expected device rotation 90°, got {angles}"


def test_build_solid_color_frame_no_rotation_when_profile_says_so(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A rotate=False profile (e.g. SCSI 320×320) produces no rotation call."""
    info = _scsi_info()
    profile = get_profile(100)
    assert profile.rotate is False

    display.build_solid_color_frame(
        info=info, color=(0, 0, 255), profile=profile,
    )

    rotates_90 = [c for c in renderer.calls
                  if c[0] == "rotate" and c[1][1] == 90]
    assert rotates_90 == []


def test_build_solid_color_frame_applies_brightness(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A per-device brightness < 100 reaches the renderer."""
    info = _scsi_info()
    display._settings.for_device(info.key).brightness = 50

    display.build_solid_color_frame(
        info=info, color=(128, 128, 128), profile=get_profile(100),
    )

    brightness_calls = [c for c in renderer.calls if c[0] == "apply_brightness"]
    assert brightness_calls, "brightness != 100 should trigger apply_brightness"
    assert brightness_calls[0][1][1] == 50


def test_build_solid_color_frame_no_brightness_call_at_100(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """At brightness=100 (default) the renderer is not called for dim."""
    info = _scsi_info()
    # leave default (100)
    display.build_solid_color_frame(
        info=info, color=(0, 0, 0), profile=get_profile(100),
    )
    brightness_calls = [c for c in renderer.calls if c[0] == "apply_brightness"]
    assert brightness_calls == []


def test_build_solid_color_frame_profile_jpeg_dispatches_to_jpeg_encoder(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """profile.jpeg=True → encode_jpeg; else encode_rgb565."""
    info = _scsi_info()
    # FBL 54 = 360×360 JPEG square panel — not a real SCSI device but the
    # mapping exercises the encoder dispatch.
    jpeg_profile = get_profile(54)
    assert jpeg_profile.jpeg is True

    display.build_solid_color_frame(
        info=info, color=(0, 0, 0), profile=jpeg_profile,
    )

    encoders = [c[0] for c in renderer.calls if c[0].startswith("encode_")]
    assert encoders == ["encode_jpeg"]


def test_build_solid_color_frame_fallback_when_profile_none(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """profile=None falls back to info.native_resolution + RGB565, no rotation."""
    info = _hid2_info(native=(240, 320))

    display.build_solid_color_frame(
        info=info, color=(255, 255, 255), profile=None,
    )

    create_calls = [c for c in renderer.calls if c[0] == "create_surface"]
    _, args = create_calls[0]
    assert (args[0], args[1]) == (240, 320)
    rotates_90 = [c for c in renderer.calls
                  if c[0] == "rotate" and c[1][1] == 90]
    assert rotates_90 == []


# ── SendColor Command dispatch ────────────────────────────────────────


def test_send_color_command_drives_real_app_dispatch(
    tmp_home: Path,
) -> None:
    """End-to-end via App.dispatch with a fake platform + recording renderer.

    Connects a real ScsiLcd over a FakeScsiTransport, then dispatches
    SendColor — verifies the byte stream reaches the transport.
    """
    # Build a Fake platform that yields a single SCSI device for scan_devices
    # and returns a scripted handshake response.
    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))

    renderer = RecordingRenderer()
    app = App(platform=platform, renderer=renderer)

    # Inject the device into the registry so ConnectDevice can find it.
    # Real platform.scan_devices() is empty here; we rely on attach() via
    # ConnectDevice, which calls find_product(vid, pid) — that works because
    # 0402:3922 is in the canonical ALL_DEVICES registry.
    connect_result = app.dispatch(ConnectDevice(key="0402:3922"))
    assert connect_result.ok, f"ConnectDevice failed: {connect_result.message}"

    # Subscribe to FrameSent so we can assert it fires
    frame_sent_events: list[FrameSent] = []
    app.events.subscribe(
        FrameSent, lambda e: frame_sent_events.append(e),   # type: ignore[arg-type, return-value]
    )

    result = app.dispatch(SendColor(key="0402:3922", r=255, g=0, b=0))

    assert result.ok, f"SendColor failed: {result.message}"
    assert result.bytes_sent > 0
    assert result.key == "0402:3922"
    assert "#ff0000" in result.message
    # FrameSent event published
    assert len(frame_sent_events) == 1
    assert frame_sent_events[0].key == "0402:3922"


def test_send_color_validates_channel_range(tmp_home: Path) -> None:
    """RGB channels out of 0-255 produce an ok=False result, no dispatch."""
    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())

    app.dispatch(ConnectDevice(key="0402:3922"))

    bad = app.dispatch(SendColor(key="0402:3922", r=256, g=0, b=0))
    assert bad.ok is False
    assert "out of range" in bad.message
    assert bad.bytes_sent == 0


def test_send_color_unknown_device_returns_failure(tmp_home: Path) -> None:
    """SendColor on an unattached key produces ok=False, no exception."""
    platform = FakePlatform(tmp_home)
    app = App(platform=platform, renderer=RecordingRenderer())

    result = app.dispatch(SendColor(key="dead:beef", r=128, g=128, b=128))

    assert result.ok is False
    assert "dead:beef" in result.message
    assert result.bytes_sent == 0


# ── CLI hex parsing ───────────────────────────────────────────────────


@pytest.mark.parametrize("input_str,expected", [
    ("ff0000", (255, 0, 0)),
    ("00ff00", (0, 255, 0)),
    ("0000ff", (0, 0, 255)),
    ("#ff0000", (255, 0, 0)),
    ("FFFFFF", (255, 255, 255)),
    ("000000", (0, 0, 0)),
    ("808080", (128, 128, 128)),
    ("  ff0000  ", (255, 0, 0)),   # whitespace stripped
])
def test_parse_hex_color_accepts(input_str: str,
                                  expected: tuple[int, int, int]) -> None:
    assert _parse_hex_color(input_str) == expected


@pytest.mark.parametrize("input_str", [
    "",
    "f",
    "ff",
    "fff",
    "ffff",
    "fffff",
    "fffffff",
    "fffffffff",
    "GGGGGG",
    "ff00zz",
    "0xff0000",
])
def test_parse_hex_color_rejects(input_str: str) -> None:
    assert _parse_hex_color(input_str) is None


# ── Helper: build SCSI poll response (re-used) ────────────────────────


def _scsi_poll_response(fbl: int, *, size: int = 0xE100) -> bytes:
    resp = bytearray(size)
    resp[0] = fbl
    return bytes(resp)
