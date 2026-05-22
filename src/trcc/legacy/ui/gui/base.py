"""
Base widget classes for PySide6 TRCC components.

Provides common functionality:
- BasePanel: delegate pattern, resource loading
- ImageLabel: fast image display
- ClickableFrame: QFrame with clicked signal
- BaseThumbnail: shared thumbnail widget (120x140)
- BaseThemeBrowser: shared scroll+grid browser panel (732x652)
- create_image_button: flat image button factory
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)



class BasePanel(QFrame):
    """Base class for TRCC panels.

    Enforces lifecycle:
        _setup_ui() — abstract, must be implemented by every subclass.
        apply_language(lang) — virtual hook, default no-op.
        get_state() / set_state() — virtual hooks, default no-op/empty.

    Provides:
        invoke_delegate() — delegate signal emission (MVC pattern).
        _apply_background(asset_name) — background image helper.
        start_periodic_updates() / stop_periodic_updates() — timer management.
    """

    # Signal for delegate pattern (replaces Tkinter invoke_delegate)
    delegate = Signal(int, object, object)  # cmd, info, data

    def __init__(self, parent=None, width=None, height=None):
        super().__init__(parent)

        # No default stylesheet - let each panel set its own background
        # either via setStyleSheet() or QPalette for image backgrounds

        # Fixed size if specified (matches Windows component sizes)
        if width and height:
            self.setFixedSize(width, height)
        elif width:
            self.setFixedWidth(width)
        elif height:
            self.setFixedHeight(height)

        # Resource directory (legacy)
        self._resource_dir = None
        # Periodic update timer
        self._update_timer: QTimer | None = None

    def __init_subclass__(cls, **kwargs):
        """Enforce that concrete subclasses implement _setup_ui()."""
        super().__init_subclass__(**kwargs)
        # Skip enforcement on intermediate abstract classes (e.g. BaseThemeBrowser)
        # by checking if _setup_ui is still the base stub or a real override.
        if '_setup_ui' not in cls.__dict__ and not any(
            '_setup_ui' in base.__dict__ for base in cls.__mro__[1:]
            if base is not BasePanel
        ):
            raise TypeError(
                f"{cls.__name__} must implement _setup_ui()"
            )

    def _setup_ui(self) -> None:
        """Create child widgets and lay out the panel.

        Called by subclass __init__ (not called automatically by BasePanel).
        Must be overridden by every concrete subclass.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _setup_ui()"
        )

    # === Virtual hooks (default no-op) ===

    def apply_language(self, lang: str) -> None:
        """Update localized text/images for the given language.

        Override in panels that have localized content.
        """

    def get_state(self) -> dict:
        """Serialize panel state for save/restore."""
        return {}

    def set_state(self, state: dict) -> None:
        """Restore panel state from a previously saved dict."""

    # === Concrete helpers ===

    def _apply_background(self, asset_name: str) -> QPixmap | None:
        """Apply a background image using set_background_pixmap."""
        return set_background_pixmap(self, asset_name)

    def start_periodic_updates(
        self, interval_ms: int, callback: Callable[[], None],
    ) -> None:
        """Start a periodic timer calling `callback` every `interval_ms`.

        Creates a QTimer on first call. Subsequent calls restart with
        the new interval and callback.
        """
        if self._update_timer is None:
            self._update_timer = QTimer(self)
        else:
            self._update_timer.stop()
            try:
                self._update_timer.timeout.disconnect()
            except RuntimeError:
                pass
        self._update_timer.timeout.connect(callback)
        self._update_timer.start(interval_ms)

    def stop_periodic_updates(self) -> None:
        """Stop the periodic update timer if running."""
        if self._update_timer is not None:
            self._update_timer.stop()

    # === Legacy ===

    def set_resource_dir(self, path):
        """Set the resource directory for loading images."""
        self._resource_dir = Path(path) if path else None

    def load_pixmap(self, name):
        """Load a pixmap from the resource directory."""
        if not self._resource_dir:
            return None

        path = self._resource_dir / name
        if path.exists():
            return QPixmap(path.as_posix())
        return None

    def invoke_delegate(self, cmd, info=None, data=None):
        """Emit delegate signal (replaces Tkinter invoke_delegate)."""
        self.delegate.emit(cmd, info, data)


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

    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)

        self._width = width
        self._height = height
        self._dragging = False

        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_image(self, image, fast: bool = False):
        """Set image from QPixmap or QImage."""
        if image is None:
            self.clear()
            return

        if isinstance(image, QPixmap):
            if (image.width(), image.height()) != (self._width, self._height):
                mode = (Qt.TransformationMode.FastTransformation if fast
                        else Qt.TransformationMode.SmoothTransformation)
                image = image.scaled(self._width, self._height,
                                     Qt.AspectRatioMode.IgnoreAspectRatio, mode)
            self.setPixmap(image)
            return

        if isinstance(image, QImage):
            if (image.width(), image.height()) != (self._width, self._height):
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
            self.drag_started.emit(pos.x(), pos.y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse drag."""
        if self._dragging:
            pos = event.position().toPoint()
            self.drag_moved.emit(pos.x(), pos.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle drag end."""
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_ended.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """WASD/arrow nudge: 1px normal, 10px with Shift (C# UCScreenImage)."""
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        key = event.key()
        if key in (Qt.Key.Key_W, Qt.Key.Key_Up):
            self.nudge.emit(0, -step)
        elif key in (Qt.Key.Key_S, Qt.Key.Key_Down):
            self.nudge.emit(0, step)
        elif key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self.nudge.emit(-step, 0)
        elif key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self.nudge.emit(step, 0)
        else:
            super().keyPressEvent(event)


class ClickableFrame(QFrame):
    """QFrame that emits clicked signal."""

    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


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
        if event.type() == QEvent.Type.Paint:
            painter = QPainter(obj)  # type: ignore[arg-type]
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

    btn = QPushButton(parent)
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
        btn._img_refs = [normal_pix, active_pix]  # type: ignore[attr-defined]
    elif fallback_text:
        btn.setText(fallback_text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.DEVICE_NORMAL_BOTTOM}; color: #AAA;
                border: 1px solid {Colors.DEVICE_NORMAL_BORDER};
                border-radius: 3px; font-size: 10px;
            }}
            QPushButton:hover {{ background: {Colors.DEVICE_NORMAL_TOP}; color: white; }}
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
# Base Thumbnail
# ============================================================================

