"""DisplayService rotation + encoding tests.

Locks the post-profile pipeline behaviour:

  1. ``profile.resolution`` drives the render canvas size (not
     ``info.native_resolution``).
  2. ``profile.rotate=True`` triggers an extra 90° rotation before encode
     — the missing "RGB565-LE rotated" step legacy used to do.
  3. ``profile.jpeg=True`` dispatches to encode_jpeg; False → encode_rgb565.
  4. When ``profile=None`` is passed (LED, pre-handshake, legacy callers),
     behaviour matches the pre-profile path: native_resolution + RGB565
     + no device rotation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.core.models import (
    FitMode,
    Kind,
    ProductInfo,
    Theme,
    Wire,
)
from trcc.core.ports import Renderer
from trcc.core.protocol import DeviceProfile, get_profile
from trcc.services.display import DisplayService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings
from trcc.services.theme import ThemeService

# ── A tiny Renderer that records every call ───────────────────────────


class _Surface:
    """Opaque surface stand-in. Carries its declared (w, h) for assertions."""

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h


class RecordingRenderer(Renderer):
    """Renderer fake that records every operation and returns sane surfaces."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    # ── Surfaces ───────────────────────────────────────────────────────
    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        self._record("create_surface", width, height, color)
        return _Surface(width, height)

    def open_image(self, path: Path) -> Any:
        self._record("open_image", path)
        return _Surface(100, 100)

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    # ── Compositing ───────────────────────────────────────────────────
    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        self._record("composite", base, overlay, position, mask)
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        self._record("resize", surface, width, height)
        return _Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        self._record("rotate", surface, degrees)
        # Apply rotation to dimensions so subsequent calls see the swap
        if degrees % 180 == 90:
            return _Surface(surface.h, surface.w)
        return _Surface(surface.w, surface.h)

    def flip_horizontal(self, surface: Any) -> Any:
        self._record("flip_horizontal", surface)
        return surface

    # ── Adjustments ───────────────────────────────────────────────────
    def apply_brightness(self, surface: Any, percent: int) -> Any:
        self._record("apply_brightness", surface, percent)
        return surface

    # ── Text ──────────────────────────────────────────────────────────
    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        self._record("draw_text", x, y, text)

    # ── Encoding ──────────────────────────────────────────────────────
    def encode_rgb565(self, surface: Any) -> bytes:
        self._record("encode_rgb565", surface)
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        self._record("encode_jpeg", surface, quality)
        return b"\xff\xd8" + b"\x00" * 100

    # ── Legacy boundary ───────────────────────────────────────────────
    def from_raw_rgb24(self, frame: Any) -> Any:
        return _Surface(frame.width, frame.height)


# ── Fakes that don't matter for what we're testing ────────────────────


class _StubOverlay(OverlayService):
    """Subclass that skips text rendering — returns the canvas unchanged."""

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
    from .conftest import FakePaths
    paths = FakePaths(tmp_home)
    settings = Settings(paths)
    return DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=_StubOverlay(renderer),
        settings=settings,
        media=MediaService(),
    )


def _hid_type2_info(native: tuple[int, int] = (240, 320)) -> ProductInfo:
    return ProductInfo(
        vid=0x0416, pid=0x5302,
        vendor="Winbond", product="USB Display (HID Type 2)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=2, native_resolution=native,
        orientations=(0, 90, 180, 270),
    )


def _scsi_info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="Frozen Warframe LCD 320x320",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )


def _theme(name: str = "test") -> Theme:
    return Theme(
        path=Path("/dev/null/themes/test"),
        name=name,
        resolution=(320, 240),
        config={"elements": []},
    )


# ── 1. profile.rotate=True applies the device rotation ────────────────


