"""Tests for Qt LED GUI components: UCColorWheel, UCScreenLED, UCLedControl.

Covers construction, state management, signal emission, position arrays,
and metrics dispatch. All painting tests are skipped — we test logic only.
"""
from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPixmap

from trcc.legacy.ui.gui.uc_color_wheel import UCColorWheel
from trcc.legacy.ui.gui.uc_led_control import (
    BRIGHT_W,
    BRIGHT_X,
    MODE_H,
    MODE_LABELS,
    MODE_W,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    PRESET_COLORS,
    UCInfoImage,
    UCLedControl,
)
from trcc.legacy.ui.gui.uc_screen_led import (
    _DECO,
    _POS_1,
    _POS_2,
    _POS_3,
    _POS_4,
    _POS_5,
    _POS_6,
    _POS_7,
    _POS_8,
    _POS_9,
    _POS_10,
    _POS_11,
    _POS_12,
    STYLE_POSITIONS,
    UCScreenLED,
)

# Expected LED counts per style (from C# UCScreenLED.cs)
_EXPECTED_COUNTS: dict[int, int] = {
    1: 30, 2: 84, 3: 64, 4: 31, 5: 93, 6: 93,
    7: 104, 8: 18, 9: 61, 10: 38, 11: 93, 12: 1,
}

ALL_POS = {
    1: _POS_1, 2: _POS_2, 3: _POS_3, 4: _POS_4,
    5: _POS_5, 6: _POS_6, 7: _POS_7, 8: _POS_8,
    9: _POS_9, 10: _POS_10, 11: _POS_11, 12: _POS_12,
}


# =========================================================================
# Fixtures
# =========================================================================

def _asset_mocks() -> dict:
    """Build fresh Asset mock kwargs — must be called after QApplication exists."""
    return {
        "get": MagicMock(return_value=None),
        "exists": MagicMock(return_value=False),
        "load_pixmap": MagicMock(return_value=QPixmap()),
        "get_localized": MagicMock(return_value="fake"),
    }


@pytest.fixture(autouse=True)
def _patch_led_assets(qapp):
    """Patch Assets in all LED-related modules to avoid filesystem I/O."""
    defaults = _asset_mocks()
    with (
        patch.multiple("trcc.legacy.ui.gui.uc_color_wheel.Assets", **defaults),
        patch.multiple("trcc.legacy.ui.gui.uc_screen_led.Assets", **defaults),
        patch.multiple("trcc.legacy.ui.gui.uc_led_control.Assets", **defaults),
        patch("trcc.legacy.ui.gui.uc_led_control.set_background_pixmap"),
    ):
        yield


@pytest.fixture
def color_wheel(qapp):
    """Pre-constructed UCColorWheel."""
    return UCColorWheel()


@pytest.fixture
def screen_led(qapp):
    """Pre-constructed UCScreenLED."""
    return UCScreenLED()


@pytest.fixture
def info_image(qapp):
    """Pre-constructed UCInfoImage."""
    return UCInfoImage(1)


@pytest.fixture
def led_control(qapp):
    """Pre-constructed UCLedControl."""
    return UCLedControl()


# =========================================================================
# UCColorWheel tests
# =========================================================================

class TestColorWheelConstruction:
    """UCColorWheel default state."""

    def test_default_hue(self, color_wheel):
        assert color_wheel._hue == 0

    def test_default_onoff(self, color_wheel):
        assert color_wheel._onoff == 1

    def test_dragging_starts_false(self, color_wheel):
        assert color_wheel._dragging is False

    def test_onoff_button_exists(self, color_wheel):
        assert color_wheel._onoff_btn is not None
        assert color_wheel._onoff_btn.isFlat()

    def test_fallback_style_when_no_assets(self, color_wheel):
        """Without image assets, button gets power symbol text."""
        assert color_wheel._onoff_btn.text() == "\u23fb"


class TestColorWheelSetHue:
    """set_hue() updates internal state without emitting signals."""

    def test_set_hue_basic(self, color_wheel):
        color_wheel.set_hue(120)
        assert color_wheel._hue == 120

    def test_set_hue_wraps_at_360(self, color_wheel):
        color_wheel.set_hue(400)
        assert color_wheel._hue == 40

    def test_set_hue_negative_wraps(self, color_wheel):
        color_wheel.set_hue(-10)
        assert color_wheel._hue == 350

    def test_set_hue_zero(self, color_wheel):
        color_wheel.set_hue(90)
        color_wheel.set_hue(0)
        assert color_wheel._hue == 0

    def test_set_hue_no_signal(self, color_wheel):
        """set_hue should NOT emit hue_changed."""
        received = []
        color_wheel.hue_changed.connect(lambda h: received.append(h))
        color_wheel.set_hue(180)
        assert len(received) == 0


