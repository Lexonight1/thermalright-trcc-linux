"""
PyQt6 UCThemeLocal - Local themes browser panel.

Matches Windows TRCC.DCUserControl.UCThemeLocal (732x652)
Shows theme thumbnails in a 5-column scrollable grid.

Features:
- Filter: All / Default / User (Windows cmd 0/1/2)
- Theme selection (Windows cmd 16)
- Delete user themes with confirmation (Windows cmd 32)
- Slideshow/carousel: select up to 6 themes for auto-rotation (Windows cmd 48)
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

from ...core.models import LocalThemeItem
from ...core.results import ThemeListEntry
from ..presentation.slideshow_model import SlideshowModel
from .assets import Assets
from .base import BaseThemeBrowser, BaseThumbnail
from .constants import Layout, Styles

log = logging.getLogger(__name__)


class ThemeThumbnail(BaseThumbnail):
    """Local theme thumbnail with optional delete button and slideshow badge."""

    delete_clicked = Signal(object)
    slideshow_toggled = Signal(object)

    def __init__(self, item_info: LocalThemeItem, parent=None):
        self._slideshow_mode = False
        super().__init__(item_info, parent)
        self._delete_btn = None
        self._badge_label = None
        log.debug(
            "ThemeThumbnail.__init__: name=%r path=%r is_user=%s",
            item_info.name, item_info.path, item_info.is_user,
        )

    def set_deletable(self, deletable: bool):
        """Show/hide delete button (top-right X) on this thumbnail."""
        if deletable and self._delete_btn is None:
            self._delete_btn = QPushButton("✕", self)
            self._delete_btn.setGeometry(96, 2, 20, 20)
            self._delete_btn.setStyleSheet(
                "QPushButton { background: rgba(180, 40, 40, 200); color: white; "
                "border: none; border-radius: 10px; font-size: 11px; font-weight: bold; }"
                "QPushButton:hover { background: rgba(220, 50, 50, 255); }"
            )
            self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._delete_btn.setToolTip("Delete theme")
            self._delete_btn.clicked.connect(self._on_delete_clicked)
            self._delete_btn.raise_()
            self._delete_btn.show()
        elif not deletable and self._delete_btn is not None:
            self._delete_btn.deleteLater()
            self._delete_btn = None

    def _on_delete_clicked(self) -> None:
        """Delete button slot — re-emit with this thumbnail's item info."""
        log.info("ThemeThumbnail._on_delete_clicked: name=%r",
                 self.item_info.name)
        self.delete_clicked.emit(self.item_info)

    def set_slideshow_badge(self, number: int):
        """Show slideshow badge. number=0 means unselected, 1-6 = position."""
        if self._badge_label is None:
            self._badge_label = QLabel(self)
            self._badge_label.setFixedSize(22, 22)
            self._badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._badge_label.move(92, 94)  # Bottom-right of 120x120 image area

        if number > 0:
            self._badge_label.setText(str(number))
            self._badge_label.setStyleSheet(
                "QLabel { background: rgba(74, 111, 165, 220); color: white; "
                "border-radius: 11px; font-size: 12px; font-weight: bold; }"
            )
        else:
            self._badge_label.setText("")
            self._badge_label.setStyleSheet(
                "QLabel { background: rgba(80, 80, 80, 180); "
                "border: 2px solid #888; border-radius: 11px; }"
            )
        self._badge_label.show()

    def clear_slideshow_badge(self):
        """Remove slideshow badge."""
        if self._badge_label is not None:
            self._badge_label.hide()
            self._badge_label.deleteLater()
            self._badge_label = None

    def set_slideshow_mode(self, enabled: bool):
        """Toggle slideshow mode for click behavior."""
        log.debug("ThemeThumbnail.set_slideshow_mode: name=%r enabled=%s",
                  self.item_info.name, enabled)
        self._slideshow_mode = enabled

    def mousePressEvent(self, event):
        """In slideshow mode, clicking lower half toggles inclusion."""
        if self._slideshow_mode and event.position().y() > 60:
            log.info(
                "ThemeThumbnail.mousePressEvent: %r slideshow_toggled",
                self.item_info.name,
            )
            self.slideshow_toggled.emit(self.item_info)
            return
        log.info("ThemeThumbnail.mousePressEvent: %r clicked",
                 self.item_info.name)
        self.clicked.emit(self.item_info)


