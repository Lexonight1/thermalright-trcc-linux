"""The tile grid -- one way to show a gallery of pictures, for every GUI.

A tile is a picture over its name (120 x 140); a browser is a scrolling grid
of them, five to a row, with the one the panel shows lit.  The gui's theme
tabs (Local themes, Cloud backgrounds, Cloud masks) and every module gallery
(the Audio Visualizer's) are this grid.

It was the gui's (``ui/gui/base.py``) until 2026-11-10, when a module page
shared by every GUI needed it -- a shared page may not import a skin.  The
code moved as it was; what the gui drew with its own colours now comes in a
:class:`TileSkin`, which each GUI builds from its own look and hands over, as
the Settings panel's ``SettingsSkin``.  The sizes are the grid's own, the same
in every GUI.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..core.logs import per_frame
from .qt_panel import BasePanel
from .qt_words import Tr

log = logging.getLogger(__name__)
#: A grid restyles every tile on each selection -- per tile, not per click.
frame_log = per_frame(__name__)

#: A tile: its size, its picture's, its name's height; a name longer than
#: ``NAME_MAX`` is cut to ``NAME_CUT`` and "...".
TILE_W, TILE_H, TILE_IMAGE, TILE_NAME_H = 120, 140, 120, 20
NAME_MAX, NAME_CUT = 15, 12
#: A browser: its size, where its grid scrolls, and the grid's spacing.
BROWSER_W, BROWSER_H = 732, 652
SCROLL = (0, 50, 732, 602)
GRID_COLS = 5
GRID_MARGIN = (30, 10, 0, 10)       # left, top, right, bottom
GRID_H_SPACE, GRID_V_SPACE = 15, 10


@dataclass(frozen=True, slots=True)
class TileSkin:
    """What a GUI hands the grid: how its tiles look.  Every field is the
    GUI's; none has a default, so a GUI says all of it."""
    #: The picture's frame, and the name under it (style sheets).
    image: str
    name: str
    #: The scrolling area and its bar.
    scroll_area: str
    #: A tile's style sheet by its class name: lit, plain (with its hover),
    #: and not yet downloaded.
    selected: Callable[[str], str]
    normal: Callable[[str], str]
    not_local: Callable[[str], str]
    #: A tile without a picture: its fill (RGB), and its words' colour.
    placeholder: tuple[int, int, int]
    placeholder_text: tuple[int, int, int]
    #: The words of an empty grid.
    empty_text: str


