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

from trcc.adapters.theme.filesystem import FileContentStore
from trcc.app import App
from trcc.core.commands import ConnectDevice, SendColor
from trcc.core.events import FrameSent
from trcc.core.models import Kind, ProductInfo, Wire
from trcc.core.ports import Renderer
from trcc.core.protocol import get_profile
from trcc.services.display import DisplayService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings
from trcc.ui.cli.display import _parse_hex_color

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

    def flip_horizontal(self, surface: Any) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        self._record("apply_brightness", surface, percent)
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False, family: str = "") -> None:
        pass

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        self._record("encode_rgb565", surface)
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        self._record("encode_jpeg", surface, quality)
        return b"\xff\xd8" + b"\x00" * 100

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _Surface(frame.width, frame.height)

    def decode_image(self, data: bytes) -> Any:
        return _Surface(100, 100)


class _StubOverlay(OverlayService):
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
    return DisplayService(
        renderer=renderer,
        themes=FileContentStore(),
        overlay=_StubOverlay(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=MediaService(),
        paths=FakePaths(tmp_home),
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
    # SendColor bypasses the scene cache (solid fill), so it carries no
    # surface — the GUI falls back to a re-render for it.
    assert frame_sent_events[0].surface is None


def test_sleep_device_blanks_lcd_with_black_frame(tmp_home: Path) -> None:
    """SleepDevice pushes a solid-black frame to a connected LCD (#143)."""
    from trcc.core.commands import SleepDevice

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok

    result = app.dispatch(SleepDevice(key="0402:3922"))

    assert result.ok, f"SleepDevice failed: {result.message}"
    assert result.bytes_sent > 0
    assert "#000000" in result.message   # composed SendColor(0,0,0)


def test_sleep_device_not_connected_returns_false(tmp_home: Path) -> None:
    """SleepDevice never raises for a missing/disconnected device — it is
    dispatched from App.close() mid-shutdown and must degrade gracefully."""
    from trcc.core.commands import SleepDevice

    app = App(platform=FakePlatform(tmp_home), renderer=RecordingRenderer())
    result = app.dispatch(SleepDevice(key="dead:beef"))

    assert not result.ok
    assert "not connected" in result.message


def test_app_close_blanks_the_panel_before_release(tmp_home: Path) -> None:
    """App.close() sends a black frame to every device before detaching, so
    the panel darkens on shutdown instead of holding its last image (#143)."""
    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok

    sent: list[FrameSent] = []
    app.events.subscribe(
        FrameSent, lambda e: sent.append(e),   # type: ignore[arg-type, return-value]
    )
    app.close()

    # A blank frame went out (the last FrameSent) and the device is released.
    assert sent, "close() sent no frame — panel would stay lit"
    assert "0402:3922" not in app.devices


def test_render_and_send_frame_sent_carries_surface(tmp_home: Path) -> None:
    """RenderAndSend ships the rendered surface in ``FrameSent`` so the GUI
    preview shows THAT frame directly — legacy's publish-the-frame shape,
    not a second render.  The carried surface is exactly what
    ``rendered_surface`` exposes (the preview reuses one source).
    """
    from trcc.core.commands import RenderAndSend
    from trcc.core.models import Theme

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok

    # RenderAndSend needs an active theme to render.
    app.active_themes["0402:3922"] = Theme(
        path=tmp_home / "t", name="t",
        resolution=(320, 320), config={"elements": []},
    )

    events: list[FrameSent] = []
    app.events.subscribe(
        FrameSent, lambda e: events.append(e),   # type: ignore[arg-type, return-value]
    )

    result = app.dispatch(RenderAndSend(key="0402:3922"))
    assert result.ok, result.message
    assert len(events) == 1
    # The surface is carried — and it's the very surface the preview reuses.
    assert events[0].surface is not None
    assert events[0].surface is app.display.rendered_surface("0402:3922")


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


# ── EnsureConnected — idempotent connect-first for wire commands ──────


def test_ensure_connected_attaches_a_fresh_device(tmp_home: Path) -> None:
    """A stateless (fresh) App holds no devices; EnsureConnected brings the
    device up so a following wire command works — the #150/#171 fix."""
    from trcc.core.commands import EnsureConnected

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())
    key = "0402:3922"
    assert key not in app.devices                      # nothing attached yet

    assert app.dispatch(EnsureConnected(key=key)).ok
    assert app.devices[key].is_connected               # now ready for wire I/O


def test_ensure_connected_is_idempotent_no_rehandshake(tmp_home: Path) -> None:
    """A second EnsureConnected on a live device is a pure no-op — it must NOT
    rebuild the transport or re-handshake (safe before every tick / in daemon
    mode).  ConnectDevice, by contrast, DOES re-handshake (dev-console inject-
    reply contract) — asserted here so the two stay distinct."""
    from trcc.core.commands import ConnectDevice, EnsureConnected

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.extend(_scsi_poll_response(100) for _ in range(3))
    app = App(platform=platform, renderer=RecordingRenderer())
    key = "0402:3922"

    assert app.dispatch(EnsureConnected(key=key)).ok
    first = app.devices[key]

    result = app.dispatch(EnsureConnected(key=key))     # already connected
    assert result.ok and "already connected" in result.message
    assert app.devices[key] is first                    # SAME instance — no rebuild

    app.dispatch(ConnectDevice(key=key))                # re-handshake path
    assert app.devices[key] is not first                # rebuilt — contract preserved


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


# ── Hotplug bridge: DeviceAttached → ConnectDevice (#139) ─────────────


def test_device_attached_event_connects_device(tmp_home: Path) -> None:
    """Publishing DeviceAttached on the bus connects the device — the hotplug
    bridge the cutover was missing (a device plugged in after launch, or
    missed at the boot discover, never connected without it). (#139)"""
    from trcc.core.events import DeviceAttached

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())

    assert "0402:3922" not in app.devices
    app.events.publish(DeviceAttached(key="0402:3922", vid=0x0402, pid=0x3922))
    assert "0402:3922" in app.devices    # bridge fired ConnectDevice


