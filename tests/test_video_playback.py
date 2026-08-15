"""PlayVideo / StopVideo Commands + DisplayService playback-override path.

PlayVideo and StopVideo are wrappers around MediaService that publish
events; their primary effect is to make ``DisplayService._resolve_background``
read from the playback instead of the active theme. The render-pipeline
behavior is tested in isolation by stubbing the Playback on a Fake App.

Real ffmpeg decoding isn't tested here — MediaService.load_video is
monkeypatched to return a synthetic Playback so tests run without ffmpeg.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    PlayVideo,
    RenderAndSend,
    StopVideo,
    TickDisplay,
)
from trcc.core.errors import ThemeError
from trcc.core.events import VideoStarted, VideoStopped
from trcc.core.models import (
    FitMode,
    Kind,
    ProductInfo,
    Theme,
    Wire,
)
from trcc.core.ports import Renderer
from trcc.core.protocol import get_profile
from trcc.services.display import DisplayService
from trcc.services.media import MediaService, Playback, VideoDecoder
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings
from trcc.services.theme import ThemeService

from .conftest import FakePaths, FakePlatform


def _encoded_frame(value: int, w: int = 320, h: int = 320) -> bytes:
    """A real solid-colour JPEG — playbacks hold ENCODED frames now.

    Values are spread across the high bits by callers so they survive both
    JPEG and the panel's RGB565 quantisation.
    """
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(value)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 100)
    buf.close()
    return bytes(ba)

_KEY = "0402:3922"


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def video_file(tmp_home: Path) -> Path:
    path = tmp_home / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")   # mp4 magic; content unused
    return path


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


@pytest.fixture
def connected_app(app: App, monkeypatch: pytest.MonkeyPatch) -> App:
    """An App with the SCSI device attached + a stubbed handshake."""
    platform = app.platform
    # Scripted SCSI handshake response so ConnectDevice succeeds
    resp = bytearray(0xE100)
    resp[0] = 100   # FBL=100 → (320, 320)
    platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    result = app.dispatch(ConnectDevice(key=_KEY))
    assert result.ok, result.message
    return app


@pytest.fixture
def stub_media(
    connected_app: App, monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, Path, tuple[int, int] | None]]:
    """Replace MediaService.load_video with a no-ffmpeg stub.

    Returns the call log so tests can assert what was decoded.
    """
    calls: list[tuple[str, Path, tuple[int, int] | None]] = []

    def fake_load(self, device_key: str, path: Path,   # type: ignore[no-untyped-def]
                  size: tuple[int, int] | None, **kwargs):
        calls.append((device_key, path, size))
        # Synthetic playback: 3 fake frames.  Use a stand-in resolution
        # when the caller asked for ``size=None`` (native decode) since
        # the fake doesn't actually run ffprobe.
        w, h = size if size is not None else (640, 480)
        frames = [
            _encoded_frame(0xFF000000, w, h)
            for _ in range(3)
        ]
        playback = Playback(frames=frames, fps=kwargs.get("fps", 15))
        self._playbacks[device_key] = playback
        return playback

    monkeypatch.setattr(MediaService, "load_video", fake_load)
    return calls


# ── PlayVideo Command ────────────────────────────────────────────────


def test_play_video_loads_into_media_service(
    connected_app: App,
    stub_media: list,
    video_file: Path,
) -> None:
    result = connected_app.dispatch(
        PlayVideo(key=_KEY, path=video_file),
    )

    assert result.ok is True
    assert result.frame_count == 3
    assert result.key == _KEY
    # MediaService was called with the device's profile resolution
    assert len(stub_media) == 1
    assert stub_media[0][0] == _KEY
    assert stub_media[0][1] == video_file
    assert stub_media[0][2] == (320, 320)   # SCSI profile resolution
    # Playback is now stored on the App
    assert connected_app.media.playback(_KEY) is not None


def test_play_video_user_uploaded_asset_decodes_at_native(
    connected_app: App, stub_media: list, tmp_home: Path,
) -> None:
    """A user-saved video under ``user_content_dir`` decodes at native
    (``size=None``) so the render pipeline's fit-mode can scale it.

    Cloud / program assets are pre-scaled to the canvas; user uploads
    are NOT, and ffmpeg-scaling them at decode time strips the user's
    ability to pick width / height / stretch."""
    user_video = tmp_home / "user" / "data" / "mybg.mp4"
    user_video.parent.mkdir(parents=True, exist_ok=True)
    user_video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    result = connected_app.dispatch(PlayVideo(key=_KEY, path=user_video))

    assert result.ok is True
    assert len(stub_media) == 1
    # The whole point of the gate: size=None means "decode native".
    assert stub_media[0][2] is None


def test_play_video_zt_user_asset_keeps_canvas_size(
    connected_app: App, stub_media: list, tmp_home: Path,
) -> None:
    """``.zt`` is a baked JPEG-sequence format — its frames are at the
    encoded resolution and ``ZtDecoder`` resizes during decode.  The
    user-asset gate intentionally EXCLUDES ``.zt`` so it always gets
    canvas size, even when sitting under ``user_content_dir``."""
    user_zt = tmp_home / "user" / "data" / "mytheme.zt"
    user_zt.parent.mkdir(parents=True, exist_ok=True)
    user_zt.write_bytes(b"\xdc\x00")   # .zt magic; content unused

    result = connected_app.dispatch(PlayVideo(key=_KEY, path=user_zt))

    assert result.ok is True
    assert len(stub_media) == 1
    assert stub_media[0][2] == (320, 320)


def test_play_video_publishes_event(
    connected_app: App, stub_media: list, video_file: Path,
) -> None:
    events: list[VideoStarted] = []
    connected_app.events.subscribe(
        VideoStarted, lambda e: events.append(e),  # type: ignore[arg-type, return-value]
    )

    connected_app.dispatch(PlayVideo(key=_KEY, path=video_file))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].path == str(video_file)
    assert events[0].frame_count == 3


def test_play_video_rejects_missing_file(
    connected_app: App, tmp_home: Path,
) -> None:
    result = connected_app.dispatch(
        PlayVideo(key=_KEY, path=tmp_home / "missing.mp4"),
    )

    assert result.ok is False
    assert "does not exist" in result.message


def test_play_video_rejects_directory(
    connected_app: App, tmp_home: Path,
) -> None:
    d = tmp_home / "looks_like.mp4"
    d.mkdir()
    result = connected_app.dispatch(PlayVideo(key=_KEY, path=d))

    assert result.ok is False
    assert "not a regular file" in result.message


@pytest.mark.parametrize("ext", [".txt", ".png", ".jpg", ".exe", ""])
def test_play_video_rejects_non_video_extension(
    connected_app: App, tmp_home: Path, ext: str,
) -> None:
    p = tmp_home / f"file{ext}"
    p.write_bytes(b"x")
    result = connected_app.dispatch(PlayVideo(key=_KEY, path=p))

    assert result.ok is False
    assert "unsupported video extension" in result.message


@pytest.mark.parametrize("ext", [".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"])
def test_play_video_accepts_supported_extensions(
    connected_app: App, stub_media: list, tmp_home: Path, ext: str,
) -> None:
    p = tmp_home / f"clip{ext}"
    p.write_bytes(b"x")
    result = connected_app.dispatch(PlayVideo(key=_KEY, path=p))
    assert result.ok is True, f"{ext} should be accepted"


def test_play_video_unknown_device_returns_failure(
    app: App, video_file: Path,
) -> None:
    """Device must be attached before PlayVideo. No exception, just ok=False."""
    result = app.dispatch(PlayVideo(key="dead:beef", path=video_file))
    assert result.ok is False
    assert "Not attached" in result.message or "dead:beef" in result.message


def test_play_video_single_frame_is_static_no_animation(
    connected_app: App, monkeypatch: pytest.MonkeyPatch, video_file: Path,
) -> None:
    """The animated-vs-static gate: a single-frame video/gif is treated as a
    STATIC background — BackgroundChanged fires (one render), VideoStarted
    does NOT (so the GUI never starts the 15fps animation timer)."""
    from trcc.core.events import BackgroundChanged

    def fake_load(self, device_key, path, size, **kwargs):   # type: ignore[no-untyped-def]
        pb = Playback(
            frames=[_encoded_frame(0xFF000000)],
            fps=15,
        )
        self._playbacks[device_key] = pb
        return pb

    monkeypatch.setattr(MediaService, "load_video", fake_load)

    started: list[VideoStarted] = []
    bg: list[BackgroundChanged] = []
    connected_app.events.subscribe(
        VideoStarted, lambda e: started.append(e),  # type: ignore[arg-type,return-value]
    )
    connected_app.events.subscribe(
        BackgroundChanged, lambda e: bg.append(e),  # type: ignore[arg-type,return-value]
    )

    result = connected_app.dispatch(PlayVideo(key=_KEY, path=video_file))

    assert result.ok is True
    assert result.frame_count == 1
    assert started == [], "single-frame bg must NOT start the animation timer"
    assert len(bg) == 1, "single-frame bg must publish BackgroundChanged (one render)"


def test_play_video_multi_frame_starts_animation(
    connected_app: App, stub_media: list, video_file: Path,
) -> None:
    """Counterpart to the static gate: a multi-frame video DOES publish
    VideoStarted (the stub returns 3 frames)."""
    started: list[VideoStarted] = []
    connected_app.events.subscribe(
        VideoStarted, lambda e: started.append(e),  # type: ignore[arg-type,return-value]
    )
    result = connected_app.dispatch(PlayVideo(key=_KEY, path=video_file))
    assert result.ok is True
    assert result.frame_count == 3
    assert len(started) == 1, "multi-frame video must start the animation timer"


# ── StopVideo Command ────────────────────────────────────────────────


def test_stop_video_clears_playback(
    connected_app: App, stub_media: list, video_file: Path,
) -> None:
    connected_app.dispatch(PlayVideo(key=_KEY, path=video_file))
    assert connected_app.media.playback(_KEY) is not None

    result = connected_app.dispatch(StopVideo(key=_KEY))

    assert result.ok is True
    assert connected_app.media.playback(_KEY) is None


def test_stop_video_publishes_event_only_when_was_playing(
    connected_app: App, stub_media: list, video_file: Path,
) -> None:
    events: list[VideoStopped] = []
    connected_app.events.subscribe(
        VideoStopped, lambda e: events.append(e),  # type: ignore[arg-type, return-value]
    )

    # No playback yet — StopVideo is a no-op
    result_noop = connected_app.dispatch(StopVideo(key=_KEY))
    assert result_noop.ok is True
    assert "no video playing" in result_noop.message
    assert events == []

    # Load a playback, then stop — event fires
    connected_app.dispatch(PlayVideo(key=_KEY, path=video_file))
    connected_app.dispatch(StopVideo(key=_KEY))
    assert len(events) == 1


def test_stop_video_idempotent_on_unattached_device(app: App) -> None:
    """StopVideo on an unattached device succeeds (no transport touched)."""
    result = app.dispatch(StopVideo(key="dead:beef"))
    assert result.ok is True


# ── DisplayService playback-override path ────────────────────────────


class _Surface:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h


class _RecordingRenderer(Renderer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        self._record("create_surface", width, height)
        return _Surface(width, height)

    def open_image(self, path: Path) -> Any:
        self._record("open_image", path)
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
        if degrees % 180 == 90:
            return _Surface(surface.h, surface.w)
        return _Surface(surface.w, surface.h)

    def flip_horizontal(self, surface: Any) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, *args: Any, **kwargs: Any) -> None:
        pass

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b"\xff\xd8"

    def from_raw_rgb24(self, frame: Any) -> Any:
        self._record("from_raw_rgb24", frame)
        return _Surface(frame.width, frame.height)

    def decode_image(self, data: bytes) -> Any:
        self._record("decode_image", data)
        return _Surface(100, 100)


class _StubOverlay(OverlayService):
    def render(self, canvas: Any, config: Any, sensors: dict[str, float],
               clock: dict[str, str] | None = None,
               user_elements: list[dict[str, Any]] | None = None,
               *, temp_unit: str = "C") -> Any:
        del config, sensors, clock, user_elements, temp_unit
        return canvas


def test_display_resolves_background_from_playback_when_present(
    tmp_home: Path,
) -> None:
    """A loaded playback overrides the theme's background."""
    renderer = _RecordingRenderer()
    media = MediaService()
    media._playbacks[_KEY] = Playback(
        frames=[_encoded_frame(0xFF000000)],
        fps=15,
    )
    display = DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=_StubOverlay(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=media,
        paths=FakePaths(tmp_home),
    )

    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0,),
    )
    theme = Theme(
        path=tmp_home / "theme",
        name="t", resolution=(320, 320),
        config={"elements": []},
    )
    display.build_frame(info=info, theme=theme, sensors={},
                        profile=get_profile(100))

    # Playback frames are held ENCODED, so the renderer is asked to decode
    # one — not to convert a raw RGB24 buffer (#264/#256).
    decode_calls = [c for c in renderer.calls if c[0] == "decode_image"]
    assert decode_calls, "Playback frame should be decoded via decode_image"
    assert not [c for c in renderer.calls if c[0] == "from_raw_rgb24"], \
        "a playback frame must not go through the raw-pixel path any more"


