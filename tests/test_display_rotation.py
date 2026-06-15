"""DisplayService rotation + encoding tests.

Locks the post-profile pipeline behaviour:

  1. ``profile.resolution`` drives the render canvas size (not
     ``info.native_resolution``).
  2. ``profile.rotate=True`` rotates before encode: simple RGB565 panels
     (320×240) get a blanket 90°; widescreen JPEG panels (854×480 …) use the
     per-resolution encode table (C# ImageToJpg directionB switch). (#136/#169)
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
    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
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
               user_elements: list[dict[str, Any]] | None = None,
               *, temp_unit: str = "C") -> Any:
        del config, sensors, clock, user_elements, temp_unit
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


# ── 7. device-only encode baseline (#137 — FW360 upside down) ─────────


def _bulk_480_info() -> ProductInfo:
    return ProductInfo(
        vid=0x87AD, pid=0x70DB,
        vendor="ChiZhu Tech", product="FW360 Ultra",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=4, fbl=72, native_resolution=(480, 480),
        orientations=(0, 90, 180, 270),
    )


def test_encode_baseline_rotates_wire_only_not_preview(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A profile.encode_baseline (FW360 PM=6 → 180°) pre-rotates the WIRE frame
    so the panel reads upright, while the stored preview_surface stays the
    pre-baseline surface — the GUI preview is unaffected. (#137)"""
    info = _bulk_480_info()
    profile = DeviceProfile(480, 480, encode_baseline=180)

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    # The baseline rotation hit the wire-encode path.
    rotate_180 = [c for c in renderer.calls if c[0] == "rotate" and c[1][1] == 180]
    assert rotate_180, "expected a rotate(surface, 180) in the wire-encode path"

    # preview_surface is the INPUT to that rotate (captured before encode),
    # never its 180°-rotated output — proves the baseline is device-only.
    baseline_input = rotate_180[-1][1][0]
    scene = display._scenes[info.key]
    assert scene.preview_surface is baseline_input


def test_zero_encode_baseline_does_not_rotate_wire(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A profile with no baseline (every non-FW360 device) must not add a
    180° rotation — guarantees zero behavior change off the #137 path."""
    info = _bulk_480_info()
    profile = DeviceProfile(480, 480, encode_baseline=0)

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    rotate_180 = [c for c in renderer.calls if c[0] == "rotate" and c[1][1] == 180]
    assert rotate_180 == [], f"unexpected baseline rotation: {rotate_180}"


# ── 8. content-matched portrait composition (#136 — Vision Max stretch) ─


def _theme_sized(w: int, h: int) -> Theme:
    return Theme(
        path=Path("/dev/null/themes/t"), name="t",
        resolution=(w, h),
        config={"elements": [], "width": w, "height": h},
    )


def _wide_info() -> ProductInfo:
    # 87AD:70DB bulk; profile (FBL 224 → 854×480 rotate=True) is passed in.
    return ProductInfo(
        vid=0x87AD, pid=0x70DB,
        vendor="ChiZhu Tech", product="Vision Max 120",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=4, fbl=224, native_resolution=(0, 0),
        orientations=(0, 90, 180, 270),
    )


def test_portrait_theme_composes_portrait_and_skips_device_rotate(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A portrait-authored theme on a non-square rotate=True panel composes at
    portrait dims (no stretch) and skips the device 90° rotate — the portrait
    canvas already matches the portrait wire buffer. (#136)"""
    profile = get_profile(224)
    assert profile.rotate and profile.resolution == (854, 480)

    display.build_frame(
        info=_wide_info(), theme=_theme_sized(480, 854), sensors={},
        profile=profile,
    )

    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (480, 854) in canvases, f"expected a 480×854 canvas, got {canvases}"
    rot90 = [c for c in renderer.calls if c[0] == "rotate" and c[1][1] == 90]
    assert rot90 == [], f"portrait compose must skip the device rotate, got {rot90}"


def test_portrait_theme_at_orientation90_is_not_rotated(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A portrait-authored theme on a non-square panel, with the device rotated
    to 90°, must NOT be rotated: the content is already portrait (the mask/theme
    has the orientation baked in).  Re-rotating it put it 90° off — the reported
    bug. (#136)"""
    info = _wide_info()
    profile = get_profile(224)                       # 854×480, rotate=True
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(
        info=info, theme=_theme_sized(480, 854), sensors={}, profile=profile,
    )

    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [], (
        f"portrait content has orientation baked in — must not rotate, "
        f"got {rotations}"
    )


def test_landscape_widescreen_orientation0_composes_landscape_unrotated(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A landscape theme on a widescreen JPEG panel composes landscape and — at
    orientation 0 — sends it UNROTATED.  The C# ImageToJpg 854×480 default maps
    directionB 0 → 0° (encode_invert=False, base 0), so the cutover's blanket
    90° was wrong.  This is the LF19 portrait-vs-landscape fix. (#169)"""
    profile = get_profile(224)

    display.build_frame(
        info=_wide_info(), theme=_theme_sized(854, 480), sensors={},
        profile=profile,
    )

    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (854, 480) in canvases, f"expected an 854×480 canvas, got {canvases}"
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [], (
        f"orientation 0 widescreen must send landscape unrotated, got {rotations}"
    )


def test_landscape_widescreen_orientation90_rotates_per_encode_table(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """At orientation 90 the same panel rotates per the encode TABLE, not a
    blanket 90°.  C# ImageToJpg 854×480 default: directionB 90 → 90°.  Proves
    the table is wired (and folds the user orientation). (#169)"""
    info = _wide_info()
    profile = get_profile(224)
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(
        info=info, theme=_theme_sized(854, 480), sensors={}, profile=profile,
    )

    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [90], (
        f"orientation 90 widescreen → single encode rotate 90°, got {rotations}"
    )


def test_unsized_theme_defaults_to_landscape(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A theme with no declared size (0,0) falls back to landscape compose; at
    orientation 0 the widescreen encode table sends it unrotated (0°). (#136/#169)"""
    profile = get_profile(224)

    display.build_frame(
        info=_wide_info(), theme=_theme_sized(0, 0), sensors={}, profile=profile,
    )

    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (854, 480) in canvases
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [], (
        f"unsized landscape at orientation 0 sends unrotated, got {rotations}"
    )


def test_composed_canvas_size_drives_preview_orientation(
    display: DisplayService,
) -> None:
    """composed_canvas_size (used by the GUI to size the preview bezel) returns
    portrait dims for a portrait theme, landscape for landscape, and swaps for
    user rotation. (#136 phase 3)"""
    info = _wide_info()
    profile = get_profile(224)   # 854×480, rotate=True

    assert display.composed_canvas_size(info, _theme_sized(480, 854), profile, 0) == (480, 854)
    assert display.composed_canvas_size(info, _theme_sized(854, 480), profile, 0) == (854, 480)
    assert display.composed_canvas_size(info, _theme_sized(0, 0), profile, 0) == (854, 480)
    # user rotation 90 swaps the composed canvas
    assert display.composed_canvas_size(info, _theme_sized(480, 854), profile, 90) == (854, 480)
