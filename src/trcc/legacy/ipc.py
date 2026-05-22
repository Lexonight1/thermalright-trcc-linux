"""IPC for the trccd singleton — manifold protocol over a Unix socket.

The daemon (`trcc daemon`) binds an `IPCServer` to its `Trcc` and serves
clients over a Unix domain socket. Clients are `TrccProxy` instances
returned by `_boot.trcc()` when ``TRCC_DAEMON=1``: the same call shape
reaches the daemon's facades through one round-trip per call.

Wire format — manifold dispatch (one line of JSON per request)::

    {"role": "lcd", "method": "set_brightness",
     "args": [0, 75], "kwargs": {}}

The role names a facade on the bound `Trcc` (``lcd`` / ``led`` /
``control_center``); the method is invoked as ``fn(*args, **kwargs)``.
Wire shape mirrors the Python call shape exactly — that's what makes
`TrccProxy` a transparent drop-in for `Trcc`. Two control shapes also
travel on the same socket:

    {"kill": true}                  # graceful daemon shutdown
    {"subscribe": "frame"}          # long-lived event subscription

Path / bytes arguments survive JSON via :func:`sanitize_for_wire` on the
client and :func:`unsanitize_from_wire` on the server. Frame surfaces
and other non-JSON values get dropped from result dicts at the boundary
— UIs that need pixel data subscribe to the EventBus stream instead.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core.results import Frame, OpResult

if TYPE_CHECKING:
    from .core.trcc import Trcc

log = logging.getLogger(__name__)

# Socket path: same dir as the instance lock file.
_SOCK_NAME = "trcc-linux.sock"


def _socket_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / _SOCK_NAME


# =========================================================================
# Wire-format encoding helpers — symmetric client/server boundary.
# =========================================================================

_BYTES_MARKER = '__bytes__'


def sanitize_for_wire(value: Any) -> Any:
    """Convert a Python value into something JSON can serialize.

    Two real cases that the manifold's command surface hands us:

      ``pathlib.Path``    →  ``str(path)`` — every facade method that
                              takes a Path immediately ``str()``\\ s it
                              internally, so the receiving end accepts
                              str just as well.
      ``bytes``           →  ``{"__bytes__": "<base64>"}`` — single-key
                              marker dict that round-trips through
                              JSON. The server reconstructs via
                              :func:`unsanitize_from_wire`.

    Plain JSON-friendly values pass through untouched. Lists and dicts
    are recursed shallowly — covers the call shapes the facades
    actually use without paying for arbitrary depth.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        import base64
        return {_BYTES_MARKER: base64.b64encode(value).decode('ascii')}
    if isinstance(value, list):
        return [sanitize_for_wire(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_wire(v) for v in value)
    if isinstance(value, dict):
        return {k: sanitize_for_wire(v) for k, v in value.items()}
    return value


def unsanitize_from_wire(value: Any) -> Any:
    """Reverse of :func:`sanitize_for_wire` for values arriving from JSON.

    Reconstructs ``bytes`` from the ``__bytes__`` marker dict. ``Path``
    isn't reversed — the receiving facade methods accept ``str`` and
    convert internally if they need ``Path``, so leaving the str on the
    wire is honest about the contract.
    """
    if isinstance(value, dict) and len(value) == 1 and _BYTES_MARKER in value:
        import base64
        return base64.b64decode(value[_BYTES_MARKER])
    if isinstance(value, list):
        return [unsanitize_from_wire(v) for v in value]
    if isinstance(value, dict):
        return {k: unsanitize_from_wire(v) for k, v in value.items()}
    return value


def _result_to_dict(result: OpResult) -> dict[str, Any]:
    """Serialize an OpResult (or subclass) into a JSON-safe dict.

    `Frame` and any other non-JSON value is dropped — the wire format only
    carries the success / error envelope plus simple extras (is_animated,
    interval_ms, latest_version, etc.). UIs that need pixels subscribe to
    the EventBus stream instead of pulling them out of dispatch results.
    """
    out: dict[str, Any] = {
        "success": result.success,
        "message": result.message,
        "error": result.error,
    }
    for f in dataclasses.fields(result):
        if f.name in {"success", "message", "error"}:
            continue
        v = getattr(result, f.name)
        if isinstance(v, Frame) or v is None:
            continue
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        out[f.name] = v
    return out


def _result_from_dict(data: dict[str, Any]) -> OpResult:
    """Build a generic OpResult from a wire-format dict.

    Subclass-specific extras are dropped at this boundary — the IPC
    contract is OpResult shape (success/message/error). Richer typed
    results travel via the EventBus stream alongside this dispatcher.
    """
    return OpResult(
        success=bool(data.get("success", False)),
        message=str(data.get("message", "")),
        error=data.get("error"),
    )


def _metrics_to_dict(metrics: Any) -> dict[str, Any]:
    """Serialize a :class:`HardwareMetrics` for the IPC wire.

    ``dataclasses.asdict`` handles the typed fields; the private
    ``_populated`` set and ``readings`` dict are sent verbatim — sets
    serialize to lists, reconstructed on the client.
    """
    body = dataclasses.asdict(metrics)
    # _populated is a set; JSON needs a list.  asdict already gives
    # us a list for set fields, but be explicit for read clarity.
    body['_populated'] = sorted(metrics._populated)
    body['readings'] = dict(metrics.readings)
    return body


def _metrics_from_dict(body: dict[str, Any]) -> Any:
    """Reconstruct a :class:`HardwareMetrics` from the IPC wire dict."""
    from .core.models import HardwareMetrics
    # Pull the side-channel fields off, then feed the remaining keys to
    # the dataclass constructor.  Unknown keys (forward-compat) are
    # dropped by filtering against the dataclass fields.
    populated: set[str] = set(body.pop('_populated', ()))
    readings: dict[str, float] = dict(body.pop('readings', {}))
    valid = {f.name for f in dataclasses.fields(HardwareMetrics)}
    init_kwargs = {k: v for k, v in body.items() if k in valid}
    m = HardwareMetrics(**init_kwargs)
    m._populated = populated
    m.readings = readings
    return m


# =========================================================================
# Server (runs in the daemon process, Qt event loop)
# =========================================================================

class IPCServer:
    """Unix-socket server bound to a `Trcc` — manifold dispatch only.

    Integrates with Qt's event loop via QSocketNotifier on the listening
    fd. Each one-shot client is handled synchronously (accept, read,
    dispatch, respond, close) in a single callback. Long-lived event
    subscriptions stay open and stream JSON lines until the client
    disconnects.
    """

    def __init__(self, *, trcc: Trcc, renderer: Any | None = None):
        self._trcc: Trcc = trcc
        # Renderer is needed only to sanitize ``Topic.FRAME`` event payloads
        # (native surfaces aren't JSON-safe).  None is acceptable — frame
        # events with surface payloads will be skipped (logged) on the wire,
        # while every other topic flows untouched.
        self._renderer: Any | None = renderer
        self._sock: socket.socket | None = None
        self._notifier: Any = None  # QSocketNotifier
        # Long-lived event subscriber connections — each is (sub_id, sock).
        # Tracked so shutdown() can unsubscribe + close them cleanly.
        self._event_subs: list[tuple[int, socket.socket]] = []

    def start(self) -> None:
        """Bind and listen on Unix domain socket (Unix only)."""
        if not hasattr(socket, 'AF_UNIX'):
            log.debug("IPC server skipped -- AF_UNIX not available (Windows)")
            return

        path = _socket_path()
        if path.exists():
            path.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.setblocking(False)
        self._sock.bind(str(path))
        self._sock.listen(5)
        os.chmod(str(path), 0o600)

        from PySide6.QtCore import QSocketNotifier
        self._notifier = QSocketNotifier(
            self._sock.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self._on_connection)
        log.info("IPC server listening on %s", path)

    def shutdown(self) -> None:
        """Close socket and clean up every long-lived subscription."""
        for sub_id, _client in self._event_subs:
            try:
                self._trcc.events.unsubscribe(sub_id)
            except Exception:
                log.exception("shutdown: events.unsubscribe(%d) raised", sub_id)
        for _sub_id, client in self._event_subs:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                log.debug("shutdown: socket.SHUT_RDWR failed (already closed)",
                          exc_info=True)
            try:
                client.close()
            except OSError:
                log.debug("shutdown: client.close() failed (already closed)",
                          exc_info=True)
        self._event_subs.clear()

        if self._notifier:
            self._notifier.setEnabled(False)
            self._notifier = None
        if self._sock:
            self._sock.close()
            self._sock = None
        path = _socket_path()
        if path.exists():
            path.unlink()
        log.info("IPC server shut down")

    def _on_connection(self) -> None:
        """Accept client, classify request, dispatch.

        Two modes: one-shot dispatch (close after responding) and
        long-lived subscription (keep open, write events back until
        the client disconnects).
        """
        if not self._sock:
            return
        try:
            client, _ = self._sock.accept()
        except OSError:
            # Listen socket dropped (server shutting down or fd recycled) —
            # silently bail out of this notifier callback.
            log.debug("_on_connection: accept() failed", exc_info=True)
            return

        request: dict = {}
        is_subscribe = False
        try:
            client.settimeout(5.0)
            if not (data := client.recv(65536)):
                return

            parsed = json.loads(data.decode().strip())
            if not isinstance(parsed, dict):
                _send_error(client, "Request must be a JSON object")
                return
            request = parsed

            # Long-lived subscription connection — keep socket open.
            if "subscribe" in request:
                is_subscribe = True
                self._handle_subscribe(client, str(request["subscribe"]))
                return

            # One-shot dispatch — respond and close.
            result = self._dispatch(request)
            client.sendall(json.dumps(result).encode() + b"\n")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _send_error(client, f"Bad request: {e}")
        except Exception as e:
            log.warning("IPC dispatch error: %s", e)
            _send_error(client, str(e))
        finally:
            # Subscription connections stay open; one-shot connections close.
            if not is_subscribe:
                try:
                    client.close()
                except OSError:
                    log.debug("_on_connection: client.close() failed",
                              exc_info=True)

    def _handle_subscribe(self, client: socket.socket, topic: str) -> None:
        """Register a subscription that forwards EventBus events to ``client``.

        Sends a one-line ack ({success}) then keeps the socket open. Each
        publish on ``topic`` writes a JSON line to the client. Write
        failures (client disconnected) trigger automatic cleanup.
        """
        # Ack the subscription before any events flow.
        try:
            client.sendall(json.dumps({"success": True}).encode() + b"\n")
        except OSError:
            log.debug("_handle_subscribe: ack send failed (client gone?)",
                      exc_info=True)
            try:
                client.close()
            except OSError:
                log.debug("_handle_subscribe: client.close() failed",
                          exc_info=True)
            return
        # Drop the recv timeout — the connection now lives until the
        # client disconnects.
        client.settimeout(None)

        # Forwarder: serializes the EventBus payload and writes to the
        # client. On write failure, unsubscribes itself.
        sub_id_holder: list[int] = []

        def _forward(*payload: Any) -> None:
            wire_payload = self._sanitize_payload(topic, payload)
            line = json.dumps({"topic": topic, "payload": wire_payload}) + "\n"
            try:
                client.sendall(line.encode())
            except OSError:
                if not sub_id_holder:
                    return
                sid = sub_id_holder[0]
                try:
                    self._trcc.events.unsubscribe(sid)
                except Exception:
                    log.exception("subscribe forwarder: unsubscribe raised")
                self._event_subs[:] = [
                    (i, c) for (i, c) in self._event_subs if i != sid
                ]
                try:
                    client.close()
                except OSError:
                    log.debug("subscribe forwarder: client.close() failed",
                              exc_info=True)

        sub_id = self._trcc.events.subscribe(topic, _forward)
        sub_id_holder.append(sub_id)
        self._event_subs.append((sub_id, client))
        log.info("IPC subscription registered: id=%d topic=%r", sub_id, topic)

    def _sanitize_payload(self, topic: str, payload: tuple) -> list:
        """Return *payload* in JSON-safe form for transmission.

        Only ``Topic.FRAME`` carries a non-JSON-safe value today: the
        rendered surface at index 1.  When a renderer is wired in, the
        surface is wrapped via :func:`trcc.core.wire.wrap_surface`; when
        not, the surface slot is replaced with ``None`` and a one-shot
        warning is logged so misconfiguration surfaces during smoke tests
        rather than as silent dropped frames in production.

        Other topics flow untouched — their payloads are already JSON-safe
        (strings, ints, lists, dataclasses-as-dicts).
        """
        from .core.events import Topic

        if topic != Topic.FRAME:
            return list(payload)

        # Topic.FRAME contract: (device_path: str, surface: Any | None).
        # Only index 1 needs sanitizing; index 0 is already a string.
        if len(payload) < 2 or payload[1] is None:
            return list(payload)

        if self._renderer is None:
            if not getattr(self, '_warned_no_renderer', False):
                log.warning(
                    "FRAME event subscribed over IPC but IPCServer has no "
                    "renderer wired — surface payloads will be dropped.  "
                    "Pass renderer=… in the composition root to enable "
                    "frame forwarding to TrccProxy clients.")
                self._warned_no_renderer = True
            return [payload[0], None, *payload[2:]]

        from .core.wire import wrap_surface
        try:
            envelope = wrap_surface(self._renderer, payload[1])
        except Exception:
            log.exception("_sanitize_payload: encode_for_wire raised — "
                          "dropping surface payload")
            return [payload[0], None, *payload[2:]]
        return [payload[0], envelope, *payload[2:]]

    def _dispatch(self, request: dict) -> dict:
        """Route request to the matching dispatcher.

        Two wire shapes are valid:
          - ``{"kill": true}``   — daemon-control, ack and self-shutdown
          - ``{"role": ...}``    — manifold (multi-device by index)

        Anything else is rejected.
        """
        if request.get("kill"):
            return self._handle_kill()
        if "role" in request:
            return self._dispatch_manifold(request)
        return {"success": False,
                "error": f"Invalid request shape: {sorted(request)!r}"}

    def _handle_kill(self) -> dict:
        """Acknowledge a kill request, then schedule a clean shutdown.

        Shutdown is deferred via a single-shot timer so the ack flushes
        back to the client before the Qt event loop tears down. The
        client's send_manifold_request returns the ack; the client can
        then poll daemon_running() to confirm the daemon is gone.
        """
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self._kill_now)
        except Exception:
            log.exception("kill: failed to schedule shutdown — falling through")
            self._kill_now()
        return {"success": True, "message": "Daemon shutting down"}

    def _kill_now(self) -> None:
        """Tear down the IPC server and quit the daemon's Qt event loop."""
        try:
            self.shutdown()
        except Exception:
            log.exception("_kill_now: server.shutdown raised")
        try:
            from PySide6.QtWidgets import QApplication
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.quit()
        except Exception:
            log.exception("_kill_now: qapp.quit raised")

    def _dispatch_manifold(self, request: dict) -> dict:
        """Manifold format: route by (role, method) on the bound Trcc.

        Wire format::

            {"role": "lcd", "method": "set_brightness",
             "args": [0, 75], "kwargs": {}}

        The role names a facade on the Trcc (``lcd`` / ``led`` /
        ``control_center``); the method is invoked on it as
        ``fn(*args, **kwargs)``. No translation layer — the wire shape
        mirrors the Python call shape exactly, which is what makes
        `TrccProxy` a transparent drop-in replacement for `Trcc`.
        """
        role = str(request.get("role", ""))
        method = str(request.get("method", ""))
        # Unsanitize so wire markers (bytes-as-base64) become real values
        # before they hit the facade method.
        args = tuple(unsanitize_from_wire(a) for a in request.get("args", ()))
        kwargs = {k: unsanitize_from_wire(v)
                  for k, v in request.get("kwargs", {}).items()}

        # Trcc-level methods (discover, etc.) live under the `_meta` role.
        # They aren't on a facade — they're on the container itself.
        if role == "_meta":
            return self._dispatch_meta(method, args, kwargs)

        target = {
            "lcd": self._trcc.lcd,
            "led": self._trcc.led,
            "control_center": self._trcc.control_center,
        }.get(role)
        if target is None:
            return {"success": False, "error": f"Unknown role: {role!r}"}

        if method.startswith("_"):
            return {"success": False, "error": f"Private method: {method!r}"}

        fn = getattr(target, method, None)
        if not callable(fn):
            return {"success": False,
                    "error": f"Unknown method: {role}.{method}"}

        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            log.exception("manifold dispatch %s.%s failed", role, method)
            return {"success": False,
                    "error": f"{type(e).__name__}: {e}"}

        if not isinstance(result, OpResult):
            return {"success": False,
                    "error": f"{role}.{method} did not return an OpResult "
                             f"(got {type(result).__name__})"}
        return _result_to_dict(result)

    def _dispatch_meta(self, method: str, args: tuple, kwargs: dict) -> dict:
        """Trcc-level methods that don't belong to a single facade.

        ``discover()`` — USB rescan on the daemon.
        ``status()``   — daemon pid + uptime + device counts; used by
                         ``/trcc/status`` and `trcc daemon status`.
        Returns OpResult-shaped dicts so clients treat them uniformly.
        """
        if method == "discover":
            try:
                result = self._trcc.discover()
            except Exception as e:
                log.exception("_meta.discover failed")
                return {"success": False,
                        "error": f"{type(e).__name__}: {e}"}
            return _result_to_dict(result)
        if method == "metrics":
            try:
                metrics = self._trcc.os.metrics
            except Exception as e:
                log.exception("_meta.metrics failed")
                return {"success": False,
                        "error": f"{type(e).__name__}: {e}"}
            return {"success": True, "data": _metrics_to_dict(metrics)}
        if method == "status":
            return self._meta_status()
        if method == "lcd_descriptors":
            try:
                infos = self._trcc.lcd_descriptors()
            except Exception as e:
                log.exception("_meta.lcd_descriptors failed")
                return {"success": False,
                        "error": f"{type(e).__name__}: {e}"}
            return {"success": True,
                    "descriptors": [info.to_wire_dict() for info in infos]}
        if method == "led_descriptors":
            try:
                infos = self._trcc.led_descriptors()
            except Exception as e:
                log.exception("_meta.led_descriptors failed")
                return {"success": False,
                        "error": f"{type(e).__name__}: {e}"}
            return {"success": True,
                    "descriptors": [info.to_wire_dict() for info in infos]}
        return {"success": False,
                "error": f"Unknown _meta method: {method}"}

    def _meta_status(self) -> dict:
        """Snapshot of the daemon's runtime state."""
        import os as _os
        import time as _time

        from . import daemon as _daemon
        uptime = (_time.monotonic() - _daemon._started_at
                  if _daemon._started_at is not None else 0.0)
        return {
            "success": True,
            "pid": _os.getpid(),
            "uptime_seconds": round(uptime, 3),
            "lcd_count": len(self._trcc.lcd_devices),
            "led_count": len(self._trcc.led_devices),
        }


