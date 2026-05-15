"""
Tests for trcc_app.py (LEDHandler, ScreencastHandler) and uc_system_info.py
(SystemInfoPanel, UCSystemInfo).

Covers:
- LEDHandler: construction, show/stop lifecycle, signal wiring, state sync, tick
- ScreencastHandler: construction, toggle, stop, params, cleanup, state machine
- uc_system_info module constants: PANEL_W, PANEL_H, grid layout, PANELS_PER_PAGE
- SystemInfoPanel: construction, update_values formatting, set_temp_unit, signals
- UCSystemInfo: construction, page navigation, add/delete panels, timer management
"""
from __future__ import annotations

import os

# Must set BEFORE any Qt imports
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

from trcc.adapters.system.config import (
    CATEGORY_COLORS,
    CATEGORY_IMAGES,
    PanelConfig,
    SensorBinding,
    SysInfoConfig,
)
from trcc.core.models import LEDMode, LEDZoneState
from trcc.ui.gui.uc_system_info import (
    COLUMNS,
    PAGE_NEXT_POS,
    PAGE_PREV_POS,
    PANEL_H,
    PANEL_W,
    PANELS_PER_PAGE,
    ROWS_PER_PAGE,
    SELECTOR_POSITIONS,
    SPACING_X,
    SPACING_Y,
    START_X,
    START_Y,
    VALUE_POSITIONS,
    SystemInfoPanel,
    UCSystemInfo,
)

# =========================================================================
# Module constants (uc_system_info)
# =========================================================================


class TestSystemInfoModuleConstants:
    """Verify module-level grid layout constants from Windows C#."""

    def test_panel_dimensions(self, qapp):
        assert PANEL_W == 266
        assert PANEL_H == 189

    def test_grid_start_position(self, qapp):
        assert START_X == 44
        assert START_Y == 36

    def test_grid_spacing(self, qapp):
        assert SPACING_X == 300
        assert SPACING_Y == 199

    def test_grid_layout(self, qapp):
        assert COLUMNS == 4
        assert ROWS_PER_PAGE == 3

    def test_panels_per_page(self, qapp):
        assert PANELS_PER_PAGE == COLUMNS * ROWS_PER_PAGE
        assert PANELS_PER_PAGE == 12

    def test_value_positions_count(self, qapp):
        assert len(VALUE_POSITIONS) == 4

    def test_value_positions_x_coord(self, qapp):
        """All value labels share x=240."""
        for vx, _ in VALUE_POSITIONS:
            assert vx == 240

    def test_value_positions_y_increasing(self, qapp):
        """Y positions increase for each row."""
        ys = [vy for _, vy in VALUE_POSITIONS]
        assert ys == sorted(ys)

    def test_selector_positions_count(self, qapp):
        assert len(SELECTOR_POSITIONS) == 4

    def test_selector_positions_dimensions(self, qapp):
        """All selector buttons share same w=16, h=30."""
        for _, _, sw, sh in SELECTOR_POSITIONS:
            assert sw == 16
            assert sh == 30

    def test_page_nav_positions(self, qapp):
        """Page nav buttons have correct dimensions."""
        _, _, pw, ph = PAGE_PREV_POS
        assert pw == 64
        assert ph == 24
        _, _, nw, nh = PAGE_NEXT_POS
        assert nw == 64
        assert nh == 24

    def test_page_prev_left_of_next(self, qapp):
        px, _, _, _ = PAGE_PREV_POS
        nx, _, _, _ = PAGE_NEXT_POS
        assert px < nx


# =========================================================================
# SystemInfoPanel
# =========================================================================


class TestSystemInfoPanel:
    """Test single SystemInfoPanel widget."""

    @pytest.fixture
    def panel(self, qapp, make_panel_config):
        """Create a SystemInfoPanel with mocked Assets."""
        config = make_panel_config()
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        return p

    @pytest.fixture
    def custom_panel(self, qapp, make_custom_panel_config):
        """Create a custom (deletable) SystemInfoPanel."""
        config = make_custom_panel_config()
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        return p

    def test_construction_size(self, panel):
        assert panel.width() == PANEL_W
        assert panel.height() == PANEL_H

    def test_construction_config_stored(self, panel):
        assert panel.config.category_id == 1
        assert panel.config.name == "CPU"

    def test_construction_value_labels_created(self, panel):
        assert len(panel._value_labels) == 4

    def test_construction_selector_buttons_created(self, panel):
        assert len(panel._selector_btns) == 4

    def test_construction_initial_values_are_dashes(self, panel):
        for lbl in panel._value_labels:
            assert lbl.text() == "--"

    def test_construction_selected_is_false(self, panel):
        assert panel._selected is False

    def test_construction_temp_unit_default(self, panel):
        assert panel._temp_unit == 0

    def test_construction_color_from_category(self, panel):
        expected = CATEGORY_COLORS.get(1)
        assert panel._color == expected

    def test_custom_panel_has_delete_button(self, custom_panel):
        assert hasattr(custom_panel, "_del_btn")

    def test_custom_panel_has_name_edit(self, custom_panel):
        assert hasattr(custom_panel, "_name_edit")
        assert custom_panel._name_edit.text() == "Custom"

    def test_non_custom_panel_no_delete_button(self, panel):
        assert not hasattr(panel, "_del_btn")

    # ── update_values ──

    def test_update_values_celsius(self, panel):
        readings = {"hwmon:coretemp:temp1": 65.0}
        panel.update_values(readings)
        assert panel._value_labels[0].text() == "65°C"

    def test_update_values_fahrenheit(self, panel):
        panel.set_temp_unit(1)
        readings = {"hwmon:coretemp:temp1": 100.0}
        panel.update_values(readings)
        # 100°C = 212°F
        assert panel._value_labels[0].text() == "212°F"

    def test_update_values_percent(self, panel):
        readings = {"psutil:cpu_percent": 42.0}
        panel.update_values(readings)
        assert panel._value_labels[1].text() == "42%"

    def test_update_values_mhz(self, panel):
        readings = {"psutil:cpu_freq": 3600.0}
        panel.update_values(readings)
        assert panel._value_labels[2].text() == "3600MHz"

    def test_update_values_watts(self, panel):
        readings = {"rapl:package-0": 95.0}
        panel.update_values(readings)
        assert panel._value_labels[3].text() == "95W"

    def test_update_values_missing_sensor(self, panel):
        """Missing sensor_id in readings shows '--'."""
        panel.update_values({})
        assert panel._value_labels[0].text() == "--"

    def test_update_values_empty_sensor_id(self, qapp):
        """Empty sensor_id in binding shows '--'."""
        config = PanelConfig(
            category_id=1,
            name="Test",
            sensors=[SensorBinding("X", "", "")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"some_key": 99.0})
        assert p._value_labels[0].text() == "--"

    def test_update_values_rpm(self, qapp):
        config = PanelConfig(
            category_id=6,
            name="Fan",
            sensors=[SensorBinding("CPUFAN", "hwmon:nct6798:fan1", "RPM")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"hwmon:nct6798:fan1": 1200.0})
        assert p._value_labels[0].text() == "1200RPM"

    def test_update_values_voltage(self, qapp):
        config = PanelConfig(
            category_id=0,
            name="Custom",
            sensors=[SensorBinding("VCORE", "hwmon:nct6798:in0", "V")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"hwmon:nct6798:in0": 1.23})
        assert p._value_labels[0].text() == "1.23V"

    def test_update_values_mb(self, qapp):
        config = PanelConfig(
            category_id=3,
            name="Memory",
            sensors=[SensorBinding("Available", "psutil:mem_avail", "MB")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"psutil:mem_avail": 8192.5})
        assert p._value_labels[0].text() == "8192.5MB"

    def test_update_values_kbs(self, qapp):
        config = PanelConfig(
            category_id=5,
            name="Net",
            sensors=[SensorBinding("UP", "computed:net_up", "KB/s")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"computed:net_up": 256.3})
        assert p._value_labels[0].text() == "256.3KB/s"

    def test_update_values_mbs(self, qapp):
        config = PanelConfig(
            category_id=4,
            name="HDD",
            sensors=[SensorBinding("Read", "computed:disk_read", "MB/s")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"computed:disk_read": 123.4})
        assert p._value_labels[0].text() == "123.4MB/s"

    def test_format_unknown_unit(self, qapp):
        """Unknown unit uses 1 decimal place."""
        config = PanelConfig(
            category_id=0,
            name="Custom",
            sensors=[SensorBinding("X", "some:sensor", "units")],
        )
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            p = SystemInfoPanel(config)
        p.update_values({"some:sensor": 3.14159})
        assert p._value_labels[0].text() == "3.1"

    # ── set_temp_unit ──

    def test_set_temp_unit_celsius(self, panel):
        panel.set_temp_unit(0)
        assert panel._temp_unit == 0

    def test_set_temp_unit_fahrenheit(self, panel):
        panel.set_temp_unit(1)
        assert panel._temp_unit == 1

    # ── set_selected ──

    def test_set_selected_true(self, panel):
        panel.set_selected(True)
        assert panel._selected is True

    def test_set_selected_false_after_true(self, panel):
        panel.set_selected(True)
        panel.set_selected(False)
        assert panel._selected is False

    # ── Signal emissions ──

    def test_signal_sensor_select_requested(self, panel):
        """Clicking a selector button emits sensor_select_requested."""
        received = []
        panel.sensor_select_requested.connect(lambda p, r: received.append((p, r)))
        panel._selector_btns[2].click()
        assert len(received) == 1
        assert received[0] == (panel, 2)

    def test_signal_delete_requested(self, custom_panel):
        """Delete button emits delete_requested signal."""
        received = []
        custom_panel.delete_requested.connect(lambda p: received.append(p))
        custom_panel._del_btn.click()
        assert len(received) == 1
        assert received[0] is custom_panel

    def test_signal_clicked(self, panel):
        """mousePressEvent emits clicked signal."""
        received = []
        panel.clicked.connect(lambda p: received.append(p))
        # Simulate mouse press via direct call
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        panel.mousePressEvent(event)
        assert len(received) == 1
        assert received[0] is panel


