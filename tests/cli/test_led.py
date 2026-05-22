"""Tests for Device (LED mode) + CLI LED wrappers (_led.py).

Fixtures build mock LEDService and inject into Device. Tests verify
device methods return correct result dicts, and CLI wrappers print/exit
correctly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trcc.legacy.core.device import Device
from trcc.legacy.core.models import LEDMode
from trcc.legacy.core.results import LEDResult, OpResult

# =========================================================================
# TestLEDDeviceInit
# =========================================================================

class TestLEDDeviceInit:
    """Construction, connected, status, service properties."""

    def test_default_init_no_svc(self, led_empty):
        assert led_empty._led_svc is None

    def test_init_with_svc(self, mock_led_svc, led):
        assert led._led_svc is mock_led_svc

    def test_connected_false_when_no_svc(self, led_empty):
        assert led_empty.connected is False

    def test_connected_true_when_svc_set(self, led):
        assert led.connected is True

    def test_status_initially_none(self, led):
        assert led.status is None

    def test_status_reflects_init_status(self, led):
        led._init_status = "AX120 (style 1)"
        assert led.status == "AX120 (style 1)"

    @pytest.mark.skip(reason="Phase 9: LEDDevice.service property removed; access via led._led_svc directly")
    def test_service_property_returns_svc(self, mock_led_svc, led):
        assert led.service is mock_led_svc

    @pytest.mark.skip(reason="Phase 9: LEDDevice.service property removed")
    def test_service_property_none_when_disconnected(self, led_empty):
        assert led_empty.service is None


# =========================================================================
# TestLEDDeviceModeResolution
# =========================================================================

class TestLEDDeviceModeResolution:
    """_resolve_mode() — string, enum, int resolution."""

    def test_resolve_string_static(self, led):
        assert led._resolve_mode('static') is LEDMode.STATIC

    def test_resolve_string_breathing(self, led):
        assert led._resolve_mode('breathing') is LEDMode.BREATHING

    def test_resolve_string_colorful(self, led):
        assert led._resolve_mode('colorful') is LEDMode.COLORFUL

    def test_resolve_string_rainbow(self, led):
        assert led._resolve_mode('rainbow') is LEDMode.RAINBOW

    def test_resolve_string_case_insensitive(self, led):
        assert led._resolve_mode('STATIC') is LEDMode.STATIC
        assert led._resolve_mode('Breathing') is LEDMode.BREATHING

    def test_resolve_enum_passthrough(self, led):
        assert led._resolve_mode(LEDMode.STATIC) is LEDMode.STATIC

    def test_resolve_int_value(self, led):
        mode = led._resolve_mode(LEDMode.STATIC.value)
        assert mode is LEDMode.STATIC

    def test_resolve_unknown_string_returns_none(self, led):
        assert led._resolve_mode('fireworks') is None

    def test_resolve_unknown_int_returns_none(self, led):
        assert led._resolve_mode(9999) is None


# =========================================================================
# TestLEDDeviceConnect
# =========================================================================

@pytest.mark.skip(reason="Phase 9: Device(...) is now a typing.Union alias of LCDDevice|LEDDevice; LED-specific connect via Device(led_svc=..., device_type=False) removed. New tests should use trcc_with_led fixture and the LEDDevice.connect() facade.")
class TestLEDDeviceConnect:
    """connect() with mocked detect_devices + LEDService."""

    def test_connect_returns_success_when_already_connected(self, mock_led_svc):
        dev = Device(led_svc=mock_led_svc, device_type=False)
        dev._init_status = "connected"
        result = dev.connect()
        assert result["success"] is True
        assert result["status"] == "connected"

    def test_connect_no_led_device_directly(self):
        """connect() returns failure when no hid_led device found."""
        fake_dev = MagicMock()
        fake_dev.implementation = 'hid_lcd'
        mock_dev_svc = MagicMock()
        mock_dev_svc.devices = [fake_dev]

        dev = Device(device_svc=mock_dev_svc, device_type=False)
        with patch('trcc.legacy.services.LEDService'):
            result = dev.connect()
        assert result["success"] is False
        assert "No LED device" in result["error"]

    def test_connect_empty_device_list(self):
        """connect() returns failure when device list is empty."""
        mock_dev_svc = MagicMock()
        mock_dev_svc.devices = []

        dev = Device(device_svc=mock_dev_svc, device_type=False)
        result = dev.connect()
        assert result["success"] is False
        assert "No LED device" in result["error"]

    def test_connect_success_sets_svc(self):
        """connect() sets _svc and _init_status on success."""
        led_dev = MagicMock()
        led_dev.implementation = 'hid_led'
        led_dev.led_style_id = 1

        fake_svc = MagicMock()
        fake_svc.initialize.return_value = "AX120"

        mock_dev_svc = MagicMock()
        mock_dev_svc.devices = [led_dev]

        dev = Device(
            device_svc=mock_dev_svc,
            led_svc_factory=lambda **kw: fake_svc,
            device_type=False,
        )
        result = dev.connect()

        assert result["success"] is True
        assert dev._led_svc is fake_svc
        assert dev._init_status == "AX120"


# =========================================================================
# TestLEDDeviceValidation
# =========================================================================

class TestLEDDeviceValidation:
    """_validate_zone and _validate_segment bounds checking."""

    def test_validate_zone_valid(self, led):
        assert led._validate_zone(0) is None
        assert led._validate_zone(1) is None
        assert led._validate_zone(2) is None

    def test_validate_zone_negative(self, led):
        err = led._validate_zone(-1)
        assert err is not None
        assert err["success"] is False
        assert "out of range" in err["error"]

    def test_validate_zone_too_high(self, led):
        err = led._validate_zone(3)  # only 0-2 valid (3 zones)
        assert err is not None
        assert err["success"] is False

    def test_validate_zone_empty_list(self, led_no_zones):
        err = led_no_zones._validate_zone(0)
        assert err is not None
        assert "no zones" in err["error"]

    def test_validate_segment_valid(self, led):
        assert led._validate_segment(0) is None
        assert led._validate_segment(3) is None

    def test_validate_segment_negative(self, led):
        err = led._validate_segment(-1)
        assert err is not None
        assert err["success"] is False
        assert "out of range" in err["error"]

    def test_validate_segment_too_high(self, led):
        err = led._validate_segment(4)  # only 0-3 valid (4 segments)
        assert err is not None
        assert err["success"] is False

    def test_validate_segment_empty_list(self, led_no_segments):
        err = led_no_segments._validate_segment(0)
        assert err is not None
        assert "no segments" in err["error"]

    def test_validate_zone_range_message_includes_max(self, led):
        err = led._validate_zone(99)
        assert "2" in err["error"]  # max index is 2 (3 zones)


# =========================================================================
# TestLEDDeviceInternalHelpers
# =========================================================================

class TestLEDDeviceInternalHelpers:
    """_apply_and_send and _send_and_save."""

    def test_apply_and_send_calls_toggle_global_true(self, led, mock_led_svc):
        led._led_apply_and_send()
        mock_led_svc.toggle_global.assert_called_with(True)

    def test_apply_and_send_calls_tick(self, led, mock_led_svc):
        led._led_apply_and_send()
        mock_led_svc.tick.assert_called_once()

    def test_apply_and_send_calls_send_colors(self, led, mock_led_svc):
        colors = mock_led_svc.tick.return_value
        led._led_apply_and_send()
        mock_led_svc.send_colors.assert_called_once_with(colors)

    def test_apply_and_send_calls_save_config(self, led, mock_led_svc):
        led._led_apply_and_send()
        mock_led_svc.save_config.assert_called_once()

    def test_apply_and_send_returns_colors(self, led, mock_led_svc):
        result = led._led_apply_and_send()
        assert result == mock_led_svc.tick.return_value

    def test_send_and_save_calls_send_tick(self, led, mock_led_svc):
        led._led_send_and_save()
        mock_led_svc.send_tick.assert_called_once()

    def test_send_and_save_calls_save_config(self, led, mock_led_svc):
        led._led_send_and_save()
        mock_led_svc.save_config.assert_called_once()


# =========================================================================
# TestLEDDeviceGlobalOps
# =========================================================================

class TestLEDDeviceGlobalOps:
    """set_color, set_mode, set_brightness, off, set_sensor_source."""

    def test_set_color_success(self, led):
        result = led.set_color(255, 0, 0)
        assert result["success"] is True
        assert "ff0000" in result["message"]

    def test_set_color_calls_set_mode_static(self, led, mock_led_svc):
        led.set_color(0, 255, 0)
        mock_led_svc.set_mode.assert_called_once_with(LEDMode.STATIC)

    def test_set_color_calls_set_color_on_svc(self, led, mock_led_svc):
        led.set_color(10, 20, 30)
        mock_led_svc.set_color.assert_called_once_with(10, 20, 30)

    def test_set_color_returns_colors(self, led, mock_led_svc):
        result = led.set_color(255, 0, 0)
        assert result["colors"] == mock_led_svc.tick.return_value

    def test_set_mode_static_not_animated(self, led):
        result = led.set_mode('static')
        assert result["success"] is True
        assert result["animated"] is False

    def test_set_mode_breathing_is_animated(self, led):
        result = led.set_mode('breathing')
        assert result["success"] is True
        assert result["animated"] is True

    def test_set_mode_colorful_is_animated(self, led):
        result = led.set_mode('colorful')
        assert result["success"] is True
        assert result["animated"] is True

    def test_set_mode_rainbow_is_animated(self, led):
        result = led.set_mode('rainbow')
        assert result["success"] is True
        assert result["animated"] is True

    def test_set_mode_unknown_fails(self, led):
        result = led.set_mode('fireworks')
        assert result["success"] is False
        assert "fireworks" in result["error"]
        assert "available" in result

    def test_set_mode_unknown_lists_available(self, led):
        result = led.set_mode('bad_mode')
        avail = result["available"]
        assert 'static' in avail
        assert 'breathing' in avail
        assert 'colorful' in avail
        assert 'rainbow' in avail

    def test_set_mode_case_insensitive(self, led):
        result = led.set_mode('STATIC')
        assert result["success"] is True

    def test_set_mode_calls_svc_set_mode(self, led, mock_led_svc):
        led.set_mode('static')
        mock_led_svc.set_mode.assert_called_once_with(LEDMode.STATIC)

    def test_set_brightness_success(self, led):
        result = led.set_brightness(75)
        assert result["success"] is True
        assert "75%" in result["message"]

    def test_set_brightness_zero(self, led):
        result = led.set_brightness(0)
        assert result["success"] is True

    def test_set_brightness_hundred(self, led):
        result = led.set_brightness(100)
        assert result["success"] is True

    def test_set_brightness_negative_fails(self, led):
        result = led.set_brightness(-1)
        assert result["success"] is False
        assert "0-100" in result["error"]

    def test_set_brightness_over_100_fails(self, led):
        result = led.set_brightness(101)
        assert result["success"] is False

    def test_set_brightness_calls_svc(self, led, mock_led_svc):
        led.set_brightness(50)
        mock_led_svc.set_brightness.assert_called_once_with(50)

    @pytest.mark.skip(reason="Phase 9: LEDDevice.off() removed; use update_global_on(False) directly")
    def test_off_success(self, led, mock_led_svc):
        result = led.off()
        assert result["success"] is True
        assert "off" in result["message"].lower()

    @pytest.mark.skip(reason="Phase 9: LEDDevice.off() removed")
    def test_off_calls_toggle_global_false(self, led, mock_led_svc):
        led.off()
        mock_led_svc.toggle_global.assert_called_with(False)

    @pytest.mark.skip(reason="Phase 9: LEDDevice.off() removed")
    def test_off_calls_send_tick(self, led, mock_led_svc):
        led.off()
        mock_led_svc.send_tick.assert_called_once()

    def test_set_sensor_source_cpu(self, led, mock_led_svc):
        result = led.set_sensor_source('cpu')
        assert result["success"] is True
        assert "CPU" in result["message"]

    def test_set_sensor_source_gpu(self, led, mock_led_svc):
        result = led.set_sensor_source('gpu')
        assert result["success"] is True
        assert "GPU" in result["message"]

    def test_set_sensor_source_case_insensitive(self, led, mock_led_svc):
        result = led.set_sensor_source('CPU')
        assert result["success"] is True

    def test_set_sensor_source_invalid(self, led):
        result = led.set_sensor_source('tpu')
        assert result["success"] is False
        assert "cpu" in result["error"].lower() or "gpu" in result["error"].lower()

    def test_set_sensor_source_calls_svc(self, led, mock_led_svc):
        led.set_sensor_source('gpu')
        mock_led_svc.set_sensor_source.assert_called_once_with('gpu')

    def test_set_sensor_source_saves_config(self, led, mock_led_svc):
        led.set_sensor_source('cpu')
        mock_led_svc.save_config.assert_called_once()


# =========================================================================
# TestLEDDeviceZoneOps
# =========================================================================

class TestLEDDeviceZoneOps:
    """set_zone_color, set_zone_mode, set_zone_brightness, toggle_zone, set_zone_sync."""

    def test_set_zone_color_valid(self, led, mock_led_svc):
        result = led.set_zone_color(0, 255, 128, 0)
        assert result["success"] is True
        assert "Zone 0" in result["message"]
        assert "ff8000" in result["message"]

    def test_set_zone_color_invalid_zone(self, led):
        result = led.set_zone_color(99, 255, 0, 0)
        assert result["success"] is False

    def test_set_zone_color_negative_zone(self, led):
        result = led.set_zone_color(-1, 255, 0, 0)
        assert result["success"] is False

    def test_set_zone_color_calls_svc(self, led, mock_led_svc):
        led.set_zone_color(1, 10, 20, 30)
        mock_led_svc.set_zone_color.assert_called_once_with(1, 10, 20, 30)

    def test_set_zone_mode_valid(self, led, mock_led_svc):
        result = led.set_zone_mode(0, 'static')
        assert result["success"] is True
        assert "Zone 0" in result["message"]

    def test_set_zone_mode_invalid_zone(self, led):
        result = led.set_zone_mode(10, 'static')
        assert result["success"] is False

    def test_set_zone_mode_unknown_mode(self, led):
        result = led.set_zone_mode(0, 'laser')
        assert result["success"] is False
        assert "laser" in result["error"]

    def test_set_zone_mode_calls_svc(self, led, mock_led_svc):
        led.set_zone_mode(2, 'breathing')
        mock_led_svc.set_zone_mode.assert_called_once_with(2, LEDMode.BREATHING)

    def test_set_zone_brightness_valid(self, led, mock_led_svc):
        result = led.set_zone_brightness(0, 80)
        assert result["success"] is True
        assert "Zone 0" in result["message"]
        assert "80%" in result["message"]

    def test_set_zone_brightness_invalid_zone(self, led):
        result = led.set_zone_brightness(99, 50)
        assert result["success"] is False

    def test_set_zone_brightness_too_high(self, led):
        result = led.set_zone_brightness(0, 101)
        assert result["success"] is False
        assert "0-100" in result["error"]

    def test_set_zone_brightness_negative(self, led):
        result = led.set_zone_brightness(0, -5)
        assert result["success"] is False

    def test_set_zone_brightness_calls_svc(self, led, mock_led_svc):
        led.set_zone_brightness(1, 50)
        mock_led_svc.set_zone_brightness.assert_called_once_with(1, 50)

    def test_toggle_zone_on(self, led, mock_led_svc):
        result = led.toggle_zone(0, True)
        assert result["success"] is True
        assert "ON" in result["message"]

    def test_toggle_zone_off(self, led, mock_led_svc):
        result = led.toggle_zone(1, False)
        assert result["success"] is True
        assert "OFF" in result["message"]

    def test_toggle_zone_invalid_zone(self, led):
        result = led.toggle_zone(99, True)
        assert result["success"] is False

    def test_toggle_zone_calls_svc(self, led, mock_led_svc):
        led.toggle_zone(2, True)
        mock_led_svc.toggle_zone.assert_called_once_with(2, True)

    def test_set_zone_sync_enabled(self, led, mock_led_svc):
        result = led.set_zone_sync(True)
        assert result["success"] is True
        assert "enabled" in result["message"]

    def test_set_zone_sync_disabled(self, led, mock_led_svc):
        result = led.set_zone_sync(False)
        assert result["success"] is True
        assert "disabled" in result["message"]

    def test_set_zone_sync_with_interval(self, led, mock_led_svc):
        result = led.set_zone_sync(True, interval=5)
        assert result["success"] is True
        mock_led_svc.set_zone_sync_interval.assert_called_once_with(5)

    def test_set_zone_sync_no_interval_skips_set_interval(self, led, mock_led_svc):
        led.set_zone_sync(True, interval=None)
        mock_led_svc.set_zone_sync_interval.assert_not_called()

    def test_set_zone_sync_calls_svc(self, led, mock_led_svc):
        led.set_zone_sync(True)
        mock_led_svc.set_zone_sync.assert_called_once_with(True)


# =========================================================================
# TestLEDDeviceSegmentOps
# =========================================================================

class TestLEDDeviceSegmentOps:
    """toggle_segment, set_clock_format, set_temp_unit."""

    def test_toggle_segment_on(self, led, mock_led_svc):
        result = led.toggle_segment(0, True)
        assert result["success"] is True
        assert "ON" in result["message"]

    def test_toggle_segment_off(self, led, mock_led_svc):
        result = led.toggle_segment(1, False)
        assert result["success"] is True
        assert "OFF" in result["message"]

    def test_toggle_segment_invalid_index(self, led):
        result = led.toggle_segment(99, True)
        assert result["success"] is False

    def test_toggle_segment_negative_index(self, led):
        result = led.toggle_segment(-1, True)
        assert result["success"] is False

    def test_toggle_segment_calls_svc(self, led, mock_led_svc):
        led.toggle_segment(2, False)
        mock_led_svc.toggle_segment.assert_called_once_with(2, False)

    def test_toggle_segment_empty_list(self, led_no_segments):
        result = led_no_segments.toggle_segment(0, True)
        assert result["success"] is False
        assert "no segments" in result["error"]

    def test_set_clock_format_24h(self, led, mock_led_svc):
        result = led.set_clock_format(True)
        assert result["success"] is True
        assert "24h" in result["message"]

    def test_set_clock_format_12h(self, led, mock_led_svc):
        result = led.set_clock_format(False)
        assert result["success"] is True
        assert "12h" in result["message"]

    def test_set_clock_format_calls_svc(self, led, mock_led_svc):
        led.set_clock_format(True)
        mock_led_svc.set_clock_format.assert_called_once_with(True)

    @pytest.mark.parametrize("unit,expected_str", [(0, "C"), (1, "F")])
    def test_set_temp_unit(self, led, mock_led_svc, unit, expected_str):
        result = led.set_temp_unit(unit)
        assert result == {"success": True, "message": f"Temperature unit set to {expected_str}"}
        mock_led_svc.set_seg_temp_unit.assert_called_with(expected_str)


# =========================================================================
# TestLEDDeviceTick
# =========================================================================

class TestLEDDeviceTick:
    """tick() — advance one animation frame."""

    def test_tick_returns_colors(self, led, mock_led_svc):
        result = led.tick()
        assert "colors" in result
        assert result["colors"] == mock_led_svc.tick.return_value

    def test_tick_calls_svc_tick(self, led, mock_led_svc):
        led.tick()
        mock_led_svc.tick.assert_called_once()

    def test_tick_calls_send_colors(self, led, mock_led_svc):
        colors = mock_led_svc.tick.return_value
        led.tick()
        mock_led_svc.send_colors.assert_called_once_with(colors)

    def test_tick_returns_display_colors(self, led, mock_led_svc):
        result = led.tick()
        assert "display_colors" in result



# =========================================================================
# CLI tests — exercise the Typer boundary with Trcc mocked.
#
# Strategy: patch `trcc.ui.cli._boot.trcc` to return a fake that exposes
# `.led.<cmd>()` returning a real LEDResult/OpResult. This exercises the
# _led.py wrappers + typer registrations without touching USB.
# =========================================================================

def _fake_trcc(**led_returns):
    """Build a mock trcc() factory whose `.led.<name>` returns the given Result.

    Any led method not overridden defaults to OpResult(success=True).
    """
    from unittest.mock import MagicMock
    app = MagicMock()
    app.led = MagicMock()
    for name, result in led_returns.items():
        getattr(app.led, name).return_value = result
    default_op = OpResult(success=True, message='ok')
    default_led = LEDResult(success=True, message='ok', display_colors=[(1, 2, 3)])
    for attr in ('set_color', 'set_mode', 'set_brightness',
                 'set_zone_color', 'set_zone_mode', 'set_zone_brightness',
                 'set_sensor_source'):
        if attr not in led_returns:
            getattr(app.led, attr).return_value = default_led
    for attr in ('toggle', 'toggle_segment', 'set_zone_sync',
                 'select_zone', 'set_clock_format', 'set_week_start',
                 'set_memory_ratio', 'set_disk_index', 'set_test_mode'):
        if attr not in led_returns:
            getattr(app.led, attr).return_value = default_op
    app.control_center = MagicMock()
    if 'set_temp_unit' not in led_returns:
        app.control_center.set_temp_unit.return_value = default_op
    return lambda: app


class TestLEDCLISuccess:
    """Every LED command exits 0 when Trcc.led returns success."""

    def test_color(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-color', 'ff0000'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_color_with_hash_prefix(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-color', '#00ff00'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_color_with_preview(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-color', 'ff0000', '--preview'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_mode(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-mode', 'static'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_brightness(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-brightness', '60'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_off(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-off'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_sensor_cpu(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-sensor', 'cpu'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_zone_color(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-color', '0', 'ff0000'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_zone_mode(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-mode', '0', 'static'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_zone_brightness(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-brightness', '0', '75'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_zone_toggle_on(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-toggle', '1', 'true'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_zone_sync_with_interval(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-sync', 'true', '--interval', '3'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_segment(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-segment', '0', 'true'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_clock_24h(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-clock', 'true'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0

    def test_temp_unit_F(self, monkeypatch):
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', _fake_trcc())
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-temp-unit', 'F'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 0


class TestLEDCLIFailures:
    """Validation and error handling."""

    def test_color_invalid_hex(self):
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-color', 'zzzzzz'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1
        assert 'Invalid hex' in (r.output + (getattr(r, 'stderr', '') or ''))

    def test_color_too_short(self):
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-color', 'ff00'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1

    def test_zone_color_invalid_hex(self):
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-color', '0', 'xyz'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1

    def test_mode_rejected_by_trcc(self, monkeypatch):
        """Trcc returns failure → CLI exits 1."""
        fake = _fake_trcc(set_mode=LEDResult(
            success=False, error="Unknown mode 'banana'"))
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake)
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-mode', 'banana'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1

    def test_zone_brightness_rejected(self, monkeypatch):
        fake = _fake_trcc(set_brightness=LEDResult(
            success=False, error='Brightness 0-100'))
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake)
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-zone-brightness', '0', '999'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1

    def test_sensor_source_invalid(self, monkeypatch):
        fake = _fake_trcc(set_sensor_source=OpResult(
            success=False, error="Source must be 'cpu' or 'gpu'"))
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake)
        r = CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                               ['led-sensor', 'fan'],
                               standalone_mode=False, catch_exceptions=False)
        assert r.return_value == 1


class TestLEDCLIInvocation:
    """Assert the CLI wrappers forward the correct positional args to Trcc."""

    def test_color_forwards_rgb(self, monkeypatch):
        fake_factory = _fake_trcc()
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake_factory)
        CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                           ['led-color', 'ff8000'],
                           standalone_mode=False, catch_exceptions=False)
        app = fake_factory()
        app.led.set_color.assert_called_with(0, 0xff, 0x80, 0x00)

    def test_zone_color_forwards_zone_kwarg(self, monkeypatch):
        fake_factory = _fake_trcc()
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake_factory)
        CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                           ['led-zone-color', '2', '00ff00'],
                           standalone_mode=False, catch_exceptions=False)
        app = fake_factory()
        app.led.set_color.assert_called_with(0, 0, 0xff, 0, zone=2)

    def test_off_calls_toggle_false(self, monkeypatch):
        fake_factory = _fake_trcc()
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake_factory)
        CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                           ['led-off'],
                           standalone_mode=False, catch_exceptions=False)
        app = fake_factory()
        app.led.toggle.assert_called_with(0, False)

    def test_temp_unit_calls_control_center(self, monkeypatch):
        fake_factory = _fake_trcc()
        monkeypatch.setattr('trcc.legacy.ui.cli._led.trcc', fake_factory)
        CliRunner().invoke(__import__('trcc.legacy.ui.cli', fromlist=['app']).app,
                           ['led-temp-unit', 'F'],
                           standalone_mode=False, catch_exceptions=False)
        app = fake_factory()
        app.control_center.set_temp_unit.assert_called_with('F')