def test_display_falls_back_to_theme_when_no_playback(tmp_home: Path) -> None:
    """When MediaService has no Playback for the device, theme background loads."""
    renderer = _RecordingRenderer()
    media = MediaService()   # empty — no playbacks
    display = DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=_StubOverlay(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=media,
        paths=FakePaths(tmp_home),
    )

    # Build a theme dir with a background image
    theme_dir = tmp_home / "themes" / "static"
    theme_dir.mkdir(parents=True)
    (theme_dir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (theme_dir / "trcc.json").write_text(
        '{"name":"static","width":320,"height":320,"elements":[]}',
    )
    theme = Theme(
        path=theme_dir, name="static",
        resolution=(320, 320), config={},
    )
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0,),
    )

    display.build_frame(info=info, theme=theme, sensors={},
                        profile=get_profile(100))

    # No Playback → no from_raw_rgb24
    rgb_calls = [c for c in renderer.calls if c[0] == "from_raw_rgb24"]
    assert rgb_calls == []
    # open_image was called instead (for the PNG)
    open_calls = [c for c in renderer.calls if c[0] == "open_image"]
    assert open_calls, "Static background should be loaded via open_image"


# ── VideoDecoder zero-dim guard ──────────────────────────────────────


class _FakeProc:
    """Stand-in for subprocess.run's CompletedProcess."""

    returncode = 0
    stderr = b""

    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout


