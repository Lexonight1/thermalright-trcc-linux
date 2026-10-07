"""
PyQt6 UCThemeWeb - Cloud themes browser panel.

Matches Windows TRCC.DCUserControl.UCThemeWeb (732x652)
Shows cloud theme thumbnails with category filtering and on-demand download.

Windows behavior:
- Preview PNGs are bundled in Web/{resolution}/ (shipped with installer)
- Clicking a thumbnail downloads the .mp4 if not cached, then plays it
- DownLoadFile() with status label "Downloading..."

A downloaded theme's tile animates when the App made it a GIF
(``CloudThemeService.materialise``, on the download thread); otherwise it shows
the static preview PNG, which is all the C# ever shows (``UCThemeWeb.SetThemeWeb``).
This panel runs no ffmpeg: it made the GIF itself, on the UI thread, ~100 ms a
tile on every visit (#264).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QMovie

from ...core.models import CloudThemeItem
from .base import BaseThumbnail, DownloadableThemeBrowser
from .constants import Layout, Sizes

log = logging.getLogger(__name__)

#: The catalog category a filter button carries, so its slot can be a named
#: method — ``feedback_no_lambdas``.
_CATEGORY_PROPERTY = "trcc_category"


class CloudThemeThumbnail(BaseThumbnail):
    """Cloud theme thumbnail.

    A downloaded theme plays the animated GIF the App wrote beside its MP4,
    when there is one.  Otherwise -- not downloaded, or no GIF -- the static
    preview PNG, with a download indicator when not downloaded.
    """

    def __init__(self, item_info: CloudThemeItem, parent=None):
        self._movie = None  # QMovie for animated GIF playback
        super().__init__(item_info, parent)

    def _get_display_name(self, info: CloudThemeItem) -> str:
        return info.id or info.name

    def _get_image_path(self, info: CloudThemeItem) -> str | None:
        log.debug("CloudThemeThumbnail: %s preview %s", info.id, info.preview)
        return info.preview

    def _load_thumbnail(self):
        """The App's GIF when it wrote one, else the static preview PNG.

        Only reads: a missing GIF is not made here.  QMovie is created but
        NOT started — UCThemeWeb.showEvent() starts animations only when the
        cloud panel is visible.
        """
        video = self.item_info.video
        gif = Path(video).with_suffix(".gif") if video else None
        if gif is not None and gif.is_file():
            log.debug("CloudThemeThumbnail: %s animates %s", self.item_info.id, gif)
            self._movie = QMovie(str(gif))
            self._movie.setScaledSize(
                QSize(Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE))
            self.thumb_label.setMovie(self._movie)
            return
        super()._load_thumbnail()



class UCThemeWeb(DownloadableThemeBrowser):
    """
    Cloud themes browser panel.

    Windows size: 732x652
    Preview PNGs are bundled; MP4s downloaded on-demand when clicked.
    GIF thumbnail animations only run while this panel is visible.
    """

    CMD_THEME_SELECTED = 16
    CMD_CATEGORY_CHANGED = 4

    def __init__(self,
                 download_fn: Callable[[str, str, str], str | None] | None = None,
                 parent=None):
        self.current_category = 'all'
        self.web_directory = None
        self._resolution = ""
        self._download_fn = download_fn
        super().__init__(parent)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_movies_running(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._set_movies_running(False)

    def _set_movies_running(self, running: bool) -> None:
        """Start or stop all QMovie animations on cloud thumbnails.

        This is what makes a downloaded entry's tile APPEAR, not merely what
        animates it: ``setMovie`` alone paints nothing, so a QMovie that has
        never been started shows an empty label.
        """
        movies = [m for w in self.item_widgets
                  if (m := getattr(w, '_movie', None)) is not None]
        log.debug("_set_movies_running: running=%s — %d movie(s) of %d thumbnail(s)",
                  running, len(movies), len(self.item_widgets))
        for movie in movies:
            movie.start() if running else movie.stop()

    def _create_filter_buttons(self):
        """Seven category buttons matching Windows positions."""
        btn_normal, btn_active = self._load_filter_assets()
        self.cat_buttons = {}
        self._btn_refs = [btn_normal, btn_active]

        for cat_id, x, y, w, h in Layout.WEB_CATEGORIES:
            btn = self._make_filter_button(x, y, w, h, btn_normal, btn_active,
                                           self._on_category_button)
            # The category rides ON the button rather than in a closure over
            # the loop variable — ``feedback_no_lambdas``.
            btn.setProperty(_CATEGORY_PROPERTY, cat_id)
            self.cat_buttons[cat_id] = btn

        self.cat_buttons['all'].setChecked(True)

    def _create_thumbnail(self, item_info: CloudThemeItem) -> CloudThemeThumbnail:
        return CloudThemeThumbnail(item_info)

    def _no_items_message(self) -> str:
        return ("No cloud themes found\n\nThey download automatically on "
                "first run, or run: trcc system download <width> <height>")

    def set_web_directory(self, path):
        """Set the Web directory (bundled PNGs + downloaded MP4s) and load themes."""
        log.debug("uc_theme_web.set_web_directory: %s", path)
        self.web_directory = Path(path) if path else None
        self.load_themes()

    def set_resolution(self, resolution: str):
        """Set resolution for cloud downloads (e.g., '320x320')."""
        log.debug("set_resolution: %s", resolution)
        self._resolution = resolution

    def _on_category_button(self) -> None:
        """A category filter was pressed — read which off the button."""
        button = self.sender()
        category = None if button is None else button.property(_CATEGORY_PROPERTY)
        log.debug("_on_category_button: category=%s", category)
        if category is not None:
            self._set_category(category)

    def _set_category(self, category):
        log.debug("_set_category called: category=%r, _downloading=%s", category, self._downloading)
        if self._downloading:
            return  # Windows isDownLoad guard
        self.current_category = category
        for cat_id, btn in self.cat_buttons.items():
            btn.setChecked(cat_id == category)
        self.load_themes()
        self.invoke_delegate(self.CMD_CATEGORY_CHANGED, category)

    def load_themes(self):
        """Load cloud themes from preview PNGs in Web directory.

        The PNGs arrive with the data install; MP4s are downloaded on demand
        when the user clicks a thumbnail.
        """
        self._clear_grid()

        if not self.web_directory:
            log.info("uc_theme_web.load_themes: no web_directory set — empty grid")
            self._show_empty_message()
            return

        # Ensure directory exists
        self.web_directory.mkdir(parents=True, exist_ok=True)

        # Find cached MP4s (already downloaded)
        cached = set()
        for mp4 in self.web_directory.glob('*.mp4'):
            cached.add(mp4.stem)

        # Scan for preview PNGs (matches Windows CheakWebFile)
        known_ids = []
        for png in sorted(self.web_directory.glob('*.png')):
            theme_id = png.stem
            if self.current_category != 'all':
                if not theme_id.startswith(self.current_category):
                    continue
            known_ids.append(theme_id)

        themes = []
        for theme_id in known_ids:
            is_local = theme_id in cached
            preview_path = self.web_directory / f"{theme_id}.png"

            themes.append(CloudThemeItem(
                name=theme_id,
                id=theme_id,
                video=str(self.web_directory / f"{theme_id}.mp4") if is_local else None,
                preview=str(preview_path) if preview_path.exists() else None,
                is_local=is_local,
            ))

        log.info(
            "uc_theme_web.load_themes: category=%r, %d theme(s) (%d cached) in %s",
            self.current_category, len(themes), len(cached), self.web_directory,
        )
        self._populate_grid(themes)
        # A rebuilt grid holds BRAND NEW QMovie objects, and a movie that was
        # never started paints nothing.  showEvent fires only when the panel
        # BECOMES visible, so any rebuild while it is ALREADY visible left every
        # downloaded entry a blank tile until the user switched tabs and back:
        # a display-angle change (set_web_directory), a category button
        # (_set_category), or a finished download.  One call here serves all
        # three callers instead of each remembering for itself.
        self._set_movies_running(self.isVisible())

    def _on_item_clicked(self, item_info: CloudThemeItem):
        """Handle click — play cached themes, download non-cached ones.

        Clicks are NOT gated by `_downloading` — users can queue multiple
        downloads in parallel.  Previously a first slow download locked
        every subsequent click until it finished; users perceived "stuck
        on first theme."
        """
        log.info("_on_item_clicked")
        self._select_item(item_info)

        if item_info.is_local:
            self.theme_selected.emit(item_info)
            self.invoke_delegate(self.CMD_THEME_SELECTED, item_info)
        else:
            self._download_cloud_theme(item_info.id)

    def _download_cloud_theme(self, theme_id: str):
        """Download a cloud theme MP4 (Windows DownLoadFile pattern)."""
        if not self.web_directory:
            log.warning("_download_cloud_theme: no web_directory — skipping %s", theme_id)
            return

        _fn = self._download_fn
        if _fn is None:
            log.warning("_download_cloud_theme: no download_fn — skipping %s", theme_id)
            return

        log.info("_download_cloud_theme: %s resolution=%s dir=%s",
                 theme_id, self._resolution, self.web_directory)

        def download_fn():
            result = _fn(theme_id, self._resolution, str(self.web_directory))
            log.info("_download_cloud_theme: %s result=%s", theme_id,
                     'ok' if result else 'failed')
            return bool(result)

        self._start_download(theme_id, download_fn)

    def _on_download_complete(self, theme_id: str, success: bool):
        """Handle download completion — refresh and auto-select."""
        log.info("_on_download_complete: theme_id=%s success=%s", theme_id, success)
        super()._on_download_complete(theme_id, success)
        if success:
            self.load_themes()      # starts the new movies (see load_themes)
            # Auto-select the newly downloaded theme
            for item in self.items:
                if item.id == theme_id:
                    self._on_item_clicked(item)
                    break
