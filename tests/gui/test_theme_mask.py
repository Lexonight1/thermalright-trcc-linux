"""Tests for UCThemeMask — custom mask scan, delete, and MaskPanel."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QImage

from tests.conftest import make_test_surface

# ── Helpers ──────────────────────────────────────────────────────────

def _make_mask_dir(parent: Path, name: str) -> Path:
    """Create a minimal mask directory with 01.png and Theme.png."""
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    make_test_surface(320, 320, (255, 0, 0, 128)).save(str(d / '01.png'))
    make_test_surface(120, 120, (0, 0, 0)).save(str(d / 'Theme.png'))
    return d


# ── Path tests ───────────────────────────────────────────────────────

class TestUserMasksDir:
    """get_user_masks_dir returns correct path structure."""

    def test_resolution_in_path(self):
        from trcc.legacy.core.paths import get_user_masks_dir
        result = get_user_masks_dir(480, 480)
        assert 'zt480480' in result
        assert '.trcc-user' in result

    def test_different_from_cloud_dir(self):
        from trcc.legacy.core.paths import get_user_masks_dir, get_web_masks_dir
        assert get_user_masks_dir(320, 320) != get_web_masks_dir(320, 320)


# ── MaskItem tests ───────────────────────────────────────────────────

class TestMaskItemCustom:
    """MaskItem.is_custom field."""

    def test_default_not_custom(self):
        from trcc.legacy.core.models import MaskItem
        item = MaskItem(name='000a')
        assert not item.is_custom

    def test_custom_flag(self):
        from trcc.legacy.core.models import MaskItem
        item = MaskItem(name='custom_001', is_custom=True)
        assert item.is_custom


# ── UCThemeMask scan tests ───────────────────────────────────────────

@pytest.fixture()
def _app():
    """Ensure QApplication exists for widget tests."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])


@pytest.mark.usefixtures("_app")
class TestRefreshMasks:
    """UCThemeMask.refresh_masks scans both cloud and user dirs."""

    def test_scans_user_masks_dir(self, tmp_path):
        """Custom masks from user dir appear in grid."""
        cloud_dir = tmp_path / 'cloud'
        user_dir = tmp_path / 'user'
        _make_mask_dir(cloud_dir, '000a')
        _make_mask_dir(user_dir, 'custom_001')

        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = cloud_dir
        panel._resolution = '320x320'

        with patch.object(panel, '_user_masks_dir', return_value=user_dir):
            panel.refresh_masks()

        names = [w.item_info.name for w in panel.item_widgets
                 if hasattr(w, 'item_info')]
        assert 'custom_001' in names
        assert '000a' in names

    def test_user_masks_marked_custom(self, tmp_path):
        """User masks have is_custom=True."""
        user_dir = tmp_path / 'user'
        _make_mask_dir(user_dir, 'custom_001')

        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = tmp_path / 'empty_cloud'
        panel._resolution = '320x320'

        with patch.object(panel, '_user_masks_dir', return_value=user_dir):
            panel.refresh_masks()

        custom_items = [w.item_info for w in panel.item_widgets
                        if hasattr(w, 'item_info') and w.item_info.is_custom]
        assert len(custom_items) == 1
        assert custom_items[0].name == 'custom_001'

    def test_user_masks_appear_before_cloud(self, tmp_path):
        """Custom masks are listed before cloud masks."""
        cloud_dir = tmp_path / 'cloud'
        user_dir = tmp_path / 'user'
        _make_mask_dir(cloud_dir, '000a')
        _make_mask_dir(user_dir, 'custom_001')

        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = cloud_dir
        panel._resolution = '320x320'

        with patch.object(panel, '_user_masks_dir', return_value=user_dir):
            panel.refresh_masks()

        local_items = [w.item_info for w in panel.item_widgets
                       if hasattr(w, 'item_info') and w.item_info.is_local]
        assert len(local_items) >= 2
        # Custom mask first
        assert local_items[0].is_custom

    def test_empty_user_dir(self, tmp_path):
        """No crash when user masks dir doesn't exist."""
        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = tmp_path / 'cloud'
        panel._resolution = '320x320'

        nonexistent = tmp_path / 'nonexistent'
        with patch.object(panel, '_user_masks_dir', return_value=nonexistent):
            panel.refresh_masks()  # should not raise


# ── Delete mask tests ────────────────────────────────────────────────

@pytest.mark.usefixtures("_app")
class TestDeleteCustomMask:
    """UCThemeMask._delete_custom_mask removes directory and refreshes."""

    def test_deletes_directory(self, tmp_path):
        """Delete removes the mask directory from disk."""
        user_dir = tmp_path / 'user'
        mask_dir = _make_mask_dir(user_dir, 'custom_001')

        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = tmp_path / 'cloud'
        panel._resolution = '320x320'

        with patch.object(panel, '_user_masks_dir', return_value=user_dir):
            panel._delete_custom_mask(mask_dir)

        assert not mask_dir.exists()

    def test_refreshes_grid_after_delete(self, tmp_path):
        """Grid refreshes after deletion."""
        user_dir = tmp_path / 'user'
        mask_dir = _make_mask_dir(user_dir, 'custom_001')

        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel.mask_directory = tmp_path / 'cloud'
        panel._resolution = '320x320'

        with patch.object(panel, 'refresh_masks') as mock_refresh:
            panel._delete_custom_mask(mask_dir)

        mock_refresh.assert_called_once()


# ── Parse resolution tests ───────────────────────────────────────────

