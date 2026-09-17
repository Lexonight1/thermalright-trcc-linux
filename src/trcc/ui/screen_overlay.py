"""Screen-grab + frozen-screen overlay primitives.

Two pieces shared by the eyedropper (pick a colour) and any future
region-capture tool (image crop "from screen", screencast region select):

* :func:`grab_full_screen` — best-effort full-display capture that
  works on X11 (Qt native) and Wayland (``grim`` / ``gnome-screenshot``
  / ``scrot`` fallback chain).  Returns a :class:`QPixmap`; null on
  total failure so callers can surface a friendly message instead of
  crashing.

* :class:`BaseScreenOverlay` — a frameless, always-on-top, fullscreen
  widget that paints a frozen screenshot of the desktop.  Subclasses
  override ``paintEvent`` + mouse handlers to layer their interaction
  (magnifier, selection rectangle) on top.  ESC is wired to cancel via
  ``_emit_cancel()``.

Why a frozen screenshot instead of overlaying a transparent window on
the live desktop: cursor-following capture is racy (compositor lag,
sub-pixel artifacts) and on Wayland we can't read pixel colour from an
arbitrary window anyway.  Freezing the screen once is honest about
what the user is picking.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ..core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


@lru_cache(maxsize=8)
def overlay_font(family: str, size: int) -> QFont:
    """A cached :class:`QFont`, built on first paint and never at import.

    A ``QFont`` at class scope is constructed when its module is imported,
    which can happen before ``QApplication`` exists — Qt does not survive
    that, and the crash surfaces in an unrelated test much later.  Plain
    values like ``QColor`` are safe at class scope; fonts are not.
    """
    log.debug("overlay_font: %s %dpt", family, size)
    return QFont(family, size)


@lru_cache(maxsize=1)
def is_wayland() -> bool:
    """``True`` if we're running under a Wayland session."""
    log.debug("is_wayland")
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


_FALLBACK_TOOLS: tuple[str, ...] = ("grim", "spectacle", "gnome-screenshot", "scrot")


def _has_tool(name: str) -> bool:
    log.debug("_has_tool: name=%s", name)
    return shutil.which(name) is not None


