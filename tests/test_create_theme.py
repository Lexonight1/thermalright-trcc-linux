"""``CreateTheme``: a background, a mask and an overlay shown as one Command.

It was the body of ``POST /display/create-theme`` until the route kept only
the upload handling.  These pin what the Command reports, above all
``reads``: the files a caller that staged them must keep, on a failure too.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    CreateTheme,
    ListMasks,
    LoadImage,
    ResolveOverlay,
)
from trcc.ipc import decode_command, encode_command
from trcc.services.media import MediaService, Playback

from .mock_platform import MockPlatform

_KEY = "0402:3922"
_TEXT = {"id": "t1", "type": "text", "x": 10, "y": 20, "text": "hi"}


def _overlay_file(folder: Path, *elements: dict) -> Path:
    path = folder / "overlay.json"
    path.write_text(json.dumps({"elements": list(elements)}))
    return path


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
    panel: App, png: Path, themed_video: Path, tmp_path: Path,
) -> None:
    """The mask goes to the user's mask library under its file's name, as a
    mask uploaded from any UI does, so the panel reads that copy -- and every
    GUI's mask browser lists it."""
    result = panel.dispatch(CreateTheme(key=_KEY, background=themed_video,
                                        mask=png,
                                        overlay=_overlay_file(tmp_path, _TEXT)))

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
    panel: App, png: Path, tmp_path: Path,
) -> None:
    result = panel.dispatch(CreateTheme(
        key=_KEY, background=png, mask=png,
        overlay=_overlay_file(tmp_path, {"id": "b", "type": "bogus"})))

    assert result.ok is False
    assert result.message == "Invalid element type 'bogus'"
    assert result.reads == ()


def test_the_file_and_the_shorthand_make_one_overlay(
    panel: App, png: Path, tmp_path: Path,
) -> None:
    """The file's elements first, then one per metric shorthand -- replacing
    the layout, so nothing from before stays."""
    result = panel.dispatch(CreateTheme(
        key=_KEY, background=png, overlay=_overlay_file(tmp_path, _TEXT),
        metrics=("cpu:temp:160,90:#ff8800:24", "gpu:0:temp:1,2")))

    assert result.ok, result.message
    shown = panel.dispatch(ResolveOverlay(key=_KEY)).elements
    assert [(e.type, e.text, e.metric, e.x, e.y, e.color, e.size)
            for e in shown] == [
        ("text", "hi", "", 10, 20, "#ffffff", 16),
        ("metric", "", "cpu:temp", 160, 90, "#ff8800", 24),
        ("metric", "", "gpu:0:temp", 1, 2, "#ffffff", 16),
    ]


@pytest.mark.parametrize(("overlay", "metrics", "message"), [
    (b"{nope", (), "invalid overlay JSON: Expecting property name enclosed in "
                   "double quotes: line 1 column 2 (char 1)"),
    (b"[]", (), "overlay JSON must be an object"),
    (b'{"elements": 3}', (), "overlay JSON 'elements' must be a list of "
                            "element dicts"),
    (None, ("cpu:temp",), "Invalid metric spec 'cpu:temp'; metric spec: "
                          "'metric_key:x,y[:color[:size]]' — e.g. "
                          "'cpu:temp:160,90:#ff8800:24'.  color defaults to "
                          "'#ffffff', size defaults to 16."),
])
def test_bad_overlay_input_is_refused_before_the_panel_changes(
    panel: App, png: Path, tmp_path: Path,
    overlay: bytes | None, metrics: tuple[str, ...], message: str,
) -> None:
    """Read first, then show: a typo in the overlay must not leave the panel
    on a new background with the old overlay."""
    path = None
    if overlay is not None:
        path = tmp_path / "overlay.json"
        path.write_bytes(overlay)
    before = panel.settings.for_device(_KEY).current_theme

    result = panel.dispatch(CreateTheme(key=_KEY, background=png, overlay=path,
                                        metrics=metrics))

    assert (result.ok, result.message, result.reads) == (False, message, ())
    assert panel.settings.for_device(_KEY).current_theme == before


def test_a_name_saves_it_as_that_theme(panel: App, png: Path) -> None:
    result = panel.dispatch(CreateTheme(key=_KEY, background=png, name="mine"))

    assert result.ok, result.message
    library = panel.platform.paths().user_theme_dir(320, 320)
    assert result.theme_path == str(library / "mine")
    assert result.message == ("Theme created from bg.png (image, 320x320), "
                              f"saved as 'mine' at {library / 'mine'}")
    assert panel.settings.for_device(_KEY).current_theme == result.theme_path


