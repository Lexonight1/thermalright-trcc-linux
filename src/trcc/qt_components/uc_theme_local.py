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

import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton

from .assets import load_pixmap
from .base import BaseThemeBrowser, BaseThumbnail
from .constants import Layout, Styles


class ThemeThumbnail(BaseThumbnail):
    """Local theme thumbnail with optional delete button and slideshow badge."""

    delete_clicked = pyqtSignal(dict)
    slideshow_toggled = pyqtSignal(dict)

    def __init__(self, item_info: dict, parent=None):
        self._slideshow_mode = False
        super().__init__(item_info, parent)
        self._delete_btn = None
        self._badge_label = None

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
            self._delete_btn.clicked.connect(
                lambda: self.delete_clicked.emit(self.item_info))
            self._delete_btn.raise_()
            self._delete_btn.show()
        elif not deletable and self._delete_btn is not None:
            self._delete_btn.deleteLater()
            self._delete_btn = None

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
        self._slideshow_mode = enabled

    def mousePressEvent(self, event):
        """In slideshow mode, clicking lower half toggles inclusion."""
        if self._slideshow_mode and event.position().y() > 60:
            self.slideshow_toggled.emit(self.item_info)
            return
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

    slideshow_changed = pyqtSignal(bool, int, list)  # enabled, interval, theme_indices
    delete_requested = pyqtSignal(dict)  # theme_info dict

    def __init__(self, parent=None):
        self.filter_mode = self.MODE_ALL
        self.theme_directory = None
        self._slideshow = False
        self._slideshow_interval = 3
        self._lunbo_array = []  # Theme names in slideshow order (max 6)
        self._all_themes = []   # Full unfiltered theme list
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
                lambda checked, m=mode: self._set_filter(m))
            self._filter_buttons.append(btn)

        self._filter_buttons[0].setChecked(True)

        # Slideshow toggle — Windows: buttonLunbo (531, 28) 40x17
        self._lunbo_off = load_pixmap('P主题轮播.png', 40, 17)
        self._lunbo_on = load_pixmap('P主题轮播a.png', 40, 17)
        self.slideshow_btn = QPushButton(self)
        self.slideshow_btn.setGeometry(531, 28, 40, 17)
        self.slideshow_btn.setFlat(True)
        self.slideshow_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.slideshow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._lunbo_off.isNull():
            self.slideshow_btn.setIcon(QIcon(self._lunbo_off))
            self.slideshow_btn.setIconSize(self.slideshow_btn.size())
        self.slideshow_btn.clicked.connect(self._on_slideshow_clicked)

        # Slideshow interval input — Windows: textBoxTimer (602, 29) 24x16
        self.timer_input = QLineEdit(self)
        self.timer_input.setGeometry(602, 29, 24, 16)
        self.timer_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.timer_input.setMaxLength(3)
        self.timer_input.setText("3")
        self.timer_input.setStyleSheet(
            "QLineEdit { background: #232227; color: white; border: none; "
            "font-family: 'Microsoft YaHei'; font-size: 9pt; }"
        )
        self.timer_input.editingFinished.connect(self._on_timer_changed)

        # Export button — Windows: buttonThemeOut (651, 27) 60x18 (empty handler)
        export_px = load_pixmap('P导出所有主题.png', 60, 18)
        self.export_btn = QPushButton(self)
        self.export_btn.setGeometry(651, 27, 60, 18)
        self.export_btn.setFlat(True)
        self.export_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if not export_px.isNull():
            self.export_btn.setIcon(QIcon(export_px))
            self.export_btn.setIconSize(self.export_btn.size())
            self.export_btn._img_ref = export_px  # type: ignore[attr-defined]

    def _create_thumbnail(self, item_info: dict) -> ThemeThumbnail:
        return ThemeThumbnail(item_info)

    def _no_items_message(self) -> str:
        return "No themes found"

    def set_theme_directory(self, path):
        self.theme_directory = Path(path) if path else None
        self.load_themes()

    def _set_filter(self, mode):
        self.filter_mode = mode
        for i, btn in enumerate(self._filter_buttons):
            btn.setChecked(i == mode)
        self.load_themes()
        self.invoke_delegate(self.CMD_FILTER_CHANGED, mode)

    def load_themes(self):
        self._clear_grid()

        if not self.theme_directory or not self.theme_directory.exists():
            self._show_empty_message()
            return

        # Load ALL themes (unfiltered) for index tracking and slideshow
        all_dirs = []
        for item in sorted(self.theme_directory.iterdir()):
            if item.is_dir():
                thumb = item / 'Theme.png'
                bg = item / '00.png'
                if thumb.exists() or bg.exists():
                    all_dirs.append({
                        'name': item.name,
                        'path': str(item),
                        'thumbnail': str(thumb if thumb.exists() else bg),
                        'is_user': item.name.startswith('User') or item.name.startswith('Custom'),
                    })

        self._all_themes = all_dirs

        # Filter for display
        if self.filter_mode == self.MODE_DEFAULT:
            theme_dirs = [t for t in all_dirs if not t.get('is_user')]
        elif self.filter_mode == self.MODE_USER:
            theme_dirs = [t for t in all_dirs if t.get('is_user')]
        else:
            theme_dirs = list(all_dirs)

        # Tag each with its global index in the unfiltered list
        for t in theme_dirs:
            try:
                t['_index'] = self._all_themes.index(t)
            except ValueError:
                t['_index'] = 0

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
            idx = info.get('_index', 0)

            # Delete buttons: not shown in slideshow mode (Windows behavior)
            if not self._slideshow:
                # Windows: MODE_ALL/DEFAULT shows delete only on index >= 5
                # MODE_USER shows delete on ALL themes
                if self.filter_mode == self.MODE_USER:
                    widget.set_deletable(True)
                elif idx >= 5:
                    widget.set_deletable(True)
                else:
                    widget.set_deletable(False)
            else:
                widget.set_deletable(False)

            # Slideshow badges
            widget.set_slideshow_mode(self._slideshow)
            if self._slideshow:
                name = info.get('name', '')
                if name in self._lunbo_array:
                    pos = self._lunbo_array.index(name) + 1
                    widget.set_slideshow_badge(pos)
                else:
                    widget.set_slideshow_badge(0)  # Empty circle
            else:
                widget.clear_slideshow_badge()

    def _on_delete_clicked(self, item_info: dict):
        """Forward delete request to parent (confirmation handled there)."""
        self.delete_requested.emit(item_info)

    def _on_slideshow_toggled(self, item_info: dict):
        """Toggle theme in/out of slideshow array (Windows lunBoArray)."""
        name = item_info.get('name', '')
        if name in self._lunbo_array:
            self._lunbo_array.remove(name)
        elif len(self._lunbo_array) < self.MAX_SLIDESHOW:
            self._lunbo_array.append(name)

        self._apply_decorations()
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def _on_item_clicked(self, item_info: dict):
        """Extend base to also invoke delegate."""
        super()._on_item_clicked(item_info)
        self.invoke_delegate(self.CMD_THEME_SELECTED, item_info)

    def _on_slideshow_clicked(self):
        """Toggle slideshow mode (Windows: buttonLunbo_Click)."""
        self._slideshow = not self._slideshow
        px = self._lunbo_on if self._slideshow else self._lunbo_off
        if not px.isNull():
            self.slideshow_btn.setIcon(QIcon(px))
            self.slideshow_btn.setIconSize(self.slideshow_btn.size())
        self._apply_decorations()
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def _on_timer_changed(self):
        """Validate and apply slideshow interval (Windows: min 3 seconds)."""
        text = self.timer_input.text().strip()
        try:
            val = int(text)
        except ValueError:
            val = 3
        val = max(3, val)
        self.timer_input.setText(str(val))
        self._slideshow_interval = val
        self.invoke_delegate(self.CMD_SLIDESHOW)

    def is_slideshow(self):
        return self._slideshow

    def get_slideshow_interval(self):
        return self._slideshow_interval

    def get_slideshow_themes(self):
        """Get list of theme info dicts in slideshow order."""
        result = []
        for name in self._lunbo_array:
            for t in self._all_themes:
                if t['name'] == name:
                    result.append(t)
                    break
        return result

    def get_selected_theme(self):
        return self.selected_item

    def delete_theme(self, theme_info: dict):
        """Delete a theme directory and refresh the list."""
        path = Path(theme_info.get('path', ''))
        if path.exists() and path.is_dir():
            shutil.rmtree(path)

        # Remove from slideshow if present
        name = theme_info.get('name', '')
        if name in self._lunbo_array:
            self._lunbo_array.remove(name)

        self.load_themes()
