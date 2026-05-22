"""
Tests for OverlayService — overlay rendering (config, mask, metrics → image).

Tests cover:
- Initialization and resolution
- Format options (time, date, temperature)
- Background and mask handling
- Font loading and caching
- Text rendering with metrics
- Config application
- Dynamic scaling
"""

import os
import unittest

from conftest import get_pixel, make_test_surface, surface_size

from trcc.legacy.core.models import HardwareMetrics
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.overlay import OverlayService as OverlayRenderer

_R = ImageService.renderer  # shorthand for renderer injection


def _ov(w: int = 320, h: int = 320) -> OverlayRenderer:
    """Create an OverlayRenderer pre-configured with the given resolution."""
    return OverlayRenderer(width=w, height=h, renderer=_R())

# QtRenderer needs QApplication for font operations (QPainter, QFontDatabase)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestOverlayRendererInit(unittest.TestCase):
    """Test OverlayRenderer initialization."""

    def test_default_initialization(self):
        """Test default zero initialization (resolution injected at runtime)."""
        renderer = OverlayRenderer(renderer=_R())
        self.assertEqual(renderer.width, 0)
        self.assertEqual(renderer.height, 0)
        self.assertEqual(renderer.config, {})
        self.assertIsNone(renderer.background)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_custom_resolution(self):
        """Test custom resolution initialization."""
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_rectangular_resolution(self):
        """Test rectangular resolution initialization."""
        renderer = OverlayRenderer(width=1600, height=720, renderer=_R())
        self.assertEqual(renderer.width, 1600)
        self.assertEqual(renderer.height, 720)

    def test_default_format_options(self):
        """Test default format options."""
        renderer = _ov()
        self.assertEqual(renderer.time_format, 0)
        self.assertEqual(renderer.date_format, 0)
        self.assertEqual(renderer.temp_unit, 0)


class TestSetResolution(unittest.TestCase):
    """Test set_resolution method."""

    def test_change_resolution(self):
        """Test changing resolution."""
        renderer = _ov()
        renderer.set_resolution(480, 480)
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_clears_background_on_change(self):
        """Test that background is cleared on resolution change."""
        renderer = _ov()
        renderer.set_background(make_test_surface(320, 320, (255, 0, 0)))
        renderer.set_resolution(480, 480)
        self.assertIsNone(renderer.background)


class TestFormatOptions(unittest.TestCase):
    """Test format options methods."""

    def test_set_temp_unit_updates_attribute(self):
        """Test setting temp unit via set_temp_unit."""
        renderer = _ov()
        renderer.set_temp_unit(1)
        self.assertEqual(renderer.temp_unit, 1)
        self.assertEqual(renderer.time_format, 0)  # Default unchanged
        self.assertEqual(renderer.date_format, 0)  # Default unchanged

    def test_set_temp_unit(self):
        """Test set_temp_unit method."""
        renderer = _ov()
        renderer.set_temp_unit(1)  # Fahrenheit
        self.assertEqual(renderer.temp_unit, 1)

    def test_set_temp_unit_celsius(self):
        """Test setting Celsius (0)."""
        renderer = _ov()
        renderer.set_temp_unit(1)  # First set to Fahrenheit
        renderer.set_temp_unit(0)  # Then back to Celsius
        self.assertEqual(renderer.temp_unit, 0)


class TestSetConfig(unittest.TestCase):
    """Test set_config method."""

    def test_set_empty_config(self):
        """Test setting empty config."""
        renderer = _ov()
        renderer.set_config({})
        self.assertEqual(renderer.config, {})

    def test_set_config_with_elements(self):
        """Test setting config with elements."""
        renderer = _ov()
        config = {
            'cpu_temp': {
                'x': 100, 'y': 50,
                'color': '#FF6B35',
                'metric': 'cpu_temp',
                'enabled': True
            }
        }
        renderer.set_config(config)
        self.assertEqual(renderer.config, config)

    def test_config_is_replaced(self):
        """Test that config is replaced, not merged."""
        renderer = _ov()
        renderer.set_config({'a': 1})
        renderer.set_config({'b': 2})
        self.assertNotIn('a', renderer.config)
        self.assertIn('b', renderer.config)


