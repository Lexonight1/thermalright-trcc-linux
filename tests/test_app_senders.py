"""App-level send-worker lifecycle (foundation incr 2).

A sender is created/started on a successful ``ConnectDevice`` and dropped on
``DisconnectDevice`` / ``close``; ``App.send`` funnels to it.  Driven through a
deterministic ``SyncSendScheduler`` (no threads).  See ``doc/SEND_FOUNDATION.md``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import ConnectDevice, DisconnectDevice

_BULK = "87ad:70db"   # scripted to 854x480 — resent every 150 ms (Bulk)
_SCSI = "0402:3922"   # 320x320 — resent every 1 s (SCSI)
_SPECS = [
    {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5},
    {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100},
]


def _app(tmp_path: Path) -> App:
    return App(MockPlatform(_SPECS, tmp_path), send_scheduler=SyncSendScheduler())


def test_connect_creates_sender_disconnect_drops_it(tmp_path: Path) -> None:
    app = _app(tmp_path)
    try:
        assert app.dispatch(ConnectDevice(key=_BULK)).ok
        assert app.dispatch(ConnectDevice(key=_SCSI)).ok
        assert set(app.senders) == {_BULK, _SCSI}

        # Asked of the real device: the base-class default, every family.
        assert app.devices[_BULK].needs_keepalive is True
        assert app.devices[_SCSI].needs_keepalive is True
        # ...and each sender idles at its device's own interval.
        assert app.senders[_BULK].run_once(0.0) == 0.150
        assert app.senders[_SCSI].run_once(0.0) == 1.0

        app.dispatch(DisconnectDevice(key=_BULK))
        assert _BULK not in app.senders
        assert _SCSI in app.senders
    finally:
        app.close()
    assert app.senders == {} or _SCSI not in app.senders


def test_a_second_connect_closes_the_first_handle_before_reopening(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A connect re-handshakes, and the handle it replaces is closed first --
    not left to garbage collection -- with no "unstable replug" warning.
    MUTATION CHECK: drop the release in ConnectDevice -- the first handle is
    never closed and the warning returns."""
    platform = MockPlatform(_SPECS, tmp_path)
    events: list[str] = []
    real_open = platform.open_transport

    def tracking_open(wire, vid, pid, *args, **kwargs):
        transport = real_open(wire, vid, pid, *args, **kwargs)
        n = sum(e.startswith("open") for e in events) + 1
        events.append(f"open {n}")
        real_close = transport.close

        def close() -> None:
            events.append(f"close {n}")
            real_close()

        transport.close = close  # type: ignore[method-assign]
        return transport

    platform.open_transport = tracking_open  # type: ignore[method-assign]
    app = App(platform, send_scheduler=SyncSendScheduler())
    try:
        assert app.dispatch(ConnectDevice(key=_SCSI)).ok
        first = app.devices[_SCSI]
        with caplog.at_level(logging.WARNING):
            assert app.dispatch(ConnectDevice(key=_SCSI)).ok

        assert app.devices[_SCSI] is not first         # it did re-handshake
        assert events[:3] == ["open 1", "close 1", "open 2"]
        assert "unstable replug" not in caplog.text
    finally:
        app.close()


def test_two_connects_at_once_never_open_the_panel_together(tmp_path: Path) -> None:
    """A UI's connect and the App's own ran side by side at start-up and the
    single-owner claim refused one (Linux and Windows, 2026-10-18).  Every
    connect now holds the App's connect lock.

    MUTATION CHECK: drop ``with app.connecting()`` from ConnectDevice -- the
    two opens overlap."""
    import threading

    platform = MockPlatform(_SPECS, tmp_path)
    real_open = platform.open_transport
    inside, peak = [0], [0]
    count = threading.Lock()

    def slow_open(wire, vid, pid, *args, **kwargs):
        transport = real_open(wire, vid, pid, *args, **kwargs)
        real_transport_open = transport.open

        def open_() -> bool:
            with count:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.2)
            with count:
                inside[0] -= 1
            return real_transport_open()

        transport.open = open_  # type: ignore[method-assign]
        return transport

    platform.open_transport = slow_open  # type: ignore[method-assign]
    app = App(platform, send_scheduler=SyncSendScheduler())
    try:
        results: list[bool] = []
        threads = [threading.Thread(
            target=lambda: results.append(app.dispatch(ConnectDevice(key=_SCSI)).ok))
            for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
        assert results == [True, True]
        assert peak[0] == 1
    finally:
        app.close()


def test_app_send_routes_to_sender(tmp_path: Path) -> None:
    app = _app(tmp_path)
    try:
        app.dispatch(ConnectDevice(key=_SCSI))
        # wait=False: queued, returns True; no sender → False.
        assert app.send(_SCSI, b"\x00\x00", wait=False) is True
        assert app.send("ffff:ffff", b"x", wait=False) is False
    finally:
        app.close()


def test_detach_joins_worker_before_transport_close(tmp_path: Path) -> None:
    # Real ThreadSendScheduler: detach must stop+join the worker BEFORE closing
    # the transport, so no keepalive write lands after disconnect.
    app = App(MockPlatform([_SPECS[0]], tmp_path))   # the volatile Bulk panel
    try:
        assert app.dispatch(ConnectDevice(key=_BULK)).ok
        app.send(_BULK, b"\x00" * 16, wait=True)     # cache a frame; keepalive runs
        transport = app.devices[_BULK]._transport
        time.sleep(0.05)                             # let a keepalive or two fire
        app.dispatch(DisconnectDevice(key=_BULK))     # → stop+join, then close
        writes_at_detach = len(transport.writes)
        time.sleep(0.35)                             # >2 keepalive intervals
        assert len(transport.writes) == writes_at_detach   # worker stopped, no race
        assert _BULK not in app.senders
    finally:
        app.close()


def test_close_shuts_down_all_senders(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.dispatch(ConnectDevice(key=_BULK))
    app.dispatch(ConnectDevice(key=_SCSI))
    assert len(app.senders) == 2
    app.close()
    assert app.senders == {}
