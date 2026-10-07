"""IPC for the shared App — manifold dispatch over a Unix socket.

The daemon (`trcc daemon`) owns one ``App`` and serves requests
through ``IPCServer``.  Clients (the ``AppProxy`` that ``_boot.trcc()``
returns by default) call ``dispatch(cmd)``
and the proxy turns each call into one socket round-trip.

Wire format — one line of JSON per request.

  Dispatch::

      {"command": "SendColor",
       "kwargs": {"key": "0402:3922", "r": 255, "g": 0, "b": 0}}

  Control::

      {"kill": true}

The dispatcher looks ``command`` up in the Command registry, builds the
dataclass via type-hint-driven coercion (str → Path, list → tuple, dict
→ nested dataclass, int → Enum), invokes ``app.dispatch(cmd)``, and
serializes the returned Result back as::

      {"type": "SendResult", "ok": true,
       "message": "Sent 204800 bytes (#ff0000)",
       "key": "0402:3922", "bytes_sent": 204800}

The serialization is reflective over ``dataclasses.fields`` so adding a
new Command + Result is zero-touch for IPC.

Events travel the same way on a subscription stream::

      {"event": "FrameSent",
       "fields": {"key": "0402:3922", "bytes_sent": 204800,
                  "surface": null, "display_colors": []}}

Bytes are encoded as ``{"__bytes__": "<base64>"}`` so binary payloads
(``SendFrame``) survive JSON.  ``Path`` survives as ``str(path)``.
"""
from __future__ import annotations

import base64
import dataclasses
import functools
import inspect
import json
import logging
import os
import queue
import socket
import threading
import time
import types
import typing
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import core
from .core import commands as _commands_module
from .core import events as _events_module
from .core import results as _results_module
from .core.commands import Command
from .core.commands._base import Query
from .core.events import AppStopping, Event, FrameSent
from .core.logs import dispatch_origin, per_frame, recurring_warning
from .core.models import IN_PROCESS_ONLY_KEY
from .core.results import Result

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)
# ``FrameSent`` is published once per rendered frame, so the event codec sits
# on the per-frame family — silent by default, or every daemon-mode frame
# writes a record (see core.logs and project_silence_has_a_mirror).
frame_log = per_frame(__name__)

_SOCK_NAME = "trcc.sock"
_BYTES_MARKER = "__bytes__"
_DEFAULT_TIMEOUT_S = 30.0

#: Events buffered between the publishing thread and the fan-out thread.
#: BOUNDED on purpose: a subscriber that stops reading must not be able to
#: grow the daemon's memory without limit.  At the measured 270 events/s for
#: nine panels at full frame rate this is ~2s of slack, which is far longer
#: than a healthy client needs and short enough that a dead one is obvious.
#: Public because it is shared policy, not an ipc internal: the API's
#: ``WS /events`` bridges the same bus to a network socket and must
#: bound its buffer the same way, for the same reason.
EVENT_QUEUE_MAX = 512
#: How long the fan-out thread will wait on one subscriber's socket before
#: giving up on it.  Kept short because the fan-out is shared: a stalled
#: client must not hold up delivery to the healthy ones for longer than this,
#: and a client that cannot absorb 103 bytes in half a second is gone.
_SUBSCRIBER_SEND_TIMEOUT_S = 0.5


# =========================================================================
# Socket path
# =========================================================================


def runtime_dir() -> Path:
    """This user's base directory for every TRCC socket.

    ``$XDG_RUNTIME_DIR``, else ``~/.cache`` — never ``/tmp``.  ``/tmp`` is
    shared by every account, so another user could create ``/tmp/trcc.sock``
    first and each TRCC UI would dispatch to THEIR process; and
    systemd-tmpfiles ages ``/tmp`` out (10 days on Fedora), orphaning a
    long-running App's socket.  No other account can create files in your home,
    and nothing ages ``~/.cache``.  macOS never sets ``XDG_RUNTIME_DIR``, so
    this fallback is every Mac session.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    frame_log.debug("runtime_dir: %s (%s)", base,
              "XDG_RUNTIME_DIR" if xdg else "fallback")
    return base


def socket_path() -> Path:
    """Return the canonical Unix-socket path for this user's daemon."""
    frame_log.debug("socket_path: called")
    return runtime_dir() / _SOCK_NAME


def daemon_running() -> bool:
    """True iff a daemon socket exists and accepts a connection."""
    log.debug("daemon_running: called")
    path = socket_path()
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(str(path))
        return True
    except OSError:
        return False


# =========================================================================
# Class registries — discovered once at import
# =========================================================================


def _collect_classes(module: Any, base: type) -> dict[str, type]:
    log.debug("_collect_classes: module=%s base=%s", module, base)
    return {
        name: cls
        for name, cls in vars(module).items()
        if inspect.isclass(cls) and issubclass(cls, base) and cls is not base
    }