def test_a_saved_video_theme_reads_its_own_copy(
    panel: App, themed_video: Path,
) -> None:
    """Saving switches the panel to the saved theme, which plays its own
    copy -- so the given file is no longer read, and a caller that staged
    it may delete it."""
    shown = panel.dispatch(CreateTheme(key=_KEY, background=themed_video))
    saved = panel.dispatch(CreateTheme(key=_KEY, background=themed_video,
                                       name="clip"))

    assert shown.reads == (str(themed_video),)
    assert saved.ok, saved.message
    assert saved.reads == ()
    assert panel.settings.for_device(_KEY).chosen_file is None


def test_a_taken_name_asks_before_it_overwrites(panel: App, png: Path) -> None:
    assert panel.dispatch(CreateTheme(key=_KEY, background=png, name="mine")).ok

    taken = panel.dispatch(CreateTheme(key=_KEY, background=png, name="mine"))
    replaced = panel.dispatch(CreateTheme(key=_KEY, background=png, name="mine",
                                          overwrite=True))

    assert (taken.ok, taken.target_exists, taken.message) == (
        False, True, "A theme named 'mine' already exists.")
    assert replaced.ok, replaced.message


def test_a_bad_name_is_refused_before_the_panel_changes(
    panel: App, png: Path,
) -> None:
    result = panel.dispatch(CreateTheme(key=_KEY, background=png,
                                        name="../escape"))

    assert (result.ok, result.message) == (
        False, "invalid theme name '../escape' (no path separators, '..', "
               "leading '.', or NUL bytes)")
    assert panel.settings.for_device(_KEY).current_theme is None


def test_it_crosses_the_socket_whole(tmp_path: Path) -> None:
    """Paths, a None and a tuple of strings all survive the IPC envelope."""
    sent = CreateTheme(key=_KEY, background=tmp_path / "bg.png", mask=None,
                       overlay=tmp_path / "o.json", metrics=("cpu:temp:1,2",),
                       name="mine", overwrite=True)

    assert decode_command(encode_command(sent)) == sent


# ── Every UI passes every input: the CLI and the API ─────────────────────────


class _Cli:
    """``trcc theme create KEY ...`` through the real CLI, and where it saves."""

    def __init__(self, platform: MockPlatform) -> None:
        from typer.testing import CliRunner

        from trcc.ui.cli.main import app as cli_app

        self._runner = CliRunner()
        self._app = cli_app
        self.paths = platform.paths()

    def __call__(self, *args: str) -> Any:
        return self._runner.invoke(self._app, ["theme", "create", _KEY, *args])

    def overlay(self) -> list[tuple[str, str]]:
        """What the panel's overlay shows, asked as a UI asks."""
        from trcc.ui.cli import _ctx

        shown = _ctx.get_app().dispatch(ResolveOverlay(key=_KEY)).elements
        return [(e.type, e.text or e.metric) for e in shown]


@pytest.fixture
def cli(tmp_path: Path, renderer: QtRenderer,
        monkeypatch: pytest.MonkeyPatch) -> Iterator[_Cli]:
    """The real CLI on one mock panel, the same App across calls."""
    from trcc.ui.cli import _ctx

    monkeypatch.setattr(MediaService, "load_video", _stub_load_video(renderer))
    platform = MockPlatform([{"vid": "0402", "pid": "3922"}], tmp_path)
    _ctx.set_platform(platform)
    _ctx.set_renderer(renderer)
    yield _Cli(platform)
    _ctx.get_app.cache_clear()
    _ctx._platform_override = None
    _ctx._renderer_override = None


def _stub_load_video(renderer: QtRenderer) -> Any:
    frame = renderer.encode_jpeg(renderer.create_surface(320, 320))

    def load_video(self: MediaService, device_key: str, path: Path,
                   size: Any, **kwargs: Any) -> Playback:
        playback = Playback(frames=[frame] * 3, fps=kwargs.get("fps", 15))
        self._playbacks[device_key] = playback
        return playback
    return load_video


