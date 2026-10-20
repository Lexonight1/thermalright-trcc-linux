"""Event fan-out over the daemon socket (Phase B, increment 4).

Daemon mode had Command dispatch and no events at all, which is why a GUI
cannot be a pure bus client: both skins build a ``BusBridge`` from
``app.events``, and ``AppProxy`` has no such attribute.  This is the server
half -- a client can now ask for a stream and receive real events.

What is pinned here is the behaviour a reader cannot see from the code: that
the handler does not do the work, that one event is encoded once for many
listeners, that a dead client is evicted rather than blocking the daemon, and
that a full queue drops events instead of growing memory.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import IO, Any, NamedTuple

import pytest

from trcc import ipc
from trcc.app import App
from trcc.core.events import DeviceConnected, FrameSent, SensorsUpdated
from trcc.ipc import IPCServer, decode_event


def _server(app: App) -> IPCServer:
    srv = IPCServer(app)
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Client(NamedTuple):
    """A connected client and the ONE reader over its socket.

    One reader, not one per read: a second ``makefile()`` starts with an empty
    buffer, so anything the first had already pulled off the socket past the
    line it was asked for would be silently dropped -- an ack and the first
    event can arrive in the same segment.
    """

    sock: socket.socket
    reader: IO[bytes]

    def send(self, payload: dict[str, Any]) -> None:
        self.sock.sendall(json.dumps(payload).encode() + b"\n")

    def subscribe(self, types: list[str]) -> dict[str, Any]:
        self.send({"subscribe": types})
        return json.loads(self.reader.readline().decode())

    def next_event(self) -> Any:
        line = self.reader.readline()
        assert line, "stream closed before an event arrived"
        return decode_event(json.loads(line.decode()))

    def close(self) -> None:
        """Reader first -- the order is the point.

        ``socket.close()`` only releases the descriptor once no ``makefile()``
        wrapper is left alive.  Closing the socket while the reader still
        holds it defers the real close to the collector, which then reports
        "unclosed socket" against whatever test happens to be running by then.
        """
        self.reader.close()
        self.sock.close()


@pytest.fixture()
def server(fake_platform, tmp_path, monkeypatch):
    """A real IPCServer on a throwaway socket."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    app = App(fake_platform)
    srv = _server(app)
    yield app, srv
    srv.shutdown()


@pytest.fixture()
def connect(server):
    """Hand out clients and close every one of them, both halves, in order."""
    opened: list[_Client] = []

    def _open() -> _Client:
        channel = ipc.channel()
        assert channel is not None
        sock = channel.connect(5.0)
        client = _Client(sock, sock.makefile("rb"))
        opened.append(client)
        return client

    yield _open
    for client in opened:
        client.close()


def test_a_subscriber_receives_a_real_event(server, connect) -> None:
    app, _srv = server
    client = connect()
    ack = client.subscribe(["DeviceConnected"])
    assert ack["ok"] and ack["subscribed"] == ["DeviceConnected"]

    app.events.publish(DeviceConnected(key="0402:3922", resolution=(320, 320)))

    event = client.next_event()
    assert isinstance(event, DeviceConnected)
    assert event.key == "0402:3922"
    assert event.resolution == (320, 320)


def test_star_subscribes_to_everything(server, connect) -> None:
    app, _srv = server
    client = connect()
    assert client.subscribe(["*"])["ok"]

    app.events.publish(SensorsUpdated(readings={"cpu:temp": 42.0}))
    assert isinstance(client.next_event(), SensorsUpdated)


def test_an_unknown_event_name_is_refused_not_silently_accepted(connect) -> None:
    """A typo'd type would otherwise look like a subscription that never fires."""
    ack = connect().subscribe(["NoSuchEvent"])
    assert not ack["ok"]
    assert "NoSuchEvent" in ack["message"]


def test_an_empty_subscription_is_refused(connect) -> None:
    assert not connect().subscribe([])["ok"]


def test_one_event_reaches_every_listener(server, connect) -> None:
    """Two clients, one publish -- both get it (encoded once, written twice)."""
    app, _srv = server
    a, b = connect(), connect()
    assert a.subscribe(["DeviceConnected"])["ok"]
    assert b.subscribe(["*"])["ok"]

    app.events.publish(DeviceConnected(key="0416:5302", resolution=(480, 480)))

    for client in (a, b):
        assert client.next_event().key == "0416:5302"


