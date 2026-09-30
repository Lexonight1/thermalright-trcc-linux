"""The gui skin's CONNECT seam: the session loads, the handler reads.

``LCDHandler.apply_device_config`` -> ``_refresh`` ->
``_restore_theme_and_preview`` used to LOAD the saved theme on first connect
(and a gui-only first-install auto-load before that), while qtgui, the API and
the daemon each primed their own way or not at all (#148).  Loading is now the
session's, once, for every UI (``App._prime``, run before any UI hears
``DeviceConnected``); the handler only shows what the device is rendering.

What must survive the move: a restart brings back the saved theme AND the
cloud/user video background (``SetBackground`` persists ``background_path``,
which gui once wrote on every pick and never read back).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "0402", "pid": "3922",
         "pm": 101, "sub": 0, "fbl": 0}
_KEY = "0402:3922"
_RES = (320, 320)


class _Widget:
    """Permissive stand-in for a shared GUI widget — records nothing it is
    not asked about, so the handler runs without a real Qt panel."""

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _Widgets(dict):
    def __missing__(self, key: str) -> Any:
        self[key] = _Widget()
        return self[key]


def _jpeg(w: int, h: int) -> bytes:
    """One real solid JPEG — playbacks hold ENCODED frames."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(0xFF0000)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(data)


def _write_theme(directory: Path, name: str) -> Path:
    """A minimal loadable theme at the canonical filenames."""
    theme = directory / name
    theme.mkdir(parents=True, exist_ok=True)
    (theme / "trcc.json").write_text(json.dumps({
        "name": name, "width": _RES[0], "height": _RES[1],
        "overlay_enabled": False, "elements": [],
    }))
    (theme / "00.png").write_bytes(_jpeg(*_RES))
    return theme


@pytest.fixture
def handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real ``LCDHandler`` over a real ``App``, plus the media call log.

    ``MediaService.load_video`` is stubbed the way ``test_video_playback``
    does it, so no ffmpeg runs and the test can assert WHAT was asked for.
    """
    from trcc.services.media import MediaService, Playback
    from trcc.ui.gui.lcd_handler import LCDHandler

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(0x0402, 0x3922)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    decoded: list[Path] = []

    def _fake_load(self, device_key: str, path: Path, size, **kwargs):
        decoded.append(Path(path))
        playback = Playback(frames=[_jpeg(*_RES)], fps=kwargs.get("fps", 15))
        self._playbacks[device_key] = playback
        return playback

    monkeypatch.setattr(MediaService, "load_video", _fake_load)

    h = LCDHandler(_KEY, _Widgets(),
                   tmp_path, app=app, lcd_idx=_KEY)
    h._pm.ui_active = True
    return h, app, decoded


def test_a_session_restart_replays_the_saved_theme_and_video_background(
    handler, tmp_path: Path,
) -> None:
    """Fresh ``active_themes`` is what a restart looks like; the session's
    prime brings back the theme and the video, and the handler shows it."""
    h, app, decoded = handler
    theme = _write_theme(app.platform.paths().theme_dir(*_RES), "Aurora")
    app.settings.set_current_theme(_KEY, str(theme.resolve()))
    video = tmp_path / "my_background.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")      # magic only; decode is stubbed
    app.settings.set_background_path(_KEY, str(video))
    assert not app.active_themes, "fixture drift: a restart starts with none"

    app.start_session()
    try:
        h._restore_theme_and_preview()
        assert app.active_themes[_KEY].name == "Aurora"
        assert video in decoded, "the saved video background was not replayed"
        assert h._pm.state.current_theme_path == theme.resolve()
    finally:
        app.close()


def test_the_handler_restore_only_reads(handler) -> None:
    """The handler loads NOTHING: re-loading here ran LoadTheme -> StopVideo
    and wiped the user's background + overlay edits on a tab switch, and a
    second loader beside the session's is the race P4b removed."""
    h, app, _decoded = handler
    theme = _write_theme(app.platform.paths().theme_dir(*_RES), "Aurora")
    app.settings.set_current_theme(_KEY, str(theme.resolve()))

    h._restore_theme_and_preview()

    assert _KEY not in app.active_themes
    assert h._pm.state.current_theme_path == theme.resolve()


@pytest.mark.parametrize("active", [True, False])
def test_themes_landing_show_the_primed_theme_on_the_active_panel_only(
    handler, active: bool,
) -> None:
    """First install: the session primes Theme1 as the data lands, and the
    ACTIVE handler shows it — the job gui's own first-install auto-load did.
    An inactive handler must not write the preview every LCD shares."""
    from trcc.core.events import DataInstalled

    h, app, _decoded = handler
    h._pm.ui_active = active
    app.start_session()
    try:
        theme = _write_theme(app.platform.paths().theme_dir(*_RES), "Theme1")
        app.events.publish(DataInstalled(resolution=_RES, ok=True))
        assert app.active_themes[_KEY].name == "Theme1"

        h._on_data_ready()

        assert h._pm.state.current_theme_path == (
            theme.resolve() if active else None)
    finally:
        app.close()


def test_connecting_a_panel_in_the_gui_writes_nothing_back(handler) -> None:
    """Opening a UI changes nothing on the panel.  Measured through the App's
    log before this: the gui sent SetBrightness / SetOrientation / SetSplitMode
    for every panel on open (6 for 2 panels), qtgui none.  It must still SHOW
    what the App has."""
    from trcc.core.commands import SetBrightness, SetOrientation, SetSplitMode

    h, app, _decoded = handler
    app.dispatch(SetBrightness(key=_KEY, percent=37))
    sent: list[str] = []
    real = app.dispatch

    def spy(cmd):
        sent.append(type(cmd).__name__)
        return real(cmd)
    app.dispatch = spy  # type: ignore[method-assign]

    h.apply_device_config(_KEY, *_RES)

    writes = [n for n in sent if n in {c.__name__ for c in (
        SetBrightness, SetOrientation, SetSplitMode)}]
    assert writes == [], f"opening the panel wrote {writes}"
    assert h._pm.brightness_level == 37