class TestColorWheelSetOnoff:
    """set_onoff() updates state without emitting signals."""

    def test_set_onoff_off(self, color_wheel):
        color_wheel.set_onoff(0)
        assert color_wheel._onoff == 0

    def test_set_onoff_on(self, color_wheel):
        color_wheel.set_onoff(0)
        color_wheel.set_onoff(1)
        assert color_wheel._onoff == 1

    def test_set_onoff_no_signal(self, color_wheel):
        """set_onoff should NOT emit onoff_changed."""
        received = []
        color_wheel.onoff_changed.connect(lambda v: received.append(v))
        color_wheel.set_onoff(0)
        assert len(received) == 0


class TestColorWheelIsOnRing:
    """_is_on_ring() distance check."""

    def test_center_is_not_ring(self, color_wheel):
        """Center point (below MIN_RING_R) should be False."""
        color_wheel.resize(216, 216)
        cx, cy = color_wheel.width() / 2.0, color_wheel.height() / 2.0
        assert not color_wheel._is_on_ring(QPointF(cx, cy))

    def test_on_ring_true(self, color_wheel):
        """Point at mid-ring distance should be True."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel._MIN_RING_R + color_wheel._MAX_RING_R) / 2.0
        assert color_wheel._is_on_ring(QPointF(cx + mid_r, cy))

    def test_outside_ring_false(self, color_wheel):
        """Point far outside MAX_RING_R should be False."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        assert not color_wheel._is_on_ring(QPointF(cx + 200, cy))

    def test_just_inside_min_boundary(self, color_wheel):
        """Point at exactly MIN_RING_R should be True."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        assert color_wheel._is_on_ring(QPointF(cx + color_wheel._MIN_RING_R, cy))

    def test_just_outside_max_boundary(self, color_wheel):
        """Point just beyond MAX_RING_R should be False."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        assert not color_wheel._is_on_ring(
            QPointF(cx + color_wheel._MAX_RING_R + 1, cy)
        )


