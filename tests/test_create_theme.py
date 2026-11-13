"""``CreateTheme``: a background, a mask and an overlay shown as one Command.

It was the body of ``POST /display/create-theme`` until the route kept only
the upload handling.  These pin what the Command reports, above all
``reads``: the files a caller that staged them must keep, on a failure too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, CreateTheme, ListMasks, LoadImage
from trcc.ipc import decode_command, encode_command
from trcc.services.media import MediaService, Playback

from .mock_platform import MockPlatform

_KEY = "0402:3922"
_TEXT = {"id": "t1", "type": "text", "x": 10, "y": 20, "text": "hi"}


@pytest.fixture
def renderer() -> QtRenderer:
    return QtRenderer()


@pytest.fixture
def panel(tmp_path: Path, renderer: QtRenderer) -> App:
    """A connected 320x320 SCSI panel."""
    app = App(MockPlatform([{"vid": "0402", "pid": "3922"}], tmp_path),
              renderer=renderer)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


@pytest.fixture
def png(tmp_path: Path, renderer: QtRenderer) -> Path:
    path = tmp_path / "bg.png"
    path.write_bytes(renderer.encode_png(
        renderer.create_surface(64, 64, color=(0, 0, 255, 255))))
    return path


@pytest.fixture
def themed_video(panel: App, png: Path, tmp_path: Path, renderer: QtRenderer,
                 monkeypatch: pytest.MonkeyPatch) -> Path:
    """A video file, on a panel that shows a theme to play it over.

    ``MediaService.load_video`` is stubbed: decoding is ffmpeg's job, tested
    where ``PlayVideo`` is; here only the composition is under test.
    """
    frame = renderer.encode_jpeg(renderer.create_surface(320, 320))

    def load_video(self: MediaService, device_key: str, path: Path,
                   size: Any, **kwargs: Any) -> Playback:
        playback = Playback(frames=[frame] * 3, fps=kwargs.get("fps", 15))
        self._playbacks[device_key] = playback
        return playback

    monkeypatch.setattr(MediaService, "load_video", load_video)
    assert panel.dispatch(LoadImage(key=_KEY, path=png)).ok
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return video


def test_a_picture_is_copied_so_nothing_given_is_still_read(
    panel: App, png: Path,
) -> None:
    result = panel.dispatch(CreateTheme(key=_KEY, background=png))

    assert result.ok, result.message
    assert (result.animated, result.width, result.height) == (False, 320, 320)
    assert result.reads == ()
    assert result.message == "Theme created from bg.png (image, 320x320)"


def test_a_video_mask_and_overlay_are_all_shown_and_kept(
    panel: App, png: Path, themed_video: Path,
) -> None:
    """The mask goes to the user's mask library under its file's name, as a
    mask uploaded from any UI does, so the panel reads that copy -- and every
    GUI's mask browser lists it."""
    result = panel.dispatch(CreateTheme(key=_KEY, background=themed_video,
                                        mask=png, elements=(_TEXT,)))

    assert result.ok, result.message
    assert result.animated is True
    assert result.reads == (str(themed_video),)
    library = panel.platform.paths().user_mask_dir(320, 320)
    settings = panel.settings.for_device(_KEY)
    assert settings.mask_path == str(library / "custom_bg" / "01.png")
    assert settings.overlay_enabled is True
    masks = panel.dispatch(ListMasks(resolution=(320, 320))).masks
    assert [m.name for m in masks] == ["custom_bg"]


def test_a_refused_mask_still_names_the_video_that_plays(
    panel: App, tmp_path: Path, themed_video: Path,
) -> None:
    """The video started before the mask step refused, and plays on -- a
    caller that deleted it would pull the file out from under the panel."""
    result = panel.dispatch(CreateTheme(key=_KEY, background=themed_video,
                                        mask=tmp_path / "missing.png"))

    assert result.ok is False
    assert result.reads == (str(themed_video),)


def test_a_refused_overlay_ends_it_with_that_steps_message(
    panel: App, png: Path,
) -> None:
    result = panel.dispatch(CreateTheme(
        key=_KEY, background=png, mask=png,
        elements=({"id": "b", "type": "bogus"},)))

    assert result.ok is False
    assert result.message == "Invalid element type 'bogus'"
    assert result.reads == ()


def test_it_crosses_the_socket_whole(tmp_path: Path) -> None:
    """A Path, a None and a tuple of dicts all survive the IPC envelope."""
    sent = CreateTheme(key=_KEY, background=tmp_path / "bg.png", mask=None,
                       elements=(_TEXT,))

    assert decode_command(encode_command(sent)) == sent