def test_rotate_true_profile_adds_device_90_degree_rotation(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A rotate=True profile must trigger an additional rotate(surface, 90)."""
    info = _hid_type2_info()
    profile = get_profile(58)   # PM=FBL=58 → 320×240, rotate=True
    assert profile.rotate is True

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    rotate_calls = [c for c in renderer.calls if c[0] == "rotate"]
    assert any(degrees == 90 for _, args in rotate_calls for degrees in (args[1],)), \
        f"Expected rotate(surface, 90), got {rotate_calls}"


def test_rotate_false_profile_does_not_apply_device_rotation(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A square panel (rotate=False) must NOT add a device rotation."""
    info = _scsi_info()
    profile = get_profile(100)   # 320×320 square, rotate=False
    assert profile.rotate is False

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    rotate_90_calls = [
        c for c in renderer.calls
        if c[0] == "rotate" and c[1][1] == 90
    ]
    assert rotate_90_calls == [], \
        f"Expected no rotate(surface, 90), got {rotate_90_calls}"


# ── 2. profile.resolution drives visual_size, not info.native_resolution ──


def test_visual_size_uses_profile_resolution_not_native(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """Canvas creation must use profile.resolution, not the registry static.

    AussieMakerGeek's case: registry says (240, 320) portrait, but PM=58
    profile says (320, 240) landscape. The bg+overlay canvases must be
    created at 320×240 so content renders in its logical orientation.
    """
    info = _hid_type2_info(native=(240, 320))   # registry: portrait
    profile = get_profile(58)                    # profile: 320×240 landscape
    assert profile.resolution == (320, 240)
    assert info.native_resolution == (240, 320)

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    create_calls = [c for c in renderer.calls if c[0] == "create_surface"]
    assert create_calls, "expected at least one create_surface call"
    # First create call is the bg canvas — should be the profile's landscape size.
    _, args = create_calls[0]
    assert (args[0], args[1]) == (320, 240), (
        f"Expected canvas (320, 240) from profile, got {(args[0], args[1])}"
    )


# ── 3. profile.jpeg drives the encoder choice ────────────────────────


def test_profile_jpeg_true_dispatches_to_encode_jpeg(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """profile.jpeg=True must produce encode_jpeg(), not encode_rgb565()."""
    info = _hid_type2_info()
    profile = DeviceProfile(width=320, height=240, jpeg=True, rotate=True)

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    encoders = [c[0] for c in renderer.calls if c[0].startswith("encode_")]
    assert encoders == ["encode_jpeg"], \
        f"Expected only encode_jpeg, got {encoders}"


def test_profile_jpeg_false_dispatches_to_encode_rgb565(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """profile.jpeg=False must produce encode_rgb565()."""
    info = _hid_type2_info()
    profile = get_profile(58)   # RGB565 (jpeg=False)
    assert profile.jpeg is False

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    encoders = [c[0] for c in renderer.calls if c[0].startswith("encode_")]
    assert encoders == ["encode_rgb565"], \
        f"Expected only encode_rgb565, got {encoders}"


# ── 4. profile=None fallback matches pre-profile behaviour ────────────


def test_profile_none_falls_back_to_native_resolution_rgb565_no_rotate(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """Pre-profile callers (profile=None) must keep the old behavior.

    Canvas = info.native_resolution, no device rotation, RGB565.
    """
    info = _hid_type2_info(native=(240, 320))

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=None)

    # Canvas at native_resolution
    create_calls = [c for c in renderer.calls if c[0] == "create_surface"]
    _, args = create_calls[0]
    assert (args[0], args[1]) == (240, 320), (
        f"Fallback should use info.native_resolution, got {(args[0], args[1])}"
    )
    # No device rotation
    rotate_90_calls = [
        c for c in renderer.calls if c[0] == "rotate" and c[1][1] == 90
    ]
    assert rotate_90_calls == [], \
        f"Fallback should not rotate, got {rotate_90_calls}"
    # RGB565 encoder
    encoders = [c[0] for c in renderer.calls if c[0].startswith("encode_")]
    assert encoders == ["encode_rgb565"]


def test_profile_none_with_info_fbl_uses_registry_lookup(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """If profile=None but info.fbl is set, get_profile(info.fbl) is used.

    SCSI registry entries have fbl=100; the registry-driven profile yields
    (320, 320) big-endian — the right behavior for those devices without
    needing a handshake.
    """
    info = _scsi_info()   # fbl=100, native_resolution=(320, 320)
    assert info.fbl == 100

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=None)

    create_calls = [c for c in renderer.calls if c[0] == "create_surface"]
    _, args = create_calls[0]
    assert (args[0], args[1]) == (320, 320), "FBL=100 profile is (320, 320)"
    # FBL=100 has rotate=False
    rotate_90_calls = [
        c for c in renderer.calls if c[0] == "rotate" and c[1][1] == 90
    ]
    assert rotate_90_calls == []


# ── 5. Order: user-orientation rotation precedes device rotation ──────


def test_user_orientation_rotation_precedes_device_rotation(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """Rotation order: user orientation first, then device rotation.

    A rotate=True profile + user orientation=90 must produce two rotate
    calls in this exact order: (270, then 90). Reversing breaks the
    legacy "fit → overlay → dim → rotate → encode" sequence.
    """
    info = _hid_type2_info()
    profile = get_profile(58)
    settings = display._settings.for_device(info.key)
    settings.orientation = 90

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    rotate_calls = [c[1] for c in renderer.calls if c[0] == "rotate"]
    # First rotation: user orientation (360 - 90 = 270)
    # Second rotation: device (90)
    rotation_angles = [args[1] for args in rotate_calls]
    assert rotation_angles == [270, 90], (
        f"Expected [user=270, device=90], got {rotation_angles}"
    )


# ── 6. fit_mode wired through (regression guard) ──────────────────────


def test_fit_mode_setting_reaches_renderer(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """The DeviceSettings.fit_mode is read per-device — not regressed by profile."""
    info = _hid_type2_info()
    profile = get_profile(58)
    settings = display._settings.for_device(info.key)
    settings.fit_mode = FitMode.STRETCH

    # Just verify build_frame runs to completion without crashing on fit.
    result = display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)
    assert isinstance(result, bytes)
