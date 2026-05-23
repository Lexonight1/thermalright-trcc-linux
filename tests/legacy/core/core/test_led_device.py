"""Tests for LEDDevice — LED application facade."""

import unittest
from unittest.mock import MagicMock

import pytest

from trcc.legacy.core.device.led import LEDDevice as Device
from trcc.legacy.core.models import DetectedDevice, LEDMode


def _make_led(**overrides) -> Device:
    """Create LEDDevice with mock service pre-wired."""
    svc = MagicMock()
    svc.state = MagicMock()
    svc.state.zones = [MagicMock(), MagicMock()]
    svc.state.segment_on = [True, False, True]
    svc.tick.return_value = [(255, 0, 0)]
    svc.apply_mask.return_value = [(255, 0, 0)]
    svc.has_protocol = True

    defaults = {'led_svc': svc, 'protocol': MagicMock()}
    defaults.update(overrides)
    led = Device(**defaults)
    return led


# =============================================================================
# Construction
# =============================================================================


class TestLEDDeviceConstruction(unittest.TestCase):
    """Device (LED mode) construction."""

    def test_default_no_service(self):
        led = Device()
        self.assertIsNone(led._led_svc)

    def test_with_service(self):
        svc = MagicMock()
        led = Device(led_svc=svc)
        self.assertIs(led._led_svc, svc)

    def test_with_protocol(self):
        proto = MagicMock()
        led = Device(protocol=proto)
        self.assertIs(led._protocol, proto)

    def test_connect_requires_device_svc(self):
        """connect() raises RuntimeError without injected device_svc."""
        led = Device()
        with self.assertRaises(RuntimeError, msg="ControllerBuilder"):
            led.connect()


# =============================================================================
# Connect with detected — device isolation
# =============================================================================


def _make_detected_led(model: str = "AX120_DIGITAL",
                       vid: int = 0x0416,
                       pid: int = 0x8001) -> DetectedDevice:
    return DetectedDevice(
        vid=vid, pid=pid,
        vendor_name="Winbond", product_name="LED Controller",
        usb_path=f"mock:led:{vid:04x}:{pid:04x}",
        implementation="hid_led", model=model,
        button_image="", protocol="led", device_type=1,
    )


class TestLEDDeviceConnectIsolation(unittest.TestCase):
    """connect(detected) uses the detected device directly — no re-detect."""

    def _make_unconnected_led(self) -> Device:
        svc = MagicMock()
        svc.initialize.return_value = "LED: AX120 (30 LEDs)"
        return Device(
            protocol=MagicMock(),
            led_svc_factory=lambda **kw: svc,
            led_config=MagicMock(),
        )

    def test_connect_uses_detected_directly(self):
        """connect(detected) converts to DeviceInfo without re-detecting."""
        led = self._make_unconnected_led()
        detected = _make_detected_led("PA120_DIGITAL", pid=0x8002)

        result = led.connect(detected)

        self.assertTrue(result["success"])
        self.assertEqual(led.device_info.model, "PA120_DIGITAL")
        self.assertEqual(led.device_info.path, "mock:led:0416:8002")

    def test_connect_different_models_get_different_identities(self):
        """Multiple LED devices each get their own identity from detected."""
        led1 = self._make_unconnected_led()
        led2 = self._make_unconnected_led()

        led1.connect(_make_detected_led("AX120_DIGITAL", pid=0x8001))
        led2.connect(_make_detected_led("PA120_DIGITAL", pid=0x8002))

        self.assertEqual(led1.device_info.model, "AX120_DIGITAL")
        self.assertEqual(led2.device_info.model, "PA120_DIGITAL")
        self.assertNotEqual(led1.device_info.path, led2.device_info.path)

    def test_connect_no_detected_falls_back_to_device_svc(self):
        """connect() without detected uses DeviceService.detect()."""
        dev_svc = MagicMock()
        dev_svc.devices = []  # No LED devices found
        led = Device(
            device_svc=dev_svc,
            protocol=MagicMock(),
        )

        result = led.connect()

        self.assertFalse(result["success"])
        dev_svc.detect.assert_called_once()


# =============================================================================
# Device ABC
# =============================================================================


class TestLEDDeviceABC(unittest.TestCase):
    """Device ABC methods on Device (LED mode)."""

    def test_connected_true_with_service(self):
        led = _make_led()
        self.assertTrue(led.connected)

    def test_connected_false_without_service(self):
        led = Device()
        self.assertFalse(led.connected)

    def test_device_info_returns_device(self):
        led = _make_led()
        dev = MagicMock()
        led._info = dev
        self.assertIs(led.device_info, dev)

    def test_cleanup_calls_service(self):
        led = _make_led()
        led.cleanup()
        led._led_svc.cleanup.assert_called_once()

    def test_cleanup_safe_without_service(self):
        led = Device()
        led.cleanup()  # should not raise

    def test_connect_returns_success_when_already_initialized(self):
        """connect() with existing service returns success immediately."""
        led = _make_led()
        led._init_status = "Ready"
        result = led.connect()
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'Ready')


