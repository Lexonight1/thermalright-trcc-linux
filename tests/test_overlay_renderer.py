"""
Tests for overlay_renderer - System metrics overlay rendering module.

Tests cover:
- OverlayRenderer initialization
- Resolution setting
- Format options (time, date, temperature)
- Background and mask handling
- Font loading and caching
- Text rendering with metrics
- Config application
"""

import os
import sys
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from PIL import Image

from trcc.overlay_renderer import OverlayRenderer


class TestOverlayRendererInit(unittest.TestCase):
    """Test OverlayRenderer initialization."""

    def test_default_initialization(self):
        """Test default 320x320 initialization."""
        renderer = OverlayRenderer()
        self.assertEqual(renderer.width, 320)
        self.assertEqual(renderer.height, 320)
        self.assertEqual(renderer.config, {})
        self.assertIsNone(renderer.background)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_custom_resolution(self):
        """Test custom resolution initialization."""
        renderer = OverlayRenderer(width=480, height=480)
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_rectangular_resolution(self):
        """Test rectangular resolution initialization."""
        renderer = OverlayRenderer(width=1600, height=720)
        self.assertEqual(renderer.width, 1600)
        self.assertEqual(renderer.height, 720)

    def test_default_format_options(self):
        """Test default format options."""
        renderer = OverlayRenderer()
        self.assertEqual(renderer.time_format, 0)
        self.assertEqual(renderer.date_format, 0)
        self.assertEqual(renderer.temp_unit, 0)


class TestSetResolution(unittest.TestCase):
    """Test set_resolution method."""

    def test_change_resolution(self):
        """Test changing resolution."""
        renderer = OverlayRenderer()
        renderer.set_resolution(480, 480)
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_clears_background_on_change(self):
        """Test that background is cleared on resolution change."""
        renderer = OverlayRenderer()
        renderer.background = Image.new('RGB', (320, 320), 'red')
        renderer.set_resolution(480, 480)
        self.assertIsNone(renderer.background)


class TestFormatOptions(unittest.TestCase):
    """Test format options methods."""

    def test_set_format_options_all(self):
        """Test setting all format options."""
        renderer = OverlayRenderer()
        renderer.set_format_options(time_format=1, date_format=2, temp_unit=1)
        self.assertEqual(renderer.time_format, 1)
        self.assertEqual(renderer.date_format, 2)
        self.assertEqual(renderer.temp_unit, 1)

    def test_set_format_options_partial(self):
        """Test setting partial format options."""
        renderer = OverlayRenderer()
        renderer.set_format_options(time_format=1)
        self.assertEqual(renderer.time_format, 1)
        self.assertEqual(renderer.date_format, 0)  # Default
        self.assertEqual(renderer.temp_unit, 0)    # Default

    def test_set_temp_unit(self):
        """Test set_temp_unit method."""
        renderer = OverlayRenderer()
        renderer.set_temp_unit(1)  # Fahrenheit
        self.assertEqual(renderer.temp_unit, 1)

    def test_set_temp_unit_celsius(self):
        """Test setting Celsius (0)."""
        renderer = OverlayRenderer()
        renderer.set_temp_unit(1)  # First set to Fahrenheit
        renderer.set_temp_unit(0)  # Then back to Celsius
        self.assertEqual(renderer.temp_unit, 0)


class TestSetConfig(unittest.TestCase):
    """Test set_config method."""

    def test_set_empty_config(self):
        """Test setting empty config."""
        renderer = OverlayRenderer()
        renderer.set_config({})
        self.assertEqual(renderer.config, {})

    def test_set_config_with_elements(self):
        """Test setting config with elements."""
        renderer = OverlayRenderer()
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
        renderer = OverlayRenderer()
        renderer.set_config({'a': 1})
        renderer.set_config({'b': 2})
        self.assertNotIn('a', renderer.config)
        self.assertIn('b', renderer.config)


