"""App-level send-worker lifecycle (foundation incr 2).

A sender is created/started on a successful ``ConnectDevice`` and dropped on
``DisconnectDevice`` / ``close``; ``App.send`` funnels to it.  Driven through a
deterministic ``SyncSendScheduler`` (no threads).  See ``doc/SEND_FOUNDATION.md``.
"""
from __future__ import annotations

from pathlib import Path

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import ConnectDevice, DisconnectDevice

_BULK = "87ad:70db"   # scripted to 854x480 — volatile (Bulk)
_SCSI = "0402:3922"   # 320x320 — non-volatile (SCSI)
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

        # Volatility comes from the wire, via the real device.
        assert app.devices[_BULK].needs_keepalive is True
        assert app.devices[_SCSI].needs_keepalive is False

        app.dispatch(DisconnectDevice(key=_BULK))
        assert _BULK not in app.senders
        assert _SCSI in app.senders
    finally:
        app.close()
    assert app.senders == {} or _SCSI not in app.senders


def test_app_send_routes_to_sender(tmp_path: Path) -> None:
    app = _app(tmp_path)
    try:
        app.dispatch(ConnectDevice(key=_SCSI))
        # wait=False: queued, returns True; no sender → False.
        assert app.send(_SCSI, b"\x00\x00", wait=False) is True
        assert app.send("ffff:ffff", b"x", wait=False) is False
    finally:
        app.close()


def test_close_shuts_down_all_senders(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.dispatch(ConnectDevice(key=_BULK))
    app.dispatch(ConnectDevice(key=_SCSI))
    assert len(app.senders) == 2
    app.close()
    assert app.senders == {}