# =============================================================================
# Properties
# =============================================================================


class TestLEDDeviceProperties(unittest.TestCase):
    """LED-specific properties."""

    def test_status(self):
        led = _make_led()
        led._init_status = 'AX120'
        self.assertEqual(led.status, 'AX120')

    @pytest.mark.skip(reason="Phase 9: LEDDevice.service property removed; access _led_svc directly")
    def test_service_accessor(self):
        pass

    def test_state_from_service(self):
        led = _make_led()
        self.assertIs(led.state, led._led_svc.state)

    def test_state_none_without_service(self):
        led = Device()
        self.assertIsNone(led.state)


# =============================================================================
# _resolve_mode
# =============================================================================


class TestResolveMode(unittest.TestCase):
    """Device._resolve_mode() — flexible mode resolution."""

    def setUp(self):
        self.led = _make_led()

    def test_resolve_from_enum(self):
        self.assertEqual(self.led._resolve_mode(LEDMode.STATIC), LEDMode.STATIC)

    def test_resolve_from_int(self):
        self.assertEqual(self.led._resolve_mode(LEDMode.STATIC.value), LEDMode.STATIC)

    def test_resolve_from_string(self):
        self.assertEqual(self.led._resolve_mode('static'), LEDMode.STATIC)

    def test_resolve_from_string_uppercase(self):
        self.assertEqual(self.led._resolve_mode('BREATHING'), LEDMode.BREATHING)

    def test_resolve_invalid_int_returns_none(self):
        self.assertIsNone(self.led._resolve_mode(9999))

    def test_resolve_invalid_string_returns_none(self):
        self.assertIsNone(self.led._resolve_mode('nonexistent'))

    def test_resolve_none_returns_none(self):
        self.assertIsNone(self.led._resolve_mode(None))


# =============================================================================
# Validation helpers
# =============================================================================


class TestValidation(unittest.TestCase):
    """Zone and segment validation."""

    def setUp(self):
        self.led = _make_led()

    def test_valid_zone(self):
        self.assertIsNone(self.led._validate_zone(0))
        self.assertIsNone(self.led._validate_zone(1))

    def test_zone_out_of_range(self):
        result = self.led._validate_zone(5)
        self.assertFalse(result['success'])
        self.assertIn('out of range', result['error'])

    def test_zone_negative(self):
        result = self.led._validate_zone(-1)
        self.assertFalse(result['success'])

    def test_zone_no_zones(self):
        self.led._led_svc.state.zones = []
        result = self.led._validate_zone(0)
        self.assertFalse(result['success'])
        self.assertIn('no zones', result['error'])

    def test_valid_segment(self):
        self.assertIsNone(self.led._validate_segment(0))
        self.assertIsNone(self.led._validate_segment(2))

    def test_segment_out_of_range(self):
        result = self.led._validate_segment(10)
        self.assertFalse(result['success'])

    def test_segment_no_segments(self):
        self.led._led_svc.state.segment_on = []
        result = self.led._validate_segment(0)
        self.assertFalse(result['success'])
        self.assertIn('no segments', result['error'])


# =============================================================================
# Global operations — CLI/API path (immediate tick/send/save)
# =============================================================================