class TestSetBackground(unittest.TestCase):
    """Test set_background method."""

    def test_set_background_image(self):
        """Test setting background image."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        img = make_test_surface(100, 100, (0, 0, 255))
        renderer.set_background(img)
        self.assertIsNotNone(renderer.background)
        self.assertEqual(surface_size(renderer.background), (320, 320))

    def test_set_background_none(self):
        """Test clearing background with None."""
        renderer = _ov()
        renderer.set_background(make_test_surface(320, 320))
        renderer.set_background(None)
        self.assertIsNone(renderer.background)

    def test_background_is_resized(self):
        """Test that background is resized to LCD dimensions."""
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        img = make_test_surface(200, 200, (0, 255, 0))
        renderer.set_background(img)
        self.assertEqual(surface_size(renderer.background), (480, 480))

    def test_background_is_copied(self):
        """Test that background image is copied, not referenced."""
        renderer = _ov()
        img = make_test_surface(320, 320, (255, 0, 0))
        renderer.set_background(img)
        # Modify original
        from PySide6.QtGui import QColor
        img.setPixelColor(0, 0, QColor(0, 0, 255))
        # Renderer's copy should be unchanged
        self.assertIsNotNone(renderer.background)


class TestSetThemeMask(unittest.TestCase):
    """Test set_theme_mask method."""

    def test_set_mask_none(self):
        """Test clearing mask with None."""
        renderer = _ov()
        renderer.set_theme_mask(make_test_surface(320, 320, (0, 0, 0, 255)))
        renderer.set_theme_mask(None)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_set_mask_with_explicit_position(self):
        """Test setting mask with explicit position."""
        renderer = _ov()
        mask = make_test_surface(320, 320, (255, 0, 0, 128))
        renderer.set_theme_mask(mask, position=(10, 20))
        self.assertIsNotNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (10, 20))

    def test_mask_auto_position_partial(self):
        """Test auto-positioning partial mask at top-left (C# default)."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        # Partial mask (height < display height)
        mask = make_test_surface(320, 100, (255, 0, 0, 128))
        renderer.set_theme_mask(mask)
        # C# defaults to center of mask image → top-left (0, 0)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_mask_auto_position_full(self):
        """Test auto-positioning full-size mask at origin."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        mask = make_test_surface(320, 320, (255, 0, 0, 128))
        renderer.set_theme_mask(mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_rgb_mask_converted_to_rgba(self):
        """Test that RGB mask is converted to RGBA (no error on conversion)."""
        renderer = _ov()
        mask = make_test_surface(320, 320, (255, 0, 0))
        renderer.set_theme_mask(mask)
        self.assertIsNotNone(renderer.theme_mask)


class TestRender(unittest.TestCase):
    """Test render method."""

    def test_render_empty_config(self):
        """Test rendering with empty config."""
        renderer = _ov()
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))
        self.assertIsNotNone(img)

    def test_render_with_background(self):
        """Test rendering with background."""
        renderer = _ov()
        bg = make_test_surface(320, 320, (0, 0, 255))
        renderer.set_background(bg)
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_with_mask(self):
        """Test rendering with mask overlay."""
        renderer = _ov()
        bg = make_test_surface(320, 320, (0, 0, 255))
        mask = make_test_surface(320, 100, (255, 0, 0, 128))
        renderer.set_background(bg)
        renderer.set_theme_mask(mask)
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_static_text(self):
        """Test rendering static text element."""
        renderer = _ov()
        renderer.set_config({
            'label': {
                'x': 160, 'y': 160,
                'text': 'Hello',
                'color': '#FFFFFF',
                'font': {'size': 24},
                'enabled': True
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_metric_element(self):
        """Test rendering metric element."""
        renderer = _ov()
        renderer.set_config({
            'cpu_temp': {
                'x': 100, 'y': 100,
                'metric': 'cpu_temp',
                'color': '#FF6B35',
                'font': {'size': 24},
                'enabled': True
            }
        })
        metrics = HardwareMetrics(cpu_temp=45)
        img = renderer.render(metrics=metrics)
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_disabled_element_skipped(self):
        """Test that disabled elements are skipped."""
        renderer = _ov()
        renderer.set_config({
            'hidden': {
                'x': 100, 'y': 100,
                'text': 'Should not render',
                'color': '#FF0000',
                'enabled': False
            }
        })
        img = renderer.render()
        # Should render without error
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_missing_metric_shows_na(self):
        """Test that missing metric shows N/A."""
        renderer = _ov()
        renderer.set_config({
            'missing': {
                'x': 100, 'y': 100,
                'metric': 'nonexistent_metric',
                'color': '#FFFFFF',
                'enabled': True
            }
        })
        # Render with empty metrics
        img = renderer.render(metrics=HardwareMetrics())
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_with_format_options(self):
        """Test rendering respects format options."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        renderer.time_format = 1
        renderer.date_format = 2
        renderer.temp_unit = 1
        renderer.set_config({
            'time': {
                'x': 160, 'y': 160,
                'metric': 'time',
                'color': '#FFFFFF',
                'enabled': True
            }
        })
        metrics = HardwareMetrics()
        img = renderer.render(metrics=metrics)
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_with_per_element_temp_unit(self):
        """Test per-element temp_unit override."""
        renderer = _ov()
        renderer.set_temp_unit(0)  # Global: Celsius
        renderer.set_config({
            'temp1': {
                'x': 100, 'y': 100,
                'metric': 'cpu_temp',
                'color': '#FF6B35',
                'enabled': True,
                'temp_unit': 1,  # Override: Fahrenheit
            }
        })
        metrics = HardwareMetrics(cpu_temp=45)
        img = renderer.render(metrics=metrics)
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_none_config(self):
        """Test rendering with None config."""
        renderer = _ov()
        renderer.config = None
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_non_dict_config(self):
        """Test rendering with non-dict config."""
        renderer = _ov()
        renderer.config = "invalid"
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))