def test_video_decoder_rejects_zero_dimension(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``size=(0, h)`` must raise ThemeError, NOT ZeroDivisionError.

    Without the guard, ``frame_bytes = w*h*3 == 0`` makes
    ``len(raw) % frame_bytes`` raise ZeroDivisionError deep in decode().
    The guard converts it to a clear, user-facing ThemeError.
    """
    clip = tmp_home / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr("trcc.services.media._ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "trcc.services.media.subprocess.run",
        lambda *a, **k: _FakeProc(b""),
    )

    with pytest.raises(ThemeError, match="width and height must both be positive"):
        VideoDecoder(clip, size=(0, 480)).decode()


def test_video_decoder_accepts_valid_dimension(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A positive ``size`` decodes the expected frame count past the guard.

    ffmpeg now emits an mjpeg stream rather than rawvideo, so the fake stands
    in two real JPEGs and the decoder must split them on SOI/EOI.
    """
    clip = tmp_home / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    two = _encoded_frame(0xFF102030, 320, 480) + _encoded_frame(0xFF405060, 320, 480)
    monkeypatch.setattr("trcc.services.media._ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "trcc.services.media.subprocess.run",
        lambda *a, **k: _FakeProc(two),
    )

    frames = VideoDecoder(clip, size=(320, 480)).decode()
    assert len(frames) == 2
    assert all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in frames)


