"""The concrete UI faces — deliberately LIGHT.

Importing this module is what populates :data:`~trcc.ui._base.UIS`, the same
way importing ``adapters.device`` populates ``DEVICES``.  That only stays
affordable because **every heavy import in this file lives inside a method
body**: measured 2026-09-08, ``import trcc.daemon`` costs 153 ms,
``trcc.ipc`` 198 ms and ``trcc.ui.gui`` 247 ms, so hoisting any of them to
module scope would put that on the critical path of every ``trcc`` invocation.
As written, the marginal cost of the bus in the context that actually runs it —
a CLI process that has already imported ``ui.cli.main`` — is **0.13 ms**, and
it pulls exactly one new module.

Two rules follow, and ``tests/test_ui_bus.py`` gates both:

* heavy imports stay in method bodies;
* these classes do NOT live under ``ui/gui/`` or ``ui/qtgui/``, because
  importing *any* submodule of those packages runs their ``__init__`` --
  ``import trcc.ui.gui.assets`` costs 249 ms and loads 17 PySide6 modules.

The CLI is absent on purpose.  It is the **router**, not a leaf face: it
composes lazily per subcommand (``_ctx.get_app()``), so putting it through
``compose() -> run(app)`` would build an App it discards — and under
``TRCC_DAEMON=1`` would spawn a daemon that a ``trcc --help`` never needed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ._base import UserInterface

if TYPE_CHECKING:
    from collections.abc import Callable

    import uvicorn
    from PySide6.QtWidgets import QWidget

    from ..app import App
    from ..core.ports import Platform, Renderer
    from ..core.results import ApiTlsResult
    from ..ipc import IPCServer, SingleInstance

    # ``TYPE_CHECKING`` only: these names cost NOTHING at runtime, so the
    # module stays light while the faces keep real types.  A ``type: ignore``
    # would have been the other way to silence the checker, and this codebase
    # does not take that trade.

log = logging.getLogger(__name__)


class ApiUI(UserInterface, key="api"):
    """The REST API — a headless server face.

    It runs a session like every other long-lived UI: coldplug, hotplug, the
    live loops, and the session prime that shows a panel's saved display on
    connect.  It used to be the one exception (``needs_session = False``),
    which is #148: a panel served by the API stayed blank until a caller
    happened to poll ``/tick``, and ``/tick`` restored the theme on every poll
    to make up for it.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8080,
                 tls: ApiTlsResult | None = None) -> None:
        log.info("ApiUI.__init__: host=%s port=%d tls=%s", host, port,
                 tls is not None)
        self.host = host
        self.port = port
        self.tls = tls
        self._server: uvicorn.Server | None = None

    def compose(self, platform: Platform | None) -> App:
        """Headless composition: the API renders preview frames, has no widgets."""
        log.info("ApiUI.compose: headless QtRenderer")
        from .._boot import trcc
        from ..adapters.render.qt import QtRenderer
        return trcc(platform=platform, renderer=QtRenderer())

    def run(self) -> int:
        """Serve until the process is stopped."""
        log.info("ApiUI.run: serving on %s:%d", self.host, self.port)
        import uvicorn

        from .api.main import build_app
        # log_config=None: uvicorn's default config goes through dictConfig,
        # which CLOSES every handler already attached — ours included, while
        # leaving them on the root logger.  Every record after this line was
        # dropped, so no API request ever reached ``trcc report``'s file.
        # access_log=False: each route logs its own entry (params sanitized);
        # a second line per request would let a ``/tick`` poller rotate the
        # diagnosis out of the file within hours.
        # ssl_* None is uvicorn's own "plain HTTP" — no branch needed here.
        # A Server we hold, not ``uvicorn.run``: :meth:`stop` must reach it.
        self._server = server = uvicorn.Server(uvicorn.Config(
            build_app(trcc=self), host=self.host, port=self.port,
            log_level="info", log_config=None, access_log=False,
            ssl_certfile=self.tls and self.tls.cert,
            ssl_keyfile=self.tls and self.tls.key))
        if self._stop_requested:
            log.info("ApiUI.run: stopped before serving")
            return 0
        server.run()
        if not server.started:
            # ``uvicorn.run``'s own verdict: it exits 3 when startup failed --
            # a port in use, a bad certificate.
            log.error("ApiUI.run: the server never started on %s:%d",
                      self.host, self.port)
            return 3
        return 0

    def stop(self) -> None:
        """Ask uvicorn to finish: it checks ``should_exit`` every tick."""
        log.info("ApiUI.stop: server=%s", self._server is not None)
        if self._server is not None:
            self._server.should_exit = True


