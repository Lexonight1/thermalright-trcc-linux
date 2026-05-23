"""Tests for core/color.py — LED color computation (rainbow table + gradients)."""

import unittest

from trcc.legacy.core.color import ColorEngine


class TestRainbowTable(unittest.TestCase):
    """ColorEngine.generate_table() — 768-entry HSV rainbow cycle."""

    def test_table_length(self):
        """Rainbow table has exactly 768 entries."""
        table = ColorEngine.generate_table()
        self.assertEqual(len(table), 768)

    def test_entries_are_rgb_tuples(self):
        """Every entry is a 3-tuple of ints."""
        for i, entry in enumerate(ColorEngine.generate_table()):
            with self.subTest(i=i):
                self.assertEqual(len(entry), 3)
                self.assertIsInstance(entry[0], int)

    def test_values_in_0_255_range(self):
        """All RGB values are 0-255."""
        for i, (r, g, b) in enumerate(ColorEngine.generate_table()):
            with self.subTest(i=i):
                self.assertTrue(0 <= r <= 255, f"R={r} at index {i}")
                self.assertTrue(0 <= g <= 255, f"G={g} at index {i}")
                self.assertTrue(0 <= b <= 255, f"B={b} at index {i}")

    def test_starts_with_pure_red(self):
        """Index 0 = pure red (255, 0, 0)."""
        self.assertEqual(ColorEngine.generate_table()[0], (255, 0, 0))

    def test_phase_boundaries(self):
        """Known phase boundary colors match C# RGBTable."""
        table = ColorEngine.generate_table()
        # Phase 0 end (127): Red→Yellow complete → (255, 255, 0)
        self.assertEqual(table[127], (255, 255, 0))
        # Phase 1 end (255): Yellow→Green complete → (0, 255, 0)
        self.assertEqual(table[255], (0, 255, 0))
        # Phase 2 end (383): Green→Cyan complete → (0, 255, 255)
        self.assertEqual(table[383], (0, 255, 255))
        # Phase 3 end (511): Cyan→Blue complete → (0, 0, 255)
        self.assertEqual(table[511], (0, 0, 255))
        # Phase 4 end (639): Blue→Magenta complete → (255, 0, 255)
        self.assertEqual(table[639], (255, 0, 255))
        # Phase 5 end (767): Magenta→Red complete → (255, 0, 0)
        self.assertEqual(table[767], (255, 0, 0))

    def test_wraps_back_to_red(self):
        """Last entry matches first — smooth hue cycle."""
        table = ColorEngine.generate_table()
        self.assertEqual(table[0], table[767])

    def test_midpoint_green_phase(self):
        """Midpoint of green→cyan phase has green=255 and partial blue."""
        table = ColorEngine.generate_table()
        # Index 320 = phase 2, offset 64 → G=255, B≈128
        r, g, b = table[320]
        self.assertEqual(r, 0)
        self.assertEqual(g, 255)
        self.assertGreater(b, 100)
        self.assertLess(b, 160)


class TestGetTable(unittest.TestCase):
    """ColorEngine.get_table() — cached rainbow table."""

    def setUp(self):
        ColorEngine._cached_table = None

    def test_returns_768_entries(self):
        self.assertEqual(len(ColorEngine.get_table()), 768)

    def test_caches_result(self):
        """Second call returns same object (cached)."""
        t1 = ColorEngine.get_table()
        t2 = ColorEngine.get_table()
        self.assertIs(t1, t2)

    def test_matches_generate(self):
        """Cached table matches fresh generation."""
        self.assertEqual(ColorEngine.get_table(), ColorEngine.generate_table())

    def tearDown(self):
        ColorEngine._cached_table = None