# ── VideoFrameCache pixel-parity gate ────────────────────────────────


def _video_display(tmp_home: Path, n_frames: int):
    """A DisplayService with a real renderer and an *n_frames* playback."""
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    media = MediaService()
    media._playbacks[_KEY] = Playback(
        frames=[
            _encoded_frame(0xFF404040 + 0x00404040 * (i % 3))
            for i in range(n_frames)
        ],
        fps=15,
    )
    display = DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=OverlayService(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=media,
        paths=FakePaths(tmp_home),
    )
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0,),
    )
    theme = Theme(
        path=tmp_home / "theme", name="t",
        resolution=(320, 320), config={"elements": []},
    )
    return display, media._playbacks[_KEY], info, theme, get_profile(100)


def _count_composites(display: DisplayService) -> Callable[[], int]:
    """Wrap ``_build_bg_mask`` so a test can read how many composites ran."""
    calls = 0
    original = display._build_bg_mask

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    display._build_bg_mask = counting            # type: ignore[method-assign]
    return lambda: calls


def test_each_cursor_renders_its_own_frame(tmp_home: Path) -> None:
    """Moving the cursor must change the bg the tick composes.

    With the pre-composited VideoFrameCache gone, the one thing stopping a
    video freezing on frame 0 is that ``_bg_mask_key`` carries the playback
    cursor, so a moving cursor misses the single-surface scene cache and
    composes fresh.  The frame cache used to mask that; it is now
    load-bearing, so it gets its own guard.

    Real ``QtRenderer``, so the comparison is on actual encoded wire bytes.
    Frame values are spread across the high bits (64/128/192) to survive
    RGB565 quantisation — 0x01..0x03 would all round to black.

    MUTATION CHECK: drop ``cursor`` from ``_bg_mask_key`` and this fails with
    all three renders identical.
    """
    display, playback, info, theme, profile = _video_display(tmp_home, 3)

    rendered = []
    for cursor in range(3):
        playback.cursor = cursor
        rendered.append(
            display.build_frame(info=info, theme=theme, sensors={}, profile=profile))

    assert len(set(rendered)) == 3


