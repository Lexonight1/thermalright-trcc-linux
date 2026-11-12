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

The default: every UI finds this process or starts it (``TRCC_DAEMON=0``
opts out).  No Qt event loop — the App is framework-blind, so the daemon is a
plain Python process.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import ipc

if TYPE_CHECKING:
    from .core.models import ModuleInfo
    from .core.ports import AppService, Platform, Renderer
    from .core.results import DaemonResult, DeviceStateResult

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
    ``DaemonUI.run`` and ``stop`` -- the IPC server, and ending it.

    ``App.close()`` used to be called here AND by the caller; the template now
    owns it exactly once, which is the double-teardown the GUI window had
    already had removed for the same reason.
    """
    log.info("run_daemon: delegating to the UI bus")
    _background_on_macos()
    from .ui._uis import DaemonUI
    return DaemonUI(renderer=renderer).start(platform)


def _background_on_macos() -> None:
    """A frozen Mac app started as the daemon should not take a Dock icon.

    The window and the daemon are the same ``TRCC`` binary.  Finder already
    shows the window.  This process is a second launch of that app, and
    Launch Services would give it a Dock tile with no window behind it.
    ``kProcessTransformToBackgroundApplication`` is 2; ``kCurrentProcess`` is
    the ``{0, 2}`` serial number.  A failure here still serves — the tile is
    the only thing lost.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        log.debug("_background_on_macos: not a frozen Mac app")
        return
    import ctypes

    log.info("_background_on_macos: keeping this process out of the Dock")
    try:
        app_services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

        class _ProcessSerialNumber(ctypes.Structure):
            # MacTypes.h: two UInt32s.  ``unsigned long`` is 8 bytes on
            # arm64, so the callee reads the low half as 0.  The current
            # process is ``{0, kCurrentProcess}``, not ``{0, 0}``.
            _fields_ = [("highLongOfPSN", ctypes.c_uint32),
                        ("lowLongOfPSN", ctypes.c_uint32)]

        transform = app_services.TransformProcessType
        transform.argtypes = [
            ctypes.POINTER(_ProcessSerialNumber), ctypes.c_uint32]
        transform.restype = ctypes.c_int32
        # {0, kCurrentProcess}.  The header's own example.
        psn = _ProcessSerialNumber(0, 2)
        err = transform(ctypes.byref(psn), 2)
    except OSError as exc:
        log.warning("_background_on_macos: %s", exc)
        return
    if err:
        log.warning("_background_on_macos: TransformProcessType returned %s", err)
    else:
        log.info("_background_on_macos: this process will not show in the Dock")


# Apple event class and ids.  ``oapp`` is this process's own launch.
# ``rapp`` is Finder opening the app again.  Measured 2026-11-12 on a
# windowed PyInstaller build: the daemon's first event is ``oapp``; a later
# ``open`` of the same bundle, while the daemon is still that app, is ``rapp``.
_AEVT = 0x61657674
_OAPP = 0x6F617070
_RAPP = 0x72617070
# Carbon: kEventClassAppleEvent / kEventAppleEvent.  CFRunLoop does not
# drain this queue — ReceiveNextEvent does.
_EPPC = 0x65707063
_APPLE_EVENT = 1
_EVENT_LOOP_TIMED_OUT = -9875  # eventLoopTimedOutErr
_EVENT_NOT_HANDLED = -1708  # errAEEventNotHandled
_REOPEN_GAP_S = 1.0

_AE_FRAMEWORK = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/AE.framework/AE")
_HI_FRAMEWORK = (
    "/System/Library/Frameworks/Carbon.framework/Frameworks/"
    "HIToolbox.framework/HIToolbox")


def _child_env() -> dict[str, str]:
    """Environment for a process this one starts.

    ``TRCC_DAEMON`` is stripped so the child cannot proxy to itself (#162).
    A frozen child also gets ``PYINSTALLER_RESET_ENVIRONMENT=1``: without it
    PyInstaller starts that child as a worker of this process, and the worker
    dies when this process does.  The daemon has to outlive the window.  The
    bootloader consumes the variable, so the child's Python never sees it.
    """
    from ._boot import _ENV_FLAG
    env = {k: v for k, v in os.environ.items() if k != _ENV_FLAG}
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        log.info("_child_env: frozen child is its own instance")
    else:
        log.debug("_child_env: not frozen")
    return env