# =========================================================================
# UCSystemInfo
# =========================================================================


class TestUCSystemInfo:
    """Test the UCSystemInfo dashboard container."""

    @pytest.fixture
    def sysinfo(self, qapp, tmp_path, mock_sensor_enumerator):
        """Create UCSystemInfo with mocked dependencies."""
        config_path = tmp_path / "system_config.json"
        with (
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_assets,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
            patch.object(SysInfoConfig, "CONFIG_PATH", config_path),
        ):
            mock_assets.load_pixmap.return_value = QPixmap()
            mock_assets.SYSINFO_BG = "A0test.png"
            widget = UCSystemInfo(mock_sensor_enumerator, SysInfoConfig())
        return widget

    def test_construction(self, sysinfo):
        assert sysinfo is not None

    def test_initial_page_is_zero(self, sysinfo):
        assert sysinfo._page == 0

    def test_default_panels_created(self, sysinfo):
        """Default config creates 6 panels (CPU, GPU, Mem, HDD, Net, Fan)."""
        assert len(sysinfo._config.panels) == 6

    def test_panels_list_populated(self, sysinfo):
        """Visible panels created for first page."""
        assert len(sysinfo._panels_list) == 6

    def test_temp_unit_default(self, sysinfo):
        assert sysinfo._temp_unit == 0

    def test_has_update_timer(self, sysinfo):
        assert isinstance(sysinfo._update_timer, QTimer)

    def test_add_button_visible_when_space(self, sysinfo):
        """Add button appears when fewer than PANELS_PER_PAGE panels."""
        assert sysinfo._add_btn is not None

    def test_first_panel_selected_by_default(self, sysinfo):
        """First panel is auto-selected on construction."""
        assert sysinfo._selected_panel is not None
        if sysinfo._panels_list:
            assert sysinfo._selected_panel is sysinfo._panels_list[0]

    # ── Page navigation ──

    def test_change_page_forward(self, sysinfo, make_custom_panel_config):
        """Adding enough panels to need a second page, then navigating."""
        # Add 7 more panels to get 13 total (need 2 pages)
        for i in range(7):
            sysinfo._config.panels.append(
                make_custom_panel_config(name=f"Extra{i}")
            )
        sysinfo._rebuild_grid()
        assert sysinfo._page == 0
        # Now navigate forward
        sysinfo._change_page(1)
        assert sysinfo._page == 1

    def test_change_page_backward(self, sysinfo, make_custom_panel_config):
        for i in range(7):
            sysinfo._config.panels.append(
                make_custom_panel_config(name=f"Extra{i}")
            )
        sysinfo._rebuild_grid()
        sysinfo._change_page(1)
        assert sysinfo._page == 1
        sysinfo._change_page(-1)
        assert sysinfo._page == 0

    def test_page_clamped_to_max(self, sysinfo):
        """Page doesn't exceed max_page on rebuild."""
        sysinfo._page = 999
        sysinfo._rebuild_grid()
        # With 6 panels, max_page is 0
        assert sysinfo._page == 0

    # ── Add panel ──

    def test_on_add_clicked_adds_panel(self, sysinfo):
        initial_count = len(sysinfo._config.panels)
        with patch.object(SysInfoConfig, "save"):
            sysinfo._on_add_clicked()
        assert len(sysinfo._config.panels) == initial_count + 1

    def test_on_add_clicked_new_panel_is_custom(self, sysinfo):
        with patch.object(SysInfoConfig, "save"):
            sysinfo._on_add_clicked()
        last = sysinfo._config.panels[-1]
        assert last.category_id == 0
        assert last.name == "Custom"

    def test_on_add_clicked_navigates_to_new_panel_page(self, sysinfo):
        """After adding, page should be the page of the new panel."""
        with patch.object(SysInfoConfig, "save"):
            sysinfo._on_add_clicked()
        new_idx = len(sysinfo._config.panels) - 1
        expected_page = new_idx // PANELS_PER_PAGE
        assert sysinfo._page == expected_page

    # ── Delete panel ──

    def test_on_delete_clicked_removes_panel(self, sysinfo, make_custom_panel_config):
        # Add a custom panel first
        custom = make_custom_panel_config()
        sysinfo._config.panels.append(custom)
        count_before = len(sysinfo._config.panels)
        with (
            patch.object(SysInfoConfig, "save"),
            patch("trcc.ui.gui.uc_system_info.Assets") as mock_a,
            patch("trcc.ui.gui.uc_system_info.set_background_pixmap"),
        ):
            mock_a.load_pixmap.return_value = QPixmap()
            # Create a panel widget for deletion
            panel_widget = MagicMock()
            panel_widget.config = custom
            sysinfo._on_delete_clicked(panel_widget)
        assert len(sysinfo._config.panels) == count_before - 1

    # ── Panel selection ──

    def test_on_panel_clicked_selects_panel(self, sysinfo):
        if len(sysinfo._panels_list) >= 2:
            second = sysinfo._panels_list[1]
            sysinfo._on_panel_clicked(second)
            assert sysinfo._selected_panel is second
            assert second._selected is True

    def test_on_panel_clicked_deselects_previous(self, sysinfo):
        if len(sysinfo._panels_list) >= 2:
            first = sysinfo._panels_list[0]
            second = sysinfo._panels_list[1]
            sysinfo._on_panel_clicked(first)
            sysinfo._on_panel_clicked(second)
            assert first._selected is False
            assert second._selected is True

    def test_panel_clicked_signal_emitted(self, sysinfo):
        received = []
        sysinfo.panel_clicked.connect(lambda p: received.append(p))
        if sysinfo._panels_list:
            sysinfo._on_panel_clicked(sysinfo._panels_list[0])
            assert len(received) == 1

    # ── Timer management ──

    def test_start_updates(self, sysinfo):
        mock_enum = sysinfo._enumerator
        mock_enum.read_all.return_value = {}
        sysinfo.start_updates()
        assert sysinfo._update_timer.isActive()
        sysinfo.stop_updates()

    def test_stop_updates(self, sysinfo):
        mock_enum = sysinfo._enumerator
        mock_enum.read_all.return_value = {}
        sysinfo.start_updates()
        sysinfo.stop_updates()
        assert not sysinfo._update_timer.isActive()

    def test_start_updates_calls_update_metrics(self, sysinfo):
        """start_updates() immediately calls _update_metrics."""
        mock_enum = sysinfo._enumerator
        mock_enum.read_all.return_value = {}
        sysinfo.start_updates()
        mock_enum.read_all.assert_called()
        sysinfo.stop_updates()

    # ── Temperature unit ──

    def test_set_temp_unit_propagates_to_panels(self, sysinfo):
        sysinfo.set_temp_unit(1)
        assert sysinfo._temp_unit == 1
        for panel in sysinfo._panels_list:
            assert panel._temp_unit == 1

    def test_set_temp_unit_celsius(self, sysinfo):
        sysinfo.set_temp_unit(0)
        assert sysinfo._temp_unit == 0

    # ── Name change ──

    def test_on_name_changed(self, sysinfo):
        if sysinfo._panels_list:
            panel = sysinfo._panels_list[0]
            with patch.object(SysInfoConfig, "save"):
                sysinfo._on_name_changed(panel, "NewName")
            assert panel.config.name == "NewName"

    # ── Update metrics error handling ──

    def test_update_metrics_handles_exception(self, sysinfo):
        """_update_metrics doesn't crash when enumerator raises."""
        sysinfo._enumerator.read_all.side_effect = RuntimeError("sensor fail")
        # Should not raise
        sysinfo._update_metrics()

    def test_update_metrics_updates_panels(self, sysinfo):
        """_update_metrics forwards readings to all panels."""
        readings = {"hwmon:coretemp:temp1": 70.0}
        sysinfo._enumerator.read_all.return_value = readings
        sysinfo._update_metrics()
        # First panel's first sensor is coretemp:temp1
        if sysinfo._panels_list:
            first = sysinfo._panels_list[0]
            # After update, first label should show the value
            text = first._value_labels[0].text()
            assert text != "--" or first.config.sensors[0].sensor_id != "hwmon:coretemp:temp1"


