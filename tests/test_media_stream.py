"""A web media source plays live, on the screencast's chain.

``SetMediaPlayer`` with a URL used to record it and play nothing -- worse, it
recorded it OVER a playing background video, which kept playing while the App
said "media", and a restart then showed nothing (driven 2026-09-30).  A URL
now plays: ``StreamReader`` runs ffmpeg on it, ``StreamDriver`` ticks
``CaptureStreamFrame``, and ``SendScreencastFrame`` composites each frame under
the theme -- the chain a screencast already runs.
"""
from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    CaptureStreamFrame,
    ConnectDevice,
    LcdSnapshot,
    PlayVideo,
    RestoreDeviceState,
    SetMediaPlayer,
)
from trcc.core.events import ErrorOccurred
from trcc.core.models import RawFrame
from trcc.core.ports import CaptureNotReady
from trcc.services.media import MediaService
from trcc.services.media_stream import StreamReader
from trcc.services.stream_driver import task_key

_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_URL = "https://example.test/live.m3u8"


# ── The App, with a fake reader: no ffmpeg, no network ───────────────


class _FakeReader:
    """What ``StreamReader`` promises, scripted."""

    def __init__(self, url: str, size: tuple[int, int], fps: int) -> None:
        self.url, self.size, self.closed = url, size, False
        self.fail: str = ""
        self.ready = True

    def latest(self) -> RawFrame:
        if self.fail:
            raise OSError(self.fail)
        if not self.ready:
            raise CaptureNotReady("buffering")
        w, h = self.size
        return RawFrame(data=b"\x40" * (w * h * 3), width=w, height=h)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    import trcc.core.toolchain as toolchain

    monkeypatch.setattr(MediaService, "stream_reader", _FakeReader)
    monkeypatch.setattr(toolchain, "present", lambda tool: True)
    # The real threaded scheduler, so a send completes (``App.send`` waits for
    # the wire); the stream DRIVER is recorded instead of run, so each test
    # dispatches its own ticks and nothing races it.
    tasks: list[str] = []
    monkeypatch.setattr(App, "add_task", lambda self, task: tasks.append(task.key))
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.tasks = tasks                        # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    yield app
    app.close()


def _reader(app: App) -> Any:
    return app.media.stream(_KEY)


def _wire(app: App) -> list[Any]:
    """What this panel's own (scripted) transport was sent."""
    return app.devices[_KEY]._transport.sent   # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]


def _source(app: App) -> str:
    return app.dispatch(LcdSnapshot(key=_KEY)).display_source


def test_a_url_plays_as_a_live_stream(app: App) -> None:
    result = app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))

    assert (result.ok, result.playing) == (True, True)
    assert _reader(app).url == _URL
    assert _reader(app).size == app.devices[_KEY].profile.resolution
    assert _source(app) == "media"
    assert app.tasks == [task_key(_KEY)], "no driver ticks the stream"  # type: ignore[attr-defined]


