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
    (None, True, 0, True),        # a root login / root service is a user too
])
def test_the_factory_uses_the_shared_app_unless_a_reason_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
    env: str | None, af_unix: bool, euid: int, shared: bool,
) -> None:
    """A root login (Proxmox, a headless Pi) or a root service shares one App
    like any user (#150 #246 #267); ELEVATED root is the next test."""
    import socket

    from trcc import _boot
    from trcc.proxy import AppProxy

    for var in _boot._ELEVATED_BY:
        monkeypatch.delenv(var, raising=False)

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


@pytest.mark.parametrize("var, value", [
    ("SUDO_UID", "1000"),         # sudo, and run0 (systemd 256+)
    ("PKEXEC_UID", "1000"),
    ("DOAS_USER", "alice"),
])
def test_root_raised_from_a_user_stays_in_process(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str,
) -> None:
    """Every install guide runs ``sudo trcc system setup``: a shared root App
    started there would outlive it, holding USB, beside the user's own."""
    from trcc import _boot

    monkeypatch.delenv(_ENV_FLAG, raising=False)
    for other in _boot._ELEVATED_BY:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(var, value)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    started: list[bool] = []
    monkeypatch.setattr(daemon, "ensure_daemon",
                        lambda **kw: started.append(True) or True)
    monkeypatch.setattr(_boot, "_build_local_app", lambda **kw: "LOCAL")

    assert _boot._local_reason() == (
        f"elevated by {var} — the shared App is the invoking user's")
    assert _boot.trcc() == "LOCAL"
    assert started == []


def test_a_user_with_a_stale_sudo_variable_is_not_elevated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ROOT can be elevated: a variable left in a user's shell means
    nothing, and must not keep them off their own App."""
    from trcc import _boot

    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    assert _boot._elevated() is None


def test_the_gui_s_real_platform_still_reaches_the_shared_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``trcc gui`` passes ``current_platform()`` (``ui/gui/__init__.py``), so
    an injected platform is NOT a reason to build locally.

    A 2026-10-02 change made it one, on the belief that production passes
    None.  Driven for real, the gui then built its own App on the host's USB,
    attached the panel the running daemon was already driving, and blanked it
    on exit.  Reverted; this pins the contract -- with the object the gui
    really passes, which this test stood in for with ``object()`` until a
    stand-in became a local reason of its own (2026-10-05).

    MUTATION CHECK: re-add ``"a platform was injected" if platform is not
    None`` to ``_local_reason`` and this fails.
    """
    from trcc import _boot
    from trcc.adapters.system import current_platform
    from trcc.proxy import AppProxy

    monkeypatch.delenv(_ENV_FLAG, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(daemon, "ensure_daemon", lambda **kw: True)
    monkeypatch.setattr(_boot, "_build_local_app", lambda **kw: "LOCAL")

    assert isinstance(_boot.trcc(platform=current_platform()), AppProxy)


def _host_subclass() -> object:
    """A keyless subclass of this host's class -- the shape of the dev mock's
    ``DevPlatform`` / ``DevMockPlatform``: an INSTANCE of the host class, so
    ``isinstance`` would wave it through to the shared App."""
    from trcc.adapters.system import host_platform_class

    return object.__new__(type("_DevLike", (host_platform_class(),), {}))


@pytest.mark.parametrize("stand_in", ["fake", "host subclass"])
def test_a_stand_in_platform_never_reaches_the_shared_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stand_in: str,
) -> None:
    """The shared App runs on this host's own platform and IGNORES the one it
    is given, so a mock handed to ``trcc()`` used to be dropped silently --
    leaving the mock harnesses local only because importing ``tests.conftest``
    sets TRCC_DAEMON=0, and with ``export TRCC_DAEMON=1`` (which the CLI
    tells users to run) ``--hardware`` reached the user's running App.
    """
    from trcc import _boot

    from .conftest import FakePlatform

    platform = FakePlatform(tmp_path) if stand_in == "fake" else _host_subclass()
    monkeypatch.setenv(_ENV_FLAG, "1")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    started: list[str] = []
    monkeypatch.setattr(daemon, "ensure_daemon",
                        lambda **kw: started.append("shared") or True)
    monkeypatch.setattr(_boot, "_build_local_app", lambda **kw: "LOCAL")

    assert _boot.trcc(platform=platform) == "LOCAL"  # type: ignore[arg-type]
    assert started == []


