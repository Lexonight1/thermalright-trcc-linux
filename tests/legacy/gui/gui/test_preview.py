"""
Tests for gui.uc_device and gui.uc_preview.

Covers:
- _get_device_images() fallback chains (HID generic, direct, underscores, spaces,
  model map, name substring, non-HID default, no match)
- DEVICE_IMAGE_MAP validation
- UCDevice: construction, button building, selection, signals, hot-plug, restore
- UCPreview: RESOLUTION_OFFSETS structure, construction, coordinate scaling,
  nudge, resolution change, status, video controls, playing state, lcd_size
"""

from __future__ import annotations

import os

# Must set before ANY Qt import
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from unittest.mock import patch

import pytest
from PySide6.QtGui import QPixmap

from trcc.legacy.ui.gui.uc_device import (
    DEVICE_IMAGE_MAP,
    UCDevice,
    _get_device_images,
)
from trcc.legacy.ui.gui.uc_preview import UCPreview

# ============================================================================
# Helpers
# ============================================================================

def _null_pixmap() -> QPixmap:
    """Return a null (empty) QPixmap."""
    return QPixmap()


def _valid_pixmap(w: int = 50, h: int = 50) -> QPixmap:
    """Return a non-null QPixmap of given size."""
    pix = QPixmap(w, h)
    pix.fill()
    return pix


# ============================================================================
# Patches applied to every test — avoid real asset I/O and device detection
# ============================================================================

@pytest.fixture(autouse=True)
def _patch_assets():
    """Patch Assets methods so no filesystem access is needed."""
    with (
        patch('trcc.legacy.ui.gui.uc_device.Assets') as mock_assets_dev,
        patch('trcc.legacy.ui.gui.uc_preview.Assets') as mock_assets_prev,
    ):
        # Default: exists -> False, load_pixmap -> null pixmap
        for mock_assets in (mock_assets_dev, mock_assets_prev):
            mock_assets.exists.return_value = False
            mock_assets.load_pixmap.return_value = _null_pixmap()
            mock_assets.get.return_value = None
            mock_assets.get_localized.return_value = 'P0CZTV.png'
            # Propagate class-level constants needed by UCDevice/UCPreview _setup_ui
            mock_assets.SIDEBAR_BG = 'A0sidebar.png'
            mock_assets.SENSOR_BTN = 'A1sensor.png'
            mock_assets.SENSOR_BTN_ACTIVE = 'A1sensora.png'
            mock_assets.ABOUT_BTN = 'A1about.png'
            mock_assets.ABOUT_BTN_ACTIVE = 'A1abouta.png'
            mock_assets.VIDEO_CONTROLS_BG = 'ucVideo.png'
            mock_assets.ICON_PLAY = 'Pplay.png'
            mock_assets.ICON_PAUSE = 'Ppause.png'
        yield mock_assets_dev


@pytest.fixture(autouse=True)
def _patch_set_background_pixmap():
    """Patch set_background_pixmap to avoid real file I/O."""
    with patch('trcc.legacy.ui.gui.uc_device.set_background_pixmap'):
        with patch('trcc.legacy.ui.gui.uc_preview.set_background_pixmap'):
            yield


# ============================================================================
# _get_device_images() tests
# ============================================================================

