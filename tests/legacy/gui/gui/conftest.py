"""GUI layer test fixtures.

Fixtures inherit from each other following the same DI chain as production code:

    mock_lcd_device ──┐
    mock_lcd_widgets ─┼→ lcd_handler
    make_timer_fn ────┘       └→ make_lcd_handler (factory for custom overrides)

    mock_sensor_enumerator → sysinfo (via test class fixture)
    make_panel_config → make_custom_panel_config
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── TRCCApp shell ─────────────────────────────────────────────────────────────

@pytest.fixture()
def bare_trcc_app(qapp):
    """Yield a bare TRCCApp instance with __init__ skipped.

    Resets the singleton before and after — safe for parallel test workers.
    """
    from trcc.legacy.ui.gui.trcc_app import TRCCApp

    TRCCApp._instance = None
    with patch.object(TRCCApp, '__init__', lambda self, *a, **kw: None):
        inst = TRCCApp.__new__(TRCCApp)
    yield inst
    TRCCApp._instance = None


# ── LCD handler chain ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_lcd_widgets():
    """Dict of mock widgets matching LCDHandler constructor expectations."""
    return {
        'preview': MagicMock(),
        'image_cut': MagicMock(),
        'video_cut': MagicMock(),
        'theme_setting': MagicMock(),
        'theme_local': MagicMock(),
        'theme_web': MagicMock(),
        'theme_mask': MagicMock(),
        'rotation_combo': MagicMock(),
    }


@pytest.fixture
def make_timer_fn():
    """Factory that produces a make_timer callable returning mock QTimers."""
    def _outer():
        def make_timer(callback, single_shot: bool = False) -> MagicMock:
            t = MagicMock(spec=QTimer)
            t._callback = callback
            return t
        return make_timer
    return _outer


@pytest.fixture
def mock_lcd_device():
    """Fully configured mock LCDDevice.

    All method return values are pre-set so LCDHandler never raises when
    routing commands through the bus.
    """
    from trcc.legacy.core.models import ALL_DEVICES, FBL_PROFILES
    info = ALL_DEVICES[(0x0402, 0x3922)]
    w, h = FBL_PROFILES[info.fbl].resolution

    lcd = MagicMock()
    lcd.lcd_size = (w, h)
    lcd.resolution = (w, h)
    lcd.native_resolution = (w, h)
    lcd.canvas_size = (w, h)
    lcd.canvas_size = (w, h)
    lcd.is_rotated.return_value = False
    lcd.has_portrait_themes = False
    lcd.rotation = 0
    lcd.theme_dir = None
    lcd.local_dir = None
    lcd.web_dir = None
    lcd.masks_dir = None
    lcd.user_theme_dir = None
    lcd.user_masks_dir = None
    lcd.connected = True
    lcd.auto_send = True
    lcd.current_theme_path = None
    lcd.enabled = False
    lcd.playing = False
    lcd.has_frames = False
    lcd.interval = 33
    lcd.last_metrics = None
    lcd.has_changed.return_value = False
    lcd.render.return_value = {'image': MagicMock()}
    lcd.load_overlay_config_from_dir.return_value = None
    lcd.set_brightness.return_value = {'success': True, 'message': 'OK'}
    lcd.set_rotation.return_value = {'success': True, 'message': 'OK'}
    lcd.set_split_mode.return_value = {'success': True, 'message': 'OK'}
    lcd.set_resolution.return_value = {'success': True}
    lcd.select.return_value = {'image': MagicMock(), 'is_animated': False}
    lcd.save.return_value = {'success': True, 'message': 'Saved'}
    lcd.export_config.return_value = {'success': True, 'message': 'Exported'}
    lcd.import_config.return_value = {'success': True, 'message': 'Imported'}
    lcd.set_config.return_value = {'success': True, 'message': 'Config set'}
    lcd.enable.return_value = {'success': True, 'enabled': True}
    lcd.enable_overlay.return_value = {'success': True}
    lcd.pause.return_value = {'state': 'paused'}
    lcd.stop.return_value = {'success': True}
    lcd.seek.return_value = {'success': True}
    lcd.set_fit_mode.return_value = {'success': True, 'image': None}
    lcd.rebuild_video_cache.return_value = {'success': True}
    lcd.set_flash_index.return_value = {'success': True}
    lcd.set_mask_position.return_value = {'success': True}
    lcd.render_and_send.return_value = {'success': True, 'image': MagicMock()}
    lcd.send.return_value = None
    lcd.load_mask_standalone.return_value = {'success': True, 'image': None}
    lcd.restore_last_theme.return_value = {'success': False, 'error': 'No saved theme'}
    lcd.device_service = MagicMock()
    # DisplayService mock — tracks resolution so LCDHandler reads correct state
    display_svc = MagicMock()
    display_svc.lcd_width = w
    display_svc.lcd_height = h
    display_svc.lcd_size = (w, h)
    display_svc.canvas_size = (w, h)
    display_svc.effective_resolution = (w, h)
    display_svc.canvas_size = (w, h)
    display_svc.native_resolution = (w, h)
    display_svc.canvas_size = (w, h)
    display_svc.rotation = 0
    display_svc.is_rotated.return_value = False
    display_svc.has_portrait_themes = False
    display_svc.theme_dir = None
    display_svc.local_dir = None
    display_svc.web_dir = None
    display_svc.masks_dir = None
    display_svc.user_theme_dir = None
    display_svc.user_masks_dir = None

    def _track_resolution(width, height):
        display_svc.lcd_width = width
        display_svc.lcd_height = height
        display_svc.lcd_size = (width, height)
        display_svc.canvas_size = (width, height)
        display_svc.effective_resolution = (width, height)
        display_svc.canvas_size = (width, height)
        display_svc.canvas_size = (width, height)
        display_svc.native_resolution = (width, height)
    display_svc.set_resolution.side_effect = _track_resolution

    lcd._display_svc = display_svc

    return lcd


@pytest.fixture
def lcd_handler(mock_lcd_device, mock_lcd_widgets, make_timer_fn, tmp_path):
    """Default LCDHandler with all dependencies wired from fixtures."""
    from trcc.legacy.ui.gui.lcd_handler import LCDHandler
    return LCDHandler(
        lcd=mock_lcd_device,
        widgets=mock_lcd_widgets,
        make_timer=make_timer_fn(),
        data_dir=tmp_path,
    )


@pytest.fixture
def make_lcd_handler(mock_lcd_device, mock_lcd_widgets, make_timer_fn, tmp_path):
    """Factory: create LCDHandler with optional overrides.

    Tests that need a non-default device or timer fn pass them as kwargs:
        h = make_lcd_handler(lcd=custom_lcd)
        h = make_lcd_handler(make_timer=tracking_timer)
    """
    from trcc.legacy.ui.gui.lcd_handler import LCDHandler

    def _factory(**overrides) -> LCDHandler:
        lcd = overrides.pop('lcd', mock_lcd_device)
        kw = {
            'lcd': lcd,
            'widgets': mock_lcd_widgets,
            'make_timer': make_timer_fn(),
            'data_dir': tmp_path,
        }
        kw.update(overrides)
        return LCDHandler(**kw)
    return _factory


# ── System info / panel fixtures ──────────────────────────────────────────────

@pytest.fixture
def make_panel_config():
    """Factory: build a PanelConfig with sensible defaults."""
    from trcc.legacy.adapters.system.config import PanelConfig, SensorBinding

    def _factory(
        category_id: int = 1,
        name: str = "CPU",
        sensors: list[SensorBinding] | None = None,
    ) -> PanelConfig:
        if sensors is None:
            sensors = [
                SensorBinding("TEMP", "hwmon:coretemp:temp1", "°C"),
                SensorBinding("Usage", "psutil:cpu_percent", "%"),
                SensorBinding("Clock", "psutil:cpu_freq", "MHz"),
                SensorBinding("Power", "rapl:package-0", "W"),
            ]
        return PanelConfig(category_id=category_id, name=name, sensors=sensors)
    return _factory


@pytest.fixture
def make_custom_panel_config(make_panel_config):
    """Factory: build a custom (category_id=0) PanelConfig.

    Inherits make_panel_config so sensor defaults are consistent.
    """
    from trcc.legacy.adapters.system.config import SensorBinding

    def _factory(name: str = "Custom"):
        return make_panel_config(
            category_id=0,
            name=name,
            sensors=[
                SensorBinding("Sensor 1", "", ""),
                SensorBinding("Sensor 2", "", ""),
                SensorBinding("Sensor 3", "", ""),
                SensorBinding("Sensor 4", "", ""),
            ],
        )
    return _factory


@pytest.fixture
def mock_sensor_enumerator():
    """Mock SensorEnumerator that returns empty results."""
    enum = MagicMock()
    enum.discover.return_value = []
    enum.read_all.return_value = {}
    return enum


@pytest.fixture
def make_led_state():
    """Factory: build a mock LED state object."""
    from trcc.legacy.core.models import LEDMode, LEDZoneState

    def _factory(
        *,
        zones: list[LEDZoneState] | None = None,
        mode: LEDMode = LEDMode.STATIC,
        color: tuple[int, int, int] = (255, 0, 0),
        brightness: int = 65,
        global_on: bool = True,
        memory_ratio: int = 1,
        zone_sync: bool = False,
        zone_sync_zones: list[bool] | None = None,
        zone_sync_interval: int = 13,
    ) -> MagicMock:
        state = MagicMock()
        state.zones = zones or []
        state.mode = mode
        state.color = color
        state.brightness = brightness
        state.global_on = global_on
        state.memory_ratio = memory_ratio
        state.segment_on = [True] * 10
        state.zone_sync = zone_sync
        state.zone_sync_zones = zone_sync_zones or []
        state.zone_sync_interval = zone_sync_interval
        return state
    return _factory


# ── Assets patching ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_assets():
    """Patch Assets across common GUI modules to avoid filesystem I/O.

    Yields the mock Assets class so tests can configure return values.
    """
    defaults = {
        "get": MagicMock(return_value=None),
        "exists": MagicMock(return_value=False),
        "load_pixmap": MagicMock(return_value=QPixmap()),
        "get_localized": MagicMock(return_value="fake"),
    }
    modules = [
        "trcc.legacy.ui.gui.uc_color_wheel.Assets",
        "trcc.legacy.ui.gui.uc_screen_led.Assets",
        "trcc.legacy.ui.gui.uc_led_control.Assets",
        "trcc.legacy.ui.gui.uc_system_info.Assets",
        "trcc.legacy.ui.gui.lcd_handler.Assets",
    ]
    patches = [patch(m, **defaults) for m in modules]
    mocks = [p.start() for p in patches]
    yield mocks[0]
    for p in patches:
        p.stop()