def macos_reopen_pump() -> _MacReopen | None:
    """The pump that turns a later double-click into a window, or None.

    Finder will not start a second process of a windowed app while one
    process of that bundle is still the app.  After the window exits, the
    daemon is that process, so the double-click never arrives as a new
    ``TRCC gui`` — Launch Services hands it to this process and, with nobody
    answering, reports that the application is not open any more.  Only a
    frozen Mac build is that bundle.  A failure here still serves: the panel
    does not depend on the window being reopenable.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        log.debug("macos_reopen_pump: not a frozen Mac app")
        return None
    try:
        return _MacReopen()
    except Exception as exc:
        log.warning("macos_reopen_pump: %s: %s — a double-click cannot open "
                    "the window", type(exc).__name__, exc)
        return None


def _spawn_gui_from_daemon() -> None:
    """Start the window as its own process, attached to this daemon.

    Direct exec of ``gui``.  ``open`` of the bundle would come back here as
    another reopen.  The new process finds this daemon and attaches; it does
    not start a second one, and it does not blank the panel.
    """
    cmd = [sys.executable, "gui"]
    log.info("_spawn_gui_from_daemon: %s", " ".join(cmd))
    subprocess.Popen(
        cmd,
        env=_child_env(),
        cwd="/",
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_detached_flags(),
    )


class _MacReopen:
    """Main-thread pump for Finder's reopen of a frozen Mac app.

    One wait replaces the daemon loop's idle join.  A timeout is silence:
    this fires twice a second for the life of the process.  ``oapp`` is
    acknowledged and does not start a window — that event is our own launch,
    and the process that spawned us is already the window.  ``rapp`` starts
    one.
    """

    def __init__(self) -> None:
        import ctypes
        log.info("_MacReopen: installing a handler for Finder's reopen")
        self._deaf = False
        self._opened_at = 0.0
        self._ae = ctypes.CDLL(_AE_FRAMEWORK)
        self._hi = ctypes.CDLL(_HI_FRAMEWORK)
        handler = ctypes.CFUNCTYPE(
            ctypes.c_int16, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        # Kept on self: a callback that only a local holds is freed, and the
        # next event crashes the process that owns the panel.
        self._on_oapp = handler(self._acknowledge)
        self._on_rapp = handler(self._reopen)
        install = self._ae.AEInstallEventHandler
        install.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, handler, ctypes.c_void_p,
            ctypes.c_uint8]
        install.restype = ctypes.c_int16
        # AEProcessEvent is HIToolbox.  AE.framework does not export it, and
        # looking it up there kills this process before it serves.
        process = self._hi.AEProcessEvent
        process.argtypes = [ctypes.c_void_p]
        process.restype = ctypes.c_int32
        release = self._hi.ReleaseEvent
        release.argtypes = [ctypes.c_void_p]
        release.restype = ctypes.c_int32

        class _EventTypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32),
                        ("eventKind", ctypes.c_uint32)]

        # POINTER(this struct), not c_void_p: byref() of the spec matches the
        # former and is rejected by the latter.  The throwaway that received
        # the reopen used this exact type.
        receive = self._hi.ReceiveNextEvent
        receive.argtypes = [
            ctypes.c_ulong, ctypes.POINTER(_EventTypeSpec), ctypes.c_double,
            ctypes.c_uint8, ctypes.POINTER(ctypes.c_void_p)]
        receive.restype = ctypes.c_int32
        self._spec = _EventTypeSpec(_EPPC, _APPLE_EVENT)
        for event_id, callback in ((_OAPP, self._on_oapp), (_RAPP, self._on_rapp)):
            err = install(_AEVT, event_id, callback, None, 0)
            if err:
                raise OSError(f"AEInstallEventHandler returned {err}")
        log.info("_MacReopen: a double-click will open the window")

    def _acknowledge(self, _event: object, _reply: object, _refcon: object) -> int:
        log.info("_MacReopen._acknowledge: this process is already the app")
        return 0

    def _reopen(self, _event: object, _reply: object, _refcon: object) -> int:
        log.info("_MacReopen._reopen: Finder asked to open the window")
        try:
            self._open_window()
        except Exception as exc:
            log.warning("_MacReopen._reopen: %s: %s", type(exc).__name__, exc)
        return 0

    def _open_window(self) -> None:
        now = time.monotonic()
        if now - self._opened_at < _REOPEN_GAP_S:
            log.info("_MacReopen._open_window: a window was just started")
            return
        self._opened_at = now
        log.info("_MacReopen._open_window: starting the window")
        _spawn_gui_from_daemon()

    def wait(self, timeout: float) -> None:
        """Drain one Apple Event, or return when *timeout* seconds pass.

        Timeouts are not logged.  A broken pump is retired after the first
        failure so the daemon loop keeps serving the panel.
        """
        import ctypes
        # Retired after one failure.  Still wait out the interval: this is
        # the daemon loop's only pause, and returning at once would spin it.
        if self._deaf:
            time.sleep(timeout)
            return
        try:
            event = ctypes.c_void_p()
            status = int(self._hi.ReceiveNextEvent(
                1, ctypes.byref(self._spec), float(timeout), 1,
                ctypes.byref(event)))
            if status == _EVENT_LOOP_TIMED_OUT:
                return
            if status != 0 or not event.value:
                log.warning("_MacReopen.wait: ReceiveNextEvent returned %s",
                            status)
                return
            dispatched = int(self._hi.AEProcessEvent(event))
            self._hi.ReleaseEvent(event)
        except Exception as exc:
            self._deaf = True
            log.warning("_MacReopen.wait: stopped listening (%s: %s)",
                        type(exc).__name__, exc)
            return
        if dispatched == 0:
            log.debug("_MacReopen.wait: delivered one event")
        elif dispatched == _EVENT_NOT_HANDLED:
            log.debug("_MacReopen.wait: an event this process does not handle")
        else:
            log.warning("_MacReopen.wait: AEProcessEvent returned %s", dispatched)


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
#: How often a UI asks whether the App holding the lock has let go of it.
#: Not every socket poll: each ask takes the lock for an instant.
_LOCK_POLL_S = 0.25
#: How long a UI waits for an App that is starting -- the one it spawned, or
#: one another UI spawned -- while that App is still alive.  A cold first start
#: after an install took ~9 s on the win11 VM (2026-10-20: Defender scanning
#: fresh files), against the 10 s this used to be; the UI gave up, built an App
#: of its own, and the late App came up beside it -- two owners of one panel.
_START_TIMEOUT_S = 60.0
#: How often a UI checks that the App it spawned is still running.
_SPAWN_POLL_S = 0.25
#: After this long a still-starting App is worth one INFO line.
_SLOW_START_S = 10.0


def _wait_for_the_lock_holder(timeout: float) -> bool:
    """Wait for the App that holds the lock: True once it answers.  False
    when it lets go of the lock without ever answering -- it was EXITING, not
    starting -- or when *timeout* runs out.

    ``trcc kill`` returns once the old App's socket is gone, about a second
    before that App releases the lock on its way out.  The next UI used to
    read the held lock as "an App is starting", wait the whole *timeout* for
    an App that was leaving, and fall back to running in-process.
    """
    log.info("_wait_for_the_lock_holder: timeout=%s", timeout)
    deadline = time.monotonic() + timeout
    asked = 0.0
    while time.monotonic() < deadline:
        if ipc.daemon_running():
            return True
        if time.monotonic() - asked >= _LOCK_POLL_S:
            asked = time.monotonic()
            if not ipc.AppLock.held():
                log.info("_wait_for_the_lock_holder: it let go of the lock "
                         "without answering -- it was exiting")
                return False
        time.sleep(0.05)
    return False


def ensure_daemon(*, timeout: float = _START_TIMEOUT_S,
                  service: AppService | None = None) -> bool:
    """Make sure a current daemon is reachable, starting one if not.

    Started through this user's App *service* when it runs this install --
    the App's one owner, which outlives a logout -- else spawned.  A spawned
    App beside an installed service lived in the launching terminal's scope,
    and the service's own daemon, refused, restarted every 5 s (2026-11-04).

    A running daemon OLDER than this install -- or too old to say -- is
    replaced: stopped, and this install's started, whose session restores what
    the panels showed.  A NEWER one is kept (never downgrade) and warned
    about.  Returns True when a daemon is reachable; False when the App this
    UI spawned exited without answering, or was still not answering after
    *timeout* seconds -- and was then stopped, so the UI's in-process App is
    never a second one (:func:`_wait_for_the_spawn`).
    """
    if ipc.daemon_running():
        from .__version__ import __version__
        if is_this_process_the_daemon():
            # Reached from inside the daemon: asking our own socket would
            # block on the request we are serving.
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

    if ipc.AppLock.held():
        # An App holds the lock and has not answered: one STARTING -- a
        # second beside it would own the same panels (#314) -- or one EXITING.
        log.info("ensure_daemon: an App holds the lock — waiting for it")
        if _wait_for_the_lock_holder(timeout):
            return True
        if ipc.AppLock.held():
            log.warning("ensure_daemon: the App holding the lock did not "
                        "answer within %.0fs", timeout)
            return False
        log.info("ensure_daemon: that App was exiting — starting a new one")
    if service is not None and service.runs_this_install():
        if (answered := _start_the_service(service, timeout)) is not None:
            return answered
    cmd = _daemon_spawn_cmd()
    log.info("Spawning the shared App: %s", " ".join(cmd))
    # ``_child_env`` strips the daemon-mode flag so the child can't proxy to
    # itself (#162), and marks a frozen child as its own instance so it
    # outlives this window.
    child_env = _child_env()
    # cwd="/", as daemons do: otherwise the App inherits whatever directory the
    # FIRST UI was started from — holding it open (an unmountable USB stick)
    # and resolving anything relative against it for its whole life.
    spawned = subprocess.Popen(
        cmd,
        env=child_env,
        cwd="/",
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_detached_flags(),
    )
    return _wait_for_the_spawn(spawned, timeout)


def _start_the_service(service: AppService, timeout: float) -> bool | None:
    """Start the App through its service and wait for it to answer: True
    once it does.  None when the OS would not start it -- the caller spawns
    one instead.  Silent at *timeout*: stopped, as a late spawn is, then
    False, so the caller's in-process App is never a second one."""
    log.info("Starting the shared App through its service (%s)",
             type(service).__name__)
    if not service.start():
        log.warning("_start_the_service: the service did not start -- "
                    "spawning the App instead")
        return None
    started = time.monotonic()
    while (left := timeout - (time.monotonic() - started)) > 0:
        if ipc.wait_for_daemon(timeout=min(_SPAWN_POLL_S, left)):
            log.info("_start_the_service: the App answered after %.1f s",
                     time.monotonic() - started)
            return True
    if ipc.daemon_running():
        log.info("_start_the_service: the App answered at the deadline")
        return True
    log.warning("_start_the_service: the App did not answer within %.0f s -- "
                "stopping the service, so it cannot start beside an "
                "in-process App", timeout)
    service.stop()
    return False