class UCThemeLocal(BaseThemeBrowser):
    """
    Local themes browser panel.

    Windows size: 732x652
    Background image provides header. Filter buttons are transparent overlays.
    """

    MODE_ALL = 0
    MODE_DEFAULT = 1
    MODE_USER = 2

    MAX_SLIDESHOW = 6  # Windows LunBoArrayCount = 6

    CMD_THEME_SELECTED = 16
    CMD_FILTER_CHANGED = 3
    CMD_SLIDESHOW = 48
    CMD_DELETE = 32

    slideshow_changed = Signal(bool, int, list)  # enabled, interval, theme_indices
    delete_requested = Signal(object)  # LocalThemeItem

    def __init__(self, parent=None):
        self.filter_mode = self.MODE_ALL
        # Slideshow interaction state lives in a toolkit-free model; this
        # panel renders it (badges, button icon) and exposes a public API
        # so the handler never reaches into private attrs.
        self._slideshow_model = SlideshowModel()
        self._all_themes = []   # Full unfiltered theme list
        log.info("UCThemeLocal.__init__: filter=%s slideshow=False",
                 self.MODE_ALL)
        super().__init__(parent)

    def _create_filter_buttons(self):
        """Three filter buttons: All, Default, User + slideshow controls."""
        btn_normal, btn_active = self._load_filter_assets()
        self._filter_buttons = []
        self._btn_refs = [btn_normal, btn_active]

        configs = [
            (Layout.LOCAL_BTN_ALL, self.MODE_ALL),
            (Layout.LOCAL_BTN_DEFAULT, self.MODE_DEFAULT),
            (Layout.LOCAL_BTN_USER, self.MODE_USER),
        ]
        for (x, y, w, h), mode in configs:
            btn = self._make_filter_button(x, y, w, h, btn_normal, btn_active,
                self._on_filter_clicked)
            btn.setProperty('filter_mode', mode)
            self._filter_buttons.append(btn)

        self._filter_buttons[0].setChecked(True)

        # Slideshow toggle — Windows: buttonLunbo (531, 28) 40x17
        self._lunbo_off = Assets.load_pixmap('theme_local_carousel.png', 40, 17)
        self._lunbo_on = Assets.load_pixmap('theme_local_carousel_active.png', 40, 17)
        self.slideshow_btn = QPushButton(self)
        self.slideshow_btn.setGeometry(531, 28, 40, 17)
        self.slideshow_btn.setFlat(True)
        self.slideshow_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.slideshow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._lunbo_off.isNull():
            self.slideshow_btn.setIcon(QIcon(self._lunbo_off))
            self.slideshow_btn.setIconSize(self.slideshow_btn.size())
        self.slideshow_btn.setToolTip("Toggle theme slideshow")
        self.slideshow_btn.clicked.connect(self._on_slideshow_clicked)

        # Slideshow interval input — Windows: textBoxTimer (602, 29) 24x16
        self.timer_input = QLineEdit(self)
        self.timer_input.setGeometry(602, 29, 24, 16)
        self.timer_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.timer_input.setMaxLength(3)
        self.timer_input.setText("3")
        self.timer_input.setToolTip("Slideshow interval (seconds)")
        self.timer_input.setStyleSheet(
            "QLineEdit { background: #232227; color: white; border: none; "
            "font-family: 'Microsoft YaHei'; font-size: 9pt; }"
        )
        self.timer_input.editingFinished.connect(self._on_timer_changed)

        # Export button — Windows: buttonThemeOut (651, 27) 60x18 (empty handler)
        export_px = Assets.load_pixmap('theme_local_export_all.png', 60, 18)
        self.export_btn = QPushButton(self)
        self.export_btn.setGeometry(651, 27, 60, 18)
        self.export_btn.setFlat(True)
        self.export_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setToolTip("Export all themes")
        if not export_px.isNull():
            self.export_btn.setIcon(QIcon(export_px))
            self.export_btn.setIconSize(self.export_btn.size())
            self.export_btn._img_ref = export_px  # type: ignore[attr-defined]

    def _create_thumbnail(self, item_info: LocalThemeItem) -> ThemeThumbnail:
        return ThemeThumbnail(item_info)

    def _no_items_message(self) -> str:
        return "No themes found"

    def _on_filter_clicked(self, *_qt_args):
        """Filter-button slot — reads filter mode from sender's property."""
        sender = self.sender()
        if sender is None:
            log.debug("UCThemeLocal._on_filter_clicked: sender is None")
            return
        mode = sender.property('filter_mode')
        log.info("UCThemeLocal._on_filter_clicked: mode=%s", mode)
        if mode is not None:
            self._set_filter(mode)

    def _set_filter(self, mode):
        log.info("UCThemeLocal._set_filter: %s -> %s",
                 self.filter_mode, mode)
        self.filter_mode = mode
        for i, btn in enumerate(self._filter_buttons):
            btn.setChecked(i == mode)
        self._render_filtered()   # re-filter the cache; no disk re-walk
        self.invoke_delegate(self.CMD_FILTER_CHANGED, mode)

    def set_themes(self, entries: list[ThemeListEntry]) -> None:
        """Render the browser from ListThemes entries — the universal Command
        result — instead of walking the disk in the View.

        ``origin`` ("user"/"shipped") drives the user/default filter (the
        canonical, location-derived classification); ``preview`` is the tile
        image.  Same data feeds CLI/API/qtgui. (#theme-collision)
        """
        log.info("UCThemeLocal.set_themes: %d entr%s",
                 len(entries), "y" if len(entries) == 1 else "ies")
        self._all_themes = [
            LocalThemeItem(
                name=e.name, path=e.path, thumbnail=e.preview,
                is_local=True, is_user=(e.origin == "user"),
            )
            for e in entries
        ]
        self._render_filtered()

    def _render_filtered(self) -> None:
        """Clear + repopulate the grid from the cached ``self._all_themes`` for
        the current filter mode.  No disk access — re-runnable on a filter click
        (the filter buttons no longer re-walk the disk)."""
        self._clear_grid()
        if not self._all_themes:
            self._show_empty_message()
            return
        if self.filter_mode == self.MODE_DEFAULT:
            theme_dirs = [t for t in self._all_themes if not t.is_user]
        elif self.filter_mode == self.MODE_USER:
            theme_dirs = [t for t in self._all_themes if t.is_user]
        else:
            theme_dirs = list(self._all_themes)

        # Tag each with its global index in the unfiltered list
        for t in theme_dirs:
            try:
                t.index = self._all_themes.index(t)
            except ValueError:
                t.index = 0

        self._populate_grid(theme_dirs)
        self._apply_decorations()

    def _populate_grid(self, items: list):
        """Override to connect delete and slideshow signals on thumbnails."""
        super()._populate_grid(items)
        for widget in self.item_widgets:
            if isinstance(widget, ThemeThumbnail):
                widget.delete_clicked.connect(self._on_delete_clicked)
                widget.slideshow_toggled.connect(self._on_slideshow_toggled)

    def _apply_decorations(self):
        """Apply delete buttons and slideshow badges based on current mode."""
        for widget in self.item_widgets:
            if not isinstance(widget, ThemeThumbnail):
                continue

            info = widget.item_info
            idx = info.index
            slideshow_on = self._slideshow_model.enabled

            # Delete buttons: not shown in slideshow mode (Windows behavior)
            if not slideshow_on:
                # Windows: MODE_ALL/DEFAULT shows delete only on index >= 5
                # MODE_USER shows delete on ALL themes
                if self.filter_mode == self.MODE_USER or idx >= 5:
                    widget.set_deletable(True)
                else:
                    widget.set_deletable(False)
            else:
                widget.set_deletable(False)

            # Slideshow badges
            widget.set_slideshow_mode(slideshow_on)
            if slideshow_on:
                widget.set_slideshow_badge(
                    self._slideshow_model.badge_position(info.name))
            else:
                widget.clear_slideshow_badge()

    def _on_delete_clicked(self, item_info: dict):
        """Forward delete request to parent (confirmation handled there)."""
        log.info("UCThemeLocal._on_delete_clicked: %s",
                 getattr(item_info, 'name', item_info))
        self.delete_requested.emit(item_info)

    def _on_slideshow_toggled(self, item_info: LocalThemeItem):
        """Toggle theme in/out of slideshow array (Windows lunBoArray)."""
        name = item_info.name
        now_in = self._slideshow_model.toggle_theme(name)
        log.info(
            "UCThemeLocal._on_slideshow_toggled: %r (now_in=%s) — array=%s",
            name, now_in, self._slideshow_model.themes,
        )
        self._apply_decorations()
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def _on_item_clicked(self, item_info: dict):
        """Extend base to also invoke delegate."""
        log.info("UCThemeLocal._on_item_clicked: %s (emitting theme_selected)",
                 getattr(item_info, 'name', item_info))
        super()._on_item_clicked(item_info)
        self.invoke_delegate(self.CMD_THEME_SELECTED, item_info)

    def _update_slideshow_button_icon(self) -> None:
        """Swap the slideshow button pixmap to match the model's enabled flag."""
        px = self._lunbo_on if self._slideshow_model.enabled else self._lunbo_off
        if not px.isNull():
            self.slideshow_btn.setIcon(QIcon(px))
            self.slideshow_btn.setIconSize(self.slideshow_btn.size())

    def _on_slideshow_clicked(self):
        """Toggle slideshow mode (Windows: buttonLunbo_Click)."""
        enabled = self._slideshow_model.toggle_enabled()
        log.info("UCThemeLocal._on_slideshow_clicked: -> %s", enabled)
        self._update_slideshow_button_icon()
        self._apply_decorations()
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def _on_timer_changed(self):
        """Validate and apply slideshow interval (Windows: min 3 seconds)."""
        val = self._slideshow_model.set_interval(self.timer_input.text().strip())
        self.timer_input.setText(str(val))
        log.info("UCThemeLocal._on_timer_changed: -> %ss", val)
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def set_slideshow_state(self, themes: list[str], enabled: bool,
                            interval: int) -> None:
        """Restore slideshow UI from persisted state.

        Public entry the handler calls instead of reaching into private
        attrs (``local._lunbo_array = …`` etc.).  Caller supplies an
        already-validated interval.
        """
        log.info(
            "UCThemeLocal.set_slideshow_state: themes=%d enabled=%s interval=%s",
            len(themes), enabled, interval,
        )
        self._slideshow_model.restore(themes, enabled, interval)
        self.timer_input.setText(str(interval))
        self._update_slideshow_button_icon()
        self._apply_decorations()

    def is_slideshow(self):
        return self._slideshow_model.enabled

    def get_slideshow_interval(self):
        return self._slideshow_model.interval

    def get_slideshow_themes(self) -> list[LocalThemeItem]:
        """Get list of theme items in slideshow order."""
        result = []
        for name in self._slideshow_model.themes:
            for t in self._all_themes:
                if t.name == name:
                    result.append(t)
                    break
        return result

    def get_selected_theme(self):
        return self.selected_item

    def forget_slideshow_theme(self, name: str) -> None:
        """Drop a deleted theme from the slideshow array.

        No filesystem, no re-list: the file removal is the ``DeleteTheme``
        Command's job and the re-list is the handler's (``ListThemes``) — the
        View only forgets the name from its slideshow model. (#theme-collision)
        """
        log.info("UCThemeLocal.forget_slideshow_theme: %r", name)
        self._slideshow_model.remove_theme(name)
