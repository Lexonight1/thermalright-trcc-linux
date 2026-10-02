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
    """#148: the daemon must bring its session up (else it owns USB but never
    ticks → blank display).  #162: it pops the daemon-mode flag from its own
    env so it can't proxy to itself.

    The daemon reaches the metrics loop through ``App.start_session`` now —
    the partner of ``close`` — so this asserts the daemon CALLS it, and
    ``tests/test_app_start_session.py`` asserts that the call actually starts
    all three loops.  Splitting it that way is what let the daemon gain the
    coldplug it never had: on Windows / macOS / BSD, whose hotplug monitors
    report only NEW devices, it used to come up owning USB with nothing
    connected."""
    monkeypatch.setenv(_ENV_FLAG, "1")
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)

    app = mock.MagicMock()
    monkeypatch.setattr(
        "trcc._boot._build_local_app",
        lambda *, platform=None, renderer=None: app,
    )
    server = mock.MagicMock()
    monkeypatch.setattr(ipc, "IPCServer", lambda a: server)

    rc = daemon.run_daemon()

    assert rc == 0
    app.start_session.assert_called_once()        # #148 — coldplug + 3 loops
    app.close.assert_called_once()                # teardown (its exact partner)
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

    sentinel_platform = mock.MagicMock()
    sentinel_renderer = mock.MagicMock()
    rc = daemon.run_daemon(platform=sentinel_platform, renderer=sentinel_renderer)

    assert rc == 0
    assert captured["platform"] is sentinel_platform
    assert captured["renderer"] is sentinel_renderer


# ── A UI replaces an older running App (V3) ───────────────────────────────


@pytest.mark.parametrize(("theirs", "replaced"), [
    ("9.0.0", True),        # older: replaced
    ("", True),             # too old to say (no ``version`` field): replaced
    ("99.0.0", False),      # newer: kept -- never downgrade
    (None, False),          # the same version: nothing to do
])
def test_an_older_running_app_is_replaced(
    monkeypatch: pytest.MonkeyPatch, theirs: str | None, replaced: bool,
) -> None:
    """A UI that found an older App talked to stale code after an upgrade.

    MUTATION CHECK: make ``_older`` always False.
    """
    from trcc.__version__ import __version__

    running = {"up": True}
    killed: list[float] = []
    monkeypatch.setattr(ipc, "daemon_running", lambda: running["up"])
    monkeypatch.setattr(ipc, "wait_for_daemon", lambda timeout: True)
    monkeypatch.setattr(daemon, "is_this_process_the_daemon", lambda: False)
    monkeypatch.setattr(daemon, "_running_version",
                        lambda: __version__ if theirs is None else theirs)

    def kill(*, timeout: float) -> bool:
        killed.append(timeout)
        running["up"] = False
        return True
    monkeypatch.setattr(daemon, "kill_daemon", kill)

    with mock.patch.object(daemon.subprocess, "Popen") as popen:
        assert daemon.ensure_daemon(timeout=0.1)

    assert bool(killed) is replaced
    assert popen.called is replaced


def test_the_daemon_never_asks_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything inside the daemon that reaches ``ensure_daemon`` must not ask
    its own socket: it would block on the request it is serving, time out,
    read as "too old to say" -- and the daemon would stop itself.

    MUTATION CHECK: drop the ``is_this_process_the_daemon`` guard.
    """
    monkeypatch.setattr(ipc, "daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "is_this_process_the_daemon", lambda: True)

    def must_not_ask() -> str:
        raise AssertionError("the daemon asked itself its version")
    monkeypatch.setattr(daemon, "_running_version", must_not_ask)
    monkeypatch.setattr(daemon, "kill_daemon",
                        lambda **_: pytest.fail("the daemon stopped itself"))

    assert daemon.ensure_daemon(timeout=0.1)


def test_a_ui_starts_a_daemon_of_its_own_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the first ``trcc`` on PATH: with two installs, a UI could start the
    OTHER one's daemon -- and with a version check, replace it forever.

    MUTATION CHECK: prefer ``which("trcc")`` again.
    """
    import sys
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/trcc")

    assert daemon._daemon_spawn_cmd() == [sys.executable, "-m", "trcc", "daemon"]


# =========================================================================
# The factory: the shared App by default, in-process only for a reason
# =========================================================================


@pytest.mark.parametrize(("env", "af_unix", "euid", "shared"), [
    (None, True, 1000, True),     # the default — every UI is a remote control
    ("1", True, 1000, True),
    ("0", True, 1000, False),     # tests, dev mocks, profilers
    (None, False, 1000, False),   # CPython on Windows has no AF_UNIX
    (None, True, 0, False),       # root: the shared App lives in userland
])
def test_the_factory_uses_the_shared_app_unless_a_reason_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
    env: str | None, af_unix: bool, euid: int, shared: bool,
) -> None:
    """Root matters most: every install guide runs ``sudo trcc system setup``,
    which dispatches — a shared root App would outlive it, holding USB."""
    import socket

    from trcc import _boot
    from trcc.proxy import AppProxy

    if env is None:
        monkeypatch.delenv(_ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(_ENV_FLAG, env)
    if not af_unix:
        monkeypatch.delattr(socket, "AF_UNIX")
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    started: list[bool] = []
    monkeypatch.setattr(daemon, "ensure_daemon",
                        lambda **kw: started.append(True) or True)
    monkeypatch.setattr(_boot, "_build_local_app", lambda **kw: "LOCAL")

    app = _boot.trcc()

    assert isinstance(app, AppProxy) is shared
    assert bool(started) is shared, "a local App must not start a daemon"


def test_a_shared_app_that_will_not_start_falls_back_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc import _boot

    monkeypatch.delenv(_ENV_FLAG, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(daemon, "ensure_daemon", lambda **kw: False)
    monkeypatch.setattr(_boot, "_build_local_app", lambda **kw: "LOCAL")

    assert _boot.trcc() == "LOCAL"


def test_the_app_starts_in_root_not_in_the_first_uis_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Inherited, the App would hold whatever directory the first UI ran from
    for its whole life — and resolve anything relative against it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)
    monkeypatch.setattr(ipc, "wait_for_daemon", lambda timeout: True)

    with mock.patch.object(daemon.subprocess, "Popen") as popen:
        daemon.ensure_daemon(timeout=0.1)

    assert popen.call_args.kwargs["cwd"] == "/"