# =========================================================================
# LEDHandler
# =========================================================================


class TestLEDHandler:
    """Test the LEDHandler — GUI adapter for LED devices.

    Handlers call update_* on LEDDevice (state-only). Panel updates
    are metrics-driven (no timer). Only the active handler updates
    the shared panel via _guard().
    """

    @pytest.fixture
    def mock_panel(self, qapp):
        """Create a mock UCLedControl panel with all required signals."""
        panel = MagicMock()
        panel.mode_changed = MagicMock()
        panel.color_changed = MagicMock()
        panel.brightness_changed = MagicMock()
        panel.global_toggled = MagicMock()
        panel.segment_clicked = MagicMock()
        panel.zone_selected = MagicMock()
        panel.zone_toggled = MagicMock()
        panel.carousel_changed = MagicMock()
        panel.carousel_zone_changed = MagicMock()
        panel.carousel_interval_changed = MagicMock()
        panel.clock_format_changed = MagicMock()
        panel.week_start_changed = MagicMock()
        panel.temp_unit_changed = MagicMock()
        panel.disk_index_changed = MagicMock()
        panel.memory_ratio_changed = MagicMock()
        panel.test_mode_changed = MagicMock()
        panel.selected_zone = 0
        return panel

    @pytest.fixture
    def handler(self, qapp, mock_panel, make_led_state):
        """Create a LEDHandler with a real QWidget for QTimer parent."""
        from trcc.ui.gui.led_handler import LEDHandler

        # QTimer needs a real QObject parent, so pass QWidget as panel.
        # led=None keeps _connect_signals() a no-op; swap _panel after.
        real_parent = QWidget()
        on_temp = MagicMock()
        h = LEDHandler(None, real_parent, on_temp)
        h._qt_parent = real_parent  # prevent GC
        h._panel = mock_panel
        h._temp_unit_cb = on_temp
        h._make_led_state = make_led_state
        return h

    def _wire_led(self, handler, zones=None, **state_kw):
        """Attach a mock LEDDevice to the handler. Returns the mock."""
        mock_led = MagicMock()
        mock_led.state = handler._make_led_state(zones=zones, **state_kw)
        handler._led = mock_led
        return mock_led

    # ── Construction & properties ─────────────────────────────────

    def test_construction_defaults(self, handler):
        assert handler._active is False
        assert handler._led is None
        assert handler._style_id == 0
        assert handler._metrics_count == 0

    def test_active_property_false_initially(self, handler):
        assert handler.active is False

    def test_has_controller_false_initially(self, handler):
        assert handler.has_controller is False

    def test_led_port_none_initially(self, handler):
        assert handler.led_port is None

    # ── Lifecycle: stop / cleanup ────────────────────────────────

    def test_stop_when_inactive(self, handler):
        """stop() when never started should not raise."""
        handler.deactivate()
        assert handler.active is False

    def test_stop_sets_active_false(self, handler):
        handler._active = True
        handler.deactivate()
        assert handler.active is False

    def test_stop_saves_config_and_deactivates(self, handler):
        """stop() saves config and sets active=False."""
        mock_led = self._wire_led(handler)
        handler._active = True
        handler.deactivate()
        mock_led.save_config.assert_called_once()
        assert not handler._active

    def test_cleanup_saves_config_before_cleanup(self, handler):
        mock_led = self._wire_led(handler)
        handler.cleanup()
        mock_led.save_config.assert_called_once()
        mock_led.cleanup.assert_called_once()

    # ── set_temp_unit ────────────────────────────────────────────

    def test_set_temp_unit_no_led(self, handler):
        handler.set_temp_unit(0)  # Should not raise

    def test_set_temp_unit_with_led(self, handler):
        mock_led = self._wire_led(handler)
        handler.set_temp_unit(1)
        mock_led.set_temp_unit.assert_called_once_with(1)

    # ── _sync_ui_from_state ──────────────────────────────────────

    def test_sync_ui_no_led(self, handler):
        handler._sync_ui_from_state()  # Should not raise

    def test_sync_ui_with_zones(self, handler):
        """Loads zone 0 state into panel when zones exist."""
        zone = LEDZoneState(
            mode=LEDMode.BREATHING, color=(0, 255, 0), brightness=80, on=True
        )
        self._wire_led(handler, zones=[zone])
        handler._sync_ui_from_state()
        handler._panel.load_zone_state.assert_called_once_with(
            0, LEDMode.BREATHING.value, (0, 255, 0), 80, True
        )

    def test_sync_ui_without_zones(self, handler):
        """Loads global state into panel when no zones."""
        self._wire_led(
            handler, mode=LEDMode.RAINBOW, color=(100, 200, 50),
            brightness=90, global_on=True,
        )
        handler._sync_ui_from_state()
        handler._panel.load_zone_state.assert_called_once_with(
            0, LEDMode.RAINBOW.value, (100, 200, 50), 90, True
        )

    # ── show() ───────────────────────────────────────────────────

    def _show_with_mock_led(self, handler, device, mock_led, style_info,
                            style_id=1):
        """Helper: wire LED into handler then call show()."""
        handler._led = mock_led
        with (
            patch("trcc.services.led.LEDService") as mock_svc,
            patch("trcc.conf.settings") as mock_settings,
        ):
            mock_svc.resolve_style_id.return_value = style_id
            mock_svc.get_style_info.return_value = style_info
            mock_settings.temp_unit = 0
            handler.show(device)

    def _make_device_and_style(self, handler, model="AX120_DIGITAL", style_id=1,
                               zone_count=1, segment_count=10):
        """Create device mock + style info for show() tests."""
        device = MagicMock()
        device.model = model
        device.led_style_id = style_id
        mock_led = MagicMock()
        mock_led.state = handler._make_led_state()
        style_info = MagicMock()
        style_info.segment_count = segment_count
        style_info.zone_count = zone_count
        return device, mock_led, style_info

    def test_show_creates_led_device(self, handler):
        device, mock_led, style_info = self._make_device_and_style(handler)
        self._show_with_mock_led(handler, device, mock_led, style_info)
        assert handler._led is mock_led
        assert handler._active is True
        handler.deactivate()

    def test_show_wires_get_protocol(self, handler):
        """show() calls initialize() on the injected LEDDevice (regression: #61).

        _led is injected via __init__ (from ControllerBuilder.build_led()),
        which wires get_protocol. show() must use the injected device, not
        create a new one.
        """
        device, mock_led, style_info = self._make_device_and_style(handler)
        self._show_with_mock_led(handler, device, mock_led, style_info)
        mock_led.initialize_led.assert_called_once_with(device, 1)
        handler.deactivate()

    def test_show_activates_handler(self, handler):
        device, mock_led, style_info = self._make_device_and_style(handler)
        self._show_with_mock_led(handler, device, mock_led, style_info)
        assert handler._active
        handler.deactivate()

    def test_show_initializes_led_device(self, handler):
        device, mock_led, style_info = self._make_device_and_style(
            handler, model="PA120_DIGITAL", style_id=2, zone_count=4)
        self._show_with_mock_led(handler, device, mock_led, style_info,
                                 style_id=2)
        mock_led.initialize_led.assert_called_once_with(device, 2)
        handler.deactivate()

    # ── Metrics-driven updates ────────────────────────────────────

    def test_update_metrics_no_led(self, handler):
        handler.update_metrics(MagicMock())  # Should not raise

    def test_update_metrics_not_active(self, handler):
        mock_led = self._wire_led(handler)
        handler._active = False
        handler.update_metrics(MagicMock())
        mock_led.update_metrics.assert_not_called()

    def test_update_metrics_active_updates_panel(self, handler):
        """update_metrics updates panel text; colors arrive via FRAME_RENDERED."""
        self._wire_led(handler)
        handler._active = True
        metrics = MagicMock()
        handler.update_metrics(metrics)
        handler._panel.update_metrics.assert_called_once_with(metrics)

    def test_update_metrics_saves_config_at_interval(self, handler):
        """Config is saved every _SAVE_INTERVAL metrics updates."""
        mock_led = self._wire_led(handler)
        handler._active = True
        handler._metrics_count = handler._SAVE_INTERVAL - 1
        handler.update_metrics(MagicMock())
        mock_led.save_config.assert_called_once()

    def test_update_metrics_no_save_before_interval(self, handler):
        """Config is NOT saved before _SAVE_INTERVAL updates."""
        mock_led = self._wire_led(handler)
        handler._active = True
        handler._metrics_count = 0
        handler.update_metrics(MagicMock())
        mock_led.save_config.assert_not_called()

    def test_update_metrics_resets_counter_after_save(self, handler):
        """Save counter resets to 0 after config save."""
        self._wire_led(handler)
        handler._active = True
        handler._metrics_count = handler._SAVE_INTERVAL - 1
        handler.update_metrics(MagicMock())
        assert handler._metrics_count == 0

    # ── Signal handlers: mode ────────────────────────────────────

    def test_on_mode_changed_no_led_via_guard(self, handler):
        """Guard prevents crash when _led is None."""
        guarded = handler._guard(handler._on_mode_changed)
        handler._active = True  # active but no _led
        guarded(0)  # Should not raise

    def test_on_mode_changed_calls_update_mode(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_mode_changed(2)
        mock_led.update_mode.assert_called_once_with(2)

    def test_on_mode_changed_no_tick_or_send(self, handler):
        """Mode change is state-only — no immediate tick/send."""
        mock_led = self._wire_led(handler)
        handler._on_mode_changed(2)
        mock_led.tick.assert_not_called()

    def test_on_mode_changed_forwards_to_zone(self, handler):
        zones = [LEDZoneState(), LEDZoneState()]
        mock_led = self._wire_led(handler, zones=zones)
        handler._panel.selected_zone = 1
        handler._on_mode_changed(3)
        mock_led.update_zone_mode.assert_called_with(1, 3)

    def test_on_mode_changed_forces_save(self, handler):
        """Mode change sets save counter to interval (forces next-tick save)."""
        self._wire_led(handler)
        handler._metrics_count = 0
        handler._on_mode_changed(2)
        assert handler._metrics_count == handler._SAVE_INTERVAL

    # ── Signal handlers: color ───────────────────────────────────

    def test_on_color_changed_no_led_via_guard(self, handler):
        guarded = handler._guard(handler._on_color_changed)
        handler._active = True
        guarded(255, 0, 0)  # Should not raise

    def test_on_color_changed_calls_update_color(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_color_changed(0, 128, 255)
        mock_led.update_color.assert_called_once_with(0, 128, 255)

    def test_on_color_changed_no_tick_or_send(self, handler):
        """Color change is state-only — no immediate tick/send (C# pattern)."""
        mock_led = self._wire_led(handler)
        handler._on_color_changed(255, 0, 0)
        mock_led.tick.assert_not_called()

    def test_on_color_changed_forwards_to_zone(self, handler):
        zones = [LEDZoneState(), LEDZoneState()]
        mock_led = self._wire_led(handler, zones=zones)
        handler._panel.selected_zone = 1
        handler._on_color_changed(10, 20, 30)
        mock_led.update_color.assert_called_with(10, 20, 30)
        mock_led.update_zone_color.assert_called_with(1, 10, 20, 30)

    def test_on_color_changed_no_zone_forward_without_zones(self, handler):
        mock_led = self._wire_led(handler)  # no zones
        handler._on_color_changed(10, 20, 30)
        mock_led.update_color.assert_called_once_with(10, 20, 30)
        mock_led.update_zone_color.assert_not_called()

    # ── Signal handlers: brightness ──────────────────────────────

    def test_on_brightness_changed_no_led_via_guard(self, handler):
        guarded = handler._guard(handler._on_brightness_changed)
        handler._active = True
        guarded(50)  # Should not raise

    def test_on_brightness_changed_calls_update_brightness(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_brightness_changed(80)
        mock_led.update_brightness.assert_called_once_with(80)

    def test_on_brightness_changed_forwards_to_zone(self, handler):
        zones = [LEDZoneState(), LEDZoneState()]
        mock_led = self._wire_led(handler, zones=zones)
        handler._panel.selected_zone = 0
        handler._on_brightness_changed(60)
        mock_led.update_zone_brightness.assert_called_with(0, 60)

    def test_on_brightness_changed_no_zone_forward_without_zones(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_brightness_changed(60)
        mock_led.update_brightness.assert_called_once_with(60)
        mock_led.update_zone_brightness.assert_not_called()

    # ── Signal handlers: global toggle ───────────────────────────

    def test_on_global_toggled_no_led_via_guard(self, handler):
        handler._guard(handler._on_global_toggled)(True)  # Should not raise

    def test_on_global_toggled_on(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_global_toggled(True)
        mock_led.update_global_on.assert_called_once_with(True)

    def test_on_global_toggled_off(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_global_toggled(False)
        mock_led.update_global_on.assert_called_once_with(False)

    # ── Signal handlers: segment click ───────────────────────────

    def test_on_segment_clicked_no_led_via_guard(self, handler):
        guarded = handler._guard(handler._on_segment_clicked)
        handler._active = True
        guarded(0)  # Should not raise

    def test_on_segment_clicked_toggles_segment(self, handler):
        """Click toggles segment on→off (reads current, sends inverse)."""
        mock_led = self._wire_led(handler)
        mock_led.state.segment_on = [True, False, True]
        handler._on_segment_clicked(0)
        mock_led.update_segment.assert_called_once_with(0, False)

    def test_on_segment_clicked_toggles_off_to_on(self, handler):
        mock_led = self._wire_led(handler)
        mock_led.state.segment_on = [True, False, True]
        handler._on_segment_clicked(1)
        mock_led.update_segment.assert_called_once_with(1, True)

    def test_on_segment_clicked_out_of_range(self, handler):
        """Out-of-range index is silently ignored."""
        mock_led = self._wire_led(handler)
        mock_led.state.segment_on = [True, True]
        handler._on_segment_clicked(5)
        mock_led.update_segment.assert_not_called()

    # ── Signal handlers: zone select ─────────────────────────────

    def test_on_zone_selected_no_led_via_guard(self, handler):
        handler._guard(handler._on_zone_selected)(0)  # Should not raise

    def test_on_zone_selected_loads_zone_state(self, handler):
        z0 = LEDZoneState(mode=LEDMode.COLORFUL, color=(10, 20, 30),
                          brightness=50, on=False)
        z1 = LEDZoneState(mode=LEDMode.RAINBOW, color=(40, 50, 60),
                          brightness=70, on=True)
        mock_led = self._wire_led(handler, zones=[z0, z1])
        handler._on_zone_selected(1)
        mock_led.update_selected_zone.assert_called_once_with(1)
        handler._panel.load_zone_state.assert_called_once_with(
            1, LEDMode.RAINBOW.value, (40, 50, 60), 70, True
        )

    def test_on_zone_selected_no_zones_no_op(self, handler):
        self._wire_led(handler)  # no zones
        handler._on_zone_selected(0)
        handler._panel.load_zone_state.assert_not_called()

    # ── Signal handlers: zone toggle ─────────────────────────────

    def test_on_zone_toggled_no_led_via_guard(self, handler):
        handler._guard(handler._on_zone_toggled)(0, True)  # Should not raise

    def test_on_zone_toggled_on(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_zone_toggled(2, True)
        mock_led.update_zone_on.assert_called_once_with(2, True)

    def test_on_zone_toggled_off(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_zone_toggled(1, False)
        mock_led.update_zone_on.assert_called_once_with(1, False)

    # ── Signal handlers: carousel (zone sync) ────────────────────

    def test_on_carousel_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_carousel_changed)(True)  # Should not raise

    def test_on_carousel_changed_enable(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_carousel_changed(True)
        mock_led.update_zone_sync.assert_called_once_with(True)

    def test_on_carousel_changed_disable(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_carousel_changed(False)
        mock_led.update_zone_sync.assert_called_once_with(False)

    def test_on_carousel_zone_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_carousel_zone_changed)(0, True)  # Should not raise

    def test_on_carousel_zone_changed(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_carousel_zone_changed(2, True)
        mock_led.update_zone_sync_zone.assert_called_once_with(2, True)

    def test_on_carousel_interval_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_carousel_interval_changed)(5)  # Should not raise

    def test_on_carousel_interval_changed(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_carousel_interval_changed(10)
        mock_led.update_zone_sync_interval.assert_called_once_with(10)

    # ── Signal handlers: clock format ────────────────────────────

    def test_on_clock_format_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_clock_format_changed)(True)  # Should not raise

    def test_on_clock_format_changed_24h(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_clock_format_changed(True)
        mock_led.update_clock_format.assert_called_once_with(True)

    def test_on_clock_format_changed_12h(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_clock_format_changed(False)
        mock_led.update_clock_format.assert_called_once_with(False)

    # ── Signal handlers: week start ──────────────────────────────

    def test_on_week_start_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_week_start_changed)(True)  # Should not raise

    def test_on_week_start_changed_sunday(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_week_start_changed(True)
        mock_led.update_week_start.assert_called_once_with(True)

    def test_on_week_start_changed_monday(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_week_start_changed(False)
        mock_led.update_week_start.assert_called_once_with(False)

    # ── Signal handlers: disk index ──────────────────────────────

    def test_on_disk_index_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_disk_index_changed)(0)  # Should not raise

    def test_on_disk_index_changed(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_disk_index_changed(3)
        mock_led.update_disk_index.assert_called_once_with(3)

    # ── Signal handlers: memory ratio ────────────────────────────

    def test_on_memory_ratio_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_memory_ratio_changed)(1)  # Should not raise

    def test_on_memory_ratio_changed(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_memory_ratio_changed(2)
        mock_led.update_memory_ratio.assert_called_once_with(2)

    # ── Signal handlers: test mode ───────────────────────────────

    def test_on_test_mode_changed_no_led_via_guard(self, handler):
        handler._guard(handler._on_test_mode_changed)(True)  # Should not raise

    def test_on_test_mode_changed_enable(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_test_mode_changed(True)
        mock_led.update_test_mode.assert_called_once_with(True)

    def test_on_test_mode_changed_disable(self, handler):
        mock_led = self._wire_led(handler)
        handler._on_test_mode_changed(False)
        mock_led.update_test_mode.assert_called_once_with(False)

    # ── Hexagonal purity: no handler calls set_*() / tick() ──────

    def test_handlers_use_update_not_set_methods(self, handler):
        """All handlers call update_*() (state-only), never set_*() (CLI/API).

        This is the core architectural invariant: GUI signal handlers call
        update_*() (state-only), timer-driven tick handles animation + USB send.
        """
        mock_led = self._wire_led(handler, zones=[LEDZoneState()])
        handler._panel.selected_zone = 0

        # Fire every handler
        handler._on_mode_changed(1)
        handler._on_color_changed(255, 0, 0)
        handler._on_brightness_changed(50)
        handler._on_global_toggled(True)
        handler._on_segment_clicked(0)
        handler._on_zone_toggled(0, True)
        handler._on_carousel_changed(True)
        handler._on_carousel_zone_changed(0, True)
        handler._on_carousel_interval_changed(5)
        handler._on_clock_format_changed(True)
        handler._on_week_start_changed(True)
        handler._on_disk_index_changed(0)
        handler._on_memory_ratio_changed(1)
        handler._on_test_mode_changed(True)

        # update_*() methods SHOULD be called (state-only)
        mock_led.update_mode.assert_called()
        mock_led.update_color.assert_called()
        mock_led.update_brightness.assert_called()
        mock_led.update_global_on.assert_called()
        mock_led.update_segment.assert_called()

        # None of the CLI/API set_*() methods should be called
        mock_led.set_color.assert_not_called()
        mock_led.set_mode.assert_not_called()
        mock_led.set_brightness.assert_not_called()
        mock_led.toggle_global.assert_not_called()
        mock_led.toggle_segment.assert_not_called()
        mock_led.toggle_zone.assert_not_called()
        mock_led.set_zone_sync.assert_not_called()
        mock_led.set_zone_color.assert_not_called()
        mock_led.set_zone_mode.assert_not_called()
        mock_led.set_zone_brightness.assert_not_called()
        mock_led.set_clock_format.assert_not_called()
        mock_led.set_week_start.assert_not_called()
        # No handler should call tick() directly
        mock_led.tick.assert_not_called()

    # ── _sync_ui_from_state: carousel restore ───────────────────

    def test_sync_ui_restores_carousel(self, handler):
        """zone_sync + zone_sync_zones → load_sync_state on panel."""
        self._wire_led(
            handler,
            zone_sync=True,
            zone_sync_zones=[True, False],
            zone_sync_interval=13,
        )
        handler._sync_ui_from_state()
        # 13 ticks × 150ms / 1000 = 1.95 → rounds to 2
        handler._panel.load_sync_state.assert_called_once_with(
            True, [True, False], 2,
        )

    def test_sync_ui_no_carousel_without_sync_zones(self, handler):
        """Empty zone_sync_zones → load_sync_state not called."""
        self._wire_led(handler, zone_sync=False, zone_sync_zones=[])
        handler._sync_ui_from_state()
        handler._panel.load_sync_state.assert_not_called()


# =========================================================================
# ScreencastHandler
# =========================================================================


class TestScreencastHandler:
    """Test the ScreencastHandler state machine from trcc_app."""

    @pytest.fixture
    def handler(self, qapp):
        from trcc.ui.gui.trcc_app import ScreencastHandler

        parent = QWidget()
        on_frame = MagicMock()
        h = ScreencastHandler(parent, on_frame)
        h.set_lcd_size(320, 320)
        # Keep parent alive so QTimer's C++ parent isn't deleted
        h._qt_parent = parent
        return h

    def test_construction(self, handler):
        assert handler.active is False
        assert handler._x == 0
        assert handler._y == 0
        assert handler._w == 0
        assert handler._h == 0
        assert handler._border is True
        assert handler._pipewire_cast is None

    def test_active_property(self, handler):
        assert handler.active is False
        handler._active = True
        assert handler.active is True

    def test_set_params(self, handler):
        handler.set_params(10, 20, 640, 480)
        assert handler._x == 10
        assert handler._y == 20
        assert handler._w == 640
        assert handler._h == 480

    def test_set_border(self, handler):
        handler.set_border(False)
        assert handler._border is False
        handler.set_border(True)
        assert handler._border is True

    def test_stop_sets_inactive(self, handler):
        handler._active = True
        handler.stop()
        assert handler.active is False

    def test_stop_stops_timer(self, handler):
        handler._timer.start(100)
        handler.stop()
        assert not handler._timer.isActive()

    def test_cleanup_stops_timer(self, handler):
        handler._timer.start(100)
        handler.cleanup()
        assert not handler._timer.isActive()

    def test_cleanup_stops_pipewire(self, handler):
        mock_cast = MagicMock()
        handler._pipewire_cast = mock_cast
        handler.cleanup()
        mock_cast.stop.assert_called_once()
        assert handler._pipewire_cast is None

    def test_toggle_on_starts_timer(self, handler):
        with patch.object(handler, "_try_start_pipewire"):
            with patch("trcc.ui.gui.screen_capture.is_wayland", return_value=False):
                handler.toggle(True)
        assert handler.active is True
        assert handler._timer.isActive()
        handler.stop()

    def test_toggle_off_stops_timer(self, handler):
        with patch("trcc.ui.gui.screen_capture.is_wayland", return_value=False):
            handler.toggle(True)
        handler.toggle(False)
        assert handler.active is False
        assert not handler._timer.isActive()

    def test_tick_no_op_when_inactive(self, handler):
        """_tick does nothing when not active."""
        handler._active = False
        handler._w = 100
        handler._h = 100
        handler._tick()  # Should not raise

    def test_tick_no_op_when_zero_dimensions(self, handler):
        """_tick does nothing when dimensions are zero."""
        handler._active = True
        handler._w = 0
        handler._h = 0
        handler._tick()  # Should not raise


# =========================================================================
# Category images / colors domain data
# =========================================================================


class TestCategoryData:
    """Verify category domain data consistency."""

    def test_category_images_has_all_ids(self, qapp):
        for i in range(7):
            assert i in CATEGORY_IMAGES

    def test_category_colors_has_all_ids(self, qapp):
        for i in range(7):
            assert i in CATEGORY_COLORS

    def test_category_colors_are_hex(self, qapp):
        for color in CATEGORY_COLORS.values():
            assert color.startswith("#")
            assert len(color) == 7

    def test_category_images_are_png(self, qapp):
        for img in CATEGORY_IMAGES.values():
            assert img.endswith(".png")


# =========================================================================
# Device poll — LED auto-select must start metrics mediator
# =========================================================================


class TestDevicePollLEDAutoSelect:
    """_activate_device auto-selects LED devices and calls handler.show().

    Regression test for #61: on autostart (--last-one), LED device was
    auto-selected but handler.show() was never called, so the display
    showed all zeros until the user manually clicked the device button.
    """

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_auto_select_led_calls_show(self, mock_settings, bare_trcc_app):
        """When _activate_device selects an LED path, handler.show() is called
        if the handler is not yet active."""
        from trcc.ui.gui.led_handler import LEDHandler

        mock_info = MagicMock()
        mock_info.path = 'led_path'

        mock_handler = MagicMock(spec=LEDHandler)
        mock_handler.active = False
        mock_handler.device_info = mock_info
        mock_handler.view_name = 'led'

        app = bare_trcc_app
        app._handlers = {'led_path': mock_handler}
        app._active_path = ''
        app._show_view = MagicMock()
        app._activate_device('led_path')

        mock_handler.show.assert_called_once_with(mock_info)


# =========================================================================
# _activate_device LCD guard — no re-init on sidebar re-navigation
# =========================================================================


class TestActivateDeviceLCDGuard:
    """_activate_device must not call apply_device_config when device_key is already set.

    Regression test: clicking control-center / metrics sidebar items was triggering
    a full apply_device_config (600 ms SCSI operation) on every click because
    _activate_device was unconditional. Fixed by guarding with `not handler.device_key`.
    """

    def _make_lcd_handler(self, device_key: str = ''):
        from trcc.ui.gui.lcd_handler import LCDHandler

        info = MagicMock()
        info.resolution = (320, 320)

        mock_handler = MagicMock(spec=LCDHandler)
        mock_handler.device_key = device_key
        mock_handler.display.connected = True
        mock_handler.display.device_info = info
        mock_handler.view_name = 'lcd'
        return mock_handler

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_apply_device_config_called_on_first_activation(self, mock_settings,
                                                             bare_trcc_app):
        """apply_device_config is called when device_key is empty (first activation)."""
        handler = self._make_lcd_handler(device_key='')

        app = bare_trcc_app
        app._handlers = {'lcd_path': handler}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._activate_device('lcd_path')

        handler.apply_device_config.assert_called_once()

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_apply_device_config_skipped_when_already_initialized(self, mock_settings,
                                                                   bare_trcc_app):
        """apply_device_config is NOT called when device_key is already set (re-navigation)."""
        handler = self._make_lcd_handler(device_key='0:0402:3922')

        app = bare_trcc_app
        app._handlers = {'lcd_path': handler}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._activate_device('lcd_path')

        handler.apply_device_config.assert_not_called()

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_reactivate_called_when_already_initialized(self, mock_settings,
                                                         bare_trcc_app):
        """reactivate() is called instead of apply_device_config on re-navigation."""
        handler = self._make_lcd_handler(device_key='0:0402:3922')

        app = bare_trcc_app
        app._handlers = {'lcd_path': handler}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._activate_device('lcd_path')

        handler.reactivate.assert_called_once_with(320, 320)

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_saves_last_device_on_activate(self, mock_settings, bare_trcc_app):
        """_activate_device persists device index via Settings.save_last_device."""
        handler = self._make_lcd_handler(device_key='k')
        handler.device_info.device_index = 2

        app = bare_trcc_app
        app._handlers = {'lcd_path': handler}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._activate_device('lcd_path')

        mock_settings.save_last_device.assert_called_once_with(2)


class TestRebuildAllHandlersRestore:
    """_rebuild_all_handlers restores last active device from config."""

    def _make_lcd_handler(self, device_index: int, device_key: str = ''):
        from trcc.ui.gui.lcd_handler import LCDHandler

        info = MagicMock()
        info.resolution = (320, 320)
        info.device_index = device_index

        mock_handler = MagicMock(spec=LCDHandler)
        mock_handler.device_key = device_key
        mock_handler.display.connected = True
        mock_handler.display.device_info = info
        mock_handler.device_info = info
        mock_handler.view_name = 'lcd'
        return mock_handler

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_restores_last_device(self, mock_settings, bare_trcc_app):
        mock_settings.get_last_device.return_value = 1

        h0 = self._make_lcd_handler(device_index=0)
        h1 = self._make_lcd_handler(device_index=1)

        app = bare_trcc_app
        app._handlers = {'path0': h0, 'path1': h1}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._refresh_sidebar = MagicMock()
        app._add_handler = MagicMock()

        # Simulate _rebuild_all_handlers selection logic
        last_idx = mock_settings.get_last_device()
        target = next(
            (p for p, h in app._handlers.items()
             if h.device_info and h.device_info.device_index == last_idx),
            None,
        )
        assert target == 'path1'

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_falls_back_to_first_lcd(self, mock_settings, bare_trcc_app):
        mock_settings.get_last_device.return_value = 99  # Not found

        h0 = self._make_lcd_handler(device_index=0)

        app = bare_trcc_app
        app._handlers = {'path0': h0}
        app._active_path = ''

        last_idx = mock_settings.get_last_device()
        target = next(
            (p for p, h in app._handlers.items()
             if h.device_info and h.device_info.device_index == last_idx),
            None,
        )
        assert target is None  # Falls back to first LCD logic

    @patch('trcc.ui.gui.trcc_app.Settings')
    def test_restores_inactive_lcd_themes(self, mock_settings, bare_trcc_app):
        """Inactive LCD devices get their saved theme sent to hardware on startup.

        Restore is now driven by ``LCDHandler.restore_inactive_state()``, which
        internally calls ``LCDDevice.restore_device_settings()`` +
        ``restore_last_theme()`` on the per-device LCD.  trcc_app.py only
        delegates to the handler — the fan-out lives in the handler.
        """
        mock_settings.get_last_device.return_value = 0

        h0 = self._make_lcd_handler(device_index=0, device_key='k0')
        h1 = self._make_lcd_handler(device_index=1, device_key='k1')
        handlers = {'path0': h0, 'path1': h1}

        app = bare_trcc_app
        app._handlers = {}
        app._active_path = ''
        app._show_view = MagicMock()
        app._update_ldd_icon = MagicMock()
        app._start_handshake = MagicMock()
        app._refresh_sidebar = MagicMock()
        # _add_handler repopulates _handlers from the device list
        app._add_handler = MagicMock(side_effect=lambda d: app._handlers.update(
            {p: h for p, h in handlers.items()
             if h.device_info.device_index == d.device_info.device_index}))

        devices = [MagicMock(device_info=h0.device_info), MagicMock(device_info=h1.device_info)]
        app._rebuild_all_handlers(devices)

        # h1 is inactive — handler-layer restore_inactive_state must fire
        h1.restore_inactive_state.assert_called_once()

        # h0 is the active device — _activate_device flow, not inactive restore
        h0.restore_inactive_state.assert_not_called()


class TestHandshakeDoneGuard:
    """_on_handshake_done must not call apply_device_config when device_key is set.

    Regression test: if a handshake completes while a theme is already playing
    (e.g. duplicate handshake signal), apply_device_config fired unconditionally,
    stopped the video theme, and reloaded the saved static theme_path.
    Fixed by guarding with `not handler.device_key`.
    """

    def _make_handler(self, device_key: str):
        from trcc.ui.gui.lcd_handler import LCDHandler

        device = MagicMock()
        device.path = 'lcd_path'
        device.resolution = (320, 320)

        handler = MagicMock(spec=LCDHandler)
        handler.device_key = device_key
        handler.view_name = 'lcd'

        return device, handler

    def test_applies_config_on_first_handshake(self, bare_trcc_app):
        """apply_device_config is called when device_key is empty (first handshake)."""
        from trcc.conf import Settings
        from trcc.core.models import HandshakeResult

        device, handler = self._make_handler(device_key='')
        app = bare_trcc_app
        app._handshake_pending = True
        app._handlers = {'lcd_path': handler}
        app._update_ldd_icon = MagicMock()
        app.uc_preview = MagicMock()
        app.uc_info_module = MagicMock()
        app._sync_device_identity = MagicMock()

        result = HandshakeResult(resolution=(320, 320), model_id=100)
        with patch.object(Settings, 'show_info_module', return_value=False):
            app._on_handshake_done(device, result)
        handler.apply_device_config.assert_called_once()

    def test_skips_config_on_duplicate_handshake(self, bare_trcc_app):
        """apply_device_config is NOT called when device_key already set (duplicate handshake)."""
        from trcc.core.models import HandshakeResult

        device, handler = self._make_handler(device_key='0:0402:3922')
        app = bare_trcc_app
        app._handshake_pending = True
        app._handlers = {'lcd_path': handler}
        app._update_ldd_icon = MagicMock()
        app.uc_preview = MagicMock()
        app.uc_info_module = MagicMock()
        app._sync_device_identity = MagicMock()

        result = HandshakeResult(resolution=(320, 320), model_id=100)
        app._on_handshake_done(device, result)
        handler.apply_device_config.assert_not_called()


# =========================================================================
# Device identity resolution — button image from handshake PM/SUB
# =========================================================================


class TestResolveDeviceIdentity:
    """_resolve_device_identity must map handshake PM/SUB to correct button image.

    SCSI devices: PM=0 from handshake, so _on_handshake_done uses `pm or fbl`
    which falls through to FBL. FBL is then used as PM in get_button_image().
    Confirmed by decompiling USBLCD.exe — SCSI devices write FBL to shared
    memory, TRCC.exe calls ADDUserButton(257, fbl, 0).
    """

    @staticmethod
    def _make_device(path: str, protocol: str, vid: int = 0x0402, pid: int = 0x3922):
        from trcc.core.models import DeviceInfo
        return DeviceInfo(
            name='LCD Display', path=path, vendor='Test', product='LCD',
            model='CZTV', vid=vid, pid=pid, device_index=0,
            protocol=protocol, device_type=1, implementation='test',
            button_image='A1CZTV',
        )

    def test_scsi_uses_fbl_as_pm(self, bare_trcc_app):
        """SCSI handshake: pm=0 → `pm or fbl` resolves to fbl=100 → FROZEN WARFRAME PRO."""
        from trcc.core.models import HandshakeResult

        app = bare_trcc_app
        app._handshake_pending = True
        app.uc_preview = MagicMock()
        app.uc_info_module = MagicMock()
        app._handlers = {}

        device = self._make_device('/dev/sg0', 'scsi')

        app.uc_device = MagicMock()
        dev_dict = {'path': '/dev/sg0', 'button_image': 'A1CZTV', 'name': 'LCD'}
        app.uc_device.devices = [dev_dict]

        # SCSI handshake: resolution, fbl=100, pm=0, sub=0
        app._on_handshake_done(
            device, HandshakeResult(resolution=(320, 320), model_id=100))

        # pm=0 → `pm or fbl` = 100 → get_variant_override(100, 0).button_image
        # = A1FROZEN WARFRAME PRO
        assert dev_dict['button_image'] == 'A1FROZEN WARFRAME PRO'
        app.uc_device.update_device_button.assert_called_once_with(dev_dict)

    def test_hid_uses_pm_sub_directly(self, bare_trcc_app):
        """HID handshake: pm=7, sub=1 → Stream Vision (not FBL-based)."""
        from trcc.core.models import HandshakeResult

        app = bare_trcc_app
        app._handshake_pending = True
        app.uc_preview = MagicMock()
        app.uc_info_module = MagicMock()
        app._handlers = {}

        device = self._make_device('hid:0416:5302', 'hid', vid=0x0416, pid=0x5302)

        app.uc_device = MagicMock()
        dev_dict = {'path': 'hid:0416:5302', 'button_image': 'A1CZTV', 'name': 'LCD'}
        app.uc_device.devices = [dev_dict]

        # HID handshake: resolution, fbl=64, pm=7, sub=1
        app._on_handshake_done(device, HandshakeResult(
            resolution=(480, 480), model_id=64, pm_byte=7, sub_byte=1))

        # pm=7 is truthy → `pm or fbl` = 7 → get_variant_override(7, 1).button_image
        # = A1Stream Vision
        assert dev_dict['button_image'] == 'A1Stream Vision'

    def test_unknown_pm_keeps_original_button(self, bare_trcc_app):
        """When PM/SUB doesn't match any entry, button_image stays unchanged."""
        from trcc.core.models import HandshakeResult

        app = bare_trcc_app
        app._handshake_pending = True
        app.uc_preview = MagicMock()
        app.uc_info_module = MagicMock()
        app._handlers = {}

        device = self._make_device('/dev/sg0', 'scsi')

        app.uc_device = MagicMock()
        dev_dict = {'path': '/dev/sg0', 'button_image': 'A1CZTV', 'name': 'LCD'}
        app.uc_device.devices = [dev_dict]

        # Unknown FBL=255 — no match in _LCD_BUTTON_IMAGE
        app._on_handshake_done(
            device, HandshakeResult(resolution=(320, 320), model_id=255))

        # Button image unchanged
        assert dev_dict['button_image'] == 'A1CZTV'
        app.uc_device.update_device_button.assert_not_called()


# =========================================================================
# Navigation: device button must work after About/SysInfo
# =========================================================================


class TestDeviceButtonAfterViewSwitch:
    """Clicking a device button after About/SysInfo must return to form view.

    Bug: _activate_device early-returns when path == _active_path.
    Navigating to About doesn't clear _active_path, so clicking the same
    device does nothing. Fix: _show_view clears _active_path when leaving form.
    """

    def test_show_view_clears_active_path(self, bare_trcc_app):
        """Navigating away from form clears _active_path."""
        app = bare_trcc_app
        app._active_path = '/dev/sg0'
        app.form_container = MagicMock()
        app.uc_about = MagicMock()
        app.uc_system_info = MagicMock()
        app.uc_led_control = MagicMock()
        app.uc_activity_sidebar = MagicMock()
        app.form1_close_btn = MagicMock()
        app.form1_help_btn = MagicMock()

        app._show_view('about')
        assert app._active_path == ''

    def test_show_view_keeps_active_path_for_form(self, bare_trcc_app):
        """Showing form view does NOT clear _active_path."""
        app = bare_trcc_app
        app._active_path = '/dev/sg0'
        app.form_container = MagicMock()
        app.uc_about = MagicMock()
        app.uc_system_info = MagicMock()
        app.uc_led_control = MagicMock()
        app.uc_activity_sidebar = MagicMock()
        app.form1_close_btn = MagicMock()
        app.form1_help_btn = MagicMock()

        app._show_view('form')
        assert app._active_path == '/dev/sg0'


# =========================================================================
# View switch must NOT stop LED (#61)
# =========================================================================


class TestViewSwitchLEDKeepsRunning:
    """_show_view must NOT stop the LED when switching away from 'led' view.

    Regression test for #61: navigating away from the LED panel (e.g. clicking
    the back button or switching to about/sysinfo) was calling _led.stop()
    which closed the USB transport. The physical LED display went dark because
    it stopped receiving data.
    """

    @pytest.fixture()
    def app(self, bare_trcc_app):
        inst = bare_trcc_app
        inst._led = MagicMock()
        inst._led.active = True
        inst.form_container = MagicMock()
        inst.uc_about = MagicMock()
        inst.uc_system_info = MagicMock()
        inst.uc_led_control = MagicMock()
        inst.uc_activity_sidebar = MagicMock()
        inst.form1_close_btn = MagicMock()
        inst.form1_help_btn = MagicMock()
        return inst

    def test_show_view_form_does_not_stop_led(self, app):
        """Switching to 'form' view must not stop active LED."""
        app._show_view('form')
        app._led.stop.assert_not_called()

    def test_show_view_about_does_not_stop_led(self, app):
        """Switching to 'about' view must not stop active LED."""
        app._show_view('about')
        app._led.stop.assert_not_called()

    def test_show_view_sysinfo_does_not_stop_led(self, app):
        """Switching to 'sysinfo' view must not stop active LED."""
        app._show_view('sysinfo')
        app._led.stop.assert_not_called()
