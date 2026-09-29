"""AppProxy — client-side drop-in for ``App`` that talks to the daemon.

UIs hold a typed ``App`` and never distinguish; in daemon mode the
``_boot.trcc()`` factory hands them an ``AppProxy`` instead.  Only
``dispatch(cmd) -> Result`` is real — every call serializes the Command,
sends it over the Unix socket, and reconstructs the typed Result.

``events`` is the ONE other attribute that is real, and it has to be: both
Qt skins build a ``BusBridge(app.events)`` at construction, so without it a
GUI cannot run as a daemon client at all — which is what kept
``TRCC_DAEMON=1`` off by default and the bus optional.  The proxy answers it
with a LOCAL ``EventBus`` fed by the daemon's stream, so every existing
subscriber works unchanged and nothing in ``ui/`` needs to know.

Other ``App`` attributes (``platform`` / ``settings`` / ``devices`` /
``display``) are not exposed by the proxy.  UIs that need state should
dispatch a Command (``DiscoverDevices`` / ``GetPlatformInfo`` /
``ReadSensors``) — that's the API contract daemon mode honors.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable
from typing import TypeVar

from . import ipc
from .core.commands import Command, DiscoverDevices
from .core.errors import DaemonUnavailableError, RemoteCommandError
from .core.events import EventBus
from .core.logs import current_origin
from .core.ports import CommandBus
from .core.results import Result

log = logging.getLogger(__name__)


R = TypeVar("R", bound=Result)


class AppProxy(CommandBus):
    """Forwards every ``dispatch(cmd)`` call to a running daemon.

    Construction is cheap (no socket connection until the first
    dispatch) so import-time use of ``_boot.trcc()`` doesn't pay
    a round-trip per invocation.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        log.debug("__init__")
        self._timeout = timeout
        self._events: EventBus | None = None
        self._reader: threading.Thread | None = None
        self._stream_sock: socket.socket | None = None
        self._stream_open = False
        # Set by ``close``.  Distinguishes a deliberate shutdown from a daemon
        # that died: the first is routine, the second is the thing a user
        # needs told about, and logging both the same way buries it.
        self._closing = False

    def dispatch(self, cmd: Command[R]) -> R:
        """Serialize *cmd*, round-trip through the daemon, return the Result.

        Three outcomes, three shapes — chosen from measurement, not taste:

        * the Command ran → its typed Result, exactly as in-process;
        * the Command RAISED daemon-side → :class:`RemoteCommandError`.
          In-process a raising Command propagates, and this restores that.
          Before the marker existed the client got a base ``Result`` and then
          ``AttributeError: 'Result' object has no attribute 'devices'`` on
          first field access — the failure was never survivable, only
          illegible;
        * the daemon is gone → :class:`DaemonUnavailableError`.

        Why the last two RAISE rather than return ``Result(ok=False)``:
        ``DiscoverResult(ok=False)`` carries ``products=[]``, and **198 of 492
        dispatch sites never check ``.ok``** — so a Result would quietly render
        "no devices" on a screen whose daemon just died.  Neither is a new
        failure: a dead daemon already raised ``ConnectionRefusedError``.  Both
        now raise something with a NAME.
        """
        envelope = ipc.encode_command(cmd)
        # Which UI asked, so the DAEMON's log names the client, not itself.
        # Older daemons ignore the key (``decode_command`` reads only
        # ``command`` + ``kwargs``).
        envelope["origin"] = current_origin()
        try:
            response = ipc.one_shot_request(envelope, timeout=self._timeout)
        except OSError as e:
            log.warning("AppProxy.dispatch: %s unreachable (%s: %s)",
                        type(cmd).__name__, type(e).__name__, e)
            raise DaemonUnavailableError(
                f"the TRCC daemon is not reachable ({type(e).__name__}: {e}); "
                f"{type(cmd).__name__} was not run",
            ) from e
        if (remote := response.get(ipc._ERROR_KEY)) is not None:
            log.warning("AppProxy.dispatch: %s raised daemon-side: %s",
                        type(cmd).__name__, remote)
            raise RemoteCommandError(f"{type(cmd).__name__}: {remote}")
        result = ipc.decode_result(response)
        log.debug("AppProxy.dispatch: %s -> %s",
                  type(cmd).__name__, type(result).__name__)
        return result  # type: ignore[return-value]   # caller's TypeVar binds the subclass

    @property
    def remote(self) -> bool:
        """True: the daemon runs the Commands (the ``CommandBus`` port)."""
        log.debug("AppProxy.remote: the daemon runs the Commands")
        return True

    # ── The observe half ────────────────────────────────────────────────

    @property
    def events(self) -> EventBus:
        """A local bus carrying the daemon's events.

        Lazily opens the stream on first access, so a CLI one-shot that never
        observes anything pays nothing.  Subscribers registered here receive
        real reconstructed ``Event`` instances — ``BusBridge`` subscribes BY
        TYPE, so decoding to the concrete class (not a dict) is what makes the
        Qt skins work untouched.
        """
        if self._events is None:
            log.info("AppProxy.events: opening the daemon event stream")
            self._events = EventBus()
            self._start_reader()
        return self._events

    def _start_reader(self) -> None:
        """Spawn the background reader that feeds the local bus."""
        log.info("AppProxy._start_reader: starting reader thread")
        self._reader = threading.Thread(
            target=self._read_events, daemon=True, name="trcc-proxy-events",
        )
        self._reader.start()

    def _read_events(self) -> None:
        """Read the stream until EOF, republishing onto the local bus.

        EOF is SURFACED, not swallowed: a GUI whose daemon died would
        otherwise sit there looking connected while every panel silently
        stopped updating.  The warning names the count so a report shows how
        far it got.
        """
        seen = 0
        try:
            sock = ipc.open_event_stream(timeout=self._timeout)
        except (OSError, ConnectionError) as e:
            log.warning("AppProxy._read_events: cannot open the event stream "
                        "(%s: %s) — this client will receive no events",
                        type(e).__name__, e)
            return
        self._stream_sock = sock
        self._stream_open = True
        try:
            with sock, sock.makefile("rb") as reader:
                for line in reader:
                    if not line.strip():
                        continue
                    try:
                        event = ipc.decode_event(json.loads(line.decode()))
                    except (ValueError, KeyError) as e:
                        log.warning("AppProxy._read_events: undecodable event "
                                    "dropped (%s: %s)", type(e).__name__, e)
                        continue
                    seen += 1
                    assert self._events is not None
                    self._events.publish(event)
        except OSError as e:
            if not self._closing:
                log.warning("AppProxy._read_events: stream failed after %d "
                            "event(s) — %s: %s", seen, type(e).__name__, e)
        finally:
            self._stream_open = False
            self._stream_sock = None
            if self._closing:
                log.info("AppProxy._read_events: stream closed on request "
                         "after %d event(s)", seen)
            else:
                log.warning("AppProxy._read_events: event stream CLOSED after "
                            "%d event(s); this client is no longer observing",
                            seen)

    # ── Session lifecycle — the daemon owns it, this client does not ────
    #
    # These three exist so ``run_gui`` / ``run_qtgui`` are IDENTICAL in both
    # modes.  The alternative is a UI asking "am I remote?", which is the
    # environment sniffing the architecture forbids and which ``AppProxy``
    # exists to make unnecessary.  Same interface, transport-appropriate
    # meaning — the Adapter pattern doing its job.
    #
    # They are deliberately NOT silent.  A no-op that logs nothing is
    # indistinguishable from a call that failed, and "the GUI came up but no
    # device connected" is exactly the report these would otherwise produce.

    def start_session(
        self, on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """No-op: the daemon brought the session up before this client existed.

        Coldplug and the live loops belong to whoever owns USB.  A client
        starting a second metrics loop would poll the sensors twice and
        publish two ``SensorsUpdated`` streams for one machine.
        """
        log.info("AppProxy.start_session: the daemon owns the session — "
                 "not starting coldplug or loops in this client")
        if on_progress is not None:
            # A splash worker waits on this callback; leaving it un-called
            # hangs the splash forever.
            on_progress("Connected to the TRCC daemon")

    def close(self) -> None:
        """Release THIS client's resources, and only this client's.

        A no-op for the daemon's devices: ``run_gui``'s ``finally`` calls this
        unconditionally, and in daemon mode disconnecting here would tear down
        the panels of every OTHER client — and of the daemon itself — because
        one window was closed.

        But the event stream IS this client's, and it must be closed.  The
        reader is a thread blocked on a socket read; without this, every
        window that opens and closes leaks a thread and a file descriptor for
        the life of the process.  ``shutdown`` is what breaks the blocking
        read — closing the socket alone does not wake the reader.
        """
        log.info("AppProxy.close: leaving the daemon's devices attached; "
                 "closing this client's event stream")
        self._closing = True
        sock = self._stream_sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                log.debug("AppProxy.close: stream shutdown failed",
                          exc_info=True)
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
            if reader.is_alive():
                log.warning("AppProxy.close: reader thread did not stop "
                            "within 2s")
        self._reader = None
        self._events = None
        self._closing = False

    def discover_and_connect(
        self, on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """Report what the daemon already has attached.

        The coldplug itself is the daemon's; running it here would race two
        processes for the same USB handles.  Dispatching ``DiscoverDevices``
        gives the splash something true to say without touching hardware.
        """
        log.info("AppProxy.discover_and_connect: querying the daemon's fleet")
        if on_progress is not None:
            on_progress("Asking the daemon which devices are attached…")
        result = self.dispatch(DiscoverDevices())
        count = len(getattr(result, "devices", ()) or ())
        log.info("AppProxy.discover_and_connect: daemon reports %d device(s)",
                 count)
        if on_progress is not None:
            on_progress(f"{count} device(s) attached")

    # ── Attributes that a real App exposes but the proxy can't ──────────

    def __getattr__(self, name: str) -> object:
        log.debug("__getattr__: name=%s", name)
        raise AttributeError(
            f"AppProxy has no attribute {name!r} — daemon mode only exposes "
            "dispatch(cmd); use a Command to query App state remotely"
        )
