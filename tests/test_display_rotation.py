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

import sys
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import (
    FitMode,
    Kind,
    ProductInfo,
    RawFrame,
    Theme,
    Wire,
)
from trcc.core.ports import Renderer
from trcc.core.protocol import DeviceProfile, get_profile
from trcc.services.display import DisplayService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_encode_angles,
)

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
                  italic: bool = False, family: str = "") -> None:
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

    def to_raw_rgb24(self, surface):
        # The inverse the port now requires.  Test doubles carry no pixels,
        # so this reports the surface's DIMENSIONS with blank bytes — enough
        # for a caller that only needs a correctly-sized RawFrame.
        w, h = self.surface_size(surface)
        return RawFrame(data=bytes(w * h * 3), width=w, height=h)

    def decode_image(self, data: bytes) -> Any:
        return _Surface(100, 100)


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
        themes=FileContentStore(),
        overlay=_StubOverlay(renderer),
        settings=settings,
        media=MediaService(),
        paths=paths,
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


def test_simple_rotate_panel_at_90_landscape_theme_rotates_whole_composite(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A non-square RGB565 rotate panel at orientation 90 with a LANDSCAPE-only
    theme (no portrait DC variant — ``rotation`` defaults 0) composes on the
    native LANDSCAPE canvas (so nothing clips) and rotates the WHOLE composite
    90° into the portrait buffer — legacy ``has_portrait_themes=False`` / the C#
    oriented-output model.  bg + text rotate together, staying aligned. (#136)
    """
    info = _hid_type2_info()
    profile = get_profile(58)
    settings = display._settings.for_device(info.key)
    settings.orientation = 90

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    # Composed on the native landscape canvas, then ONE whole-composite rotate.
    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (320, 240) in canvases, f"expected a 320×240 canvas, got {canvases}"
    rotation_angles = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotation_angles == [90], (
        f"landscape theme at 90 rotates the whole composite once, got "
        f"{rotation_angles}"
    )


def test_bulk_jpeg_small_rotate_panel_at_90_rotates_whole_composite(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """A simple 320×240 rotate panel that arrives on the bulk wire as JPEG
    (FBL 50, PM=5 Mjolnir: ``jpeg=True rotate=True widescreen=False``) must
    rotate like its RGB565 sibling — one whole-composite rotate into the
    portrait buffer — NOT fall through the widescreen encode-table path (empty
    table → no rotation, the #176 bug: "landscape works, can't rotate").  The
    final encode is still JPEG.  (#176)
    """
    info = _hid_type2_info()
    profile = DeviceProfile(width=320, height=240, jpeg=True, rotate=True)
    assert profile.widescreen is False
    settings = display._settings.for_device(info.key)
    settings.orientation = 90

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (320, 240) in canvases, f"expected a 320×240 canvas, got {canvases}"
    rotation_angles = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotation_angles == [90], (
        f"JPEG small rotate panel at 90 rotates the whole composite once "
        f"(like RGB565), got {rotation_angles}"
    )
    encoders = [c[0] for c in renderer.calls
                if c[0] in ("encode_jpeg", "encode_rgb565")]
    assert encoders == ["encode_jpeg"], f"expected JPEG encode, got {encoders}"


# ── 5b. Widescreen JPEG panels apply the wire rotation on the LIVE path ──
# Regression for #169: the live path composes the widescreen 90/270 frame on the
# portrait canvas and passes portrait_content=True, which used to short-circuit
# wire_angle to 0 — so the frame shipped as an unrotated 720×1600 portrait to a
# device that wants 1600×720.  These drive the WHOLE build_frame (not wire_angle
# in isolation, which the old test did with portrait_content=False and missed the
# bug) and assert the C# ImageToJpg rotation actually reaches the renderer.


def _widescreen_info(fbl: int, native: tuple[int, int]) -> ProductInfo:
    return ProductInfo(
        vid=0x87AD, pid=0x70DB,
        vendor="Thermalright", product=f"Widescreen LCD {native[0]}x{native[1]}",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=2, fbl=fbl, native_resolution=native,
        orientations=(0, 90, 180, 270),
    )


@pytest.mark.parametrize(
    "fbl,native,orientation",
    [
        # FBL 114 (1600×720, WillVinzant's panel — encode base 180).
        (114, (1600, 720), 90),
        (114, (1600, 720), 270),
        # FBL 224 (854×480 — encode base 0, and the one family that does NOT
        # invert, so it covers the other sign as well as the other base).
        (224, (854, 480), 90),
        (224, (854, 480), 270),
    ],
)
def test_widescreen_panel_applies_wire_rotation_on_live_path(
    display: DisplayService, renderer: RecordingRenderer,
    fbl: int, native: tuple[int, int], orientation: int,
) -> None:
    """A widescreen panel at 90/270 must rotate the composite by the C# angle and
    encode a LANDSCAPE frame — proving resolve_encode_angle reaches the live
    path (portrait_content=True no longer suppresses it). (#169)

    The expected angle comes from the C# oracle, not from a number typed here.
    It was typed here, and it was wrong for 854×480 at both angles — the same
    stale table this test was meant to defend. (#203/#171)
    """
    info = _widescreen_info(fbl, native)
    profile = get_profile(fbl)
    assert profile.widescreen and profile.jpeg and profile.rotate
    wire_deg = csharp_encode_angles(native, jpeg=True)[orientation]
    settings = display._settings.for_device(info.key)
    settings.orientation = orientation

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)

    # Composed on the transposed PORTRAIT canvas (h, w).
    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (native[1], native[0]) in canvases, (
        f"expected a {native[1]}×{native[0]} portrait canvas, got {canvases}"
    )
    # Exactly one whole-composite rotation, by the C# ImageToJpg angle.
    rotation_angles = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotation_angles == [wire_deg], (
        f"FBL {fbl} @ {orientation}°: expected one wire rotate of {wire_deg}°, "
        f"got {rotation_angles} (0/absent = the #169 unrotated-portrait bug)"
    )
    # The encoded surface is the native LANDSCAPE frame the device expects.
    enc = [c for c in renderer.calls if c[0] == "encode_jpeg"]
    assert len(enc) == 1, f"expected one JPEG encode, got {len(enc)}"
    encoded_surface = enc[0][1][0]
    assert (encoded_surface.w, encoded_surface.h) == native, (
        f"FBL {fbl} @ {orientation}°: encoded {encoded_surface.w}×"
        f"{encoded_surface.h}, expected native landscape {native[0]}×{native[1]}"
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


def test_rotate_panel_at_90_composes_portrait_and_skips_device_rotate(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """On a non-square rotate=True panel the canvas follows the ORIENTATION (the
    per-orientation catalog), not the theme's declared size: at 90 it composes
    portrait (480×854) and skips the device rotate — the portrait content is
    pre-oriented.  Theme size is irrelevant (DC themes declare none). (#136)"""
    info = _wide_info()
    profile = get_profile(224)
    assert profile.rotate and profile.resolution == (854, 480)
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(
        info=info, theme=_theme_sized(0, 0), sensors={}, profile=profile,
    )

    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (480, 854) in canvases, f"expected a 480×854 canvas, got {canvases}"
    # ONE rotation, the wire one.  This used to read "no rotate by 90°", which
    # was a proxy for "no compose-time device rotate on top of the wire
    # rotate" — true only while the wire angle here happened not to be 90.
    # Correcting 854×480 made it 90 and the proxy started reporting a bug that
    # was not there.  Assert the invariant itself: the composite is turned
    # exactly once, by the C# angle.
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [csharp_encode_angles((854, 480), jpeg=True)[90]], (
        f"expected exactly one wire rotation, got {rotations} — two means the "
        f"compose step is rotating as well as the encoder"
    )


def test_widescreen_at_orientation90_applies_csharp_wire_rotation(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """At orientation 90 a widescreen panel composes on the portrait canvas and
    rotates the whole composite by the C# ImageToJpg angle to reach the device's
    fixed LANDSCAPE wire dims — even for an unsized DC theme.

    This REVERSES the prior "portrait content is pre-oriented, no rotate"
    assumption, which was self-admittedly unverified ("verify Trofeo first") and
    shipped in v9.8.7: the actual C# decompile applies ``RotateImg`` to every
    widescreen frame (854×480 base 0 → directionB 90 → 270°), the wire header is
    hard-coded landscape (so a 480×854 portrait buffer MUST be rotated), and #169
    reports the unrotated output is wrong on glass. (#169)"""
    info = _wide_info()
    profile = get_profile(224)                       # 854×480, rotate=True
    want = csharp_encode_angles((854, 480), jpeg=True)[90]
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(
        info=info, theme=_theme_sized(0, 0), sensors={}, profile=profile,
    )

    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [want], (
        f"854×480 @ 90° → C# RotateImg {want}° to reach landscape wire dims, "
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


def test_widescreen_orientation90_encodes_landscape_wire_frame(
    display: DisplayService, renderer: RecordingRenderer,
) -> None:
    """At orientation 90 the widescreen panel composes portrait then rotates to a
    LANDSCAPE wire frame — the device's fixed dims (854×480), never portrait.

    Reversal of the earlier "portrait catalog, pre-oriented, no rotate" model
    (unverified, shipped v9.8.7): the C# ``ImageToJpg`` always applies the
    directionB ``RotateImg`` and the USB frame header is hard-coded landscape, so
    the compose buffer (480×854) must be rotated 270° back to 854×480.  #169's
    on-glass report confirms the no-rotate frame was wrong. (#169)"""
    info = _wide_info()
    profile = get_profile(224)
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(
        info=info, theme=_theme_sized(0, 0), sensors={}, profile=profile,
    )

    enc = [c for c in renderer.calls if c[0] == "encode_jpeg"]
    assert len(enc) == 1, f"expected one JPEG encode, got {len(enc)}"
    surface = enc[0][1][0]
    assert (surface.w, surface.h) == (854, 480), (
        f"orientation 90 must encode the 854×480 landscape wire frame, got "
        f"{surface.w}×{surface.h}"
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


@pytest.mark.parametrize("orientation, expected", [
    (0, (854, 480)),     # landscape catalog
    (90, (480, 854)),    # portrait catalog
    (180, (854, 480)),   # landscape catalog
    (270, (480, 854)),   # portrait catalog
])
@pytest.mark.parametrize("theme_wh", [(480, 854), (854, 480), (0, 0)])
def test_canvas_geometry_is_orientation_driven(
    display: DisplayService, theme_wh, orientation, expected,
) -> None:
    """The render/preview canvas follows the ORIENTATION (the per-orientation
    catalog the content was loaded from), independent of the theme's declared
    size — DC themes declare none, so orientation is the single signal.  90/270
    → portrait, 0/180 → landscape.  This is the GUI preview-bezel size too
    (composed_canvas_size). (#136/#169)"""
    profile = get_profile(224)   # 854×480, rotate=True
    got = display.composed_canvas_size(
        _wide_info(), _theme_sized(*theme_wh), profile, orientation,
    )
    assert got == expected, f"orientation={orientation} theme={theme_wh}: got {got}"


# ── 9. landscape-only theme at 90/270 — compose landscape, rotate whole (#dc-clip-90)
#
# A non-square rotate=True panel viewing a LOCAL theme saved landscape-only
# (DC rotation=0) at 90/270 composes on the native LANDSCAPE canvas (bg + text
# aligned, nothing clipped) and rotates the WHOLE composite into the portrait
# buffer — legacy has_portrait_themes=False / the C# oriented-output model.
# These drive the REAL OverlayService so the coords reaching draw_text are
# observable, proving the text lands on the unclipped landscape canvas.


def _display_real(renderer: RecordingRenderer, tmp_home: Path) -> DisplayService:
    from .conftest import FakePaths
    return DisplayService(
        renderer=renderer, themes=FileContentStore(),
        overlay=OverlayService(renderer),          # REAL overlay → records draw_text
        settings=Settings(FakePaths(tmp_home)),
        media=MediaService(),
        paths=FakePaths(tmp_home),
    )


def _landscape_dc_theme(rotation: int) -> Theme:
    """320×240-authored DC: a metric at the right edge (x=250) that would clip a
    240-wide portrait canvas, but fits the 320-wide landscape compose canvas."""
    return Theme(
        path=Path("/dev/null/themes/local"), name="local",
        resolution=(320, 240),
        config={"rotation": rotation, "overlay_enabled": True, "elements": [
            {"id": "m", "type": "text", "x": 250, "y": 120,
             "text": "796 MHz", "color": "#ffffff", "size": 20},
        ]},
    )


@pytest.mark.parametrize("orientation", [90, 270])
def test_landscape_only_theme_composes_landscape_then_rotates_whole(
    renderer: RecordingRenderer, tmp_home: Path, orientation: int,
) -> None:
    """The text draws at its native landscape coord (x=250, on the 320-wide
    canvas — unclipped), and the whole composite is rotated once to portrait."""
    info = _hid_type2_info()
    profile = get_profile(58)          # 320×240 rotate, jpeg=False
    display = _display_real(renderer, tmp_home)
    display._settings.for_device(info.key).orientation = orientation

    display.build_frame(info=info, theme=_landscape_dc_theme(0), sensors={},
                        profile=profile)

    # Composed on the 320-wide landscape canvas → the x=250 text never clips.
    draws = [args for name, args in renderer.calls if name == "draw_text"]
    assert [(x, y) for x, y, _ in draws] == [(250, 120)]
    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (320, 240) in canvases, f"expected a 320×240 canvas, got {canvases}"
    # ONE whole-composite rotation by the user orientation.
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [orientation], (
        f"expected a single rotate({orientation}), got {rotations}"
    )


def test_cloud_portrait_theme_still_composes_portrait_no_rotate(
    renderer: RecordingRenderer, tmp_home: Path,
) -> None:
    """A portrait DC (rotation=90 — the cloud path) keeps the existing
    portrait-compose: transposed canvas, coords as authored, NO rotation."""
    info = _hid_type2_info()
    profile = get_profile(58)
    display = _display_real(renderer, tmp_home)
    display._settings.for_device(info.key).orientation = 90

    display.build_frame(info=info, theme=_landscape_dc_theme(90), sensors={},
                        profile=profile)

    draws = [args for name, args in renderer.calls if name == "draw_text"]
    assert [(x, y) for x, y, _ in draws] == [(250, 120)]
    canvases = [c[1][:2] for c in renderer.calls if c[0] == "create_surface"]
    assert (240, 320) in canvases, f"expected a 240×320 canvas, got {canvases}"
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [], f"portrait content must not rotate, got {rotations}"


def test_landscape_theme_at_orientation_0_is_unchanged(
    renderer: RecordingRenderer, tmp_home: Path,
) -> None:
    """At 0 the existing path runs: landscape compose + a single device 90°."""
    info = _hid_type2_info()
    profile = get_profile(58)
    display = _display_real(renderer, tmp_home)
    display._settings.for_device(info.key).orientation = 0

    display.build_frame(info=info, theme=_landscape_dc_theme(0), sensors={},
                        profile=profile)

    draws = [args for name, args in renderer.calls if name == "draw_text"]
    assert [(x, y) for x, y, _ in draws] == [(250, 120)]
    rotations = [c[1][1] for c in renderer.calls if c[0] == "rotate"]
    assert rotations == [90], f"orientation 0 keeps the device 90°, got {rotations}"


# ── #264: the no-mask branch must do no filesystem work ──────────────


class _CountingThemes(FileContentStore):
    """FileContentStore that records how often the render path asked for a mask."""

    def __init__(self) -> None:
        super().__init__()
        self.mask_path_calls = 0

    def mask_path(self, theme: Theme) -> Path | None:
        self.mask_path_calls += 1
        return super().mask_path(theme)


def test_composing_without_a_mask_does_not_look_one_up_twice(
    renderer: RecordingRenderer, tmp_home: Path,
) -> None:
    """``_resolve_mask_source`` already decides the mask; the branch that logs
    "no mask composited" must not go and resolve it a SECOND time.

    Log-call arguments are evaluated eagerly, so naming ``mask_path(theme)``
    inside a ``log.debug`` did real filesystem work on every rendered frame to
    build a string that is normally discarded — and when ``mask_visible`` is
    False it performed the very lookup ``_resolve_mask_source`` had just
    decided to skip (#264).

    MUTATION CHECK: put ``self._themes.mask_path(theme)`` back into that
    log.debug and this fails with 1 != 0 (or 2 != 1 on the visible path).
    """
    from .conftest import FakePaths

    paths = FakePaths(tmp_home)
    settings = Settings(paths)
    themes = _CountingThemes()
    display = DisplayService(
        renderer=renderer, themes=themes, overlay=_StubOverlay(renderer),
        settings=settings, media=MediaService(), paths=paths,
    )

    theme_dir = tmp_home / "PlainTheme"
    theme_dir.mkdir(parents=True, exist_ok=True)
    (theme_dir / "config.json").write_text('{"elements": []}')
    theme = themes.load(theme_dir)          # a theme with no mask on disk
    themes.mask_path_calls = 0              # ignore any lookups during load

    info = _hid_type2_info()
    settings.set_mask_visible(info.key, False)

    display._build_bg_mask(info, theme, (240, 320))

    # mask_visible=False: _resolve_mask_source returns before looking at all.
    assert themes.mask_path_calls == 0