def test_composing_never_scales_with_video_length(tmp_home: Path) -> None:
    """A tick composes ONE frame, however long the video is.

    We used to pre-composite every frame before showing the first one.  On a
    1600x720 panel that froze the UI for 3.96s and retained 4.13GB — 1.84GB
    even for a stock 228-frame cloud theme (#264, #256).  The C# never does
    this: ``GenerateImage`` (UCScreenImage.cs:634) composes background + mask
    + text afresh for every frame it sends, holding ONE mask bitmap rather
    than a copy per frame.

    "One composite per tick" is what makes the cost independent of video
    length, which is the whole fix.

    MUTATION CHECK: restore the pre-build and this fails with 40 != 4 — the
    entire video is composed on the first tick.
    """
    display, playback, info, theme, profile = _video_display(tmp_home, 40)
    composites = _count_composites(display)

    ticks = 4
    for tick in range(ticks):
        playback.cursor = tick
        display.build_frame(info=info, theme=theme, sensors={}, profile=profile)

    assert composites() == ticks


def test_preview_also_composes_one_frame_per_tick(tmp_home: Path) -> None:
    """The GUI preview shares ``_resolve_bg_overlay`` with the wire path, so it
    inherits the same per-tick cost and never pre-builds the whole video.

    MUTATION CHECK: restore the pre-build and this fails with 40 != 4.
    """
    display, playback, info, theme, profile = _video_display(tmp_home, 40)
    composites = _count_composites(display)

    ticks = 4
    for tick in range(ticks):
        playback.cursor = tick
        surface = display.build_preview_surface(
            info=info, theme=theme, sensors={}, profile=profile,
        )
        assert surface is not None

    assert composites() == ticks


def test_rendered_surface_exposes_sent_frame_for_preview(
    tmp_home: Path,
) -> None:
    """``build_frame`` stashes its pre-encode surface so the GUI preview can
    reuse it instead of re-rendering the whole pipeline a second time.

    Contract: None before any frame and after ``invalidate``; the exact
    composited surface (not the encoded bytes) right after a build.
    """
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    media = MediaService()
    media._playbacks[_KEY] = Playback(
        frames=[_encoded_frame(0xFF5A5A5A)],
        fps=15,
    )
    display = DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=OverlayService(renderer),
        settings=Settings(FakePaths(tmp_home)),
        media=media,
        paths=FakePaths(tmp_home),
    )
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0,),
    )
    theme = Theme(
        path=tmp_home / "theme", name="t",
        resolution=(320, 320), config={"elements": []},
    )
    profile = get_profile(100)

    assert display.rendered_surface(_KEY) is None      # nothing built yet

    encoded = display.build_frame(
        info=info, theme=theme, sensors={}, profile=profile,
    )
    surface = display.rendered_surface(_KEY)
    assert surface is not None
    assert not isinstance(surface, bytes)              # the surface, not the wire bytes
    assert isinstance(encoded, bytes)                  # build_frame still returns bytes

    display.invalidate(_KEY)
    assert display.rendered_surface(_KEY) is None       # cleared with the scene


# ── TickDisplay vs RenderAndSend — the two tick ROLES ─────────────────
#
# TickDisplay is the ANIMATION tick; RenderAndSend is the RE-RENDER tick.  They
# are two Commands rather than one with an ``advance`` flag because
# RenderAndSend has six call sites in two roles, and a flag makes passing the
# wrong value easy and its failure silent.  These tests pin the split.


