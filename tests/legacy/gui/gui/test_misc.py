"""Tests for miscellaneous Qt GUI components.

Covers: UCImageCut, UCVideoCut, UCAbout, UCActivitySidebar,
UCInfoModule, SensorPickerDialog, UCThemeWeb, UCThemeMask.

Uses QT_QPA_PLATFORM=offscreen for headless testing.
"""

from __future__ import annotations

import os

# Must set before ANY Qt import
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QImage

from tests.conftest import make_test_surface
from trcc.legacy.ui.gui.uc_about import UCAbout
from trcc.legacy.ui.gui.uc_activity_sidebar import (
    CATEGORY_COLORS,
    SENSOR_TO_OVERLAY,
    SENSORS,
    SensorItem,
    UCActivitySidebar,
)
from trcc.legacy.ui.gui.uc_image_cut import (
    _PAN_MULTIPLIERS,
    PANEL_H,
    PANEL_W,
    PREVIEW_H,
    PREVIEW_W,
    SLIDER_CENTER,
    SLIDER_X_MAX,
    SLIDER_X_MIN,
    UCImageCut,
)
from trcc.legacy.ui.gui.uc_info_module import (
    DEFAULT_SENSORS,
    SensorBox,
    UCInfoModule,
)
from trcc.legacy.ui.gui.uc_video_cut import (
    EXPORT_FPS,
    FRAME_INTERVAL_MS,
    MAX_DURATION_MS,
    TIMELINE_H,
    TIMELINE_W,
    TIMELINE_X,
    TIMELINE_Y,
    ExportWorker,
    UCVideoCut,
    _format_time,
)

# ============================================================================
# Module-level autouse fixture: patch Assets across all gui modules
# ============================================================================

_ASSETS_PATCH_TARGETS = [
    "trcc.legacy.ui.gui.uc_image_cut.Assets",
    "trcc.legacy.ui.gui.uc_video_cut.Assets",
    "trcc.legacy.ui.gui.uc_about.Assets",
    "trcc.legacy.ui.gui.uc_sensor_picker.Assets",
    "trcc.legacy.ui.gui.uc_sensor_picker.set_background_pixmap",
    "trcc.legacy.ui.gui.uc_about.set_background_pixmap",
    "trcc.legacy.ui.gui.uc_about.create_image_button",
    "trcc.legacy.ui.gui.assets.Assets",
]


@pytest.fixture(autouse=True)
def _mock_all_assets(qapp):
    """Patch Assets (and helpers that use it) across every gui module
    referenced in this test file.  Runs before every test so QPixmap
    construction never hits real files in offscreen mode.
    """
    patchers = [patch(target) for target in _ASSETS_PATCH_TARGETS]
    mocks = [p.start() for p in patchers]

    # Set up commonly-needed return values on each Assets mock.
    for mock in mocks:
        if hasattr(mock, "load_pixmap"):
            mock.load_pixmap.return_value = MagicMock(isNull=lambda: True)
        if hasattr(mock, "get_localized"):
            mock.get_localized.return_value = "bg.png"
        # Class-level constants referenced by UCAbout._setup_ui
        for attr in ("ABOUT_BG", "ABOUT_LOGOUT", "ABOUT_LOGOUT_HOVER",
                      "CHECKBOX_OFF", "CHECKBOX_ON"):
            if not hasattr(mock, attr) or isinstance(getattr(mock, attr), MagicMock):
                setattr(mock, attr, "dummy.png")

    yield mocks

    for p in patchers:
        p.stop()


# ============================================================================
# UCImageCut Tests
# ============================================================================


class TestImageCutConstants:
    """Verify UCImageCut constants are sensible."""

    def test_panel_dimensions(self):
        assert PANEL_W == 500
        assert PANEL_H == 702

    def test_preview_dimensions(self):
        assert PREVIEW_W == 500
        assert PREVIEW_H == 540

    def test_slider_range(self):
        assert SLIDER_X_MIN < SLIDER_CENTER < SLIDER_X_MAX
        assert SLIDER_X_MIN == 12
        assert SLIDER_X_MAX == 484
        assert SLIDER_CENTER == 248

    def test_pan_multipliers_keys(self):
        for key, val in _PAN_MULTIPLIERS.items():
            assert len(key) == 2
            assert isinstance(val, int)
            assert val >= 1

    def test_pan_multipliers_known_resolutions(self):
        assert _PAN_MULTIPLIERS[(240, 240)] == 1
        assert _PAN_MULTIPLIERS[(480, 480)] == 2
        assert _PAN_MULTIPLIERS[(1920, 462)] == 4