class ClickableFrame(QFrame):
    """QFrame that emits clicked signal."""

    clicked = Signal()

    def mousePressEvent(self, event):
        log.debug("ClickableFrame.mousePressEvent: %s", type(self).__name__)
        self.clicked.emit()
        super().mousePressEvent(event)


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

    def __init__(self, item_info, skin: TileSkin, parent=None):
        super().__init__(parent)
        self.item_info = item_info
        self._skin = skin
        self.is_local: bool = getattr(item_info, 'is_local', True)
        self.selected = False

        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Thumbnail image
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(TILE_IMAGE, TILE_IMAGE)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet(skin.image)
        layout.addWidget(self.thumb_label)

        # Name label
        name = self._get_display_name(item_info)
        if len(name) > NAME_MAX:
            name = name[:NAME_CUT] + "..."
        self.name_label = QLabel(name)
        self.name_label.setFixedHeight(TILE_NAME_H)
        self.name_label.setStyleSheet(skin.name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)
        frame_log.debug("BaseThumbnail.__init__: %s %r", type(self).__name__, name)

        self._load_thumbnail()

    def _get_display_name(self, info) -> str:
        """Extract display name from item. Override for custom field."""
        frame_log.debug("BaseThumbnail._get_display_name")
        return getattr(info, 'name', 'Unknown')

    def _get_image_path(self, info) -> str | None:
        """Extract image path from item. Override for custom field."""
        frame_log.debug("BaseThumbnail._get_image_path")
        return getattr(info, 'thumbnail', None)

    def _get_extra_style(self) -> str | None:
        """Return dashed-border style for non-local (downloadable) items."""
        frame_log.debug("BaseThumbnail._get_extra_style: local=%s", self.is_local)
        if not self.is_local:
            return self._skin.not_local(type(self).__name__)
        return None

    def _load_thumbnail(self):
        """Load thumbnail image into thumb_label."""
        path = self._get_image_path(self.item_info)
        frame_log.debug("BaseThumbnail._load_thumbnail: %s", path)
        if path and Path(path).exists():
            try:
                src = QImage(str(path))
                if src.isNull():
                    raise ValueError("QImage failed to load")
                scaled = src.scaled(
                    TILE_IMAGE, TILE_IMAGE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                bg = QImage(TILE_IMAGE, TILE_IMAGE, QImage.Format.Format_RGB32)
                bg.fill(QColor(0, 0, 0))
                px = (TILE_IMAGE - scaled.width()) // 2
                py = (TILE_IMAGE - scaled.height()) // 2
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
        frame_log.debug("BaseThumbnail._show_placeholder: %s", type(self).__name__)
        try:
            bg = QImage(TILE_IMAGE, TILE_IMAGE, QImage.Format.Format_RGB32)
            bg.fill(QColor(*self._skin.placeholder))
            name = self._get_display_name(self.item_info)
            text = f"⬇\n{name}" if not self.is_local else name
            painter = QPainter(bg)
            painter.setPen(QColor(*self._skin.placeholder_text))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(
                QRect(0, 0, TILE_IMAGE, TILE_IMAGE),
                Qt.AlignmentFlag.AlignCenter,
                text)
            painter.end()
            self.thumb_label.setPixmap(QPixmap.fromImage(bg))
        except (OSError, RuntimeError) as e:
            log.debug("base: text-thumbnail rendering failed: %s", e)

    def _update_style(self):
        cls_name = type(self).__name__
        frame_log.debug("BaseThumbnail._update_style: %s selected=%s", cls_name,
                       self.selected)
        if self.selected:
            self.setStyleSheet(self._skin.selected(cls_name))
        else:
            extra = self._get_extra_style()
            if extra:
                self.setStyleSheet(extra)
            else:
                self.setStyleSheet(self._skin.normal(cls_name))

    def set_selected(self, selected):
        # On change only: the grid calls this for EVERY thumbnail on each
        # selection, so logging each call wrote the whole grid per click.
        if selected != self.selected:
            log.debug("BaseThumbnail.set_selected: name=%r selected=%s",
                      getattr(self.item_info, 'name', '?'), selected)
        self.selected = selected
        self._update_style()

    def mousePressEvent(self, event):
        log.info(
            "BaseThumbnail.mousePressEvent: %s name=%r (emit clicked)",
            type(self).__name__,
            getattr(self.item_info, 'name', self.item_info),
        )
        self.clicked.emit(self.item_info)
        # Don't call super - we override clicked signal with different signature


class BaseThemeBrowser(BasePanel):
    """
    Base class for theme/mask browser panels (732x652).

    Provides: scroll area, grid layout, thumbnail management, selection.

    Subclasses override:
    - _create_filter_buttons(): Create filter/category buttons above grid
    - _create_thumbnail(item_info) -> BaseThumbnail: Factory method
    - _no_items_message() -> str: Empty state message
    """

    theme_selected = Signal(object)

    def __init__(self, parent=None, *, skin: TileSkin):
        self._skin = skin
        super().__init__(parent, width=BROWSER_W, height=BROWSER_H)
        log.debug("%s.__init__: tile grid", type(self).__name__)

        self.items: list = []
        self.item_widgets: list = []
        self.selected_item = None
        #: The window's language, for the thumbnails a browser rebuilds.
        self._tr: Tr = str

        self._setup_ui()

    def set_language(self, lang: str) -> None:
        """The window's language, for what this panel builds from now on --
        the window's own pass translated what is there already."""
        log.info("%s.set_language: %s", type(self).__name__, lang)
        self._tr = partial(tr, lang=lang)

    def _setup_ui(self) -> None:
        """BaseThemeBrowser UI: scroll area + filter buttons."""
        log.debug("%s._setup_ui", type(self).__name__)
        self._setup_base_ui()
        self._create_filter_buttons()

    def _setup_base_ui(self):
        """Create scroll area and grid (shared by all browsers)."""
        log.debug("%s._setup_base_ui", type(self).__name__)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setGeometry(*SCROLL)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setStyleSheet(self._skin.scroll_area)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background-color: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(*GRID_MARGIN)
        self.grid_layout.setHorizontalSpacing(GRID_H_SPACE)
        self.grid_layout.setVerticalSpacing(GRID_V_SPACE)
        self.grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.scroll_area.setWidget(self.grid_container)

    def _create_filter_buttons(self):
        """Override to add filter/category buttons above the grid."""

    def _create_thumbnail(self, item_info: dict) -> BaseThumbnail:
        """Override to create the appropriate thumbnail widget."""
        raise NotImplementedError

    def _no_items_message(self) -> str:
        """Override to provide custom empty-state message."""
        log.debug("%s._no_items_message: default", type(self).__name__)
        return "No items found"

    def _clear_grid(self):
        """Clear all widgets from the grid."""
        log.debug("%s._clear_grid: %d widget(s)", type(self).__name__,
                  len(self.item_widgets))
        for widget in self.item_widgets:
            widget.deleteLater()
        self.item_widgets.clear()
        self.items.clear()

    def _populate_grid(self, items: list):
        """Populate grid with thumbnails for the given items."""
        log.info("%s._populate_grid: %d items",
                 type(self).__name__, len(items))
        self.items = items

        if not items:
            self._show_empty_message()
            return

        for i, item_info in enumerate(items):
            row = i // GRID_COLS
            col = i % GRID_COLS
            thumb = self._create_thumbnail(item_info)
            thumb.clicked.connect(self._on_item_clicked)
            self.grid_layout.addWidget(thumb, row, col)
            self.item_widgets.append(thumb)

    def _show_empty_message(self):
        """Show empty state label."""
        log.info("%s._show_empty_message: %r",
                 type(self).__name__, self._no_items_message())
        label = QLabel(self._no_items_message())
        label.setStyleSheet(
            f"color: {self._skin.empty_text}; font-size: 12px; background: transparent;"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid_layout.addWidget(label, 0, 0, 1, GRID_COLS)
        self.item_widgets.append(label)

    def _select_item(self, item_info):
        """Update selection state and visuals (no signals emitted)."""
        log.debug("%s._select_item: name=%r",
                  type(self).__name__,
                  getattr(item_info, 'name', item_info))
        self.selected_item = item_info
        for widget in self.item_widgets:
            if isinstance(widget, BaseThumbnail):
                widget.set_selected(widget.item_info == item_info)

    def _on_item_clicked(self, item_info):
        """Handle thumbnail click — select and notify."""
        log.info("%s._on_item_clicked: name=%r (emit theme_selected)",
                 type(self).__name__,
                 getattr(item_info, 'name', item_info))
        self._select_item(item_info)
        self.theme_selected.emit(item_info)

    def get_selected(self):
        log.debug("%s.get_selected: %r", type(self).__name__,
                  getattr(self.selected_item, 'name', self.selected_item))
        return self.selected_item
