"""GUI composition root for next/ — wires Qt adapter.

Single entry point for the graphical interface.  Builds the windowed
``QApplication`` (which Qt requires before any QWidget), constructs an
``App`` via ``trcc._boot.trcc_next()``, then hands the app handle
to ``MainWindow``.  ``discover`` runs in a background ``BootstrapWorker``
so the splash shows immediate feedback.

Composition root — this is the ONE place that imports concrete adapters
(``Platform``, ``QtRenderer``, ``IPCServer``, ``SingleInstance``).  Every
other file under ``next/ui/gui/`` holds an ``App`` handle and dispatches
Commands.
"""
from __future__ import annotations

import logging
import signal
import sys

from .base import BasePanel, ImageLabel
from .trcc_app import TRCCApp
from .uc_device import UCDevice
from .uc_preview import UCPreview
from .uc_theme_local import UCThemeLocal
from .uc_theme_mask import UCThemeMask
from .uc_theme_setting import UCThemeSetting
from .uc_theme_web import UCThemeWeb

__all__ = [
    'BasePanel',
    'ImageLabel',
    'TRCCApp',
    'UCDevice',
    'UCPreview',
    'UCThemeLocal',
    'UCThemeMask',
    'UCThemeSetting',
    'UCThemeWeb',
]

log = logging.getLogger(__name__)


def launch(verbosity: int = 0, decorated: bool = False,
           start_hidden: bool = False) -> int:
    """Bootstrap and run the next/ GUI application.

    Returns the Qt exit code.
    """
    from typing import cast

    from PySide6.QtWidgets import QApplication

    # ── Platform first ───────────────────────────────────────────────
    from ...adapters.system import PlatformFactory
    platform = PlatformFactory.current()

    # ── stdout/stderr UTF-8 (Windows cp1252 fix; no-op elsewhere) ────
    # Must precede ``configure_logging`` so the StreamHandler attaches
    # to an already-UTF-8 stream.
    platform.configure_stdout()

    # ── Logging — rotating file at paths.log_file() + stderr WARNING+
    # Without this, only the CLI root callback's basicConfig is in
    # effect (stderr-only) and `~/.trcc/trcc.log` never gets written.
    from ...adapters.infra.logging import configure_logging
    configure_logging(
        platform.paths().log_file(),
        level=logging.DEBUG if verbosity >= 1 else logging.INFO,
    )

    # ── Single-instance lock + raise-existing-window ─────────────────
    from ...ipc import SingleInstance
    instance = SingleInstance("gui")
    if instance is None:
        # A peer GUI was already running; raise was sent.  Exit cleanly.
        return 0

    # ── Assets dir (packaged location) ───────────────────────────────
    from .assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(_PKG_ASSETS_DIR)

    # ── Qt bootstrap (windowed QApp — must precede QtRenderer) ──────
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))

    from ..qapp import configure_qapplication
    configure_qapplication(qapp)

    # ── Build App via the canonical factory ──────────────────────────
    # ``trcc_next()`` returns either a real App (default) or an
    # AppProxy (when ``TRCC_NEXT_DAEMON=1``) — both expose the same
    # dispatch surface, so MainWindow doesn't care which it gets.
    from ..._boot import trcc_next
    from ...adapters.render.qt import QtRenderer
    from ...app import App
    renderer = QtRenderer()
    app = cast(App, trcc_next(platform=platform, renderer=renderer))

    # ── Splash + background discover ────────────────────────────────
    from .splash import run_bootstrap_with_splash
    if not run_bootstrap_with_splash(app):
        return 1

    # ── Hotplug listener (live device attach/detach) ────────────────
    app.start_hotplug()

    # ── Metrics broadcast — publishes SensorsUpdated every
    # refresh_interval_s so system info / activity sidebar / overlay
    # refresh all tick from one cadence.
    app.metrics_loop.start()

    # ── Main window — TRCCApp keeps the legacy chrome ──────────────
    window = TRCCApp(app=app, decorated=decorated)

    # ── IPC server bound to App — daemon-style Command dispatch ─────
    from ...ipc import IPCServer
    ipc_server = IPCServer(app=app)
    ipc_server.start()
    window._ipc_server = ipc_server

    # ── Wire raise-existing-window callback ─────────────────────────
    def _raise_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()
    instance.on_raise = _raise_window

    # ── Initial device replay — discover ran in the splash worker, so
    # iterate ``app.devices`` once for the first sidebar render.  Live
    # mutations after this come through DeviceConnected/Disconnected.
    window.replay_initial_devices()

    # ── Signals + verbosity bookkeeping ─────────────────────────────
    del verbosity

    def _on_sigint(*_args: object) -> None:
        """SIGINT — quit the Qt event loop cleanly."""
        qapp.quit()
    signal.signal(signal.SIGINT, _on_sigint)

    if not start_hidden:
        window.show()

    try:
        exit_code = qapp.exec()
    finally:
        ipc_server.shutdown()
        instance.close()
        app.close()
        log.info("launch: cleanup complete — process exit")
    # Belt-and-suspenders: Qt's metrics/sensor/render threads occasionally
    # outlive ``qapp.exec()``'s return when native libraries (pynvml,
    # psutil's ffi handles, pyusb) hold the GIL on shutdown.  A bare
    # ``return`` then leaves the python process alive past the user's
    # window-close.  ``os._exit`` skips atexit handlers and finalizers —
    # we already did our cleanup in the finally above, so this is the
    # safe place to force the kernel to reap the process.
    import os as _os
    _os._exit(exit_code)