class TestColorWheelUpdateHue:
    """_update_hue_from_pos() converts position to hue."""

    def test_top_of_ring_near_zero(self, color_wheel):
        """Top of ring (12 o'clock) -> hue near 0/360 (Red)."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        # Top = (cx, cy - mid_r)
        color_wheel._update_hue_from_pos(QPointF(cx, cy - mid_r))
        # Hue should be very close to 0 (or 360 wrapping)
        assert color_wheel._hue <= 5 or color_wheel._hue >= 355

    def test_right_of_ring(self, color_wheel):
        """Right of ring (3 o'clock) -> hue ~270."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        color_wheel._update_hue_from_pos(QPointF(cx + mid_r, cy))
        assert abs(color_wheel._hue - 270) <= 5

    def test_bottom_of_ring(self, color_wheel):
        """Bottom of ring (6 o'clock) -> hue ~180."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        color_wheel._update_hue_from_pos(QPointF(cx, cy + mid_r))
        assert abs(color_wheel._hue - 180) <= 5

    def test_left_of_ring(self, color_wheel):
        """Left of ring (9 o'clock) -> hue ~90."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        color_wheel._update_hue_from_pos(QPointF(cx - mid_r, cy))
        assert abs(color_wheel._hue - 90) <= 5

    def test_emits_hue_changed(self, color_wheel):
        """_update_hue_from_pos should emit hue_changed signal."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        received: list[int] = []
        color_wheel.hue_changed.connect(lambda h: received.append(h))
        color_wheel._update_hue_from_pos(QPointF(cx + mid_r, cy))
        assert len(received) == 1

    def test_no_emit_on_same_hue(self, color_wheel):
        """_update_hue_from_pos should not emit if hue hasn't changed."""
        color_wheel.resize(216, 216)
        cx = color_wheel.width() / 2.0
        cy = color_wheel.height() / 2.0
        mid_r = (color_wheel.OUTER_RADIUS + color_wheel.INNER_RADIUS) / 2.0
        # Set hue to 270 first (right of ring)
        color_wheel._update_hue_from_pos(QPointF(cx + mid_r, cy))
        received: list[int] = []
        color_wheel.hue_changed.connect(lambda h: received.append(h))
        # Same position again — same hue, no emit
        color_wheel._update_hue_from_pos(QPointF(cx + mid_r, cy))
        assert len(received) == 0


class TestColorWheelToggle:
    """_toggle_onoff() toggles state and emits signal."""

    def test_toggle_from_on_to_off(self, color_wheel):
        assert color_wheel._onoff == 1
        received: list[int] = []
        color_wheel.onoff_changed.connect(lambda v: received.append(v))
        color_wheel._toggle_onoff()
        assert color_wheel._onoff == 0
        assert received == [0]

    def test_toggle_from_off_to_on(self, color_wheel):
        color_wheel._onoff = 0
        received: list[int] = []
        color_wheel.onoff_changed.connect(lambda v: received.append(v))
        color_wheel._toggle_onoff()
        assert color_wheel._onoff == 1
        assert received == [1]

    def test_double_toggle_returns_original(self, color_wheel):
        color_wheel._toggle_onoff()
        color_wheel._toggle_onoff()
        assert color_wheel._onoff == 1


class TestColorWheelUpdateOnoffImage:
    """_update_onoff_image() fallback path without assets."""

    def test_off_state_fallback_style(self, color_wheel):
        """Without assets, OFF state sets color #666."""
        color_wheel._onoff = 0
        color_wheel._update_onoff_image()
        style = color_wheel._onoff_btn.styleSheet()
        assert "#666" in style

    def test_on_state_fallback_style(self, color_wheel):
        """Without assets, ON state sets color #0ff."""
        color_wheel._onoff = 1
        color_wheel._update_onoff_image()
        style = color_wheel._onoff_btn.styleSheet()
        assert "#0ff" in style


# =========================================================================
# UCScreenLED — position array tests
# =========================================================================

class TestScreenLEDPositionArrays:
    """All 12 style position arrays are well-formed."""

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_correct_led_count(self, style_id):
        """Each position array has the expected number of entries."""
        positions = ALL_POS[style_id]
        assert len(positions) == _EXPECTED_COUNTS[style_id]

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_tuple_format(self, style_id):
        """Every entry is a 4-tuple of ints."""
        for pos in ALL_POS[style_id]:
            assert len(pos) == 4
            assert all(isinstance(v, int) for v in pos)

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_non_negative_coordinates(self, style_id):
        """All x, y, w, h values are >= 0."""
        for x, y, w, h in ALL_POS[style_id]:
            assert x >= 0 and y >= 0 and w >= 0 and h >= 0

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_fits_within_460x460(self, style_id):
        """All LED rects fit within the 460x460 widget."""
        for x, y, w, h in ALL_POS[style_id]:
            assert x + w <= 460, f"Style {style_id}: x+w={x + w} > 460"
            assert y + h <= 460, f"Style {style_id}: y+h={y + h} > 460"

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_positive_dimensions(self, style_id):
        """All w and h are > 0 (no zero-size rects)."""
        for x, y, w, h in ALL_POS[style_id]:
            assert w > 0 and h > 0

    def test_style_positions_dict_complete(self):
        """STYLE_POSITIONS covers all 12 styles."""
        for sid in range(1, 13):
            assert sid in STYLE_POSITIONS


# =========================================================================
# UCScreenLED — widget tests
# =========================================================================

class TestScreenLEDWidget:
    """UCScreenLED construction and public API."""

    def test_default_size(self, screen_led):
        assert screen_led.width() == 460
        assert screen_led.height() == 460

    def test_default_style(self, screen_led):
        assert screen_led._style_id == 1
        assert len(screen_led._positions) == 30

    def test_set_style_changes_positions(self, screen_led):
        screen_led.set_style(2, 84)
        assert screen_led._style_id == 2
        assert len(screen_led._positions) == 84

    @pytest.mark.parametrize("style_id", range(1, 13))
    def test_set_style_for_all_styles(self, screen_led, style_id):
        count = _EXPECTED_COUNTS[style_id]
        screen_led.set_style(style_id, count)
        assert screen_led._style_id == style_id
        assert screen_led._led_count == count

    def test_set_colors_basic(self, screen_led):
        colors = [(255, 0, 0)] * 30
        screen_led.set_colors(colors)
        assert screen_led._colors[0] == (255, 0, 0)
        assert len(screen_led._colors) == 30

    def test_set_colors_pads_short_list(self, screen_led):
        screen_led.set_colors([(100, 200, 50)] * 5)
        assert len(screen_led._colors) == 30
        assert screen_led._colors[5] == (0, 0, 0)  # padded

    def test_set_colors_truncates_long_list(self, screen_led):
        screen_led.set_colors([(1, 2, 3)] * 100)
        assert len(screen_led._colors) == 30

    def test_set_segment_on_toggle(self, screen_led):
        assert screen_led._is_on[0] is True
        screen_led.set_segment_on(0, False)
        assert screen_led._is_on[0] is False
        screen_led.set_segment_on(0, True)
        assert screen_led._is_on[0] is True

    def test_set_segment_on_out_of_range(self, screen_led):
        """Out of range index should not crash."""
        screen_led.set_segment_on(999, False)  # no crash
        screen_led.set_segment_on(-1, False)  # no crash

    def test_set_overlay(self, screen_led):
        pm = QPixmap(460, 460)
        screen_led.set_overlay(pm)
        assert screen_led._overlay is pm

    def test_set_overlay_none(self, screen_led):
        screen_led.set_overlay(QPixmap(460, 460))
        screen_led.set_overlay(None)
        assert screen_led._overlay is None

    def test_set_background_alias(self, screen_led):
        """set_background is an alias for set_overlay."""
        pm = QPixmap(460, 460)
        screen_led.set_background(pm)
        assert screen_led._overlay is pm

    def test_set_led_mode(self, screen_led):
        screen_led.set_led_mode(4)
        assert screen_led._led_mode == 4

    def test_segment_clicked_signal(self, screen_led):
        """segment_clicked signal is defined on the widget."""
        received: list[int] = []
        screen_led.segment_clicked.connect(lambda i: received.append(i))
        screen_led.segment_clicked.emit(5)
        assert received == [5]


# =========================================================================
# UCScreenLED — decoration config tests
# =========================================================================

class TestScreenLEDDecorations:
    """Decoration config data is well-formed."""

    def test_deco_styles_exist(self):
        assert 6 in _DECO
        assert 7 in _DECO
        assert 8 in _DECO
        assert 12 in _DECO

    def test_deco_style_6_images(self):
        deco = _DECO[6]
        assert len(deco.images) == 3
        for name, x, y in deco.images:
            assert isinstance(name, str)
            assert isinstance(x, int) and isinstance(y, int)

    def test_deco_style_8_no_color_fills(self):
        assert _DECO[8].color_fills == []

    def test_deco_style_12_single_image(self):
        deco = _DECO[12]
        assert len(deco.images) == 1
        assert deco.images[0][0] == "led_bg_rgb_lf13"


# =========================================================================
# UCInfoImage tests
# =========================================================================

class TestUCInfoImage:
    """UCInfoImage sensor gauge widget."""

    def test_construction_size(self, info_image):
        assert info_image.width() == 240
        assert info_image.height() == 30

    def test_default_values(self, info_image):
        assert info_image._value == 0.0
        assert info_image._text == "--"
        assert info_image._unit == ""
        assert info_image._mode == 1

    def test_set_value(self, info_image):
        info_image.set_value(65.0, "65", "\u00b0C")
        assert info_image._value == 65.0
        assert info_image._text == "65"
        assert info_image._unit == "\u00b0C"

    def test_set_mode(self, info_image):
        info_image.set_mode(2)
        assert info_image._mode == 2

    @pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6])
    def test_all_indices_construct(self, qapp, index):
        w = UCInfoImage(index)
        assert w._index == index


