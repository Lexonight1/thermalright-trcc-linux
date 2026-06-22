"""IPC for the next/ daemon — manifold dispatch over a Unix socket.

The daemon (`trcc daemon`) owns one ``App`` and serves requests
through ``IPCServer``.  Clients (``AppProxy`` returned by
``_boot.trcc()`` when ``TRCC_DAEMON=1``) call ``dispatch(cmd)``
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

_SOCK_NAME = "trcc.sock"
_BYTES_MARKER = "__bytes__"
_DEFAULT_TIMEOUT_S = 30.0


# =========================================================================
# Socket path
# =========================================================================


def socket_path() -> Path:
    """Return the canonical Unix-socket path for this user's next/ daemon."""
    log.debug("socket_path: called")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(runtime) / _SOCK_NAME


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
    # Unserializable at the JSON boundary (e.g. a renderer surface on an
    # in-process-only event field like FrameSent.surface).  Warn loudly
    # and drop to None rather than let json.dumps crash the whole call —
    # the receiver treats None as "not available" (the preview re-renders).
    log.warning("_to_wire: non-serializable %s dropped to None at IPC "
                "boundary", type(value).__name__)
    return None


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
    log.debug("encode_command: cmd=%s", type(cmd).__name__)
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
    log.debug("decode_command: keys=%s", sorted(envelope))
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
    log.debug("encode_result: result=%s", type(result).__name__)
    body = {f.name: _to_wire(getattr(result, f.name))
            for f in dataclasses.fields(result)}
    return {"type": type(result).__name__, **body}


def decode_result(envelope: dict[str, Any]) -> Result:
    """Reconstruct a Result from a response envelope."""
    log.debug("decode_result: keys=%s", sorted(envelope))
    type_name = envelope.get("type", "Result")
    cls = RESULT_TYPES.get(str(type_name), Result)
    body = {k: v for k, v in envelope.items() if k != "type"}
    return _build_dataclass(cls, body)


# =========================================================================
# Wire transport — one-shot client + simple line-oriented server
# =========================================================================


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    log.debug("_send_json: keys=%s", sorted(payload))
    sock.sendall(json.dumps(payload).encode() + b"\n")


def _recv_json(sock: socket.socket, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Read one newline-delimited JSON object from *sock*."""
    log.debug("_recv_json: max_bytes=%d", max_bytes)
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
    log.info("one_shot_request: keys=%s timeout=%s",
             sorted(payload), timeout)
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
        log.info("IPC server listening on %s", path)

    def serve_forever(self) -> None:
        """Accept connections until ``shutdown`` is called or a kill arrives."""
        log.info("serve_forever: called")
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
                daemon=True, name="trcc-ipc",
            )
            t.start()
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
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime) / SingleInstance._DIR_NAME
    else:
        base = Path.home() / ".cache" / SingleInstance._DIR_NAME
    return base / f"{name}.sock"


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
    """True if a peer is currently bound + accepting on ``path``."""
    if not path.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.close()
        return True
    except OSError:
        return False


def _send_raise(path: Path, payload: bytes, timeout: float) -> None:
    """Connect + send a raise message to an existing peer."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(path))
    try:
        sock.sendall(payload)
    finally:
        sock.close()