def _wait_for_the_spawn(spawned: subprocess.Popen[bytes], timeout: float) -> bool:
    """Wait for the App this UI just started: True once it answers.

    Its process is the evidence a fixed wait lacked.  Exited without answering
    -> it failed: False now.  Alive -> still starting: keep waiting, up to
    *timeout*.  Alive and silent at *timeout* -> stopped, then False: the
    caller falls back to an in-process App, and the late one must not come up
    beside it (it did on the win11 VM, 2026-10-20).
    """
    log.info("_wait_for_the_spawn: pid %s, up to %.0f s", spawned.pid, timeout)
    started = time.monotonic()
    said_slow = False
    while (left := timeout - (time.monotonic() - started)) > 0:
        if ipc.wait_for_daemon(timeout=min(_SPAWN_POLL_S, left)):
            log.info("_wait_for_the_spawn: the App answered after %.1f s",
                     time.monotonic() - started)
            return True
        if (code := spawned.poll()) is not None:
            log.warning("_wait_for_the_spawn: the App exited with code %s before "
                        "answering", code)
            return False
        if not said_slow and time.monotonic() - started >= _SLOW_START_S:
            said_slow = True
            log.info("_wait_for_the_spawn: the App is still starting after %.0f s "
                     "-- waiting (a first start after an install can be slow)",
                     _SLOW_START_S)
    if ipc.daemon_running():
        log.info("_wait_for_the_spawn: the App answered at the deadline")
        return True
    log.warning("_wait_for_the_spawn: the App did not answer within %.0f s -- "
                "stopping it, so it cannot start beside an in-process App", timeout)
    spawned.kill()
    try:
        spawned.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        log.warning("_wait_for_the_spawn: pid %s did not exit after kill", spawned.pid)
    return False


