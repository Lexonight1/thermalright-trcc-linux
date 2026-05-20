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

from trcc.next.app import App
from trcc.next.core.commands import ConnectDevice, PlayVideo, StopVideo
from trcc.next.core.events import VideoStarted, VideoStopped
from trcc.next.core.models import (
    FitMode,
    Kind,
    ProductInfo,
    RawFrame,
    Theme,
    Wire,
)
from trcc.next.core.ports import Renderer
from trcc.next.core.protocol import get_profile
from trcc.next.services.display import DisplayService
from trcc.next.services.media import MediaService, Playback
from trcc.next.services.overlay import OverlayService
from trcc.next.services.settings import Settings
from trcc.next.services.theme import ThemeService

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
) -> list[tuple[str, Path, tuple[int, int]]]:
    """Replace MediaService.load_video with a no-ffmpeg stub.

    Returns the call log so tests can assert what was decoded.
    """
    calls: list[tuple[str, Path, tuple[int, int]]] = []

    def fake_load(self, device_key: str, path: Path,   # type: ignore[no-untyped-def]
                  size: tuple[int, int], **kwargs):
        calls.append((device_key, path, size))
        # Synthetic playback: 3 fake frames at the requested size
        frames = [
            RawFrame(data=b"\x00" * (size[0] * size[1] * 3),
                     width=size[0], height=size[1])
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

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, *args: Any, **kwargs: Any) -> None:
        pass

    def encode_rgb565(self, surface: Any) -> bytes:
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b"\xff\xd8"

    def from_raw_rgb24(self, frame: Any) -> Any:
        self._record("from_raw_rgb24", frame)
        return _Surface(frame.width, frame.height)


class _StubOverlay(OverlayService):
    def render(self, canvas: Any, config: Any, sensors: dict[str, float],
               clock: dict[str, str] | None = None) -> Any:
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
    )

    # Build a theme dir with a background image
    theme_dir = tmp_home / "themes" / "static"
    theme_dir.mkdir(parents=True)
    (theme_dir / "background.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (theme_dir / "trcc-next.json").write_text(
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


_ = FitMode   # keep ruff happy