@pytest.mark.parametrize("sys_platform", ["freebsd14", "openbsd7", "netbsd10"])
def test_every_bsd_host_reaches_the_shared_app(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str,
) -> None:
    """Each BSD resolves to an UNREGISTERED child of ``BsdOS``, so "is the
    platform's class registered" would send the real gui local on every BSD
    -- 2026-10-02 again.  The rule is "is it exactly this host's class".
    """
    import sys

    from trcc import _boot
    from trcc.adapters.system import host_platform_class

    monkeypatch.setattr(sys, "platform", sys_platform)
    monkeypatch.delenv(_ENV_FLAG, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    host = object.__new__(host_platform_class())

    assert _boot._local_reason(host) is None  # type: ignore[arg-type]


def test_a_stand_in_face_runs_caller_commands_on_its_own_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """``_caller_app`` asks the same question: a face on a stand-in already
    holds a local App, and must not build a second one beside it."""
    from trcc import _boot
    from trcc.app import App
    from trcc.core.commands import DaemonStatus, ListDevices
    from trcc.ui._uis import CliUI

    from .conftest import FakePlatform

    platform = FakePlatform(tmp_path)
    monkeypatch.delenv(_ENV_FLAG, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(daemon, "ensure_daemon", lambda **kw: True)
    built: list[str] = []
    monkeypatch.setattr(_boot, "_build_local_app",
                        lambda **kw: built.append("app") or App(platform))
    ui = CliUI()
    ui._platform = platform
    ui.dispatch(ListDevices())           # the face's own App: local, on it
    assert built == ["app"]

    ui.dispatch(DaemonStatus())          # a caller-side Command

    assert built == ["app"], "a second in-process App was built beside it"


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


# ── One App per user, answering before its coldplug finishes (#314) ──────
#
# The App bound its socket only AFTER bring_up -- the device scan, every
# handshake, the video prime -- while each UI waited 10 s for it and then
# built an App of its own: two owners of one panel.  A first connect of a
# firmware-4.07 HID panel spends 8 s in its probe alone.  Two UIs starting
# together both spawned, and the second App unlinked the first one's socket.


def test_the_app_answers_before_its_coldplug_finishes(
    monkeypatch: pytest.MonkeyPatch, fake_platform,
) -> None:
    """MUTATION CHECK: bind in ``run`` again (after bring_up) -> fails."""
    import threading
    import time

    from trcc.app import App
    from trcc.ui._uis import DaemonUI

    coldplug, finish = threading.Event(), threading.Event()

    def slow_session(self) -> None:
        coldplug.set()
        finish.wait(10)

    monkeypatch.setattr(App, "start_session", slow_session)
    ui = DaemonUI()
    worker = threading.Thread(target=ui.start, args=(fake_platform,), daemon=True)
    worker.start()
    try:
        assert coldplug.wait(5), "bring_up never started"
        deadline = time.monotonic() + 3
        while not ipc.daemon_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ipc.daemon_running(), "no socket while the coldplug ran"
    finally:
        finish.set()
        deadline = time.monotonic() + 5
        while ui._server is None and time.monotonic() < deadline:
            time.sleep(0.05)
        ui.stop()
        worker.join(10)
    assert not worker.is_alive()


def test_a_second_app_refuses_while_one_holds_the_lock() -> None:
    """MUTATION CHECK: skip the lock in preflight -> a second App starts."""
    from trcc.ui._uis import DaemonUI

    holder = ipc.AppLock()
    assert holder.acquire()
    try:
        assert DaemonUI().preflight() == 1
    finally:
        holder.release()


def test_no_ui_spawns_an_app_while_one_is_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock held, socket not yet bound: that App is STARTING -- wait for it."""
    holder = ipc.AppLock()
    assert holder.acquire()

    def no_spawn(*a, **k):
        raise AssertionError("spawned a second App beside a starting one")

    monkeypatch.setattr(daemon.subprocess, "Popen", no_spawn)
    try:
        assert daemon.ensure_daemon(timeout=0.3) is False
    finally:
        holder.release()


def test_a_server_never_removes_a_socket_it_does_not_own(fake_platform) -> None:
    """An orphaned App's shutdown deleted the LIVE App's socket file, so the
    next UI found nothing and started a third."""
    import socket as _socket

    from trcc.app import App

    first = ipc.IPCServer(App(fake_platform))
    first.start()
    path = ipc.socket_path()
    path.unlink()                                 # a newer App took the path
    other = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    other.bind(str(path))
    try:
        first.shutdown()
        assert path.exists(), "shutdown removed another server's socket"
    finally:
        other.close()