def _detached_flags() -> int:
    """Windows' ``start_new_session``: no console window of its own, and out
    of the launching console's process group -- so closing that terminal or
    pressing Ctrl+C in it does not stop the App.  0 elsewhere."""
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log.debug("_detached_flags: 0x%x", flags)
    return flags


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


def running_status() -> DaemonResult | None:
    """The running App's OWN ``DaemonStatus`` -- executed there, over its socket.

    Its pid, uptime and version are facts only that process knows.  None when
    it does not answer.  Straight through ``AppProxy``, never a UI's
    ``dispatch``: ``DaemonStatus`` runs in the caller, and this is how the
    caller gets the App's side.
    """
    from .core.commands import DaemonStatus
    from .proxy import AppProxy
    proxy = AppProxy(timeout=5.0)
    try:
        status = proxy.dispatch(DaemonStatus(here=True))
    except Exception as e:
        log.warning("running_status: the running App did not answer (%s: %s)",
                    type(e).__name__, e)
        status = None
    finally:
        proxy.close()
    log.info("running_status: %s", status)
    return status


def held_devices() -> list[DeviceStateResult] | None:
    """The devices the running App reports, each with its live state.

    Over its socket, like :func:`running_status`: ``trcc report`` runs in the
    caller, and a panel the App holds must be described by the App rather
    than probed a second time.  None when the App does not answer.
    """
    from .core.commands import DeviceState, ListDevices
    from .proxy import AppProxy
    proxy = AppProxy(timeout=5.0)
    try:
        listing = proxy.dispatch(ListDevices())
        states: list[DeviceStateResult] | None = [
            proxy.dispatch(DeviceState(key=entry.key))
            for entry in listing.devices]
    except Exception as e:
        log.warning("held_devices: the running App did not answer (%s: %s)",
                    type(e).__name__, e)
        states = None
    finally:
        proxy.close()
    log.info("held_devices: %s",
             None if states is None else [s.key for s in states])
    return states


