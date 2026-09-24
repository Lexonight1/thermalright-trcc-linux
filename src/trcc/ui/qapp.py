"""QApplication-level configuration shared by every next/ Qt UI.

``configure_qapplication(qapp)`` applies the env + Qt settings the
shipping GUI requires:

* Silence two categories of Qt noise (``qt.qpa.services``,
  ``qt.qpa.theme.gnome``) that aren't actionable for users.
* Disable High-DPI auto-scaling (legacy parity — the baked PNG
  backgrounds are 1× and look broken under Qt's auto-scale).
* Pick ``Microsoft YaHei`` as the global font with a ``Sans Serif``
  fallback so CJK glyphs land correctly on Windows-style overlays.
* Set the freedesktop ``WMClass`` so wayland compositors match the
  ``trcc-linux.desktop`` file (correct icon in the dock).
* Keep the QApplication alive while the system tray is visible — the
  legacy GUI hides to tray instead of quitting on close.

Callers (both ``next.ui.gui.launch`` and ``next.ui.qtgui.launch``) build
a ``QApplication`` and pass it here exactly once during composition.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

from PySide6.QtGui import QFont, QScreen
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from ..app import App
    from ..core.ports import Platform

log = logging.getLogger(__name__)


def configure_qt_environment() -> None:
    """Set the Qt env vars — call this BEFORE constructing QApplication.

    Qt reads ``QT_LOGGING_RULES`` (and the HighDPI / platform vars) while
    the QApplication is being built, so setting them afterwards is too late:
    on systems without ``xdg-desktop-portal`` the portal warnings
    (``qt.qpa.theme.gnome`` / ``qt.qpa.services`` DBus 'NameHasNoOwner')
    would already have hit stderr.  ``setdefault`` lets a user override via
    the environment (e.g. ``QT_LOGGING_RULES=*=true`` to see everything).
    """
    log.debug("configure_qt_environment")
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.qpa.services=false;qt.qpa.theme.gnome=false",
    )
    # No ``QT_ENABLE_HIGHDPI_SCALING`` here.  It is a **Qt5** variable, and it
    # sat in this function from v6.1.7 (2026-02-26) doing nothing at all: this
    # tree has only ever depended on PySide6, and Qt6 makes high-DPI scaling
    # mandatory -- ``AA_DisableHighDpiScaling`` still exists but has no effect,
    # and the env var is documented as testing-only, ignored outright on
    # Wayland and macOS.  Measured on PySide6 6.11, offscreen: devicePixelRatio
    # is 1.00 with it set to "0" and 1.00 with it set to "1"; under
    # ``QT_SCALE_FACTOR=2`` it is 2.00 either way.  Two CHANGELOG entries credit
    # it with fixing HiDPI layout corruption, and #220's investigation was
    # shaped for 71 days by the belief that it was protecting the render path.
    #
    # It does not need protecting.  The wire frame is DPI-immune BY
    # CONSTRUCTION -- ``QtRenderer`` composes on ``QImage(w, h)`` with explicit
    # pixels, whose ``logicalDpiX`` stays 96 whatever the screen reports, and
    # ``QPixmap`` (the devicePixelRatio-aware type) is only ever used for the
    # GUI preview.  That invariant is now GATED, which the env var never was:
    # ``tests/test_hidpi_frame_is_dpi_immune.py``.
    #
    # Do not re-add a scale-factor override to chase a window-chrome symptom.
    # One was added and reverted before ("Qt6 owns DPI -- VM-caught"), and this
    # line is the second thing to be believed in without being measured.
    # Wipe an offscreen platform forced by an upstream CLI invocation
    # so subsequent windowed launches show real chrome.
    os.environ.pop("QT_QPA_PLATFORM", None)


def configure_qapplication(qapp: QApplication) -> None:
    """Apply the shared QApplication-level settings.  Idempotent.

    Env vars are set separately by :func:`configure_qt_environment`, which
    callers MUST invoke BEFORE building the QApplication (see its docstring).
    """
    # ── QApplication-level ───────────────────────────────────────────
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setDesktopFileName("trcc-linux")

    # Font: try Microsoft YaHei (CJK + Latin coverage matches Windows
    # baked overlays).  Fall back to Sans Serif so a fresh install
    # without that font still renders cleanly.
    font = QFont("Microsoft YaHei", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    qapp.setFont(font)

    log.debug(
        "configure_qapplication: font=%r quit_on_last_closed=%s",
        qapp.font().family(),
        qapp.quitOnLastWindowClosed(),
    )


def _log_screen(screen: QScreen) -> None:
    """Emit one INFO line of a screen's DPI/scale facts.

    Logged at INFO so every default ``trcc report`` (which pastes the log
    tail) carries device-pixel-ratio + geometry — the facts a HiDPI /
    desktop-scaling bug (#220) needs, and which no verbosity surfaced before
    because nothing read them.
    """
    g = screen.geometry()
    log.info(
        "screen %r: devicePixelRatio=%.2f logicalDpi=%.0f geometry=%dx%d@(%d,%d)",
        screen.name(), screen.devicePixelRatio(), screen.logicalDotsPerInch(),
        g.width(), g.height(), g.x(), g.y(),
    )


def _on_screen_metrics_changed(*_args: object) -> None:
    """Re-probe every screen when one's geometry or DPI changes at runtime.

    This is the #220 repro: the user toggles desktop scaling (100% ↔ 200%)
    while the app is open.  A startup-only probe misses it; this fires on the
    change so the log captures the before/after.
    """
    log.info("screen metrics changed at runtime — re-probing all screens")
    for screen in QApplication.screens():
        _log_screen(screen)


def _on_primary_screen_changed(screen: QScreen | None) -> None:
    log.info("primaryScreenChanged -> %r", screen.name() if screen else None)
    if screen is not None:
        _log_screen(screen)


def probe_screens(qapp: QApplication) -> None:
    """Log DPI/scale facts now and wire live-change re-probes (#220).

    GUI-only: the headless CLI/API render path never builds a windowed
    QApplication, so there are no real screens to probe there.
    """
    log.debug("probe_screens: qapp=%s", qapp)
    for screen in qapp.screens():
        _log_screen(screen)
        screen.geometryChanged.connect(_on_screen_metrics_changed)
        screen.logicalDotsPerInchChanged.connect(_on_screen_metrics_changed)
        screen.physicalDotsPerInchChanged.connect(_on_screen_metrics_changed)
    qapp.primaryScreenChanged.connect(_on_primary_screen_changed)


def build_qt_app(platform: Platform | None = None) -> App:
    """Compose the ``App`` for a widget Qt UI — the shared Qt-first path used
    by BOTH the shipping GUI and the qtgui skin.

    The unified launch seam (see ``METHOD_UI.md``): every UI is injected the
    ``Platform`` port and composes its own App from it, respecting its own
    renderer lifecycle.  Here that lifecycle is Qt-first — set the Qt env,
    ensure a windowed ``QApplication`` (``QtRenderer`` needs one), apply the
    shared QApplication settings, then build through the canonical factory with
    a ``QtRenderer``.

    ``platform=None`` uses the host platform; the dev mock injects a
    ``MockPlatform``.  Returns the composed App; the QApplication is reachable
    via ``QApplication.instance()``.
    """
    log.debug("build_qt_app: platform=%s", platform)
    configure_qt_environment()
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):
        qapp = QApplication(sys.argv)
    configure_qapplication(qapp)
    probe_screens(qapp)
    from .._boot import trcc
    from ..adapters.render.qt import QtRenderer
    return trcc(platform=platform, renderer=QtRenderer())
