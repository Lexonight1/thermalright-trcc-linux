"""
Tests for qt_components.constants – layout values, color palette, and styles.

Pure data tests — no Qt or display server required.

Tests cover:
- Colors: palette values, preset color tuples
- Sizes: window dimensions, grid layout values, thumb sizes
- Layout: geometry tuples, tab positions, category button list
- Styles: stylesheet strings, dynamic thumb_selected/normal/non_local generators
"""

import unittest

from trcc.qt_components.constants import Colors, Layout, Sizes, Styles


class TestColors(unittest.TestCase):
    """Test Colors palette constants."""

    def test_hex_format(self):
        """Hex color strings start with '#'."""
        for attr in ('WINDOW_BG', 'ACCENT', 'BASE_BG', 'CLOSE_HOVER'):
            with self.subTest(attr=attr):
                val = getattr(Colors, attr)
                self.assertTrue(val.startswith('#'), f"{attr} = {val}")

    def test_preset_colors_count(self):
        """11 preset color swatches (Windows buttonC1-C11)."""
        self.assertEqual(len(Colors.PRESET_COLORS), 11)

    def test_preset_colors_are_rgb_tuples(self):
        for i, color in enumerate(Colors.PRESET_COLORS):
            with self.subTest(i=i):
                self.assertEqual(len(color), 3)
                self.assertTrue(all(0 <= c <= 255 for c in color))

    def test_placeholder_bg_tuple(self):
        self.assertEqual(len(Colors.PLACEHOLDER_BG), 3)


class TestSizes(unittest.TestCase):
    """Test Sizes dimension constants."""

    def test_window_dimensions(self):
        self.assertEqual(Sizes.WINDOW_W, 1454)
        self.assertEqual(Sizes.WINDOW_H, 800)

    def test_sidebar(self):
        self.assertEqual(Sizes.SIDEBAR_W, 180)
        self.assertEqual(Sizes.SIDEBAR_H, 800)

    def test_preview_frame(self):
        self.assertEqual(Sizes.PREVIEW_FRAME, 500)

    def test_thumbnail_sizes(self):
        self.assertEqual(Sizes.THUMB_W, 120)
        self.assertEqual(Sizes.THUMB_H, 140)
        self.assertEqual(Sizes.THUMB_IMAGE, 120)

    def test_grid_cols(self):
        self.assertEqual(Sizes.GRID_COLS, 5)

    def test_grid_margin_tuple(self):
        self.assertEqual(len(Sizes.GRID_MARGIN), 4)

    def test_panel_dimensions(self):
        self.assertEqual(Sizes.PANEL_W, 732)
        self.assertEqual(Sizes.PANEL_H, 652)

    def test_form_starts_after_sidebar(self):
        self.assertEqual(Sizes.FORM_X, Sizes.SIDEBAR_W)


class TestLayout(unittest.TestCase):
    """Test Layout geometry tuples."""

    def test_tuples_are_4_ints(self):
        """All standard layout rects are (x, y, w, h)."""
        rects = [
            Layout.SIDEBAR, Layout.FORM_CONTAINER, Layout.PREVIEW,
            Layout.PANEL_STACK, Layout.TAB_LOCAL, Layout.ROTATION_COMBO,
        ]
        for rect in rects:
            with self.subTest(rect=rect):
                self.assertEqual(len(rect), 4)
                self.assertTrue(all(isinstance(v, int) for v in rect))

    def test_sidebar_origin(self):
        self.assertEqual(Layout.SIDEBAR[:2], (0, 0))

    def test_form_container_x_matches_sidebar_width(self):
        self.assertEqual(Layout.FORM_CONTAINER[0], Sizes.SIDEBAR_W)

    def test_web_categories_list(self):
        """7 category buttons, each is (key, x, y, w, h)."""
        self.assertEqual(len(Layout.WEB_CATEGORIES), 7)
        for entry in Layout.WEB_CATEGORIES:
            self.assertEqual(len(entry), 5)

    def test_lang_buttons_list(self):
        """9 language checkboxes, each is (x, y, suffix)."""
        self.assertEqual(len(Layout.ABOUT_LANG_BUTTONS), 9)
        for entry in Layout.ABOUT_LANG_BUTTONS:
            self.assertEqual(len(entry), 3)

    def test_tab_buttons_ascending_x(self):
        """Tab buttons are ordered left-to-right by x coordinate."""
        tabs = [Layout.TAB_LOCAL, Layout.TAB_MASK, Layout.TAB_CLOUD, Layout.TAB_SETTINGS]
        x_coords = [t[0] for t in tabs]
        self.assertEqual(x_coords, sorted(x_coords))


class TestStyles(unittest.TestCase):
    """Test Styles stylesheet strings and generators."""

    def test_flat_button_contains_transparent(self):
        self.assertIn('transparent', Styles.FLAT_BUTTON)

    def test_scroll_area_no_border(self):
        self.assertIn('border: none', Styles.SCROLL_AREA)

    def test_thumb_selected_generator(self):
        """Dynamic stylesheet includes accent color and class name."""
        css = Styles.thumb_selected('MyThumb')
        self.assertIn('MyThumb', css)
        self.assertIn(Colors.ACCENT, css)

    def test_thumb_normal_generator(self):
        css = Styles.thumb_normal('MyThumb')
        self.assertIn('MyThumb', css)
        self.assertIn('hover', css)

    def test_thumb_non_local_generator(self):
        css = Styles.thumb_non_local('MaskThumb')
        self.assertIn('MaskThumb', css)
        self.assertIn('dashed', css)

    def test_slider_style(self):
        self.assertIn('QSlider', Styles.SLIDER)

    def test_add_element_btn(self):
        self.assertIn('QPushButton', Styles.ADD_ELEMENT_BTN)


if __name__ == '__main__':
    unittest.main()