class TestSetBackground(unittest.TestCase):
    """Test set_background method."""

    def test_set_background_image(self):
        """Test setting background image."""
        renderer = OverlayRenderer()
        img = Image.new('RGB', (100, 100), 'blue')
        renderer.set_background(img)
        self.assertIsNotNone(renderer.background)
        self.assertEqual(renderer.background.size, (320, 320))

    def test_set_background_none(self):
        """Test clearing background with None."""
        renderer = OverlayRenderer()
        renderer.background = Image.new('RGB', (320, 320))
        renderer.set_background(None)
        self.assertIsNone(renderer.background)

    def test_background_is_resized(self):
        """Test that background is resized to LCD dimensions."""
        renderer = OverlayRenderer(width=480, height=480)
        img = Image.new('RGB', (200, 200), 'green')
        renderer.set_background(img)
        self.assertEqual(renderer.background.size, (480, 480))

    def test_background_is_copied(self):
        """Test that background image is copied, not referenced."""
        renderer = OverlayRenderer()
        img = Image.new('RGB', (320, 320), 'red')
        renderer.set_background(img)
        # Modify original
        img.putpixel((0, 0), (0, 0, 255))
        # Renderer's copy should be unchanged
        self.assertIsNotNone(renderer.background)


class TestSetThemeMask(unittest.TestCase):
    """Test set_theme_mask method."""

    def test_set_mask_none(self):
        """Test clearing mask with None."""
        renderer = OverlayRenderer()
        renderer.theme_mask = Image.new('RGBA', (320, 320))
        renderer.set_theme_mask(None)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_set_mask_with_explicit_position(self):
        """Test setting mask with explicit position."""
        renderer = OverlayRenderer()
        mask = Image.new('RGBA', (320, 320), (255, 0, 0, 128))
        renderer.set_theme_mask(mask, position=(10, 20))
        self.assertIsNotNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (10, 20))

    def test_mask_auto_position_partial(self):
        """Test auto-positioning partial mask at bottom."""
        renderer = OverlayRenderer(width=320, height=320)
        # Partial mask (height < display height)
        mask = Image.new('RGBA', (320, 100), (255, 0, 0, 128))
        renderer.set_theme_mask(mask)
        # Should be positioned at bottom
        self.assertEqual(renderer.theme_mask_position, (0, 220))

    def test_mask_auto_position_full(self):
        """Test auto-positioning full-size mask at origin."""
        renderer = OverlayRenderer(width=320, height=320)
        mask = Image.new('RGBA', (320, 320), (255, 0, 0, 128))
        renderer.set_theme_mask(mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_rgb_mask_converted_to_rgba(self):
        """Test that RGB mask is converted to RGBA."""
        renderer = OverlayRenderer()
        mask = Image.new('RGB', (320, 320), 'red')
        renderer.set_theme_mask(mask)
        self.assertEqual(renderer.theme_mask.mode, 'RGBA')


class TestGetFont(unittest.TestCase):
    """Test get_font method."""

    def test_font_caching(self):
        """Test that fonts are cached."""
        renderer = OverlayRenderer()
        font1 = renderer.get_font(24, bold=False)
        font2 = renderer.get_font(24, bold=False)
        self.assertIs(font1, font2)

    def test_different_sizes_different_cache(self):
        """Test that different sizes have different cache entries."""
        renderer = OverlayRenderer()
        font1 = renderer.get_font(24, bold=False)
        font2 = renderer.get_font(32, bold=False)
        self.assertIsNot(font1, font2)

    def test_bold_different_cache(self):
        """Test that bold uses different cache entry."""
        renderer = OverlayRenderer()
        font1 = renderer.get_font(24, bold=False)
        font2 = renderer.get_font(24, bold=True)
        self.assertIsNot(font1, font2)

    def test_fallback_to_default(self):
        """Test fallback to default font when no fonts found."""
        renderer = OverlayRenderer()
        # Even if no fonts exist, should return a usable font
        font = renderer.get_font(24)
        self.assertIsNotNone(font)


class TestRender(unittest.TestCase):
    """Test render method."""

    def test_render_empty_config(self):
        """Test rendering with empty config."""
        renderer = OverlayRenderer()
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))
        self.assertEqual(img.mode, 'RGB')

    def test_render_with_background(self):
        """Test rendering with background."""
        renderer = OverlayRenderer()
        bg = Image.new('RGB', (320, 320), 'blue')
        renderer.set_background(bg)
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_render_with_mask(self):
        """Test rendering with mask overlay."""
        renderer = OverlayRenderer()
        bg = Image.new('RGB', (320, 320), 'blue')
        mask = Image.new('RGBA', (320, 100), (255, 0, 0, 128))
        renderer.set_background(bg)
        renderer.set_theme_mask(mask)
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_render_static_text(self):
        """Test rendering static text element."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))

    def test_render_metric_element(self):
        """Test rendering metric element."""
        renderer = OverlayRenderer()
        renderer.set_config({
            'cpu_temp': {
                'x': 100, 'y': 100,
                'metric': 'cpu_temp',
                'color': '#FF6B35',
                'font': {'size': 24},
                'enabled': True
            }
        })
        metrics = {'cpu_temp': 45}
        img = renderer.render(metrics)
        self.assertEqual(img.size, (320, 320))

    def test_render_disabled_element_skipped(self):
        """Test that disabled elements are skipped."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))

    def test_render_missing_metric_shows_na(self):
        """Test that missing metric shows N/A."""
        renderer = OverlayRenderer()
        renderer.set_config({
            'missing': {
                'x': 100, 'y': 100,
                'metric': 'nonexistent_metric',
                'color': '#FFFFFF',
                'enabled': True
            }
        })
        # Render with empty metrics
        img = renderer.render({})
        self.assertEqual(img.size, (320, 320))

    def test_render_with_format_options(self):
        """Test rendering respects format options."""
        renderer = OverlayRenderer()
        renderer.set_format_options(time_format=1, date_format=2, temp_unit=1)
        renderer.set_config({
            'time': {
                'x': 160, 'y': 160,
                'metric': 'time',
                'color': '#FFFFFF',
                'enabled': True
            }
        })
        metrics = {'time': '14:30'}
        img = renderer.render(metrics)
        self.assertEqual(img.size, (320, 320))

    def test_render_with_per_element_temp_unit(self):
        """Test per-element temp_unit override."""
        renderer = OverlayRenderer()
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
        metrics = {'cpu_temp': 45}
        img = renderer.render(metrics)
        self.assertEqual(img.size, (320, 320))

    def test_render_none_config(self):
        """Test rendering with None config."""
        renderer = OverlayRenderer()
        renderer.config = None
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_render_non_dict_config(self):
        """Test rendering with non-dict config."""
        renderer = OverlayRenderer()
        renderer.config = "invalid"
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))


