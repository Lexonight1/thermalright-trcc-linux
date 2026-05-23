"""
Tests for gui.uc_theme_setting and gui.uc_theme_local.

Uses QT_QPA_PLATFORM=offscreen for headless testing.

Tests cover:
- Module constants: CATEGORY_NAMES, CATEGORY_COLORS, SUB_METRICS
- OverlayElementWidget: construction, config, selection, signals
- OverlayGridPanel: grid creation, cell selection, add/delete, serialization
- ColorPickerPanel: color change, position change, font change signals
- AddElementPanel: category dropdown, type buttons, element_added signal
- DataTablePanel: mode switching, format cycling, text input
- DisplayModePanel: toggle, action buttons, mode_changed signal
- UCThemeSetting: sub-panel wiring, tab switching, delegate routing
- ThemeThumbnail: construction, delete button, slideshow badge/mode
- UCThemeLocal: filter modes, slideshow toggle/max, theme loading, deletion
"""
from __future__ import annotations

import os
from dataclasses import replace
from unittest.mock import patch

# Must set before ANY Qt import
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QMouseEvent, QPixmap

from trcc.legacy.core.models import (
    LocalThemeItem,
    OverlayElementConfig,
    OverlayMode,
)
from trcc.legacy.ui.gui.uc_theme_local import (
    ThemeThumbnail,
    UCThemeLocal,
)
from trcc.legacy.ui.gui.uc_theme_setting import (
    CATEGORY_COLORS,
    CATEGORY_NAMES,
    SUB_METRICS,
    AddElementPanel,
    ColorPickerPanel,
    DataTablePanel,
    DisplayModePanel,
    OverlayElementWidget,
    OverlayGridPanel,
    ScreenCastPanel,
    UCThemeSetting,
)

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _patch_theme_assets(qapp):
    """Patch Assets.load_pixmap + set_background_pixmap across theme modules."""
    def _null_pixmap(*_args, **_kwargs):
        return QPixmap()

    with (
        patch("trcc.legacy.ui.gui.overlay_element.Assets.load_pixmap",
              side_effect=_null_pixmap),
        patch("trcc.legacy.ui.gui.overlay_grid.Assets.load_pixmap",
              side_effect=_null_pixmap),
        patch("trcc.legacy.ui.gui.color_and_add_panels.Assets.load_pixmap",
              side_effect=_null_pixmap),
        patch("trcc.legacy.ui.gui.display_mode_panels.Assets.load_pixmap",
              side_effect=_null_pixmap),
        patch("trcc.legacy.ui.gui.display_mode_panels.set_background_pixmap"),
        patch("trcc.legacy.ui.gui.color_and_add_panels.set_background_pixmap"),
        patch("trcc.legacy.ui.gui.uc_theme_local.Assets.load_pixmap",
              side_effect=_null_pixmap),
        patch("trcc.legacy.ui.gui.base.set_background_pixmap"),
    ):
        yield


@pytest.fixture
def make_config():
    """Factory fixture for OverlayElementConfig."""
    def _factory(
        mode: OverlayMode = OverlayMode.HARDWARE,
        x: int = 50,
        y: int = 60,
        main_count: int = 0,
        sub_count: int = 1,
        color: str = "#32C5FF",
    ) -> OverlayElementConfig:
        return OverlayElementConfig(
            mode=mode, x=x, y=y,
            main_count=main_count, sub_count=sub_count,
            color=color,
        )
    return _factory


@pytest.fixture
def make_local_item():
    """Factory fixture for LocalThemeItem."""
    def _factory(
        name: str = "TestTheme",
        path: str = "/tmp/themes/TestTheme",
        is_user: bool = False,
        index: int = 0,
    ) -> LocalThemeItem:
        return LocalThemeItem(
            name=name,
            path=path,
            thumbnail=f"{path}/Theme.png",
            is_user=is_user,
            index=index,
        )
    return _factory


# ============================================================================
# Module constants
# ============================================================================

class TestModuleConstants:
    """Test module-level constants in uc_theme_setting."""

    def test_category_names_has_six_entries(self):
        assert len(CATEGORY_NAMES) == 6

    def test_category_names_keys(self):
        assert set(CATEGORY_NAMES.keys()) == {0, 1, 2, 3, 4, 5}

    def test_category_names_values(self):
        expected = {"CPU", "GPU", "MEM", "HDD", "NET", "FAN"}
        assert set(CATEGORY_NAMES.values()) == expected

    def test_category_colors_has_six_entries(self):
        assert len(CATEGORY_COLORS) == 6

    def test_category_colors_keys_match_names(self):
        assert set(CATEGORY_COLORS.keys()) == set(CATEGORY_NAMES.keys())

    def test_category_colors_are_hex(self):
        for color in CATEGORY_COLORS.values():
            assert color.startswith("#")
            assert len(color) == 7

    def test_sub_metrics_has_six_categories(self):
        assert len(SUB_METRICS) == 6

    def test_sub_metrics_each_has_four_entries(self):
        for cat_id, metrics in SUB_METRICS.items():
            assert len(metrics) == 4, f"Category {cat_id} has {len(metrics)} entries"

    def test_sub_metrics_keys_start_at_one(self):
        for metrics in SUB_METRICS.values():
            assert set(metrics.keys()) == {1, 2, 3, 4}


