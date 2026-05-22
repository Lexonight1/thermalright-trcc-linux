"""
UCThemeMask — Cloud + custom masks browser panel.

Matches Windows TRCC.DCUserControl.UCThemeMask (732x652).
Shows cloud layout masks with download, plus user-uploaded custom masks.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu

from ...core.models import CLOUD_MASK_URLS, MaskItem, is_safe_archive_member
from .base import BaseThumbnail, DownloadableThemeBrowser

if TYPE_CHECKING:
    from ...core.ports import Paths


@dataclass(slots=True)
class _DiscoveredMask:
    """Filesystem-discovered mask entry — local row source for refresh_masks."""
    name: str
    path: Path
    preview_path: Path
    is_custom: bool

log = logging.getLogger(__name__)


class MaskThumbnail(BaseThumbnail):
    """Mask thumbnail with non-local (dashed border) state."""

    def __init__(self, mask_info: MaskItem, parent=None):
        super().__init__(mask_info, parent)

    def _get_image_path(self, info: MaskItem) -> str | None:
        return info.preview


class UCThemeMask(DownloadableThemeBrowser):
    """
    Cloud + custom masks browser panel.

    Windows size: 732x652
    Background image provides header with 7 category filter buttons.
    Grid: 5 columns, starts at (30, 60).
    Custom masks live in ~/.trcc-user/data/web/zt{W}{H}/.
    """

    # Cloud mask server URLs by resolution — canonical source in core/models.py
    CLOUD_URLS = CLOUD_MASK_URLS

    CMD_MASK_SELECTED = 16
    CMD_DOWNLOAD = 100

    mask_selected = Signal(object)

    def __init__(self, parent=None, paths: Paths | None = None):
        self.mask_directory = None
        self._resolution = ""
        self._local_masks: set[str] = set()
        self._category = 'all'
        # Paths port for resolving user-masks dir; trcc_app injects it.
        self._paths = paths
        super().__init__(parent)

    def _create_filter_buttons(self):
        """Seven category buttons."""
        from .constants import Layout
        btn_normal, btn_active = self._load_filter_assets()
        self.cat_buttons = {}
        self._btn_refs = [btn_normal, btn_active]

        for cat_id, x, y, w, h in Layout.WEB_CATEGORIES:
            btn = self._make_filter_button(x, y, w, h, btn_normal, btn_active,
                self._on_category_clicked)
            btn.setProperty('cat_id', cat_id)
            self.cat_buttons[cat_id] = btn

        self.cat_buttons['all'].setChecked(True)

    def _on_category_clicked(self, *_qt_args) -> None:
        """Single category-button slot — reads the cat_id from sender's property."""
        sender = self.sender()
        if sender is None:
            return
        cat_id = sender.property('cat_id')
        if cat_id is not None:
            self._set_category(cat_id)

    def _set_category(self, category: str):
        """Filter masks by category suffix (a-e, y) or show all."""
        if self._downloading:
            return
        self._category = category
        for cat_id, btn in self.cat_buttons.items():
            btn.setChecked(cat_id == category)
        self.refresh_masks()

    def _create_thumbnail(self, item_info: MaskItem) -> MaskThumbnail:
        thumb = MaskThumbnail(item_info)
        if item_info.is_custom:
            thumb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            thumb.customContextMenuRequested.connect(
                self._on_custom_context_menu_requested)
        return thumb

    def _on_custom_context_menu_requested(self, _pos) -> None:
        """Context-menu slot — reads thumb + item_info from sender."""
        thumb = self.sender()
        if not isinstance(thumb, MaskThumbnail):
            return
        info = getattr(thumb, 'mask_info', None) or getattr(thumb, 'item_info', None)
        if info is not None:
            self._show_custom_context_menu(thumb, info)

    def _show_custom_context_menu(self, widget: MaskThumbnail, info: MaskItem):
        """Right-click menu for custom masks — delete option."""
        menu = QMenu(widget)
        delete_action = menu.addAction("Delete")
        action = menu.exec(widget.mapToGlobal(widget.rect().center()))
        if action == delete_action and info.path:
            self._delete_custom_mask(Path(info.path))

    def _delete_custom_mask(self, mask_dir: Path):
        """Delete a custom mask directory and refresh the grid."""
        if mask_dir.exists():
            shutil.rmtree(mask_dir)
            log.info("Deleted custom mask: %s", mask_dir)
        self.refresh_masks()

    def _no_items_message(self) -> str:
        return "No masks found\n\nMasks can be downloaded by clicking on cloud mask thumbnails"

    def set_mask_directory(self, path):
        """Set the mask directory and load masks."""
        log.info("uc_theme_mask.set_mask_directory: %s", path)
        self.mask_directory = Path(path) if path else None
        if self.mask_directory:
            self.mask_directory.mkdir(parents=True, exist_ok=True)
        self.refresh_masks()

    def set_resolution(self, resolution: str):
        """Set resolution for cloud downloads."""
        log.debug("set_resolution: %s", resolution)
        self._resolution = resolution

    def _parse_resolution(self) -> tuple[int, int]:
        """Parse resolution string (e.g. '320x320') into (width, height)."""
        if not self._resolution or 'x' not in self._resolution:
            return (0, 0)
        parts = self._resolution.split('x')
        return (int(parts[0]), int(parts[1]))

    def _user_masks_dir(self) -> Path:
        """Get the user custom masks directory for current resolution."""
        w, h = self._parse_resolution()
        if self._paths is None:
            raise RuntimeError(
                "WebMaskBrowser was constructed without a Paths port — "
                "trcc_app must inject one at build time."
            )
        return self._paths.user_mask_dir(w, h)

    def refresh_masks(self):
        """Reload masks from disk — only shows what exists per device resolution."""
        self._clear_grid()
        self._local_masks.clear()

        if self.mask_directory:
            self.mask_directory.mkdir(parents=True, exist_ok=True)

        user_dir = self._user_masks_dir()
        # Walk both the per-resolution cloud-cache dir and the user
        # custom-masks dir.  Each subdir with a Theme.png is one mask.
        discovered: list = []
        for source_dir, is_custom in [
            (self.mask_directory, False),
            (user_dir, True),
        ]:
            if not source_dir or not source_dir.is_dir():
                continue
            for entry in sorted(source_dir.iterdir()):
                if not entry.is_dir():
                    continue
                # Accept any subdir that has either a DC config (the
                # mask's own ``config1.dc`` carries its center coords)
                # OR a preview image OR the canonical mask file.
                # Preview lookup falls back through Theme.png → 01.png
                # → first PNG so a DC-only mask still surfaces.
                preview = entry / "Theme.png"
                if not preview.exists():
                    canonical = entry / "01.png"
                    if canonical.exists():
                        preview = canonical
                    else:
                        any_png = next(entry.glob("*.png"), None)
                        if any_png is not None:
                            preview = any_png
                        elif (entry / "config1.dc").exists():
                            preview = entry / "config1.dc"  # no image but valid
                        else:
                            continue
                discovered.append(_DiscoveredMask(
                    name=entry.name,
                    path=entry,
                    preview_path=preview,
                    is_custom=is_custom,
                ))

        masks: list[MaskItem] = []
        for m in discovered:
            masks.append(MaskItem(
                name=m.name,
                path=str(m.path) if m.path else None,
                preview=str(m.preview_path) if m.preview_path else None,
                is_local=True,
                is_custom=m.is_custom,
            ))
            self._local_masks.add(m.name.lower())

        # Filter by category (last char of mask name matches suffix)
        if self._category != 'all':
            masks = [m for m in masks if m.name and m.name[-1:] == self._category]

        log.info("uc_theme_mask.refresh_masks: %d mask(s), cat=%s, "
                 "cloud_dir=%s user_dir=%s",
                 len(masks), self._category, self.mask_directory, user_dir)
        self._populate_grid(masks)

    def _on_item_clicked(self, item_info: MaskItem):
        """Handle click — select local masks, download non-local ones.

        No `_downloading` gate: user clicks are always honored, even when
        an earlier download is still in flight.  Parallel downloads are
        fine (different files) and the UI never feels "stuck on the
        first click."
        """
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

    # ── Cloud mask download ─────────────────────────────────────────

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