class BaseThumbnail(ClickableFrame):
    """
    Base class for all theme/mask thumbnail widgets (120x140).

    Subclasses can override:
    - _get_display_name(info) -> str
    - _get_image_path(info) -> str | None
    - _get_extra_style() -> str | None  (for non-local dashed border etc.)
    - _show_placeholder()
    """

    clicked = Signal(object)

    def __init__(self, item_info, parent=None):
        super().__init__(parent)
        self.item_info = item_info
        self.is_local: bool = getattr(item_info, 'is_local', True)
        self.selected = False

        self.setFixedSize(Sizes.THUMB_W, Sizes.THUMB_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Thumbnail image
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet(Styles.THUMB_IMAGE)
        layout.addWidget(self.thumb_label)

        # Name label
        name = self._get_display_name(item_info)
        if len(name) > Sizes.THUMB_NAME_MAX:
            name = name[:Sizes.THUMB_NAME_TRUNC] + "..."
        self.name_label = QLabel(name)
        self.name_label.setFixedHeight(Sizes.THUMB_NAME_H)
        self.name_label.setStyleSheet(Styles.THUMB_NAME)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)

        self._load_thumbnail()

    def _get_display_name(self, info) -> str:
        """Extract display name from item. Override for custom field."""
        return getattr(info, 'name', 'Unknown')

    def _get_image_path(self, info) -> str | None:
        """Extract image path from item. Override for custom field."""
        return getattr(info, 'thumbnail', None)

    def _get_extra_style(self) -> str | None:
        """Return dashed-border style for non-local (downloadable) items."""
        if not self.is_local:
            return Styles.thumb_non_local(type(self).__name__)
        return None

    def _load_thumbnail(self):
        """Load thumbnail image into thumb_label."""
        path = self._get_image_path(self.item_info)
        if path and Path(path).exists():
            try:
                thumb_size = Sizes.THUMB_IMAGE
                src = QImage(str(path))
                if src.isNull():
                    raise ValueError("QImage failed to load")
                scaled = src.scaled(
                    thumb_size, thumb_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                bg = QImage(thumb_size, thumb_size, QImage.Format.Format_RGB32)
                bg.fill(QColor(0, 0, 0))
                px = (thumb_size - scaled.width()) // 2
                py = (thumb_size - scaled.height()) // 2
                painter = QPainter(bg)
                painter.drawImage(px, py, scaled)
                painter.end()
                self.thumb_label.setPixmap(QPixmap.fromImage(bg))
            except Exception as exc:
                log.warning("Failed to load thumbnail: %s", exc)
                self._show_placeholder()
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        """Show a labeled placeholder when thumbnail image is missing."""
        try:
            thumb_size = Sizes.THUMB_IMAGE
            bg = QImage(thumb_size, thumb_size, QImage.Format.Format_RGB32)
            bg.fill(QColor(*Colors.PLACEHOLDER_BG))
            name = self._get_display_name(self.item_info)
            text = f"\u2b07\n{name}" if not self.is_local else name
            painter = QPainter(bg)
            painter.setPen(QColor(100, 100, 100))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(
                QRect(0, 0, thumb_size, thumb_size),
                Qt.AlignmentFlag.AlignCenter,
                text)
            painter.end()
            self.thumb_label.setPixmap(QPixmap.fromImage(bg))
        except (OSError, RuntimeError) as e:
            log.debug("base: text-thumbnail rendering failed: %s", e)

    def _update_style(self):
        cls_name = type(self).__name__
        if self.selected:
            self.setStyleSheet(Styles.thumb_selected(cls_name))
        else:
            extra = self._get_extra_style()
            if extra:
                self.setStyleSheet(extra)
            else:
                self.setStyleSheet(Styles.thumb_normal(cls_name))

    def set_selected(self, selected):
        self.selected = selected
        self._update_style()

    def mousePressEvent(self, event):
        self.clicked.emit(self.item_info)
        # Don't call super - we override clicked signal with different signature


# ============================================================================
# Base Theme Browser
# ============================================================================

class BaseThemeBrowser(BasePanel):
    """
    Base class for theme/mask browser panels (732x652).

    Provides: scroll area, grid layout, thumbnail management, selection.

    For browsers that download content on-demand, use DownloadableThemeBrowser
    instead — it adds download_started/download_finished signals and a
    _start_download() template method.

    Subclasses override:
    - _create_filter_buttons(): Create filter/category buttons above grid
    - _create_thumbnail(item_info) -> BaseThumbnail: Factory method
    - _no_items_message() -> str: Empty state message
    """

    theme_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent, width=Sizes.PANEL_W, height=Sizes.PANEL_H)

        self.items: list = []
        self.item_widgets: list = []
        self.selected_item = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """BaseThemeBrowser UI: scroll area + filter buttons."""
        self._setup_base_ui()
        self._create_filter_buttons()

    def _setup_base_ui(self):
        """Create scroll area and grid (shared by all browsers)."""
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setGeometry(*Layout.THEME_SCROLL)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setStyleSheet(Styles.SCROLL_AREA)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background-color: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(*Sizes.GRID_MARGIN)
        self.grid_layout.setHorizontalSpacing(Sizes.GRID_H_SPACE)
        self.grid_layout.setVerticalSpacing(Sizes.GRID_V_SPACE)
        self.grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.scroll_area.setWidget(self.grid_container)

    def _create_filter_buttons(self):
        """Override to add filter/category buttons above the grid."""

    def _load_filter_assets(self):
        """Load shared filter button pixmaps (normal + active)."""
        from .assets import Assets
        normal = Assets.load_pixmap('theme_browser_filter.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
        active = Assets.load_pixmap('theme_browser_filter_active.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
        return normal, active

    def _make_filter_button(self, x, y, w, h, normal_pix, active_pix, callback):
        """Create a flat checkable filter button with icon states."""
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

    def _create_thumbnail(self, item_info: dict) -> BaseThumbnail:
        """Override to create the appropriate thumbnail widget."""
        raise NotImplementedError

    def _no_items_message(self) -> str:
        """Override to provide custom empty-state message."""
        return "No items found"

    def _clear_grid(self):
        """Clear all widgets from the grid."""
        for widget in self.item_widgets:
            widget.deleteLater()
        self.item_widgets.clear()
        self.items.clear()

    def _populate_grid(self, items: list):
        """Populate grid with thumbnails for the given items."""
        self.items = items

        if not items:
            self._show_empty_message()
            return

        for i, item_info in enumerate(items):
            row = i // Sizes.GRID_COLS
            col = i % Sizes.GRID_COLS
            thumb = self._create_thumbnail(item_info)
            thumb.clicked.connect(self._on_item_clicked)
            self.grid_layout.addWidget(thumb, row, col)
            self.item_widgets.append(thumb)

    def _show_empty_message(self):
        """Show empty state label."""
        label = QLabel(self._no_items_message())
        label.setStyleSheet(
            f"color: {Colors.EMPTY_TEXT}; font-size: 12px; background: transparent;"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid_layout.addWidget(label, 0, 0, 1, Sizes.GRID_COLS)
        self.item_widgets.append(label)

    def _select_item(self, item_info):
        """Update selection state and visuals (no signals emitted)."""
        self.selected_item = item_info
        for widget in self.item_widgets:
            if isinstance(widget, BaseThumbnail):
                widget.set_selected(widget.item_info == item_info)

    def _on_item_clicked(self, item_info):
        """Handle thumbnail click — select and notify."""
        self._select_item(item_info)
        self.theme_selected.emit(item_info)

    def get_selected(self):
        return self.selected_item


# ============================================================================
# Downloadable Theme Browser
# ============================================================================

class DownloadableThemeBrowser(BaseThemeBrowser):
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
        self._downloading = True
        self.download_started.emit(item_id)

        def task():
            try:
                ok = download_fn()
                self.download_finished.emit(item_id, ok)
            except Exception as e:
                log.error("Download failed for %s: %s", item_id, e)
                self.download_finished.emit(item_id, False)

        self._threading.Thread(target=task, daemon=True).start()

    def _on_download_complete(self, item_id: str, success: bool):
        """Handle download completion. Override to refresh + auto-select."""
        self._downloading = False
