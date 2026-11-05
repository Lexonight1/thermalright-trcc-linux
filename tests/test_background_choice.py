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


@pytest.mark.parametrize("chosen", [
    "module", "media",
])
def test_a_restart_over_a_video_theme_keeps_the_panels_choice(
    app: App, tmp_path: Path, chosen: str,
) -> None:
    """Bug #1: the reload played the theme's own video, recorded as a pick."""
    from trcc.core.models import MediaPlayerBackground, ModuleBackground

    _video_theme(app, tmp_path)
    choice = {"module": ModuleBackground("visualizer"),
              "media": MediaPlayerBackground("/music/song.mp4")}[chosen]
    app.settings.for_device(_KEY).background = choice      # what the last run saved
    app.dispatch(RestoreDeviceState(key=_KEY))
    assert app.settings.for_device(_KEY).background == choice


def test_a_restart_over_a_video_theme_keeps_the_users_picture(
    app: App, tmp_path: Path,
) -> None:
    """Bug #2: the restore replayed ``background_path`` -- the theme's video."""
    from trcc.core.models import PictureBackground

    _video_theme(app, tmp_path)
    picture = tmp_path / "mine.png"
    picture.write_bytes((tmp_path / "themes" / "VideoTheme" / "00.png").read_bytes())
    app.settings.for_device(_KEY).background = PictureBackground(str(picture))
    app.dispatch(RestoreDeviceState(key=_KEY))
    assert app.settings.for_device(_KEY).background == PictureBackground(str(picture))


def test_switching_a_module_off_over_a_video_theme_keeps_the_choice(
    app: App, tmp_path: Path,
) -> None:
    """Bug #3: the theme's video playing again erased the kept module."""
    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    app.end_source(_KEY, keep_choice=True)                  # the module switched off
    assert app.settings.for_device(_KEY).chosen_module == "visualizer"


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


# ── The choice itself (B1): one value, saved and read back ────────────────


def _one_of_each() -> list[Any]:
    from trcc.core.models import (
        MediaPlayerBackground,
        ModuleBackground,
        PictureBackground,
        ThemeBackground,
        VideoBackground,
    )
    return [ThemeBackground(), PictureBackground("/u/a.png"),
            VideoBackground("/u/a.mp4"), MediaPlayerBackground("http://radio/x"),
            ModuleBackground("visualizer")]


def test_every_kind_of_choice_is_tested() -> None:
    """A kind added later is caught here until it joins the round trip."""
    from trcc.core.models import BACKGROUND_CHOICES

    assert {c.kind for c in _one_of_each()} == set(BACKGROUND_CHOICES)


@pytest.mark.parametrize("choice", _one_of_each(), ids=lambda c: c.kind)
def test_a_choice_survives_being_saved(choice: Any) -> None:
    """Through real JSON, as trcc.json stores it."""
    import json

    from trcc.core.models import BackgroundChoice

    assert BackgroundChoice.from_json(json.loads(json.dumps(choice.to_json()))) == choice


@pytest.mark.parametrize("saved", [
    {"kind": "hologram"},             # a newer TRCC's kind
    {"kind": "picture"},              # its field missing
    "junk", None,                     # not an object at all
])
def test_an_unreadable_choice_is_the_themes_own_background(saved: Any) -> None:
    from trcc.core.models import BackgroundChoice, ThemeBackground

    assert BackgroundChoice.from_json(saved) == ThemeBackground()


