"""
Base widget classes for PyQt6 TRCC components.

Provides common functionality:
- BasePanel: delegate pattern, resource loading
- ImageLabel: fast PIL image display
- ClickableFrame: QFrame with clicked signal
- BaseThumbnail: shared thumbnail widget (120x140)
- BaseThemeBrowser: shared scroll+grid browser panel (732x652)
- create_image_button: flat image button factory
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QIcon, QImage, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .constants import Colors, Layout, Sizes, Styles

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class BasePanel(QFrame):
    """
    Base class for TRCC panels.

    Matches Tkinter component pattern with delegate callbacks.
    """

    # Signal for delegate pattern (replaces Tkinter invoke_delegate)
    delegate = pyqtSignal(int, object, object)  # cmd, info, data

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

        # Resource directory
        self._resource_dir = None

    def set_resource_dir(self, path):
        """Set the resource directory for loading images."""
        self._resource_dir = Path(path) if path else None

    def load_pixmap(self, name):
        """Load a pixmap from the resource directory."""
        if not self._resource_dir:
            return None

        path = self._resource_dir / name
        if path.exists():
            return QPixmap(str(path))
        return None

    def invoke_delegate(self, cmd, info=None, data=None):
        """Emit delegate signal (replaces Tkinter invoke_delegate)."""
        self.delegate.emit(cmd, info, data)


class ImageLabel(QLabel):
    """
    QLabel optimized for fast image updates.

    Matches Tkinter Canvas image pattern but faster.
    """

    clicked = pyqtSignal()

    def __init__(self, width=320, height=320, parent=None):
        super().__init__(parent)

        self._width = width
        self._height = height

        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")

    def set_pil_image(self, pil_image):
        """Set image from PIL Image - fast conversion path."""
        if pil_image is None:
            self.clear()
            return

        if pil_image.size != (self._width, self._height):
            pil_image = pil_image.resize(
                (self._width, self._height),
                Image.Resampling.LANCZOS
            )

        self.setPixmap(pil_to_pixmap(pil_image))

    def mousePressEvent(self, event):
        """Handle mouse click."""
        self.clicked.emit()
        super().mousePressEvent(event)


class ClickableFrame(QFrame):
    """QFrame that emits clicked signal."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


def pil_to_pixmap(pil_image):
    """
    Convert PIL Image to QPixmap efficiently.

    Args:
        pil_image: PIL Image

    Returns:
        QPixmap
    """
    if pil_image is None:
        return QPixmap()

    # Convert to RGB if needed
    if pil_image.mode == 'RGBA':
        bg = Image.new('RGB', pil_image.size, (0, 0, 0))
        bg.paste(pil_image, mask=pil_image.split()[3])
        pil_image = bg
    elif pil_image.mode != 'RGB':
        pil_image = pil_image.convert('RGB')

    data = pil_image.tobytes()
    qimage = QImage(
        data,
        pil_image.width,
        pil_image.height,
        pil_image.width * 3,
        QImage.Format.Format_RGB888
    )
    return QPixmap.fromImage(qimage)


def pixmap_to_pil(pixmap):
    """Convert QPixmap to PIL Image.

    Args:
        pixmap: QPixmap

    Returns:
        PIL Image (RGB)
    """
    qimage = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
    width = qimage.width()
    height = qimage.height()
    ptr = qimage.bits()
    ptr.setsize(qimage.sizeInBytes())
    return Image.frombytes('RGB', (width, height), bytes(ptr), 'raw', 'RGB',
                           qimage.bytesPerLine())


