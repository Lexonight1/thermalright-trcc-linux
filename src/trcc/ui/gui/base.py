"""
Base widget classes for PySide6 TRCC components.

Provides common functionality:
- BasePanel: delegate pattern, resource loading
- ImageLabel: fast image display
- GuiThumbnail / GuiThemeBrowser: the shared tile grid (``ui/tile_grid``)
  in the gui's look, ``gui_tile_skin``
- create_image_button: flat image button factory
"""

from __future__ import annotations

import logging
from functools import cache

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QWidget,
)

from ...core.logs import per_frame
from ..module_page import ModuleLook
from ..qt_panel import BasePanel as BasePanel
from ..tile_grid import BaseThemeBrowser, BaseThumbnail, TileSkin
from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)
#: The preview repaints and re-images once per rendered frame.
frame_log = per_frame(__name__)



def paint_page(widget: QWidget, title: str) -> None:
    """*widget*'s full-window page: the window's colour, the gold title bar
    with *title* on it, the framed body -- About, RGB Lighting, Thanks and
    Modules all draw this."""
    frame_log.debug("paint_page: %s", title)
    bar = QRect(*Layout.PAGE_TITLE_BAR)
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(widget.rect(), QColor(Colors.WINDOW_BG))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(Colors.PAGE_GOLD))
    p.drawRoundedRect(bar, 6, 6)
    font = QFont()
    font.setPixelSize(30)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#3a3a3a"))
    p.drawText(bar.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, title)
    p.setPen(QPen(QColor(Colors.PAGE_FRAME), 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRect(*Layout.PAGE_BODY), 6, 6)
    p.end()


class ImageLabel(QLabel):
    """
    QLabel optimized for fast image updates.

    Matches Tkinter Canvas image pattern but faster.
    Supports mouse drag for overlay element repositioning.
    """

    clicked = Signal()
    drag_started = Signal(int, int)    # (x, y) in widget coords
    drag_moved = Signal(int, int)      # (x, y) in widget coords
    drag_ended = Signal()
    nudge = Signal(int, int)           # (dx, dy) in pixels (1 or 10)
    double_clicked = Signal()

    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)

        self._width = width
        self._height = height
        self._dragging = False
        #: The last image as it ARRIVED, before scaling -- so a resize (the
        #: widescreen pop-out) redraws at the new size at once instead of
        #: waiting for the next frame, which a static theme never sends.
        self._source: QPixmap | QImage | None = None

        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def resize_area(self, width: int, height: int) -> None:
        """Show the image at a new size -- docked bezel or the pop-out."""
        log.debug("ImageLabel.resize_area: %dx%d -> %dx%d",
                  self._width, self._height, width, height)
        self._width, self._height = width, height
        self.setFixedSize(width, height)
        if self._source is not None:
            self.set_image(self._source)

    def set_image(self, image, fast: bool = False):
        """Set image from QPixmap or QImage."""
        self._source = image
        if image is None:
            frame_log.debug("set_image: None — preview cleared")
            self.clear()
            return

        if isinstance(image, QPixmap):
            scaled = (image.width(), image.height()) != (self._width,
                                                         self._height)
            frame_log.debug("set_image: QPixmap %dx%d -> %dx%d (scaled=%s "
                            "fast=%s)", image.width(), image.height(),
                            self._width, self._height, scaled, fast)
            if scaled:
                mode = (Qt.TransformationMode.FastTransformation if fast
                        else Qt.TransformationMode.SmoothTransformation)
                image = image.scaled(self._width, self._height,
                                     Qt.AspectRatioMode.IgnoreAspectRatio, mode)
            self.setPixmap(image)
            return

        if isinstance(image, QImage):
            scaled = (image.width(), image.height()) != (self._width,
                                                         self._height)
            frame_log.debug("set_image: QImage %dx%d -> %dx%d (scaled=%s "
                            "fast=%s)", image.width(), image.height(),
                            self._width, self._height, scaled, fast)
            if scaled:
                mode = (Qt.TransformationMode.FastTransformation if fast
                        else Qt.TransformationMode.SmoothTransformation)
                image = image.scaled(
                    self._width, self._height,
                    Qt.AspectRatioMode.IgnoreAspectRatio, mode)
            self.setPixmap(QPixmap.fromImage(image))

    def set_rgb565(self, data: bytes, width: int, height: int,
                   byte_order: str = '>'):
        """Set image from pre-converted RGB565 bytes.

        Creates QPixmap directly from the RGB565 bytes sent to the LCD device.
        """
        pixmap = rgb565_to_pixmap(data, width, height, byte_order)
        self._source = pixmap
        if (width, height) != (self._width, self._height):
            pixmap = pixmap.scaled(
                self._width, self._height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pixmap)

    def mousePressEvent(self, event):
        """Handle mouse click — start drag."""
        self.clicked.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            log.info("ImageLabel.mousePressEvent: drag start at (%d,%d)",
                     pos.x(), pos.y())
            self.drag_started.emit(pos.x(), pos.y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse drag."""
        if self._dragging:
            pos = event.position().toPoint()
            # Per-event during drag — DEBUG.
            self.drag_moved.emit(pos.x(), pos.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle drag end."""
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            log.info("ImageLabel.mouseReleaseEvent: drag end")
            self.drag_ended.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """A double-click -- the C#'s pop-out gesture on a widescreen panel."""
        log.info("ImageLabel.mouseDoubleClickEvent: %s", event.button())
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        """WASD/arrow nudge: 1px normal, 10px with Shift (C# UCScreenImage)."""
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        key = event.key()
        if key in (Qt.Key.Key_W, Qt.Key.Key_Up):
            log.info("ImageLabel.keyPressEvent: nudge (0,-%d)", step)
            self.nudge.emit(0, -step)
        elif key in (Qt.Key.Key_S, Qt.Key.Key_Down):
            log.info("ImageLabel.keyPressEvent: nudge (0,+%d)", step)
            self.nudge.emit(0, step)
        elif key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            log.info("ImageLabel.keyPressEvent: nudge (-%d,0)", step)
            self.nudge.emit(-step, 0)
        elif key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            log.info("ImageLabel.keyPressEvent: nudge (+%d,0)", step)
            self.nudge.emit(step, 0)
        else:
            super().keyPressEvent(event)


def rgb565_to_pixmap(data: bytes, width: int, height: int,
                     byte_order: str = '>') -> QPixmap:
    """Create QPixmap from RGB565 bytes.

    Qt Format_RGB16 expects native byte order (little-endian on x86).
    Device byte order is swapped to native if needed.
    """
    import sys

    import numpy as np

    if byte_order == '>' and sys.byteorder == 'little':
        buf = np.frombuffer(data, dtype='>u2').astype('<u2').tobytes()
    elif byte_order == '<' and sys.byteorder == 'big':
        buf = np.frombuffer(data, dtype='<u2').astype('>u2').tobytes()
    else:
        buf = bytes(data)

    qimage = QImage(buf, width, height, width * 2,
                    QImage.Format.Format_RGB16)
    return QPixmap.fromImage(qimage)


class _BgPaintFilter(QObject):
    """Event filter that paints a background pixmap once at (0,0).

    Matches Windows WinForms BackgroundImageLayout.None — draw once at
    top-left, no tiling, no stretching.  Avoids QBrush texture tiling.
    """

    def __init__(self, pixmap: QPixmap, parent: QObject | None = None):
        super().__init__(parent)
        self._pixmap = pixmap

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Logged on the Paint branch ONLY, and deliberately.  Qt calls this for
        # EVERY event on the watched widget — mouse moves included — and every
        # one but Paint falls straight through to ``return False``.  An entry
        # line would be a firehose at -vvv reporting that nothing happened;
        # this reports the repaint that did.
        if event.type() == QEvent.Type.Paint and isinstance(obj, QWidget):
            frame_log.debug("background repaint: %dx%d pixmap on %s",
                            self._pixmap.width(), self._pixmap.height(),
                            type(obj).__name__)
            painter = QPainter(obj)
            painter.drawPixmap(0, 0, self._pixmap)
            painter.end()
            return True
        return False


def set_background_pixmap(widget, asset_name, width=None, height=None,
                          fallback_style=None):
    """Apply a background image to a widget (no tiling).

    Uses a paint event filter to draw the pixmap once at (0,0), matching
    Windows BackgroundImageLayout.None.  Does NOT use QPalette+QBrush
    (which tiles), and does NOT use setStyleSheet (which blocks QPalette
    on descendants).

    Args:
        widget: QWidget to set background on.
        asset_name: Asset filename (or pre-loaded QPixmap).
        width: Scale width (defaults to widget width).
        height: Scale height (defaults to widget height).
        fallback_style: CSS to apply if image not found.

    Returns:
        The QPixmap if successfully set, or None.
    """
    from .assets import Assets

    if isinstance(asset_name, QPixmap):
        pixmap = asset_name
    else:
        w = width or widget.width() or None
        h = height or widget.height() or None
        pixmap = Assets.load_pixmap(asset_name, w, h)

    if pixmap and not pixmap.isNull():
        # Remove any previous background filter to avoid stale paint
        for child in widget.children():
            if isinstance(child, _BgPaintFilter):
                widget.removeEventFilter(child)
                child.deleteLater()
        filt = _BgPaintFilter(pixmap, widget)
        widget.installEventFilter(filt)
        widget.setAutoFillBackground(False)
        widget.update()
        return pixmap

    if fallback_style:
        widget.setStyleSheet(fallback_style)
    return None


# ============================================================================
# Shared utilities
# ============================================================================

class SkinButton(QPushButton):
    """A skin button that keeps its state pixmaps.

    ``_img_refs`` holds the pixmaps its icon states were built from -- read
    back to swap an icon (the preview's play/pause) and kept so they outlive
    the call that made them.  On the device rail it also carries the device
    it stands for.  Both used to be attributes set on a bare QPushButton.
    """

    _img_refs: list[QPixmap | None] | None = None
    device_info: dict | None = None


def create_image_button(parent, x, y, w, h, normal_img, active_img,
                        checkable=False, fallback_text=None):
    """Create a flat image button matching Windows style.

    Args:
        parent: Parent widget
        x, y, w, h: Geometry
        normal_img: Normal state image filename
        active_img: Active/hover state image filename
        checkable: Whether button is checkable (toggle)
        fallback_text: Text to show if images not found

    Returns:
        QPushButton
    """
    from .assets import Assets

    btn = SkinButton(parent)
    btn.setGeometry(x, y, w, h)
    btn.setFlat(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(Styles.FLAT_BUTTON)

    if checkable:
        btn.setCheckable(True)

    normal_pix = Assets.load_pixmap(normal_img, w, h) if normal_img else None
    active_pix = Assets.load_pixmap(active_img, w, h) if active_img else None

    if normal_pix and not normal_pix.isNull():
        icon = QIcon(normal_pix)
        if active_pix and not active_pix.isNull():
            if checkable:
                icon.addPixmap(active_pix, QIcon.Mode.Normal, QIcon.State.On)
            else:
                icon.addPixmap(active_pix, QIcon.Mode.Active)
        btn.setIcon(icon)
        btn.setIconSize(btn.size())
        # Store pixmaps to prevent GC
        btn._img_refs = [normal_pix, active_pix]
    elif fallback_text:
        btn.setText(fallback_text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.DEVICE_NORMAL_BOTTOM}; color: #AAA;
                border: 1px solid {Colors.DEVICE_NORMAL_BORDER};
                border-radius: 3px; font-size: 10px;
            }}
            QPushButton:hover {{ background: {Colors.DEVICE_NORMAL_TOP}; color: white; }}
            QPushButton:checked {{
                background: {Colors.DEVICE_SELECTED_TOP}; color: white;
                font-weight: bold;
                border: 2px solid {Colors.ACCENT_BORDER};
            }}
        """)

    return btn


def make_icon_button(parent, rect, img_name, fallback, handler):
    """Create an icon button with text fallback.

    Used by cut panels (UCImageCut, UCVideoCut) for toolbar buttons.

    Args:
        parent: Parent widget
        rect: (x, y, w, h) geometry tuple
        img_name: Image filename for icon
        fallback: Text to show if image not found
        handler: Click handler to connect

    Returns:
        QPushButton
    """
    from .assets import Assets

    btn = QPushButton(parent)
    btn.setGeometry(*rect)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    pix = Assets.load_pixmap(img_name, rect[2], rect[3])
    if not pix.isNull():
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(rect[2], rect[3]))
        btn.setStyleSheet(Styles.FLAT_BUTTON_HOVER)
    else:
        btn.setText(fallback)
        btn.setStyleSheet(Styles.TEXT_BUTTON)
    btn.clicked.connect(handler)
    return btn


# ============================================================================
# The gui's tile grid -- the shared one (``ui/tile_grid``) in the gui's look
# ============================================================================

@cache
def gui_tile_skin() -> TileSkin:
    """The gui's tiles: its own colours and styles, the values the grid has
    always drawn with."""
    log.debug("gui_tile_skin: built")
    return TileSkin(
        image=Styles.THUMB_IMAGE,
        name=Styles.THUMB_NAME,
        scroll_area=Styles.SCROLL_AREA,
        selected=Styles.thumb_selected,
        normal=Styles.thumb_normal,
        not_local=Styles.thumb_non_local,
        placeholder=Colors.PLACEHOLDER_BG,
        placeholder_text=(100, 100, 100),
        empty_text=Colors.EMPTY_TEXT,
    )


@cache
def gui_module_look() -> ModuleLook:
    """The gui's look for a module's page (``ui/module_page``): its tiles,
    and the controls in its own colours -- the groove shows on the dark
    panel, which the shared slider style leaves transparent."""
    log.debug("gui_module_look: built")
    return ModuleLook(
        tiles=gui_tile_skin(),
        controls=(f"QLabel {{ color: {Colors.TEXT}; background: transparent; }}"
                  f"QPushButton {{ background: {Colors.BUTTON_BG}; color: "
                  f"{Colors.BUTTON_TEXT}; border: 1px solid {Colors.HOVER_BORDER}; "
                  f"padding: 4px 12px; }}"
                  f"QPushButton:checked {{ background: {Colors.PAGE_GOLD}; color: #2b2b2b; "
                  f"border-color: {Colors.PAGE_GOLD}; }}"
                  # A pick reads as one: a field and its arrow, as the font select.
                  f"QComboBox {{ background-color: rgba(0, 0, 0, 70); color: {Colors.TEXT}; "
                  f"border: 1px solid #555; border-radius: 3px; padding: 2px 8px; }}"
                  "QComboBox:disabled { color: #666; }"
                  "QComboBox QAbstractItemView { background-color: #2b2b2b; color: white; "
                  "selection-background-color: #4a4a4a; }"
                  + Styles.SLIDER
                  + f"QSlider::groove:horizontal {{ background: {Colors.BUTTON_BG}; "
                    f"height: 6px; border-radius: 3px; }}"),
        dimmed=Colors.EMPTY_TEXT,
        status=f"color: {Colors.TEXT}; background: transparent; font-size: 12px;",
    )


class GuiThumbnail(BaseThumbnail):
    """A tile of the gui's theme tabs, in the gui's look."""

    def __init__(self, item_info, parent=None):
        log.debug("GuiThumbnail.__init__: %s", type(self).__name__)
        super().__init__(item_info, gui_tile_skin(), parent)


class GuiThemeBrowser(BaseThemeBrowser):
    """A gui theme tab: the shared grid in the gui's look, and the gui's
    filter buttons above it."""

    def __init__(self, parent=None):
        log.debug("GuiThemeBrowser.__init__: %s", type(self).__name__)
        super().__init__(parent, skin=gui_tile_skin())

    def _load_filter_assets(self):
        """Load shared filter button pixmaps (normal + active)."""
        log.debug("%s._load_filter_assets", type(self).__name__)
        from .assets import Assets
        normal = Assets.load_pixmap('theme_browser_filter.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
        active = Assets.load_pixmap('theme_browser_filter_active.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
        return normal, active

    def _make_filter_button(self, x, y, w, h, normal_pix, active_pix, callback):
        """Create a flat checkable filter button with icon states."""
        log.debug("%s._make_filter_button: at %s", type(self).__name__, (x, y))
        btn = QPushButton(self)
        btn.setGeometry(x, y, w, h)
        btn.setFlat(True)
        btn.setCheckable(True)
        btn.setStyleSheet(Styles.FLAT_BUTTON)
        if not normal_pix.isNull():
            icon = QIcon(normal_pix)
            icon.addPixmap(active_pix, QIcon.Mode.Normal, QIcon.State.On)
            btn.setIcon(icon)
            btn.setIconSize(btn.size())
        btn.clicked.connect(callback)
        return btn



# ============================================================================
# Downloadable Theme Browser
# ============================================================================

class DownloadableThemeBrowser(GuiThemeBrowser):
    """Base for theme browsers that download content on-demand.

    Adds shared download infrastructure:
    - download_started/download_finished signals
    - _downloading guard flag
    - _start_download() runs a callable in a background thread

    Subclasses override _on_download_complete() to refresh their grid.
    """

    import threading as _threading

    download_started = Signal(str)        # item_id
    download_finished = Signal(str, bool)  # item_id, success

    def __init__(self, parent=None):
        self._downloading = False
        super().__init__(parent)
        self.download_finished.connect(self._on_download_complete)

    def _start_download(self, item_id: str, download_fn):
        """Run download_fn() in a background thread, emit signals.

        Args:
            item_id: Identifier for the item being downloaded.
            download_fn: Callable that returns True on success, False on failure.
        """
        log.info("%s._start_download: item_id=%s",
                 type(self).__name__, item_id)
        self._downloading = True
        self.download_started.emit(item_id)

        def task():
            try:
                ok = download_fn()
                log.info("%s._start_download: %s ok=%s",
                         type(self).__name__, item_id, ok)
                self.download_finished.emit(item_id, ok)
            except Exception as e:
                log.error("%s._start_download: %s raised %s: %s",
                          type(self).__name__, item_id, type(e).__name__, e)
                self.download_finished.emit(item_id, False)

        self._threading.Thread(target=task, daemon=True).start()

    def _on_download_complete(self, item_id: str, success: bool):
        """Handle download completion. Override to refresh + auto-select."""
        log.info("%s._on_download_complete: item_id=%s success=%s",
                 type(self).__name__, item_id, success)
        self._downloading = False
