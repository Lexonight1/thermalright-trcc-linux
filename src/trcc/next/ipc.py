"""IPC for the next/ daemon — manifold dispatch over a Unix socket.

The daemon (`trcc-next daemon`) owns one ``App`` and serves requests
through ``IPCServer``.  Clients (``AppProxy`` returned by
``_boot.trcc_next()`` when ``TRCC_NEXT_DAEMON=1``) call ``dispatch(cmd)``
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

Bytes are encoded as ``{"__bytes__": "<base64>"}`` so binary payloads
(``SendFrame``) survive JSON.  ``Path`` survives as ``str(path)``.
"""
from __future__ import annotations

import base64
import dataclasses
import inspect
import json
import logging
import os
import socket
import time
import types
import typing
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import core
from .core import commands as _commands_module
from .core import results as _results_module
from .core.commands import Command
from .core.results import Result

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

_SOCK_NAME = "trcc-next.sock"
_BYTES_MARKER = "__bytes__"
_DEFAULT_TIMEOUT_S = 30.0


# =========================================================================
# Socket path
# =========================================================================


def socket_path() -> Path:
    """Return the canonical Unix-socket path for this user's next/ daemon."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(runtime) / _SOCK_NAME


def daemon_running() -> bool:
    """True iff a daemon socket exists and accepts a connection."""
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
    return {
        name: cls
        for name, cls in vars(module).items()
        if inspect.isclass(cls) and issubclass(cls, base) and cls is not base
    }


COMMAND_TYPES: dict[str, type[Command[Any]]] = _collect_classes(
    _commands_module, Command,
)
RESULT_TYPES: dict[str, type[Result]] = {
    Result.__name__: Result,
    **_collect_classes(_results_module, Result),
}


# =========================================================================
# Sanitize / unsanitize — Path / bytes / Enum at the JSON boundary
# =========================================================================


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
                for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [_to_wire(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_wire(v) for k, v in value.items()}
    # Last resort — let json.dumps complain if this isn't representable
    return value


def _from_wire(raw: Any) -> Any:
    """Reverse of :func:`_to_wire` for ``bytes`` only.

    Enums and dataclasses need their target type (see ``_coerce``) — this
    walker only handles the type-blind bytes marker.
    """
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


def _build_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Reconstruct a dataclass instance from a JSON-decoded dict."""
    hints = typing.get_type_hints(cls)
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
    # Every concrete Command is a frozen dataclass; the runtime check
    # narrows the type for the static checker too.
    assert dataclasses.is_dataclass(cmd), (
        f"{type(cmd).__name__} is not a dataclass — every Command subclass "
        "must use @dataclass(frozen=True, slots=True)"
    )
    kwargs = {f.name: _to_wire(getattr(cmd, f.name))
              for f in dataclasses.fields(cmd)}
    return {"command": type(cmd).__name__, "kwargs": kwargs}


def decode_command(envelope: dict[str, Any]) -> Command[Any]:
    """Reconstruct a Command from a dispatch envelope."""
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
    body = {f.name: _to_wire(getattr(result, f.name))
            for f in dataclasses.fields(result)}
    return {"type": type(result).__name__, **body}


def decode_result(envelope: dict[str, Any]) -> Result:
    """Reconstruct a Result from a response envelope."""
    type_name = envelope.get("type", "Result")
    cls = RESULT_TYPES.get(str(type_name), Result)
    body = {k: v for k, v in envelope.items() if k != "type"}
    return _build_dataclass(cls, body)


# =========================================================================
# Wire transport — one-shot client + simple line-oriented server
# =========================================================================


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall(json.dumps(payload).encode() + b"\n")


def _recv_json(sock: socket.socket, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Read one newline-delimited JSON object from *sock*."""
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
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path()))
        _send_json(sock, payload)
        return _recv_json(sock)


# =========================================================================
# IPCServer — bound to one App, serves requests on a Unix socket
# =========================================================================


class _ShutdownRequested(Exception):
    """Internal marker — the request handler asked the server to exit."""


class IPCServer:
    """Threaded Unix-socket server.

    One blocking accept loop runs on a background thread; each connection
    is dispatched on its own short-lived worker thread.  Concurrent
    Command dispatch on the shared App is serialized by ``app._lock``
    (held inside ``app.dispatch`` once we wire it).
    """

    def __init__(self, app: App) -> None:
        self._app = app
        self._sock: socket.socket | None = None
        self._stop = False
        self._workers: list[Any] = []   # threading.Thread, kept for join on shutdown

    def start(self) -> None:
        """Bind + listen.  Caller is responsible for serving (see ``serve_forever``)."""
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX not available — daemon mode requires Unix")
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(8)
        path.chmod(0o600)
        self._sock = sock
        log.info("IPC server listening on %s", path)

    def serve_forever(self) -> None:
        """Accept connections until ``shutdown`` is called or a kill arrives."""
        import threading
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
                daemon=True, name="trcc-next-ipc",
            )
            t.start()
            self._workers.append(t)

    def shutdown(self) -> None:
        """Stop accepting + clean up the socket file."""
        self._stop = True
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                log.debug("shutdown: SHUT_RDWR on listening socket failed",
                          exc_info=True)
            self._sock.close()
            self._sock = None
        path = socket_path()
        if path.exists():
            try:
                path.unlink()
            except OSError:
                log.debug("shutdown: socket unlink failed", exc_info=True)
        log.info("IPC server shut down")

    def _serve_client(self, client: socket.socket) -> None:
        try:
            client.settimeout(_DEFAULT_TIMEOUT_S)
            envelope = _recv_json(client)
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
            try:
                client.close()
            except OSError:
                log.debug("client close failed", exc_info=True)

    def _dispatch_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        try:
            cmd = decode_command(envelope)
        except (ValueError, TypeError) as e:
            return {"type": "Result", "ok": False, "message": f"Decode error: {e}"}
        try:
            result = self._app.dispatch(cmd)
        except Exception as e:
            log.exception("Command %s raised", type(cmd).__name__)
            return {"type": "Result", "ok": False,
                    "message": f"{type(e).__name__}: {e}"}
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