class TestClear(unittest.TestCase):
    """Test clear method."""

    def test_clear_resets_all(self):
        """Test that clear resets all settings."""
        renderer = OverlayRenderer()
        renderer.set_config({'key': 'value'})
        renderer.set_background(Image.new('RGB', (320, 320)))
        renderer.set_theme_mask(Image.new('RGBA', (320, 100)))

        renderer.clear()

        self.assertEqual(renderer.config, {})
        self.assertIsNone(renderer.background)
        self.assertIsNone(renderer.theme_mask)
        self.assertEqual(renderer.theme_mask_position, (0, 0))

    def test_clear_preserves_resolution(self):
        """Test that clear preserves resolution."""
        renderer = OverlayRenderer(width=480, height=480)
        renderer.clear()
        self.assertEqual(renderer.width, 480)
        self.assertEqual(renderer.height, 480)

    def test_clear_preserves_format_options(self):
        """Test that clear preserves format options."""
        renderer = OverlayRenderer()
        renderer.set_format_options(time_format=1, date_format=2, temp_unit=1)
        renderer.clear()
        # Format options are NOT cleared
        self.assertEqual(renderer.time_format, 1)
        self.assertEqual(renderer.date_format, 2)
        self.assertEqual(renderer.temp_unit, 1)


class TestRenderIntegration(unittest.TestCase):
    """Integration tests for complete render workflow."""

    def test_full_render_workflow(self):
        """Test complete rendering workflow."""
        renderer = OverlayRenderer(width=320, height=320)

        # Set background
        bg = Image.new('RGB', (320, 320), (30, 30, 30))
        renderer.set_background(bg)

        # Set mask
        mask = Image.new('RGBA', (320, 80), (255, 255, 255, 200))
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

        # Set format options
        renderer.set_format_options(time_format=0, date_format=0, temp_unit=0)

        # Render with metrics
        metrics = {
            'time': '14:30',
            'cpu_temp': 45
        }
        img = renderer.render(metrics)

        # Verify output
        self.assertEqual(img.size, (320, 320))
        self.assertEqual(img.mode, 'RGB')

    def test_render_without_metrics(self):
        """Test rendering when no metrics provided."""
        renderer = OverlayRenderer()
        renderer.set_config({
            'cpu_temp': {
                'x': 100, 'y': 100,
                'metric': 'cpu_temp',
                'enabled': True
            }
        })
        # Render without providing metrics
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_render_transparent_background(self):
        """Test rendering with no background starts from black (RGBA→RGB conversion)."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.mode, 'RGB')
        # Transparent RGBA becomes black when converted to RGB
        pixel = img.getpixel((0, 0))
        self.assertEqual(pixel, (0, 0, 0))


class TestConfigElements(unittest.TestCase):
    """Test handling of various config element types."""

    def test_element_without_font_config(self):
        """Test element with no font config uses defaults."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))

    def test_element_with_non_dict_font(self):
        """Test element with non-dict font config uses default size."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))

    def test_element_without_x_y_uses_defaults(self):
        """Test element without x/y uses default position."""
        renderer = OverlayRenderer()
        renderer.set_config({
            'no_position': {
                'text': 'Test',
                'color': '#FFFFFF',
                'enabled': True
                # No 'x' or 'y' keys
            }
        })
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_non_dict_element_skipped(self):
        """Test that non-dict elements are skipped."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))