# =========================================================================
# UCLedControl tests
# =========================================================================

class TestLedControlConstruction:
    """UCLedControl construction and default state."""

    def test_panel_size(self, led_control):
        assert led_control.width() == PANEL_WIDTH
        assert led_control.height() == PANEL_HEIGHT

    def test_default_mode(self, led_control):
        assert led_control._current_mode == 0

    def test_default_zone_count(self, led_control):
        assert led_control._zone_count == 1

    def test_mode_buttons_count(self, led_control):
        assert len(led_control._mode_buttons) == len(MODE_LABELS)

    def test_first_mode_checked(self, led_control):
        assert led_control._mode_buttons[0].isChecked()

    def test_preset_buttons_count(self, led_control):
        assert len(led_control._preset_buttons) == 8

    def test_rgb_sliders_count(self, led_control):
        assert len(led_control._rgb_sliders) == 3

    def test_rgb_spinboxes_count(self, led_control):
        assert len(led_control._rgb_spinboxes) == 3

    def test_rgb_default_values(self, led_control):
        """Red slider/spinbox defaults to 255, green and blue to 0."""
        assert led_control._rgb_sliders[0].value() == 255
        assert led_control._rgb_sliders[1].value() == 0
        assert led_control._rgb_sliders[2].value() == 0
        assert led_control._rgb_spinboxes[0].value() == 255
        assert led_control._rgb_spinboxes[1].value() == 0
        assert led_control._rgb_spinboxes[2].value() == 0

    def test_brightness_default(self, led_control):
        assert led_control._brightness_slider.value() == 100

    def test_brightness_range(self, led_control):
        assert led_control._brightness_slider.minimum() == 0
        assert led_control._brightness_slider.maximum() == 100

    def test_zone_buttons_hidden_by_default(self, led_control):
        for btn in led_control._zone_buttons:
            assert not btn.isVisible()

    def test_carousel_btn_hidden_by_default(self, led_control):
        assert not led_control._carousel_btn.isVisible()

    def test_temp_legend_hidden_by_default(self, led_control):
        assert not led_control._temp_legend.isVisible()

    def test_color_wheel_exists(self, led_control):
        assert isinstance(led_control._color_wheel, UCColorWheel)


