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

from PySide6.QtGui import QFont
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
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.qpa.services=false;qt.qpa.theme.gnome=false",
    )
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
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
    configure_qt_environment()
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):
        qapp = QApplication(sys.argv)
    configure_qapplication(qapp)
    from .._boot import trcc
    from ..adapters.render.qt import QtRenderer
    return trcc(platform=platform, renderer=QtRenderer())
