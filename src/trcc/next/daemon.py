"""trcc-next daemon — background process that owns USB + serves UIs.

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

Opt-in today (``TRCC_NEXT_DAEMON=1``).  No Qt event loop — next/'s App
is framework-blind, so the daemon is a plain Python process.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any

from . import ipc

log = logging.getLogger(__name__)


# Set when run_daemon() takes ownership of the process so endpoints
# like ``/system/status`` can report uptime.  None outside the daemon.
_started_at: float | None = None


# =========================================================================
# Daemon entry point
# =========================================================================


def run_daemon() -> int:
    """Bind the socket, build the App, serve until shutdown.

    Returns a Unix exit code — 0 on clean shutdown, 1 if another daemon
    was already running and we declined to start.
    """
    if ipc.daemon_running():
        log.warning("trcc-next daemon: another daemon already owns %s",
                    ipc.socket_path())
        return 1

    global _started_at
    _started_at = time.monotonic()
    log.info("trcc-next daemon starting (pid=%d)", os.getpid())

    from ._boot import _build_local_app
    app = _build_local_app()
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
    log.info("trcc-next daemon exited")
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
    subprocess.Popen(
        cmd,
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
    def _shutdown(signo: int, _frame: Any) -> None:
        name = signal.Signals(signo).name
        log.info("trcc-next daemon: received %s — shutting down", name)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def _daemon_spawn_cmd() -> list[str]:
    """Argv that re-invokes this Python as the daemon entry point.

    Prefer the installed ``trcc-next`` console script when on PATH so the
    daemon picks up the user's installed entry point; otherwise fall back
    to ``python -m trcc.next daemon``.
    """
    from shutil import which
    if (trcc_bin := which("trcc-next")) is not None:
        return [trcc_bin, "daemon"]
    return [sys.executable, "-m", "trcc.next", "daemon"]