def _send_error(client: socket.socket, msg: str) -> None:
    try:
        client.sendall(json.dumps({"success": False, "error": msg}).encode() + b"\n")
    except OSError:
        pass


# =========================================================================
# Manifold-format client helpers — used by `TrccProxy` in core/trcc_proxy.py.
# Kept here so the wire format stays in one module (server + client side).
# =========================================================================

def daemon_running(*, socket_path: Path | None = None) -> bool:
    """Probe the daemon socket without sending a request."""
    if not hasattr(socket, 'AF_UNIX'):
        return False
    path = socket_path or _socket_path()
    if not path.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(path))
        s.close()
        return True
    except OSError:
        return False


def open_and_send(payload: dict, *, socket_path: Path | None = None,
                  timeout: float = 10.0) -> socket.socket:
    """Open a Unix socket to the daemon, send one JSON line, return it open.

    The returned socket is the caller's to manage. Use it for one-shot
    request/response (read a line, close) or for long-lived streams
    (subscribe channel, keep open). Raises ``OSError`` on connect/send
    failure — caller decides whether to swallow into a failed Result.
    """
    if not hasattr(socket, 'AF_UNIX'):
        raise OSError("AF_UNIX not available on this platform")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path or _socket_path()))
        s.sendall(json.dumps(payload).encode() + b"\n")
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        raise
    return s


