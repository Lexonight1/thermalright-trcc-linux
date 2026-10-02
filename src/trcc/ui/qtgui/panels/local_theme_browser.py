"""LocalThemeBrowser — list + apply themes from ``user_content_dir``.

Dispatches:
* ``ListThemes`` on construct + on the Refresh button;
* ``LoadTheme`` on Apply (uses the selected theme's path);
* ``DeleteTheme`` on Delete (confirms via QMessageBox).

The device key is a free text field — once a `DevicePicker` widget
lands it'll slot in as a child here, but the current Display panel
takes the same shape so this is consistent.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ....core.commands import (
    DeleteTheme,
    ExportTheme,
    ImportTheme,
    ListThemes,
    LoadImage,
    LoadTheme,
    SaveTheme,
)
from ....core.models import MEDIA, MediaKind
from ..assets import thumbnail_icon
from ..device_picker import DevicePickerWidget
from ._browser_base import AssetBrowserPanel

log = logging.getLogger(__name__)


class LocalThemeBrowser(AssetBrowserPanel):
    """List + apply + delete themes from the user content directory."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )
        self._picker.key_changed.connect(self._on_key_changed)
        # Saved, imported or deleted in ANY UI, this one included: the one
        # re-list.  Queued -- the event may arrive from the IPC reader thread.
        self._bus.themes_changed.connect(
            self._on_themes_changed, type=Qt.ConnectionType.QueuedConnection)

        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)

        # Thumbnail grid: each theme shows its Theme.png preview.
        self._list = self._build_asset_list()

        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)

        self._apply_btn = QPushButton("Apply", self)
        self._apply_btn.clicked.connect(self._on_apply)

        self._delete_btn = QPushButton("Delete…", self)
        self._delete_btn.clicked.connect(self._on_delete)

        self._from_image_btn = QPushButton("Create from image…", self)
        self._from_image_btn.setToolTip(
            "Pick an image, crop it to your device's resolution, "
            "save it as a theme and apply it.",
        )
        self._from_image_btn.clicked.connect(self._on_create_from_image)

        self._from_video_btn = QPushButton("Create from video…", self)
        self._from_video_btn.setToolTip(
            "Pick a video, trim it, encode it as a Theme.zt animation, "
            "save it as a theme and apply it.",
        )
        self._from_video_btn.clicked.connect(self._on_create_from_video)

        self._save_btn = QPushButton("Save current as…", self)
        self._save_btn.setToolTip(
            "Save the device's current rendered state (background, mask, "
            "overlay edits) as a new theme.",
        )
        self._save_btn.clicked.connect(self._on_save)

        self._export_btn = QPushButton("Export…", self)
        self._export_btn.setToolTip(
            "Zip the selected theme to a shareable .tr archive.",
        )
        self._export_btn.clicked.connect(self._on_export)

        self._import_btn = QPushButton("Import…", self)
        self._import_btn.setToolTip(
            "Unpack a .tr theme archive into your library.",
        )
        self._import_btn.clicked.connect(self._on_import)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_btn)
        button_row.addWidget(self._apply_btn)
        button_row.addWidget(self._delete_btn)
        button_row.addWidget(self._save_btn)
        button_row.addWidget(self._export_btn)
        button_row.addWidget(self._import_btn)
        button_row.addWidget(self._from_image_btn)
        button_row.addWidget(self._from_video_btn)
        button_row.addStretch(1)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(key_form)
        root.addWidget(self._list, stretch=1)
        root.addLayout(button_row)
        root.addWidget(self._status)

        self.refresh()

    # ── Public API ────────────────────────────────────────────────────

    def _on_key_changed(self, key: str) -> None:
        """The window switched device — re-list that device's themes."""
        log.debug("_on_key_changed: key=%s", key)
        self.refresh()

    def refresh(self) -> None:
        """Re-fetch the theme list for the picked device's resolution."""
        log.debug("refresh")
        self._list.clear()
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first to list themes at its resolution.",
            )
            return
        resolution = self._target_resolution(key)
        if resolution is None:
            return
        result = self.dispatch(ListThemes(resolution=resolution))
        for theme in result.themes:
            w, h = theme.resolution
            item = QListWidgetItem(thumbnail_icon(Path(theme.preview)),
                                   f"{theme.name}\n{w}×{h}")
            item.setData(Qt.ItemDataRole.UserRole, theme.path)
            item.setData(Qt.ItemDataRole.UserRole + 1, theme.name)
            self._list.addItem(item)
        self._status.setText(result.message)

    # ── Actions ───────────────────────────────────────────────────────

    def _selected(self) -> tuple[str, str] | None:
        """Return (theme_path, theme_name) or None if nothing selected."""
        log.debug("_selected")
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Select a theme first.")
            return None
        path = str(item.data(Qt.ItemDataRole.UserRole))
        name = str(item.data(Qt.ItemDataRole.UserRole + 1))
        return path, name

    def _on_apply(self) -> None:
        log.info("_on_apply")
        selected = self._selected()
        if selected is None:
            return
        key = self._device_key()
        if key is None:
            return
        path, _name = selected
        result = self.dispatch(LoadTheme(key=key, path=Path(path)))
        self._status.setText(result.message)

    def _on_delete(self) -> None:
        log.info("_on_delete")
        selected = self._selected()
        if selected is None:
            return
        path, name = selected
        confirm = QMessageBox.question(
            self,
            "Delete theme?",
            f"Delete theme {name!r} from disk?  This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = self.dispatch(DeleteTheme(path=Path(path)))
        self._status.setText(result.message)   # re-lists on the event

    def _on_themes_changed(self, event: object) -> None:
        log.info("_on_themes_changed: %s %s -> refreshing",
                 type(event).__name__, getattr(event, "theme_name", ""))
        self.refresh()

    def _on_save(self) -> None:
        key = self._device_key()
        if key is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Save theme", "Theme name:",
        )
        name = name.strip()
        log.info("_on_save: key=%s name=%r accepted=%s", key, name, accepted)
        if not accepted or not name:
            return

        result = self.dispatch(SaveTheme(key=key, name=name))
        if not result.ok and result.target_exists:
            log.info("_on_save: %r exists — prompting for overwrite", name)
            confirm = QMessageBox.question(
                self,
                "Overwrite theme?",
                f"A theme named {name!r} already exists.\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                log.info("_on_save: user declined overwrite of %r", name)
                self._status.setText("Save cancelled — choose a different name")
                return
            log.info("_on_save: user confirmed overwrite of %r", name)
            result = self.dispatch(SaveTheme(key=key, name=name, overwrite=True))

        self._status.setText(result.message)   # re-lists on the event

    def _on_export(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        key = self._device_key()
        if key is None:
            return
        _path, name = selected
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export theme", f"{name}.tr",
            "Theme archive (*.tr);;All files (*)",
        )
        if not dest:
            return
        log.info("_on_export: key=%s theme=%r dest=%s", key, name, dest)
        result = self.dispatch(ExportTheme(
            key=key, theme_name=name, archive_path=Path(dest),
        ))
        self._status.setText(result.message)

    def _on_import(self) -> None:
        key = self._device_key()
        if key is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Import theme archive", "",
            "Theme archive (*.tr *.zip);;All files (*)",
        )
        if not source:
            return
        log.info("_on_import: key=%s source=%s", key, source)
        result = self.dispatch(ImportTheme(key=key, archive_path=Path(source)))
        self._status.setText(result.message)   # re-lists on the event

    # ── Create-from-… ─────────────────────────────────────────────────

    def _on_create_from_image(self) -> None:
        log.info("_on_create_from_image")
        key = self._device_key()
        if key is None:
            return
        size = self._target_resolution(key)
        if size is None:
            return
        target_w, target_h = size

        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a source image", "",
            f"Images ({MEDIA.patterns(MediaKind.IMAGE)})",
        )
        if not source:
            return

        from ..image_crop import ImageCropDialog
        dialog = ImageCropDialog(self)
        dialog.load_image(source, target_w=target_w, target_h=target_h)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Image crop cancelled.")
            return
        cropped = dialog.cropped()
        if cropped is None or not isinstance(cropped, QImage):
            self._status.setText(
                "Image crop produced no output — try a different file.",
            )
            return

        # Stage the crop + dispatch LoadImage, which copies it into a theme
        # NAMED after the staged file.  A random tempfile name made every crop
        # a new theme called ``trcc-crop-<random>``; the source's own name
        # makes a re-crop of the same picture replace its theme.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="trcc-crop-") as scratch:
            tmp_path = Path(scratch) / f"{Path(source).stem}.png"
            # No explicit format: Qt reads it from the .png suffix.  PySide6
            # 6.11 raises ValueError on b"PNG" (what this passed), while its
            # stubs reject "PNG" -- the suffix satisfies both.
            if not cropped.save(str(tmp_path)):
                self._status.setText(
                    "Failed to write cropped image to a temporary file.",
                )
                return
            result = self.dispatch(LoadImage(key=key, path=tmp_path))
            self._status.setText(result.message)
            if result.ok:
                self.refresh()

    def _on_create_from_video(self) -> None:
        log.info("_on_create_from_video")
        key = self._device_key()
        if key is None:
            return
        # No ``_target_resolution`` here any more: ``ExportVideoClip``
        # resolves the canvas from the device (or the registry) itself, and
        # a second lookup at the call site is a second thing to get wrong.
        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a source video", "",
            f"Videos ({MEDIA.patterns(MediaKind.ANIMATED)})",
        )
        if not source:
            return

        from ..video_crop import VideoCropDialog
        dialog = VideoCropDialog(self.app, self.bus, key, self)
        if not dialog.load_video(Path(source)):
            # The dialog already showed the Result's own wording; repeating
            # a guess about ffprobe here would contradict it.
            self._status.setText(f"Couldn't load {Path(source).name}.")
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Video crop cancelled.")
            return
        output = dialog.output_path()
        if output is None:
            self._status.setText("Video crop produced no output.")
            return

        from ....core.commands import LoadVideo
        result = self.dispatch(LoadVideo(key=key, path=output))
        self._status.setText(result.message)
        if result.ok:
            self.refresh()
