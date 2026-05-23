"""Tests for DcConfig unified class — dc_config.py.

Covers:
- get_hardware_metric_name() known and unknown mappings
- metric_to_hardware_ids() known and unknown mappings
- DcConfig empty init defaults
- DcConfig(filepath) with mocked parser
- _load() display_options mapping (background, rotation, ui_mode, display_mode)
- _load() transparent_display fallback to screencast_display
- _load() display_mode fallback to 'mode' key
- _load() mask_settings with overlay_rect (4 elements)
- _load() mask_settings with mask_position (2 elements)
- _load() overlay_rect/mask_position wrong length ignored
- save() calls dc_writer.write with correct ThemeConfig
- _to_theme_config() produces correct ThemeConfig
- to_overlay_config() delegates to DcParser.to_overlay_config
- from_overlay_config() creates DcConfig from overlay dict
- to_dict() returns all fields
- __repr__() 0xDD and 0xDC formats
"""

from unittest.mock import MagicMock, patch

from trcc.legacy.adapters.infra.dc_config import (
    DcConfig,
    get_hardware_metric_name,
    metric_to_hardware_ids,
)
from trcc.legacy.core.models import (
    DisplayElement,
    FontConfig,
    ThemeConfig,
)

# ── Module-level functions ──


class TestGetHardwareMetricName:
    """get_hardware_metric_name() — maps (main, sub) to metric name."""

    def test_known_cpu_temp(self):
        assert get_hardware_metric_name(0, 1) == 'cpu_temp'

    def test_known_gpu_usage(self):
        assert get_hardware_metric_name(1, 2) == 'gpu_usage'

    def test_known_fan_cpu(self):
        assert get_hardware_metric_name(5, 1) == 'fan_cpu'

    def test_unknown_fallback(self):
        assert get_hardware_metric_name(99, 42) == 'sensor_99_42'


class TestMetricToHardwareIds:
    """metric_to_hardware_ids() — maps metric name to (main, sub) tuple."""

    def test_known_cpu_temp(self):
        assert metric_to_hardware_ids('cpu_temp') == (0, 1)

    def test_known_net_up(self):
        assert metric_to_hardware_ids('net_up') == (4, 2)

    def test_unknown_fallback(self):
        assert metric_to_hardware_ids('nonexistent_metric') == (0, 0)


# ── DcConfig.__init__ defaults ──


class TestDcConfigDefaults:
    """DcConfig() with no filepath — all attributes at default."""

    def test_empty_elements(self):
        dc = DcConfig()
        assert dc.elements == []

    def test_system_info_enabled(self):
        dc = DcConfig()
        assert dc.system_info_enabled is True

    def test_display_option_defaults(self):
        dc = DcConfig()
        assert dc.background_display is True
        assert dc.transparent_display is False
        assert dc.rotation == 0
        assert dc.ui_mode == 0
        assert dc.display_mode == 0

    def test_overlay_defaults(self):
        dc = DcConfig()
        assert dc.overlay_enabled is True
        assert dc.overlay_x == 0
        assert dc.overlay_y == 0
        assert dc.overlay_w == 0
        assert dc.overlay_h == 0

    def test_mask_defaults(self):
        dc = DcConfig()
        assert dc.mask_enabled is False
        assert dc.mask_x == 0
        assert dc.mask_y == 0

    def test_parse_side_defaults(self):
        dc = DcConfig()
        assert dc.version == 0
        assert dc.fonts == []
        assert dc.flags == {}
        assert dc.custom_text == ""
        assert dc.legacy_elements == {}
        assert dc.display_options == {}
        assert dc.mask_settings == {}


# ── DcConfig._load via DcConfig(filepath) ──


def _make_parsed_dict(**overrides):
    """Build a minimal parsed dict with sensible defaults, applying overrides."""
    base = {
        'version': 0x000000DD,
        'fonts': [],
        'flags': {'system_info': True},
        'custom_text': 'hello',
        'elements': {'time': MagicMock()},
        'display_elements': [],
        'display_options': {},
        'mask_settings': {},
    }
    base.update(overrides)
    return base