COMMAND_TYPES: dict[str, type[Command[Any]]] = {
    # ``Query`` is a Command subclass, so ``_collect_classes`` collects the
    # ABC itself and the registry advertised a dispatchable command that is
    # not one: decoding {"command": "Query"} reached ``dataclasses.fields``
    # on an abstract base and raised a raw TypeError instead of the clean
    # "Unknown command".  It also made the contract read 136 where every
    # other count says 135 (101 Commands + 34 Queries).  Unlike
    # ``RESULT_TYPES``, which keeps its base on purpose because
    # ``decode_result`` degrades to it, nothing decodes to a bare Command.
    name: cls
    for name, cls in _collect_classes(_commands_module, Command).items()
    if cls is not Query
}
RESULT_TYPES: dict[str, type[Result]] = {
    Result.__name__: Result,
    **_collect_classes(_results_module, Result),
}
EVENT_TYPES: dict[str, type[Event]] = {
    Event.__name__: Event,
    **_collect_classes(_events_module, Event),
}


# =========================================================================
# Sanitize / unsanitize — Path / bytes / Enum at the JSON boundary
# =========================================================================


#: Response key set ONLY when the daemon-side dispatch raised, so the client can
#: tell a Command that failed (a real Result with ``ok=False``) from a Command
#: that blew up (a base Result that will AttributeError on first field access).
_ERROR_KEY = "__error__"


def _to_wire(value: Any) -> Any:
    """JSON-safe representation of arbitrary Python values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {_BYTES_MARKER: base64.b64encode(value).decode("ascii")}
    if dataclasses.is_dataclass(value):
        return {f.name: _to_wire(getattr(value, f.name))
                for f in dataclasses.fields(value)
                if not f.metadata.get(IN_PROCESS_ONLY_KEY)}
    if isinstance(value, (list, tuple)):
        return [_to_wire(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_wire(v) for k, v in value.items()}
    # Unserializable and NOT declared in-process only (``IN_PROCESS_ONLY``):
    # a surprise.  Dropped to None rather than let json.dumps crash the call,
    # and warned once per type -- this runs per frame.
    recurring_warning(log, "_to_wire: non-serializable %s dropped to None at "
                      "IPC boundary", type(value).__name__)
    return None


def _from_wire(raw: Any) -> Any:
    """Reverse of :func:`_to_wire` for ``bytes`` only.

    Enums and dataclasses need their target type (see ``_coerce``) — this
    walker only handles the type-blind bytes marker.
    """
    frame_log.debug("_from_wire: %s", type(raw).__name__)
    if isinstance(raw, dict) and len(raw) == 1 and _BYTES_MARKER in raw:
        return base64.b64decode(raw[_BYTES_MARKER])
    if isinstance(raw, list):
        return [_from_wire(v) for v in raw]
    if isinstance(raw, dict):
        return {k: _from_wire(v) for k, v in raw.items()}
    return raw


# =========================================================================
# Type-hint-driven coercion — reconstruct dataclasses, enums, tuples
# =========================================================================


def _coerce(hint: Any, raw: Any) -> Any:
    """Convert a JSON-decoded value into the type the field expects."""
    frame_log.debug("_coerce: hint=%s raw=%s", hint, type(raw).__name__)
    if raw is None:
        return None
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    # ``X | None`` / ``Union[X, None]`` — try the non-None branch
    if origin in (types.UnionType, typing.Union):
        non_none = [a for a in args if a is not type(None)]
        for branch in non_none:
            try:
                return _coerce(branch, raw)
            except (TypeError, ValueError):
                continue
        return raw

    if origin is list:
        elem_type = args[0] if args else Any
        return [_coerce(elem_type, v) for v in raw]

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(args[0], v) for v in raw)
        return tuple(_coerce(t, v) for t, v in zip(args, raw, strict=False))

    if origin is dict:
        key_t, val_t = args if args else (Any, Any)
        return {_coerce(key_t, k): _coerce(val_t, v) for k, v in raw.items()}

    if inspect.isclass(hint):
        if hint is bytes and isinstance(raw, dict) and _BYTES_MARKER in raw:
            return base64.b64decode(raw[_BYTES_MARKER])
        if hint is Path and isinstance(raw, str):
            return Path(raw)
        if issubclass(hint, Enum):
            return hint(raw)
        if dataclasses.is_dataclass(hint) and isinstance(raw, dict):
            return _build_dataclass(hint, raw)
    return raw


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    """Resolved type hints for *cls*, memoised for the life of the process.

    ``typing.get_type_hints`` is the single most expensive thing on the IPC
    path, and it is expensive here for a reason specific to this codebase:
    every module uses ``from __future__ import annotations``, so every
    annotation is a STRING that has to be ``eval``'d back into a type.  CPython
    does that through ``ForwardRef._evaluate`` -> ``compile()``, and ``compile``
    is one of the most costly calls in the interpreter.

    Measured 2026-09-08 on the mock daemon, ``ListDevices`` dispatched through
    a real socket, ``perf stat -e instructions:u`` over both processes,
    differential (N=5000 minus N=1000) so start-up cancels:

    ==========================  ===========  =========
    per dispatch                before       after
    ==========================  ===========  =========
    ``compile()`` calls         19           0
    instructions                2,456,030    839,134
    ==========================  ===========  =========

    **66%.**  A client-only prototype predicted 53%; the extra comes from the
    DAEMON also decoding every Command with the same cache, which the
    prototype could not patch.  Both halves of the round trip pay this cost,
    so both halves save it.  The saving scales with nesting -- ``decode_result``
    on a 200-entry list went 8.24 ms -> 1.33 ms.

    Never measure this with CPU% or wall time: this box scales its clock, and
    replicate spread on wall time was 53% against <0.4% on instructions.

    Caching is safe because a dataclass's annotations are fixed at import: the
    only keys are the ~279 Command / Result / Event classes in the registries,
    all module-level singletons, so the cache is bounded and its strong
    references cost nothing.  A concurrent miss may resolve twice, which is
    idempotent.  Gated by ``tests/test_ipc_wire.py``, which round-trips every
    registered type -- 279/279 identical with the cache on and off.
    """
    log.debug("_hints: resolving annotations for %s (cache miss)", cls.__name__)
    return typing.get_type_hints(cls)


def _build_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Reconstruct a dataclass instance from a JSON-decoded dict."""
    frame_log.debug("_build_dataclass: %s fields=%s", cls.__name__, sorted(data))
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in data:
            continue
        kwargs[field.name] = _coerce(hints.get(field.name, Any), data[field.name])
    return cls(**kwargs)