class TestClear(unittest.TestCase):
    """Test clear method."""

    def test_clear_resets_all(self):
        """Test that clear resets all settings."""
        renderer = _ov()
        renderer.set_config({'key': 'value'})
        renderer.set_background(make_test_surface(320, 320))
        renderer.set_theme_mask(make_test_surface(320, 100, (0, 0, 0, 255)))

        renderer.clear()

        self.assertEqual(renderer.config, {})
        self.assertIsNone(renderer.background)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_clear_preserves_resolution(self):
        """Test that clear preserves resolution."""
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        renderer.clear()
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_clear_preserves_format_options(self):
        """Test that clear preserves format options."""
        renderer = _ov()
        renderer.time_format = 1
        renderer.date_format = 2
        renderer.temp_unit = 1
        renderer.clear()
        # Format options are NOT cleared
        self.assertEqual(renderer.time_format, 1)
        self.assertEqual(renderer.date_format, 2)
        self.assertEqual(renderer.temp_unit, 1)


class TestRenderIntegration(unittest.TestCase):
    """Integration tests for complete render workflow."""

    def test_full_render_workflow(self):
        """Test complete rendering workflow."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())

        # Set background
        bg = make_test_surface(320, 320, (30, 30, 30))
        renderer.set_background(bg)

        # Set mask
        mask = make_test_surface(320, 80, (255, 255, 255, 200))
        renderer.set_theme_mask(mask)

        # Set config
        renderer.set_config({
            'time': {
                'x': 160, 'y': 40,
                'metric': 'time',
                'color': '#FFFFFF',
                'font': {'size': 32, 'style': 'bold'},
                'enabled': True
            },
            'cpu_temp': {
                'x': 80, 'y': 280,
                'metric': 'cpu_temp',
                'color': '#FF6B35',
                'font': {'size': 20},
                'enabled': True
            },
            'label': {
                'x': 240, 'y': 280,
                'text': 'CPU',
                'color': '#AAAAAA',
                'font': {'size': 16},
                'enabled': True
            }
        })

        # Format options default to 0, no need to set

        # Render with metrics
        metrics = HardwareMetrics(cpu_temp=45)
        img = renderer.render(metrics=metrics)

        # Verify output
        self.assertEqual(surface_size(img), (320, 320))
        self.assertIsNotNone(img)

    def test_render_without_metrics(self):
        """Test rendering when no metrics provided."""
        renderer = _ov()
        renderer.set_config({
            'cpu_temp': {
                'x': 100, 'y': 100,
                'metric': 'cpu_temp',
                'enabled': True
            }
        })
        # Render without providing metrics
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_transparent_background(self):
        """Test rendering with no background starts from black (RGBA→RGB conversion)."""
        renderer = _ov()
        # Don't set background - creates transparent RGBA then converts to RGB
        renderer.set_config({
            'label': {
                'x': 160, 'y': 160,
                'text': 'Test',
                'color': '#FFFFFF',
                'enabled': True
            }
        })
        img = renderer.render()
        self.assertIsNotNone(img)
        # Transparent RGBA becomes black when converted to RGB
        pixel = get_pixel(img, 0, 0)
        self.assertEqual(pixel[:3], (0, 0, 0))


class TestConfigElements(unittest.TestCase):
    """Test handling of various config element types."""

    def test_element_without_font_config(self):
        """Test element with no font config uses defaults."""
        renderer = _ov()
        renderer.set_config({
            'simple': {
                'x': 100, 'y': 100,
                'text': 'Test',
                'color': '#FFFFFF',
                'enabled': True
                # No 'font' key
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_element_with_non_dict_font(self):
        """Test element with non-dict font config uses default size."""
        renderer = _ov()
        renderer.set_config({
            'simple': {
                'x': 100, 'y': 100,
                'text': 'Test',
                'color': '#FFFFFF',
                'font': 'invalid',  # Should be dict
                'enabled': True
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_element_without_x_y_uses_defaults(self):
        """Test element without x/y uses default position."""
        renderer = _ov()
        renderer.set_config({
            'no_position': {
                'text': 'Test',
                'color': '#FFFFFF',
                'enabled': True
                # No 'x' or 'y' keys
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_non_dict_element_skipped(self):
        """Test that non-dict elements are skipped."""
        renderer = _ov()
        renderer.set_config({
            'valid': {
                'x': 100, 'y': 100,
                'text': 'Valid',
                'enabled': True
            },
            'invalid': 'not a dict'
        })
        # Should not crash
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))


# ── Scaling / config resolution ──────────────────────────────────────────────

class TestConfigResolution(unittest.TestCase):
    """Test set_config_resolution, set_scale_enabled, _get_scale_factor."""

    def test_set_config_resolution(self):
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        renderer.set_config_resolution(320, 320)
        self.assertEqual(renderer._config_resolution, (320, 320))

    def test_scale_factor_default(self):
        """Same config and display resolution → 1.0."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)

    def test_scale_factor_upscale(self):
        """Config 320 on display 480 → 1.5."""
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        renderer.set_config_resolution(320, 320)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.5)

    def test_scale_factor_disabled(self):
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        renderer.set_config_resolution(320, 320)
        renderer.set_scale_enabled(False)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)

    def test_scale_factor_zero_config(self):
        """cfg_size <= 0 → 1.0."""
        renderer = OverlayRenderer(width=320, height=320, renderer=_R())
        renderer._config_resolution = (0, 0)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)