class TestImageCutZoomFormula:
    """Test _calc_zoom_from_slider and _slider_x_from_zoom."""

    @pytest.fixture
    def widget(self):
        return UCImageCut()

    def test_center_gives_zoom_1(self, widget):
        assert widget._calc_zoom_from_slider(SLIDER_CENTER) == pytest.approx(1.0)

    def test_right_of_center_gives_zoom_gt_1(self, widget):
        zoom = widget._calc_zoom_from_slider(SLIDER_CENTER + 10)
        assert zoom == pytest.approx(1.0 + 10 * 0.03)
        assert zoom > 1.0

    def test_left_of_center_gives_zoom_lt_1(self, widget):
        zoom = widget._calc_zoom_from_slider(SLIDER_CENTER - 10)
        expected = 1.0 / (1.0 + 10 * 0.03)
        assert zoom == pytest.approx(expected)
        assert zoom < 1.0

    def test_max_slider_zoom(self, widget):
        zoom = widget._calc_zoom_from_slider(SLIDER_X_MAX)
        assert zoom > 1.0

    def test_min_slider_zoom(self, widget):
        zoom = widget._calc_zoom_from_slider(SLIDER_X_MIN)
        assert 0 < zoom < 1.0

    def test_slider_inverse_at_center(self, widget):
        sx = widget._slider_x_from_zoom(1.0)
        assert sx == SLIDER_CENTER

    def test_slider_inverse_zoom_gt_1(self, widget):
        zoom = 2.5
        sx = widget._slider_x_from_zoom(zoom)
        assert sx > SLIDER_CENTER

    def test_slider_inverse_zoom_lt_1(self, widget):
        zoom = 0.5
        sx = widget._slider_x_from_zoom(zoom)
        assert sx < SLIDER_CENTER

    def test_roundtrip_zoom_above_1(self, widget):
        original_x = 300
        zoom = widget._calc_zoom_from_slider(original_x)
        recovered_x = widget._slider_x_from_zoom(zoom)
        assert recovered_x == pytest.approx(original_x, abs=1)

    def test_roundtrip_zoom_below_1(self, widget):
        original_x = 100
        zoom = widget._calc_zoom_from_slider(original_x)
        recovered_x = widget._slider_x_from_zoom(zoom)
        assert recovered_x == pytest.approx(original_x, abs=1)


class TestImageCutWidget:
    """Test UCImageCut construction and state management."""

    @pytest.fixture
    def widget(self, settings_with_resolution):
        return UCImageCut()

    def test_initial_state(self, widget):
        assert widget._zoom == 1.0
        assert widget._pan_x == 0
        assert widget._pan_y == 0
        assert widget._rotation == 0
        assert widget._target_w == 0
        assert widget._target_h == 0

    def test_fixed_size(self, widget):
        assert widget.width() == PANEL_W
        assert widget.height() == PANEL_H

    def test_set_resolution(self, widget):
        widget.set_resolution(480, 480)
        assert widget._target_w == 480
        assert widget._target_h == 480
        assert widget._pan_multiplier == 2

    def test_set_resolution_unknown(self, widget):
        widget.set_resolution(999, 999)
        assert widget._pan_multiplier == 1

    def test_load_image_none(self, widget):
        widget.load_image(None, 320, 320)
        assert widget._source_image is None

    def test_load_landscape_image(self):
        w = UCImageCut()
        img = make_test_surface(800, 600, (128, 0, 0))
        w.load_image(img, 320, 320)
        assert w._source_image is not None
        assert w._target_w == 320
        assert w._target_h == 320
        assert w._rotation == 0
        # Landscape image -> width fit -> zoom = 320/800
        assert w._zoom == pytest.approx(320 / 800)

    def test_load_portrait_image(self):
        w = UCImageCut()
        img = make_test_surface(400, 800, (128, 0, 0))
        w.load_image(img, 320, 320)
        # Portrait -> height fit -> zoom = 320/800
        assert w._zoom == pytest.approx(320 / 800)

    def test_on_rotate(self):
        w = UCImageCut()
        img = make_test_surface(400, 300, (0, 0, 0))
        w.load_image(img, 320, 320)
        assert w._rotation == 0
        w._on_rotate()
        assert w._rotation == 90
        w._on_rotate()
        assert w._rotation == 180
        w._on_rotate()
        assert w._rotation == 270
        w._on_rotate()
        assert w._rotation == 0

    def test_rotate_resets_pan(self):
        w = UCImageCut()
        img = make_test_surface(400, 300, (0, 0, 0))
        w.load_image(img, 320, 320)
        w._pan_x = 50
        w._pan_y = 30
        w._on_rotate()
        assert w._pan_x == 0
        assert w._pan_y == 0

    def test_get_cropped_output(self):
        w = UCImageCut()
        img = make_test_surface(640, 480, (255, 0, 0))
        w.load_image(img, 320, 320)
        output = w._get_cropped_output()
        assert output is not None
        assert isinstance(output, QImage)
        assert output.width() == 320 and output.height() == 320

    def test_get_cropped_output_no_image(self):
        w = UCImageCut()
        assert w._get_cropped_output() is None

    def test_ok_emits_signal(self):
        w = UCImageCut()
        img = make_test_surface(200, 200, (0, 255, 0))
        w.load_image(img, 320, 320)
        received = []
        w.image_cut_done.connect(received.append)
        w._on_ok()
        assert len(received) == 1
        assert received[0] is not None
        assert isinstance(received[0], QImage)

    def test_close_emits_none(self):
        w = UCImageCut()
        received = []
        w.image_cut_done.connect(received.append)
        w._on_close()
        assert len(received) == 1
        assert received[0] is None

    def test_fit_width(self):
        w = UCImageCut()
        img = make_test_surface(800, 600, (0, 0, 0))
        w.load_image(img, 640, 480)
        w._fit_width()
        assert w._zoom == pytest.approx(640 / 800)
        assert w._pan_x == 0
        assert w._pan_y == 0

    def test_fit_height(self):
        w = UCImageCut()
        img = make_test_surface(800, 600, (0, 0, 0))
        w.load_image(img, 640, 480)
        w._fit_height()
        assert w._zoom == pytest.approx(480 / 600)
        assert w._pan_x == 0
        assert w._pan_y == 0