# =========================================================================
# Command / Result envelopes
# =========================================================================


def encode_command(cmd: Command[Any]) -> dict[str, Any]:
    """Serialize a Command into a dispatch envelope."""
    frame_log.debug("encode_command: cmd=%s", type(cmd).__name__)
    # Every concrete Command is a frozen dataclass; the runtime check
    # narrows the type for the static checker too.
    assert dataclasses.is_dataclass(cmd), (
        f"{type(cmd).__name__} is not a dataclass — every Command subclass "
        "must use @dataclass(frozen=True, slots=True)"
    )
    return {"command": type(cmd).__name__, "kwargs": _to_wire(cmd)}


def decode_command(envelope: dict[str, Any]) -> Command[Any]:
    """Reconstruct a Command from a dispatch envelope."""
    frame_log.debug("decode_command: keys=%s", sorted(envelope))
    name = envelope.get("command")
    if not isinstance(name, str):
        raise ValueError("envelope missing 'command' key")
    cls = COMMAND_TYPES.get(name)
    if cls is None:
        raise ValueError(f"Unknown command: {name!r}")
    raw_kwargs = envelope.get("kwargs", {})
    if not isinstance(raw_kwargs, dict):
        raise ValueError("envelope 'kwargs' must be an object")
    return _build_dataclass(cls, raw_kwargs)


def encode_result(result: Result) -> dict[str, Any]:
    """Serialize a Result into a response envelope (carries the class name)."""
    frame_log.debug("encode_result: result=%s", type(result).__name__)
    return {"type": type(result).__name__, **_to_wire(result)}


def decode_result(envelope: dict[str, Any]) -> Result:
    """Reconstruct a Result from a response envelope."""
    frame_log.debug("decode_result: keys=%s", sorted(envelope))
    type_name = envelope.get("type", "Result")
    cls = RESULT_TYPES.get(str(type_name), Result)
    body = {k: v for k, v in envelope.items() if k != "type"}
    return _build_dataclass(cls, body)


def encode_event(event: Event) -> dict[str, Any]:
    """Serialize an Event for the subscription stream.

    The payload is nested under ``fields`` rather than flattened the way
    ``encode_result`` flattens, for two reasons: an event field can never
    collide with the discriminator, and a client reading a mixed stream can
    tell an event from a response by shape alone.

    ``FrameSent.surface`` is a live renderer surface and cannot cross JSON —
    ``_to_wire`` drops it to ``None`` and the receiver re-renders.
    """
    frame_log.debug("encode_event: event=%s", type(event).__name__)
    return {"event": type(event).__name__, "fields": _to_wire(event)}


def decode_event(envelope: dict[str, Any]) -> Event:
    """Reconstruct an Event from a subscription envelope.

    Raises on an unknown name rather than falling back to the base ``Event``
    the way :func:`decode_result` falls back to ``Result``.  That asymmetry is
    deliberate: ``EventBus.publish`` fans out on ``type(event)`` — an exact
    match, not ``isinstance`` — so a base-``Event`` stand-in would be
    delivered to nobody at all.  A client on an older build than the daemon
    must hear about the event it cannot decode, not silently lose it.
    """
    frame_log.debug("decode_event: keys=%s", sorted(envelope))
    name = envelope.get("event")
    if not isinstance(name, str):
        raise ValueError("envelope missing 'event' key")
    cls = EVENT_TYPES.get(name)
    if cls is None:
        raise ValueError(f"Unknown event: {name!r}")
    fields = envelope.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("envelope 'fields' must be an object")
    return _build_dataclass(cls, fields)


# =========================================================================
# Wire transport — one-shot client + simple line-oriented server
# =========================================================================


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    frame_log.debug("_send_json: keys=%s", sorted(payload))
    sock.sendall(json.dumps(payload).encode() + b"\n")


def _recv_json(sock: socket.socket, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Read one newline-delimited JSON object from *sock*."""
    frame_log.debug("_recv_json: max_bytes=%d", max_bytes)
    chunks: list[bytes] = []
    received = 0
    while received < max_bytes:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
        if b"\n" in chunk:
            break
    line = b"".join(chunks).split(b"\n", 1)[0]
    if not line:
        raise ConnectionError("peer closed without sending data")
    decoded = json.loads(line.decode())
    if not isinstance(decoded, dict):
        raise ValueError("response was not a JSON object")
    return decoded


def one_shot_request(
    payload: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send one envelope over the daemon socket, return the response."""
    frame_log.debug("one_shot_request: keys=%s timeout=%s",
             sorted(payload), timeout)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path()))
        _send_json(sock, payload)
        return _recv_json(sock)