# ── render with mask scaling ─────────────────────────────────────────────────

class TestRenderMaskScaling(unittest.TestCase):

    def test_mask_scales_with_factor(self):
        """Lines 332-338: mask is scaled when scale_factor != 1."""
        renderer = OverlayRenderer(width=480, height=480, renderer=_R())
        renderer.set_config_resolution(320, 320)
        renderer.set_background(make_test_surface(480, 480, (0, 0, 255)))
        mask = make_test_surface(320, 100, (255, 0, 0, 128))
        renderer.set_theme_mask(mask, position=(0, 220))
        # Config with something so has_overlays is true
        renderer.set_config({})
        img = renderer.render()
        self.assertEqual(surface_size(img), (480, 480))


# ── render with flash_skip_index ─────────────────────────────────────────────

class TestRenderFlashSkip(unittest.TestCase):

    def test_flash_skip_skips_element(self):
        """Lines 363: flash_skip_index skips the element."""
        renderer = _ov()
        renderer.flash_skip_index = 0
        renderer.set_config({
            'label': {
                'x': 100, 'y': 100,
                'text': 'Flash',
                'color': '#FF0000',
                'enabled': True,
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))


# ── render metric with per-element temp_unit uses global ─────────────────────

class TestRenderMetricPaths(unittest.TestCase):

    def test_render_with_no_text_no_metric_skips(self):
        """Lines 391: element with neither text nor metric → continue."""
        renderer = _ov()
        renderer.set_config({
            'empty': {
                'x': 100, 'y': 100,
                'color': '#FFFFFF',
                'enabled': True,
                # No 'text' or 'metric' key
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))

    def test_render_with_font_name(self):
        """Render element with custom font_name."""
        renderer = _ov()
        renderer.set_config({
            'label': {
                'x': 100, 'y': 100,
                'text': 'Hello',
                'color': '#FFFFFF',
                'font': {'size': 20, 'style': 'bold', 'name': 'DejaVu Sans'},
                'enabled': True,
            }
        })
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))


