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

    from PySide6.QtWidgets import QWidget

    from ..app import App
    from ..core.ports import Platform, Renderer
    from ..ipc import SingleInstance

    # ``TYPE_CHECKING`` only: these names cost NOTHING at runtime, so the
    # module stays light while the faces keep real types.  A ``type: ignore``
    # would have been the other way to silence the checker, and this codebase
    # does not take that trade.

log = logging.getLogger(__name__)


class ApiUI(UserInterface, key="api"):
    """The REST API — a headless server face.

    ``needs_session`` is **False**, which preserves today's behaviour exactly:
    the API brings up no coldplug and no live loops (measured — it dispatches
    neither ``start_session`` nor ``discover_and_connect`` anywhere).
    ``App.start_session``'s own docstring calls that out as the #148
    divergence, and flipping it here would change what the API does to USB on
    startup.  That is a behavioural fix and belongs in its own increment where
    it can be verified against a real fleet, not folded into a structural one.
    """

    needs_session = False

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8080) -> None:
        log.info("ApiUI.__init__: host=%s port=%d", host, port)
        self.host = host
        self.port = port

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
        uvicorn.run(build_app(trcc=self._app), host=self.host, port=self.port,
                    log_level="info")
        return 0


class DaemonUI(UserInterface, key="daemon"):
    """The background process that owns USB and serves every other face.

    A UI like the others -- "daemon should be a command that any ui can
    utilize" -- but with two things that are genuinely its own, and both are
    overrides rather than shared defaults for reasons that are load-bearing.
    """

    def __init__(self, *, renderer: Renderer | None = None) -> None:
        log.info("DaemonUI.__init__: renderer=%s", renderer is not None)
        self._renderer = renderer

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
        from ..daemon import _install_signal_handlers
        server = ipc.IPCServer(self._app)
        server.start()
        _install_signal_handlers(server)
        try:
            server.serve_forever()
        finally:
            server.shutdown()
        log.info("DaemonUI.run: served to completion")
        return 0


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

    @staticmethod
    def _install_quit_handlers() -> None:
        """SIGINT / SIGTERM must reach the Qt loop, or teardown never runs.

        SIGTERM is what the session manager sends at PC shutdown.  Without a
        handler the process dies before ``qapp.exec()`` returns, the cleanup
        never happens, and the panel is left mid-stream showing its last frame
        (#143).
        """
        import signal

        from PySide6.QtWidgets import QApplication

        def _quit(*_args: object) -> None:
            log.info("_install_quit_handlers: quit signal — stopping the loop")
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.quit()

        signal.signal(signal.SIGINT, _quit)
        signal.signal(signal.SIGTERM, _quit)

    @staticmethod
    def _exec() -> int:
        """Run the Qt event loop to completion."""
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        assert qapp is not None, "compose() must have built a QApplication"
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
        """
        log.info("GuiUI.bring_up: splash bootstrap")
        from .gui.splash import run_bootstrap_with_splash
        if not run_bootstrap_with_splash(self._app):
            return False
        return super().bring_up()

    def run(self) -> int:
        log.info("GuiUI.run: building the window")
        from ..core.commands import DeviceConnectionIssues
        from .gui.trcc_app import TRCCApp

        if self._platform is None:
            raise RuntimeError(
                "GuiUI needs the host Platform it was started with: the "
                "window's screen capture belongs to the window's session")
        window = TRCCApp(app=self._app, platform=self._platform,
                         decorated=self.decorated)
        if self._instance is not None:
            # Fired from SingleInstance's accept thread; the Qt signal marshals
            # it onto the GUI thread (a direct cross-thread QWidget call
            # deadlocked the event loop, #196).
            self._instance.on_raise = window.raise_requested.emit
        window.replay_initial_devices()
        if self.on_ready is not None:
            self.on_ready(window)
        self._install_quit_handlers()
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
        """
        log.info("QtGuiUI.bring_up: splash + session")
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

        window = MainWindow(self._app)
        if self.start_hidden:
            log.info("QtGuiUI.run: --resume — starting hidden in the tray")
        else:
            window.show()
        if self._splash is not None:
            auto_close(self._splash, after_ms=250)
            self._splash = None
        if self.on_ready is not None:
            self.on_ready(window)
        self._install_quit_handlers()
        return self._exec()


class CliUI(UserInterface, key="cli"):
    """The terminal face — and the router that launches the others.

    The CLI is both, which is why it was left out of the registry at first:
    ``trcc gui`` is this process starting a DIFFERENT face, while
    ``trcc device list`` is this face doing its own work.  Being the launcher
    does not stop it being a UI, and leaving it out made "every UI is a
    ``UserInterface``" false for the surface users touch most.

    What kept it out was cost, and lazy composition removed it.  Measured,
    ``trcc --help`` builds **zero** Apps in 65 ms; composing eagerly in
    :meth:`start` would have added a platform scan plus a 111 ms
    ``QtRenderer`` to every ``--help`` and every text command.  Now nothing is
    composed until something dispatches, so joining the bus costs a ``--help``
    exactly nothing.

    ``needs_session`` is **False**: a one-shot command must not pay for a
    coldplug it will never use — ``App.start_session``'s own docstring says
    one-shot scripts skip it.  A command that does need a device asks for one
    explicitly through ``EnsureConnected``.
    """

    needs_session = False

    def compose(self, platform: Platform | None) -> App:
        """The SAME lazy singleton the command bodies already use.

        ``_ctx.get_app()`` is ``@lru_cache``'d and read by 113 command bodies.
        Returning it here means this face and those bodies share ONE App
        rather than composing a second one behind their backs — the bus is a
        different door onto the same object, not a parallel world.
        """
        log.info("CliUI.compose: reusing the CLI's lazy App singleton")
        from .cli._ctx import get_app, set_platform
        if platform is not None:
            set_platform(platform)
        return get_app()

    def run(self) -> int:
        """Parse argv and run one command.  Typer owns the exit code."""
        log.info("CliUI.run: handing off to the argv router")
        from .cli.main import app as typer_app
        typer_app()
        return 0
