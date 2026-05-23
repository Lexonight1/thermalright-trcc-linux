#!/usr/bin/env python3
"""MockPlatform-backed daemon for the daemon-mode smoke harness.

A short-lived ``trccd`` analogue: builds a Trcc against the mock USB
transport (zero real hardware needed), wires an IPCServer onto a tmp
socket, and runs a Qt event loop until ``{"kill": True}`` arrives.

Spawned by ``dev/smoke_daemon_gui.py`` as a subprocess.  The two
processes communicate by:

  - shared socket path passed via ``$TRCC_MOCK_DAEMON_SOCKET`` env var
  - shared dev/.trcc data root (the mock harness root)
  - the standard manifold + event subscription protocols

Usage from a parent test::

    env = dict(os.environ, TRCC_MOCK_DAEMON_SOCKET=str(sock_path))
    proc = subprocess.Popen([sys.executable, 'dev/_mock_daemon.py'], env=env)
    # ... run TrccProxy(socket_path=sock_path) assertions ...
    one_shot_request({'kill': True}, socket_path=sock_path)
    proc.wait(timeout=5)
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any, cast

# Same Qt env knobs as the regular daemon.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import bootstrap


def _socket_path_from_env() -> Path:
    raw = os.environ.get("TRCC_MOCK_DAEMON_SOCKET")
    if not raw:
        sys.stderr.write(
            "_mock_daemon: $TRCC_MOCK_DAEMON_SOCKET not set — "
            "the smoke harness must pass an explicit socket path.\n")
        sys.exit(2)
    return Path(raw)


def main() -> int:
    sock_path = _socket_path_from_env()
    platform = bootstrap()

    from PySide6.QtWidgets import QApplication
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))

    from trcc._boot import trcc as _boot_trcc
    from trcc.adapters.render.qt import QtRenderer
    renderer = QtRenderer()
    t = _boot_trcc(cast(Any, platform), renderer=renderer, discover_now=True)

    # IPCServer bound to the test socket, with renderer wired in for
    # Topic.FRAME envelope sanitization (10C.4).
    from trcc.ipc import IPCServer
    server = IPCServer(trcc=t, renderer=renderer)
    # IPCServer.start() uses _socket_path() by default — point it at our
    # tmp path explicitly via the same env-override the production
    # socket helpers honour.  Doing it explicitly makes the dependency
    # clear in the smoke output.
    os.environ["XDG_RUNTIME_DIR"] = str(sock_path.parent)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    # Override _SOCK_NAME indirectly by making sock_path's filename the name.
    import trcc.ipc as _ipc
    _ipc._SOCK_NAME = sock_path.name  # type: ignore[attr-defined]
    server.start()

    # SIGTERM kills the process; the smoke harness sends `{"kill": True}`
    # via IPC for clean shutdown but we honour signals as a fallback.
    signal.signal(signal.SIGTERM, lambda *_: qapp.quit())
    signal.signal(signal.SIGINT, lambda *_: qapp.quit())

    sys.stdout.write(f"_mock_daemon: ready on {sock_path}\n")
    sys.stdout.flush()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
