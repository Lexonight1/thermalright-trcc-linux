"""``AppProxy.events`` — the client half of the observe channel.

Both Qt skins build a ``BusBridge(app.events)`` at construction.  Until this
existed that was an ``AttributeError`` on a proxy, so a GUI could not run as a
daemon client at all — which is the whole reason ``TRCC_DAEMON=1`` stayed off
by default and the Command bus stayed optional rather than mandatory.

What is pinned here is what a reader cannot see from the code: that events
arrive as REAL typed instances (``BusBridge`` subscribes by type, so a dict
would silently deliver nothing), that opening the stream is lazy (a CLI
one-shot must not pay for a subscription it never reads), and that a dead
stream is surfaced rather than leaving a GUI looking connected while every
panel quietly stops updating.
"""
from __future__ import annotations

import threading
import time

import pytest

from trcc.app import App
from trcc.core.events import DeviceConnected, ThemeLoaded
from trcc.ipc import IPCServer
from trcc.proxy import AppProxy


@pytest.fixture()
def daemon(fake_platform, tmp_path, monkeypatch):
    """A real IPCServer on a throwaway socket, plus a real AppProxy."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    app = App(fake_platform)
    srv = IPCServer(app)
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proxy = AppProxy()
    yield app, srv, proxy
    # Close the CLIENT first: its reader is a thread blocked on a socket read,
    # and one left running logs into whatever test runs next in this worker.
    proxy.close()
    srv.shutdown()


def _wait(seen: list, n: int = 1, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while len(seen) < n and time.monotonic() < deadline:
        time.sleep(0.02)


def test_a_proxy_observes_events_published_daemon_side(daemon) -> None:
    app, _srv, proxy = daemon
    seen: list = []
    proxy.events.subscribe(DeviceConnected, seen.append)
    time.sleep(0.4)                      # reader attaches

    app.events.publish(DeviceConnected(key="0402:3922", resolution=(320, 320)))
    _wait(seen)

    assert len(seen) == 1
    assert seen[0].key == "0402:3922"


def test_events_arrive_as_typed_instances_not_dicts(daemon) -> None:
    """``BusBridge`` subscribes BY TYPE — a dict would deliver nothing."""
    app, _srv, proxy = daemon
    seen: list = []
    proxy.events.subscribe(ThemeLoaded, seen.append)
    time.sleep(0.4)

    app.events.publish(ThemeLoaded(key="k", theme_name="Theme3"))
    _wait(seen)

    assert isinstance(seen[0], ThemeLoaded)
    assert seen[0].theme_name == "Theme3"


def test_type_routing_is_preserved_across_the_wire(daemon) -> None:
    """A subscriber for one type must not receive another."""
    app, _srv, proxy = daemon
    themes: list = []
    connects: list = []
    proxy.events.subscribe(ThemeLoaded, themes.append)
    proxy.events.subscribe(DeviceConnected, connects.append)
    time.sleep(0.4)

    app.events.publish(ThemeLoaded(key="k", theme_name="A"))
    _wait(themes)

    assert len(themes) == 1
    assert connects == [], "an event was delivered to the wrong subscriber"


def test_the_stream_is_lazy(daemon) -> None:
    """A CLI one-shot that never observes must not open a subscription."""
    _app, srv, proxy = daemon
    proxy.dispatch
    time.sleep(0.2)
    assert not srv._subscribers, "the stream opened without anyone asking"

    proxy.events
    time.sleep(0.4)
    assert srv._subscribers, "accessing .events did not open the stream"


def test_events_is_the_same_bus_every_time(daemon) -> None:
    """Two panels subscribing must land on ONE bus, not two streams."""
    _app, srv, proxy = daemon
    first, second = proxy.events, proxy.events
    time.sleep(0.4)
    assert first is second
    assert len(srv._subscribers) == 1


def test_a_dead_daemon_is_surfaced_not_silent(daemon, caplog) -> None:
    """A GUI must not sit there looking connected while nothing updates."""
    import logging

    _app, srv, proxy = daemon
    proxy.events.subscribe(DeviceConnected, lambda _e: None)
    # Wait for the stream to OPEN.  A fixed 0.4 s sleep was a guess: under
    # load the stream was not open yet, the close loop below exited at once,
    # and no "CLOSED" line could ever be logged (2 of 8 under 12 burners).
    deadline = time.monotonic() + 5.0
    while not proxy._stream_open and time.monotonic() < deadline:
        time.sleep(0.02)
    assert proxy._stream_open, "the event stream never opened"

    # What the flag said at the moment the CLOSED line was written.  The
    # reader used to clear the flag FIRST, so a waiter could see False and
    # read the log before the line existed -- a race that failed this test
    # under load.  Recording the flag at emit time pins the order without
    # needing the race to happen.
    flag_at_close: list[bool] = []

    class _Witness(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "event stream CLOSED" in record.getMessage():
                flag_at_close.append(proxy._stream_open)

    witness = _Witness()
    logging.getLogger("trcc.proxy").addHandler(witness)
    try:
        with caplog.at_level(logging.WARNING, logger="trcc.proxy"):
            srv.shutdown()
            deadline = time.monotonic() + 5.0
            while proxy._stream_open and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        logging.getLogger("trcc.proxy").removeHandler(witness)

    assert not proxy._stream_open
    assert any("event stream CLOSED" in r.message for r in caplog.records), (
        "the stream died silently — that is the failure mode this guards"
    )
    assert flag_at_close == [True], (
        "the flag dropped before the CLOSED line was written, so a waiter "
        "can read the log too early")


def test_dispatch_still_works_alongside_a_live_stream(daemon) -> None:
    """The two halves share a daemon; neither may starve the other."""
    from trcc.core.commands import ListLanguages

    _app, _srv, proxy = daemon
    proxy.events.subscribe(DeviceConnected, lambda _e: None)
    time.sleep(0.4)

    result = proxy.dispatch(ListLanguages())
    assert result.ok and result.languages


# =========================================================================
# Session lifecycle — the daemon owns it, a client does not
# =========================================================================


def test_lifecycle_methods_exist_so_run_gui_is_mode_agnostic(daemon) -> None:
    """``run_gui`` must be IDENTICAL in both modes.

    The alternative is a UI asking "am I remote?", which is the environment
    sniffing the architecture forbids and the thing ``AppProxy`` exists to
    make unnecessary.
    """
    _app, _srv, proxy = daemon
    for name in ("dispatch", "events", "start_session", "close",
                 "discover_and_connect"):
        assert hasattr(proxy, name), f"a GUI calls {name}() at boot"


def test_state_reaches_are_still_refused(daemon) -> None:
    """The lifecycle additions must not become a general escape hatch."""
    _app, _srv, proxy = daemon
    for name in ("platform", "settings", "devices", "display", "renderer",
                 "themes", "cloud_themes"):
        with pytest.raises(AttributeError):
            getattr(proxy, name)


def test_close_does_not_disconnect_the_daemons_devices(daemon) -> None:
    """``run_gui``'s ``finally`` calls close() unconditionally.

    In daemon mode that would tear down every OTHER client's panels because
    one window was shut.
    """
    app, _srv, proxy = daemon
    app.devices["0402:3922"] = object()   # pretend something is attached
    try:
        proxy.close()
        assert "0402:3922" in app.devices, "a client closed the daemon's devices"
    finally:
        # Not a real Device; leaving it would break teardown for whatever
        # runs next in this worker.
        app.devices.pop("0402:3922", None)


def test_start_session_does_not_start_a_second_metrics_loop(daemon) -> None:
    """Two loops would poll the sensors twice for one machine."""
    app, _srv, proxy = daemon
    before = app.metrics_loop.is_running
    proxy.start_session()
    assert app.metrics_loop.is_running == before, (
        "the client started the daemon's metrics loop a second time"
    )


def test_start_session_still_answers_the_splash(daemon) -> None:
    """A splash worker waits on on_progress; never calling it hangs the splash."""
    _app, _srv, proxy = daemon
    said: list[str] = []
    proxy.start_session(on_progress=said.append)
    assert said, "the splash callback was never invoked — the splash would hang"


def test_discover_and_connect_reports_the_daemons_fleet(daemon) -> None:
    _app, _srv, proxy = daemon
    said: list[str] = []
    proxy.discover_and_connect(on_progress=said.append)
    assert said and "device(s) attached" in said[-1]


def test_close_stops_this_clients_reader_thread(daemon) -> None:
    """Otherwise every window that opens and closes leaks a thread and an fd."""
    _app, _srv, proxy = daemon
    proxy.events.subscribe(DeviceConnected, lambda _e: None)
    time.sleep(0.4)
    assert proxy._stream_open
    reader = proxy._reader
    assert reader is not None and reader.is_alive()

    proxy.close()

    assert not proxy._stream_open
    assert not reader.is_alive(), "the reader thread outlived close()"
    assert proxy._reader is None


def test_a_relative_path_crosses_the_socket_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """The App resolves a relative path against ITS directory: ``trcc report -o
    rel.txt`` run elsewhere landed in the App's (measured).  22 Commands carry
    a Path; the proxy is the one boundary where caller and App differ."""
    from pathlib import Path

    from trcc import ipc
    from trcc.core.commands import GenerateDebugReport, UploadBootAnimation
    from trcc.core.errors import DaemonUnavailableError
    from trcc.proxy import AppProxy

    monkeypatch.chdir(tmp_path)
    sent: list[dict] = []

    def capture(envelope, timeout):
        sent.append(envelope)
        raise OSError("captured")
    monkeypatch.setattr(ipc, "one_shot_request", capture)

    for cmd in (GenerateDebugReport(output_path=Path("rel.txt")),
                UploadBootAnimation(key="0402:3922", frame_paths=[
                    Path("a.png"), Path("/abs/b.png")], delays_ds=[5, 5])):
        with pytest.raises(DaemonUnavailableError):
            AppProxy().dispatch(cmd)

    assert sent[0]["kwargs"]["output_path"] == str(tmp_path / "rel.txt")
    assert sent[1]["kwargs"]["frame_paths"] == [str(tmp_path / "a.png"), "/abs/b.png"]


def test_a_sent_frame_crosses_the_socket_as_a_picture(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """The preview observes the frame the panel got, even across the socket.

    The live surface is in-process only; the App encodes it ONCE as JPEG for
    subscribers.  Without that, a remote window re-rendered and PNG-encoded
    every frame itself -- 25-29% of a core in the daemon (2026-10-06).

    MUTATION CHECK: return the event unchanged from ``_for_the_wire`` and the
    client gets a FrameSent with no picture at all.
    """
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.events import FrameSent

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    app = App(fake_platform, renderer=QtRenderer())
    srv = IPCServer(app)
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proxy = AppProxy()
    try:
        seen: list = []
        proxy.events.subscribe(FrameSent, seen.append)
        time.sleep(0.4)
        frame = QImage(320, 320, QImage.Format.Format_RGB888)
        frame.fill(QColor(0, 64, 128))

        app.events.publish(FrameSent(key="0402:3922", bytes_sent=204800,
                                     surface=frame))
        _wait(seen)

        assert seen[0].surface is None, "a live surface never crosses"
        picture = QImage.fromData(seen[0].image)
        assert (picture.width(), picture.height()) == (320, 320)
        r, g, b, _ = picture.pixelColor(160, 160).getRgb()
        assert abs(r - 0) <= 4 and abs(g - 64) <= 4 and abs(b - 128) <= 4
    finally:
        proxy.close()
        srv.shutdown()