def read_json_line(sock: socket.socket) -> dict:
    """Read one JSON line from a socket, return the parsed dict.

    Returns ``{}`` on EOF / empty payload. Raises ``json.JSONDecodeError``
    on malformed JSON, ``OSError`` on transport failure, ``TimeoutError``
    if the socket is in timeout mode and times out — same exception
    classes :func:`one_shot_request` catches for its total-contract.
    """
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    payload = b"".join(chunks).decode().strip()
    return json.loads(payload) if payload else {}


def one_shot_request(payload: dict, *, socket_path: Path | None = None,
                     timeout: float = 10.0) -> dict:
    """Send + receive one JSON line + close. Total contract.

    Every transport-level failure (no daemon, timeout, malformed reply)
    is caught and returned as ``{"success": False, "error": "<details>"}``
    — callers can treat the result as data, never an exception.
    """
    try:
        s = open_and_send(payload, socket_path=socket_path, timeout=timeout)
    except (TimeoutError, OSError) as e:
        return {"success": False,
                "error": f"IPC transport: {type(e).__name__}: {e}"}
    try:
        response = read_json_line(s)
    except (TimeoutError, OSError, json.JSONDecodeError) as e:
        return {"success": False,
                "error": f"IPC transport: {type(e).__name__}: {e}"}
    finally:
        try:
            s.close()
        except OSError:
            pass
    return response or {"success": False, "error": "IPC: empty response"}


def send_manifold_request(role: str, method: str,
                          args: tuple, kwargs: dict,
                          *, socket_path: Path | None = None,
                          timeout: float = 10.0) -> dict:
    """Send a manifold-format request to the daemon, return the response.

    Wire format::

        {"role": "lcd", "method": "set_brightness",
         "args": [0, 75], "kwargs": {}}

    Thin wrapper over :func:`one_shot_request` that knows the manifold
    payload shape. Most callers should use this, not the lower-level
    helpers — keeps the wire shape pinned to one place.
    """
    return one_shot_request(
        {"role": role, "method": method,
         "args": list(args), "kwargs": kwargs},
        socket_path=socket_path, timeout=timeout,
    )