class DaemonUI(UserInterface, key="daemon"):
    """The background process that owns USB and serves every other face.

    A UI like the others -- "daemon should be a command that any ui can
    utilize" -- but with two things that are genuinely its own, and both are
    overrides rather than shared defaults for reasons that are load-bearing.
    """

    def __init__(self, *, renderer: Renderer | None = None) -> None:
        log.info("DaemonUI.__init__: renderer=%s", renderer is not None)
        self._renderer = renderer
        self._server: IPCServer | None = None

    def preflight(self) -> int | None:
        """Refuse to start if another daemon already owns the socket.

        Exit code **1**, not 0: unlike the GUI -- where a peer means the
        running window was raised and this launch succeeded at what the user
        wanted -- a second daemon simply failed to start.

        This must run before :meth:`compose`.  Placed any later, the process
        would compose an App and coldplug in :meth:`bring_up`, **opening USB**,
        before discovering another daemon already owns the bus.
        """
        from .. import ipc
        if ipc.daemon_running():
            log.warning("DaemonUI.preflight: another daemon already owns %s",
                        ipc.socket_path())
            return 1
        from ..daemon import mark_started
        mark_started()
        return None

    def compose(self, platform: Platform | None) -> App:
        """Build a LOCAL App, never a proxy.

        The daemon owns USB directly and must never proxy to itself.  If
        ``TRCC_DAEMON`` leaked into this process's environment -- set in a
        shell profile, or inherited from the client that spawned us -- the
        inherited default would reach for a daemon socket instead of opening
        USB, and the startup path through it would re-spawn: a fork bomb
        (#162).  Strip the flag first, then build local.
        """
        import os

        from .._boot import _ENV_FLAG, _build_local_app
        log.info("DaemonUI.compose: stripping %s and building local",
                 _ENV_FLAG)
        os.environ.pop(_ENV_FLAG, None)
        return _build_local_app(platform=platform, renderer=self._renderer)

    def run(self) -> int:
        """Bind the socket and serve Commands until shutdown.

        ``App.close()`` is NOT called here: :meth:`UserInterface.start` owns
        it.  Closing in both places ran the whole detach-and-blank sequence
        twice -- the same double-teardown the GUI window had removed once
        already.
        """
        log.info("DaemonUI.run: binding the IPC server")
        from .. import ipc
        self._server = server = ipc.IPCServer(self._app)
        if self._stop_requested:
            log.info("DaemonUI.run: stopped before the server bound")
            return 0
        server.start()
        try:
            server.serve_forever()
        finally:
            server.shutdown()
        log.info("DaemonUI.run: served to completion")
        return 0

    def stop(self) -> None:
        """Stop serving: flip the server's flag and wake its accept loop."""
        log.info("DaemonUI.stop: server=%s", self._server is not None)
        if self._server is not None:
            self._server.shutdown()


