"""
PyQt6 UCThemeMask - Cloud masks browser panel.

Matches Windows TRCC.DCUserControl.UCThemeMask (732x652)
Shows cloud layout masks with download functionality.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from trcc.paths import is_safe_archive_member

from ..core.models import MaskItem
from .base import BaseThumbnail, DownloadableThemeBrowser

log = logging.getLogger(__name__)


class MaskThumbnail(BaseThumbnail):
    """Cloud mask thumbnail with non-local (dashed border) state."""

    def __init__(self, mask_info: MaskItem, parent=None):
        super().__init__(mask_info, parent)

    def _get_image_path(self, info: MaskItem) -> str | None:
        return info.preview


class UCThemeMask(DownloadableThemeBrowser):
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

    mask_selected = pyqtSignal(object)

    def __init__(self, parent=None):
        self.mask_directory = None
        self._resolution = "320x320"
        self._local_masks = set()
        super().__init__(parent)

    def _create_thumbnail(self, item_info: MaskItem) -> MaskThumbnail:
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

        if self.mask_directory:
            self.mask_directory.mkdir(parents=True, exist_ok=True)

        masks: list[MaskItem] = []

        # Load local masks
        if self.mask_directory and self.mask_directory.exists():
            for item in sorted(self.mask_directory.iterdir()):
                if item.is_dir():
                    thumb_path = item / 'Theme.png'
                    mask_path = item / '01.png'

                    if thumb_path.exists() or mask_path.exists():
                        masks.append(MaskItem(
                            name=item.name,
                            path=str(item),
                            preview=str(thumb_path if thumb_path.exists() else mask_path),
                            is_local=True,
                        ))
                        self._local_masks.add(item.name.lower())

        # Add known cloud masks that aren't locally cached
        for mask_id in self.KNOWN_MASKS:
            if mask_id.lower() not in self._local_masks:
                masks.append(MaskItem(
                    name=mask_id,
                    is_local=False,
                ))

        log.debug("refresh_masks: %d local, %d cloud, dir=%s",
                   len(self._local_masks), len(masks) - len(self._local_masks),
                   self.mask_directory)
        self._populate_grid(masks)

    def _on_item_clicked(self, item_info: MaskItem):
        """Handle click — select local masks, download non-local ones."""
        if self._downloading:
            return

        self._select_item(item_info)

        if item_info.is_local:
            self.mask_selected.emit(item_info)
            self.theme_selected.emit(item_info)
            self.invoke_delegate(self.CMD_MASK_SELECTED, item_info)
        else:
            self._download_cloud_mask(item_info.name)

    def _on_download_complete(self, mask_id: str, success: bool):
        """Handle download completion on the main thread — refresh grid."""
        super()._on_download_complete(mask_id, success)
        if success:
            log.info("Mask %s downloaded — refreshing grid", mask_id)
            self.refresh_masks()

    def _download_cloud_mask(self, mask_id: str):
        """Download a cloud mask from the server."""
        if not self.mask_directory or not self._resolution:
            log.warning("Cannot download mask: directory or resolution not set")
            return

        base_url = self.CLOUD_URLS.get(self._resolution)
        if not base_url:
            log.warning("No cloud URL for resolution %s", self._resolution)
            return

        def download_fn():
            import io
            import urllib.error
            import urllib.request
            import zipfile

            mask_url = f"{base_url}{mask_id}.zip"
            assert self.mask_directory is not None
            mask_dir = self.mask_directory / mask_id

            log.info("Downloading mask %s from %s", mask_id, mask_url)
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
                            if not is_safe_archive_member(info.filename):
                                continue
                            zf.extract(info, mask_dir)
                        log.info("Extracted mask %s", mask_id)
                except zipfile.BadZipFile:
                    mask_dir.mkdir(parents=True, exist_ok=True)
                    (mask_dir / "Theme.png").write_bytes(data)
                return True

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._download_mask_files(mask_id, base_url, mask_dir)
                    return True
                log.warning("HTTP %d downloading mask %s", e.code, mask_id)
                return False

        self._start_download(mask_id, download_fn)

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
                    log.info("Downloaded %s/%s", mask_id, filename)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    log.warning("HTTP %d downloading %s/%s", e.code, mask_id, filename)
            except Exception as e:
                log.warning("Failed to download %s/%s: %s", mask_id, filename, e)

    def get_selected_mask(self):
        return self.selected_item
