"""Cross-UI smoke — the same device drives all four UI surfaces.

The UIs are UNIFIED: cli / api / gui / qtgui each dispatch the SAME Commands
(``DiscoverDevices`` / ``ConnectDevice``) to one Command bus.  A device that
works in the core therefore works through every UI — this proves each adapter
is live by driving a real ``MockPlatform`` device through its actual surface
(Typer CliRunner, FastAPI TestClient, the two Qt windows).

Device coverage is the core's job (``test_device_catalog_smoke.py``, every
cooler); this is the UI axis — device-agnostic, one device is enough.
"""
from __future__ import annotations

from pathlib import Path

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"


def _app(tmp_path: Path) -> App:
    return App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())


def test_the_window_runs_no_capture_loop_of_its_own(tmp_path: Path) -> None:
    """The one capture loop is the App's driver, started by ``StartScreencast``.

    The window used to run its own 150 ms timer, which made it the one face
    where a saved screencast theme captured -- and, once the Command started
    the driver, would have been a second loop on the wire.  With the
    scheduler held still, a started cast in an open window grabs NOTHING
    until the App's driver ticks, and then exactly one frame.

    MUTATION CHECK: give the window a capture timer of its own (as its
    ``ScreencastHandler`` once had) and frames arrive while the scheduler is
    still.
    """
    from PySide6.QtTest import QTest

    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.core.commands import StartScreencast
    from trcc.ui.gui.trcc_app import TRCCApp

    scheduler = SyncSendScheduler()
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer(),
              send_scheduler=scheduler)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        assert app.dispatch(StartScreencast(key=_KEY, x=0, y=0, w=32, h=32)).ok
        QTest.qWait(300)

        assert window.uc_theme_setting.screencast_panel.values() == (0, 0, 32, 32), (
            "the window missed ScreencastStarted")
        assert app.platform.capture.regions == [], (
            "the window captured on its own — a second capture loop")
        scheduler.tick(0.0)
        assert app.platform.capture.regions == [(0, 0, 32, 32)]
    finally:
        app.close()


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_lists_and_connects_device(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app

    _ctx.set_platform(MockPlatform([_SPEC], tmp_path))
    _ctx.set_renderer(QtRenderer())  # type: ignore[arg-type]
    try:
        runner = CliRunner()
        listed = runner.invoke(app, ["device", "list"])
        assert listed.exit_code == 0, listed.output
        assert _KEY in listed.output

        connected = runner.invoke(app, ["device", "connect", _KEY])
        assert connected.exit_code == 0, connected.output
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_lists_and_connects_device(tmp_path: Path) -> None:
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    api = build_app(trcc=_app(tmp_path))
    with loopback_client(api) as client:
        listed = client.get("/devices")
        assert listed.status_code == 200, listed.text
        keys = [p["key"] for p in listed.json()["products"]]
        assert _KEY in keys, keys

        connected = client.post(f"/devices/{_KEY}/connect")
        assert connected.status_code == 200, connected.text


# ── GUI (TRCCApp) ─────────────────────────────────────────────────────────────


def test_gui_builds_a_handler_for_the_device(tmp_path: Path) -> None:
    from trcc.ui.gui.trcc_app import TRCCApp

    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        assert _KEY in window._handlers, list(window._handlers)
    finally:
        app.close()


def test_the_window_follows_what_another_ui_does(tmp_path: Path) -> None:
    """A theme loaded and a slideshow switched from ANOTHER UI show in the window.

    The vision's second question.  The gui followed neither: its slideshow
    rotated from its own timer and updated itself from that tick, so once the
    App rotates -- or the CLI loads a theme -- the window has to hear it.

    MUTATION CHECK: drop the ``theme_loaded`` or ``slideshow_changed``
    connection in ``TRCCApp``.
    """
    from PySide6.QtTest import QTest

    from tests.conftest import renderable_theme
    from trcc.core.commands import ConfigureSlideshow, LoadTheme, SetSlideshow
    from trcc.ui.gui.trcc_app import TRCCApp

    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        handler = window._handlers[_KEY]
        assert handler._pm.ui_active, "fixture: the one panel should be the active one"
        root = app.platform.paths().user_content_dir() / "data" / "theme320320"
        theme = renderable_theme(root, "FromTheCli")

        assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok
        assert app.dispatch(ConfigureSlideshow(
            key=_KEY, themes=("FromTheCli",), interval_s=60.0)).ok
        assert app.dispatch(SetSlideshow(key=_KEY, enabled=True)).ok
        QTest.qWait(300)
        assert handler._pm.state.current_theme_path == theme
        assert window.uc_theme_local.is_slideshow()

        assert app.dispatch(SetSlideshow(key=_KEY, enabled=False)).ok
        QTest.qWait(300)
        assert not window.uc_theme_local.is_slideshow()
    finally:
        app.close()


def _spied(app: App) -> list[str]:
    """Record the name of every Command the App is asked to run from here on."""
    sent: list[str] = []
    real = app.dispatch

    def spy(cmd):
        sent.append(type(cmd).__name__)
        return real(cmd)
    app.dispatch = spy                  # type: ignore[method-assign]
    return sent


def test_quitting_the_window_leaves_the_panels_to_the_app(tmp_path: Path) -> None:
    """A window quitting blanked every panel itself -- as a daemon client, for
    every other UI too.  The App blanks them when IT closes, exactly once.

    MUTATION CHECK: restore the handler's ``SleepDevice`` teardown.
    """
    from trcc.ui.gui.trcc_app import TRCCApp

    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        sent = _spied(app)

        window.close()                  # offscreen: no tray, a real quit

        assert not {"SleepDevice", "StopVideo", "SendColor"} & set(sent), sent
        assert app.devices[_KEY].is_connected
    finally:
        app.close()
    assert sent.count("SleepDevice") == 1, sent


def test_a_disconnect_is_not_undone_by_the_window(tmp_path: Path) -> None:
    """The window's teardown sent ``SleepDevice``, which connects its device
    first -- so a disconnect from anywhere was reconnected by an open gui, which
    then wrote brightness, orientation and split back into the App.  Measured.

    MUTATION CHECK: restore the handler's ``SleepDevice`` teardown.
    """
    from PySide6.QtTest import QTest

    from trcc.core.commands import DisconnectDevice
    from trcc.ui.gui.trcc_app import TRCCApp

    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        assert app.dispatch(DisconnectDevice(key=_KEY)).ok
        sent = _spied(app)
        QTest.qWait(500)

        assert not {"EnsureConnected", "ConnectDevice"} & set(sent), sent
        assert not (_KEY in app.devices and app.devices[_KEY].is_connected)
        assert _KEY not in window._handlers
    finally:
        app.close()


# ── qtgui (MainWindow) ────────────────────────────────────────────────────────


def test_qtgui_main_window_constructs_with_a_device(
    tmp_path: Path, make_window,
) -> None:
    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = make_window(app)
        # The unified surface is live: the devices panel exists and the window
        # holds the same App that already has the connected device.
        assert "devices" in window._panels
        assert _KEY in app.devices
    finally:
        app.close()