class TestLedControlSignals:
    """Signal emissions from UCLedControl."""

    def test_mode_changed_signal(self, led_control):
        received: list[int] = []
        led_control.mode_changed.connect(lambda m: received.append(m))
        led_control._on_mode_clicked(2)
        assert received == [2]

    def test_mode_clicked_updates_buttons(self, led_control):
        led_control._on_mode_clicked(3)
        for i, btn in enumerate(led_control._mode_buttons):
            assert btn.isChecked() == (i == 3)

    def test_color_changed_from_rgb_slider(self, led_control):
        received: list[tuple] = []
        led_control.color_changed.connect(lambda r, g, b: received.append((r, g, b)))
        led_control._rgb_sliders[0].setValue(128)
        # Signal should have fired with (128, 0, 0)
        assert len(received) >= 1
        assert received[-1][0] == 128

    def test_brightness_changed_signal(self, led_control):
        received: list[int] = []
        led_control.brightness_changed.connect(lambda v: received.append(v))
        led_control._brightness_slider.setValue(50)
        assert 50 in received

    def test_global_toggled_from_wheel(self, led_control):
        received: list[bool] = []
        led_control.global_toggled.connect(lambda v: received.append(v))
        led_control._on_wheel_onoff(0)
        assert received == [False]

    def test_temp_unit_changed_signal(self, led_control):
        received: list[str] = []
        led_control.temp_unit_changed.connect(lambda u: received.append(u))
        led_control._set_temp_unit_btn(True)
        assert received == ["F"]

    def test_temp_unit_celsius(self, led_control):
        received: list[str] = []
        led_control.temp_unit_changed.connect(lambda u: received.append(u))
        led_control._set_temp_unit_btn(False)
        assert received == ["C"]


class TestLedControlRGBSync:
    """RGB slider/spinbox synchronization."""

    def test_slider_to_spinbox_sync(self, led_control):
        led_control._rgb_sliders[1].setValue(200)
        assert led_control._rgb_spinboxes[1].value() == 200

    def test_spinbox_to_slider_sync(self, led_control):
        led_control._rgb_spinboxes[2].setValue(150)
        assert led_control._rgb_sliders[2].value() == 150

    def test_set_color_updates_both(self, led_control):
        led_control._set_color(10, 20, 30)
        assert led_control._rgb_sliders[0].value() == 10
        assert led_control._rgb_sliders[1].value() == 20
        assert led_control._rgb_sliders[2].value() == 30
        assert led_control._rgb_spinboxes[0].value() == 10
        assert led_control._rgb_spinboxes[1].value() == 20
        assert led_control._rgb_spinboxes[2].value() == 30

    def test_set_color_emits_color_changed(self, led_control):
        received: list[tuple] = []
        led_control.color_changed.connect(lambda r, g, b: received.append((r, g, b)))
        led_control._set_color(50, 100, 150)
        assert (50, 100, 150) in received


