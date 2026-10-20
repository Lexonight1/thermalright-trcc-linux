"""IPCServer.start() must degrade gracefully without AF_UNIX (Windows).

Legacy returned here ("IPC server skipped -- AF_UNIX not available"); the cutover
changed it to ``raise RuntimeError``, which crashed the Windows GUI on launch —
``run_gui`` calls ``ipc_server.start()`` unconditionally, and that surfaced once
the libusb fix let the GUI boot far enough to reach IPC setup (#187 follow-on).
The GUI hosts no socket on Windows; it just runs in-process.
"""
from __future__ import annotations

from typing import cast

import pytest

import trcc.ipc as ipc_mod
from trcc.app import App


def test_start_skips_without_af_unix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate Windows: no socket.AF_UNIX.
    monkeypatch.delattr(ipc_mod.socket, "AF_UNIX", raising=False)

    # Deliberately no App: start() must give up before it reaches one.
    server = ipc_mod.IPCServer(app=cast(App, object()))
    server.start()              # must NOT raise — the Windows GUI relies on this
    assert server._sock is None  # nothing bound

    server.shutdown()           # safe even though start() never bound a socket