def set_background_pixmap(widget, asset_name, width=None, height=None,
                          fallback_style=None):
    """Apply a background image to a widget via QPalette.

    Uses QPalette+QBrush (not stylesheet) to avoid blocking palette
    propagation to child widgets.

    Args:
        widget: QWidget to set background on.
        asset_name: Asset filename (or pre-loaded QPixmap).
        width: Scale width (defaults to widget width).
        height: Scale height (defaults to widget height).
        fallback_style: CSS to apply if image not found.

    Returns:
        The QPixmap if successfully set, or None.
    """
    from .assets import load_pixmap as _load

    if isinstance(asset_name, QPixmap):
        pixmap = asset_name
    else:
        w = width or widget.width() or None
        h = height or widget.height() or None
        pixmap = _load(asset_name, w, h)

    if pixmap and not pixmap.isNull():
        palette = widget.palette()
        palette.setBrush(QPalette.ColorRole.Window, QBrush(pixmap))
        widget.setPalette(palette)
        widget.setAutoFillBackground(True)
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
    from .assets import load_pixmap

    btn = QPushButton(parent)
    btn.setGeometry(x, y, w, h)
    btn.setFlat(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(Styles.FLAT_BUTTON)

    if checkable:
        btn.setCheckable(True)

    normal_pix = load_pixmap(normal_img, w, h) if normal_img else None
    active_pix = load_pixmap(active_img, w, h) if active_img else None

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
    from .assets import load_pixmap

    btn = QPushButton(parent)
    btn.setGeometry(*rect)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    pix = load_pixmap(img_name, rect[2], rect[3])
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

    clicked = pyqtSignal(dict)

    def __init__(self, item_info: dict, parent=None):
        super().__init__(parent)
        self.item_info = item_info
        self.is_local: bool = item_info.get('is_local', True)
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

    def _get_display_name(self, info: dict) -> str:
        """Extract display name from info dict. Override for custom key."""
        return info.get('name', 'Unknown')

    def _get_image_path(self, info: dict) -> str | None:
        """Extract image path from info dict. Override for custom key."""
        return info.get('thumbnail')

    def _get_extra_style(self) -> str | None:
        """Return dashed-border style for non-local (downloadable) items."""
        if not self.is_local:
            return Styles.thumb_non_local(type(self).__name__)
        return None

    def _load_thumbnail(self):
        """Load thumbnail image into thumb_label."""
        if not PIL_AVAILABLE:
            return
        path = self._get_image_path(self.item_info)
        if path and Path(path).exists():
            try:
                img = Image.open(path)
                size = (Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE)
                img.thumbnail(size, Image.Resampling.LANCZOS)
                bg = Image.new('RGB', size, (0, 0, 0))
                offset = ((size[0] - img.width) // 2,
                          (size[1] - img.height) // 2)
                bg.paste(img, offset)
                self.thumb_label.setPixmap(pil_to_pixmap(bg))
            except Exception as e:
                print(f"[!] Failed to load thumbnail: {e}")
                self._show_placeholder()
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        """Show a labeled placeholder when thumbnail image is missing."""
        if not PIL_AVAILABLE:
            return
        try:
            from PIL import ImageDraw
            size = (Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE)
            img = Image.new('RGB', size, color=Colors.PLACEHOLDER_BG)
            draw = ImageDraw.Draw(img)
            name = self._get_display_name(self.item_info)
            text = f"\u2b07\n{name}" if not self.is_local else name
            draw.text((size[0] // 2, size[1] // 2),
                     text, fill=(100, 100, 100), anchor='mm', align='center')
            self.thumb_label.setPixmap(pil_to_pixmap(img))
        except Exception:
            pass

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

    Subclasses override:
    - _create_filter_buttons(): Create filter/category buttons above grid
    - _create_thumbnail(item_info) -> BaseThumbnail: Factory method
    - _no_items_message() -> str: Empty state message
    """

    theme_selected = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent, width=Sizes.PANEL_W, height=Sizes.PANEL_H)

        self.items = []
        self.item_widgets = []
        self.selected_item = None

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
        pass

    def _load_filter_assets(self):
        """Load shared filter button pixmaps (normal + active)."""
        from .assets import load_pixmap as _load
        normal = _load('P主题分类选择.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
        active = _load('P主题分类选择0.png', Sizes.FILTER_BTN_W, Sizes.FILTER_BTN_H)
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

    def _select_item(self, item_info: dict):
        """Update selection state and visuals (no signals emitted)."""
        self.selected_item = item_info
        for widget in self.item_widgets:
            if isinstance(widget, BaseThumbnail):
                widget.set_selected(widget.item_info == item_info)

    def _on_item_clicked(self, item_info: dict):
        """Handle thumbnail click — select and notify."""
        self._select_item(item_info)
        self.theme_selected.emit(item_info)

    def get_selected(self):
        return self.selected_item