# ============================================================================
# OverlayElementWidget
# ============================================================================

class TestOverlayElementWidget:
    """Test OverlayElementWidget construction and behavior."""

    def test_construction(self, qapp):
        widget = OverlayElementWidget(0)
        assert widget.index == 0
        assert widget.config is None
        assert widget._selected is False

    def test_construction_with_index(self, qapp):
        widget = OverlayElementWidget(41)
        assert widget.index == 41

    def test_set_config(self, qapp, make_config):
        widget = OverlayElementWidget(0)
        cfg = make_config()
        widget.set_config(cfg)
        assert widget.config is cfg

    def test_set_config_clears_live_values(self, qapp, make_config):
        widget = OverlayElementWidget(0)
        widget._live_value = "42"
        widget._live_unit = "C"
        widget.set_config(make_config())
        assert widget._live_value == ""
        assert widget._live_unit == ""

    def test_set_config_none(self, qapp, make_config):
        widget = OverlayElementWidget(0)
        widget.set_config(make_config())
        widget.set_config(None)
        assert widget.config is None

    def test_set_selected(self, qapp):
        widget = OverlayElementWidget(0)
        widget.set_selected(True)
        assert widget._selected is True
        widget.set_selected(False)
        assert widget._selected is False

    def test_clicked_signal(self, qapp):
        widget = OverlayElementWidget(7)
        received = []
        widget.clicked.connect(received.append)
        # Simulate left click
        from PySide6.QtCore import QPointF
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(30, 30),
            QPointF(30, 30),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.mousePressEvent(event)
        assert received == [7]

    def test_double_click_only_with_config(self, qapp, make_config):
        widget = OverlayElementWidget(3)
        received = []
        widget.double_clicked.connect(received.append)
        from PySide6.QtCore import QPointF
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(30, 30),
            QPointF(30, 30),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        # No config — should NOT emit
        widget.mouseDoubleClickEvent(event)
        assert received == []
        # With config — should emit
        widget.set_config(make_config())
        widget.mouseDoubleClickEvent(event)
        assert received == [3]

    def test_fixed_size(self, qapp):
        from trcc.legacy.ui.gui.constants import Sizes
        widget = OverlayElementWidget(0)
        assert widget.width() == Sizes.OVERLAY_CELL
        assert widget.height() == Sizes.OVERLAY_CELL


# ============================================================================
# OverlayGridPanel
# ============================================================================