def _playback_for(app: App, video_file: Path) -> Playback:
    """Start a 3-frame playback on the connected device and return it."""
    assert app.dispatch(PlayVideo(key=_KEY, path=video_file)).ok
    playback = app.media.playback(_KEY)
    assert playback is not None
    return playback


def test_tick_display_advances_the_cursor(
    connected_app: App, stub_media: Any, video_file: Path,
) -> None:
    """The animation tick moves the cursor — this is what makes video play.

    Before this Command the advance lived in three UIs (GUI timer, CLI play
    loop, REST tick route); qtgui had no copy and so showed frame 0 forever.
    """
    playback = _playback_for(connected_app, video_file)
    start = playback.cursor

    connected_app.dispatch(TickDisplay(key=_KEY))

    assert playback.cursor != start, "TickDisplay must advance the playback"


def test_render_and_send_never_advances_the_cursor(
    connected_app: App, stub_media: Any, video_file: Path,
) -> None:
    """ROLE PIN: the re-render tick must NOT advance.

    ``DeviceRenderObserver`` dispatches RenderAndSend on every SensorsUpdated,
    and the gui's ``_render_and_send`` does the same for static themes.  Were
    either to advance, a playing video would speed up whenever the sensors
    moved — erratic playback with no obvious cause.  If this test ever fails,
    someone has merged the two roles.
    """
    playback = _playback_for(connected_app, video_file)
    start = playback.cursor

    for _ in range(5):
        connected_app.dispatch(RenderAndSend(key=_KEY))

    assert playback.cursor == start, (
        "RenderAndSend advanced the cursor — the animation and re-render "
        "roles have been merged"
    )


def test_tick_display_reports_video_state_on_the_result(
    connected_app: App, stub_media: Any, video_file: Path,
) -> None:
    """The Result carries cursor/frame_count/interval_ms.

    This is what lets a UI draw its progress bar and pace its own timer
    WITHOUT reaching for ``app.media`` — which is an AttributeError under
    TRCC_DAEMON=1, where the UI holds an AppProxy (#249).
    """
    playback = _playback_for(connected_app, video_file)

    result = connected_app.dispatch(TickDisplay(key=_KEY))

    assert result.frame_count == 3
    assert result.cursor == playback.cursor
    assert result.interval_ms == playback.interval_ms


def test_tick_display_without_a_playback_reports_no_video(
    connected_app: App,
) -> None:
    """No playback → the video fields stay None, NOT zero.

    A UI needs to tell "this theme is not a video" from "frame 0 of a video":
    the gui stops its animation timer on exactly that transition.
    """
    result = connected_app.dispatch(TickDisplay(key=_KEY))

    assert result.cursor is None
    assert result.frame_count is None
    assert result.interval_ms is None


def test_tick_display_honours_pause(
    connected_app: App, stub_media: Any, video_file: Path,
) -> None:
    """Paused playback holds its cursor — the guard lives in Playback.advance.

    The three UI copies each hand-rolled a ``not playback.paused`` check; they
    lose it here rather than move it, because the playback already self-guards.
    """
    playback = _playback_for(connected_app, video_file)
    playback.pause(True)
    start = playback.cursor

    connected_app.dispatch(TickDisplay(key=_KEY))

    assert playback.cursor == start


_ = FitMode   # keep ruff happy


# ─────────────────────────────────────────────────────────────────────
# One decode per apply — the 33-second theme apply
#
# Applying a video theme decoded the SAME file three times: LoadTheme
# unloaded the playback, and every render that fired before PlayVideo
# reloaded it (``_resolve_background`` used to call ``load_video``
# itself).  Two of those renders raced — the GUI thread inside LoadTheme
# and the metrics thread, since EventBus publishes on the caller's
# thread.  Measured on real hardware: 33 s for one apply.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def rendering_app(connected_app: App) -> App:
    """``connected_app`` with a real Renderer attached.

    The module's ``app`` fixture deliberately runs renderer-free (playback
    behaviour is tested by stubbing).  These tests need one: LoadTheme
    returns "no Renderer attached" before it ever reaches the video branch,
    and RenderAndSend needs a DisplayService.
    """
    from trcc.adapters.render.qt import QtRenderer

    connected_app.set_renderer(QtRenderer())
    return connected_app