class TestLedControlZones:
    """Zone button behavior."""

    def test_zone_selected_signal(self, led_control):
        led_control._zone_count = 4
        led_control._is_select_all_style = False
        received: list[int] = []
        led_control.zone_selected.connect(lambda z: received.append(z))
        led_control._on_zone_clicked(2)
        assert received == [2]

    def test_zone_radio_select(self, led_control):
        """In non-carousel mode, only the clicked zone is checked."""
        led_control._zone_count = 4
        led_control._is_select_all_style = False
        led_control._on_zone_clicked(2)
        for i, btn in enumerate(led_control._zone_buttons):
            assert btn.isChecked() == (i == 2)

    def test_carousel_changed_signal(self, led_control):
        led_control._zone_count = 4
        led_control._is_select_all_style = False
        received: list[bool] = []
        led_control.carousel_changed.connect(lambda c: received.append(c))
        led_control._on_sync_toggled(True)
        assert received == [True]


class TestLedControlLoadZone:
    """load_zone_state() UI synchronization."""

    def test_load_zone_sets_rgb(self, led_control):
        led_control.load_zone_state(0, 1, (100, 150, 200), 75)
        assert led_control._rgb_sliders[0].value() == 100
        assert led_control._rgb_sliders[1].value() == 150
        assert led_control._rgb_sliders[2].value() == 200

    def test_load_zone_sets_brightness(self, led_control):
        led_control.load_zone_state(0, 2, (0, 0, 0), 42)
        assert led_control._brightness_slider.value() == 42

    def test_load_zone_sets_mode(self, led_control):
        led_control.load_zone_state(0, 3, (0, 0, 0), 50)
        assert led_control._current_mode == 3
        assert led_control._mode_buttons[3].isChecked()


class TestLedControlLoadSyncState:
    """load_sync_state() — restore carousel/circulate UI from config."""

    def _init_multi_zone(self, led_control, style_id=6, zones=2):
        """Initialize panel as a multi-zone device (e.g. LF12 style 6)."""
        led_control.initialize(style_id, segment_count=72, zone_count=zones,
                               model='LF12')

    def test_enables_carousel(self, led_control):
        self._init_multi_zone(led_control)
        led_control.load_sync_state(True, [True, False], 3)
        assert led_control._carousel_mode is True
        assert led_control._carousel_btn.isChecked()
        assert not led_control._carousel_interval.isHidden()
        assert led_control._carousel_interval.text() == "3"

    def test_disabled_carousel(self, led_control):
        self._init_multi_zone(led_control)
        led_control.load_sync_state(False, [True, False], 2)
        assert led_control._carousel_mode is False
        assert not led_control._carousel_btn.isChecked()
        assert led_control._carousel_interval.isHidden()

    def test_sets_zone_buttons(self, led_control):
        self._init_multi_zone(led_control)
        led_control.load_sync_state(True, [False, True], 2)
        assert not led_control._zone_buttons[0].isChecked()
        assert led_control._zone_buttons[1].isChecked()

    def test_no_signal_emission(self, led_control):
        """blockSignals prevents round-trip through handler."""
        self._init_multi_zone(led_control)
        received: list[bool] = []
        led_control.carousel_changed.connect(lambda c: received.append(c))
        led_control.load_sync_state(True, [True, False], 3)
        assert received == []

    def test_select_all_style_hides_interval(self, led_control):
        """Style 2 (select-all) — interval stays hidden even when enabled."""
        led_control.initialize(2, segment_count=30, zone_count=4,
                               model='PA120')
        led_control.load_sync_state(True, [True, True, True, True], 2)
        assert led_control._carousel_mode is True
        assert led_control._carousel_interval.isHidden()


class TestLedControlClockFormat:
    """LC2 clock format handlers."""

    def test_set_clock_24h(self, led_control):
        received: list[bool] = []
        led_control.clock_format_changed.connect(lambda v: received.append(v))
        led_control._set_clock_format(True)
        assert led_control._is_timer_24h is True
        assert led_control._btn_24h.isChecked()
        assert not led_control._btn_12h.isChecked()
        assert received == [True]

    def test_set_clock_12h(self, led_control):
        received: list[bool] = []
        led_control.clock_format_changed.connect(lambda v: received.append(v))
        led_control._set_clock_format(False)
        assert led_control._is_timer_24h is False
        assert not led_control._btn_24h.isChecked()
        assert led_control._btn_12h.isChecked()
        assert received == [False]

    def test_set_week_sunday(self, led_control):
        received: list[bool] = []
        led_control.week_start_changed.connect(lambda v: received.append(v))
        led_control._set_week_start(True)
        assert led_control._is_week_sunday is True
        assert led_control._btn_sun.isChecked()
        assert not led_control._btn_mon.isChecked()
        assert received == [True]