def open_event_stream(
    types: list[str] | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> socket.socket:
    """Connect, subscribe, and hand back the still-open stream socket.

    The caller owns the socket and reads newline-delimited event envelopes
    from it until EOF.  Raises :class:`ConnectionError` if the daemon refuses
    the subscription, so a caller never ends up holding a socket that will
    stay silent forever.
    """
    wanted = types or ["*"]
    log.info("open_event_stream: types=%s", wanted)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(socket_path()))
        _send_json(sock, {"subscribe": wanted})
        ack = _recv_json(sock)
    except OSError:
        sock.close()
        raise
    if not ack.get("ok"):
        sock.close()
        raise ConnectionError(
            f"daemon refused the subscription: {ack.get('message', ack)}",
        )
    # The stream carries no further requests, so a read timeout would kill a
    # healthy but idle subscriber.
    sock.settimeout(None)
    log.info("open_event_stream: subscribed to %s", ack.get("subscribed"))
    return sock


# =========================================================================
# IPCServer — bound to one App, serves requests on a Unix socket
# =========================================================================


class _Subscriber:
    """One connected client that asked for an event stream.

    Holds the socket and the set of event-type names it wants.  It does NOT
    hold a queue or a thread of its own: the server owns one queue and one
    fan-out thread for every subscriber, so an event is encoded ONCE no matter
    how many clients are listening (measured 3.3 us/event -- cheap, but N
    encodes for N clients is still N times nothing for no reason).
    """

    __slots__ = ("sock", "types", "wants_all")

    def __init__(self, sock: socket.socket, types: set[str]) -> None:
        self.sock = sock
        self.types = types
        self.wants_all = "*" in types
        log.info("_Subscriber: %d type(s)%s",
                 len(types), " (all)" if self.wants_all else "")

    def close(self) -> None:
        log.info("_Subscriber.close: dropping subscriber")
        try:
            self.sock.close()
        except OSError:
            log.debug("_Subscriber.close: socket close failed", exc_info=True)


