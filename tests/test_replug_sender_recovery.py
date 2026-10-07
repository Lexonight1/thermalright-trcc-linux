"""An unstable replug must not wedge the panel forever (#254).

chamalkalakshan tested a real unplug/replug end to end on v9.9.4.  A clean
re-enumeration recovered.  An UNSTABLE one — theirs re-enumerated three times
after two `can't set config #1, error -71` failures — left the GUI logging
`send() called before connect()` 7000+ times over five minutes and never
recovering, while the device itself was properly connected and a separate
`trcc display test` worked fine.  Only killing the process fixed it.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice

_KEY = "0402:3922"
_SPEC = [{"type": "lcd", "name": "Frozen Warframe", "vid": "0402",
          "pid": "3922", "fbl": 100}]


@pytest.fixture
def app() -> App:
    from .mock_platform import MockPlatform
    return App(platform=MockPlatform(_SPEC, Path(tempfile.mkdtemp())))


def test_a_sender_left_on_a_released_device_is_replaced(app: App) -> None:
    """The wedge: a worker outliving the device it was built for.

    ``_on_device_attached`` runs on the hotplug thread, so a burst of `add`
    events can register a sender just after another pass released the device
    it was built for.  ``start_sender`` then saw a sender already present and
    returned — so the reconnected device never got a worker, and the stale one
    kept writing to a dead handle.

    MUTATION CHECK: restore ``if key in self.senders: return`` and this fails
    — the sender is still the stale object and its device is disconnected.
    """
    app.dispatch(ConnectDevice(key=_KEY))
    stale = app.senders[_KEY]

    # The interleave, exactly: the device goes away AFTER its sender exists.
    app.devices.pop(_KEY).disconnect()
    assert _KEY in app.senders, "precondition: the stale worker is still registered"

    app.dispatch(ConnectDevice(key=_KEY))       # the replug finally settles

    assert app.devices[_KEY].is_connected
    assert app.senders[_KEY] is not stale, "the stale worker must be replaced"
    assert app.senders[_KEY].device is app.devices[_KEY], \
        "the worker must drive the device we actually hold"


def test_a_healthy_reconnect_keeps_its_sender(app: App) -> None:
    """Idempotence still holds — this must not churn a working worker.

    A duplicate `add` (coldplug replay, a monitor that repeats itself) has to
    stay a no-op, or every replay would tear down a perfectly good wire.
    """
    app.dispatch(ConnectDevice(key=_KEY))
    sender = app.senders[_KEY]

    app.start_sender(_KEY)
    app.start_sender(_KEY)

    assert app.senders[_KEY] is sender


def test_the_worker_always_matches_the_attached_device(app: App) -> None:
    """The invariant the wedge broke, stated once: whatever is in ``senders``
    drives whatever is in ``devices``."""
    app.dispatch(ConnectDevice(key=_KEY))
    for _ in range(3):                          # an unstable burst
        app.devices.pop(_KEY).disconnect()
        app.dispatch(ConnectDevice(key=_KEY))

    assert app.senders[_KEY].device is app.devices[_KEY]
    assert app.devices[_KEY].is_connected


# ── A panel taken without an unplug (2026-10-06) ─────────────────────────────
#
# A VM with the panel passed through took it from a running daemon: the
# kernel node vanished, writes failed, the device's own `_reconnect` could not
# re-open — and closed its transport.  `is_connected` read the HANDSHAKE, so the
# corpse stayed "connected": the video tick kept sending into it (two records
# and an ErrorOccurred per frame, three hours), and when the panel came back a
# replug's `_connect_unit` saw "already connected" and did nothing.


def _take_the_panel(app: App) -> None:
    """The node vanishes under a connected device: writes fail, re-open fails."""
    import errno

    transport = app.devices[_KEY]._transport

    def gone(*_args: object, **_kwargs: object) -> bool:
        raise OSError(errno.ENODEV, "No such device")

    transport.send_cdb = gone           # type: ignore[method-assign]
    transport.open = lambda: False      # type: ignore[method-assign]
    app.send(_KEY, b"\x00" * 204800)    # the frame that finds out


def test_a_panel_whose_transport_closed_is_not_connected(app: App) -> None:
    app.dispatch(ConnectDevice(key=_KEY))

    _take_the_panel(app)

    assert not app.devices[_KEY]._transport.is_open, "precondition: closed"
    assert not app.devices[_KEY].is_connected


def test_producers_stop_sending_into_a_closed_transport(
    app: App, caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-frame render refuses a dead panel instead of writing to it.

    MUTATION CHECK: read ``is_connected`` off the handshake alone and every
    render below queues a frame, each one failing in the worker.
    """
    import logging

    from trcc.core.commands import RenderAndSend
    from trcc.core.errors import TransportError

    from .conftest import show_a_theme

    app.dispatch(ConnectDevice(key=_KEY))
    show_a_theme(app, _KEY)
    _take_the_panel(app)
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        results = [app.dispatch(RenderAndSend(key=_KEY)) for _ in range(20)]
        # Drain: a waited frame runs after anything queued before it.
        with pytest.raises(TransportError):
            app.send(_KEY, b"\x00" * 204800)

    assert [r.connected for r in results] == [False] * 20
    refused = [r for r in caplog.records
               if "send() called before connect()" in r.getMessage()]
    assert len(refused) == 1, "only the drain frame may reach the dead transport"


def test_a_replug_heals_a_panel_whose_transport_closed(app: App) -> None:
    """``_connect_unit`` releases the corpse and connects a fresh device.

    MUTATION CHECK: read ``is_connected`` off the handshake alone and the
    hotplug add answers "already connected" — the same dead object stays.
    """
    from trcc.core.events import DeviceAttached

    app.dispatch(ConnectDevice(key=_KEY))
    corpse = app.devices[_KEY]
    _take_the_panel(app)

    app._on_device_attached(DeviceAttached(key=_KEY, vid=0x0402, pid=0x3922))

    assert app.devices[_KEY] is not corpse
    assert app.devices[_KEY].is_connected
    assert app.senders[_KEY].device is app.devices[_KEY]
    assert app.send(_KEY, b"\x00" * 204800) is True
