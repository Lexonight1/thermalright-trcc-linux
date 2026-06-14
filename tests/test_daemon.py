"""Daemon lifecycle — fork-bomb guard (#162) + metrics-loop start (#148)."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from trcc import daemon, ipc
from trcc._boot import _ENV_FLAG


def test_ensure_daemon_strips_daemon_flag_from_child_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#162: the spawned daemon must NOT inherit ``TRCC_DAEMON`` — if it
    did, its own ``trcc()`` would try to proxy to a socket that isn't
    bound yet and re-spawn, a fork bomb."""
    monkeypatch.setenv(_ENV_FLAG, "1")
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)
    monkeypatch.setattr(ipc, "wait_for_daemon", lambda timeout: True)

    with mock.patch.object(daemon.subprocess, "Popen") as popen:
        daemon.ensure_daemon(timeout=0.1)

    env = popen.call_args.kwargs["env"]
    assert _ENV_FLAG not in env          # the flag is stripped from the child
    assert "PATH" in env                 # the rest of the environment survives


def test_run_daemon_starts_metrics_loop_and_pops_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#148: the daemon must start the metrics loop (else it owns USB but
    never ticks → blank display).  #162: it pops the daemon-mode flag from
    its own env so it can't proxy to itself."""
    monkeypatch.setenv(_ENV_FLAG, "1")
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)

    app = mock.MagicMock()
    monkeypatch.setattr(
        "trcc._boot._build_local_app",
        lambda *, platform=None, renderer=None: app,
    )
    server = mock.MagicMock()
    monkeypatch.setattr(ipc, "IPCServer", lambda a: server)
    monkeypatch.setattr(daemon, "_install_signal_handlers", lambda s: None)

    rc = daemon.run_daemon()

    assert rc == 0
    app.metrics_loop.start.assert_called_once()   # #148
    app.close.assert_called_once()                # teardown (stops the loop)
    assert _ENV_FLAG not in os.environ            # #162 — flag popped


def test_run_daemon_injects_platform_and_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DI seam: an injected ``platform`` / ``renderer`` flows straight to
    ``_build_local_app`` (so ``dev/_mock_daemon`` runs the real daemon entry
    against a scripted Platform instead of hand-rolling bring-up)."""
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)
    captured: dict[str, object] = {}
    app = mock.MagicMock()

    def _capture(*, platform=None, renderer=None):
        captured["platform"] = platform
        captured["renderer"] = renderer
        return app

    monkeypatch.setattr("trcc._boot._build_local_app", _capture)
    monkeypatch.setattr(ipc, "IPCServer", lambda a: mock.MagicMock())
    monkeypatch.setattr(daemon, "_install_signal_handlers", lambda s: None)

    sentinel_platform = mock.MagicMock()
    sentinel_renderer = mock.MagicMock()
    rc = daemon.run_daemon(platform=sentinel_platform, renderer=sentinel_renderer)

    assert rc == 0
    assert captured["platform"] is sentinel_platform
    assert captured["renderer"] is sentinel_renderer
