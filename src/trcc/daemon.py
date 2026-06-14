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
    """Bind the socket, build the App, serve until shutdown.

    Returns a Unix exit code — 0 on clean shutdown, 1 if another daemon
    was already running and we declined to start.

    ``platform`` / ``renderer`` are optional injection seams (default
    ``None`` → auto-detect, the production path): the same DI shape
    ``_boot.trcc`` / ``_build_local_app`` expose, so a harness can run the
    *real* daemon entry against a scripted ``Platform`` (``dev/_mock_daemon``)
    instead of hand-rolling bring-up.
    """
    if ipc.daemon_running():
        log.warning("trcc daemon: another daemon already owns %s",
                    ipc.socket_path())
        return 1

    global _started_at
    _started_at = time.monotonic()
    log.info("trcc daemon starting (pid=%d)", os.getpid())

    # The daemon owns USB directly — it must never proxy to itself.  If the
    # daemon-mode flag leaked into this process's env (set globally in a
    # shell profile, or inherited from the spawning client), every
    # ``trcc()`` here would try to reach a daemon socket instead of
    # opening USB, and a startup path through it would re-spawn — a fork
    # bomb (#162).  Strip it so this process is unambiguously the daemon.
    from ._boot import _ENV_FLAG, _build_local_app
    os.environ.pop(_ENV_FLAG, None)
    app = _build_local_app(platform=platform, renderer=renderer)
    app.start_hotplug()
    # Without the metrics loop the daemon owns USB but never ticks — the
    # device stays connected yet permanently blank (#148).  ``App.close()``
    # in the finally below stops it on shutdown.
    app.metrics_loop.start()
    server = ipc.IPCServer(app)
    server.start()
    _install_signal_handlers(server)

    try:
        server.serve_forever()
    finally:
        server.shutdown()
        # ``App.close`` releases every attached device's transport — important
        # because the daemon may have been holding /dev/sgN open for hours.
        try:
            app.close()
        except Exception:
            log.exception("App.close raised during daemon shutdown")
    log.info("trcc daemon exited")
    return 0


# =========================================================================
# Client helpers — auto-spawn + remote kill
# =========================================================================


def ensure_daemon(*, timeout: float = 10.0) -> bool:
    """Make sure a daemon is reachable, spawning one if not.

    Returns True when the socket becomes reachable, False if the spawn
    didn't come up within *timeout* seconds.  Idempotent — if a daemon
    is already up this is a fast no-op.
    """
    if ipc.daemon_running():
        return True

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


def _daemon_spawn_cmd() -> list[str]:
    """Argv that re-invokes this Python as the daemon entry point.

    Prefer the installed ``trcc`` console script when on PATH so the
    daemon picks up the user's installed entry point; otherwise fall back
    to ``python -m trcc daemon``.
    """
    log.debug("_daemon_spawn_cmd: called")
    from shutil import which
    if (trcc_bin := which("trcc")) is not None:
        return [trcc_bin, "daemon"]
    return [sys.executable, "-m", "trcc", "daemon"]
