"""PlayVideo / StopVideo Commands + DisplayService playback-override path.

PlayVideo and StopVideo are wrappers around MediaService that publish
events; their primary effect is to make ``DisplayService._resolve_background``
read from the playback instead of the active theme. The render-pipeline
behavior is tested in isolation by stubbing the Playback on a Fake App.

Real ffmpeg decoding isn't tested here — MediaService.load_video is
monkeypatched to return a synthetic Playback so tests run without ffmpeg.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, PlayVideo, StopVideo
from trcc.core.errors import ThemeError
from trcc.core.events import VideoStarted, VideoStopped
from trcc.core.models import (
    FitMode,
    Kind,
    ProductInfo,
    RawFrame,
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
            RawFrame(data=b"\x00" * (w * h * 3),
                     width=w, height=h)
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
            frames=[RawFrame(data=b"\x00" * (320 * 320 * 3),
                             width=320, height=320)],
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
        frames=[RawFrame(data=b"\x00" * (320 * 320 * 3),
                         width=320, height=320)],
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

    # Renderer was asked to convert the Playback frame to a surface
    rgb_calls = [c for c in renderer.calls if c[0] == "from_raw_rgb24"]
    assert rgb_calls, "Playback frame should be converted via from_raw_rgb24"


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
    """A positive ``size`` decodes the expected frame count past the guard."""
    clip = tmp_home / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr("trcc.services.media._ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "trcc.services.media.subprocess.run",
        lambda *a, **k: _FakeProc(b"\x00" * (320 * 480 * 3)),
    )

    frames = VideoDecoder(clip, size=(320, 480)).decode()
    assert len(frames) == 1
    assert frames[0].width == 320
    assert frames[0].height == 480


# ── VideoFrameCache pixel-parity gate ────────────────────────────────


def test_video_cache_frames_byte_identical_to_direct_build(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache path must produce byte-identical frames to the direct path.

    This is the gate for wiring the VideoFrameCache into ``build_frame``:
    ``get_surface(cursor)`` returns exactly what ``_build_bg_mask`` builds
    for that cursor, so the encoded wire bytes are identical whether the
    bg came from the cache or a fresh per-frame rebuild.  A real
    ``QtRenderer`` is used so the comparison is on actual encoded bytes,
    not stand-in surfaces; three distinct solid-colour frames prove the
    cursor maps to the right frame (not the same one returned thrice).
    """
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    media = MediaService()
    # Three distinct solid-colour frames → distinct backgrounds.  Values
    # are spread across the high bits (64 / 128 / 192) so they survive
    # RGB565 quantisation — 0x01..0x03 would all round to black.
    frames = [
        RawFrame(data=bytes([64 * (i + 1)]) * (320 * 320 * 3),
                 width=320, height=320)
        for i in range(3)
    ]
    media._playbacks[_KEY] = Playback(frames=frames, fps=15)
    playback = media._playbacks[_KEY]

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

    def render_each_cursor() -> list[bytes]:
        out: list[bytes] = []
        for index in range(len(frames)):
            playback.cursor = index
            out.append(display.build_frame(
                info=info, theme=theme, sensors={}, profile=profile,
            ))
        return out

    # Cache path (the new default for multi-frame video).
    cached = render_each_cursor()

    # Direct path: clear both caches, force ``_video_cache`` to opt out,
    # re-render the same cursors through ``_build_bg_mask`` each tick.
    display.invalidate_all()
    monkeypatch.setattr(display, "_video_cache", lambda *a, **k: None)
    direct = render_each_cursor()

    assert cached == direct                    # byte-identical
    assert len(set(cached)) == 3               # cursor genuinely maps per-frame


def test_video_cache_builds_once_then_serves_lookups(
    tmp_home: Path,
) -> None:
    """The CPU win: ``_build_bg_mask`` runs once per frame (at the single
    cache build), then every later tick is a list lookup — NOT a fresh
    rebuild.  15 ticks over a 9-frame loop must call ``_build_bg_mask``
    exactly 9 times (the build), not 15."""
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    media = MediaService()
    n_frames = 9
    media._playbacks[_KEY] = Playback(
        frames=[
            RawFrame(data=bytes([64 * (i % 3 + 1)]) * (320 * 320 * 3),
                     width=320, height=320)
            for i in range(n_frames)
        ],
        fps=15,
    )
    playback = media._playbacks[_KEY]
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

    builds = 0
    original = display._build_bg_mask

    def counting_build_bg_mask(*args: Any, **kwargs: Any) -> Any:
        nonlocal builds
        builds += 1
        return original(*args, **kwargs)

    display._build_bg_mask = counting_build_bg_mask  # type: ignore[method-assign]

    for tick in range(15):
        playback.cursor = tick % n_frames
        display.build_frame(info=info, theme=theme, sensors={}, profile=profile)

    # Built once (one _build_bg_mask per frame), then 15 ticks served as
    # lookups — not 15 rebuilds.
    assert builds == n_frames


def test_build_preview_surface_uses_the_video_cache(tmp_home: Path) -> None:
    """The GUI preview path goes through the same VideoFrameCache as the wire
    path — so a video theme's preview is a cache lookup, not a fresh
    per-tick decode.  15 preview calls over a 9-frame loop call
    ``_build_bg_mask`` exactly 9 times (the single cache build)."""
    from trcc.adapters.render.qt import QtRenderer

    renderer = QtRenderer()
    media = MediaService()
    n_frames = 9
    media._playbacks[_KEY] = Playback(
        frames=[
            RawFrame(data=bytes([64 * (i % 3 + 1)]) * (320 * 320 * 3),
                     width=320, height=320)
            for i in range(n_frames)
        ],
        fps=15,
    )
    playback = media._playbacks[_KEY]
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

    builds = 0
    original = display._build_bg_mask

    def counting_build_bg_mask(*args: Any, **kwargs: Any) -> Any:
        nonlocal builds
        builds += 1
        return original(*args, **kwargs)

    display._build_bg_mask = counting_build_bg_mask  # type: ignore[method-assign]

    for tick in range(15):
        playback.cursor = tick % n_frames
        surface = display.build_preview_surface(
            info=info, theme=theme, sensors={}, profile=profile,
        )
        assert surface is not None

    assert builds == n_frames   # the cache, not 15 fresh decodes


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
        frames=[RawFrame(data=bytes([90]) * (320 * 320 * 3),
                         width=320, height=320)],
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


_ = FitMode   # keep ruff happy
