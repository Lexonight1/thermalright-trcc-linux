"""trcc daemon — background process that owns USB + serves UIs.

One process per user.  Holds the singleton ``App`` and serves Commands
over a Unix-domain socket via ``IPCServer``.  CLI clients dispatched
through ``AppProxy`` see the same Command API; the proxy round-trips
each call to the daemon.

Lifecycle::

    1. Bail out fast if another daemon already owns the socket.
    2. Build the App through ``_boot``.
    3. Bind IPCServer to the App and start the accept loop.
    4. Install SIGTERM / SIGINT handlers that flip the server's stop flag.
    5. Block in ``serve_forever`` until the loop exits.

Opt-in today (``TRCC_DAEMON=1``).  No Qt event loop — next/'s App
is framework-blind, so the daemon is a plain Python process.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from . import ipc

if TYPE_CHECKING:
    from .core.ports import Platform, Renderer

log = logging.getLogger(__name__)


# Set when run_daemon() takes ownership of the process so endpoints
# like ``/system/status`` can report uptime.  None outside the daemon.
_started_at: float | None = None


# =========================================================================
# Daemon entry point
# =========================================================================


def run_daemon(
    platform: Platform | None = None,
    renderer: Renderer | None = None,
) -> int:
    """Bind the socket, build the App, serve until shutdown.  Exit code.

    A thin alias over the UI bus: the daemon is a face of the one app like the
    GUI or the API, so its launch sequence lives on the shared
    ``UserInterface.start`` template rather than being hand-written here for
    the fourth time.  This function is kept because ``dev/_mock_daemon.py``,
    the CLI and the tests all call it with an injected platform.

    What the bus owns: the "another daemon already holds the socket" refusal
    (``DaemonUI.preflight``, exit 1), building a LOCAL App with ``TRCC_DAEMON``
    stripped so the daemon can never proxy to itself (#162), the session
    bring-up, and the single ``App.close()``.  What stays daemon-specific is
    ``DaemonUI.run`` -- the IPC server and its signal handlers.

    ``App.close()`` used to be called here AND by the caller; the template now
    owns it exactly once, which is the double-teardown the GUI window had
    already had removed for the same reason.
    """
    log.info("run_daemon: delegating to the UI bus")
    from .ui._uis import DaemonUI
    return DaemonUI(renderer=renderer).start(platform)


# =========================================================================
# Client helpers — auto-spawn + remote kill
# =========================================================================


def mark_started() -> None:
    """Record that THIS process is now the daemon.

    The writer partner of :func:`is_this_process_the_daemon` and
    :func:`uptime_s`, which both read the same private clock.  Public because
    the daemon's launch sequence lives on the UI bus (``ui/_uis.py``) rather
    than in this module, and reaching in to set a module private from there
    would be exactly the kind of hidden coupling the bus exists to remove.
    """
    global _started_at
    _started_at = time.monotonic()
    log.info("mark_started: this process is the daemon (pid=%d)", os.getpid())


def is_this_process_the_daemon() -> bool:
    """True iff THIS process called :func:`run_daemon`.

    The distinction matters because "a daemon is running" and "I am it" are
    different facts, and a client that conflates them reports its own pid and
    a zero uptime as though they were the daemon's.  Public because the answer
    belongs to callers, not to this module's internals — ``ui/api/trcc.py``
    used to read the private ``_started_at`` directly.
    """
    log.debug("is_this_process_the_daemon: %s", _started_at is not None)
    return _started_at is not None


def uptime_s() -> int:
    """Seconds since this process became the daemon; 0 if it never did.

    Zero is honest here: a process that is not the daemon does not know how
    long the daemon has been up, and inventing a number would be worse than
    reporting none.  Ask the daemon (dispatch ``DaemonStatus`` over the
    socket) and the Command runs THERE, where the answer is real.
    """
    if _started_at is None:
        log.debug("uptime_s: this process is not the daemon — 0")
        return 0
    seconds = int(time.monotonic() - _started_at)
    log.debug("uptime_s: %ds", seconds)
    return seconds


#: How long an older App gets to shut down when it is replaced: its close
#: blanks and releases every panel (~5 s for a 10-panel fleet, measured).
_REPLACE_TIMEOUT_S = 15.0


def ensure_daemon(*, timeout: float = 10.0) -> bool:
    """Make sure a current daemon is reachable, spawning one if not.

    A running daemon OLDER than this install -- or too old to say -- is
    replaced: stopped, and this install's started, whose session restores what
    the panels showed.  A NEWER one is kept (never downgrade) and warned
    about.  Returns True when a daemon is reachable, False if the spawn didn't
    come up within *timeout* seconds.
    """
    if ipc.daemon_running():
        from .__version__ import __version__
        if is_this_process_the_daemon():
            # EnsureDaemon dispatched over the socket runs HERE; asking our own
            # socket would block on the request we are serving.
            log.debug("ensure_daemon: this process is the daemon")
            return True
        theirs = _running_version()
        if not _older(theirs, __version__):
            if theirs != __version__:
                log.warning("ensure_daemon: the running App is %s, newer than "
                            "this %s — using it", theirs, __version__)
            return True
        log.info("ensure_daemon: the running App is %s, older than this %s — "
                 "replacing it", theirs or "too old to say", __version__)
        if not kill_daemon(timeout=_REPLACE_TIMEOUT_S):
            log.warning("ensure_daemon: the old App did not stop within %.0fs "
                        "— using it", _REPLACE_TIMEOUT_S)
            return ipc.daemon_running()

    cmd = _daemon_spawn_cmd()
    log.info("Spawning next/ daemon: %s", " ".join(cmd))
    # Strip the daemon-mode flag from the child's env so the spawned daemon
    # can't inherit it and try to proxy to itself — it builds the App
    # in-process via run_daemon → _build_local_app regardless (#162).
    from ._boot import _ENV_FLAG
    child_env = {k: v for k, v in os.environ.items() if k != _ENV_FLAG}
    subprocess.Popen(
        cmd,
        env=child_env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    return ipc.wait_for_daemon(timeout=timeout)


def kill_daemon(*, timeout: float = 5.0) -> bool:
    """Ask a running daemon to shut down, wait for the socket to clear.

    Returns True when no daemon is reachable, False on timeout.  Idempotent.
    """
    log.info("kill_daemon: timeout=%s", timeout)
    if not ipc.daemon_running():
        return True
    try:
        response = ipc.one_shot_request({"kill": True}, timeout=2.0)
    except OSError as e:
        log.warning("kill_daemon: %s", e)
        return False
    if not response.get("ok"):
        log.warning("kill_daemon: %s", response.get("message"))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not ipc.daemon_running():
            return True
        time.sleep(0.05)
    return False


# =========================================================================
# Internals
# =========================================================================


def _install_signal_handlers(server: ipc.IPCServer) -> None:
    """SIGTERM / SIGINT flip the server's stop flag + wake the accept loop."""
    log.debug("_install_signal_handlers: called")
    def _shutdown(signo: int, _frame: Any) -> None:
        name = signal.Signals(signo).name
        log.info("trcc daemon: received %s — shutting down", name)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def _running_version() -> str:
    """The running daemon's version, asked over its socket.

    ``""`` when it cannot say -- a daemon older than ``DaemonResult.version``
    sends no such field, and one that does not answer is no better.
    """
    from .core.commands import DaemonStatus
    from .proxy import AppProxy
    proxy = AppProxy(timeout=5.0)
    try:
        version = proxy.dispatch(DaemonStatus()).version
    except Exception as e:
        log.warning("_running_version: the running App did not say (%s: %s)",
                    type(e).__name__, e)
        version = ""
    finally:
        proxy.close()
    log.info("_running_version: %s", version or "unknown")
    return version


def _older(theirs: str, ours: str) -> bool:
    """Whether version *theirs* is older than *ours* ("" is oldest)."""
    import re
    older = ([int(n) for n in re.findall(r"\d+", theirs)]
             < [int(n) for n in re.findall(r"\d+", ours)])
    log.debug("_older: %r < %r -> %s", theirs, ours, older)
    return older


def _daemon_spawn_cmd() -> list[str]:
    """Argv that starts a daemon running THIS install's code.

    The running interpreter, not the first ``trcc`` on PATH: with two installs
    (pipx + a distro package), a UI could start a daemon of the other one --
    and with a version check, replace it forever.  A frozen build (Windows,
    macOS) has no ``-m``; it keeps the console-script lookup until the daemon
    is verified there.
    """
    log.debug("_daemon_spawn_cmd: frozen=%s", getattr(sys, "frozen", False))
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "trcc", "daemon"]
    from shutil import which
    if (trcc_bin := which("trcc")) is not None:
        return [trcc_bin, "daemon"]
    return [sys.executable, "-m", "trcc", "daemon"]