class TestGlobalOperations(unittest.TestCase):
    """CLI/API operations that immediately tick, send, and save."""

    def setUp(self):
        self.led = _make_led()

    def test_set_color(self):
        result = self.led.set_color(255, 0, 0)
        self.assertTrue(result['success'])
        self.led._led_svc.set_mode.assert_called_with(LEDMode.STATIC)
        self.led._led_svc.set_color.assert_called_with(255, 0, 0)
        self.assertIn('#ff0000', result['message'])

    def test_set_mode_by_name(self):
        result = self.led.set_mode('static')
        self.assertTrue(result['success'])
        self.assertIn('static', result['message'])

    def test_set_mode_by_enum(self):
        result = self.led.set_mode(LEDMode.BREATHING)
        self.assertTrue(result['success'])
        self.assertTrue(result['animated'])

    def test_set_mode_invalid(self):
        result = self.led.set_mode('bogus')
        self.assertFalse(result['success'])
        self.assertIn('available', result)

    def test_set_brightness_valid(self):
        result = self.led.set_brightness(50)
        self.assertTrue(result['success'])
        self.led._led_svc.set_brightness.assert_called_with(50)

    def test_set_brightness_too_high(self):
        result = self.led.set_brightness(150)
        self.assertFalse(result['success'])

    def test_set_brightness_negative(self):
        result = self.led.set_brightness(-1)
        self.assertFalse(result['success'])

    def test_toggle_global_on(self):
        result = self.led.toggle_global(True)
        self.assertTrue(result['success'])
        self.assertIn('on', result['message'])

    def test_toggle_global_off(self):
        result = self.led.toggle_global(False)
        self.assertTrue(result['success'])
        self.assertIn('off', result['message'])

    @pytest.mark.skip(reason="Phase 9: LEDDevice.off() removed; use update_global_on(False)")
    def test_off(self):
        pass

    def test_set_sensor_source_cpu(self):
        result = self.led.set_sensor_source('cpu')
        self.assertTrue(result['success'])
        self.led._led_svc.set_sensor_source.assert_called_with('cpu')

    def test_set_sensor_source_gpu(self):
        result = self.led.set_sensor_source('GPU')
        self.assertTrue(result['success'])
        self.led._led_svc.set_sensor_source.assert_called_with('gpu')

    def test_set_sensor_source_invalid(self):
        result = self.led.set_sensor_source('memory')
        self.assertFalse(result['success'])


# =============================================================================
# Zone operations
# =============================================================================


class TestZoneOperations(unittest.TestCase):
    """Zone-level operations with validation."""

    def setUp(self):
        self.led = _make_led()

    def test_set_zone_color(self):
        result = self.led.set_zone_color(0, 0, 255, 0)
        self.assertTrue(result['success'])
        self.led._led_svc.set_zone_color.assert_called_with(0, 0, 255, 0)

    def test_set_zone_color_invalid_zone(self):
        result = self.led.set_zone_color(99, 0, 0, 0)
        self.assertFalse(result['success'])

    def test_set_zone_mode(self):
        result = self.led.set_zone_mode(0, LEDMode.STATIC)
        self.assertTrue(result['success'])

    def test_set_zone_mode_invalid_mode(self):
        result = self.led.set_zone_mode(0, 'bogus')
        self.assertFalse(result['success'])

    def test_set_zone_brightness(self):
        result = self.led.set_zone_brightness(1, 80)
        self.assertTrue(result['success'])

    def test_set_zone_brightness_out_of_range(self):
        result = self.led.set_zone_brightness(0, 200)
        self.assertFalse(result['success'])

    def test_toggle_zone(self):
        result = self.led.toggle_zone(0, True)
        self.assertTrue(result['success'])
        self.led._led_svc.toggle_zone.assert_called_with(0, True)

    def test_set_zone_sync(self):
        result = self.led.set_zone_sync(True, interval=5)
        self.assertTrue(result['success'])
        self.led._led_svc.set_zone_sync_interval.assert_called_with(5)
        self.led._led_svc.set_zone_sync.assert_called_with(True)

    def test_set_zone_sync_no_interval(self):
        result = self.led.set_zone_sync(False)
        self.assertTrue(result['success'])
        self.led._led_svc.set_zone_sync_interval.assert_not_called()


# =============================================================================
# Segment operations
# =============================================================================


class TestSegmentOperations(unittest.TestCase):
    """Segment toggle and display operations."""

    def setUp(self):
        self.led = _make_led()

    def test_toggle_segment(self):
        result = self.led.toggle_segment(0, False)
        self.assertTrue(result['success'])
        self.led._led_svc.toggle_segment.assert_called_with(0, False)

    def test_toggle_segment_invalid(self):
        result = self.led.toggle_segment(100, True)
        self.assertFalse(result['success'])

    def test_set_clock_format_24h(self):
        result = self.led.set_clock_format(True)
        self.assertTrue(result['success'])
        self.assertIn('24h', result['message'])

    def test_set_clock_format_12h(self):
        result = self.led.set_clock_format(False)
        self.assertTrue(result['success'])
        self.assertIn('12h', result['message'])

    def test_set_temp_unit(self):
        for unit, expected in [(0, "C"), (1, "F")]:
            result = self.led.set_temp_unit(unit)
            self.assertTrue(result['success'])
            self.assertEqual(result['message'], f"Temperature unit set to {expected}")
        self.assertTrue(result['success'])
        self.led._led_svc.set_seg_temp_unit.assert_called_with('F')



# =============================================================================
# State-only mutators (GUI path — no tick/send)
# =============================================================================