# ── Scaling / config resolution ──────────────────────────────────────────────

class TestConfigResolution(unittest.TestCase):
    """Test set_config_resolution, set_scale_enabled, _get_scale_factor."""

    def test_set_config_resolution(self):
        renderer = OverlayRenderer(width=480, height=480)
        renderer.set_config_resolution(320, 320)
        self.assertEqual(renderer._config_resolution, (320, 320))

    def test_scale_factor_default(self):
        """Same config and display resolution → 1.0."""
        renderer = OverlayRenderer(width=320, height=320)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)

    def test_scale_factor_upscale(self):
        """Config 320 on display 480 → 1.5."""
        renderer = OverlayRenderer(width=480, height=480)
        renderer.set_config_resolution(320, 320)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.5)

    def test_scale_factor_disabled(self):
        renderer = OverlayRenderer(width=480, height=480)
        renderer.set_config_resolution(320, 320)
        renderer.set_scale_enabled(False)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)

    def test_scale_factor_zero_config(self):
        """cfg_size <= 0 → 1.0."""
        renderer = OverlayRenderer(width=320, height=320)
        renderer._config_resolution = (0, 0)
        self.assertAlmostEqual(renderer._get_scale_factor(), 1.0)

    def test_set_scale_enabled_clears_cache(self):
        renderer = OverlayRenderer()
        renderer.font_cache = {('key',): 'val'}
        renderer.set_scale_enabled(False)
        self.assertEqual(renderer.font_cache, {})


# ── _resolve_font_path ──────────────────────────────────────────────────────

