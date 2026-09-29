"""``WS /events`` — the bus, pushed to REST clients.

The API could dispatch and it could read, but it could not **observe**.
Everything a UI learns without asking — a device attached, a theme loaded,
sensors ticked, a video export finishing — reached the two Qt skins through
``BusBridge`` and the CLI through ``AppProxy.events``, and reached a REST
client not at all.  A browser dashboard had to poll, which is both wasteful
and unable to see anything transient: an export publishes its progress once
and never repeats it.

**One wire format, not two.**  The envelopes here are produced by
``ipc.encode_event`` — the same serializer the daemon's Unix socket uses — so
a WebSocket client and a socket client see byte-identical JSON, and a new
Event is zero-touch for both.  Writing a second encoder here would have been
the "one fact expressed twice" trap with a network in the middle.

**Why a plain ``queue.Queue`` and not ``asyncio.Queue``.**  ``EventBus.publish``
is synchronous and arrives from arbitrary threads — the sensor poller, a
device worker, the video-export runner, the IPC fan-out.  ``asyncio.Queue`` is
not thread-safe and its ``put_nowait`` from a foreign thread is a race.  The
handler therefore touches nothing but a thread-safe queue, and the coroutine
drains it.
"""
from __future__ import annotations

import asyncio
import logging
import queue

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...core.events import Event
from ...core.logs import per_frame
from ...ipc import EVENT_QUEUE_MAX, EVENT_TYPES, encode_event
from ._shared import admit_ws

log = logging.getLogger(__name__)
#: The drain runs 20x/s per client — the ``trcc.frame`` family, whose
#: level is set once in ``configure_logging`` so these records are not
#: even constructed at the default verbosity.
frame_log = per_frame(__name__)

router = APIRouter(tags=["events"])

#: How long the drain loop sleeps when the queue is empty.  Bounds delivery
#: latency at 50 ms — well under the 200 ms the preview stream already treats
#: as live — while an idle stream costs 20 no-op wakeups a second.
_IDLE_POLL_S = 0.05


class _Subscription:
    """Bridges one client's chosen event types onto a thread-safe queue.

    Owns its own subscribe/unsubscribe pair so the coroutine's ``finally``
    has exactly one thing to call.  A stream that unsubscribed only some of
    its handlers would leave the EventBus fanning out to a dead socket for
    the life of the process — the same leak class as a reader thread that is
    never stopped.
    """

    __slots__ = ("_bus", "_handlers", "_queue", "dropped")

    def __init__(self, bus: object, types: list[type[Event]]) -> None:
        log.info("_Subscription.__init__: %d event type(s)", len(types))
        self._bus = bus
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=EVENT_QUEUE_MAX)
        self._handlers: list[tuple[type[Event], object]] = []
        self.dropped = 0
        for event_type in types:
            handler = self._on_event
            bus.subscribe(event_type, handler)   # type: ignore[attr-defined]
            self._handlers.append((event_type, handler))

    def _on_event(self, event: Event) -> None:
        """Called on the PUBLISHER's thread — must not touch the event loop."""
        try:
            self._queue.put_nowait(encode_event(event))
        except queue.Full:
            # The client has stopped reading.  Drop the newest rather than
            # block whichever thread published — the render loop is one of
            # them — and count it so a report shows the stall instead of
            # hiding it.  Same policy, and the same reason, as
            # ``IPCServer._on_bus_event``.
            self.dropped += 1
            if self.dropped % 100 == 1:
                log.warning("_on_event: WS event queue full — dropped %d "
                            "event(s); this client is not reading",
                            self.dropped)

    def drain(self) -> list[dict]:
        """Every envelope queued so far, oldest first.  Never blocks."""
        batch: list[dict] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                frame_log.debug("drain: %d envelope(s)", len(batch))
                return batch

    def close(self) -> None:
        log.info("_Subscription.close: unsubscribing %d handler(s), "
                 "%d dropped", len(self._handlers), self.dropped)
        for event_type, handler in self._handlers:
            self._bus.unsubscribe(event_type, handler)   # type: ignore[attr-defined]
        self._handlers.clear()


def _resolve_types(spec: str) -> list[type[Event]] | None:
    """Parse a ``types=`` filter; ``None`` when it names something unknown.

    Refusing beats accepting: a client that misspells an event name and is
    quietly subscribed to nothing waits forever for a stream that was never
    going to carry anything.  ``IPCServer._handle_subscribe`` makes the same
    ruling on the same input.
    """
    wanted = [t.strip() for t in spec.split(",") if t.strip()]
    if not wanted or "*" in wanted:
        log.info("_resolve_types: %r → all %d types", spec, len(EVENT_TYPES))
        return list(EVENT_TYPES.values())
    unknown = [name for name in wanted if name not in EVENT_TYPES]
    if unknown:
        log.warning("_resolve_types: unknown event type(s) %s", unknown)
        return None
    log.info("_resolve_types: %r → %d type(s)", spec, len(wanted))
    return [EVENT_TYPES[name] for name in wanted]


@router.websocket("/events")
async def events_stream(ws: WebSocket, types: str = "*") -> None:
    """Push bus events to the client as JSON, one object per message.

    Each message is ``{"event": "<ClassName>", "fields": {...}}`` — the
    envelope ``ipc.encode_event`` produces, identical to what the daemon's
    Unix socket sends, so a client written against one works against the
    other.

    ``?types=`` filters: a comma-separated list of event class names, or
    ``*`` (the default) for everything.  An unrecognised name closes the
    socket rather than subscribing you to silence.

    Auth: the API token goes in ``?token=``, as for ``/preview/stream`` —
    browsers cannot set headers on a WebSocket handshake.  When no token is
    configured (loopback dev mode) the parameter is accepted and not
    enforced, the same posture as the HTTP middleware.

    Fields that cannot cross JSON are declared in-process only and never sent:
    ``FrameSent.surface`` and ``PreviewResult.surface`` are live renderer
    surfaces, and a client that wants pixels asks ``/preview/stream``.
    """
    log.info("api WS /events: types=%s", types)
    # Admission FIRST — token, or loopback-only when there is none.
    if not await admit_ws(ws):
        return

    wanted = _resolve_types(types)
    if wanted is None:
        await ws.close(code=1008, reason="unknown event type")
        return

    await ws.accept()
    subscription = _Subscription(ws.app.state.trcc.events, wanted)
    sent = 0
    try:
        while True:
            for envelope in subscription.drain():
                await ws.send_json(envelope)
                sent += 1
            await asyncio.sleep(_IDLE_POLL_S)
    except WebSocketDisconnect:
        log.info("api WS /events: client disconnected after %d event(s)", sent)
    finally:
        # Unconditional: a subscription that outlives its socket keeps the
        # EventBus fanning out to a closed connection forever.
        subscription.close()