class IPCServer:
    """Threaded Unix-socket server.

    One blocking accept loop runs on a background thread; each connection
    is dispatched on its own short-lived worker thread.  Concurrent
    Command dispatch on the shared App is serialized by ``app._lock``
    (held inside ``app.dispatch`` once we wire it).
    """

    def __init__(self, app: App) -> None:
        log.debug("__init__: app=%s", app)
        self._app = app
        self._sock: socket.socket | None = None
        # The path we actually BOUND.  ``shutdown`` must unlink this, not a
        # freshly-computed ``socket_path()``: that reads XDG_RUNTIME_DIR, and
        # if the environment moved between start and shutdown we would delete
        # a path we never owned -- plausibly a live daemon's socket.
        self._bound_path: Path | None = None
        self._stop = False
        # Event fan-out state.  A subscriber connection is long-lived, unlike
        # the one-shot dispatch connections, so it is tracked here rather than
        # living and dying inside ``_serve_client``.
        self._subscribers: list[_Subscriber] = []
        self._sub_lock = threading.Lock()
        self._event_q: queue.Queue[Event] = queue.Queue(maxsize=EVENT_QUEUE_MAX)
        self._fanout_thread: threading.Thread | None = None
        self._bridged: set[str] = set()   # event names already subscribed on the bus
        self._dropped = 0
        self._workers: list[Any] = []   # threading.Thread, kept for join on shutdown

    def start(self) -> None:
        """Bind + listen.  Caller is responsible for serving (see ``serve_forever``)."""
        log.info("start: called")
        if not hasattr(socket, "AF_UNIX"):
            # Windows has no AF_UNIX — the GUI/daemon simply runs in-process,
            # hosting no socket.  Legacy degraded here; the cutover changed this
            # to `raise`, which crashed the Windows GUI on launch (#187 follow-on,
            # surfaced once the libusb fix let the GUI boot far enough to reach it).
            log.info("start: AF_UNIX unavailable (Windows) — IPC server skipped; "
                     "running in-process")
            return
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(8)
        path.chmod(0o600)
        self._sock = sock
        self._bound_path = path
        log.info("IPC server listening on %s", path)

    def serve_forever(self) -> None:
        """Accept connections until ``shutdown`` is called or a kill arrives."""
        log.info("serve_forever: called")
        import threading
        if self._stop:
            # ``shutdown`` came first -- a SIGTERM while the daemon bound.
            log.info("serve_forever: shut down before serving — returning")
            return
        if self._sock is None:
            raise RuntimeError("serve_forever called before start")
        while not self._stop:
            try:
                client, _ = self._sock.accept()
            except OSError:
                # listening socket closed — that's our exit signal
                break
            t = threading.Thread(
                target=self._serve_client, args=(client,),
                daemon=True, name="trcc-ipc",
            )
            t.start()
            # Reap finished workers.  This list only ever grew: one entry per
            # connection for the life of the daemon, and a subscriber stream
            # makes each entry long-lived.  Pruning here keeps it bounded by
            # the number of IN-FLIGHT connections, which is what it was for.
            self._workers = [w for w in self._workers if w.is_alive()]
            self._workers.append(t)

    def shutdown(self) -> None:
        """Stop accepting + clean up the socket file."""
        log.info("shutdown: called")
        self._stop = True
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                log.debug("shutdown: SHUT_RDWR on listening socket failed",
                          exc_info=True)
            self._sock.close()
            self._sock = None
        # Joined first: it re-reads ``_stop`` every 0.25 s, and a write it
        # still has in flight must not interleave with the notice below.
        # Alive only: ``_ensure_fanout`` assigns the thread a moment before it
        # starts it, and joining one not yet started raises.  One that starts
        # after this sees ``_stop`` and returns before writing anything.
        fanout = self._fanout_thread
        if (fanout is not None and fanout.is_alive()
                and fanout is not threading.current_thread()):
            fanout.join(timeout=1.0)
        with self._sub_lock:
            subs, self._subscribers = self._subscribers, []
        # The last line of every stream says WHY it ends, so a UI can tell a
        # quit (close with it) from a crash (the stream just stops).
        notice = json.dumps(encode_event(AppStopping())).encode() + b"\n"
        for sub in subs:
            self._write_or_evict(sub, notice)
            sub.close()
        if subs:
            log.info("shutdown: told %d subscriber stream(s) the App is "
                     "stopping, then closed them", len(subs))
        # Last: ``trcc kill`` returns when this file is gone, so every UI has
        # been told by then.
        path = self._bound_path
        if path is not None and path.exists():
            try:
                path.unlink()
            except OSError:
                log.debug("shutdown: socket unlink failed", exc_info=True)
        self._bound_path = None
        log.info("IPC server shut down")

    def _serve_client(self, client: socket.socket) -> None:
        # A subscriber connection outlives this function; everything else is
        # one-request-one-response-close.
        keep_open = False
        try:
            client.settimeout(_DEFAULT_TIMEOUT_S)
            envelope = _recv_json(client)
            if "subscribe" in envelope:
                # A stream, not a request/response: hand the socket over and
                # return WITHOUT closing it (the ``finally`` below is skipped
                # via ``keep_open``).  The fan-out thread owns it from here.
                keep_open = self._handle_subscribe(client, envelope["subscribe"])
                return
            if envelope.get("kill") is True:
                _send_json(client, {"ok": True, "message": "shutting down"})
                self._stop = True
                # Wake the accept() loop by closing the listening socket
                if self._sock is not None:
                    try:
                        self._sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        log.debug("kill: SHUT_RDWR on listening socket failed",
                                  exc_info=True)
                return
            response = self._dispatch_envelope(envelope)
            _send_json(client, response)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_error(client, f"Bad request: {e}")
        except ConnectionError as e:
            log.debug("IPC client disconnected: %s", e)
        except Exception as e:
            log.exception("IPC dispatch error")
            self._send_error(client, str(e))
        finally:
            if not keep_open:
                try:
                    client.close()
                except OSError:
                    log.debug("client close failed", exc_info=True)

    # ── Event fan-out ────────────────────────────────────────────────

    def _handle_subscribe(self, client: socket.socket, raw: Any) -> bool:
        """Turn *client* into a long-lived event subscriber.

        Returns True when the stream was ACCEPTED and this server now owns the
        socket.  A refusal returns False so the caller still closes it —
        getting that wrong leaks one fd per bad subscribe request.

        ``{"subscribe": ["FrameSent", ...]}`` or ``{"subscribe": ["*"]}``.
        The connection is NOT closed and its receive timeout is lifted -- both
        are correct for the one-shot dispatch path this shares, and both are
        wrong for a stream.
        """
        names = [str(n) for n in raw] if isinstance(raw, list) else []
        log.info("_handle_subscribe: requested %s", names or "<nothing>")
        if not names:
            _send_json(client, {"ok": False,
                                "message": "subscribe needs a non-empty list"})
            return False
        unknown = [n for n in names if n != "*" and n not in EVENT_TYPES]
        if unknown:
            # Naming an event that does not exist is a client bug, and a silent
            # accept would look like a working subscription that never fires.
            log.warning("_handle_subscribe: unknown event type(s) %s", unknown)
            _send_json(client, {
                "ok": False,
                "message": f"unknown event type(s): {', '.join(sorted(unknown))}",
            })
            return False

        wanted = set(names)
        self._bridge_events(wanted)
        sub = _Subscriber(client, wanted)
        # Register and acknowledge under ONE lock, registration first.  Each
        # ordering on its own breaks a different invariant:
        #   ack-then-register loses every event published in the gap, to a
        #     client that has just been told it is attached;
        #   register-then-ack outside the lock lets ``_fanout_loop`` write an
        #     event line BEFORE the ack line, so the client parses an event
        #     where it expects its receipt -- a desynced stream, which is
        #     worse than a dropped event.
        # The fan-out snapshots its targets under this same lock (see
        # ``_fanout_loop``), so while it is held no event can reach this
        # socket, and by the time one can the ack is already on the wire.
        # The ack keeps the bounded send timeout: a client that has stopped
        # reading must not be able to park this thread inside the lock and
        # stall the fan-out for every other subscriber.
        client.settimeout(_SUBSCRIBER_SEND_TIMEOUT_S)
        with self._sub_lock:
            self._subscribers.append(sub)
            try:
                _send_json(client, {"ok": True, "subscribed": sorted(wanted)})
            except OSError as e:
                # The stream died between accept and receipt.  Un-register
                # inline -- ``_evict`` takes ``_sub_lock``, which is not
                # reentrant -- and let the caller close the socket.
                self._subscribers.remove(sub)
                log.warning("_handle_subscribe: ack failed, not attached "
                            "-- %s: %s", type(e).__name__, e)
                return False
            attached = len(self._subscribers)
        # The stream has no further requests on it, so the 30s read timeout
        # that guards a one-shot dispatch would kill a healthy subscriber.
        client.settimeout(None)
        self._ensure_fanout()
        log.info("_handle_subscribe: %d subscriber(s) now attached", attached)
        return True

    def _bridge_events(self, names: set[str]) -> None:
        """Subscribe this server to the bus for every name in *names*, once.

        One handler per event TYPE for the whole server, not one per client:
        the handler's only job is to hand the event to the fan-out thread, and
        doing that twice for two clients would double the work on the
        publishing thread -- which is the render tick for ``FrameSent`` and the
        udev thread for eleven others.
        """
        targets = set(EVENT_TYPES) if "*" in names else names
        fresh = targets - self._bridged
        log.info("_bridge_events: %d requested, %d new", len(targets), len(fresh))
        for name in sorted(fresh):
            self._app.events.subscribe(EVENT_TYPES[name], self._on_bus_event)
            self._bridged.add(name)

    def _on_bus_event(self, event: Event) -> None:
        """Bus handler -- ENQUEUE ONLY.

        This runs synchronously on whichever thread published, so it must not
        encode, must not write to a socket and must not block.  ``EventBus``'s
        own docstring prescribes exactly this, and ``BusBridge`` models it.
        """
        try:
            self._event_q.put_nowait(event)
        except queue.Full:
            # A subscriber has stopped reading and the buffer is full.  Drop
            # the NEWEST rather than block the render thread; the count is
            # logged so a report shows the stall instead of hiding it.
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("_on_bus_event: event queue full — dropped %d "
                            "event(s); a subscriber is not reading",
                            self._dropped)

    def _ensure_fanout(self) -> None:
        """Start the fan-out thread on the first subscriber."""
        if self._fanout_thread is not None and self._fanout_thread.is_alive():
            log.debug("_ensure_fanout: already running")
            return
        log.info("_ensure_fanout: starting fan-out thread")
        self._fanout_thread = threading.Thread(
            target=self._fanout_loop, daemon=True, name="trcc-ipc-events",
        )
        self._fanout_thread.start()

    def _fanout_loop(self) -> None:
        """Drain the queue, encode ONCE per event, write to every listener."""
        log.info("_fanout_loop: started")
        while not self._stop:
            try:
                event = self._event_q.get(timeout=0.25)
            except queue.Empty:
                continue
            name = type(event).__name__
            with self._sub_lock:
                targets = [s for s in self._subscribers
                           if s.wants_all or name in s.types]
            if not targets:
                continue
            # ONE encode, N writes -- the whole reason the fan-out is central.
            line = json.dumps(encode_event(self._for_the_wire(event))).encode() + b"\n"
            frame_log.debug("_fanout_loop: %s -> %d subscriber(s), %d bytes",
                            name, len(targets), len(line))
            for sub in targets:
                self._write_or_evict(sub, line)
        log.info("_fanout_loop: stopped")

    def _for_the_wire(self, event: Event) -> Event:
        """Carry what the socket cannot: a ``FrameSent``'s picture, as JPEG.

        The live surface is in-process only, so a remote preview used to get
        a FrameSent with no picture and ask for a fresh render + PNG every
        frame.  Encoded HERE, on the fan-out thread -- never on the render
        thread that published it -- once for every subscriber, and only for
        an event someone is subscribed to.
        """
        if not isinstance(event, FrameSent) or event.surface is None or event.image:
            return event
        try:
            image = self._app.display.encode_jpeg(event.surface)
        except Exception as e:
            recurring_warning(log, "_for_the_wire: FrameSent for %s sent without "
                              "its picture — %s: %s", event.key,
                              type(e).__name__, e)
            return event
        frame_log.debug("_for_the_wire: FrameSent %s carries %d byte(s) of JPEG",
                        event.key, len(image))
        return dataclasses.replace(event, image=image)

    def _write_or_evict(self, sub: _Subscriber, line: bytes) -> None:
        """Send *line*; drop the subscriber on any failure."""
        try:
            sub.sock.settimeout(_SUBSCRIBER_SEND_TIMEOUT_S)
            sub.sock.sendall(line)
        except (OSError, ValueError) as e:
            log.info("_write_or_evict: evicting subscriber — %s: %s",
                     type(e).__name__, e)
            self._evict(sub)

    def _evict(self, sub: _Subscriber) -> None:
        """Remove *sub* and close its socket.  Idempotent."""
        with self._sub_lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)
            remaining = len(self._subscribers)
        sub.close()
        log.info("_evict: %d subscriber(s) remain", remaining)

    def _dispatch_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Decode, dispatch, encode.  Never lets a client kill the daemon.

        The ``_ERROR_KEY`` marker distinguishes "the Command ran and reported
        failure" from "the Command RAISED".  Without it both arrived as
        ``{"type": "Result", "ok": False}``, which decodes to a BASE ``Result``
        — and a caller reading the field it asked for got
        ``AttributeError: 'Result' object has no attribute 'devices'``, with
        the real exception nowhere in sight.  Verified end to end before this
        marker existed.

        Catching here is not optional: one client's bad Command must not take
        down the process that owns USB for everyone else.  So the daemon
        survives and the CLIENT re-raises, which is what in-process already
        does when a Command raises.

        Unknown keys are ignored by ``_build_dataclass`` (it reads only
        declared fields), so adding this is backward compatible: an older
        client decodes the same Result it always did.
        """
        try:
            cmd = decode_command(envelope)
        except (ValueError, TypeError) as e:
            log.warning("_dispatch_envelope: undecodable command: %s", e)
            return {"type": "Result", "ok": False,
                    "message": f"Decode error: {e}",
                    _ERROR_KEY: f"Decode error: {e}"}
        # The CLIENT's origin (``[cli]``, ``[api]``…), so a daemon log names who
        # asked.  ``ipc`` for a caller with no name: a client too old to send
        # one, or one that dispatched outside any UI (it sends the default,
        # ``core``) — ``[core]`` in the DAEMON's log means the daemon itself.
        origin = envelope.get("origin")
        try:
            with dispatch_origin(origin if origin not in (None, "", "core")
                                 else "ipc"):
                result = self._app.dispatch(cmd)
        except Exception as e:
            log.exception("Command %s raised", type(cmd).__name__)
            return {"type": "Result", "ok": False,
                    "message": f"{type(e).__name__}: {e}",
                    _ERROR_KEY: f"{type(e).__name__}: {e}"}
        return encode_result(result)

    @staticmethod
    def _send_error(client: socket.socket, message: str) -> None:
        try:
            _send_json(client, {"type": "Result", "ok": False, "message": message})
        except OSError:
            log.debug("error response send failed", exc_info=True)


# =========================================================================
# Client wait helpers
# =========================================================================


def wait_for_daemon(*, timeout: float, poll_s: float = 0.05) -> bool:
    """Block until ``daemon_running()`` returns True or *timeout* elapses."""
    log.info("wait_for_daemon: timeout=%s poll_s=%s", timeout, poll_s)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_running():
            return True
        time.sleep(poll_s)
    return False


# Touch core so static analyzers see why we import it (the registries
# above need the modules loaded — this line is a documentation anchor,
# not a runtime no-op).
_ = core


# =========================================================================
# SingleInstance — per-UI single-instance + raise-existing-window helper
# =========================================================================


class SingleInstance:
    """Per-UI socket-based single-instance + raise-existing-window helper.

    The first launch of ``trcc gui`` binds ``~/.cache/trcc/gui.sock``
    and listens for ``{"raise": true}`` messages.  Subsequent launches
    fail to bind, instead connect to the existing socket, send
    ``{"raise": true}``, and bail out — the running window's
    :attr:`on_raise` callback fires on this helper's **accept thread**
    (not the Qt main thread).  The consumer is therefore responsible for
    marshalling any GUI work onto the main thread: the legacy GUI wires
    ``on_raise`` to a Qt signal whose slot is connected with
    ``QueuedConnection`` (see ``ui/gui/__init__.py``).

    Architectural note: this is separate from the daemon's
    :class:`IPCServer` (which serves Command dispatch).  Each UI type
    (``gui`` / ``qtgui`` / future REPL) gets its own socket so two
    different UIs can coexist while still enforcing one of each.

    Usage::

        instance = SingleInstance("gui")
        if instance is None:        # peer launch — raise sent, exit cleanly
            return 0
        instance.on_raise = window.raise_requested.emit  # thread-safe emit
        ...
    """

    # Not strictly a normal class — the constructor may return None
    # (when a peer is already running).  Encoded via __new__.

    _DIR_NAME = "trcc"
    _RAISE_MESSAGE = b'{"raise": true}\n'
    _CONNECT_TIMEOUT_S = 1.0

    def __new__(cls, name: str) -> SingleInstance | None:  # type: ignore[misc]
        path = _instance_socket_path(name)
        if not hasattr(socket, "AF_UNIX"):
            # Legacy Windows fallback (build < 17063 / Windows 10 pre-1803).
            # AF_UNIX missing means no socket-based lock; legacy used
            # ``msvcrt.locking`` on a file and accepted no raise-existing
            # support.  Same shape here — single-instance enforcement
            # without window-raise, silent fallthrough on success.
            fallback = _msvcrt_acquire(name)
            if fallback is None:
                log.info(
                    "SingleInstance(%r): peer already running (msvcrt lock held)",
                    name,
                )
                return None
            instance = super().__new__(cls)
            instance._msvcrt_handle = fallback  # type: ignore[attr-defined]
            log.info(
                "SingleInstance(%r): using msvcrt file lock (AF_UNIX unavailable)",
                name,
            )
            return instance

        # ── Peer alive? ──
        if _peer_alive(path, cls._CONNECT_TIMEOUT_S):
            try:
                _send_raise(path, cls._RAISE_MESSAGE, cls._CONNECT_TIMEOUT_S)
                log.info("SingleInstance(%r): peer alive, raise sent", name)
            except OSError as e:
                log.warning(
                    "SingleInstance(%r): peer responded then closed; "
                    "raise send failed (%s)", name, e,
                )
            return None

        # ── No peer — clean any stale socket file + bind ──
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                log.debug("SingleInstance(%r): stale socket unlink failed: %s",
                          name, e)
        path.parent.mkdir(parents=True, exist_ok=True)
        instance = super().__new__(cls)
        instance._bind(name, path)
        return instance

    def __init__(self, name: str) -> None:
        # __new__ does all the work; __init__ runs again on re-entry but
        # binding already happened.  Keep this idempotent.
        log.debug("__init__: name=%s", name)
        if not hasattr(self, "_name"):
            self._name = name

    def _bind(self, name: str, path: Path) -> None:
        """Actually listen on the socket + spawn the accept thread."""
        import threading

        self._name = name
        self._path = path
        self._stop = False
        self.on_raise: typing.Callable[[], None] | None = None

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(4)
        try:
            path.chmod(0o600)
        except OSError:
            log.debug("SingleInstance(%r): chmod 600 failed", name)
        self._sock: socket.socket | None = sock

        self._thread = threading.Thread(
            target=self._accept_loop,
            name=f"trcc-single-instance-{name}",
            daemon=True,
        )
        self._thread.start()
        log.info("SingleInstance(%r): listening on %s", name, path)

    def close(self) -> None:
        """Stop listening and remove the socket file."""
        log.info("close: called")
        sock = getattr(self, "_sock", None)
        if sock is None:
            return
        self._stop = True
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            log.debug("SingleInstance.close: SHUT_RDWR failed", exc_info=True)
        try:
            sock.close()
        except OSError:
            log.debug("SingleInstance.close: socket close failed", exc_info=True)
        self._sock = None
        try:
            self._path.unlink()
        except OSError:
            log.debug("SingleInstance.close: unlink failed", exc_info=True)

    # ── Internal — accept loop ───────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._stop:
            sock = self._sock
            if sock is None:
                break
            try:
                client, _ = sock.accept()
            except OSError:
                break  # closed
            with client:
                client.settimeout(self._CONNECT_TIMEOUT_S)
                try:
                    data = client.recv(256)
                except OSError:
                    continue
                if not data:
                    continue
                try:
                    payload = json.loads(data.decode("utf-8").strip())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    log.debug("SingleInstance: ignoring malformed peer message")
                    continue
                if isinstance(payload, dict) and payload.get("raise"):
                    cb = self.on_raise
                    if cb is not None:
                        try:
                            cb()
                        except Exception:
                            log.exception(
                                "SingleInstance.on_raise raised",
                            )


def _instance_socket_path(name: str) -> Path:
    """Per-UI socket path (one file per UI flavour)."""
    log.debug("_instance_socket_path: name=%s", name)
    return runtime_dir() / SingleInstance._DIR_NAME / f"{name}.sock"


def _msvcrt_acquire(name: str) -> Any | None:
    """Acquire a Windows single-instance file-lock via ``msvcrt.locking``.

    Used as the fallback when ``AF_UNIX`` isn't available (Windows 8.1
    or 10 pre-1803).  Ports legacy ``WindowsPlatform.acquire_instance_lock``
    (windows_platform.py:269-280).  Returns the open file handle on
    success (caller stores it for the process lifetime — the lock
    releases when the FD closes), or ``None`` when a peer already
    holds the lock.

    No raise-existing-window support — legacy didn't have it either
    (``raise_existing_instance`` was a ``pass``).  Modern Windows
    (1803+) gets the full AF_UNIX path with raise.
    """
    log.debug("_msvcrt_acquire: name=%s", name)
    try:
        import msvcrt  # pyright: ignore[reportMissingImports]
    except ImportError:
        # Not on Windows; the AF_UNIX branch would have been taken.
        # Reaching here means an exotic platform — fail open.
        return None
    lock_path = (
        Path.home() / ".cache" / SingleInstance._DIR_NAME / f"{name}.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # The file handle must stay open for the process lifetime — closing
    # it releases the lock — so deliberately do NOT use a with-block.
    try:
        fh = lock_path.open("w")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        return None
    return fh


def _peer_alive(path: Path, timeout: float) -> bool:
    """True if a peer is currently bound + accepting on ``path``.

    The socket is opened in a ``with`` block because the interesting answer is
    *no*: a socket file left behind by a killed GUI still exists, so every
    launch after a crash reaches ``connect()`` and it raises.  Constructing the
    socket inside the ``try`` leaked one descriptor down exactly that path.
    """
    if not path.exists():
        log.debug("_peer_alive: %s — no socket file, no peer", path)
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
    except OSError:
        log.debug("_peer_alive: %s — stale socket file, nothing accepting",
                  path, exc_info=True)
        return False
    log.debug("_peer_alive: %s — a peer is accepting", path)
    return True


def _send_raise(path: Path, payload: bytes, timeout: float) -> None:
    """Connect + send a raise message to an existing peer.

    ``connect()`` sits inside the ``with`` for the same reason as above: the
    peer can die between :func:`_peer_alive` saying yes and this call, and the
    caller swallows that ``OSError`` — so a descriptor leaked here was never
    noticed.
    """
    log.debug("_send_raise: %d byte(s) to %s", len(payload), path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(payload)