# ============================================================================
# UCVideoCut Tests
# ============================================================================


class TestFormatTime:
    """Test _format_time helper."""

    def test_zero(self):
        assert _format_time(0) == "00:00:00"

    def test_one_second(self):
        assert _format_time(1000) == "00:00:01"

    def test_one_minute(self):
        assert _format_time(60000) == "00:01:00"

    def test_one_hour(self):
        assert _format_time(3600000) == "01:00:00"

    def test_complex_time(self):
        # 1h 23m 45s = 5025000 ms
        assert _format_time(5025000) == "01:23:45"

    def test_negative_clamps_to_zero(self):
        assert _format_time(-5000) == "00:00:00"

    def test_fractional_ms(self):
        # 1500 ms = 1.5s -> truncated to 1s
        assert _format_time(1500) == "00:00:01"


class TestVideoCutConstants:
    """Verify UCVideoCut constants."""

    def test_panel_size(self):
        assert UCVideoCut is not None
        # From module constants
        from trcc.legacy.ui.gui.uc_video_cut import PANEL_H as VH
        from trcc.legacy.ui.gui.uc_video_cut import PANEL_W as VW

        assert VW == 500
        assert VH == 702

    def test_timeline_dimensions(self):
        assert TIMELINE_W == 480
        assert TIMELINE_H == 20
        assert TIMELINE_X == 9
        assert TIMELINE_Y == 564

    def test_max_duration(self):
        assert MAX_DURATION_MS == 300000  # 5 minutes

    def test_export_fps(self):
        assert EXPORT_FPS == 24

    def test_frame_interval(self):
        assert pytest.approx(1000.0 / 24) == FRAME_INTERVAL_MS


class TestExportWorker:
    """Test ExportWorker construction and signal types."""

    def test_construction(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 0, True)
        assert w.video_path == "/tmp/test.mp4"
        assert w.start_ms == 0
        assert w.end_ms == 5000
        assert w.target_w == 320
        assert w.target_h == 320
        assert w.rotation == 0
        assert w.width_fit is True

    def test_has_progress_signal(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 0, True)
        assert hasattr(w, "progress")

    def test_has_finished_signal(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 0, True)
        assert hasattr(w, "finished")

    def test_has_error_signal(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 0, True)
        assert hasattr(w, "error")

    def test_rotation_90(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 90, True)
        assert w.rotation == 90

    def test_rotation_270(self):
        w = ExportWorker("/tmp/test.mp4", 0, 5000, 320, 320, 270, False)
        assert w.rotation == 270
        assert w.width_fit is False