def test_device_attached_is_idempotent(tmp_home: Path) -> None:
    """A DeviceAttached for an already-connected device is a no-op — coldplug
    replays + duplicate adds must not reconnect or replace the device. (#139)"""
    from trcc.core.events import DeviceAttached

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))
    app = App(platform=platform, renderer=RecordingRenderer())

    app.events.publish(DeviceAttached(key="0402:3922", vid=0x0402, pid=0x3922))
    device = app.devices["0402:3922"]
    # Second event — guarded, so it neither re-handshakes (no second poll
    # response is scripted) nor swaps the live device object.
    app.events.publish(DeviceAttached(key="0402:3922", vid=0x0402, pid=0x3922))
    assert app.devices["0402:3922"] is device


# ── Post-handshake data download (#136 Phase 1) ──────────────────────


def test_connect_installs_data_for_handshake_resolution(tmp_home: Path) -> None:
    """ConnectDevice installs theme/cloud/mask data for the HANDSHAKE-resolved
    resolution. Non-square bulk panels report native_resolution=(0,0) and only
    learn their real size at handshake, so this is the only place their data
    (both orientations, via ensure_all) gets installed. Port of legacy's
    _ensure_data_background(device, w, h). (#136)

    Since #275 it goes through ``data_install_runner`` rather than being called
    inline, so the runner is rebuilt around the spy too -- it captured the real
    service when the App was constructed."""
    from trcc.adapters.infra.data_install_runner import SyncDataInstallRunner
    from trcc.core.commands import ConnectDevice
    from trcc.services.data_install import EnsureDataResult

    platform = FakePlatform(tmp_home)
    platform.scsi.read_script.append(_scsi_poll_response(100))   # FBL 100 → 320×320
    app = App(platform=platform, renderer=RecordingRenderer())

    installed: list[tuple[int, int]] = []

    class _SpyInstall:
        def ensure_all(self, resolution: tuple[int, int], variant: str = "",
                       mask_variant: str = "") -> EnsureDataResult:
            installed.append(resolution)
            return EnsureDataResult(
                resolution=resolution,
                themes_ok=True, web_ok=True, masks_ok=True,
            )

    app.data_install = _SpyInstall()   # type: ignore[assignment]
    app.data_install_runner = SyncDataInstallRunner(
        app.data_install, app.events,
    )

    assert app.dispatch(ConnectDevice(key="0402:3922")).ok
    assert installed == [(320, 320)]   # the handshake-resolved resolution