class TestLerp(unittest.TestCase):
    """ColorEngine._lerp() — linear interpolation between RGB colors."""

    def test_t0_returns_first_color(self):
        result = ColorEngine._lerp((255, 0, 0), (0, 255, 0), 0.0)
        self.assertEqual(result, (255, 0, 0))

    def test_t1_returns_second_color(self):
        result = ColorEngine._lerp((255, 0, 0), (0, 255, 0), 1.0)
        self.assertEqual(result, (0, 255, 0))

    def test_t_half_returns_midpoint(self):
        result = ColorEngine._lerp((0, 0, 0), (200, 100, 50), 0.5)
        self.assertEqual(result, (100, 50, 25))

    def test_clamps_below_zero(self):
        """t < 0 clamps to t=0."""
        result = ColorEngine._lerp((100, 100, 100), (200, 200, 200), -0.5)
        self.assertEqual(result, (100, 100, 100))

    def test_clamps_above_one(self):
        """t > 1 clamps to t=1."""
        result = ColorEngine._lerp((100, 100, 100), (200, 200, 200), 1.5)
        self.assertEqual(result, (200, 200, 200))

    def test_quarter_interpolation(self):
        result = ColorEngine._lerp((0, 0, 0), (100, 200, 40), 0.25)
        self.assertEqual(result, (25, 50, 10))


class TestColorForValue(unittest.TestCase):
    """ColorEngine.color_for_value() — sensor value → RGB gradient mapping."""

    gradient = ColorEngine.TEMP_GRADIENT

    def test_below_min_clamps_to_first_stop(self):
        """Value below first gradient stop → first color (cyan)."""
        result = ColorEngine.color_for_value(0, self.gradient)
        self.assertEqual(result, (0, 255, 255))

    def test_at_min_returns_first_stop(self):
        result = ColorEngine.color_for_value(30, self.gradient)
        self.assertEqual(result, (0, 255, 255))

    def test_above_max_clamps_to_last_stop(self):
        """Value above last gradient stop → last color (red)."""
        result = ColorEngine.color_for_value(200, self.gradient)
        self.assertEqual(result, (255, 0, 0))

    def test_at_max_returns_last_stop(self):
        result = ColorEngine.color_for_value(100, self.gradient)
        self.assertEqual(result, (255, 0, 0))

    def test_exact_stop_returns_stop_color(self):
        """Exact gradient stop values return the stop's color."""
        self.assertEqual(ColorEngine.color_for_value(50, self.gradient), (0, 255, 0))
        self.assertEqual(ColorEngine.color_for_value(70, self.gradient), (255, 255, 0))

    def test_midpoint_interpolation(self):
        """Midpoint between two stops gives interpolated color."""
        # Midpoint between 30°C (cyan) and 50°C (green) = 40°C
        result = ColorEngine.color_for_value(40, self.gradient)
        # Cyan (0,255,255) → Green (0,255,0), t=0.5 → (0,255,127)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 255)
        self.assertAlmostEqual(result[2], 127, delta=2)

    def test_quarter_interpolation(self):
        """Quarter point between stops."""
        # 35°C = quarter between 30°C and 50°C
        result = ColorEngine.color_for_value(35, self.gradient)
        # t=0.25: cyan→green → B should be ~191
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 255)
        self.assertAlmostEqual(result[2], 191, delta=2)

    def test_load_gradient_same_as_temp(self):
        """LOAD_GRADIENT is the same object as TEMP_GRADIENT."""
        self.assertIs(ColorEngine.LOAD_GRADIENT, ColorEngine.TEMP_GRADIENT)

    def test_high_color_param_ignored(self):
        """high_color parameter is ignored (backward compat)."""
        a = ColorEngine.color_for_value(60, self.gradient)
        b = ColorEngine.color_for_value(60, self.gradient, high_color=(0, 0, 0))
        self.assertEqual(a, b)

    def test_all_stops_return_valid_rgb(self):
        """Every gradient stop returns values in 0-255."""
        for temp in range(0, 120):
            with self.subTest(temp=temp):
                r, g, b = ColorEngine.color_for_value(temp, self.gradient)
                self.assertTrue(0 <= r <= 255)
                self.assertTrue(0 <= g <= 255)
                self.assertTrue(0 <= b <= 255)

    def test_monotonic_red_channel_increases(self):
        """Red channel increases monotonically from 50°C to 100°C."""
        prev_r = 0
        for temp in range(50, 101, 5):
            r, _, _ = ColorEngine.color_for_value(temp, self.gradient)
            self.assertGreaterEqual(r, prev_r, f"Red decreased at {temp}°C")
            prev_r = r


if __name__ == '__main__':
    unittest.main()
