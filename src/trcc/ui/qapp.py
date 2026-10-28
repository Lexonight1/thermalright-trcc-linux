"""QApplication-level configuration shared by every next/ Qt UI.

``configure_qapplication(qapp)`` applies the env + Qt settings the
shipping GUI requires:

* Silence two categories of Qt noise (``qt.qpa.services``,
  ``qt.qpa.theme.gnome``) that aren't actionable for users.
* No High-DPI override: Qt6 makes scaling mandatory (see the note in
  ``configure_qt_environment`` — the old Qt5 variable did nothing).
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

import gc
import logging
import os
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QScreen
from PySide6.QtWidgets import QApplication

from ..core.logs import per_frame

if TYPE_CHECKING:
    from ..app import App
    from ..core.ports import Platform

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: How often the GUI thread checks whether a collection is due -- pyqtgraph's
#: default.  Garbage made in between waits at most this long.
GC_INTERVAL_MS = 1000

#: 3.14 made the collector incremental: what runs automatically is the young
#: generation plus one slice of the old, which is ``gc.collect(1)``, and it
#: never runs a full collection on its own.
_INCREMENTAL_GC = sys.version_info >= (3, 14)


def collect_due_garbage() -> int:
    """Run the collections Python would have run by now; return objects freed.

    Call it only from the GUI thread -- that is the point of it (see
    :func:`collect_on_gui_thread`).  The threshold check is pyqtgraph's
    ``GarbageCollector.check`` (``pyqtgraph/util/garbage_collector.py``, MIT,
    credited there to Erik Janssens): gen 0 when its count passes its threshold,
    then gen 1 and gen 2 the same way.  One difference from automatic gc
    before 3.14: it also holds a full collection back until the long-lived
    objects pending exceed a quarter of the total, a number Python code cannot
    read -- so this runs gen 2 somewhat more often, at most once per ~100 s.
    """
    counts = gc.get_count()
    thresholds = gc.get_threshold()
    if counts[0] <= thresholds[0]:
        return 0
    if _INCREMENTAL_GC:
        generations = [1]
    else:
        generations = [0]
        if counts[1] > thresholds[1]:
            generations.append(1)
            if counts[2] > thresholds[2]:
                generations.append(2)
    freed = sum(gc.collect(generation) for generation in generations)
    frame_log.debug("collect_due_garbage: counts=%s generations=%s freed=%d",
                    counts, generations, freed)
    return freed


def collect_on_gui_thread(qapp: QApplication) -> QTimer:
    """Turn automatic gc off and collect from a GUI-thread timer instead.

    Automatic gc runs on whichever thread happens to allocate when a threshold
    is crossed.  When that collection frees a widget, PySide runs its C++
    destructor on that thread with the GIL released -- for every Python
    SUBCLASS of a widget, which is every TRCC widget (shiboken's
    ``SbkDeallocWrapperCommon``; only a plain ``QWidget`` is deferred to the
    main thread).  A widget torn down off the GUI thread while the GUI thread
    is inside Qt crashes or deadlocks: the CI hang twice caught the main thread
    in ``setStyleSheet`` while the preview backfill thread sat in Python.
    A test-suite trip-wire counted 1,961 such collections in one run.

    The cure is pyqtgraph's: collect on the GUI thread only.  The timer is
    parented to *qapp*, which keeps it alive.  The daemon keeps automatic gc --
    it has no Qt loop to run this, and it makes no QObjects.
    """
    gc.disable()
    timer = QTimer(qapp)
    timer.setInterval(GC_INTERVAL_MS)
    timer.timeout.connect(collect_due_garbage)
    timer.start()
    log.info("collect_on_gui_thread: automatic gc off, collecting every %d ms "
             "on the GUI thread (incremental=%s, thresholds=%s)",
             GC_INTERVAL_MS, _INCREMENTAL_GC, gc.get_threshold())
    return timer


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
    # Before the App: its threads, and the proxy's, must never collect.
    collect_on_gui_thread(qapp)
    from .._boot import trcc
    from ..adapters.render.qt import QtRenderer
    return trcc(platform=platform, renderer=QtRenderer())