@pytest.mark.parametrize(("old", "kind"), [
    ({}, "theme"),
    ({"background_path": "/home/u/.trcc-user/data/bg/mine.png"}, "picture"),
    ({"background_path": "/home/u/.trcc-user/data/bg/cut.zt"}, "video"),
    ({"background_path": "/home/u/.trcc/data/web/320320/a001.mp4"}, "video"),
    # The old model stored the theme's OWN video here (bug #2's cause): it is
    # the theme's background, not something the user picked.
    ({"background_path": "/themes/T/Theme.mp4"}, "theme"),
    ({"media_player_uri": "http://radio/x", "background_path": "/u/a.png"}, "media"),
    ({"module_source": "visualizer", "background_path": "/u/a.png"}, "module"),
])
def test_the_old_fields_become_one_choice(old: dict[str, str], kind: str) -> None:
    from trcc.core.models import background_choice_from_fields

    choice = background_choice_from_fields(
        old.get("background_path"), old.get("media_player_uri"),
        old.get("module_source"), theme_dir="/themes/T")
    assert choice.kind == kind


@pytest.mark.parametrize("background_path", [None, "/u/a.png"])
@pytest.mark.parametrize("media_player_uri", [None, "http://radio/x"])
@pytest.mark.parametrize("module_source", [None, "visualizer"])
def test_the_migration_agrees_with_what_the_panel_showed(
    background_path: str | None, media_player_uri: str | None,
    module_source: str | None,
) -> None:
    """Whatever the old fields held, the one choice names the same source the
    old ``display_source`` reported -- a user upgrading sees no change.  That
    reading is spelled here, as it was: media, then module, then the
    background slot."""
    from trcc.core.models import DeviceSettings, PanelSource, background_choice_from_fields

    was = (PanelSource.MEDIA if media_player_uri
           else PanelSource.MODULE if module_source
           else PanelSource.BACKGROUND)
    choice = background_choice_from_fields(background_path, media_player_uri,
                                           module_source, theme_dir="/themes/T")
    assert DeviceSettings(background=choice).display_source is was


def test_a_saved_themes_symlinked_video_is_the_themes_own(tmp_path: Path) -> None:
    """SaveTheme may drop ``Theme.mp4`` in as a symlink to the library video,
    which resolves OUTSIDE the theme folder: it is still the theme's own."""
    from trcc.core.models import ThemeBackground, background_choice_from_fields

    library = tmp_path / "web" / "a001.mp4"
    library.parent.mkdir()
    library.write_bytes(b"")
    theme = tmp_path / "themes" / "Saved"
    theme.mkdir(parents=True)
    (theme / "Theme.mp4").symlink_to(library)
    assert background_choice_from_fields(
        str(theme / "Theme.mp4"), None, None, str(theme)) == ThemeBackground()


# ── B2: every writer records the ONE choice ───────────────────────────────


def _picture(root: Path) -> Path:
    picture = root / "mine.png"
    picture.write_bytes((root / "themes" / "VideoTheme" / "00.png").read_bytes())
    return picture


def _clip(root: Path, name: str = "clip.mp4") -> Path:
    clip = root / name
    clip.write_bytes(b"decoded by the faked load_video")
    return clip


def _choice(app: App) -> Any:
    return app.settings.for_device(_KEY).background


def test_a_picked_picture_is_the_choice(app: App, tmp_path: Path) -> None:
    from trcc.core.commands import SetBackground
    from trcc.core.models import PictureBackground

    _video_theme(app, tmp_path)
    picture = _picture(tmp_path)
    assert app.dispatch(SetBackground(key=_KEY, path=picture)).ok
    assert _choice(app) == PictureBackground(str(picture))


def test_a_picked_video_is_the_choice(app: App, tmp_path: Path) -> None:
    from trcc.core.commands import PlayVideo
    from trcc.core.models import VideoBackground

    _video_theme(app, tmp_path)
    clip = _clip(tmp_path)
    assert app.dispatch(PlayVideo(key=_KEY, path=clip)).ok
    assert _choice(app) == VideoBackground(str(clip))


def test_the_media_player_is_the_choice_not_its_file(app: App, tmp_path: Path) -> None:
    from trcc.core.commands import SetMediaPlayer
    from trcc.core.models import MediaPlayerBackground

    _video_theme(app, tmp_path)
    song = _clip(tmp_path, "song.mp4")
    assert app.dispatch(SetMediaPlayer(key=_KEY, uri=str(song))).ok
    assert _choice(app) == MediaPlayerBackground(str(song))