class TestVideoCutWidget:
    """Test UCVideoCut construction and state."""

    @pytest.fixture
    def widget(self, settings_with_resolution):
        return UCVideoCut()

    def test_initial_state(self, widget):
        assert widget._video_path is None
        assert widget._rotation == 0
        assert widget._width_fit is True
        assert widget._target_w == 0
        assert widget._target_h == 0
        assert widget._duration_ms == 0
        assert widget._is_processing is False

    def test_set_resolution(self, widget):
        widget.set_resolution(640, 480)
        assert widget._target_w == 640
        assert widget._target_h == 480

    def test_on_rotate(self, widget):
        assert widget._rotation == 0
        widget._on_rotate()
        assert widget._rotation == 90
        widget._on_rotate()
        assert widget._rotation == 180

    def test_on_width_fit(self, widget):
        widget._width_fit = False
        widget._on_width_fit()
        assert widget._width_fit is True

    def test_on_height_fit(self, widget):
        widget._width_fit = True
        widget._on_height_fit()
        assert widget._width_fit is False

    def test_close_emits_empty_string(self, widget):
        received = []
        widget.video_cut_done.connect(received.append)
        widget._on_close()
        assert len(received) == 1
        assert received[0] == ""

    def test_close_stops_preview(self, widget):
        widget._previewing = True
        widget._on_close()
        assert widget._previewing is False

    def test_x_to_ms_at_start(self, widget):
        widget._duration_ms = 10000
        ms = widget._x_to_ms(TIMELINE_X)
        assert ms == pytest.approx(0, abs=1)

    def test_x_to_ms_at_end(self, widget):
        widget._duration_ms = 10000
        ms = widget._x_to_ms(TIMELINE_X + TIMELINE_W)
        assert ms == pytest.approx(10000, abs=1)

    def test_x_to_ms_at_midpoint(self, widget):
        widget._duration_ms = 10000
        ms = widget._x_to_ms(TIMELINE_X + TIMELINE_W // 2)
        assert ms == pytest.approx(5000, abs=50)

    def test_ms_to_x_at_start(self, widget):
        widget._duration_ms = 10000
        x = widget._ms_to_x(0)
        assert x == pytest.approx(TIMELINE_X, abs=1)

    def test_ms_to_x_at_end(self, widget):
        widget._duration_ms = 10000
        x = widget._ms_to_x(10000)
        assert x == pytest.approx(TIMELINE_X + TIMELINE_W, abs=1)

    def test_ms_to_x_zero_duration(self, widget):
        widget._duration_ms = 0
        x = widget._ms_to_x(5000)
        assert x == TIMELINE_X

    def test_on_export_no_video(self, widget):
        """Export with no video loaded does nothing."""
        widget._video_path = None
        widget._on_export()
        assert widget._is_processing is False

    def test_on_export_while_processing(self, widget):
        """Export while already processing does nothing."""
        widget._is_processing = True
        widget._video_path = "/tmp/test.mp4"
        # Should return early
        widget._on_export()
        # Still processing (the existing one)
        assert widget._is_processing is True

    def test_on_export_progress(self, widget):
        widget._on_export_progress(50, "Converting frames...")
        assert widget._progress.value() == 50
        assert widget._lbl_info.text() == "Converting frames..."

    def test_on_export_finished(self, widget):
        widget._is_processing = True
        received = []
        widget.video_cut_done.connect(received.append)
        widget._on_export_finished("/tmp/Theme.zt")
        assert widget._is_processing is False
        assert len(received) == 1
        assert received[0] == "/tmp/Theme.zt"

    def test_on_export_error(self, widget):
        widget._is_processing = True
        widget._on_export_error("FFmpeg crashed")
        assert widget._is_processing is False
        assert "FFmpeg crashed" in widget._lbl_info.text()

    def test_preview_toggle_start(self, widget):
        assert widget._previewing is False
        widget._video_path = "/tmp/test.mp4"
        widget._start_ms = 0
        widget._start_preview()
        assert widget._previewing is True
        widget._stop_preview()
        assert widget._previewing is False


# ============================================================================
# UCAbout Tests
# ============================================================================


_AUTOSTART_MOD = "trcc.legacy.adapters.system.linux_platform"


class TestAboutAutostart:
    """Test LinuxAutostartManager."""

    def test_is_autostart_enabled_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            f"{_AUTOSTART_MOD}._AUTOSTART_FILE",
            tmp_path / "nonexistent.desktop",
        )
        from trcc.legacy.adapters.system.linux_platform import LinuxAutostartManager

        assert LinuxAutostartManager().is_enabled() is False

    def test_is_autostart_enabled_with_file(self, tmp_path, monkeypatch):
        f = tmp_path / "trcc-linux.desktop"
        f.write_text("[Desktop Entry]\nExec=trcc\n")
        monkeypatch.setattr(f"{_AUTOSTART_MOD}._AUTOSTART_FILE", f)
        from trcc.legacy.adapters.system.linux_platform import LinuxAutostartManager

        assert LinuxAutostartManager().is_enabled() is True

    def test_set_autostart_enable(self, tmp_path, monkeypatch):
        autostart_dir = tmp_path / "autostart"
        autostart_file = autostart_dir / "trcc-linux.desktop"
        monkeypatch.setattr(f"{_AUTOSTART_MOD}._AUTOSTART_DIR", autostart_dir)
        monkeypatch.setattr(f"{_AUTOSTART_MOD}._AUTOSTART_FILE", autostart_file)
        from trcc.legacy.adapters.system.linux_platform import LinuxAutostartManager

        LinuxAutostartManager().enable()
        assert autostart_file.exists()

    def test_set_autostart_disable(self, tmp_path, monkeypatch):
        autostart_dir = tmp_path / "autostart"
        autostart_dir.mkdir()
        autostart_file = autostart_dir / "trcc-linux.desktop"
        autostart_file.write_text("test")
        monkeypatch.setattr(f"{_AUTOSTART_MOD}._AUTOSTART_DIR", autostart_dir)
        monkeypatch.setattr(f"{_AUTOSTART_MOD}._AUTOSTART_FILE", autostart_file)
        from trcc.legacy.adapters.system.linux_platform import LinuxAutostartManager

        LinuxAutostartManager().disable()
        assert not autostart_file.exists()

    def test_parse_version(self):
        from trcc.legacy.ui.gui.uc_about import _parse_version

        assert _parse_version("3.0.9") == (3, 0, 9)
        assert _parse_version("6.2.1") == (6, 2, 1)
        assert _parse_version("1.0.0") < _parse_version("2.0.0")