class TestLedControlTempUnit:
    """Temperature unit management."""

    def test_set_temp_unit_celsius(self, led_control):
        led_control.set_temp_unit(0)
        assert led_control._temp_unit == "\u00b0C"
        assert led_control._btn_celsius.isChecked()
        assert not led_control._btn_fahrenheit.isChecked()

    def test_set_temp_unit_fahrenheit(self, led_control):
        led_control.set_temp_unit(1)
        assert led_control._temp_unit == "\u00b0F"
        assert not led_control._btn_celsius.isChecked()
        assert led_control._btn_fahrenheit.isChecked()


class TestLedControlProperties:
    """Properties and read accessors."""

    def test_selected_zone_default(self, led_control):
        assert led_control.selected_zone == 0

    def test_carousel_mode_default(self, led_control):
        assert led_control.carousel_mode is False

    def test_set_status(self, led_control):
        led_control.set_status("Connected")
        assert led_control._status.text() == "Connected"

    def test_set_led_colors_delegates(self, led_control):
        colors = [(10, 20, 30)] * 30
        led_control.set_led_colors(colors)
        assert led_control._preview._colors[0] == (10, 20, 30)

    def test_set_memory_ratio(self, led_control):
        led_control.set_memory_ratio(4)
        assert led_control._memory_ratio == 4
        assert led_control._ddr_combo.currentIndex() == 2


class TestLedControlMetrics:
    """update_metrics() dispatch to sub-widgets."""

    @staticmethod
    def _metrics(**kw):
        from trcc.legacy.core.models import HardwareMetrics
        return HardwareMetrics(**kw)

    def test_update_metrics_style_1_updates_sensors(self, led_control):
        """Style 1 (AX120): routes to sensor gauges."""
        led_control._style_id = 1
        m = self._metrics(cpu_temp=55.0, cpu_freq=3500.0, cpu_percent=30.0,
                          gpu_temp=60.0, gpu_clock=1800.0, gpu_usage=50.0)
        led_control.update_metrics(m)
        assert led_control._info_images['cpu_temp']._value == 55.0
        assert led_control._info_images['gpu_clock']._value == 1800.0

    def test_update_metrics_style_4_updates_memory(self, led_control):
        """Style 4 (LC1): routes to memory labels."""
        led_control._style_id = 4
        m = self._metrics(mem_temp=45.0, mem_clock=1600.0,
                          mem_percent=60.0, mem_available=8000.0)
        led_control.update_metrics(m)
        assert "1600" in led_control._mem_labels['mem_clock'].text()

    def test_update_metrics_style_10_updates_disk(self, led_control):
        """Style 10 (LF11): routes to disk labels."""
        led_control._style_id = 10
        m = self._metrics(disk_temp=42.0, disk_activity=75.0,
                          disk_read=100.0, disk_write=50.0)
        led_control.update_metrics(m)
        assert "75" in led_control._disk_labels['lf11_disk_usage'].text()
        assert "100" in led_control._disk_labels['lf11_disk_read'].text()

    def test_update_metrics_fahrenheit(self, led_control):
        """Fahrenheit display — mediator pre-converts temps before dispatch."""
        led_control._style_id = 1
        led_control._temp_unit = "\u00b0F"
        # Mediator applies C/F conversion before dispatch — pass pre-converted
        m = self._metrics(cpu_temp=212.0, gpu_temp=32.0)
        led_control.update_metrics(m)
        assert led_control._info_images['cpu_temp']._value == 212.0


class TestLedControlLayoutConstants:
    """Module-level layout constants."""

    def test_panel_dimensions(self):
        assert PANEL_WIDTH == 1274
        assert PANEL_HEIGHT == 800

    def test_mode_button_dimensions(self):
        assert MODE_W == 93
        assert MODE_H == 62

    def test_mode_labels_count(self):
        assert len(MODE_LABELS) == 6

    def test_preset_colors_count(self):
        assert len(PRESET_COLORS) == 8

    def test_preset_colors_valid_rgb(self):
        for r, g, b in PRESET_COLORS:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    def test_brightness_slider_layout(self):
        assert BRIGHT_X == 976
        assert BRIGHT_W == 190