def test_the_cli_passes_every_input(cli: _Cli, png: Path, tmp_path: Path) -> None:
    """--mask, --overlay and --metric all reach the one Command, and the
    result is saved under NAME."""
    done = cli("mine", "--bg", str(png), "--mask", str(png),
               "--overlay", str(_overlay_file(tmp_path, _TEXT)),
               "--metric", "cpu:temp:160,90:#ff8800:24")

    assert done.exit_code == 0, done.output
    themes = cli.paths.user_theme_dir(320, 320)
    assert f"saved as 'mine' at {themes / 'mine'}" in done.output
    assert (themes / "mine").is_dir()
    assert (cli.paths.user_mask_dir(320, 320) / "custom_bg").is_dir()
    assert cli.overlay() == [("text", "hi"), ("metric", "cpu:temp")]


def test_the_cli_saves_a_video_theme(cli: _Cli, png: Path, tmp_path: Path) -> None:
    """A video background was API-only; the CLI's --bg takes one now."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    assert cli("first", "--bg", str(png)).exit_code == 0
    done = cli("clip", "--bg", str(video))

    assert done.exit_code == 0, done.output
    assert "Theme created from clip.mp4 (video, 320x320)" in done.output


def test_the_cli_asks_before_it_overwrites(cli: _Cli, png: Path) -> None:
    assert cli("mine", "--bg", str(png)).exit_code == 0

    taken = cli("mine", "--bg", str(png))
    replaced = cli("mine", "--bg", str(png), "--overwrite")

    assert taken.exit_code == 1
    assert "Re-run with --overwrite to replace it." in taken.output
    assert replaced.exit_code == 0, replaced.output


def test_a_bad_metric_leaves_the_cli_panel_as_it_was(cli: _Cli, png: Path) -> None:
    """It used to show the background first and refuse the metric after."""
    done = cli("mine", "--bg", str(png), "--metric", "cpu:temp")

    assert done.exit_code == 1
    assert "Invalid metric spec 'cpu:temp'" in done.output
    assert not (cli.paths.user_content_dir() / "single-image").exists()


@pytest.fixture
def api(tmp_path: Path, renderer: QtRenderer) -> Any:
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    app = App(MockPlatform([{"vid": "0402", "pid": "3922"}], tmp_path),
              renderer=renderer)
    with loopback_client(build_app(trcc=app)) as client:
        yield client


def _create(client: Any, png: Path, **form: Any) -> Any:
    return client.post(f"/devices/{_KEY}/display/create-theme",
                       files={"background": ("bg.png", png.read_bytes(),
                                             "image/png")},
                       data=form)


def test_the_api_takes_the_shorthand_and_a_name(api: Any, png: Path) -> None:
    done = _create(api, png, metric=["cpu:temp:160,90", "gpu:0:temp:1,2"],
                   name="mine")

    assert done.status_code == 200, done.text
    assert done.json()["theme_name"] == "mine"
    assert done.json()["message"] == ("theme created from bg.png (image, "
                                      "320x320), saved as 'mine'")
    shown = api.get(f"/devices/{_KEY}/display/overlay-elements").json()
    assert [e["metric"] for e in shown["elements"]] == ["cpu:temp", "gpu:0:temp"]


def test_a_taken_name_is_a_409_the_client_can_retry(api: Any, png: Path) -> None:
    """The negotiation /theme/save has: same body, target_exists, 409."""
    assert _create(api, png, name="mine").status_code == 200

    taken = _create(api, png, name="mine")
    replaced = _create(api, png, name="mine", overwrite="true")

    assert taken.status_code == 409
    assert (taken.json()["target_exists"], taken.json()["theme_name"]) == (True, "mine")
    assert replaced.status_code == 200, replaced.text


def test_an_uploaded_video_is_kept_while_the_panel_plays_it(
    panel: App, themed_video: Path,
) -> None:
    """The staged upload is the file PlayVideo reads, so the cleanup keeps
    it -- and, staged under its own name, its folder.  Removing the folder
    would pull the clip out from under the panel."""
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    with loopback_client(build_app(trcc=panel)) as client:
        done = client.post(f"/devices/{_KEY}/display/create-theme",
                           files={"background": ("clip.mp4",
                                                 themed_video.read_bytes(),
                                                 "video/mp4")})

    assert done.status_code == 200, done.text
    playing = Path(panel.settings.for_device(_KEY).chosen_file or "")
    uploads = panel.platform.paths().user_content_dir() / "uploads"
    assert playing.name == "clip.mp4"
    assert uploads in playing.parents
    assert playing.read_bytes() == themed_video.read_bytes()
