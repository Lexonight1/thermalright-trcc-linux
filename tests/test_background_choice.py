"""What the user chose for a panel's background must survive everything that
is not the user choosing again.

Four bugs, each DRIVEN on the mock before this file existed (2026-11-05), all
from one cause: a panel's choice is spread over four nullable settings fields
plus ``pending_background``, kept consistent by ``_only_source``, and a theme
playing its OWN video writes ``background_path`` -- the field that means "the
user picked this override" -- so loading that theme wipes the real choice.

1. a restart over a video theme forgets the panel's module / media choice;
2. a restart over a video theme replaces the user's own picture with the
   theme's video;
3. switching a module off over a video theme forgets the choice (his decision
   10-21: switching a module off KEEPS each panel's choice);
4. stopping a video while a module draws leaves the choice saying "module"
   with nothing drawing -- every UI shows the module as running.

On image themes 1-3 already hold; the video theme is what breaks them.

These are ``xfail(strict=True)`` until the fix -- one background choice per
panel (memory: project_layer_abc_trace) -- lands: a test that starts passing
fails the run, so the marker cannot outlive the bug it documents.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import LcdSnapshot, LoadTheme, RestoreDeviceState, StopVideo
from trcc.core.models import RawFrame
from trcc.core.ports import CaptureNotReady, FrameSource
from trcc.services.media import MediaService, Playback

from .conftest import renderable_theme
from .mock_platform import MockPlatform
from .test_video_playback import _encoded_frame

_KEY = "0402:3922"
_BUG = ("a panel's choice is four nullable fields kept by _only_source; one background "
        "choice per panel fixes it (memory: project_layer_abc_trace)")


class _Waiting(FrameSource):
    """A module's picture that has nothing to show yet."""

    name = "probe"

    def latest(self) -> RawFrame:
        raise CaptureNotReady("nothing yet")

    def close(self) -> None:
        pass


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    """A connected 320x320 panel; videos load without ffmpeg."""

    def load_video(self: MediaService, device_key: str, path: Path,
                   size: Any = None, **_: Any) -> Playback:
        playback = Playback(frames=[_encoded_frame(0xFF000000, 320, 320)] * 2, fps=15)
        self._playbacks[device_key] = playback
        return playback

    monkeypatch.setattr(MediaService, "load_video", load_video)
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    yield app
    app.close()


def _video_theme(app: App, root: Path) -> None:
    theme = renderable_theme(root / "themes", "VideoTheme")
    (theme / "Theme.mp4").write_bytes(b"decoded by the faked load_video")
    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok


@pytest.mark.xfail(strict=True, reason=_BUG)
@pytest.mark.parametrize(("field", "value"), [
    ("module_source", "visualizer"),
    ("media_player_uri", "/music/song.mp4"),
])
def test_a_restart_over_a_video_theme_keeps_the_panels_choice(
    app: App, tmp_path: Path, field: str, value: str,
) -> None:
    _video_theme(app, tmp_path)
    setattr(app.settings.for_device(_KEY), field, value)   # what the last run saved
    app.dispatch(RestoreDeviceState(key=_KEY))
    assert getattr(app.settings.for_device(_KEY), field) == value


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_a_restart_over_a_video_theme_keeps_the_users_picture(
    app: App, tmp_path: Path,
) -> None:
    _video_theme(app, tmp_path)
    picture = tmp_path / "mine.png"
    picture.write_bytes((tmp_path / "themes" / "VideoTheme" / "00.png").read_bytes())
    app.settings.for_device(_KEY).background_path = str(picture)
    app.dispatch(RestoreDeviceState(key=_KEY))
    assert app.settings.for_device(_KEY).background_path == str(picture)


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_switching_a_module_off_over_a_video_theme_keeps_the_choice(
    app: App, tmp_path: Path,
) -> None:
    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    app.end_source(_KEY, keep_choice=True)                  # the module switched off
    assert app.settings.for_device(_KEY).module_source == "visualizer"


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_the_panel_never_says_module_with_nothing_drawing(
    app: App, tmp_path: Path,
) -> None:
    assert app.dispatch(LoadTheme(
        key=_KEY, path=renderable_theme(tmp_path / "themes", "T"))).ok
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    app.dispatch(StopVideo(key=_KEY))
    snapshot = app.dispatch(LcdSnapshot(key=_KEY))
    drawing = app.media.live_kind(_KEY) is not None
    assert drawing or snapshot.display_source != "module", (
        "the snapshot says a module draws the panel, and nothing does")