def _write_video_theme(directory: Path, name: str) -> Path:
    """A theme dir whose background is a bundled video."""
    import json

    theme_dir = directory / name
    theme_dir.mkdir(parents=True, exist_ok=True)
    (theme_dir / "trcc.json").write_text(
        json.dumps({"name": name, "width": 320, "height": 320,
                    "elements": []}), encoding="utf-8",
    )
    (theme_dir / "Theme.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return theme_dir


def _write_static_theme(directory: Path, name: str) -> Path:
    """A theme dir with a static 00.png background."""
    import json

    theme_dir = directory / name
    theme_dir.mkdir(parents=True, exist_ok=True)
    (theme_dir / "trcc.json").write_text(
        json.dumps({"name": name, "width": 320, "height": 320,
                    "elements": []}), encoding="utf-8",
    )
    (theme_dir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return theme_dir


def test_video_theme_apply_decodes_exactly_once(
    rendering_app: App, stub_media: list, tmp_home: Path,
) -> None:
    """Each apply of a video theme costs ONE decode — including repeats.

    The regression this pins is the repeat apply: the first-ever load was
    always one decode, but re-applying (or switching between video themes)
    unloaded the playback and let the intervening renders decode it back,
    then PlayVideo decoded it again.
    """
    from trcc.core.commands import LoadTheme

    theme = _write_video_theme(tmp_home, "vid")

    for attempt in range(1, 4):
        before = len(stub_media)
        result = rendering_app.dispatch(LoadTheme(key=_KEY, path=theme))
        assert result.ok, result.message
        decodes = len(stub_media) - before
        assert decodes == 1, (
            f"apply #{attempt} decoded {decodes}x, expected 1 — a render is "
            f"decoding again, or the playback is being unloaded first"
        )


def test_render_never_decodes_a_video(
    rendering_app: App, stub_media: list, tmp_home: Path,
) -> None:
    """Rendering is a READ — ``PlayVideo`` is the only decoder.

    A render of a video-backed theme with no playback loaded must paint no
    background rather than decode one.  Decoding here is what let a render
    cost 16 seconds, and it used the wrong size policy (``visual_size``
    instead of PlayVideo's oriented canvas).
    """
    from trcc.core.commands import RenderAndSend
    from trcc.services.theme import ThemeService

    theme = _write_video_theme(tmp_home, "vid")
    rendering_app.active_themes[_KEY] = ThemeService().load(theme)
    rendering_app.media.unload(_KEY)
    stub_media.clear()

    rendering_app.dispatch(RenderAndSend(key=_KEY))

    assert stub_media == [], (
        "a render decoded a video — _resolve_background must not call "
        "load_video"
    )


def test_video_theme_replaces_playback_static_theme_stops_it(
    rendering_app: App, stub_media: list, tmp_home: Path,
) -> None:
    """Switching TO a video replaces in place; switching to static stops.

    Replacing avoids a window where the device is mid-load with no playback
    (every render in that window re-decoded).  The static path must keep
    stopping, or an animated→static switch silently keeps showing the old
    animation — a bug LoadTheme's ordering was written to fix.
    """
    from trcc.core.commands import LoadTheme

    stopped: list[VideoStopped] = []
    rendering_app.events.subscribe(
        VideoStopped, stopped.append,  # type: ignore[arg-type]
    )

    first = _write_video_theme(tmp_home, "vid1")
    rendering_app.dispatch(LoadTheme(key=_KEY, path=first))
    assert rendering_app.media.playback(_KEY) is not None
    stopped.clear()

    # video → video: replaced, never stopped.
    second = _write_video_theme(tmp_home, "vid2")
    rendering_app.dispatch(LoadTheme(key=_KEY, path=second))
    assert stopped == [], (
        "a video→video switch published VideoStopped — the playback was "
        "unloaded instead of replaced"
    )
    assert rendering_app.media.playback(_KEY) is not None

    # video → static: stopped, and the playback is gone.
    static = _write_static_theme(tmp_home, "still")
    rendering_app.dispatch(LoadTheme(key=_KEY, path=static))
    assert len(stopped) == 1, (
        "a video→static switch must stop the video, or the old animation "
        "keeps playing under the new theme"
    )
    assert rendering_app.media.playback(_KEY) is None
