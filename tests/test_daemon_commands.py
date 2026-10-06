"""Daemon lifecycle on the Command bus.

The daemon is this application's own software, not an OS facility, so its
lifecycle belongs on the bus like every other capability.  Before these
existed, ``cli`` and ``api`` imported ``daemon.kill_daemon`` /
``ipc.daemon_running`` directly and the two GUIs could not ask at all — so
"is a daemon running?" was a question only half the UIs could pose, about the
one process all four depend on.

The shape a UI wants at boot is *is the daemon up?  no — create it; yes —
talk to it*, and it has to be idempotent so every UI can dispatch it
unconditionally instead of probing first and racing its own spawn.
"""
from __future__ import annotations

import time

from trcc.app import App
from trcc.core.commands import DaemonStatus, StopDaemon


def test_status_reports_no_daemon_on_a_clean_runtime_dir(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    result = App(fake_platform).dispatch(DaemonStatus())

    assert result.ok            # answering "no" is a success, not a failure
    assert not result.running
    assert result.socket_path.startswith(str(tmp_path))


def test_status_is_a_read_and_never_spawns(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """``DaemonStatus`` is a Query — asking must not start anything."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    app = App(fake_platform)
    app.dispatch(DaemonStatus())

    assert not (tmp_path / "trcc.sock").exists()


def test_stopping_a_daemon_that_is_not_running_succeeds(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """The caller's intent — "there should be no daemon" — is already met.

    Returning ok=False here would make every UI's shutdown path log an error
    on the common case of a daemon that was never started.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    result = App(fake_platform).dispatch(StopDaemon(timeout=2.0))

    assert result.ok
    assert not result.running


def test_finding_a_running_app_spawns_nothing(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """``ensure_daemon`` against an App already serving the socket: found,
    nothing started.  Served here by a process NOT marked as the daemon -- the
    shape that made ``DaemonStatus`` ask itself back until the timeout, then
    "replace" the App and spawn a real one (fixed by ``here=True``)."""
    import threading
    from unittest import mock

    from trcc import daemon
    from trcc.ipc import IPCServer

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    srv = IPCServer(App(fake_platform))
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with mock.patch.object(daemon.subprocess, "Popen") as popen:
            started = time.monotonic()
            assert daemon.ensure_daemon(timeout=5.0)
            elapsed = time.monotonic() - started
        popen.assert_not_called()
        assert elapsed < 4.0, f"took {elapsed:.1f}s — it asked itself back"
    finally:
        srv.shutdown()


def test_every_ui_can_reach_the_daemon_lifecycle(fake_platform) -> None:
    """The point of moving it onto the bus.

    ``cli`` imported ``kill_daemon`` and ``daemon_running``; ``api`` imported
    ``kill_daemon``; neither GUI could ask at all.
    """
    import ast
    import pathlib

    for name in ("DaemonStatus", "StopDaemon"):
        assert hasattr(
            __import__("trcc.core.commands", fromlist=[name]), name,
        ), f"{name} is not exported from the Command package"

    # And the two UIs that used to import the functions now dispatch instead.
    for rel in ("ui/cli/main.py", "ui/api/trcc.py"):
        src = pathlib.Path("src/trcc", rel).read_text(encoding="utf-8")
        imported = {
            alias.name
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "kill_daemon" not in imported, (
            f"{rel} still imports kill_daemon instead of dispatching StopDaemon"
        )
        assert "daemon_running" not in imported, (
            f"{rel} still imports daemon_running instead of DaemonStatus"
        )


def test_status_never_reports_the_callers_pid_as_the_daemons(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """"A daemon is running" and "I am it" are different facts.

    ``GET /trcc/status`` used to answer ``os.getpid()`` and the private
    ``daemon._started_at``: this process's facts, presented as the daemon's.
    Correct only while the API ran INSIDE the daemon — as a client it named
    the wrong process for an ops script to signal, and a permanent uptime of
    zero.  Zero here means "ask the daemon", and dispatching over the socket
    does exactly that because the Command then executes there.
    """
    import os
    import threading

    import trcc.daemon as daemon_mod
    from trcc.ipc import IPCServer

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    # ``_started_at`` is process-global: once any test runs the daemon
    # in-process this worker is "the daemon" for good.  Pin it so this test
    # measures the CLIENT case it is about.
    monkeypatch.setattr(daemon_mod, "_started_at", None)
    app = App(fake_platform)
    srv = IPCServer(app)
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        result = app.dispatch(DaemonStatus())
        # A socket is reachable, but THIS process never called run_daemon.
        assert result.running
        assert result.pid == 0, "reported the caller's pid as the daemon's"
        assert result.pid != os.getpid()
        assert result.uptime_seconds == 0
    finally:
        srv.shutdown()


def test_the_api_no_longer_reads_daemon_privates(fake_platform) -> None:
    """``ui/api/trcc.py`` imported ``daemon._started_at`` — a private global.

    It was the only cross-layer private import in the whole ``ui/`` tree.
    """
    import ast
    import pathlib

    src = pathlib.Path("src/trcc/ui/api/trcc.py").read_text(encoding="utf-8")
    private = {
        alias.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name.startswith("_") and not alias.name.startswith("__")
    }
    assert not private, f"ui/api/trcc.py imports privates: {sorted(private)}"


def test_uptime_is_zero_rather_than_invented_off_the_daemon(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """A process that is not the daemon does not know how long it has been up."""
    import trcc.daemon as daemon_mod

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    # ``_started_at`` is process-global and another test in this worker may
    # have run the daemon in-process; pin it rather than inherit whatever ran
    # first.
    monkeypatch.setattr(daemon_mod, "_started_at", None)
    assert not daemon_mod.is_this_process_the_daemon()
    assert daemon_mod.uptime_s() == 0

    monkeypatch.setattr(daemon_mod, "_started_at", time.monotonic() - 42)
    assert daemon_mod.is_this_process_the_daemon()
    assert daemon_mod.uptime_s() >= 42


# =========================================================================
# RUNS_IN_CALLER — never find or START the shared App to run these
# =========================================================================
#
# Measured before this existed, with the shared App the default: through a UI,
# ``daemon-status`` STARTED an App to answer "running", ``kill`` did the same
# to stop it, ``sudo`` (setup / upgrade) could not prompt inside the detached
# App, and ``report`` failed after 37.6 s against a hung one.  The tests above
# dispatch on an in-process App directly, so none of them could see it.


def _shared_by_default(monkeypatch, fake_platform, tmp_path) -> list[str]:
    """The production default (no TRCC_DAEMON, not root), a recorder on the one
    function that starts an App, and the in-process App built on FakePlatform."""
    import os

    from trcc import _boot, daemon

    monkeypatch.delenv("TRCC_DAEMON", raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    started: list[str] = []
    monkeypatch.setattr(daemon, "ensure_daemon",
                        lambda **kw: started.append("ensure_daemon") or False)
    monkeypatch.setattr(_boot, "_build_local_app",
                        lambda **kw: App(fake_platform))
    return started


def test_status_and_kill_through_a_ui_never_start_an_app(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    from trcc.core.commands import ListDevices
    from trcc.ui._uis import CliUI

    started = _shared_by_default(monkeypatch, fake_platform, tmp_path)
    ui = CliUI()

    status = ui.dispatch(DaemonStatus())
    stopped = ui.dispatch(StopDaemon(timeout=1.0))

    assert started == [], "asking about the App started one"
    assert status.message == "No daemon is running"
    assert stopped.message == "No TRCC App was running"
    # The control: an ordinary Command through the SAME face does reach for
    # the shared App -- so an empty recorder above is a fact, not a blind spot.
    ui.dispatch(ListDevices())
    assert started == ["ensure_daemon"]


def test_the_caller_side_app_builds_no_renderer(
    fake_platform, tmp_path, monkeypatch,
) -> None:
    """``trcc kill`` and ``daemon-status`` build an App only to dispatch on it,
    and none of the caller-side Commands draws.  A QtRenderer plus its display
    wiring was ~70 ms of every one (349 -> 280 ms measured, 2026-10-06)."""
    from trcc import _boot
    from trcc.adapters.render import qt
    from trcc.ui._uis import CliUI

    real_build = _boot._build_local_app      # before the helper stubs it
    _shared_by_default(monkeypatch, fake_platform, tmp_path)
    renderers: list[int] = []
    real_qt = qt.QtRenderer

    def counting_renderer() -> object:
        renderers.append(1)
        return real_qt()

    def build_on_the_fake(**kw: object) -> App:
        return real_build(**{**kw, "platform": fake_platform})  # type: ignore[arg-type]

    monkeypatch.setattr(qt, "QtRenderer", counting_renderer)
    monkeypatch.setattr(_boot, "_build_local_app", build_on_the_fake)

    result = CliUI().dispatch(DaemonStatus())

    assert result.message == "No daemon is running"
    assert renderers == [], "the caller-side App built a QtRenderer"


def test_stop_says_it_stopped_the_app(fake_platform, monkeypatch) -> None:
    from trcc import daemon, ipc

    monkeypatch.setattr(ipc, "daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "kill_daemon", lambda **kw: True)

    assert App(fake_platform).dispatch(StopDaemon()).message == "TRCC App stopped"


def test_status_from_the_caller_carries_the_apps_own_answer(
    fake_platform, monkeypatch,
) -> None:
    """Its pid, uptime and version are facts only the App knows; the caller
    must not report its own in their place."""
    from trcc import daemon, ipc
    from trcc.core.results import DaemonResult

    monkeypatch.setattr(ipc, "daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "is_this_process_the_daemon", lambda: False)
    monkeypatch.setattr(daemon, "running_status", lambda: DaemonResult(
        ok=True, running=True, pid=4242, uptime_seconds=77, version="9.9.9"))

    result = App(fake_platform).dispatch(DaemonStatus())

    assert (result.pid, result.uptime_seconds, result.version) == (4242, 77, "9.9.9")


def test_exactly_these_commands_run_in_the_caller() -> None:
    """A new one is a decision to review, not an accident to discover."""
    import trcc.core.commands as C

    marked = {name for name in dir(C)
              if getattr(getattr(C, name), "RUNS_IN_CALLER", False) is True}

    assert marked == {"StopDaemon", "DaemonStatus", "RunSetup", "RunUpgrade",
                      "GenerateDebugReport"}



def test_provide_api_tls_makes_the_pair_once_in_the_config_directory(
    fake_platform,
) -> None:
    """Made once: a client pins the fingerprint, so a restart must not change it."""
    from pathlib import Path

    from trcc.core.commands import ProvideApiTls

    app = App(fake_platform)
    first = app.dispatch(ProvideApiTls(bind_host="0.0.0.0"))
    second = app.dispatch(ProvideApiTls(bind_host="0.0.0.0"))

    tls_dir = fake_platform.paths().config_dir() / "tls"
    assert Path(first.cert).parent == tls_dir and Path(first.key).parent == tls_dir
    assert second.fingerprint == first.fingerprint


def test_provide_api_tls_serves_the_users_own_pair(fake_platform, tmp_path) -> None:
    """Their files, at THEIR paths — never the self-signed pair in their place."""
    import shutil
    from pathlib import Path

    from trcc.core.commands import ProvideApiTls

    app = App(fake_platform)
    made = app.dispatch(ProvideApiTls())
    cert, key = tmp_path / "mine.crt", tmp_path / "mine.key"
    shutil.copy(made.cert, cert)
    shutil.copy(made.key, key)

    theirs = app.dispatch(ProvideApiTls(cert=cert, key=key))

    assert (Path(theirs.cert), Path(theirs.key)) == (cert, key)
    assert theirs.fingerprint == made.fingerprint
