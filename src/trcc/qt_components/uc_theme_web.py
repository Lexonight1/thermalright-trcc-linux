"""
PyQt6 UCThemeWeb - Cloud themes browser panel.

Matches Windows TRCC.DCUserControl.UCThemeWeb (732x652)
Shows cloud theme thumbnails with category filtering and on-demand download.

Windows behavior:
- Shows PNG thumbnails of all cached cloud themes
- Clicking a thumbnail downloads the .mp4 if not cached, then plays it
- DownLoadFile() with status label "Downloading..."
"""

import subprocess
import threading
from pathlib import Path

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import pyqtSignal, QTimer
from PyQt6.QtGui import QIcon

from .base import BaseThemeBrowser, BaseThumbnail, pil_to_pixmap
from .assets import load_pixmap
from .constants import Sizes, Layout, Styles, Colors

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class CloudThemeThumbnail(BaseThumbnail):
    """Cloud theme thumbnail. Shows download icon for non-cached themes."""

    def __init__(self, item_info: dict, parent=None):
        self.is_local = item_info.get('is_local', True)
        super().__init__(item_info, parent)

    def _get_display_name(self, info: dict) -> str:
        return info.get('id', info.get('name', 'Unknown'))

    def _get_image_path(self, info: dict) -> str | None:
        return info.get('preview')

    def _get_extra_style(self) -> str | None:
        if not self.is_local:
            return Styles.thumb_non_local(type(self).__name__)
        return None

    def _show_placeholder(self):
        """Show download placeholder for non-cached themes."""
        if not PIL_AVAILABLE:
            return
        try:
            size = (Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE)
            img = Image.new('RGB', size, color=Colors.PLACEHOLDER_BG)
            draw = ImageDraw.Draw(img)
            theme_id = self.item_info.get('id', self.item_info.get('name', '?'))
            text = f"⬇\n{theme_id}" if not self.is_local else theme_id
            draw.text((size[0] // 2, size[1] // 2),
                     text, fill=(100, 100, 100), anchor='mm', align='center')
            self.thumb_label.setPixmap(pil_to_pixmap(img))
        except Exception:
            pass


class UCThemeWeb(BaseThemeBrowser):
    """
    Cloud themes browser panel.

    Windows size: 732x652
    Shows all known cloud themes; downloads on-demand when clicked.
    """

    CMD_THEME_SELECTED = 16
    CMD_CATEGORY_CHANGED = 4

    download_started = pyqtSignal(str)       # theme_id
    download_finished = pyqtSignal(str, bool)  # theme_id, success

    def __init__(self, parent=None):
        self.current_category = 'all'
        self.videos_directory = None
        self._resolution = "320x320"
        self._downloading = False  # Windows isDownLoad guard
        self._cancel_previews = threading.Event()
        self._preview_thread = None
        super().__init__(parent)

    def _create_filter_buttons(self):
        """Seven category buttons matching Windows positions."""
        btn_normal, btn_active = self._load_filter_assets()
        self.cat_buttons = {}
        self._btn_refs = [btn_normal, btn_active]

        for cat_id, x, y, w, h in Layout.WEB_CATEGORIES:
            btn = self._make_filter_button(x, y, w, h, btn_normal, btn_active,
                lambda checked, c=cat_id: self._set_category(c))
            self.cat_buttons[cat_id] = btn

        self.cat_buttons['all'].setChecked(True)

    def _create_thumbnail(self, item_info: dict) -> CloudThemeThumbnail:
        return CloudThemeThumbnail(item_info)

    def _no_items_message(self) -> str:
        return "No cloud themes found\n\nDownload with: trcc download themes-320"

    def set_videos_directory(self, path):
        """Set the videos directory and load themes."""
        self.videos_directory = Path(path) if path else None
        self.load_themes()

    def set_resolution(self, resolution: str):
        """Set resolution for cloud downloads (e.g., '320x320')."""
        self._resolution = resolution

    def _set_category(self, category):
        if self._downloading:
            return  # Windows isDownLoad guard
        self.current_category = category
        for cat_id, btn in self.cat_buttons.items():
            btn.setChecked(cat_id == category)
        self.load_themes()
        self.invoke_delegate(self.CMD_CATEGORY_CHANGED, category)

    def load_themes(self):
        """Load cloud themes: show cached + known non-cached."""
        self._cancel_preview_downloads()
        self._clear_grid()

        if not self.videos_directory:
            self._show_empty_message()
            return

        # Ensure directory exists
        self.videos_directory.mkdir(parents=True, exist_ok=True)

        # Find cached themes (have .mp4 locally)
        cached = set()
        for mp4 in self.videos_directory.glob('*.mp4'):
            cached.add(mp4.stem)

        # Get all known theme IDs from cloud_downloader
        try:
            from ..cloud_downloader import get_themes_by_category
            known_ids = get_themes_by_category(self.current_category)
        except ImportError:
            # Fallback: only show cached themes
            known_ids = sorted(cached)

        themes = []
        for theme_id in known_ids:
            is_local = theme_id in cached
            preview_path = self.videos_directory / f"{theme_id}.png"

            themes.append({
                'id': theme_id,
                'name': theme_id,
                'video': str(self.videos_directory / f"{theme_id}.mp4") if is_local else None,
                'preview': str(preview_path) if preview_path.exists() else None,
                'is_local': is_local,
            })

        self._populate_grid(themes)
        self._start_preview_downloads()

    def _on_item_clicked(self, item_info: dict):
        """Handle click — play cached themes, download non-cached ones."""
        if self._downloading:
            return

        self.selected_item = item_info
        for widget in self.item_widgets:
            if isinstance(widget, BaseThumbnail):
                widget.set_selected(widget.item_info == item_info)

        if item_info.get('is_local', True):
            self.theme_selected.emit(item_info)
            self.invoke_delegate(self.CMD_THEME_SELECTED, item_info)
        else:
            self._download_cloud_theme(item_info['id'])

    def _download_cloud_theme(self, theme_id: str):
        """Download a cloud theme video (Windows DownLoadFile pattern)."""
        if not self.videos_directory:
            return

        self._downloading = True
        self.download_started.emit(theme_id)

        def download_task():
            try:
                from ..cloud_downloader import CloudThemeDownloader

                downloader = CloudThemeDownloader(
                    resolution=self._resolution,
                    cache_dir=str(self.videos_directory)
                )
                result = downloader.download_theme(theme_id)

                if result:
                    self._extract_preview(theme_id)
                    QTimer.singleShot(0, lambda: self._on_download_complete(theme_id, True))
                else:
                    QTimer.singleShot(0, lambda: self._on_download_complete(theme_id, False))
            except Exception as e:
                print(f"[!] Cloud theme download failed: {e}")
                QTimer.singleShot(0, lambda: self._on_download_complete(theme_id, False))

        thread = threading.Thread(target=download_task, daemon=True)
        thread.start()

    def _extract_preview(self, theme_id: str):
        """Try to extract a PNG preview from a downloaded MP4 via FFmpeg."""
        try:
            mp4_path = self.videos_directory / f"{theme_id}.mp4"
            png_path = self.videos_directory / f"{theme_id}.png"
            if mp4_path.exists() and not png_path.exists():
                subprocess.run([
                    'ffmpeg', '-i', str(mp4_path),
                    '-vframes', '1', '-y', str(png_path)
                ], capture_output=True, timeout=10)
        except Exception:
            pass

    def _on_download_complete(self, theme_id: str, success: bool):
        """Handle download completion — refresh and auto-select."""
        self._downloading = False
        self.download_finished.emit(theme_id, success)
        if success:
            self.load_themes()
            # Auto-select the newly downloaded theme
            for item in self.items:
                if item.get('id') == theme_id:
                    self._on_item_clicked(item)
                    break

    def _cancel_preview_downloads(self):
        """Cancel any ongoing background preview downloads."""
        self._cancel_previews.set()
        self._preview_thread = None

    def _start_preview_downloads(self):
        """Download missing preview PNGs in the background."""
        if not self.videos_directory or not self.items:
            return

        # Collect theme IDs that need previews
        missing = [item['id'] for item in self.items if not item.get('preview')]
        if not missing:
            return

        self._cancel_previews = threading.Event()
        videos_dir = str(self.videos_directory)
        resolution = self._resolution

        def download_previews():
            try:
                from ..cloud_downloader import CloudThemeDownloader
                downloader = CloudThemeDownloader(
                    resolution=resolution,
                    cache_dir=videos_dir
                )
                for theme_id in missing:
                    if self._cancel_previews.is_set():
                        return
                    result = downloader.download_preview_png(theme_id)
                    if result:
                        QTimer.singleShot(0, lambda tid=theme_id, path=result:
                                          self._on_preview_downloaded(tid, path))
            except Exception as e:
                print(f"[!] Preview download error: {e}")

        self._preview_thread = threading.Thread(target=download_previews, daemon=True)
        self._preview_thread.start()

    def _on_preview_downloaded(self, theme_id: str, preview_path: str):
        """Update the thumbnail widget after its preview PNG was downloaded."""
        for widget in self.item_widgets:
            if isinstance(widget, CloudThemeThumbnail) and widget.item_info.get('id') == theme_id:
                widget.item_info['preview'] = preview_path
                widget._load_thumbnail()
                break

    def get_selected_theme(self):
        return self.selected_item
