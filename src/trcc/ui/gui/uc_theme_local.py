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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, SignalInstance
from PySide6.QtGui import QIcon, QIntValidator
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

    MAX_SLIDESHOW = 6  # Windows LunBoArrayCount = 6

    CMD_THEME_SELECTED = 16
    CMD_SLIDESHOW = 48
    CMD_DELETE = 32
    #: The C#'s delegate code for both game-mode controls (UCThemeLocal.cs:627,
    #: :726); here it carries the one half that changed.
    CMD_GAME_MODE = 64

    slideshow_changed = Signal(bool, int, list)  # enabled, interval, theme_indices
    delete_requested = Signal(object)  # LocalThemeItem
    # The C#'s buttonDaoChu / buttonDaoRu (UCThemeLocal 441,28 / 482,28) --
    # the window owns the file dialogs and the device the theme goes to.
    export_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent=None):
        # Slideshow interaction state lives in a toolkit-free model; this
        # panel renders it (badges, button icon) and exposes a public API
        # so the handler never reaches into private attrs.
        self._slideshow_model = SlideshowModel()
        self._all_themes = []   # Full unfiltered theme list
        self._current_path: Path | None = None   # the theme on the panel
        log.info("UCThemeLocal.__init__: slideshow=False")
        super().__init__(parent)

    def _create_filter_buttons(self):
        """The "All" button + slideshow, export and import controls.

        The C# also builds buttonDefault / buttonUser and HIDES both, for
        good (``UCThemeLocal.cs:821``, ``:836``, never shown), so the list is
        always every theme.
        """
        btn_normal, btn_active = self._load_filter_assets()
        self._btn_refs = [btn_normal, btn_active]
        self.all_btn = self._make_filter_button(
            *Layout.LOCAL_BTN_ALL, btn_normal, btn_active, self._on_all_clicked)
        self.all_btn.setChecked(True)

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

        # Windows: buttonDaoChu (441, 28) / buttonDaoRu (482, 28), 40x18 --
        # moved here from FormCZTV in 2.1.6.  (The C#'s buttonThemeOut
        # "export all" at 651,27 is hidden with an empty handler: not built.)
        self.export_btn = self._icon_button(
            441, 'app_export.png', "Export the panel's theme",
            self.export_requested)
        self.import_btn = self._icon_button(
            482, 'app_import.png', "Import a theme", self.import_requested)

        self.game = GameModeControls(self)

    def _icon_button(self, x: int, image: str, tip: str,
                     signal: SignalInstance | Callable[..., Any]) -> QPushButton:
        """A flat 40x18 picture button on the header row (y=28)."""
        log.debug("_icon_button: %s at x=%d", image, x)
        btn = QPushButton(self)
        btn.setGeometry(x, 28, 40, 18)
        btn.setFlat(True)
        btn.setStyleSheet(Styles.FLAT_BUTTON)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
        pix = Assets.load_pixmap(image, 40, 18)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
        btn.clicked.connect(signal)
        return btn

    def _create_thumbnail(self, item_info: LocalThemeItem) -> ThemeThumbnail:
        return ThemeThumbnail(item_info)

    def _no_items_message(self) -> str:
        return "No themes found"

    def _on_all_clicked(self, *_qt_args) -> None:
        """The C#'s buttonAll: show every theme (it is the only list)."""
        log.info("UCThemeLocal._on_all_clicked")
        self.all_btn.setChecked(True)
        self._render_filtered()   # no disk re-walk

    def set_themes(self, entries: list[ThemeListEntry]) -> None:
        """Render the browser from ListThemes entries — the universal Command
        result — instead of walking the disk in the View.

        ``origin`` ("user"/"shipped") says which themes may be deleted (the
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
        """Clear + repopulate the grid from the cached ``self._all_themes``.
        No disk access — re-runnable on an "All" click."""
        log.debug("_render_filtered: %d theme(s)", len(self._all_themes))
        self._clear_grid()
        if not self._all_themes:
            self._show_empty_message()
            return
        theme_dirs = list(self._all_themes)
        for index, t in enumerate(theme_dirs):
            t.index = index

        self._populate_grid(theme_dirs)
        self._apply_decorations()
        self._highlight_current()

    def show_current_theme(self, path: Path | None) -> None:
        """Highlight the theme the panel is showing — sends nothing.

        Remembered, because every re-list (a filter click, a rotation, a
        theme saved) rebuilds the tiles and drops the highlight.
        """
        log.info("UCThemeLocal.show_current_theme: %s → %s",
                 self._current_path, path)
        self._current_path = path
        self._highlight_current()

    def _highlight_current(self) -> None:
        current = next((w.item_info for w in self.item_widgets
                        if isinstance(w, BaseThumbnail)
                        and Path(w.item_info.path) == self._current_path), None)
        log.debug("_highlight_current: %s → %s", self._current_path,
                  current.name if current else None)
        self._select_item(current)

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
            slideshow_on = self._slideshow_model.enabled

            # Delete: the user's own themes, never in slideshow mode.  The C#
            # spells it "index >= 5" because its first five are always the
            # shipped Theme1-5; ours ship 5 or 10 per resolution, or none
            # when the download failed, so the position is not the fact --
            # where the theme lives is.  DeleteTheme refuses the rest anyway.
            widget.set_deletable(info.is_user and not slideshow_on)

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


    def forget_slideshow_theme(self, name: str) -> None:
        """Drop a deleted theme from the slideshow array.

        No filesystem, no re-list: the file removal is the ``DeleteTheme``
        Command's job and the re-list is the handler's (``ListThemes``) — the
        View only forgets the name from its slideshow model. (#theme-collision)
        """
        log.info("UCThemeLocal.forget_slideshow_theme: %r", name)
        self._slideshow_model.remove_theme(name)


class GameModeControls:
    """Game mode's two controls on the local-theme panel -- the C#'s
    ``buttonGame`` (228, 28) 40x18 and ``textBoxCPU`` (336, 29) 24x16, two
    digits, default 75 (UCThemeLocal.cs:910-935).

    Each sends only its own half through the panel's delegate, and neither
    changes its own look: the App's ``GameModeChanged`` comes back through
    :meth:`show`, so they show what the App holds.
    """

    def __init__(self, panel: UCThemeLocal) -> None:
        log.debug("GameModeControls.__init__: panel=%s", type(panel).__name__)
        self._panel = panel
        self.enabled = False
        self.threshold = 75
        self._off_px = Assets.load_pixmap('theme_local_game.png', 40, 18)
        self._on_px = Assets.load_pixmap('theme_local_game_active.png', 40, 18)
        self.button = panel._icon_button(
            228, 'theme_local_game.png',
            "Game mode: while CPU usage stays above the threshold, the panel "
            "shows only its overlay", self._on_clicked)
        self.cpu_input = QLineEdit(str(self.threshold), panel)
        self.cpu_input.setGeometry(336, 29, 24, 16)
        self.cpu_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cpu_input.setMaxLength(2)
        self.cpu_input.setValidator(QIntValidator(0, 99, self.cpu_input))
        self.cpu_input.setToolTip("Game mode: CPU usage % that takes the panel")
        self.cpu_input.setStyleSheet(
            "QLineEdit { background: #232227; color: white; border: none; "
            "font-family: 'Microsoft YaHei'; font-size: 9pt; }"
        )
        self.cpu_input.editingFinished.connect(self._on_threshold_edited)

    def _on_clicked(self, *_qt_args) -> None:
        """Ask for the switch the other way (Windows: buttonGame_Click)."""
        log.info("GameModeControls._on_clicked: shown=%s -> asking %s",
                 self.enabled, not self.enabled)
        self._panel.invoke_delegate(self._panel.CMD_GAME_MODE, None,
                                    {"enabled": not self.enabled})

    def _on_threshold_edited(self) -> None:
        """Send a typed threshold (Windows: textBoxCPU_TextChanged).

        Sent when the edit is finished, not per keystroke as the C# does --
        "7" on the way to "75" would be a threshold the user never meant.
        An emptied box shows the saved value again.
        """
        text = self.cpu_input.text().strip()
        log.info("GameModeControls._on_threshold_edited: %r (shown %d)",
                 text, self.threshold)
        if not text or int(text) == self.threshold:
            self.cpu_input.setText(str(self.threshold))
            return
        self._panel.invoke_delegate(self._panel.CMD_GAME_MODE, None,
                                    {"threshold": int(text)})

    def show(self, enabled: bool, threshold: int) -> None:
        """Show the App's game mode -- READ only (Windows: buttonGame_Set)."""
        log.info("GameModeControls.show: %s/%d -> %s/%d", self.enabled,
                 self.threshold, enabled, threshold)
        self.enabled, self.threshold = enabled, threshold
        px = self._on_px if enabled else self._off_px
        if not px.isNull():
            self.button.setIcon(QIcon(px))
        if not self.cpu_input.hasFocus():
            self.cpu_input.setText(str(threshold))