def test_a_module_is_the_choice(app: App, tmp_path: Path) -> None:
    from trcc.core.models import ModuleBackground

    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    assert _choice(app) == ModuleBackground("visualizer")


@pytest.mark.parametrize(("keep_choice", "kind"), [(True, "module"), (False, "theme")])
def test_a_module_switched_off_keeps_the_choice_ended_does_not(
    app: App, tmp_path: Path, keep_choice: bool, kind: str,
) -> None:
    """Off (``keep_choice``) the theme's own video plays again -- and chooses
    nothing, so the module comes back with its module (bug #3)."""
    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    app.end_source(_KEY, keep_choice=keep_choice)
    assert _choice(app).kind == kind


def test_a_video_theme_reloaded_keeps_the_choice(app: App, tmp_path: Path) -> None:
    """A restart's reload plays the theme's own video -- which chooses
    nothing (bugs #1 and #2, the writing half)."""
    from trcc.core.models import ModuleBackground

    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    assert app.dispatch(LoadTheme(key=_KEY, path=tmp_path / "themes" / "VideoTheme",
                                  reset_overrides=False)).ok
    assert _choice(app) == ModuleBackground("visualizer")


@pytest.mark.parametrize(("picked", "kind"), [(True, "theme"), (False, "module")])
def test_only_a_picked_theme_ends_the_choice(
    app: App, tmp_path: Path, picked: bool, kind: str,
) -> None:
    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    assert app.dispatch(LoadTheme(key=_KEY, path=tmp_path / "themes" / "VideoTheme",
                                  picked=picked)).ok
    assert _choice(app).kind == kind


@pytest.mark.parametrize(("keep_override", "kind"), [(False, "theme"), (True, "picture")])
def test_a_stop_ends_a_picked_file_a_teardown_keeps_it(
    app: App, tmp_path: Path, keep_override: bool, kind: str,
) -> None:
    from trcc.core.commands import SetBackground

    _video_theme(app, tmp_path)
    assert app.dispatch(SetBackground(key=_KEY, path=_picture(tmp_path))).ok
    app.dispatch(StopVideo(key=_KEY, keep_override=keep_override))
    assert _choice(app).kind == kind


def test_the_choice_is_saved_with_its_kind(app: App, tmp_path: Path) -> None:
    """``asdict`` alone saves a choice as its bare fields -- which kind it
    was is lost, and every one reads back as the theme's own."""
    from trcc.core.models import ModuleBackground
    from trcc.services.settings import Settings

    _video_theme(app, tmp_path)
    app.show_source("visualizer", _KEY, _Waiting(), 30.0)
    reread = Settings(app.platform.paths())
    assert reread.for_device(_KEY).background == ModuleBackground("visualizer")


@pytest.mark.parametrize(("old", "kind"), [
    ({"module_source": "visualizer"}, "module"),
    ({"background_path": "THEME/Theme.mp4"}, "theme"),
    ({"background_path": "/u/mine.png"}, "picture"),
    ({}, "theme"),
])
def test_a_v7_config_folds_into_one_choice(
    tmp_path: Path, old: dict[str, str], kind: str,
) -> None:
    """The real file, read by the real Settings -- the theme folder is the
    panel's ``current_theme``."""
    import json

    from trcc.services.settings import Settings, config_path

    from .conftest import FakePaths

    paths = FakePaths(tmp_path)
    theme = str(tmp_path / "themes" / "T")
    device = {"current_theme": theme,
              **{k: v.replace("THEME", theme) for k, v in old.items()}}
    path = config_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 7, "devices": {_KEY: device}}),
                    encoding="utf-8")
    assert Settings(paths).for_device(_KEY).background.kind == kind