class TestAboutWidget:
    """Test UCAbout construction and signals."""

    @pytest.fixture
    def widget(self, tmp_config):
        from unittest.mock import MagicMock
        mock_platform = MagicMock()
        mock_platform.autostart_enabled.return_value = False
        with patch("trcc.legacy.ui.gui.uc_about.Thread"):
            w = UCAbout(platform=mock_platform)
        return w

    def test_construction(self, widget):
        assert widget is not None
        assert widget._temp_mode == "C"

    def test_temp_unit_signal(self, widget):
        received = []
        widget.temp_unit_changed.connect(received.append)
        widget._set_temp("F")
        assert widget._temp_mode == "F"
        assert received == ["F"]

    def test_temp_unit_celsius(self, widget):
        widget._set_temp("F")
        widget._set_temp("C")
        assert widget._temp_mode == "C"

    def test_close_signal(self, widget):
        received = []
        widget.close_requested.connect(lambda: received.append(True))
        widget._on_close()
        assert received == [True]

    def test_refresh_changed_signal(self, widget):
        received = []
        widget.refresh_changed.connect(received.append)
        widget.refresh_input.setText("5")
        widget._on_refresh_changed()
        assert received == [5]
        assert widget.refresh_interval == 5

    def test_refresh_clamps_min(self, widget):
        widget.refresh_input.setText("0")
        widget._on_refresh_changed()
        assert widget.refresh_interval == 1

    def test_refresh_clamps_max(self, widget):
        widget.refresh_input.setText("200")
        widget._on_refresh_changed()
        assert widget.refresh_interval == 100

    def test_refresh_empty_defaults_to_1(self, widget):
        widget.refresh_input.setText("")
        widget._on_refresh_changed()
        assert widget.refresh_interval == 1


# ============================================================================
# UCActivitySidebar Tests
# ============================================================================


class TestSensorDefinitions:
    """Verify sensor definition completeness."""

    def test_six_categories(self):
        assert len(SENSORS) == 6
        assert set(SENSORS.keys()) == {"cpu", "gpu", "memory", "hdd", "network", "fan"}

    def test_four_sensors_per_category(self):
        for cat, sensors in SENSORS.items():
            assert len(sensors) == 4, f"{cat} has {len(sensors)} sensors, expected 4"

    def test_category_colors_match(self):
        for cat in SENSORS:
            assert cat in CATEGORY_COLORS

    def test_sensor_to_overlay_has_all_entries(self):
        """Every sensor key from SENSORS has a corresponding overlay mapping."""
        for cat, sensors in SENSORS.items():
            for key_suffix, _label, _unit, _metric in sensors:
                sensor_key = f"{cat}_{key_suffix}"
                assert sensor_key in SENSOR_TO_OVERLAY, f"Missing: {sensor_key}"

    def test_overlay_mapping_structure(self):
        """Each overlay mapping is a (main_count, sub_count) tuple."""
        for key, val in SENSOR_TO_OVERLAY.items():
            assert isinstance(val, tuple)
            assert len(val) == 2


class TestSensorItem:
    """Test SensorItem widget."""

    @pytest.fixture
    def item(self):
        return SensorItem("cpu", "temp", "TEMP", "\u00b0C", "cpu_temp", "#32C5FF")

    def test_construction(self, item):
        assert item.category == "cpu"
        assert item.key_suffix == "temp"
        assert item.metric_key == "cpu_temp"
        assert item.unit == "\u00b0C"

    def test_update_value_float(self, item):
        metrics = SimpleNamespace(cpu_temp=65.3)
        item.update_value(metrics)
        assert item.value_label.text() == "65.3\u00b0C"

    def test_update_value_float_large(self, item):
        metrics = SimpleNamespace(cpu_temp=1500.0)
        item.update_value(metrics)
        assert item.value_label.text() == "1500\u00b0C"

    def test_update_value_int(self, item):
        metrics = SimpleNamespace(cpu_temp=65)
        item.update_value(metrics)
        assert item.value_label.text() == "65\u00b0C"

    def test_update_value_none(self, item):
        metrics = SimpleNamespace()
        item.update_value(metrics)
        assert item.value_label.text() == "--\u00b0C"

    def test_overlay_config(self, item):
        cfg = item._overlay_config
        assert cfg.main_count == 0
        assert cfg.sub_count == 1

    def test_click_emits_signal(self, item):
        received = []
        item.clicked.connect(received.append)
        # Simulate left click via mouse press
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(5, 5),
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        item.mousePressEvent(event)
        assert len(received) == 1