def test_a_subscriber_only_gets_the_types_it_asked_for(server, connect) -> None:
    app, _srv = server
    client = connect()
    assert client.subscribe(["DeviceConnected"])["ok"]

    app.events.publish(SensorsUpdated(readings={"cpu:temp": 1.0}))   # not wanted
    app.events.publish(DeviceConnected(key="k", resolution=(1, 1)))  # wanted

    event = client.next_event()
    assert isinstance(event, DeviceConnected), "an unwanted type leaked through"


def test_a_dead_client_is_evicted_and_dispatch_keeps_working(server, connect) -> None:
    """The daemon must survive a subscriber vanishing mid-stream."""
    from trcc.core.commands import ListLanguages
    from trcc.ipc import decode_result, encode_command

    app, srv = server
    client = connect()
    assert client.subscribe(["*"])["ok"]
    client.close()                    # vanish without unsubscribing

    # Publishing must not raise, and must eventually drop the subscriber.
    for _ in range(50):
        app.events.publish(DeviceConnected(key="k", resolution=(1, 1)))
    deadline = time.monotonic() + 5.0
    while srv._subscribers and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not srv._subscribers, "a closed client was never evicted"

    # And the request/response half still answers.
    other = connect()
    other.send(encode_command(ListLanguages()))
    reply = decode_result(json.loads(other.reader.readline().decode()))
    assert reply.ok


def test_the_handler_never_encodes_on_the_publishing_thread(server) -> None:
    """``_on_bus_event`` must only enqueue.

    It runs synchronously on whichever thread published -- the render tick for
    ``FrameSent``, the udev thread for eleven others.  Encoding or writing
    there is the defect this design exists to avoid.
    """
    import trcc.ipc as ipc_mod

    _app, srv = server

    def _explode(_event):
        raise AssertionError(
            "encode_event was called on the publishing thread — the handler "
            "must only enqueue",
        )

    monkey = ipc_mod.encode_event
    ipc_mod.encode_event = _explode
    try:
        srv._on_bus_event(FrameSent(key="k", bytes_sent=1))
    finally:
        ipc_mod.encode_event = monkey
    # Buffered, not encoded and not written.
    assert srv._event_q.qsize() == 1


def test_a_full_queue_drops_instead_of_blocking(server) -> None:
    """A stalled subscriber must not be able to grow the daemon's memory."""
    from trcc.ipc import EVENT_QUEUE_MAX

    _app, srv = server
    for _ in range(EVENT_QUEUE_MAX + 50):
        srv._on_bus_event(FrameSent(key="k", bytes_sent=1))

    assert srv._event_q.qsize() <= EVENT_QUEUE_MAX
    assert srv._dropped >= 50


def test_the_ack_is_written_only_after_the_subscriber_is_registered(
    server, connect, monkeypatch,
) -> None:
    """The receipt must not outrun the registration.

    ``_handle_subscribe`` used to ack first and append second, so every event
    published in that window reached nobody while the client had already been
    told it was attached.  Registering first, on its own, is no better: the
    fan-out snapshots its targets under ``_sub_lock`` and writes OUTSIDE it,
    so an event line could be written before the ack line and the client
    would parse an event where it expects its receipt.  Both halves are why
    the two operations now happen under one lock, registration first.

    Pinned DIRECTLY rather than left to a race.  The test below
    (``test_shutdown_releases_subscriber_streams``) does observe the same bug,
    but only by losing a race: it passed 25 times out of 25 in isolation and
    failed once under 16 xdist workers.  A gate that fires by luck is not a
    gate.  This one reads the invariant at the only instant it is decidable —
    the moment the ack is handed to the socket.
    """
    from trcc import ipc as ipc_mod

    _app, srv = server
    registered_at_ack: list[int] = []
    real_send = ipc_mod._send_json

    def _spy(sock: socket.socket, payload: dict[str, Any]) -> None:
        if payload.get("ok") and "subscribed" in payload:
            registered_at_ack.append(len(srv._subscribers))
        real_send(sock, payload)

    monkeypatch.setattr(ipc_mod, "_send_json", _spy)
    assert connect().subscribe(["*"])["ok"]

    assert registered_at_ack == [1], (
        "the ack was written while the server held "
        f"{registered_at_ack} subscriber(s) — registration must precede the "
        "receipt, or an event published in the gap is lost to a client that "
        "has been told it is attached"
    )


def test_shutdown_releases_subscriber_streams(server, connect) -> None:
    """Otherwise the daemon cannot exit while a GUI is attached."""
    _app, srv = server
    assert connect().subscribe(["*"])["ok"]
    assert srv._subscribers

    srv.shutdown()
    assert not srv._subscribers