@pytest.mark.usefixtures("_app")
class TestParseResolution:
    """UCThemeMask._parse_resolution parses resolution string."""

    def test_square(self):
        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel._resolution = '480x480'
        assert panel._parse_resolution() == (480, 480)

    def test_rectangular(self):
        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask
        panel = UCThemeMask()
        panel._resolution = '640x480'
        assert panel._parse_resolution() == (640, 480)


# ── MaskPanel tests ──────────────────────────────────────────────────

@pytest.mark.usefixtures("_app")
class TestMaskPanel:
    """MaskPanel — X/Y inputs and eye toggle."""

    def test_has_upload_action(self):
        """MaskPanel includes Upload as second action button."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        assert 'Upload' in panel.actions
        assert 'Load' in panel.actions

    def test_set_position(self):
        """set_position updates entry fields."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        panel.set_position(100, 200)
        assert panel.entry_x.text() == '100'
        assert panel.entry_y.text() == '200'

    def test_set_position_no_signal(self):
        """set_position doesn't emit mask_position_changed."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        panel.mask_position_changed = MagicMock()
        panel.set_position(50, 75)
        panel.mask_position_changed.emit.assert_not_called()

    def test_position_signal_on_text_change(self):
        """Typing in X/Y emits mask_position_changed."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        signals = []
        panel.mask_position_changed.connect(lambda x, y: signals.append((x, y)))
        panel.entry_x.setText('42')
        assert (42, 0) in signals

    def test_eye_toggle(self):
        """Eye toggle emits mask_visibility_toggled."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        signals = []
        panel.mask_visibility_toggled.connect(lambda v: signals.append(v))
        panel.eye_btn.click()
        # Starts visible=True, click toggles to False
        assert signals == [False]

    def test_set_mask_visible(self):
        """set_mask_visible updates internal state."""
        from trcc.legacy.ui.gui.display_mode_panels import MaskPanel
        panel = MaskPanel()
        panel.set_mask_visible(False)
        assert not panel._mask_visible


# ── Save custom mask tests ───────────────────────────────────────────

class TestSaveCustomMask:
    """_save_and_apply_custom_mask creates mask files correctly."""

    def test_saves_mask_files(self, tmp_path):
        """Saving creates 01.png and Theme.png."""
        from trcc.legacy.ui.gui.trcc_app import TRCCApp

        user_dir = tmp_path / 'user_masks'
        cropped = make_test_surface(320, 320, (0, 255, 0, 200))

        app = MagicMock(spec=TRCCApp)
        app._active_lcd.return_value.display.lcd_size = (320, 320)
        app._mask_upload_filename = 'my_overlay'
        app.uc_theme_mask = MagicMock()
        app.uc_preview = MagicMock()

        app._trcc = MagicMock()
        app._trcc.settings._path_resolver.user_masks_dir.return_value = str(user_dir)
        TRCCApp._save_and_apply_custom_mask(app, cropped)

        mask_dir = user_dir / 'my_overlay'
        assert mask_dir.exists()
        assert (mask_dir / '01.png').exists()
        assert (mask_dir / 'Theme.png').exists()

    def test_thumbnail_120x120(self, tmp_path):
        """Theme.png thumbnail is 120x120."""
        from trcc.legacy.ui.gui.trcc_app import TRCCApp

        user_dir = tmp_path / 'user_masks'
        cropped = make_test_surface(320, 320, (0, 0, 255, 128))

        app = MagicMock(spec=TRCCApp)
        app._active_lcd.return_value.display.lcd_size = (320, 320)
        app._mask_upload_filename = 'test_mask'
        app.uc_theme_mask = MagicMock()
        app.uc_preview = MagicMock()

        app._trcc = MagicMock()
        app._trcc.settings._path_resolver.user_masks_dir.return_value = str(user_dir)
        TRCCApp._save_and_apply_custom_mask(app, cropped)

        thumb = QImage(str(user_dir / 'test_mask' / 'Theme.png'))
        assert thumb.width() == 120 and thumb.height() == 120

    def test_dedup_existing_name(self, tmp_path):
        """Duplicate name gets _2 suffix."""
        from trcc.legacy.ui.gui.trcc_app import TRCCApp

        user_dir = tmp_path / 'user_masks'
        _make_mask_dir(user_dir, 'my_mask')
        cropped = make_test_surface(320, 320, (255, 0, 0, 255))

        app = MagicMock(spec=TRCCApp)
        app._active_lcd.return_value.display.lcd_size = (320, 320)
        app._mask_upload_filename = 'my_mask'
        app.uc_theme_mask = MagicMock()
        app.uc_preview = MagicMock()

        app._trcc = MagicMock()
        app._trcc.settings._path_resolver.user_masks_dir.return_value = str(user_dir)
        TRCCApp._save_and_apply_custom_mask(app, cropped)

        assert (user_dir / 'my_mask_2').exists()

    def test_applies_mask(self, tmp_path):
        """Saving calls apply_mask on handler."""
        from trcc.legacy.ui.gui.trcc_app import TRCCApp

        user_dir = tmp_path / 'user_masks'
        cropped = make_test_surface(320, 320, (0, 0, 0, 255))

        app = MagicMock(spec=TRCCApp)
        app._active_lcd.return_value.display.lcd_size = (320, 320)
        app._mask_upload_filename = 'applied'
        app.uc_theme_mask = MagicMock()
        app.uc_preview = MagicMock()

        app._trcc = MagicMock()
        app._trcc.settings._path_resolver.user_masks_dir.return_value = str(user_dir)
        TRCCApp._save_and_apply_custom_mask(app, cropped)

        app._active_lcd.return_value.apply_mask.assert_called_once()
        item = app._active_lcd.return_value.apply_mask.call_args[0][0]
        assert item.is_custom
        assert item.name == 'applied'
