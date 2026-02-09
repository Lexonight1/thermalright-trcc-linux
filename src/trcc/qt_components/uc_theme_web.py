"""
PyQt6 UCThemeWeb - Cloud themes browser panel.

Matches Windows TRCC.DCUserControl.UCThemeWeb (732x652)
Shows cloud theme thumbnails with category filtering and on-demand download.

Windows behavior:
- Preview PNGs are bundled in Web/{resolution}/ (shipped with installer)
- Clicking a thumbnail downloads the .mp4 if not cached, then plays it
- DownLoadFile() with status label "Downloading..."
- Downloaded themes show animated thumbnail previews from the MP4
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QMovie

from .base import BaseThemeBrowser, BaseThumbnail
from .constants import Layout, Sizes


def _ensure_thumb_gif(mp4_path: str, size: int = Sizes.THUMB_IMAGE) -> str | None:
    """Create a 120x120 animated GIF from an MP4 via ffmpeg (cached).

    Returns path to the GIF, or None if ffmpeg fails.
    """
    gif_path = Path(mp4_path).with_suffix('.gif')
    if gif_path.exists():
        return str(gif_path)
    try:
        subprocess.run([
            'ffmpeg', '-i', mp4_path,
            '-vf', f'scale={size}:{size}:force_original_aspect_ratio=decrease,'
                   f'pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:black,'
                   'fps=8',
            '-loop', '0', '-y', str(gif_path),
        ], capture_output=True, timeout=30)
        if gif_path.exists():
            return str(gif_path)
    except Exception:
        pass
    return None


class CloudThemeThumbnail(BaseThumbnail):
    """Cloud theme thumbnail.

    Downloaded themes play an animated GIF (generated from MP4 via ffmpeg).
    Non-downloaded themes show static preview PNG with download indicator.
    """

    def __init__(self, item_info: dict, parent=None):
        self._movie = None  # QMovie for animated GIF playback
        super().__init__(item_info, parent)

    def _get_display_name(self, info: dict) -> str:
        return info.get('id', info.get('name', 'Unknown'))

    def _get_image_path(self, info: dict) -> str | None:
        video = info.get('video')
        if video and Path(video).exists():
            return None  # handled by _load_thumbnail via QMovie
        return info.get('preview')

    def _load_thumbnail(self):
        """Load thumbnail — animated GIF from MP4 or static PNG."""
        video = self.item_info.get('video')
        if video and Path(video).exists():
            gif_path = _ensure_thumb_gif(video)
            if gif_path:
                self._movie = QMovie(gif_path)
                self._movie.setScaledSize(
                    QSize(Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE))
                self.thumb_label.setMovie(self._movie)
                self._movie.start()
                return
        # Fall back to static PNG
        super()._load_thumbnail()



class UCThemeWeb(BaseThemeBrowser):
    """
    Cloud themes browser panel.

    Windows size: 732x652
    Preview PNGs are bundled; MP4s downloaded on-demand when clicked.
    """

    CMD_THEME_SELECTED = 16
    CMD_CATEGORY_CHANGED = 4

    download_started = pyqtSignal(str)       # theme_id
    download_finished = pyqtSignal(str, bool)  # theme_id, success

    def __init__(self, parent=None):
        self.current_category = 'all'
        self.web_directory = None
        self._resolution = "320x320"
        self._downloading = False  # Windows isDownLoad guard
        super().__init__(parent)
        self.download_finished.connect(self._on_download_complete)

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

    def set_web_directory(self, path):
        """Set the Web directory (bundled PNGs + downloaded MP4s) and load themes."""
        self.web_directory = Path(path) if path else None
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

    def _ensure_previews_extracted(self):
        """Extract preview PNGs from .7z archive if not already extracted."""
        if not self.web_directory:
            return
        # Check if PNGs already exist
        if list(self.web_directory.glob('*.png')):
            return
        # Look for .7z archive next to the directory (Web/{resolution}.7z)
        archive = self.web_directory.parent / f"{self.web_directory.name}.7z"
        if not archive.exists():
            return
        try:
            import py7zr
            with py7zr.SevenZipFile(str(archive), 'r') as z:
                z.extractall(str(self.web_directory))
        except ImportError:
            # Fallback to 7z command
            try:
                subprocess.run(
                    ['7z', 'x', str(archive), f'-o{self.web_directory}', '-y'],
                    capture_output=True, timeout=30
                )
            except Exception:
                pass
        except Exception:
            pass

    def load_themes(self):
        """Load cloud themes from preview PNGs in Web directory.

        PNGs are extracted from bundled .7z archives on first load.
        MP4s are downloaded on-demand when user clicks a thumbnail.
        """
        self._clear_grid()

        if not self.web_directory:
            self._show_empty_message()
            return

        # Ensure directory exists
        self.web_directory.mkdir(parents=True, exist_ok=True)

        # Extract PNGs from .7z if needed
        self._ensure_previews_extracted()

        # Find cached MP4s (already downloaded)
        cached = set()
        for mp4 in self.web_directory.glob('*.mp4'):
            cached.add(mp4.stem)

        # Scan for preview PNGs (matches Windows CheakWebFile)
        known_ids = []
        for png in sorted(self.web_directory.glob('*.png')):
            theme_id = png.stem
            if self.current_category != 'all':
                if self.current_category not in theme_id:
                    continue
            known_ids.append(theme_id)

        themes = []
        for theme_id in known_ids:
            is_local = theme_id in cached
            preview_path = self.web_directory / f"{theme_id}.png"

            themes.append({
                'id': theme_id,
                'name': theme_id,
                'video': str(self.web_directory / f"{theme_id}.mp4") if is_local else None,
                'preview': str(preview_path) if preview_path.exists() else None,
                'is_local': is_local,
            })

        self._populate_grid(themes)

    def _on_item_clicked(self, item_info: dict):
        """Handle click — play cached themes, download non-cached ones."""
        if self._downloading:
            return

        self._select_item(item_info)

        if item_info.get('is_local', True):
            self.theme_selected.emit(item_info)
            self.invoke_delegate(self.CMD_THEME_SELECTED, item_info)
        else:
            self._download_cloud_theme(item_info['id'])

    def _download_cloud_theme(self, theme_id: str):
        """Download a cloud theme MP4 (Windows DownLoadFile pattern)."""
        if not self.web_directory:
            return

        self._downloading = True
        self.download_started.emit(theme_id)

        def download_task():
            try:
                from ..cloud_downloader import CloudThemeDownloader

                downloader = CloudThemeDownloader(
                    resolution=self._resolution,
                    cache_dir=str(self.web_directory)
                )
                result = downloader.download_theme(theme_id)

                if result:
                    self._extract_preview(theme_id)
                    self.download_finished.emit(theme_id, True)
                else:
                    self.download_finished.emit(theme_id, False)
            except Exception as e:
                print(f"[!] Cloud theme download failed: {e}")
                self.download_finished.emit(theme_id, False)

        thread = threading.Thread(target=download_task, daemon=True)
        thread.start()

    def _extract_preview(self, theme_id: str):
        """Extract first frame from MP4 as PNG preview via FFmpeg."""
        if self.web_directory is None:
            return
        try:
            mp4_path = self.web_directory / f"{theme_id}.mp4"
            png_path = self.web_directory / f"{theme_id}.png"
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
        if success:
            self.load_themes()
            # Auto-select the newly downloaded theme
            for item in self.items:
                if item.get('id') == theme_id:
                    self._on_item_clicked(item)
                    break

    def get_selected_theme(self):
        return self.selected_item