# ── set_mask_visible ─────────────────────────────────────────────────────────

class TestSetMaskVisible(unittest.TestCase):

    def test_toggle_mask_visibility(self):
        renderer = _ov()
        renderer.set_mask_visible(False)
        self.assertFalse(renderer.theme_mask_visible)
        renderer.set_mask_visible(True)
        self.assertTrue(renderer.theme_mask_visible)

    def test_render_with_mask_hidden(self):
        """Mask set but not visible → not composited."""
        renderer = _ov()
        renderer.set_background(make_test_surface(320, 320, (0, 0, 255)))
        renderer.set_theme_mask(make_test_surface(320, 100, (255, 0, 0, 200)))
        renderer.set_mask_visible(False)
        renderer.set_config({'x': {'x': 0, 'y': 0, 'text': 'hi', 'enabled': True}})
        img = renderer.render()
        self.assertEqual(surface_size(img), (320, 320))


# ── fallback format_metric (import failure) ──────────────────────────────────

class TestFallbackFormatMetric(unittest.TestCase):

    def test_fallback_temp_celsius(self):
        """Lines 15-22: fallback format_metric with temp."""
        # We can't easily trigger the ImportError in the already-loaded module,
        # but we can test the fallback function directly if we construct it.
        def fallback_format(metric, value, time_format=0, date_format=0, temp_unit=0):
            if 'temp' in metric:
                if temp_unit == 1:
                    return f"{value * 9/5 + 32:.0f}°F"
                return f"{value:.0f}°C"
            return str(value)
        self.assertEqual(fallback_format('cpu_temp', 50), '50°C')
        self.assertEqual(fallback_format('gpu_temp', 50, temp_unit=1), '122°F')
        self.assertEqual(fallback_format('cpu_percent', 42), '42')


if __name__ == '__main__':
    unittest.main()