class _QtUI(UserInterface):
    """Shared base for the two widget skins.  Intermediate — not registered.

    Both compose Qt-first through ``qapp.build_qt_app``: ``QtRenderer`` needs a
    live ``QApplication`` before it exists, which is the constraint that made
    the launch seam inject a ``Platform`` rather than a pre-built ``App``.
    """

    def compose(self, platform: Platform | None) -> App:
        log.info("%s.compose: Qt-first via build_qt_app", type(self).__name__)
        from .qapp import build_qt_app
        return build_qt_app(platform)

    def stop(self) -> None:
        """Queue a quit for the Qt loop.

        Queued, not called: ``quit()`` is ignored unless ``exec()`` is running,
        and a queued one is honoured the moment it starts.  One posted during
        the splash's nested loop is ignored there (measured), so the coldplug
        finishes and :meth:`UserInterface.start` reads the flag instead.
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        log.info("%s.stop: queueing quit (qapp=%s)", type(self).__name__,
                 qapp is not None)
        if qapp is not None:
            QTimer.singleShot(0, qapp.quit)

    def _exec(self) -> int:
        """Run the Qt event loop to completion -- unless a stop came first."""
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        assert qapp is not None, "compose() must have built a QApplication"
        if self._stop_requested:
            log.info("_exec: stopped while the window was built — not entering "
                     "the Qt event loop")
            return 0
        log.info("_exec: entering the Qt event loop")
        return qapp.exec()


class GuiUI(_QtUI, key="gui"):
    """The shipping GUI — legacy chrome, one window, single-instance."""

    def __init__(self, *, decorated: bool = False, start_hidden: bool = False,
                 single_instance: bool = True,
                 on_ready: Callable[[Any], None] | None = None) -> None:
        log.info("GuiUI.__init__: decorated=%s start_hidden=%s "
                 "single_instance=%s", decorated, start_hidden, single_instance)
        self.decorated = decorated
        self.start_hidden = start_hidden
        self.want_single_instance = single_instance
        self.on_ready = on_ready
        self._instance: SingleInstance | None = None

    def preflight(self) -> int | None:
        """Take the GUI's cross-process lock; 0 if a peer already holds it.

        Exit **0**, not 1: a peer means the running window was raised, so this
        launch did exactly what the user asked for.  It must precede
        :meth:`compose` — the early return has to happen before a
        ``QApplication`` is built or USB is opened.
        """
        if not self.want_single_instance:
            log.info("GuiUI.preflight: single-instance disabled (dev mock)")
            return None
        from ..ipc import SingleInstance
        self._instance = SingleInstance("gui")
        if self._instance is None:
            log.info("GuiUI.preflight: peer GUI raised — exiting cleanly")
            return 0
        return None

    def compose(self, platform: Platform | None) -> App:
        """Point the asset resolver at the packaged directory, then build."""
        log.debug("compose: platform=%s", platform)
        from .gui.assets import _PKG_ASSETS_DIR, set_assets_dir
        set_assets_dir(_PKG_ASSETS_DIR)
        return super().compose(platform)

    def bring_up(self) -> bool:
        """Coldplug on the splash worker, then the live loops.

        The coldplug runs on a background QThread so the splash can paint
        per-device progress; ``start_session`` afterwards is idempotent and
        skips the coldplug it already did, starting only the loops.

        ``--resume`` (autostart, hidden in the tray) shows no splash: a window
        flashing up at login is what starting hidden exists to avoid, and the
        C# never shows one at all (``FormStart`` is hidden 1 ms after it is
        shown, ``Form1.cs:519-521``).  The shared bring-up does the same
        coldplug inline.
        """
        if self.start_hidden:
            log.info("GuiUI.bring_up: --resume — no splash")
            return super().bring_up()
        log.info("GuiUI.bring_up: splash bootstrap")
        from .gui.splash import run_bootstrap_with_splash
        if not run_bootstrap_with_splash(self._app):
            return False
        return super().bring_up()

    def run(self) -> int:
        log.info("GuiUI.run: building the window")
        from ..core.commands import DeviceConnectionIssues
        from .gui.trcc_app import TRCCApp

        window = TRCCApp(app=self, decorated=self.decorated)
        if self._instance is not None:
            # Fired from SingleInstance's accept thread; the Qt signal marshals
            # it onto the GUI thread (a direct cross-thread QWidget call
            # deadlocked the event loop, #196).
            self._instance.on_raise = window.raise_requested.emit
        window.replay_initial_devices()
        if self.on_ready is not None:
            self.on_ready(window)
        if not self.start_hidden:
            window.show()
            # Surface devices found but not connected, read from the bus: the
            # failures fired before the window subscribed.
            window.notify_device_failures(
                self.dispatch(DeviceConnectionIssues()).issues,
            )
        return self._exec()

    def teardown(self) -> None:
        """Release the single-instance lock this face took in preflight."""
        if self._instance is not None:
            log.info("GuiUI.teardown: releasing the single-instance lock")
            self._instance.close()
            self._instance = None


class QtGuiUI(_QtUI, key="qtgui"):
    """The native-skin rebuild.  No single-instance lock — it never had one."""

    def __init__(self, *, start_hidden: bool = False,
                 on_ready: Callable[[Any], None] | None = None) -> None:
        log.info("QtGuiUI.__init__: start_hidden=%s", start_hidden)
        self.start_hidden = start_hidden
        self.on_ready = on_ready
        self._splash: QWidget | None = None

    def bring_up(self) -> bool:
        """Splash up, coldplug inline, loops started — before the window builds.

        Inline rather than on a worker (the gui's shape): one handshake per
        attached device is fast, and doing it first means the pickers and
        browsers populate at construction instead of booting blank.
        ``--resume`` shows no splash, for the gui's reason.
        """
        log.info("QtGuiUI.bring_up: splash=%s + session", not self.start_hidden)
        if not self.start_hidden:
            from PySide6.QtWidgets import QApplication

            from .qtgui.splash import show_splash
            self._splash = show_splash()
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.processEvents()
        return super().bring_up()

    def run(self) -> int:
        log.info("QtGuiUI.run: building the window")
        from .qtgui.app import MainWindow
        from .qtgui.splash import auto_close

        window = MainWindow(self)
        if self.start_hidden:
            log.info("QtGuiUI.run: --resume — starting hidden in the tray")
        else:
            window.show()
        if self._splash is not None:
            auto_close(self._splash, after_ms=250)
            self._splash = None
        if self.on_ready is not None:
            self.on_ready(window)
        return self._exec()


class CliUI(UserInterface, key="cli"):
    """The terminal face.  ``_ctx.get_app()`` returns one, so every CLI command
    body dispatches through it and the log reads ``[cli] dispatch …``.

    The CLI does NOT run :meth:`start`: a one-shot command must leave its
    frame on the panel, and ``start`` ends in ``App.close``, which sleeps every
    connected panel — ``trcc color`` would blank the colour it just sent.
    ``_entry`` hands argv straight to Typer, so :meth:`run` is unused.

    ``needs_session`` is **False**: a one-shot command must not pay for a
    coldplug it will never use.  A Command that needs a device connects it
    in ``App.dispatch`` (``USES_DEVICE``).
    """

    needs_session = False

    def compose(self, platform: Platform | None) -> App:
        """The CLI's App, from the overrides the tests and harnesses set."""
        log.info("CliUI.compose: composing the CLI's App")
        from .cli._ctx import compose_app, set_platform
        if platform is not None:
            set_platform(platform)
        return compose_app()

    def run(self) -> int:
        """Parse argv and run one command.  Typer owns the exit code."""
        log.info("CliUI.run: handing off to the argv router")
        from .cli.main import app as typer_app
        typer_app()
        return 0