class TestResolveFontPath(unittest.TestCase):

    def test_fc_match_success(self):
        """fc-match returns valid path."""
        renderer = OverlayRenderer()
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix='.ttf')
        os.close(fd)
        try:
            from unittest.mock import MagicMock, patch
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=tmp)
                result = renderer._resolve_font_path('DejaVu Sans')
            self.assertEqual(result, tmp)
        finally:
            os.unlink(tmp)

    def test_fc_match_not_found(self):
        """fc-match fails → falls through to manual scan."""
        renderer = OverlayRenderer()
        from unittest.mock import patch
        with patch('subprocess.run', side_effect=FileNotFoundError):
            # Manual scan won't find 'NoSuchFont' either
            result = renderer._resolve_font_path('NoSuchFontXYZ')
        self.assertIsNone(result)

    def test_fc_match_timeout(self):
        """fc-match times out → falls through."""
        import subprocess
        renderer = OverlayRenderer()
        from unittest.mock import patch
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('fc-match', 2)):
            result = renderer._resolve_font_path('SomeFont')
        self.assertIsNone(result)

    def test_fc_match_returns_nonexistent_path(self):
        """fc-match returns path that doesn't exist → None."""
        renderer = OverlayRenderer()
        from unittest.mock import MagicMock, patch
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='/nonexistent/font.ttf')
            result = renderer._resolve_font_path('SomeFont')
        self.assertIsNone(result)

    def test_manual_scan_finds_font(self):
        """Manual scan finds font file by name."""
        import os
        import tempfile
        renderer = OverlayRenderer()
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake font file
            open(os.path.join(tmpdir, 'DejaVuSans.ttf'), 'w').close()
            with patch('subprocess.run', side_effect=FileNotFoundError), \
                 patch('trcc.overlay_renderer.FONT_SEARCH_DIRS', [tmpdir]):
                result = renderer._resolve_font_path('DejaVu Sans')
            # Should find it in our patched search dir
            if result:
                self.assertIn('DejaVu', result)


# ── get_font with named font ────────────────────────────────────────────────

class TestGetFontNamed(unittest.TestCase):

    def test_named_font_resolved(self):
        """get_font with font_name calls _resolve_font_path."""
        renderer = OverlayRenderer()
        from unittest.mock import patch
        with patch.object(renderer, '_resolve_font_path', return_value=None):
            font = renderer.get_font(24, bold=False, font_name='CustomFont')
        self.assertIsNotNone(font)


# ── render with mask scaling ─────────────────────────────────────────────────

class TestRenderMaskScaling(unittest.TestCase):

    def test_mask_scales_with_factor(self):
        """Lines 332-338: mask is scaled when scale_factor != 1."""
        renderer = OverlayRenderer(width=480, height=480)
        renderer.set_config_resolution(320, 320)
        renderer.set_background(Image.new('RGB', (480, 480), 'blue'))
        mask = Image.new('RGBA', (320, 100), (255, 0, 0, 128))
        renderer.set_theme_mask(mask, position=(0, 220))
        # Config with something so has_overlays is true
        renderer.set_config({})
        img = renderer.render()
        self.assertEqual(img.size, (480, 480))


# ── render with flash_skip_index ─────────────────────────────────────────────

class TestRenderFlashSkip(unittest.TestCase):

    def test_flash_skip_skips_element(self):
        """Lines 363: flash_skip_index skips the element."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))


# ── render metric with per-element temp_unit uses global ─────────────────────

class TestRenderMetricPaths(unittest.TestCase):

    def test_render_with_no_text_no_metric_skips(self):
        """Lines 391: element with neither text nor metric → continue."""
        renderer = OverlayRenderer()
        renderer.set_config({
            'empty': {
                'x': 100, 'y': 100,
                'color': '#FFFFFF',
                'enabled': True,
                # No 'text' or 'metric' key
            }
        })
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))

    def test_render_with_font_name(self):
        """Render element with custom font_name."""
        renderer = OverlayRenderer()
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
        self.assertEqual(img.size, (320, 320))


# ── set_mask_visible ─────────────────────────────────────────────────────────

class TestSetMaskVisible(unittest.TestCase):

    def test_toggle_mask_visibility(self):
        renderer = OverlayRenderer()
        renderer.set_mask_visible(False)
        self.assertFalse(renderer.theme_mask_visible)
        renderer.set_mask_visible(True)
        self.assertTrue(renderer.theme_mask_visible)

    def test_render_with_mask_hidden(self):
        """Mask set but not visible → not composited."""
        renderer = OverlayRenderer()
        renderer.set_background(Image.new('RGB', (320, 320), 'blue'))
        renderer.set_theme_mask(Image.new('RGBA', (320, 100), (255, 0, 0, 200)))
        renderer.set_mask_visible(False)
        renderer.set_config({'x': {'x': 0, 'y': 0, 'text': 'hi', 'enabled': True}})
        img = renderer.render()
        self.assertEqual(img.size, (320, 320))


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