class TestUCActivitySidebar:
    """Test UCActivitySidebar construction and updates."""

    @pytest.fixture
    def sidebar(self):
        return UCActivitySidebar()

    def test_construction(self, sidebar):
        assert sidebar is not None

    def test_sensor_items_count(self, sidebar):
        """6 categories x 4 sensors = 24 items."""
        assert len(sidebar._sensor_items) == 24

    def test_sensor_items_have_correct_categories(self, sidebar):
        categories = {item.category for item in sidebar._sensor_items}
        assert categories == {"cpu", "gpu", "memory", "hdd", "network", "fan"}

    def test_stop_updates_noop(self, sidebar):
        """stop_updates() is a no-op (MetricsMediator drives updates)."""
        sidebar.stop_updates()  # should not raise

    def test_update_from_metrics(self, sidebar):
        """update_from_metrics updates sensor items."""
        metrics = SimpleNamespace(
            cpu_temp=65.0, cpu_percent=30.0, cpu_freq=3200.0, cpu_power=95.0,
            gpu_temp=55.0, gpu_usage=45.0, gpu_clock=1800.0, gpu_power=120.0,
            mem_temp=40.0, mem_percent=60.0, mem_clock=3200.0, mem_available=8192.0,
            disk_temp=35.0, disk_activity=10.0, disk_read=100.0, disk_write=50.0,
            net_up=500.0, net_down=1200.0, net_total_up=2048.0, net_total_down=4096.0,
            fan_cpu=1200, fan_gpu=1500, fan_ssd=800, fan_sys2=900,
        )
        sidebar.update_from_metrics(metrics)
        # Spot check: first item is cpu_temp
        assert sidebar._sensor_items[0].value_label.text() == "65.0\u00b0C"

    def test_sensor_clicked_signal(self, sidebar):
        received = []
        sidebar.sensor_clicked.connect(received.append)
        # Simulate clicking via internal method
        cfg = MagicMock()
        sidebar._on_sensor_clicked(cfg)
        assert len(received) == 1
        assert received[0] is cfg


# ============================================================================
# UCInfoModule Tests
# ============================================================================


class TestSensorBox:
    """Test SensorBox widget."""

    def test_construction(self):
        box = SensorBox("CPU Temp", "#FF6B6B")
        assert box.name_label.text() == "CPU Temp"
        assert box.value_label.text() == "--"
        assert box.color == "#FF6B6B"

    def test_metric_key_default(self):
        box = SensorBox("GPU Temp", "#4ECDC4")
        assert box.metric_key == ""


class TestUCInfoModule:
    """Test UCInfoModule construction and updates."""

    @pytest.fixture
    def module(self):
        return UCInfoModule()

    def test_construction(self, module):
        assert module is not None
        assert module._temp_unit == "\u00b0C"

    def test_sensor_boxes_count(self, module):
        assert len(module._sensor_boxes) == 4

    def test_sensor_boxes_keys(self, module):
        expected = {"cpu_temp", "gpu_temp", "cpu_percent", "gpu_usage"}
        assert set(module._sensor_boxes.keys()) == expected

    def test_default_sensors_structure(self):
        for key, label, color in DEFAULT_SENSORS:
            assert isinstance(key, str)
            assert isinstance(label, str)
            assert isinstance(color, str)
            assert color.startswith("#")

    def test_set_temp_unit(self, module):
        module.set_temp_unit("\u00b0F")
        assert module._temp_unit == "\u00b0F"

    def test_update_from_metrics(self, module):
        """update_from_metrics updates sensor boxes."""
        metrics = SimpleNamespace(
            cpu_temp=65.0, gpu_temp=55.0, cpu_percent=30.0, gpu_usage=45.0,
        )
        module.update_from_metrics(metrics)
        assert module._sensor_boxes['cpu_temp'].value_label.text() == "65\u00b0C"
        assert module._sensor_boxes['gpu_usage'].value_label.text() == "45%"

    def test_stop_updates_noop(self, module):
        """stop_updates() is a no-op (MetricsMediator drives updates)."""
        module.stop_updates()  # should not raise


# ============================================================================
# SensorPickerDialog Tests
# ============================================================================


