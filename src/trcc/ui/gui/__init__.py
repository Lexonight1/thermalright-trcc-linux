"""GUI composition root for next/ — wires Qt adapter.

Single entry point for the graphical interface.  Builds the windowed
``QApplication`` (which Qt requires before any QWidget), constructs an
``App`` via ``trcc._boot.trcc()``, then hands the app handle
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
from collections.abc import Callable
from typing import Any

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
    'launch',
    'run_gui',
]

log = logging.getLogger(__name__)


def launch(verbosity: int = 0, decorated: bool = False,
           start_hidden: bool = False) -> int:
    """Bootstrap and run the shipping GUI.  Returns the Qt exit code.

    Thin wrapper over :func:`run_gui` — builds the real host platform and
    runs the full composition with the production seams on (single-instance
    lock, IPC server, ``os._exit`` reap).

    ``verbosity`` is unused: the CLI root callback (``ui.cli.main:_root``)
    ALWAYS runs first and has already configured logging at the requested
    level.  Re-configuring here would silently downgrade DEBUG back to INFO.
    """
    del verbosity
    from ...adapters.system import PlatformFactory
    platform = PlatformFactory.current()
    return run_gui(platform, decorated=decorated, start_hidden=start_hidden)


def run_gui(platform: Any, *, decorated: bool = False,
            start_hidden: bool = False, single_instance: bool = True,
            ipc: bool = True, force_exit: bool = True,
            on_ready: Callable[[Any], None] | None = None) -> int:
    """Run the GUI composition for a given ``platform``.  Returns exit code.

    The ONE shared composition root for every GUI entry point — shipping
    ``launch`` and ``dev/mock_gui`` both call this, so the dev mock exercises
    the SAME code the real app runs (the whole reason to mock: real code
    paths surface real bugs).  Callers differ only in what ``platform`` they
    build and these seams:

      * ``single_instance`` — acquire the cross-process GUI lock (off for the
        dev mock so it never collides with a real install).
      * ``ipc`` — bind the daemon-style IPC server (off for the dev mock to
        avoid socket collision).
      * ``force_exit`` — ``os._exit`` to reap native threads (psutil / pyusb /
        pynvml can outlive ``qapp.exec()``); the dev mock returns normally.

    Logging is NOT configured here — that stays the caller's job (CLI root
    callback for shipping, ``dev/_mock_bootstrap`` for the mock), so the
    "configure_logging exactly once" invariant holds.
    """
    from typing import cast

    from PySide6.QtWidgets import QApplication

    # ── stdout/stderr UTF-8 (Windows cp1252 fix; no-op elsewhere) ────
    platform.configure_stdout()

    # ── Single-instance lock + raise-existing-window ─────────────────
    instance = None
    if single_instance:
        from ...ipc import SingleInstance
        instance = SingleInstance("gui")
        if instance is None:
            # A peer GUI was already running; raise was sent.  Exit cleanly.
            return 0

    # ── Assets dir (packaged location) ───────────────────────────────
    from .assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(_PKG_ASSETS_DIR)

    # ── Qt bootstrap (windowed QApp — must precede QtRenderer) ──────
    # Env (QT_LOGGING_RULES etc.) MUST be set before QApplication so the
    # desktop-portal warnings are silenced at startup, not after.
    from ..qapp import configure_qapplication, configure_qt_environment
    configure_qt_environment()
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))
    configure_qapplication(qapp)

    # ── Build App via the canonical factory (renderer follows QApp) ──
    from ..._boot import trcc
    from ...adapters.render.qt import QtRenderer
    from ...app import App
    renderer = QtRenderer()
    app = cast(App, trcc(platform=platform, renderer=renderer))

    # ── Splash + background discover ────────────────────────────────
    from .splash import run_bootstrap_with_splash
    bootstrap = run_bootstrap_with_splash(app)
    if bootstrap.aborted:
        return 1

    # ── Hotplug listener + metrics broadcast (one cadence drives the
    # system-info / activity sidebar / overlay refresh) ─────────────
    app.start_hotplug()
    app.metrics_loop.start()
    # Fast LED effect/carousel animation (breathing/colour-cycle/rainbow) —
    # the slow sensor cadence can't animate them.
    app.led_animation_loop.start()

    # ── Main window — TRCCApp keeps the legacy chrome ──────────────
    window = TRCCApp(app=app, decorated=decorated)

    # ── IPC server bound to App — daemon-style Command dispatch ─────
    ipc_server = None
    if ipc:
        from ...ipc import IPCServer
        ipc_server = IPCServer(app=app)
        ipc_server.start()
        window._ipc_server = ipc_server

    # ── Wire raise-existing-window callback ─────────────────────────
    if instance is not None:
        def _raise_window() -> None:
            window.show()
            window.raise_()
            window.activateWindow()
        instance.on_raise = _raise_window

    # ── Initial device replay — discover ran in the splash worker, so
    # iterate ``app.devices`` once for the first sidebar render.  Live
    # mutations after this come through DeviceConnected/Disconnected.
    window.replay_initial_devices()

    # Optional post-build hook — a behaviour-neutral extension point (default
    # None).  The dev mock GUI uses it to mount its developer console; shipping
    # callers pass nothing.
    if on_ready is not None:
        on_ready(window)

    def _on_sigint(*_args: object) -> None:
        """SIGINT — quit the Qt event loop cleanly."""
        qapp.quit()
    signal.signal(signal.SIGINT, _on_sigint)

    if not start_hidden:
        window.show()
        # Surface any device that was found but didn't connect — with the
        # OS-correct hint the Platform supplied (e.g. "run as administrator").
        # Done after show() because the splash-time failure has no GUI sink
        # yet (the window + bus_bridge are built above, post-bootstrap).
        window.notify_device_failures(bootstrap.failures)
        # Foolproof GPU sensors: if an NVIDIA card is present but its reader
        # isn't installed, offer a one-click install (consented).  Guarded so
        # an optional prompt can never block startup.
        from .gpu_reader_prompt import maybe_offer_gpu_reader_install
        try:
            maybe_offer_gpu_reader_install(app, window)
        except Exception:
            log.exception("GPU reader install prompt failed — continuing")

    try:
        exit_code = qapp.exec()
    finally:
        if ipc_server is not None:
            ipc_server.shutdown()
        if instance is not None:
            instance.close()
        app.close()
        log.info("run_gui: cleanup complete — process exit")

    # Belt-and-suspenders: Qt's metrics/sensor/render threads occasionally
    # outlive ``qapp.exec()``'s return when native libraries (pynvml,
    # psutil's ffi handles, pyusb) hold the GIL on shutdown.  ``os._exit``
    # skips atexit handlers and finalizers — we already did our cleanup in
    # the finally above, so this is the safe place to force the kernel to
    # reap the process.  The dev mock returns normally (``force_exit=False``).
    if force_exit:
        import os as _os
        _os._exit(exit_code)
    return exit_code