def running_modules() -> tuple[ModuleInfo, ...] | None:
    """Every module the running App has, on or off -- over its socket, like
    :func:`held_devices`: which are on is the App's to say, never this
    process's reading of the disk.  None when it does not answer."""
    from .core.commands import ListModules
    from .proxy import AppProxy
    proxy = AppProxy(timeout=5.0)
    try:
        modules: tuple[ModuleInfo, ...] | None = proxy.dispatch(ListModules()).modules
    except Exception as e:
        log.warning("running_modules: the running App did not answer (%s: %s)",
                    type(e).__name__, e)
        modules = None
    finally:
        proxy.close()
    log.info("running_modules: %s",
             None if modules is None else {m.key: m.enabled for m in modules})
    return modules


def _running_version() -> str:
    """The running daemon's version; ``""`` when it cannot say -- a daemon
    older than ``DaemonResult.version`` sends no such field."""
    status = running_status()
    version = status.version if status is not None else ""
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
    and with a version check, replace it forever.  A frozen build has no
    ``-m``.  On Windows the windowed twin is ``trcc-gui.exe``.  On macOS the
    one ``TRCC`` binary is the App, started as ``TRCC daemon``.
    """
    log.debug("_daemon_spawn_cmd: frozen=%s", getattr(sys, "frozen", False))
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "trcc", "daemon"]
    # A frozen build has no ``-m``.  On Windows the windowed twin is
    # ``trcc-gui.exe`` beside the console exe: a console exe would open a
    # terminal for the App's whole life.  Everywhere else — the Mac app is
    # one binary named ``TRCC`` — this executable is the App, and ``daemon``
    # is a real subcommand.  ``which("trcc")`` can be a different install,
    # and ``-m`` is not a Python flag once the file is frozen.
    windowed = Path(sys.executable).with_name("trcc-gui.exe")
    if sys.platform == "win32" and windowed.is_file():
        return [str(windowed), "daemon"]
    log.info("_daemon_spawn_cmd: frozen %s -> %s daemon",
             sys.platform, sys.executable)
    return [sys.executable, "daemon"]
