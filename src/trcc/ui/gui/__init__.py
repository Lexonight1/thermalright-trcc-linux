"""GUI composition root — wires Qt adapter.

Single entry point for the graphical interface. Builds the windowed
``QApplication`` (which Qt requires before any QWidget), constructs a
``QtRenderer`` against it, then delegates the rest of the DI graph to
``trcc._boot.trcc()``. ``discover_now=False`` lets the splash show
progress while USB connect + theme extraction run in a background
``BootstrapWorker``.
"""
from __future__ import annotations

import logging
import os
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
    """Bootstrap and run the GUI application.

    Returns the Qt exit code.
    """
    # 10C.5: the daemon-mode refusal is gone.  The GUI now boots whether
    # ``_boot.trcc()`` returns a real ``Trcc`` or a ``TrccProxy``.  Writes
    # already go through the command bus (10C.2), descriptors round-trip
    # over IPC (10C.1), and FRAME event payloads carry their surface as
    # a JSON-safe envelope (10C.4).
    #
    # NB: descriptor-driven handler construction is still on the
    # backlog (10C.6) — a daemon-mode GUI launches cleanly today but
    # the device sidebar will be empty until handlers grow a (descriptor,
    # trcc, idx) constructor instead of holding an LCDDevice reference.

    # ── Platform first — needed for lock check, DPI config, autostart, etc.
    from trcc.adapters.system import PlatformFactory
    platform = PlatformFactory.current()

    # ── Single-instance lock — acquire before any heavy setup ────────────
    lock = platform.acquire_instance_lock()
    if lock is None:
        platform.raise_existing_instance()
        return 0

    # ── Qt bootstrap (windowed QApp — must precede QtRenderer construction)
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(platform.resolve_assets_dir(_PKG_ASSETS_DIR))

    # Silence two categories of Qt noise that aren't actionable for our users:
    #   qt.qpa.services            — generic platform-services chatter
    #   qt.qpa.theme.gnome         — xdg-desktop-portal startup failures on
    #                                 systems where the portal isn't running
    #                                 (KDE/Hyprland/sway sessions, missing
    #                                 xdg-desktop-portal-gnome package, etc.)
    # setdefault so a debugging user can still override with their own rules.
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.qpa.services=false;qt.qpa.theme.gnome=false",
    )
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ.pop("QT_QPA_PLATFORM", None)  # clear offscreen set by CLI

    platform.configure_dpi()

    from typing import cast

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setDesktopFileName("trcc-linux")
    qapp.setProperty("_instance_lock", lock)

    font = QFont("Microsoft YaHei", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    qapp.setFont(font)

    # ── Build Trcc with the windowed QApp's renderer; defer discovery so
    # the splash can show progress while USB connect + theme extraction
    # run in BootstrapWorker.
    from trcc._boot import trcc as _boot_trcc
    from trcc.adapters.render.qt import QtRenderer
    renderer = QtRenderer()
    t = _boot_trcc(platform, renderer=renderer,
                   discover_now=False, verbosity=verbosity)

    # ── Splash + background discover ─────────────────────────────────────
    from trcc.ui.gui.splash import run_bootstrap_with_splash
    if not run_bootstrap_with_splash(t):
        return 1

    # ── GUI adapter — pulls Trcc handle via _boot.trcc() (cached)  ───────
    from trcc.ui.gui.trcc_app import TRCCApp as _TRCCApp
    window = _TRCCApp(
        platform=platform,
        decorated=decorated,
    )

    # ── IPC server bound to Trcc — manifold dispatch for clients ────────
    # Renderer is forwarded so Topic.FRAME events get their surface payload
    # encoded into a JSON-safe envelope before reaching TrccProxy clients.
    from trcc.ipc import IPCServer
    ipc_server = IPCServer(trcc=t, renderer=renderer)
    ipc_server.start()
    window._ipc_server = ipc_server

    # ── Replay device list to the window — subscribers run in __init__,
    # so they missed the publish that happened during discover. ─────────
    from itertools import chain

    from trcc.core.events import Topic
    t.events.publish(
        Topic.DEVICE_LIST,
        tuple(chain(t.lcd_devices, t.led_devices)),
    )
    # Metrics loop already running — `Trcc.discover()` (called by the splash
    # QThread) starts the OS-provided MetricsLoop after device enumeration.

    # ── IPC raise + signals ───────────────────────────────────────────────
    signal.signal(signal.SIGINT, lambda *_: qapp.quit())
    platform.wire_ipc_raise(qapp, window)
    # Issue #143 ("USB communication lost" after GUI exit) is handled at
    # the kernel level — udev rule sets power/autosuspend_delay_ms=30000
    # so the kernel autosuspends the device ~30s after our frame stream
    # stops, and the firmware sleeps the panel cleanly. No userspace
    # shutdown hook needed.

    if not start_hidden:
        window.show()

    return qapp.exec()