class TestSensorRow:
    """Test SensorRow widget."""

    def _make_sensor_info(self, sensor_id="hwmon:temp1", name="CPU Temp",
                          category="temperature", unit="\u00b0C", source="hwmon"):
        from trcc.legacy.core.models import SensorInfo

        return SensorInfo(
            id=sensor_id, name=name, category=category, unit=unit, source=source,
        )

    def test_construction(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info()
        row = SensorRow(info)
        assert row.sensor is info
        assert row._name.text() == "CPU Temp"

    def test_update_value_celsius(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info()
        row = SensorRow(info)
        row.update_value(65.3)
        assert row._value.text() == "65\u00b0C"

    def test_update_value_none(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info()
        row = SensorRow(info)
        row.update_value(None)
        assert row._value.text() == "--"

    def test_update_value_rpm(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info(unit="RPM", category="fan")
        row = SensorRow(info)
        row.update_value(1200.0)
        assert row._value.text() == "1200RPM"

    def test_update_value_voltage(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info(unit="V", category="voltage")
        row = SensorRow(info)
        row.update_value(1.35)
        assert row._value.text() == "1.35V"

    def test_update_value_mhz(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info(unit="MHz", category="clock")
        row = SensorRow(info)
        row.update_value(3600.0)
        assert row._value.text() == "3600MHz"

    def test_update_value_mbps(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info(unit="MB/s", category="other")
        row = SensorRow(info)
        row.update_value(150.7)
        assert row._value.text() == "150.7MB/s"

    def test_set_selected(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info()
        row = SensorRow(info)
        row.set_selected(True)
        assert row._selected is True
        row.set_selected(False)
        assert row._selected is False

    def test_clicked_signal(self):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorRow

        info = self._make_sensor_info()
        row = SensorRow(info)
        received = []
        row.clicked.connect(received.append)
        # Simulate mouse press
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(5, 5),
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        row.mousePressEvent(event)
        assert received == ["hwmon:temp1"]


class TestSensorPickerDialog:
    """Test SensorPickerDialog construction."""

    def _make_enumerator(self):
        from trcc.legacy.core.models import SensorInfo

        mock_enum = MagicMock()
        mock_enum.get_sensors.return_value = [
            SensorInfo(id="hwmon:temp1", name="CPU Temp",
                       category="temperature", unit="\u00b0C", source="hwmon"),
            SensorInfo(id="psutil:cpu_percent", name="CPU Usage",
                       category="usage", unit="%", source="psutil"),
        ]
        mock_enum.read_all.return_value = {"hwmon:temp1": 65.0, "psutil:cpu_percent": 30.0}
        return mock_enum

    def test_construction(self, tmp_config):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorPickerDialog

        dialog = SensorPickerDialog(self._make_enumerator())
        assert dialog is not None
        assert len(dialog._rows) == 2
        dialog._timer.stop()

    def test_set_current_sensor(self, tmp_config):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorPickerDialog

        dialog = SensorPickerDialog(self._make_enumerator())
        dialog.set_current_sensor("hwmon:temp1")
        assert dialog._selected_id == "hwmon:temp1"
        dialog._timer.stop()

    def test_get_selected_sensor_none_initially(self, tmp_config):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorPickerDialog

        dialog = SensorPickerDialog(self._make_enumerator())
        assert dialog.get_selected_sensor() is None
        dialog._timer.stop()

    def test_on_row_clicked(self, tmp_config):
        from trcc.legacy.ui.gui.uc_sensor_picker import SensorPickerDialog

        dialog = SensorPickerDialog(self._make_enumerator())
        dialog._on_row_clicked("psutil:cpu_percent")
        assert dialog._selected_id == "psutil:cpu_percent"
        dialog._timer.stop()


# ============================================================================
# UCThemeWeb Tests
# ============================================================================


class TestUCThemeWeb:
    """Test UCThemeWeb construction and state."""

    @pytest.fixture
    def widget(self, settings_with_resolution):
        from trcc.legacy.ui.gui.uc_theme_web import UCThemeWeb

        return UCThemeWeb()

    def test_construction(self, widget):
        assert widget is not None
        assert widget.current_category == "all"
        assert widget.web_directory is None
        assert widget._resolution == ""

    def test_set_resolution(self, widget):
        widget.set_resolution("480x480")
        assert widget._resolution == "480x480"

    def test_set_web_directory(self, widget, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        widget.set_web_directory(str(web_dir))
        assert widget.web_directory == web_dir

    def test_set_web_directory_none(self, widget):
        widget.set_web_directory(None)
        assert widget.web_directory is None

    def test_no_items_message(self, widget):
        msg = widget._no_items_message()
        assert "cloud themes" in msg.lower()

    def test_downloading_guard(self, widget):
        """Category change is blocked during download."""
        widget._downloading = True
        widget.current_category = "all"
        widget._set_category("a")
        assert widget.current_category == "all"  # unchanged

    def test_load_themes_no_directory(self, widget):
        widget.web_directory = None
        widget.load_themes()
        # No crash, items should be empty
        assert widget.items == []

    def test_load_themes_with_pngs(self, widget, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        # Create some preview PNGs
        make_test_surface(120, 120, (0, 0, 0)).save(str(web_dir / "bg001.png"))
        make_test_surface(120, 120, (0, 0, 0)).save(str(web_dir / "bg002.png"))
        widget.web_directory = web_dir
        widget.load_themes()
        assert len(widget.items) == 2

    def test_load_themes_with_category_filter(self, widget, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        make_test_surface(120, 120, (0, 0, 0)).save(str(web_dir / "cat_a_01.png"))
        make_test_surface(120, 120, (0, 0, 0)).save(str(web_dir / "cat_b_01.png"))
        widget.web_directory = web_dir
        widget.current_category = "a"
        widget.load_themes()
        # Only themes with "a" in id
        assert all("a" in item.id for item in widget.items)

    def test_get_selected_theme_none(self, widget):
        assert widget.get_selected_theme() is None


# ============================================================================
# UCThemeMask Tests
# ============================================================================


class TestUCThemeMask:
    """Test UCThemeMask construction and state."""

    @pytest.fixture
    def widget(self, settings_with_resolution):
        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask

        return UCThemeMask()

    def test_construction(self, widget):
        assert widget is not None
        assert widget.mask_directory is None
        assert widget._resolution == ""
        assert widget._category == "all"

    def test_set_resolution(self, widget):
        widget.set_resolution("480x480")
        assert widget._resolution == "480x480"

    def test_set_mask_directory(self, widget, tmp_path):
        mask_dir = tmp_path / "masks"
        widget.set_mask_directory(str(mask_dir))
        assert widget.mask_directory == mask_dir
        assert mask_dir.exists()

    def test_set_mask_directory_none(self, widget):
        widget.set_mask_directory(None)
        assert widget.mask_directory is None

    def test_no_items_message(self, widget):
        msg = widget._no_items_message()
        assert "mask" in msg.lower()

    def test_cloud_urls(self, widget):
        from trcc.legacy.ui.gui.uc_theme_mask import UCThemeMask

        assert "320x320" in UCThemeMask.CLOUD_URLS
        assert "480x480" in UCThemeMask.CLOUD_URLS

    def test_refresh_masks_no_directory(self, widget):
        widget.mask_directory = None
        widget.refresh_masks()
        # No directory → no masks (disk-only, no cloud placeholders)
        assert len(widget.items) == 0

    def test_refresh_masks_shows_only_local(self, widget, tmp_path):
        mask_dir = tmp_path / "masks"
        mask_dir.mkdir()
        # Create two local mask directories with Theme.png
        for name in ("001a", "002b"):
            d = mask_dir / name
            d.mkdir()
            make_test_surface(120, 120, (0, 0, 0)).save(str(d / "Theme.png"))
        widget.mask_directory = mask_dir
        widget.refresh_masks()
        assert len(widget.items) == 2
        names = {i.name for i in widget.items}
        assert names == {"001a", "002b"}
        assert all(i.is_local for i in widget.items)

    def test_category_filter(self, widget, tmp_path):
        mask_dir = tmp_path / "masks"
        mask_dir.mkdir()
        for name in ("001a", "001b", "002a"):
            d = mask_dir / name
            d.mkdir()
            make_test_surface(120, 120, (0, 0, 0)).save(str(d / "Theme.png"))
        widget.mask_directory = mask_dir
        widget._category = "a"
        widget.refresh_masks()
        assert len(widget.items) == 2
        for item in widget.items:
            assert item.name.endswith("a")

    def test_downloading_guard(self, widget):
        """Category change is blocked during download."""
        widget._downloading = True
        widget._category = "all"
        widget._set_category("b")
        assert widget._category == "all"  # unchanged

    def test_get_selected_mask_none(self, widget):
        assert widget.get_selected_mask() is None

    def test_has_mask_selected_signal(self, widget):
        assert hasattr(widget, "mask_selected")

    def test_download_cloud_mask_no_directory(self, widget):
        """Download with no directory set does nothing (no crash)."""
        widget.mask_directory = None
        widget._download_cloud_mask("001a")
        # Should not crash, _downloading stays False
        assert not widget._downloading

    def test_download_cloud_mask_no_resolution(self, widget, tmp_path):
        """Download with unknown resolution URL does nothing."""
        widget.mask_directory = tmp_path / "masks"
        widget.mask_directory.mkdir()
        widget._resolution = "9999x9999"
        widget._download_cloud_mask("001a")
        assert not widget._downloading
