"""
PyQt6 UCThemeMask - Cloud masks browser panel.

Matches Windows TRCC.DCUserControl.UCThemeMask (732x652)
Shows cloud layout masks with download functionality.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal

from .base import BaseThemeBrowser, BaseThumbnail

try:
    import PIL  # noqa: F401 — PIL_AVAILABLE guard
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class MaskThumbnail(BaseThumbnail):
    """Cloud mask thumbnail with non-local (dashed border) state."""

    def __init__(self, mask_info, parent=None):
        super().__init__(mask_info, parent)

    def _get_image_path(self, info: dict) -> str | None:
        return info.get('preview')


class UCThemeMask(BaseThemeBrowser):
    """
    Cloud masks browser panel.

    Windows size: 732x652
    Background image provides header. No visible buttons.
    Grid: 5 columns, starts at (30, 60).
    """

    # Known cloud mask IDs (000a-023e pattern)
    KNOWN_MASKS = [f"{i:03d}{c}" for i in range(24) for c in "abcde"]

    # Cloud mask server URLs by resolution
    CLOUD_URLS = {
        "320x320": "http://www.czhorde.cc/tr/zt320320/",
        "480x480": "http://www.czhorde.cc/tr/zt480480/",
        "240x240": "http://www.czhorde.cc/tr/zt240240/",
        "360x360": "http://www.czhorde.cc/tr/zt360360/",
    }

    CMD_MASK_SELECTED = 16
    CMD_DOWNLOAD = 100

    mask_selected = pyqtSignal(dict)
    download_started = pyqtSignal(str)
    download_finished = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        self.mask_directory = None
        self._resolution = "320x320"
        self._local_masks = set()
        super().__init__(parent)

    def _create_thumbnail(self, item_info: dict) -> MaskThumbnail:
        return MaskThumbnail(item_info)

    def _no_items_message(self) -> str:
        return "No masks found\n\nMasks can be downloaded by clicking on cloud mask thumbnails"

    def set_mask_directory(self, path):
        """Set the mask directory and load masks."""
        self.mask_directory = Path(path) if path else None
        if self.mask_directory:
            self.mask_directory.mkdir(parents=True, exist_ok=True)
        self.refresh_masks()

    def set_resolution(self, resolution: str):
        """Set resolution for cloud downloads."""
        self._resolution = resolution

    def refresh_masks(self):
        """Reload masks from disk and show cloud masks available for download."""
        self._clear_grid()
        self._local_masks.clear()

        if not PIL_AVAILABLE:
            self._show_empty_message()
            return

        if self.mask_directory:
            self.mask_directory.mkdir(parents=True, exist_ok=True)

        masks = []

        # Load local masks
        if self.mask_directory and self.mask_directory.exists():
            for item in sorted(self.mask_directory.iterdir()):
                if item.is_dir():
                    thumb_path = item / 'Theme.png'
                    mask_path = item / '01.png'

                    if thumb_path.exists() or mask_path.exists():
                        masks.append({
                            'name': item.name,
                            'path': str(item),
                            'preview': str(thumb_path if thumb_path.exists() else mask_path),
                            'is_local': True,
                        })
                        self._local_masks.add(item.name.lower())

        # Add known cloud masks that aren't locally cached
        for mask_id in self.KNOWN_MASKS:
            if mask_id.lower() not in self._local_masks:
                masks.append({
                    'name': mask_id,
                    'path': None,
                    'preview': None,
                    'is_local': False,
                })

        self._populate_grid(masks)

    def _on_item_clicked(self, item_info: dict):
        """Handle click — select local masks, download non-local ones."""
        self.selected_item = item_info
        for widget in self.item_widgets:
            if isinstance(widget, BaseThumbnail):
                widget.set_selected(widget.item_info == item_info)

        if item_info.get('is_local', True):
            self.mask_selected.emit(item_info)
            self.theme_selected.emit(item_info)
            self.invoke_delegate(self.CMD_MASK_SELECTED, item_info)
        else:
            self._download_cloud_mask(item_info['name'])

    def _download_cloud_mask(self, mask_id: str):
        """Download a cloud mask from the server."""
        if not self.mask_directory or not self._resolution:
            print("[!] Cannot download mask: directory or resolution not set")
            return

        base_url = self.CLOUD_URLS.get(self._resolution)
        if not base_url:
            print(f"[!] No cloud URL for resolution {self._resolution}")
            return

        self.download_started.emit(mask_id)

        def download_task():
            try:
                import io
                import os
                import urllib.error
                import urllib.request
                import zipfile

                mask_url = f"{base_url}{mask_id}.zip"
                assert self.mask_directory is not None
                mask_dir = self.mask_directory / mask_id

                print(f"[+] Downloading mask {mask_id} from {mask_url}")

                req = urllib.request.Request(mask_url, headers={
                    'User-Agent': 'TRCC-Linux/1.0'
                })

                try:
                    with urllib.request.urlopen(req, timeout=30) as response:
                        data = response.read()

                    try:
                        with zipfile.ZipFile(io.BytesIO(data)) as zf:
                            mask_dir.mkdir(parents=True, exist_ok=True)
                            for info in zf.infolist():
                                if os.path.isabs(info.filename) or '..' in info.filename.split('/'):
                                    continue
                                zf.extract(info, mask_dir)
                            print(f"[+] Extracted mask {mask_id}")
                    except zipfile.BadZipFile:
                        mask_dir.mkdir(parents=True, exist_ok=True)
                        (mask_dir / "Theme.png").write_bytes(data)

                    QTimer.singleShot(100, self.refresh_masks)
                    self.download_finished.emit(mask_id, True)

                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        self._download_mask_files(mask_id, base_url, mask_dir)
                        QTimer.singleShot(100, self.refresh_masks)
                        self.download_finished.emit(mask_id, True)
                    else:
                        print(f"[!] HTTP Error {e.code} downloading mask {mask_id}")
                        self.download_finished.emit(mask_id, False)

            except Exception as e:
                print(f"[!] Failed to download mask {mask_id}: {e}")
                self.download_finished.emit(mask_id, False)

        thread = threading.Thread(target=download_task, daemon=True)
        thread.start()

    def _download_mask_files(self, mask_id: str, base_url: str, mask_dir: Path):
        """Download individual mask files."""
        import urllib.error
        import urllib.request

        mask_dir.mkdir(parents=True, exist_ok=True)
        files = ['Theme.png', '01.png', 'config1.dc']

        for filename in files:
            try:
                url = f"{base_url}{mask_id}/{filename}"
                req = urllib.request.Request(url, headers={'User-Agent': 'TRCC-Linux/1.0'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    (mask_dir / filename).write_bytes(response.read())
                    print(f"[+] Downloaded {mask_id}/{filename}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    print(f"[!] HTTP Error {e.code} downloading {mask_id}/{filename}")
            except Exception as e:
                print(f"[!] Failed to download {mask_id}/{filename}: {e}")

    def get_selected_mask(self):
        return self.selected_item