class TestGetDeviceImages:
    """Test _get_device_images() fallback chain."""

    def test_hid_generic_returns_none(self, qapp: object) -> None:
        """HID protocol with generic A1CZTV button_image returns (None, None)."""
        info = {'protocol': 'hid', 'button_image': 'A1CZTV'}
        assert _get_device_images(info) == (None, None)

    def test_button_image_found_directly(self, qapp: object) -> None:
        """button_image exists in Assets -> returns (name, name+'a')."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1FROZEN WARFRAME'
            info = {'button_image': 'A1FROZEN WARFRAME', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1FROZEN WARFRAME', 'A1FROZEN WARFRAMEa')

    def test_button_image_underscores_to_spaces(self, qapp: object) -> None:
        """button_image with underscores replaced by spaces matches."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            # Direct name not found, spaced version found
            m.exists.side_effect = lambda n: n == 'A1FROZEN WARFRAME'
            info = {'button_image': 'A1FROZEN_WARFRAME', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1FROZEN WARFRAME', 'A1FROZEN WARFRAMEa')

    def test_button_image_spaces_to_underscores(self, qapp: object) -> None:
        """button_image with spaces replaced by underscores matches."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            # Neither direct nor spaced found, underscored found
            m.exists.side_effect = lambda n: n == 'A1FROZEN_WARFRAME'
            info = {'button_image': 'A1FROZEN WARFRAME', 'protocol': 'scsi'}
            result = _get_device_images(info)
            assert result == ('A1FROZEN_WARFRAME', 'A1FROZEN_WARFRAMEa')

    def test_model_field_lookup(self, qapp: object) -> None:
        """model field found in DEVICE_IMAGE_MAP -> returns mapped image."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1CZ1'
            info = {'button_image': '', 'model': 'CZ1', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1CZ1', 'A1CZ1a')

    def test_name_substring_match(self, qapp: object) -> None:
        """name field substring matches a DEVICE_IMAGE_MAP key."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1LC1'
            info = {'button_image': '', 'model': '', 'name': 'My LC1 Device', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1LC1', 'A1LC1a')

    def test_name_substring_case_insensitive(self, qapp: object) -> None:
        """Name substring match is case insensitive."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1CZTV'
            info = {'button_image': '', 'model': '', 'name': 'cztv cooler', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1CZTV', 'A1CZTVa')

    def test_non_hid_default_fallback(self, qapp: object) -> None:
        """Non-HID device with no match falls back to A1CZTV if asset exists."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            # Only A1CZTV exists
            m.exists.side_effect = lambda n: n == 'A1CZTV'
            info = {'button_image': '', 'model': '', 'name': 'Unknown XYZ', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1CZTV', 'A1CZTVa')

    def test_hid_no_match_returns_none(self, qapp: object) -> None:
        """HID device with no match returns (None, None) -- no default fallback."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.return_value = False
            info = {'button_image': 'Unknown', 'model': '', 'name': 'Unknown', 'protocol': 'hid'}
            assert _get_device_images(info) == (None, None)

    def test_no_match_at_all_returns_none(self, qapp: object) -> None:
        """When nothing matches and A1CZTV asset missing, returns (None, None)."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.return_value = False
            info = {'button_image': '', 'model': '', 'name': 'Unknown', 'protocol': 'scsi'}
            assert _get_device_images(info) == (None, None)

    def test_empty_device_info(self, qapp: object) -> None:
        """Empty dict returns (None, None) when no assets exist."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.return_value = False
            assert _get_device_images({}) == (None, None)

    def test_button_image_empty_string_skips_to_model(self, qapp: object) -> None:
        """Empty button_image skips directly to model lookup."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1LF12'
            info = {'button_image': '', 'model': 'LF12', 'protocol': 'scsi'}
            assert _get_device_images(info) == ('A1LF12', 'A1LF12a')

    def test_hid_non_generic_button_image(self, qapp: object) -> None:
        """HID device with non-generic button_image uses it."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1FROZEN WARFRAME PRO'
            info = {'button_image': 'A1FROZEN WARFRAME PRO', 'protocol': 'hid'}
            assert _get_device_images(info) == ('A1FROZEN WARFRAME PRO', 'A1FROZEN WARFRAME PROa')

    def test_model_key_not_in_map(self, qapp: object) -> None:
        """Model field that is not in DEVICE_IMAGE_MAP falls through to name."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            m.exists.side_effect = lambda n: n == 'A1LC2'
            info = {
                'button_image': '', 'model': 'UNKNOWN_MODEL',
                'name': 'LC2 device', 'protocol': 'scsi',
            }
            assert _get_device_images(info) == ('A1LC2', 'A1LC2a')

    def test_model_asset_not_found_falls_to_name(self, qapp: object) -> None:
        """Model maps to an image that doesn't exist -> falls through to name."""
        with patch('trcc.legacy.ui.gui.uc_device.Assets') as m:
            # Model maps to A1CZ1 which doesn't exist, but name matches CZTV
            m.exists.side_effect = lambda n: n == 'A1CZTV'
            info = {
                'button_image': '', 'model': 'CZ1',
                'name': 'CZTV cooler', 'protocol': 'scsi',
            }
            assert _get_device_images(info) == ('A1CZTV', 'A1CZTVa')


# ============================================================================
# DEVICE_IMAGE_MAP validation
# ============================================================================

class TestDeviceImageMap:
    """Validate the DEVICE_IMAGE_MAP constant."""

    def test_all_values_are_strings(self, qapp: object) -> None:
        for key, val in DEVICE_IMAGE_MAP.items():
            assert isinstance(key, str), f"Key {key!r} is not a string"
            assert isinstance(val, str), f"Value for {key!r} is not a string"

    def test_all_values_start_with_a1(self, qapp: object) -> None:
        for key, val in DEVICE_IMAGE_MAP.items():
            assert val.startswith('A1'), f"Value for {key!r} doesn't start with 'A1': {val!r}"

    def test_map_is_nonempty(self, qapp: object) -> None:
        assert len(DEVICE_IMAGE_MAP) > 30, "Expected 30+ entries in DEVICE_IMAGE_MAP"

    def test_known_entries_present(self, qapp: object) -> None:
        assert 'CZTV' in DEVICE_IMAGE_MAP
        assert 'CZ1' in DEVICE_IMAGE_MAP
        assert 'LC1' in DEVICE_IMAGE_MAP
        assert 'LF8' in DEVICE_IMAGE_MAP


# ============================================================================
# UCDevice tests
# ============================================================================

def _make_device(path: str = '/dev/sg0', name: str = 'LCD',
                 protocol: str = 'scsi', model: str = '',
                 button_image: str = '') -> dict:
    """Create a device info dict for testing."""
    return {
        'path': path, 'name': name, 'protocol': protocol,
        'model': model, 'button_image': button_image,
        'vid': 0x87CD, 'pid': 0x70DB,
    }


class TestUCDeviceConstruction:
    """Test UCDevice widget construction."""

    def test_construction_no_devices(self, qapp: object) -> None:
        panel = UCDevice()
        assert panel.devices == []
        assert panel.device_buttons == []
        assert panel.selected_device is None

    def test_construction_with_devices(self, qapp: object) -> None:
        dev = _make_device()
        panel = UCDevice(detect_fn=lambda: [dev])
        assert len(panel.devices) == 1
        assert len(panel.device_buttons) == 1
        assert panel.selected_device is dev

    def test_construction_with_multiple_devices(self, qapp: object) -> None:
        devices = [_make_device(f'/dev/sg{i}', f'LCD {i}') for i in range(3)]
        panel = UCDevice(detect_fn=lambda: devices)
        assert len(panel.device_buttons) == 3
        # First device auto-selected
        assert panel.selected_device is devices[0]


class TestUCDeviceBuildButtons:
    """Test _build_device_buttons() behavior."""

    def test_no_devices_shows_labels(self, qapp: object) -> None:
        panel = UCDevice()
        # Use isHidden() — isVisible() requires the entire parent chain to be shown
        assert not panel.no_devices_label.isHidden()
        assert not panel.hint_label.isHidden()

    def test_devices_hide_labels(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        assert panel.no_devices_label.isHidden()
        assert panel.hint_label.isHidden()

    def test_button_text_fallback(self, qapp: object) -> None:
        """When no image is found, button shows full device name."""
        dev = _make_device(name='My Custom LCD Device')
        panel = UCDevice(detect_fn=lambda: [dev])
        btn = panel.device_buttons[0]
        assert btn.text() == 'My Custom LCD Device'

    def test_buttons_are_checkable(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        assert panel.device_buttons[0].isCheckable()


class TestUCDeviceSelection:
    """Test device selection, deselection, and signals."""

    def test_select_device_emits_signal(self, qapp: object) -> None:
        dev = _make_device()
        panel = UCDevice(detect_fn=lambda: [dev])
        received: list[dict] = []
        panel.device_selected.connect(lambda d: received.append(d))
        panel._select_device(dev)
        assert len(received) == 1
        assert received[0] == dev

    def test_select_device_deselects_header_buttons(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        panel.sensor_btn.setChecked(True)
        panel.about_btn.setChecked(True)
        panel._select_device(_make_device())
        assert not panel.sensor_btn.isChecked()
        assert not panel.about_btn.isChecked()

    def test_select_device_checks_correct_button(self, qapp: object) -> None:
        dev_a = _make_device('/dev/sg0', 'A')
        dev_b = _make_device('/dev/sg1', 'B')
        panel = UCDevice(detect_fn=lambda: [dev_a, dev_b])
        panel._select_device(dev_b)
        assert not panel.device_buttons[0].isChecked()
        assert panel.device_buttons[1].isChecked()

    def test_deselect_all_devices(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device('/dev/sg0'), _make_device('/dev/sg1')])
        panel._deselect_all_devices()
        for btn in panel.device_buttons:
            assert not btn.isChecked()

    def test_deselect_header_buttons(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        panel.sensor_btn.setChecked(True)
        panel.about_btn.setChecked(True)
        panel._deselect_header_buttons()
        assert not panel.sensor_btn.isChecked()
        assert not panel.about_btn.isChecked()


class TestUCDeviceHeaderClicks:
    """Test home/about button state transitions."""

    def test_on_home_clicked(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        home_fired: list[bool] = []
        panel.home_clicked.connect(lambda: home_fired.append(True))
        panel._on_home_clicked()
        assert panel.sensor_btn.isChecked()
        assert not panel.about_btn.isChecked()
        for btn in panel.device_buttons:
            assert not btn.isChecked()
        assert home_fired

    def test_on_about_clicked(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        about_fired: list[bool] = []
        panel.about_clicked.connect(lambda: about_fired.append(True))
        panel._on_about_clicked()
        assert panel.about_btn.isChecked()
        assert not panel.sensor_btn.isChecked()
        for btn in panel.device_buttons:
            assert not btn.isChecked()
        assert about_fired

    def test_home_then_about_toggles(self, qapp: object) -> None:
        panel = UCDevice(detect_fn=lambda: [_make_device()])
        panel._on_home_clicked()
        assert panel.sensor_btn.isChecked()
        panel._on_about_clicked()
        assert not panel.sensor_btn.isChecked()
        assert panel.about_btn.isChecked()


class TestUCDeviceUpdateDevices:
    """Test hot-plug update_devices() behavior."""

    def test_same_paths_no_rebuild(self, qapp: object) -> None:
        panel = UCDevice()
        dev = _make_device('/dev/sg0')
        panel.devices = [dev]
        panel._build_device_buttons([dev])
        old_buttons = list(panel.device_buttons)
        panel.update_devices([dev])
        # Same paths -> no rebuild, same button objects
        assert panel.device_buttons == old_buttons

    def test_changed_paths_rebuilds(self, qapp: object) -> None:
        panel = UCDevice()
        dev_a = _make_device('/dev/sg0')
        panel.devices = [dev_a]
        panel._build_device_buttons([dev_a])
        dev_b = _make_device('/dev/sg1')
        received: list[dict] = []
        panel.device_selected.connect(lambda d: received.append(d))
        panel.update_devices([dev_b])
        assert len(panel.device_buttons) == 1
        assert panel.devices == [dev_b]
        # New device auto-selected
        assert panel.selected_device is dev_b

    def test_update_devices_restores_selection(self, qapp: object) -> None:
        panel = UCDevice()
        dev_a = _make_device('/dev/sg0')
        dev_b = _make_device('/dev/sg1')
        panel.devices = [dev_a, dev_b]
        panel._build_device_buttons([dev_a, dev_b])
        panel.selected_device = dev_b
        # Add a third device but keep the first two
        dev_c = _make_device('/dev/sg2')
        panel.update_devices([dev_a, dev_b, dev_c])
        assert panel.selected_device is dev_b

    def test_update_to_empty(self, qapp: object) -> None:
        panel = UCDevice()
        dev = _make_device()
        panel.devices = [dev]
        panel._build_device_buttons([dev])
        panel.selected_device = dev
        panel.update_devices([])
        assert panel.selected_device is None
        assert panel.device_buttons == []

    def test_update_devices_prev_gone_selects_first(self, qapp: object) -> None:
        """When previously selected device is gone, selects first new device."""
        panel = UCDevice()
        dev_a = _make_device('/dev/sg0')
        dev_b = _make_device('/dev/sg1')
        panel.devices = [dev_a]
        panel._build_device_buttons([dev_a])
        panel.selected_device = dev_a
        panel.update_devices([dev_b])
        assert panel.selected_device is dev_b


class TestUCDeviceRestoreSelection:
    """Test restore_device_selection()."""

    def test_restore_device_selection(self, qapp: object) -> None:
        dev_a = _make_device('/dev/sg0', 'A')
        dev_b = _make_device('/dev/sg1', 'B')
        panel = UCDevice(detect_fn=lambda: [dev_a, dev_b])
        panel._select_device(dev_b)
        # Simulate going to About
        panel._on_about_clicked()
        assert panel.about_btn.isChecked()
        # Restore
        panel.restore_device_selection()
        assert not panel.about_btn.isChecked()
        assert not panel.sensor_btn.isChecked()
        assert panel.device_buttons[1].isChecked()
        assert not panel.device_buttons[0].isChecked()

    def test_restore_no_selection_noop(self, qapp: object) -> None:
        """restore_device_selection() with no selected_device is a safe no-op."""
        panel = UCDevice()
        panel.restore_device_selection()
        assert not panel.sensor_btn.isChecked()
        assert not panel.about_btn.isChecked()


class TestUCDeviceButtonUpdate:
    """Test update_device_button() post-handshake."""

    def test_update_device_button_with_image(self, qapp: object) -> None:
        """After handshake, button icon is updated if image is found."""
        dev = _make_device(name='Generic')
        panel = UCDevice(detect_fn=lambda: [dev])
        # Simulate handshake resolving the product
        dev['button_image'] = 'A1FROZEN WARFRAME'
        with patch('trcc.legacy.ui.gui.uc_device._get_device_images') as mock_img:
            mock_img.return_value = ('A1FROZEN WARFRAME', 'A1FROZEN WARFRAMEa')
            with patch('trcc.legacy.ui.gui.uc_device.Assets') as mock_a:
                pix = _valid_pixmap(140, 50)
                mock_a.load_pixmap.return_value = pix
                panel.update_device_button(dev)
        btn = panel.device_buttons[0]
        assert btn.text() == ''  # Text cleared when icon set

    def test_update_device_button_no_match(self, qapp: object) -> None:
        """If _get_device_images returns (None, None), button is not changed."""
        dev = _make_device(name='Unknown')
        panel = UCDevice(detect_fn=lambda: [dev])
        original_text = panel.device_buttons[0].text()
        with patch('trcc.legacy.ui.gui.uc_device._get_device_images', return_value=(None, None)):
            panel.update_device_button(dev)
        assert panel.device_buttons[0].text() == original_text

    def test_update_device_button_wrong_device(self, qapp: object) -> None:
        """Updating a device_info not in buttons list is a safe no-op."""
        dev = _make_device(name='Known')
        panel = UCDevice(detect_fn=lambda: [dev])
        other_dev = _make_device('/dev/sg9', 'Other')
        panel.update_device_button(other_dev)  # Should not raise


class TestUCDeviceGetters:
    """Test get_selected_device() and get_devices()."""

    def test_get_selected_device(self, qapp: object) -> None:
        dev = _make_device()
        panel = UCDevice(detect_fn=lambda: [dev])
        assert panel.get_selected_device() is dev

    def test_get_selected_device_none(self, qapp: object) -> None:
        panel = UCDevice()
        assert panel.get_selected_device() is None

    def test_get_devices(self, qapp: object) -> None:
        devices = [_make_device(f'/dev/sg{i}') for i in range(3)]
        panel = UCDevice(detect_fn=lambda: devices)
        assert panel.get_devices() == devices

    def test_get_devices_empty(self, qapp: object) -> None:
        panel = UCDevice()
        assert panel.get_devices() == []


class TestUCDeviceDelegateSignals:
    """Test that delegate signals fire with correct CMDs."""

    def test_select_device_delegate(self, qapp: object) -> None:
        dev = _make_device()
        panel = UCDevice(detect_fn=lambda: [dev])
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._select_device(dev)
        cmds = [r[0] for r in received]
        assert UCDevice.CMD_SELECT_DEVICE in cmds

    def test_home_delegate(self, qapp: object) -> None:
        panel = UCDevice()
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_home_clicked()
        cmds = [r[0] for r in received]
        assert UCDevice.CMD_HOME in cmds

    def test_about_delegate(self, qapp: object) -> None:
        panel = UCDevice()
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_about_clicked()
        cmds = [r[0] for r in received]
        assert UCDevice.CMD_ABOUT in cmds


# ============================================================================
# UCPreview.RESOLUTION_OFFSETS validation
# ============================================================================

class TestResolutionOffsets:
    """Validate RESOLUTION_OFFSETS structure and values."""

    def test_all_entries_are_5_tuples(self, qapp: object) -> None:
        for key, val in UCPreview.RESOLUTION_OFFSETS.items():
            assert isinstance(val, tuple), f"Entry for {key} is not a tuple"
            assert len(val) == 5, f"Entry for {key} has {len(val)} elements, expected 5"

    def test_all_keys_are_2_tuples(self, qapp: object) -> None:
        for key in UCPreview.RESOLUTION_OFFSETS:
            assert isinstance(key, tuple) and len(key) == 2, f"Key {key} is not a 2-tuple"
            assert all(isinstance(v, int) for v in key), f"Key {key} has non-int values"

    def test_expected_count(self, qapp: object) -> None:
        assert len(UCPreview.RESOLUTION_OFFSETS) == 32

    def test_all_offsets_within_500x500(self, qapp: object) -> None:
        for key, (left, top, w, h, _) in UCPreview.RESOLUTION_OFFSETS.items():
            assert left + w <= 500, f"Entry for {key}: left+w={left + w} exceeds 500"
            assert top + h <= 500, f"Entry for {key}: top+h={top + h} exceeds 500"
            assert left >= 0, f"Entry for {key}: negative left={left}"
            assert top >= 0, f"Entry for {key}: negative top={top}"

    def test_all_frame_images_are_strings(self, qapp: object) -> None:
        for key, (_, _, _, _, frame) in UCPreview.RESOLUTION_OFFSETS.items():
            assert isinstance(frame, str), f"Frame for {key} is not a string"
            assert frame.endswith('.png'), f"Frame for {key} doesn't end with .png"

    def test_common_resolutions_present(self, qapp: object) -> None:
        expected = [(320, 320), (240, 240), (480, 480), (360, 360),
                    (320, 240), (1280, 480), (1920, 462)]
        for res in expected:
            assert res in UCPreview.RESOLUTION_OFFSETS, f"{res} not in RESOLUTION_OFFSETS"

    def test_default_offset_is_320x320(self, qapp: object) -> None:
        assert UCPreview.DEFAULT_OFFSET == (90, 90, 320, 320, 'preview_320x320.png')


# ============================================================================
# UCPreview construction
# ============================================================================

class TestUCPreviewConstruction:
    """Test UCPreview construction with various resolutions."""

    @pytest.fixture
    def panel(self, settings_with_resolution):
        """UCPreview built from active settings — mirrors production trcc_app.py."""
        from trcc.legacy.conf import settings
        return UCPreview(settings.width, settings.height)

    def test_default_construction(self, panel: UCPreview) -> None:
        assert panel._lcd_width == 320
        assert panel._lcd_height == 320

    def test_construction_known_resolution(self, qapp: object) -> None:
        panel = UCPreview(width=480, height=480)
        assert panel._lcd_width == 480
        assert panel._lcd_height == 480
        assert panel._offset_info == UCPreview.RESOLUTION_OFFSETS[(480, 480)]

    def test_construction_unknown_resolution_uses_default(self, qapp: object) -> None:
        panel = UCPreview(width=999, height=999)
        assert panel._offset_info == UCPreview.DEFAULT_OFFSET

    def test_construction_widescreen(self, qapp: object) -> None:
        panel = UCPreview(width=1280, height=480)
        assert panel._lcd_width == 1280
        assert panel._lcd_height == 480

    def test_preview_label_created(self, panel: UCPreview) -> None:
        assert panel.preview_label is not None

    def test_status_label_defaults_to_ready(self, panel: UCPreview) -> None:
        assert panel.status_label.text() == 'Ready'

    def test_progress_container_hidden_by_default(self, panel: UCPreview) -> None:
        assert panel.progress_container.isHidden()


# ============================================================================
# UCPreview._widget_to_lcd() coordinate scaling
# ============================================================================

class TestWidgetToLcd:
    """Test _widget_to_lcd() coordinate translation."""

    def test_origin(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        assert panel._widget_to_lcd(0, 0) == (0, 0)

    def test_midpoint_square(self, qapp: object) -> None:
        """Midpoint of preview maps to midpoint of LCD for square 1:1."""
        panel = UCPreview(width=320, height=320)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw // 2, ph // 2)
        assert lx == 160
        assert ly == 160

    def test_full_extent_square(self, qapp: object) -> None:
        """Full extent of preview widget -> full LCD extent (clamped)."""
        panel = UCPreview(width=320, height=320)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw, ph)
        assert lx == 320
        assert ly == 320

    def test_negative_clamps_to_zero(self, qapp: object) -> None:
        """Negative widget coordinates clamp to 0."""
        panel = UCPreview(width=320, height=320)
        lx, ly = panel._widget_to_lcd(-10, -10)
        assert lx == 0
        assert ly == 0

    def test_beyond_extent_clamps(self, qapp: object) -> None:
        """Widget coordinates beyond preview size clamp to LCD extent."""
        panel = UCPreview(width=320, height=320)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw * 2, ph * 2)
        assert lx == 320  # Clamped to lcd_width
        assert ly == 320  # Clamped to lcd_height

    def test_widescreen_scaling(self, qapp: object) -> None:
        """Widescreen: preview width maps to LCD width (different ratios)."""
        panel = UCPreview(width=1280, height=480)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw, ph)
        # Full preview -> full LCD
        assert lx == 1280
        assert ly == 480

    def test_widescreen_midpoint(self, qapp: object) -> None:
        panel = UCPreview(width=1280, height=480)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw // 2, ph // 2)
        assert lx == 640
        assert ly == 240

    def test_small_display_240x240(self, qapp: object) -> None:
        panel = UCPreview(width=240, height=240)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw, ph)
        assert lx == 240
        assert ly == 240

    def test_rectangular_320x240(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=240)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw // 2, ph // 2)
        assert lx == 160
        assert ly == 120

    def test_zero_widget_size_returns_origin(self, qapp: object) -> None:
        """If preview widget size is 0 (safety), returns (0, 0)."""
        panel = UCPreview(width=320, height=320)
        # Force zero preview size
        panel._offset_info = (0, 0, 0, 0, 'test.png')
        assert panel._widget_to_lcd(50, 50) == (0, 0)

    def test_1920x462_extreme_widescreen(self, qapp: object) -> None:
        panel = UCPreview(width=1920, height=462)
        _, _, pw, ph, _ = panel._offset_info
        lx, ly = panel._widget_to_lcd(pw, ph)
        assert lx == 1920
        assert ly == 462


# ============================================================================
# UCPreview._on_nudge() keyboard nudge
# ============================================================================

class TestOnNudge:
    """Test _on_nudge() LCD-scaled keyboard nudges."""

    def test_nudge_right_1px(self, qapp: object) -> None:
        """1px widget nudge maps to at least 1px LCD nudge."""
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(1, 0)
        assert len(received) == 1
        assert received[0][0] >= 1
        assert received[0][1] == 0

    def test_nudge_down_1px(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(0, 1)
        assert received[0][0] == 0
        assert received[0][1] >= 1

    def test_nudge_left_negative(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(-1, 0)
        assert received[0][0] <= -1

    def test_nudge_minimum_1px_guarantee(self, qapp: object) -> None:
        """For large displays, 1px widget nudge must still produce 1px LCD nudge."""
        panel = UCPreview(width=1920, height=462)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(1, 0)
        assert received[0][0] >= 1  # Guaranteed minimum

    def test_nudge_shift_10px(self, qapp: object) -> None:
        """10px widget nudge maps to proportionally larger LCD nudge."""
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(10, 0)
        assert received[0][0] >= 10

    def test_nudge_zero_zero_no_emit_with_zeros(self, qapp: object) -> None:
        """dx=0 and dy=0 emits (0, 0) without minimum guarantee."""
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(0, 0)
        assert received[0] == (0, 0)

    def test_nudge_zero_preview_size_noop(self, qapp: object) -> None:
        """Zero preview size -> no emit (guard return)."""
        panel = UCPreview(width=320, height=320)
        panel._offset_info = (0, 0, 0, 0, 'test.png')
        received: list[tuple[int, int]] = []
        panel.element_nudge.connect(lambda dx, dy: received.append((dx, dy)))
        panel._on_nudge(5, 5)
        assert received == []


# ============================================================================
# UCPreview.set_resolution()
# ============================================================================

class TestSetResolution:
    """Test set_resolution() changes internal state."""

    def test_set_known_resolution(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        panel.set_resolution(480, 480)
        assert panel._lcd_width == 480
        assert panel._lcd_height == 480
        assert panel._offset_info == UCPreview.RESOLUTION_OFFSETS[(480, 480)]

    def test_set_unknown_resolution_uses_default(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        panel.set_resolution(999, 999)
        assert panel._lcd_width == 999
        assert panel._lcd_height == 999
        assert panel._offset_info == UCPreview.DEFAULT_OFFSET

    def test_set_resolution_updates_preview_label_size(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        panel.set_resolution(240, 240)
        expected_w = UCPreview.RESOLUTION_OFFSETS[(240, 240)][2]
        expected_h = UCPreview.RESOLUTION_OFFSETS[(240, 240)][3]
        assert panel.preview_label.width() == expected_w
        assert panel.preview_label.height() == expected_h

    def test_set_resolution_updates_preview_label_position(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        panel.set_resolution(240, 240)
        expected_left = UCPreview.RESOLUTION_OFFSETS[(240, 240)][0]
        expected_top = UCPreview.RESOLUTION_OFFSETS[(240, 240)][1]
        assert panel.preview_label.x() == expected_left
        assert panel.preview_label.y() == expected_top


# ============================================================================
# UCPreview.set_status() / show_video_controls() / set_progress()
# ============================================================================

class TestUCPreviewStatus:
    """Test status and progress methods."""

    @pytest.fixture
    def panel(self, settings_with_resolution):
        from trcc.legacy.conf import settings
        return UCPreview(settings.width, settings.height)

    def test_set_status(self, panel: UCPreview) -> None:
        panel.set_status("Loading...")
        assert panel.status_label.text() == "Loading..."

    def test_set_status_empty(self, panel: UCPreview) -> None:
        panel.set_status("")
        assert panel.status_label.text() == ""

    def test_show_video_controls_true(self, panel: UCPreview) -> None:
        panel.show_video_controls(True)
        assert not panel.progress_container.isHidden()

    def test_show_video_controls_false(self, panel: UCPreview) -> None:
        panel.show_video_controls(True)
        panel.show_video_controls(False)
        assert panel.progress_container.isHidden()

    def test_set_progress(self, panel: UCPreview) -> None:
        panel.set_progress(50, "01:30", "03:00")
        assert panel.progress_slider.value() == 50
        assert panel.time_label.text() == "01:30 / 03:00"

    def test_set_progress_zero(self, panel: UCPreview) -> None:
        panel.set_progress(0, "00:00", "00:00")
        assert panel.progress_slider.value() == 0

    def test_set_progress_100(self, panel: UCPreview) -> None:
        panel.set_progress(100, "05:00", "05:00")
        assert panel.progress_slider.value() == 100


# ============================================================================
# UCPreview.set_playing()
# ============================================================================

class TestSetPlaying:
    """Test set_playing() icon/text toggling."""

    @pytest.fixture
    def panel(self, settings_with_resolution) -> UCPreview:
        from trcc.legacy.conf import settings
        return UCPreview(settings.width, settings.height)

    def test_set_playing_no_refs_uses_text(self, panel: UCPreview) -> None:
        """When _img_refs not present, falls back to text."""
        if hasattr(panel.play_btn, '_img_refs'):
            delattr(panel.play_btn, '_img_refs')
        panel.set_playing(True)
        assert panel.play_btn.text() == "\u23f8"  # pause symbol

    def test_set_playing_false_no_refs(self, panel: UCPreview) -> None:
        if hasattr(panel.play_btn, '_img_refs'):
            delattr(panel.play_btn, '_img_refs')
        panel.set_playing(False)
        assert panel.play_btn.text() == "\u25b6"  # play symbol

    def test_set_playing_with_refs(self, panel: UCPreview) -> None:
        """When _img_refs exist with valid pixmaps, uses icon (no exception)."""
        play_pix = _valid_pixmap(34, 26)
        pause_pix = _valid_pixmap(34, 26)
        panel.play_btn._img_refs = [play_pix, pause_pix]  # type: ignore[attr-defined]
        panel.set_playing(True)
        # Should have set icon without raising
        assert not panel.play_btn.icon().isNull()

    def test_set_playing_false_with_refs(self, panel: UCPreview) -> None:
        play_pix = _valid_pixmap(34, 26)
        pause_pix = _valid_pixmap(34, 26)
        panel.play_btn._img_refs = [play_pix, pause_pix]  # type: ignore[attr-defined]
        panel.set_playing(False)
        assert not panel.play_btn.icon().isNull()

    def test_set_playing_with_none_refs(self, panel: UCPreview) -> None:
        """When _img_refs has None entries, falls back to text."""
        panel.play_btn._img_refs = [None, None]  # type: ignore[attr-defined]
        panel.set_playing(True)
        assert panel.play_btn.text() == "\u23f8"


# ============================================================================
# UCPreview.get_lcd_size()
# ============================================================================

class TestGetLcdSize:
    """Test get_lcd_size() returns correct dimensions."""

    @pytest.fixture
    def panel(self, settings_with_resolution):
        """UCPreview built from active settings — mirrors production trcc_app.py."""
        from trcc.legacy.conf import settings
        return UCPreview(settings.width, settings.height)

    def test_default(self, panel: UCPreview) -> None:
        assert panel.get_lcd_size() == (320, 320)

    def test_after_construction(self, qapp: object) -> None:
        panel = UCPreview(width=1280, height=480)
        assert panel.get_lcd_size() == (1280, 480)

    def test_after_set_resolution(self, panel: UCPreview) -> None:
        panel.set_resolution(240, 240)
        assert panel.get_lcd_size() == (240, 240)


# ============================================================================
# UCPreview drag signal emissions
# ============================================================================

class TestUCPreviewDragSignals:
    """Test that drag events are forwarded as LCD-scaled coordinates."""

    def test_drag_start_signal(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_drag_start.connect(lambda x, y: received.append((x, y)))
        # Simulate drag start at widget origin
        panel._on_drag_started(0, 0)
        assert received == [(0, 0)]

    def test_drag_move_signal(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        received: list[tuple[int, int]] = []
        panel.element_drag_move.connect(lambda x, y: received.append((x, y)))
        _, _, pw, ph, _ = panel._offset_info
        panel._on_drag_moved(pw // 2, ph // 2)
        assert received == [(160, 160)]

    def test_drag_end_signal(self, qapp: object) -> None:
        panel = UCPreview(width=320, height=320)
        fired: list[bool] = []
        panel.element_drag_end.connect(lambda: fired.append(True))
        panel.preview_label.drag_ended.emit()
        assert fired


# ============================================================================
# UCPreview command delegates
# ============================================================================

class TestUCPreviewDelegates:
    """Test that video control actions fire delegate signals."""

    @pytest.fixture
    def panel(self, settings_with_resolution) -> UCPreview:
        from trcc.legacy.conf import settings
        return UCPreview(settings.width, settings.height)

    def test_play_pause_delegate(self, panel: UCPreview) -> None:
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_play_pause()
        assert any(r[0] == UCPreview.CMD_VIDEO_PLAY_PAUSE for r in received)

    def test_height_fit_delegate(self, panel: UCPreview) -> None:
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_height_fit()
        assert any(r[0] == UCPreview.CMD_VIDEO_FIT_HEIGHT for r in received)

    def test_width_fit_delegate(self, panel: UCPreview) -> None:
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_width_fit()
        assert any(r[0] == UCPreview.CMD_VIDEO_FIT_WIDTH for r in received)

    def test_seek_delegate(self, panel: UCPreview) -> None:
        received: list[tuple] = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel._on_seek(42)
        assert any(r[0] == UCPreview.CMD_VIDEO_SEEK for r in received)

    def test_preview_click_emits_image_clicked(self, panel: UCPreview) -> None:
        received: list[tuple[int, int]] = []
        panel.image_clicked.connect(lambda x, y: received.append((x, y)))
        panel._on_preview_clicked()
        assert received == [(0, 0)]