class TestOverlayGridPanel:
    """Test the 7x6 overlay element grid."""

    def test_construction(self, qapp):
        panel = OverlayGridPanel()
        assert len(panel._cells) == 42

    def test_grid_dimensions(self, qapp):
        from trcc.legacy.ui.gui.constants import Sizes
        panel = OverlayGridPanel()
        assert panel.width() == Sizes.OVERLAY_GRID_W
        assert panel.height() == Sizes.OVERLAY_GRID_H

    def test_cells_are_42(self, qapp):
        panel = OverlayGridPanel()
        assert len(panel._cells) == 7 * 6

    def test_initial_selection_is_negative(self, qapp):
        panel = OverlayGridPanel()
        assert panel.get_selected_index() == -1

    def test_overlay_enabled_default(self, qapp):
        panel = OverlayGridPanel()
        assert panel.overlay_enabled is True

    def test_set_overlay_enabled(self, qapp):
        panel = OverlayGridPanel()
        panel.set_overlay_enabled(False)
        assert panel.overlay_enabled is False
        panel.set_overlay_enabled(True)
        assert panel.overlay_enabled is True

    def test_add_element(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config()
        panel.add_element(cfg)
        assert len(panel.get_all_configs()) == 1
        assert panel.get_selected_index() == 0

    def test_add_element_max(self, qapp, make_config):
        panel = OverlayGridPanel()
        for i in range(42):
            panel.add_element(make_config(x=i))
        assert len(panel.get_all_configs()) == 42
        # 43rd element should be rejected
        panel.add_element(make_config(x=99))
        assert len(panel.get_all_configs()) == 42

    def test_delete_element(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config(x=10))
        panel.add_element(make_config(x=20))
        panel.delete_element(0)
        configs = panel.get_all_configs()
        assert len(configs) == 1
        assert configs[0].x == 20

    def test_delete_element_signal(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config())
        deleted = []
        panel.element_deleted.connect(deleted.append)
        panel.delete_element(0)
        assert deleted == [0]

    def test_update_element(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(x=10)
        panel.add_element(cfg)
        new_cfg = replace(cfg, x=99)
        panel.update_element(0, new_cfg)
        assert panel.get_all_configs()[0].x == 99

    def test_clear_all(self, qapp, make_config):
        panel = OverlayGridPanel()
        for i in range(5):
            panel.add_element(make_config(x=i))
        panel.clear_all()
        assert len(panel.get_all_configs()) == 0
        assert panel.get_selected_index() == -1

    def test_load_configs(self, qapp, make_config):
        panel = OverlayGridPanel()
        configs = [make_config(x=i) for i in range(3)]
        panel.load_configs(configs)
        assert len(panel.get_all_configs()) == 3
        assert panel.get_selected_index() == -1

    def test_cell_click_selects(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config())
        received = []
        panel.element_selected.connect(lambda idx, cfg: received.append(idx))
        panel._on_cell_clicked(0)
        assert received == [0]
        assert panel.get_selected_index() == 0

    def test_cell_click_deselects_previous(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config(x=10))
        panel.add_element(make_config(x=20))
        panel._on_cell_clicked(0)
        assert panel._cells[0]._selected is True
        panel._on_cell_clicked(1)
        assert panel._cells[0]._selected is False
        assert panel._cells[1]._selected is True

    def test_cell_click_empty_next_slot_triggers_add(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config())
        received = []
        panel.add_requested.connect(lambda: received.append(True))
        # Click the slot right after the last config (index 1)
        panel._on_cell_clicked(1)
        assert received == [True]

    def test_to_overlay_config_empty(self, qapp):
        panel = OverlayGridPanel()
        assert panel.to_overlay_config() == {}

    def test_to_overlay_config_disabled(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config(mode=OverlayMode.TIME))
        panel.set_overlay_enabled(False)
        assert panel.to_overlay_config() == {}

    def test_to_overlay_config_hardware(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.HARDWARE, main_count=0, sub_count=1)
        panel.add_element(cfg)
        result = panel.to_overlay_config()
        assert len(result) == 1
        key = list(result.keys())[0]
        assert key.startswith("hw_")
        entry = result[key]
        assert entry["metric"] == "cpu_temp"
        assert entry["x"] == 50
        assert entry["y"] == 60

    def test_to_overlay_config_time(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.TIME)
        cfg.mode_sub = 1
        panel.add_element(cfg)
        result = panel.to_overlay_config()
        key = list(result.keys())[0]
        assert key.startswith("time_")
        assert result[key]["metric"] == "time"
        assert result[key]["time_format"] == 1

    def test_to_overlay_config_date(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.DATE)
        cfg.mode_sub = 2
        panel.add_element(cfg)
        result = panel.to_overlay_config()
        key = list(result.keys())[0]
        assert key.startswith("date_")
        assert result[key]["date_format"] == 2

    def test_to_overlay_config_weekday(self, qapp, make_config):
        panel = OverlayGridPanel()
        panel.add_element(make_config(mode=OverlayMode.WEEKDAY))
        result = panel.to_overlay_config()
        key = list(result.keys())[0]
        assert key.startswith("weekday_")

    def test_to_overlay_config_custom(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.CUSTOM)
        cfg.text = "Hello"
        panel.add_element(cfg)
        result = panel.to_overlay_config()
        key = list(result.keys())[0]
        assert key.startswith("custom_")
        assert result[key]["text"] == "Hello"

    def test_to_overlay_config_font(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.TIME)
        cfg.font_size = 48
        cfg.font_style = 1
        cfg.font_name = "Arial"
        panel.add_element(cfg)
        result = panel.to_overlay_config()
        entry = list(result.values())[0]
        assert entry["font"]["size"] == 48
        assert entry["font"]["style"] == "bold"
        assert entry["font"]["name"] == "Arial"

    def test_load_from_overlay_config_roundtrip(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(mode=OverlayMode.HARDWARE, main_count=1, sub_count=2)
        cfg.font_size = 24
        cfg.color = "#FF0000"
        panel.add_element(cfg)
        exported = panel.to_overlay_config()
        panel.clear_all()
        panel.load_from_overlay_config(exported)
        configs = panel.get_all_configs()
        assert len(configs) == 1
        assert configs[0].mode == OverlayMode.HARDWARE
        assert configs[0].main_count == 1
        assert configs[0].sub_count == 2
        assert configs[0].color == "#FF0000"

    def test_toggle_changed_signal(self, qapp):
        panel = OverlayGridPanel()
        received = []
        panel.toggle_changed.connect(received.append)
        panel._on_toggle(False)
        assert received == [False]
        assert panel.overlay_enabled is False

    def test_get_selected_config_with_selection(self, qapp, make_config):
        panel = OverlayGridPanel()
        cfg = make_config(x=77)
        panel.add_element(cfg)
        assert panel.get_selected_config() is not None
        assert panel.get_selected_config().x == 77

    def test_get_selected_config_without_selection(self, qapp):
        panel = OverlayGridPanel()
        assert panel.get_selected_config() is None


# ============================================================================
# ColorPickerPanel
# ============================================================================

class TestColorPickerPanel:
    """Test color picker construction and signal emission."""

    def test_construction(self, qapp):
        panel = ColorPickerPanel()
        assert panel._current_color.red() == 255
        assert panel._current_color.green() == 255
        assert panel._current_color.blue() == 255

    def test_set_color(self, qapp):
        panel = ColorPickerPanel()
        panel.set_color(100, 150, 200)
        assert panel._current_color.red() == 100
        assert panel._current_color.green() == 150
        assert panel._current_color.blue() == 200
        assert panel.r_input.text() == "100"
        assert panel.g_input.text() == "150"
        assert panel.b_input.text() == "200"

    def test_set_color_hex(self, qapp):
        panel = ColorPickerPanel()
        panel.set_color_hex("#FF8000")
        assert panel._current_color.red() == 255
        assert panel._current_color.green() == 128
        assert panel._current_color.blue() == 0

    def test_color_changed_signal(self, qapp):
        panel = ColorPickerPanel()
        received = []
        panel.color_changed.connect(lambda r, g, b: received.append((r, g, b)))
        panel._apply_color(10, 20, 30)
        assert received == [(10, 20, 30)]

    def test_set_position(self, qapp):
        panel = ColorPickerPanel()
        panel.set_position(123, 456)
        assert panel.x_spin.value() == 123
        assert panel.y_spin.value() == 456

    def test_position_changed_signal(self, qapp):
        panel = ColorPickerPanel()
        received = []
        panel.position_changed.connect(lambda x, y: received.append((x, y)))
        panel.x_spin.setValue(42)
        # Signal fires once per value change
        assert len(received) >= 1
        assert received[-1][0] == 42

    def test_font_display(self, qapp):
        panel = ColorPickerPanel()
        panel.set_font_display("Arial", 24, 1)
        assert panel._current_font_name == "Arial"
        assert panel._current_font_size == 24
        assert panel._current_font_style == 1
        assert panel.font_btn.text() == "Arial"
        assert panel.font_size_spin.value() == 24

    def test_font_size_changed_signal(self, qapp):
        panel = ColorPickerPanel()
        received = []
        panel.font_changed.connect(
            lambda name, size, style: received.append((name, size, style))
        )
        panel.font_size_spin.setValue(48)
        assert len(received) >= 1
        assert received[-1][1] == 48

    def test_rgb_input_validation(self, qapp):
        panel = ColorPickerPanel()
        received = []
        panel.color_changed.connect(lambda r, g, b: received.append((r, g, b)))
        panel.r_input.setText("128")
        panel.g_input.setText("64")
        panel.b_input.setText("32")
        panel._on_rgb_changed()
        assert received == [(128, 64, 32)]

    def test_eyedropper_signal(self, qapp):
        panel = ColorPickerPanel()
        received = []
        panel.eyedropper_requested.connect(lambda: received.append(True))
        panel.eyedropper_btn.click()
        assert received == [True]

    def test_x_spin_range(self, qapp):
        panel = ColorPickerPanel()
        assert panel.x_spin.minimum() == 0
        assert panel.x_spin.maximum() == 480

    def test_font_size_spin_range(self, qapp):
        panel = ColorPickerPanel()
        assert panel.font_size_spin.minimum() == 6
        assert panel.font_size_spin.maximum() == 200


# ============================================================================
# AddElementPanel
# ============================================================================

class TestAddElementPanel:
    """Test add element panel."""

    def test_construction(self, qapp):
        panel = AddElementPanel()
        assert len(panel.ELEMENT_TYPES) == 5

    def test_hardware_emits_hardware_requested(self, qapp):
        """Hardware Data button emits hardware_requested, not element_added."""
        panel = AddElementPanel()
        hw_received = []
        elem_received = []
        panel.hardware_requested.connect(lambda: hw_received.append(True))
        panel.element_added.connect(elem_received.append)
        panel._on_type_clicked(OverlayMode.HARDWARE)
        assert len(hw_received) == 1
        assert len(elem_received) == 0  # no element_added for hardware

    def test_element_added_signal_time(self, qapp):
        panel = AddElementPanel()
        received = []
        panel.element_added.connect(received.append)
        panel._on_type_clicked(OverlayMode.TIME)
        assert len(received) == 1
        assert received[0].mode == OverlayMode.TIME

    def test_element_added_signal_weekday(self, qapp):
        panel = AddElementPanel()
        received = []
        panel.element_added.connect(received.append)
        panel._on_type_clicked(OverlayMode.WEEKDAY)
        assert len(received) == 1
        assert received[0].mode == OverlayMode.WEEKDAY

    def test_element_added_signal_date(self, qapp):
        panel = AddElementPanel()
        received = []
        panel.element_added.connect(received.append)
        panel._on_type_clicked(OverlayMode.DATE)
        assert len(received) == 1
        assert received[0].mode == OverlayMode.DATE

    def test_element_added_signal_custom(self, qapp):
        panel = AddElementPanel()
        received = []
        panel.element_added.connect(received.append)
        panel._on_type_clicked(OverlayMode.CUSTOM)
        assert len(received) == 1
        assert received[0].mode == OverlayMode.CUSTOM

    def test_non_hardware_does_not_emit_hardware_requested(self, qapp):
        """Time/Date/Weekday/Custom should not trigger hardware sidebar."""
        panel = AddElementPanel()
        hw_received = []
        panel.hardware_requested.connect(lambda: hw_received.append(True))
        for mode in (OverlayMode.TIME, OverlayMode.DATE,
                     OverlayMode.WEEKDAY, OverlayMode.CUSTOM):
            panel._on_type_clicked(mode)
        assert len(hw_received) == 0

    def test_element_types_count(self, qapp):
        assert len(AddElementPanel.ELEMENT_TYPES) == 5


# ============================================================================
# DataTablePanel
# ============================================================================

class TestDataTablePanel:
    """Test data table panel mode switching and format cycling."""

    def test_construction(self, qapp):
        panel = DataTablePanel()
        assert panel._current_mode == -1

    def test_set_mode_hardware(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.HARDWARE, 0)
        assert panel.unit_btn.isHidden() is False
        assert panel.time_btn.isHidden() is True
        assert panel.date_btn.isHidden() is True
        assert panel.text_input.isHidden() is True

    def test_set_mode_time(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.TIME, 0)
        assert panel.unit_btn.isHidden() is True
        assert panel.time_btn.isHidden() is False

    def test_set_mode_weekday(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.WEEKDAY, 0)
        assert panel.unit_btn.isHidden() is True
        assert panel.time_btn.isHidden() is True
        assert panel.date_btn.isHidden() is True
        assert panel.text_input.isHidden() is True

    def test_set_mode_date(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.DATE, 0)
        assert panel.date_btn.isHidden() is False
        # default sub should be 1 when passed 0
        assert panel._mode_sub == 1

    def test_set_mode_custom(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.CUSTOM, 0)
        assert panel.text_input.isHidden() is False

    def test_unit_toggle(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.HARDWARE, 0)
        received = []
        panel.format_changed.connect(lambda m, s: received.append((m, s)))
        panel._on_unit_clicked()
        assert panel._mode_sub == 1
        panel._on_unit_clicked()
        assert panel._mode_sub == 0
        assert len(received) == 2

    def test_time_toggle(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.TIME, 0)
        received = []
        panel.format_changed.connect(lambda m, s: received.append((m, s)))
        panel._on_time_clicked()
        # Default 0 -> toggle should give 1
        assert panel._mode_sub == 1
        panel._on_time_clicked()
        assert panel._mode_sub == 2
        panel._on_time_clicked()
        assert panel._mode_sub == 1

    def test_date_cycle(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.DATE, 1)
        received = []
        panel.format_changed.connect(lambda m, s: received.append((m, s)))
        # Cycle: 1->2->3->4->1
        panel._on_date_clicked()
        assert panel._mode_sub == 2
        panel._on_date_clicked()
        assert panel._mode_sub == 3
        panel._on_date_clicked()
        assert panel._mode_sub == 4
        panel._on_date_clicked()
        assert panel._mode_sub == 1
        assert len(received) == 4

    def test_text_changed_signal(self, qapp):
        panel = DataTablePanel()
        panel.set_mode(OverlayMode.CUSTOM)
        received = []
        panel.text_changed.connect(received.append)
        panel.text_input.setText("Hello World")
        panel.text_input.editingFinished.emit()
        assert received == ["Hello World"]


# ============================================================================
# DisplayModePanel
# ============================================================================

class TestDisplayModePanel:
    """Test display mode toggle panel."""

    def test_construction(self, qapp):
        panel = DisplayModePanel("background", ["Image", "Video"])
        assert panel.mode_id == "background"
        assert len(panel._action_buttons) == 2

    def test_toggle_emits_signal(self, qapp):
        panel = DisplayModePanel("mask", ["Load", "Clear"])
        received = []
        panel.mode_changed.connect(lambda mode, enabled: received.append((mode, enabled)))
        panel._on_toggle(True)
        assert received == [("mask", True)]

    def test_actions_disabled_initially(self, qapp):
        panel = DisplayModePanel("background", ["Image", "Video"])
        for btn in panel._action_buttons:
            assert btn.isEnabled() is False

    def test_toggle_enables_actions(self, qapp):
        panel = DisplayModePanel("background", ["Image", "Video"])
        panel._on_toggle(True)
        for btn in panel._action_buttons:
            assert btn.isEnabled() is True

    def test_toggle_disables_actions(self, qapp):
        panel = DisplayModePanel("background", ["Image", "Video"])
        panel._on_toggle(True)
        panel._on_toggle(False)
        for btn in panel._action_buttons:
            assert btn.isEnabled() is False

    def test_set_enabled(self, qapp):
        panel = DisplayModePanel("video", ["VideoLoad"])
        panel.set_enabled(True)
        assert panel.toggle_btn.isChecked() is True
        assert panel._action_buttons[0].isEnabled() is True

    def test_action_requested_signal(self, qapp):
        panel = DisplayModePanel("background", ["Image", "Video"])
        received = []
        panel.action_requested.connect(received.append)
        panel._on_toggle(True)
        panel._action_buttons[0].click()
        assert received == ["Image"]

    def test_no_actions(self, qapp):
        panel = DisplayModePanel("test_mode", [])
        assert len(panel._action_buttons) == 0

    def test_mask_uses_slider_toggle(self, qapp):
        panel = DisplayModePanel("mask", ["Load"])
        # Mask panel uses TOGGLE_MASK geometry
        from trcc.legacy.ui.gui.constants import Layout
        geo = panel.toggle_btn.geometry()
        assert geo.x() == Layout.TOGGLE_MASK[0]
        assert geo.y() == Layout.TOGGLE_MASK[1]


# ============================================================================
# ScreenCastPanel
# ============================================================================

class TestScreenCastPanel:
    """Test screen cast panel with coordinate inputs."""

    @pytest.fixture(autouse=True)
    def _with_resolution(self, settings_with_resolution):
        pass

    def test_construction(self, qapp):
        panel = ScreenCastPanel()
        assert panel.mode_id == "screencast"
        assert panel._resolution == (0, 0)

    def test_set_values(self, qapp):
        panel = ScreenCastPanel()
        panel.set_values(x=100, y=200, w=300, h=400)
        assert panel.entry_x.text() == "100"
        assert panel.entry_y.text() == "200"
        assert panel.entry_w.text() == "300"
        assert panel.entry_h.text() == "400"

    def test_set_resolution(self, qapp):
        panel = ScreenCastPanel()
        panel.set_resolution(640, 480)
        assert panel._resolution == (640, 480)

    def test_border_toggle(self, qapp):
        panel = ScreenCastPanel()
        received = []
        panel.border_toggled.connect(received.append)
        panel._on_border_toggle()
        assert panel._show_border is False
        assert received == [False]


# ============================================================================
# UCThemeSetting (main container)
# ============================================================================

class TestUCThemeSetting:
    """Test the main settings container."""

    def test_construction(self, qapp):
        settings = UCThemeSetting()
        assert settings.overlay_grid is not None
        assert settings.color_panel is not None
        assert settings.add_panel is not None
        assert settings.data_table is not None
        assert settings.mask_panel is not None
        assert settings.background_panel is not None
        assert settings.screencast_panel is not None
        assert settings.video_panel is not None

    def test_fixed_size(self, qapp):
        from trcc.legacy.ui.gui.constants import Sizes
        settings = UCThemeSetting()
        assert settings.width() == Sizes.SETTING_W
        assert settings.height() == Sizes.SETTING_H

    def test_right_stack_starts_with_color_panel(self, qapp):
        settings = UCThemeSetting()
        assert settings.right_stack.currentWidget() is settings.color_panel

    def test_add_requested_shows_add_panel(self, qapp):
        settings = UCThemeSetting()
        settings._on_add_requested()
        assert settings.right_stack.currentWidget() is settings.add_panel

    def test_element_selected_shows_color_panel(self, qapp, make_config):
        settings = UCThemeSetting()
        settings._on_add_requested()  # switch to add panel first
        cfg = make_config()
        settings._on_element_selected(0, cfg)
        assert settings.right_stack.currentWidget() is settings.color_panel

    def test_delegate_on_elements_changed(self, qapp, make_config):
        settings = UCThemeSetting()
        received = []
        settings.delegate.connect(lambda cmd, info, data: received.append(cmd))
        settings.overlay_grid.add_element(make_config())
        assert UCThemeSetting.CMD_OVERLAY_CHANGED in received

    def test_background_mode_disables_others(self, qapp):
        settings = UCThemeSetting()
        settings._on_mode_changed("background", True)
        assert settings.screencast_panel.toggle_btn.isChecked() is False
        assert settings.video_panel.toggle_btn.isChecked() is False

    def test_screencast_mode_disables_others(self, qapp):
        settings = UCThemeSetting()
        settings._on_mode_changed("screencast", True)
        assert settings.background_panel.toggle_btn.isChecked() is False
        assert settings.video_panel.toggle_btn.isChecked() is False

    def test_video_mode_disables_others(self, qapp):
        settings = UCThemeSetting()
        settings._on_mode_changed("video", True)
        assert settings.background_panel.toggle_btn.isChecked() is False
        assert settings.screencast_panel.toggle_btn.isChecked() is False

    def test_load_configs_delegates(self, qapp, make_config):
        settings = UCThemeSetting()
        configs = [make_config(x=i) for i in range(3)]
        settings.load_configs(configs)
        assert len(settings.get_all_configs()) == 3

    def test_to_overlay_config_delegates(self, qapp, make_config):
        settings = UCThemeSetting()
        settings.overlay_grid.add_element(make_config(mode=OverlayMode.TIME))
        result = settings.to_overlay_config()
        assert len(result) == 1

    def test_set_overlay_enabled_delegates(self, qapp):
        settings = UCThemeSetting()
        settings.set_overlay_enabled(False)
        assert settings.overlay_grid.overlay_enabled is False

    def test_set_resolution_delegates(self, qapp):
        settings = UCThemeSetting()
        settings.set_resolution(640, 480)
        assert settings.screencast_panel._resolution == (640, 480)

    def test_action_requested_routing(self, qapp):
        settings = UCThemeSetting()
        received = []
        settings.delegate.connect(lambda cmd, info, data: received.append(cmd))
        settings._on_action_requested("Image")
        assert UCThemeSetting.CMD_BACKGROUND_LOAD_IMAGE in received

    def test_action_requested_video_load(self, qapp):
        settings = UCThemeSetting()
        received = []
        settings.delegate.connect(lambda cmd, info, data: received.append(cmd))
        settings._on_action_requested("VideoLoad")
        assert UCThemeSetting.CMD_VIDEO_LOAD in received

    def test_action_requested_mask_load(self, qapp):
        settings = UCThemeSetting()
        received = []
        settings.delegate.connect(lambda cmd, info, data: received.append(cmd))
        settings._on_action_requested("Load")
        assert UCThemeSetting.CMD_MASK_LOAD in received

    @patch("trcc.legacy.conf.Settings")
    def test_format_changed_persists_time(self, mock_settings, qapp, make_config):
        settings = UCThemeSetting()
        cfg = make_config(mode=OverlayMode.TIME)
        settings.overlay_grid.add_element(cfg)
        settings.overlay_grid._on_cell_clicked(0)
        settings._on_format_changed(OverlayMode.TIME, 1)
        mock_settings.save_format_pref.assert_called_with("time_format", 1)


# ============================================================================
# ThemeThumbnail
# ============================================================================

class TestThemeThumbnail:
    """Test local theme thumbnail widget."""

    def test_construction(self, qapp, make_local_item):
        item = make_local_item()
        thumb = ThemeThumbnail(item)
        assert thumb.item_info is item
        assert thumb._slideshow_mode is False
        assert thumb._delete_btn is None
        assert thumb._badge_label is None

    def test_set_deletable_creates_button(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_deletable(True)
        assert thumb._delete_btn is not None
        assert thumb._delete_btn.isHidden() is False

    def test_set_deletable_removes_button(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_deletable(True)
        thumb.set_deletable(False)
        assert thumb._delete_btn is None

    def test_set_deletable_idempotent(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_deletable(True)
        btn1 = thumb._delete_btn
        thumb.set_deletable(True)  # No-op: already has button
        assert thumb._delete_btn is btn1

    def test_slideshow_badge_show(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_slideshow_badge(3)
        assert thumb._badge_label is not None
        assert thumb._badge_label.text() == "3"

    def test_slideshow_badge_unselected(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_slideshow_badge(0)
        assert thumb._badge_label is not None
        assert thumb._badge_label.text() == ""

    def test_clear_slideshow_badge(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_slideshow_badge(1)
        thumb.clear_slideshow_badge()
        assert thumb._badge_label is None

    def test_slideshow_mode(self, qapp, make_local_item):
        thumb = ThemeThumbnail(make_local_item())
        thumb.set_slideshow_mode(True)
        assert thumb._slideshow_mode is True

    def test_click_normal_mode(self, qapp, make_local_item):
        item = make_local_item()
        thumb = ThemeThumbnail(item)
        received = []
        thumb.clicked.connect(received.append)
        from PySide6.QtCore import QPointF
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(60, 100),  # lower half
            QPointF(60, 100),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        thumb.mousePressEvent(event)
        assert len(received) == 1
        assert received[0] is item

    def test_click_slideshow_mode_lower_half(self, qapp, make_local_item):
        item = make_local_item()
        thumb = ThemeThumbnail(item)
        thumb.set_slideshow_mode(True)
        slideshow_received = []
        click_received = []
        thumb.slideshow_toggled.connect(slideshow_received.append)
        thumb.clicked.connect(click_received.append)
        from PySide6.QtCore import QPointF
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(60, 100),  # y > 60 -> slideshow toggle
            QPointF(60, 100),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        thumb.mousePressEvent(event)
        assert len(slideshow_received) == 1
        assert slideshow_received[0] is item
        assert click_received == []

    def test_click_slideshow_mode_upper_half(self, qapp, make_local_item):
        item = make_local_item()
        thumb = ThemeThumbnail(item)
        thumb.set_slideshow_mode(True)
        click_received = []
        thumb.clicked.connect(click_received.append)
        from PySide6.QtCore import QPointF
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(60, 30),  # y <= 60 -> normal click
            QPointF(60, 30),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        thumb.mousePressEvent(event)
        assert len(click_received) == 1

    def test_delete_clicked_signal(self, qapp, make_local_item):
        item = make_local_item()
        thumb = ThemeThumbnail(item)
        thumb.set_deletable(True)
        received = []
        thumb.delete_clicked.connect(received.append)
        thumb._delete_btn.click()
        assert len(received) == 1
        assert received[0] is item


# ============================================================================
# UCThemeLocal
# ============================================================================

class TestUCThemeLocal:
    """Test local theme browser panel."""

    def test_construction(self, qapp):
        panel = UCThemeLocal()
        assert panel.filter_mode == UCThemeLocal.MODE_ALL
        assert panel.theme_directory is None
        assert panel._slideshow is False
        assert panel._slideshow_interval == 3

    def test_mode_constants(self, qapp):
        assert UCThemeLocal.MODE_ALL == 0
        assert UCThemeLocal.MODE_DEFAULT == 1
        assert UCThemeLocal.MODE_USER == 2
        assert UCThemeLocal.MAX_SLIDESHOW == 6

    def test_set_filter_all(self, qapp):
        panel = UCThemeLocal()
        panel._set_filter(UCThemeLocal.MODE_ALL)
        assert panel.filter_mode == UCThemeLocal.MODE_ALL

    def test_set_filter_default(self, qapp):
        panel = UCThemeLocal()
        panel._set_filter(UCThemeLocal.MODE_DEFAULT)
        assert panel.filter_mode == UCThemeLocal.MODE_DEFAULT

    def test_set_filter_user(self, qapp):
        panel = UCThemeLocal()
        panel._set_filter(UCThemeLocal.MODE_USER)
        assert panel.filter_mode == UCThemeLocal.MODE_USER

    def test_load_themes_no_directory(self, qapp):
        panel = UCThemeLocal()
        panel.load_themes()
        assert panel._all_themes == []

    def test_load_themes_with_directory(self, qapp, tmp_path):
        from tests.conftest import make_test_surface
        # Create real theme directories with real PNG thumbnails
        theme_dir = tmp_path / "themes"
        theme_dir.mkdir()
        for name in ["Default01", "Default02", "User01"]:
            td = theme_dir / name
            td.mkdir()
            make_test_surface(10, 10, (0, 0, 0)).save(str(td / "Theme.png"))

        panel = UCThemeLocal()
        panel.theme_directory = theme_dir
        panel.load_themes()
        # Default themes sort before user themes (is_user=False first)
        assert len(panel._all_themes) == 3
        assert panel._all_themes[0].name == "Default01"
        assert panel._all_themes[0].is_user is False
        assert panel._all_themes[2].name == "User01"
        assert panel._all_themes[2].is_user is True

    def test_slideshow_toggle(self, qapp):
        panel = UCThemeLocal()
        assert panel.is_slideshow() is False
        panel._on_slideshow_clicked()
        assert panel.is_slideshow() is True
        panel._on_slideshow_clicked()
        assert panel.is_slideshow() is False

    def test_slideshow_add_remove(self, qapp, make_local_item):
        panel = UCThemeLocal()
        panel._all_themes = [
            make_local_item(f"Theme{i}", f"/tmp/themes/Theme{i}", index=i)
            for i in range(8)
        ]
        # Add themes to slideshow
        panel._on_slideshow_toggled(panel._all_themes[0])
        assert len(panel._lunbo_array) == 1
        panel._on_slideshow_toggled(panel._all_themes[1])
        assert len(panel._lunbo_array) == 2
        # Remove by toggling again
        panel._on_slideshow_toggled(panel._all_themes[0])
        assert len(panel._lunbo_array) == 1
        assert panel._lunbo_array[0] == "Theme1"

    def test_slideshow_max_six(self, qapp, make_local_item):
        panel = UCThemeLocal()
        panel._all_themes = [
            make_local_item(f"Theme{i}", f"/tmp/themes/Theme{i}", index=i)
            for i in range(8)
        ]
        for i in range(7):
            panel._on_slideshow_toggled(panel._all_themes[i])
        # Only first 6 should be added
        assert len(panel._lunbo_array) == 6

    def test_get_slideshow_themes(self, qapp, make_local_item):
        panel = UCThemeLocal()
        items = [
            make_local_item(f"Theme{i}", f"/tmp/themes/Theme{i}", index=i)
            for i in range(3)
        ]
        panel._all_themes = items
        panel._lunbo_array = ["Theme2", "Theme0"]
        result = panel.get_slideshow_themes()
        assert len(result) == 2
        assert result[0].name == "Theme2"
        assert result[1].name == "Theme0"

    def test_slideshow_interval_default(self, qapp):
        panel = UCThemeLocal()
        assert panel.get_slideshow_interval() == 3

    def test_timer_changed_min_three(self, qapp):
        panel = UCThemeLocal()
        panel.timer_input.setText("1")
        panel._on_timer_changed()
        assert panel._slideshow_interval == 3
        assert panel.timer_input.text() == "3"

    def test_timer_changed_valid(self, qapp):
        panel = UCThemeLocal()
        panel.timer_input.setText("10")
        panel._on_timer_changed()
        assert panel._slideshow_interval == 10

    def test_timer_changed_invalid(self, qapp):
        panel = UCThemeLocal()
        panel.timer_input.setText("abc")
        panel._on_timer_changed()
        assert panel._slideshow_interval == 3

    @patch("trcc.legacy.ui.gui.uc_theme_local.shutil.rmtree")
    def test_delete_theme(self, mock_rmtree, qapp, tmp_path, make_local_item):
        theme_dir = tmp_path / "themes"
        theme_dir.mkdir()
        td = theme_dir / "UserTheme"
        td.mkdir()
        (td / "Theme.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 100)

        panel = UCThemeLocal()
        panel.theme_directory = theme_dir

        item = make_local_item("UserTheme", str(td), is_user=True)
        panel._lunbo_array = ["UserTheme"]
        panel.delete_theme(item)
        mock_rmtree.assert_called_once_with(td)
        assert "UserTheme" not in panel._lunbo_array

    def test_delete_theme_removes_from_slideshow(
        self, qapp, tmp_path, make_local_item,
    ):
        panel = UCThemeLocal()
        panel.theme_directory = tmp_path / "themes"
        panel._lunbo_array = ["Theme1", "Theme2", "Theme3"]
        item = make_local_item("Theme2", str(tmp_path / "nonexistent"))
        # path doesn't exist, so rmtree won't be called but slideshow should be cleaned
        panel.delete_theme(item)
        assert "Theme2" not in panel._lunbo_array
        assert panel._lunbo_array == ["Theme1", "Theme3"]

    def test_get_selected_theme(self, qapp):
        panel = UCThemeLocal()
        assert panel.get_selected_theme() is None

    def test_delete_requested_signal(self, qapp, make_local_item):
        panel = UCThemeLocal()
        received = []
        panel.delete_requested.connect(received.append)
        item = make_local_item()
        panel._on_delete_clicked(item)
        assert len(received) == 1