class TestDcConfigLoad:
    """DcConfig(filepath) — _load populates all fields from parsed dict."""

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_filepath_triggers_load(self, mock_parse):
        """Constructor with filepath calls DcParser.parse."""
        elem = DisplayElement(mode=0, mode_sub=0, x=10, y=20)
        font = FontConfig(name='Arial', size=12.0, style=0, unit=3, charset=0,
                          color_argb=(255, 0, 0, 0))
        mock_parse.return_value = _make_parsed_dict(
            display_elements=[elem],
            fonts=[font],
            custom_text='test text',
        )
        dc = DcConfig('/fake/path.dc')

        mock_parse.assert_called_once_with('/fake/path.dc')
        assert dc.elements == [elem]
        assert dc.fonts == [font]
        assert dc.custom_text == 'test text'

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_version_and_flags(self, mock_parse):
        mock_parse.return_value = _make_parsed_dict(
            version=0x000000DD,
            flags={'system_info': False, 'extra': 1},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.version == 0x000000DD
        assert dc.system_info_enabled is False
        assert dc.flags == {'system_info': False, 'extra': 1}

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_display_options_mapped(self, mock_parse):
        """display_options dict maps to flat attributes."""
        mock_parse.return_value = _make_parsed_dict(
            display_options={
                'background_display': False,
                'transparent_display': True,
                'direction': 90,
                'ui_mode': 2,
                'display_mode': 3,
            },
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.background_display is False
        assert dc.transparent_display is True
        assert dc.rotation == 90
        assert dc.ui_mode == 2
        assert dc.display_mode == 3

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_transparent_display_fallback_to_screencast(self, mock_parse):
        """transparent_display falls back to screencast_display if missing."""
        mock_parse.return_value = _make_parsed_dict(
            display_options={'screencast_display': True},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.transparent_display is True

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_transparent_display_prefers_own_key(self, mock_parse):
        """transparent_display takes priority over screencast_display."""
        mock_parse.return_value = _make_parsed_dict(
            display_options={
                'transparent_display': False,
                'screencast_display': True,
            },
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.transparent_display is False

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_display_mode_fallback_to_mode_key(self, mock_parse):
        """display_mode reads from 'mode' key when 'display_mode' absent."""
        mock_parse.return_value = _make_parsed_dict(
            display_options={'mode': 5},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.display_mode == 5

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_display_mode_prefers_mode_over_display_mode(self, mock_parse):
        """opts.get('mode', opts.get('display_mode', 0)) — 'mode' takes priority."""
        mock_parse.return_value = _make_parsed_dict(
            display_options={'mode': 7, 'display_mode': 3},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.display_mode == 7

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_overlay_rect_4_elements(self, mock_parse):
        """mask_settings overlay_rect with 4 elements unpacks to x,y,w,h."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={
                'overlay_enabled': True,
                'overlay_rect': [10, 20, 400, 300],
                'mask_enabled': False,
            },
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.overlay_x == 10
        assert dc.overlay_y == 20
        assert dc.overlay_w == 400
        assert dc.overlay_h == 300

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_overlay_rect_wrong_length_ignored(self, mock_parse):
        """overlay_rect with length != 4 leaves defaults intact."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'overlay_rect': [10, 20]},
        )
        dc = DcConfig('/fake/path.dc')
        # Defaults unchanged (uninitialized = 0)
        assert dc.overlay_x == 0
        assert dc.overlay_y == 0
        assert dc.overlay_w == 0
        assert dc.overlay_h == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_overlay_rect_none_ignored(self, mock_parse):
        """overlay_rect=None leaves defaults intact."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'overlay_rect': None},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.overlay_w == 0
        assert dc.overlay_h == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_mask_position_2_elements(self, mock_parse):
        """mask_settings mask_position with 2 elements unpacks to mask_x, mask_y."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={
                'mask_enabled': True,
                'mask_position': [50, 75],
            },
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.mask_enabled is True
        assert dc.mask_x == 50
        assert dc.mask_y == 75

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_mask_position_wrong_length_ignored(self, mock_parse):
        """mask_position with length != 2 leaves defaults intact."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'mask_position': [50]},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.mask_x == 0
        assert dc.mask_y == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_mask_position_none_ignored(self, mock_parse):
        """mask_position=None leaves defaults intact."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'mask_position': None},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.mask_x == 0
        assert dc.mask_y == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_legacy_elements_stored(self, mock_parse):
        """Parsed 'elements' dict stored as legacy_elements."""
        legacy = {'time': MagicMock(), 'date': MagicMock()}
        mock_parse.return_value = _make_parsed_dict(elements=legacy)
        dc = DcConfig('/fake/path.dc')
        assert dc.legacy_elements is legacy

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_filepath_converted_to_str(self, mock_parse):
        """Path object is converted to str before passing to DcParser.parse."""
        from pathlib import Path
        mock_parse.return_value = _make_parsed_dict()
        DcConfig(Path('/some/path.dc'))
        mock_parse.assert_called_once_with('/some/path.dc')


# ── save() ──


class TestDcConfigSave:
    """save() converts to ThemeConfig and calls dc_writer.write."""

    def test_save_calls_writer_with_theme_config(self, tmp_path):
        """save() passes ThemeConfig and filepath string to dc_writer.write."""
        dc = DcConfig()
        dc.elements = [DisplayElement(mode=1, mode_sub=0, x=5, y=10)]
        dc.system_info_enabled = False
        dc.rotation = 180
        dc.overlay_w = 640
        dc.overlay_h = 480

        filepath = str(tmp_path / 'config1.dc')
        with patch('trcc.legacy.adapters.infra.dc_writer.write') as mock_write:
            dc.save(filepath)
            mock_write.assert_called_once()
            tc = mock_write.call_args[0][0]
            assert isinstance(tc, ThemeConfig)
            assert tc.rotation == 180
            assert tc.system_info_enabled is False
            assert tc.overlay_w == 640
            assert tc.overlay_h == 480
            assert len(tc.elements) == 1
            assert mock_write.call_args[0][1] == filepath

    def test_save_accepts_path_object(self, tmp_path):
        """save() accepts pathlib.Path and converts to str for writer."""
        dc = DcConfig()
        filepath = tmp_path / 'config1.dc'
        with patch('trcc.legacy.adapters.infra.dc_writer.write') as mock_write:
            dc.save(filepath)
            # Second arg should be a string
            assert mock_write.call_args[0][1] == str(filepath)


# ── _to_theme_config() ──


class TestToThemeConfig:
    """_to_theme_config() produces ThemeConfig with all fields."""

    def test_all_fields_mapped(self):
        dc = DcConfig()
        elem = DisplayElement(mode=0, mode_sub=1, x=100, y=200)
        dc.elements = [elem]
        dc.system_info_enabled = False
        dc.background_display = False
        dc.transparent_display = True
        dc.rotation = 90
        dc.ui_mode = 1
        dc.display_mode = 2
        dc.overlay_enabled = False
        dc.overlay_x = 10
        dc.overlay_y = 20
        dc.overlay_w = 640
        dc.overlay_h = 480
        dc.mask_enabled = True
        dc.mask_x = 30
        dc.mask_y = 40

        tc = dc._to_theme_config()

        assert isinstance(tc, ThemeConfig)
        assert tc.elements == [elem]
        assert tc.system_info_enabled is False
        assert tc.background_display is False
        assert tc.transparent_display is True
        assert tc.rotation == 90
        assert tc.ui_mode == 1
        assert tc.display_mode == 2
        assert tc.overlay_enabled is False
        assert tc.overlay_x == 10
        assert tc.overlay_y == 20
        assert tc.overlay_w == 640
        assert tc.overlay_h == 480
        assert tc.mask_enabled is True
        assert tc.mask_x == 30
        assert tc.mask_y == 40


# ── to_overlay_config() ──


class TestToOverlayConfig:
    """to_overlay_config() delegates to DcParser.to_overlay_config."""

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.to_overlay_config')
    def test_delegates_with_correct_parsed_dict(self, mock_to_overlay):
        mock_to_overlay.return_value = {'cpu_usage': {'x': 10, 'y': 20}}

        dc = DcConfig()
        dc.legacy_elements = {'time': MagicMock()}
        dc.elements = [DisplayElement(mode=0, mode_sub=0, x=10, y=20)]
        dc.custom_text = 'hello'
        dc.flags = {'system_info': True}

        result = dc.to_overlay_config()

        mock_to_overlay.assert_called_once()
        call_args = mock_to_overlay.call_args
        parsed_dict = call_args[0][0]
        assert parsed_dict['elements'] is dc.legacy_elements
        assert parsed_dict['display_elements'] is dc.elements
        assert parsed_dict['custom_text'] == 'hello'
        assert parsed_dict['flags'] is dc.flags
        assert result == {'cpu_usage': {'x': 10, 'y': 20}}


# ── from_overlay_config() ──


class TestFromOverlayConfig:
    """from_overlay_config() creates DcConfig from overlay dict."""

    @patch('trcc.legacy.adapters.infra.dc_writer.overlay_to_theme')
    def test_creates_dc_from_overlay(self, mock_overlay_to_theme):
        elem = DisplayElement(mode=1, mode_sub=0, x=5, y=10)
        fake_tc = ThemeConfig(elements=[elem], overlay_w=800, overlay_h=600)
        mock_overlay_to_theme.return_value = fake_tc

        overlay = {'time': {'x': 5, 'y': 10, 'enabled': True}}
        dc = DcConfig.from_overlay_config(overlay, 800, 600)

        mock_overlay_to_theme.assert_called_once_with(overlay, 800, 600)
        assert dc.elements == [elem]
        assert dc.overlay_w == 800
        assert dc.overlay_h == 600

    @patch('trcc.legacy.adapters.infra.dc_writer.overlay_to_theme')
    def test_explicit_dimensions_passed_through(self, mock_overlay_to_theme):
        fake_tc = ThemeConfig(elements=[], overlay_w=480, overlay_h=320)
        mock_overlay_to_theme.return_value = fake_tc

        DcConfig.from_overlay_config({}, 480, 320)
        mock_overlay_to_theme.assert_called_once_with({}, 480, 320)

    @patch('trcc.legacy.adapters.infra.dc_writer.overlay_to_theme')
    def test_returns_dc_config_instance(self, mock_overlay_to_theme):
        fake_tc = ThemeConfig()
        mock_overlay_to_theme.return_value = fake_tc

        dc = DcConfig.from_overlay_config({}, 320, 320)
        assert isinstance(dc, DcConfig)


# ── to_dict() ──


class TestToDict:
    """to_dict() returns backward-compatible parsed dict."""

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_all_fields_present(self, mock_parse):
        elem = DisplayElement(mode=0, mode_sub=0, x=0, y=0)
        font = FontConfig(name='Arial', size=10.0, style=0, unit=3, charset=0,
                          color_argb=(255, 0, 0, 0))
        legacy = {'time': MagicMock()}
        flags = {'system_info': True}
        display_opts = {'direction': 90}
        mask_sets = {'overlay_enabled': True}

        mock_parse.return_value = _make_parsed_dict(
            version=0x000000DD,
            display_elements=[elem],
            fonts=[font],
            custom_text='test',
            elements=legacy,
            flags=flags,
            display_options=display_opts,
            mask_settings=mask_sets,
        )
        dc = DcConfig('/fake/path.dc')
        d = dc.to_dict()

        assert d['version'] == 0x000000DD
        assert d['elements'] is legacy
        assert d['fonts'] == [font]
        assert d['flags'] is flags
        assert d['display_elements'] == [elem]
        assert d['custom_text'] == 'test'
        assert d['display_options'] is display_opts
        assert d['mask_settings'] is mask_sets

    def test_empty_config_to_dict(self):
        dc = DcConfig()
        d = dc.to_dict()
        assert d['version'] == 0
        assert d['elements'] == {}
        assert d['fonts'] == []
        assert d['flags'] == {}
        assert d['display_elements'] == []
        assert d['custom_text'] == ''
        assert d['display_options'] == {}
        assert d['mask_settings'] == {}


# ── __repr__() ──


class TestRepr:
    """__repr__() shows format, element count, rotation."""

    def test_0xdd_format(self):
        dc = DcConfig()
        dc.version = 0x000000DD  # low byte = 0xDD
        dc.elements = [DisplayElement(mode=0, mode_sub=0, x=0, y=0)] * 3
        dc.rotation = 90
        r = repr(dc)
        assert '0xDD' in r
        assert 'elements=3' in r
        assert 'rotation=90' in r

    def test_0xdc_format(self):
        dc = DcConfig()
        dc.version = 0x000000DC  # low byte = 0xDC
        dc.elements = []
        dc.rotation = 0
        r = repr(dc)
        assert '0xDC' in r
        assert 'elements=0' in r
        assert 'rotation=0' in r

    def test_default_version_shows_0xdc(self):
        """version=0 → low byte 0x00 ≠ 0xDD → shows 0xDC."""
        dc = DcConfig()
        assert '0xDC' in repr(dc)

    def test_repr_is_string(self):
        dc = DcConfig()
        assert repr(dc).startswith('DcConfig(')


# ── Edge cases ──


class TestEdgeCases:
    """Miscellaneous edge cases for full coverage."""

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_empty_parsed_dict(self, mock_parse):
        """Parser returns minimal dict — all fields get defaults."""
        mock_parse.return_value = {}
        dc = DcConfig('/fake/path.dc')
        assert dc.version == 0
        assert dc.fonts == []
        assert dc.elements == []
        assert dc.system_info_enabled is True
        assert dc.background_display is True
        assert dc.overlay_enabled is True

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_overlay_rect_empty_list(self, mock_parse):
        """overlay_rect=[] is falsy — defaults preserved."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'overlay_rect': []},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.overlay_x == 0
        assert dc.overlay_w == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_mask_position_empty_list(self, mock_parse):
        """mask_position=[] is falsy — defaults preserved."""
        mock_parse.return_value = _make_parsed_dict(
            mask_settings={'mask_position': []},
        )
        dc = DcConfig('/fake/path.dc')
        assert dc.mask_x == 0
        assert dc.mask_y == 0

    @patch('trcc.legacy.adapters.infra.dc_config.DcParser.parse')
    def test_system_info_defaults_true_when_missing(self, mock_parse):
        """flags dict without 'system_info' key defaults to True."""
        mock_parse.return_value = _make_parsed_dict(flags={})
        dc = DcConfig('/fake/path.dc')
        assert dc.system_info_enabled is True