def test_a_tick_puts_the_stream_s_frame_on_the_panel(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    sent = len(_wire(app))

    result = app.dispatch(CaptureStreamFrame(key=_KEY))

    assert result.ok, result.message
    assert len(_wire(app)) > sent, "no frame reached the wire"


def test_the_url_stops_what_played_before(
    app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trap: the URL was recorded over a playing video, which played on."""
    from tests.test_video_playback import _encoded_frame
    from trcc.services.media import Playback

    def fake_load(self: Any, device_key: str, path: Path, size: Any, **_k: Any) -> Any:
        self._playbacks[device_key] = Playback(frames=[_encoded_frame(0xFF000000, 320, 320)])
        return self._playbacks[device_key]
    monkeypatch.setattr(MediaService, "load_video", fake_load)
    clip = tmp_path / "bg.mp4"
    clip.write_bytes(b"\x00")
    app.dispatch(PlayVideo(key=_KEY, path=clip))

    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))

    assert app.media.playback(_KEY) is None, "the old video kept playing"
    assert app.settings.for_device(_KEY).background_path is None


@pytest.mark.parametrize("uri", ["file:///etc/passwd", "data://x", "ftp://h/v.mp4"])
def test_only_web_schemes_are_opened(app: App, uri: str) -> None:
    """``file:///`` has a ``://`` too: it must not reach ffmpeg."""
    result = app.dispatch(SetMediaPlayer(key=_KEY, uri=uri))

    assert not result.ok
    assert _reader(app) is None
    assert app.settings.for_device(_KEY).media_player_uri is None


def test_another_source_ends_the_stream_within_a_tick(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    reader = _reader(app)
    app.settings.set_media_player_uri(_KEY, None)    # what any other source does

    app.dispatch(CaptureStreamFrame(key=_KEY))

    assert reader.closed and _reader(app) is None


def test_clearing_ends_the_stream(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    reader = _reader(app)

    app.dispatch(SetMediaPlayer(key=_KEY, uri=""))

    assert reader.closed and _reader(app) is None
    assert _source(app) == "background"


def test_a_failed_stream_ends_and_says_why(app: App) -> None:
    heard: list[str] = []
    app.events.subscribe(ErrorOccurred, lambda e: heard.append(e.message))
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    _reader(app).fail = "404 Not Found"

    result = app.dispatch(CaptureStreamFrame(key=_KEY))

    assert not result.ok
    assert heard == ["404 Not Found"]
    assert _reader(app) is None and _source(app) == "background"


def test_buffering_is_not_a_failure(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    _reader(app).ready = False

    assert app.dispatch(CaptureStreamFrame(key=_KEY)).ok
    assert _reader(app) is not None


def test_a_pushed_image_holds_the_stream_off_the_panel(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    app.held.add(_KEY)
    sent = len(_wire(app))

    app.dispatch(CaptureStreamFrame(key=_KEY))

    assert len(_wire(app)) == sent


def test_a_stream_owns_the_panel_s_cadence(app: App) -> None:
    """So the metrics tick does not render the theme over it."""
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    observer = app._render_observer         # pyright: ignore[reportPrivateUsage]
    assert observer._cadence_owner(_KEY) == "stream"


def test_restore_resumes_the_stream(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    app.media.close_stream(_KEY)                     # what a restart leaves

    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert _reader(app) is not None and _reader(app).url == _URL


def test_disconnect_ends_the_stream(app: App) -> None:
    app.dispatch(SetMediaPlayer(key=_KEY, uri=_URL))
    reader = _reader(app)

    app.stop_sender(_KEY)

    assert reader.closed and _reader(app) is None
    assert task_key(_KEY) == "stream:" + _KEY


# ── The reader itself, with real ffmpeg on loopback ──────────────────

_needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                   reason="ffmpeg is not installed")


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    """A 2 s test clip served over HTTP on loopback."""
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=160x120:rate=16",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _wait_for(check: Any, timeout: float = 10.0) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return check()
        except CaptureNotReady:
            time.sleep(0.05)
    raise AssertionError("timed out")


@_needs_ffmpeg
def test_the_reader_yields_frames_at_the_panel_s_size(served: str) -> None:
    reader = StreamReader(f"{served}/clip.mp4", (64, 48), 16)
    try:
        frame = _wait_for(reader.latest)
        assert (frame.width, frame.height, len(frame.data)) == (64, 48, 64 * 48 * 3)
    finally:
        reader.close()
    assert reader._proc is not None and reader._proc.poll() is not None, (  # pyright: ignore[reportPrivateUsage]
        "close() left ffmpeg running")


@_needs_ffmpeg
def test_a_missing_source_says_why(served: str) -> None:
    reader = StreamReader(f"{served}/missing.mp4", (64, 48), 16)
    try:
        with pytest.raises(OSError, match="no picture"):
            _wait_for(reader.latest)
    finally:
        reader.close()


def test_ffmpeg_may_not_open_local_files_for_a_stream() -> None:
    """The scheme allow-list stops a ``file://`` URL; this stops a remote
    playlist that points ffmpeg at a local file."""
    reader = StreamReader.__new__(StreamReader)
    reader.url, reader._size, reader._fps = _URL, (8, 8), 16  # pyright: ignore[reportPrivateUsage]
    cmd = reader._command()                                     # pyright: ignore[reportPrivateUsage]

    allowed = cmd[cmd.index("-protocol_whitelist") + 1].split(",")
    assert {"file", "concat", "subfile", "pipe", "data"}.isdisjoint(allowed)
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")


# ── Every UI can start it, and every UI shows it (item 1c) ───────────
#
# The App streamed, and only the CLI and API could start it; qtgui had no
# media player at all, the gui opened files only, and neither qtgui nor the
# CLI's text said what the panel was showing.


@pytest.fixture
def qtgui_panel(app: App, qtbot: Any) -> Any:
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    panel = DisplayPanel(app, BusBridge(app.events))
    qtbot.addWidget(panel)
    qtbot.waitUntil(lambda: panel._picker.current_key() == _KEY)
    panel._show_state()
    return panel


def test_qtgui_plays_a_url_and_says_so(app: App, qtgui_panel: Any, qtbot: Any) -> None:
    assert qtgui_panel._showing.text() == "the theme's background"
    assert not qtgui_panel._media._close.isEnabled()

    qtgui_panel._media._source.setText(_URL)
    qtgui_panel._media._on_play()
    qtbot.wait(50)

    assert _reader(app).url == _URL
    assert qtgui_panel._showing.text() == f"the media player: {_URL}"
    assert qtgui_panel._media._close.isEnabled()

    qtgui_panel._media._on_close()
    qtbot.wait(50)
    assert _reader(app) is None
    assert qtgui_panel._showing.text() == "the theme's background"


def test_qtgui_shows_a_cast_another_ui_started(
    app: App, qtgui_panel: Any, qtbot: Any,
) -> None:
    from tests.conftest import FakeMic
    from trcc.core.commands import StartScreencast

    app.audio = FakeMic()                       # type: ignore[assignment]
    assert app.dispatch(StartScreencast(key=_KEY, x=0, y=0, w=32, h=32)).ok
    qtbot.wait(50)

    assert qtgui_panel._showing.text() == "a screen cast"


def test_qtgui_browse_fills_the_field(
    qtgui_panel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: ("/videos/clip.mp4", ""))

    qtgui_panel._media._on_browse()

    assert qtgui_panel._media._source.text() == "/videos/clip.mp4"


def test_the_gui_web_video_action_streams_it(
    app: App, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QInputDialog

    from trcc.ui.gui.trcc_app import TRCCApp

    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: win._handlers[_KEY]._pm.ui_active)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (f" {_URL} ", True))

    win.uc_theme_setting.video_panel.action_requested.emit("VideoUrl")
    qtbot.wait(50)

    assert _reader(app).url == _URL
    assert win.uc_theme_setting.video_panel.toggle_btn.isChecked()
    assert "Web Video" in [key for _, key in win._i18n_labels]
    win.close()


def test_the_cli_text_says_what_is_showing(
    tmp_path: Path, cli_runner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trcc.core.toolchain as toolchain
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer

    monkeypatch.setattr(MediaService, "stream_reader", _FakeReader)
    monkeypatch.setattr(toolchain, "present", lambda tool: True)
    monkeypatch.setattr(App, "add_task", lambda self, task: None)
    _ctx.set_platform(MockPlatform([_SPEC], tmp_path))
    _ctx.set_renderer(_CliRenderer())          # type: ignore[arg-type]
    try:
        assert cli_runner.invoke(cli, ["display", "media-player", _KEY, _URL]).exit_code == 0
        out = cli_runner.invoke(cli, ["display", "snapshot", _KEY]).output
        status = cli_runner.invoke(cli, ["status"]).output
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None

    assert f"  showing          the media player: {_URL}" in out.splitlines()
    assert f"  showing:          the media player: {_URL}" in status.splitlines()