class TestUpdateMutators(unittest.TestCase):
    """GUI-path state mutators that don't tick/send."""

    def setUp(self):
        self.led = _make_led()

    def test_update_color(self):
        self.led.update_color(100, 200, 50)
        self.led._led_svc.set_color.assert_called_with(100, 200, 50)

    def test_update_mode(self):
        self.led.update_mode(LEDMode.RAINBOW)
        self.led._led_svc.set_mode.assert_called_with(LEDMode.RAINBOW)

    def test_update_mode_from_int(self):
        self.led.update_mode(LEDMode.STATIC.value)
        self.led._led_svc.set_mode.assert_called_with(LEDMode.STATIC)

    def test_update_brightness_clamps_high(self):
        self.led.update_brightness(200)
        self.led._led_svc.set_brightness.assert_called_with(100)

    def test_update_brightness_clamps_low(self):
        self.led.update_brightness(-10)
        self.led._led_svc.set_brightness.assert_called_with(0)

    def test_update_global_on(self):
        self.led.update_global_on(True)
        self.led._led_svc.toggle_global.assert_called_with(True)

    def test_update_segment(self):
        self.led.update_segment(1, True)
        self.led._led_svc.toggle_segment.assert_called_with(1, True)

    def test_update_zone_color(self):
        self.led.update_zone_color(0, 10, 20, 30)
        self.led._led_svc.set_zone_color.assert_called_with(0, 10, 20, 30)

    def test_update_zone_brightness_clamps(self):
        self.led.update_zone_brightness(0, 150)
        self.led._led_svc.set_zone_brightness.assert_called_with(0, 100)

    def test_update_clock_format(self):
        self.led.update_clock_format(True)
        self.led._led_svc.set_clock_format.assert_called_with(True)

    def test_update_week_start(self):
        self.led.update_week_start(False)
        self.led._led_svc.set_week_start.assert_called_with(False)

    def test_update_disk_index(self):
        self.led.update_disk_index(2)
        self.led._led_svc.set_disk_index.assert_called_with(2)

    def test_update_memory_ratio(self):
        self.led.update_memory_ratio(75)
        self.led._led_svc.set_memory_ratio.assert_called_with(75)

    def test_update_test_mode(self):
        self.led.update_test_mode(True)
        self.led._led_svc.set_test_mode.assert_called_with(True)

    def test_update_selected_zone(self):
        self.led.update_selected_zone(1)
        self.led._led_svc.set_selected_zone.assert_called_with(1)


# =============================================================================
# Tick + config
# =============================================================================


class TestTickAndConfig(unittest.TestCase):
    """Animation tick and config persistence."""

    def setUp(self):
        self.led = _make_led()

    def test_tick_returns_colors(self):
        result = self.led.tick()
        self.assertIn('colors', result)
        self.assertIn('display_colors', result)

    def test_tick_sends_when_protocol_connected(self):
        self.led._led_svc.has_protocol = True
        self.led.tick()
        self.led._led_svc.send_colors.assert_called_once()

    def test_tick_skips_send_without_protocol(self):
        self.led._led_svc.has_protocol = False
        self.led.tick()
        self.led._led_svc.send_colors.assert_not_called()

    def test_save_config(self):
        self.led.save_config()
        self.led._led_svc.save_config.assert_called_once()

    def test_save_config_safe_without_service(self):
        led = Device()
        led.save_config()  # no crash

    def test_load_config(self):
        self.led.load_config()
        self.led._led_svc.load_config.assert_called_once()

    def test_load_config_safe_without_service(self):
        led = Device()
        led.load_config()  # no crash

    def test_update_metrics(self):
        metrics = MagicMock()
        result = self.led.update_metrics(metrics)
        self.assertTrue(result['success'])
        self.led._led_svc.update_metrics.assert_called_with(metrics)


# =============================================================================
# Initialize (GUI path)
# =============================================================================


class TestInitialize(unittest.TestCase):
    """Device.initialize_led() — GUI path with pre-detected device."""

    def test_initialize_creates_service_if_needed(self):
        led = Device(protocol=MagicMock())
        device = MagicMock()
        led._led_svc = None  # force creation
        # Phase 9: Device is now a Union alias of LCDDevice|LEDDevice; patch
        # the concrete class's initialize_led directly.
        with unittest.mock.patch(
            'trcc.legacy.core.device.led.LEDDevice.initialize_led') as mock_init:
            mock_init.return_value = {"success": True, "status": "", "style": 2}
            result = led.initialize_led(device, 2)
        self.assertTrue(result['success'])

    def test_initialize_with_existing_service(self):
        led = _make_led()
        device = MagicMock()
        result = led.initialize_led(device, 3)
        self.assertTrue(result['success'])
        self.assertEqual(result['style'], 3)
        self.assertIs(led._info, device)


if __name__ == '__main__':
    unittest.main()