def _try_external_capture(tmp_path: str) -> QPixmap:
    """Run a fallback screenshot tool, return what it wrote (or null)."""
    import time
    cmds = {
        "grim": ["grim", tmp_path],
        "spectacle": ["spectacle", "-b", "-n", "-o", tmp_path],
        "gnome-screenshot": ["gnome-screenshot", "-f", tmp_path],
        "scrot": ["scrot", tmp_path],
    }
    for tool in _FALLBACK_TOOLS:
        if not _has_tool(tool):
            log.debug("screen capture: %s not installed", tool)
            continue
        try:
            subprocess.run(
                cmds[tool], capture_output=True, timeout=5, check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("screen capture: %s timed out", tool)
            continue
        for _ in range(20):
            if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
                break
            time.sleep(0.025)
        if not Path(tmp_path).exists() or Path(tmp_path).stat().st_size == 0:
            log.debug("screen capture: %s output file missing or empty", tool)
            continue
        pix = QPixmap(tmp_path)
        if not pix.isNull():
            log.debug("screen capture via %s", tool)
            return pix
    return QPixmap()


def grab_full_screen() -> QPixmap:
    """Capture the full primary screen, X11 + Wayland.

    Tries the Qt native path first (works on X11, sometimes blank on
    Wayland).  Falls back to ``grim`` / ``spectacle`` / ``gnome-screenshot`` / ``scrot``
    in that order.  Returns a null pixmap if every option fails — the
    caller is responsible for surfacing that to the user.
    """
    log.debug("grab_full_screen")
    screen = QApplication.primaryScreen()
    if screen is not None:
        pix = screen.grabWindow(0)  # type: ignore[arg-type]
        if not pix.isNull() and pix.width() > 1:
            return pix

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        Path(tmp_path).unlink(missing_ok=True)
        return _try_external_capture(tmp_path)
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


class BaseScreenOverlay(QWidget):
    """Frameless fullscreen widget that paints a frozen screenshot.

    Subclasses override ``paintEvent`` and the mouse handlers to layer
    interaction on top.  ``_emit_cancel()`` must be implemented to emit
    the subclass's cancel signal — base ESC handling calls it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        log.debug("__init__: parent=%s", parent)
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._screenshot: QPixmap = QPixmap()

    def show(self) -> None:
        """Capture the screen, then show fullscreen.

        If capture fails (null pixmap), we immediately ``_emit_cancel``
        and do not present an empty window — better than showing a
        black screen that swallows clicks.
        """
        self._screenshot = grab_full_screen()
        if self._screenshot.isNull():
            log.warning("screen overlay: capture failed, cancelling")
            self._emit_cancel()
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        log.debug("keyPressEvent: event=%s", event)
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    def _cancel(self) -> None:
        log.debug("_cancel")
        self.hide()
        self._emit_cancel()
        self.deleteLater()

    def _emit_cancel(self) -> None:
        log.debug("_emit_cancel")
        raise NotImplementedError(
            "BaseScreenOverlay subclass must emit its own cancel signal",
        )


class DragSelectOverlay(BaseScreenOverlay):
    """A frozen-screen overlay on which the user drags out a rectangle.

    Owns the whole interaction, so every skin gets the same one: press to
    anchor, drag to size, release to confirm, right-click or ESC to cancel.
    The backdrop dims, the live selection is punched back through it, and a
    size label tracks the rectangle.

    A subclass supplies only what genuinely differs — :meth:`_confirm`,
    which decides what the chosen rectangle *means*: a cropped pixmap for
    the gui capture tool, four ints for the qtgui region picker.

    :class:`EyedropperOverlay` deliberately stays on
    :class:`BaseScreenOverlay`.  It samples one pixel under the cursor and
    never drags out a region, so none of this state is its business.
    """

    _DIM = QColor(0, 0, 0, 120)
    _BORDER = QColor(200, 200, 200)
    _BORDER_W = 2
    _LABEL_BG = QColor(0, 0, 0, 180)
    _LABEL_TEXT = QColor(255, 255, 255)
    _FONT_FAMILY = "sans-serif"
    _LABEL_PT = 11
    _HINT_PT = 14
    _HINT = "Click and drag to choose a region.\nESC to cancel."

    #: A drag shorter than this on either edge is a misclick, not a selection.
    _MIN_EDGE = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selecting = False
        self._start = QPoint()
        self._end = QPoint()
        log.debug("%s.__init__: drag-select overlay built", type(self).__name__)

    # ── Interaction ──────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._selecting = True
            self._start = event.position().toPoint()
            self._end = event.position().toPoint()
            log.debug("%s.mousePressEvent: anchor at (%d, %d)",
                      type(self).__name__, self._start.x(), self._start.y())
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            log.info("%s.mousePressEvent: right-click cancels",
                     type(self).__name__)
            self._cancel()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._end = event.position().toPoint()
            frame_log.debug("%s.mouseMoveEvent: (%d, %d)",
                            type(self).__name__, self._end.x(), self._end.y())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._selecting:
            return
        self._end = event.position().toPoint()
        self._selecting = False
        sel = self._selection_rect()
        if sel.width() >= self._MIN_EDGE and sel.height() >= self._MIN_EDGE:
            log.info("%s.mouseReleaseEvent: %dx%d at (%d, %d) — confirming",
                     type(self).__name__, sel.width(), sel.height(),
                     sel.x(), sel.y())
            self._confirm(sel)
        else:
            log.info("%s.mouseReleaseEvent: %dx%d under the %dpx minimum "
                     "— treated as a misclick",
                     type(self).__name__, sel.width(), sel.height(),
                     self._MIN_EDGE)
            self.update()

    def _selection_rect(self) -> QRect:
        """The dragged rectangle — the same one whichever way the hand moved.

        Built from the sorted corners rather than
        ``QRect(start, end).normalized()``.  Qt's two-point constructor is
        INCLUSIVE of both corners, but ``normalized()`` repairs a negative
        extent by moving both edges inward, so a rectangle dragged up-left
        came out 2px smaller and 1px offset from the identical rectangle
        dragged down-right.  Sorting first means there is never a negative
        extent to repair, and the down-right answer — the one that was
        already right — is unchanged.
        """
        x1, x2 = sorted((self._start.x(), self._end.x()))
        y1, y2 = sorted((self._start.y(), self._end.y()))
        rect = QRect(x1, y1, x2 - x1 + 1, y2 - y1 + 1)
        frame_log.debug("%s._selection_rect: %dx%d at (%d, %d)",
                        type(self).__name__, rect.width(), rect.height(),
                        rect.x(), rect.y())
        return rect

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        del event
        if self._screenshot.isNull():
            frame_log.debug("%s.paintEvent: no screenshot, nothing to paint",
                            type(self).__name__)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self._screenshot)
        painter.fillRect(self.rect(), self._DIM)

        if self._selecting and self._start != self._end:
            sel = self._selection_rect()
            # Punch the live selection back through the dim layer.
            painter.drawPixmap(sel, self._screenshot, sel)
            pen = QPen(self._BORDER, self._BORDER_W)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(sel)
            self._draw_size_label(painter, sel)
        else:
            painter.setPen(self._LABEL_TEXT)
            painter.setFont(overlay_font(self._FONT_FAMILY,
                                         self._HINT_PT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             self._HINT)
        painter.end()

    def _draw_size_label(self, painter: QPainter, sel: QRect) -> None:
        """Draw ``W × H`` just below the selection, or above it near the edge."""
        label = f"{sel.width()} × {sel.height()}"
        painter.setFont(overlay_font(self._FONT_FAMILY,
                                     self._LABEL_PT))
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 12
        height = metrics.height() + 6
        x = sel.center().x() - width // 2
        y = sel.bottom() + 8
        if y + height > self.height():
            y = sel.top() - height - 8
        frame_log.debug("%s._draw_size_label: %r at (%d, %d)",
                        type(self).__name__, label, x, y)
        painter.fillRect(x, y, width, height, self._LABEL_BG)
        painter.setPen(self._LABEL_TEXT)
        painter.drawText(x + 6, y + metrics.ascent() + 3, label)

    # ── Subclass contract ────────────────────────────────────────────

    def _confirm(self, sel: QRect) -> None:
        """Act on the chosen rectangle — hide, emit, ``deleteLater``."""
        log.debug("_confirm: sel=%s", sel)
        raise NotImplementedError(
            "DragSelectOverlay subclass must act on the chosen rectangle",
        )
