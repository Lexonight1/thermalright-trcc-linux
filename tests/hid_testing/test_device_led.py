"""Mock tests for LED HID protocol layer (FormLED equivalent).

No real USB hardware required — all USB I/O is mocked via UsbTransport.
Tests cover LED styles, PM mappings, RGB table generation, color thresholds,
packet building, HID sender chunking, handshake, and the public API.
"""

import math
from unittest.mock import MagicMock, call, patch

import pytest

from trcc.device_hid import (
    DEFAULT_TIMEOUT_MS,
    EP_READ_01,
    EP_WRITE_02,
    UsbTransport,
)
from trcc.device_led import (
    DELAY_POST_INIT_S,
    DELAY_PRE_INIT_S,
    HID_REPORT_SIZE,
    LED_CMD_DATA,
    LED_CMD_INIT,
    LED_COLOR_SCALE,
    LED_HEADER_SIZE,
    LED_INIT_SIZE,
    LED_MAGIC,
    LED_PID,
    LED_REMAP_TABLES,
    LED_RESPONSE_SIZE,
    LED_STYLES,
    LED_VID,
    PRESET_COLORS,
    SEND_COOLDOWN_S,
    ColorEngine,
    LedDeviceStyle,
    LedHandshakeInfo,
    LedHidSender,
    LedPacketBuilder,
    PmRegistry,
    remap_led_colors,
    send_led_colors,
)

# Patch time.sleep globally for all tests in this module so handshake/send
# delays don't slow the suite down.
pytestmark = pytest.mark.usefixtures("_patch_sleep")


@pytest.fixture(autouse=True)
def _patch_sleep():
    """Disable time.sleep in led_device for fast tests."""
    with patch("trcc.device_led.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _clear_rgb_table_cache():
    """Reset the ColorEngine cached table between tests."""
    from trcc.device_led import ColorEngine
    original = ColorEngine._cached_table
    ColorEngine._cached_table = None
    yield
    ColorEngine._cached_table = original


# =========================================================================
# Helpers
# =========================================================================

def _make_mock_transport() -> MagicMock:
    """Create a MagicMock that satisfies the UsbTransport interface."""
    t = MagicMock(spec=UsbTransport)
    t.is_open = True
    return t


def _make_valid_handshake_response(pm: int = 3, sub_type: int = 0) -> bytes:
    """Build a valid LED handshake response (64 bytes).

    Offsets match Windows UCDevice.cs with Report ID removed:
        PM  = data[6] → raw resp[5]
        SUB = data[5] → raw resp[4]
    """
    resp = bytearray(LED_RESPONSE_SIZE)
    resp[0:4] = LED_MAGIC  # magic echo
    resp[4] = sub_type     # SUB at raw[4] (Windows data[5])
    resp[5] = pm           # PM at raw[5] (Windows data[6])
    resp[12] = LED_CMD_INIT  # cmd echo = 1
    return bytes(resp)


# =========================================================================
# TestLedDeviceStyle — LED_STYLES registry
# =========================================================================

class TestLedDeviceStyle:
    """Test LED_STYLES registry completeness and correctness."""

    def test_registry_has_13_styles(self):
        assert len(LED_STYLES) == 13

    def test_style_ids_are_1_through_13(self):
        assert set(LED_STYLES.keys()) == set(range(1, 14))

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_has_positive_led_count(self, style_id):
        style = LED_STYLES[style_id]
        assert style.led_count > 0

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_has_positive_segment_count(self, style_id):
        style = LED_STYLES[style_id]
        assert style.segment_count > 0

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_segment_count_lte_led_count(self, style_id):
        """Segment count should never exceed LED count."""
        style = LED_STYLES[style_id]
        assert style.segment_count <= style.led_count

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_zone_count_positive(self, style_id):
        style = LED_STYLES[style_id]
        assert style.zone_count >= 1

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_has_model_name(self, style_id):
        style = LED_STYLES[style_id]
        assert style.model_name != ""

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_has_preview_image(self, style_id):
        style = LED_STYLES[style_id]
        assert style.preview_image.startswith("D")

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_has_background_base(self, style_id):
        style = LED_STYLES[style_id]
        assert style.background_base.startswith("D0")

    @pytest.mark.parametrize("style_id", range(1, 14))
    def test_style_id_field_matches_key(self, style_id):
        """The style_id field should match the dictionary key."""
        style = LED_STYLES[style_id]
        assert style.style_id == style_id

    def test_known_led_counts(self):
        """Verify specific LED counts from FormLED.cs."""
        assert LED_STYLES[1].led_count == 30   # AX120_DIGITAL
        assert LED_STYLES[2].led_count == 84   # PA120_DIGITAL
        assert LED_STYLES[3].led_count == 64   # AK120_DIGITAL
        assert LED_STYLES[4].led_count == 31   # LC1
        assert LED_STYLES[5].led_count == 93   # LF8
        assert LED_STYLES[6].led_count == 124  # LF12
        assert LED_STYLES[7].led_count == 116  # LF10
        assert LED_STYLES[8].led_count == 18   # CZ1
        assert LED_STYLES[9].led_count == 61   # LC2
        assert LED_STYLES[10].led_count == 38  # LF11
        assert LED_STYLES[11].led_count == 93  # LF15
        assert LED_STYLES[12].led_count == 62  # LF13

    def test_known_zone_counts(self):
        """Verify specific zone counts from FormLED.cs."""
        assert LED_STYLES[1].zone_count == 1   # single zone
        assert LED_STYLES[2].zone_count == 4   # PA120 has 4 zones
        assert LED_STYLES[3].zone_count == 2   # AK120 has 2 zones
        assert LED_STYLES[8].zone_count == 4   # CZ1 has 4 zones

    def test_dataclass_default_zone_count(self):
        """LedDeviceStyle defaults zone_count to 1."""
        style = LedDeviceStyle(style_id=99, led_count=10, segment_count=5)
        assert style.zone_count == 1

    def test_dataclass_default_background_base(self):
        """LedDeviceStyle defaults background_base."""
        style = LedDeviceStyle(style_id=99, led_count=10, segment_count=5)
        assert style.background_base == "D0\u6570\u7801\u5c4f"

    def test_max_led_count(self):
        """LF12 (style 6) has the highest LED count at 124."""
        max_count = max(s.led_count for s in LED_STYLES.values())
        assert max_count == 124
        assert LED_STYLES[6].led_count == max_count


# =========================================================================
# TestPmMapping — PmRegistry._REGISTRY, PmRegistry.PM_TO_STYLE, PmRegistry.get_model_name
# =========================================================================

class TestPmMapping:
    """Test PM byte to style and model mappings."""

    def test_pm_to_style_all_values_map_to_valid_styles(self):
        """Every PM byte should map to a valid LED style."""
        for pm, style_id in PmRegistry.PM_TO_STYLE.items():
            assert style_id in LED_STYLES, f"PM {pm} maps to unknown style {style_id}"

    def test_pm_to_style_known_mappings(self):
        """Verify specific PM→style mappings from FormLEDInit."""
        assert PmRegistry.PM_TO_STYLE[1] == 1    # FROZEN_HORIZON_PRO → style 1
        assert PmRegistry.PM_TO_STYLE[16] == 2   # PA120_DIGITAL → style 2
        assert PmRegistry.PM_TO_STYLE[32] == 3   # AK120_DIGITAL → style 3
        assert PmRegistry.PM_TO_STYLE[48] == 5   # LF8 → style 5
        assert PmRegistry.PM_TO_STYLE[80] == 6   # LF12 → style 6
        assert PmRegistry.PM_TO_STYLE[96] == 7   # LF10 → style 7
        assert PmRegistry.PM_TO_STYLE[112] == 9  # LC2 → style 9
        assert PmRegistry.PM_TO_STYLE[128] == 4  # LC1 → style 4
        assert PmRegistry.PM_TO_STYLE[208] == 8  # CZ1 → style 8

    def test_pm_to_style_pa120_variants(self):
        """PA120 variants (pm 16-31) all map to style 2."""
        for pm in [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31]:
            assert PmRegistry.PM_TO_STYLE[pm] == 2

    def test_pm_to_model_known_mappings(self):
        """Verify specific PM→model name via PmRegistry.get_model_name()."""
        assert PmRegistry.get_model_name(1) == "FROZEN_HORIZON_PRO"
        assert PmRegistry.get_model_name(2) == "FROZEN_MAGIC_PRO"
        assert PmRegistry.get_model_name(3) == "AX120_DIGITAL"
        assert PmRegistry.get_model_name(16) == "PA120_DIGITAL"
        assert PmRegistry.get_model_name(32) == "AK120_DIGITAL"
        assert PmRegistry.get_model_name(208) == "CZ1"

    def test_pm_registry_has_entries(self):
        """PmRegistry._REGISTRY should cover all known PM values."""
        assert len(PmRegistry._REGISTRY) >= 30  # 16 primary + 14 PA120 variants

    def test_pm_registry_pa120_variants_have_model(self):
        """PA120 variant PMs (17-22, 24-31) should have model names."""
        for pm in (17, 18, 19, 20, 21, 22, 24, 25, 26, 27, 28, 29, 30, 31):
            assert PmRegistry.get_model_name(pm) == "PA120_DIGITAL"

    def test_get_model_name_unknown(self):
        """Unknown PM falls back to 'Unknown (pm=N)'."""
        assert PmRegistry.get_model_name(255) == "Unknown (pm=255)"

    def test_get_model_name_hr10_override(self):
        """HR10 (pm=128, sub=129) overrides LC1 model name."""
        assert PmRegistry.get_model_name(128) == "LC1"
        assert PmRegistry.get_model_name(128, 129) == "HR10_2280_PRO_DIGITAL"

    def test_get_style_known(self):
        """PmRegistry.get_style returns correct style for known PM."""
        style = PmRegistry.get_style(1)
        assert style.style_id == 1
        assert style.model_name == "AX120_DIGITAL"

    def test_get_style_unknown_falls_back_to_style_1(self):
        """Unknown PM bytes should fall back to style 1."""
        style = PmRegistry.get_style(255)
        assert style.style_id == 1
        assert style.led_count == 30

    def test_get_style_zero(self):
        """PM 0 is unknown — falls back to style 1."""
        style = PmRegistry.get_style(0)
        assert style.style_id == 1

    def test_get_style_pa120(self):
        """PA120 style has correct zone count."""
        style = PmRegistry.get_style(16)
        assert style.zone_count == 4
        assert style.led_count == 84

    def test_get_style_cz1(self):
        """CZ1 (pm=208) resolves to style 8."""
        style = PmRegistry.get_style(208)
        assert style.style_id == 8
        assert style.led_count == 18

    def test_get_style_lc1_default(self):
        """LC1 (pm=128, sub_type=0) resolves to style 4."""
        style = PmRegistry.get_style(128, sub_type=0)
        assert style.style_id == 4

    def test_get_style_hr10_sub_type_override(self):
        """HR10 (pm=128, sub_type=129) resolves to style 13 via SUB_TYPE_OVERRIDES."""
        style = PmRegistry.get_style(128, sub_type=129)
        assert style.style_id == 13
        assert style.model_name == "HR10_2280_PRO_DIGITAL"
        assert style.led_count == 31

    def test_sub_type_override_takes_precedence(self):
        """PmRegistry._OVERRIDES should override PmRegistry.PM_TO_STYLE."""
        # HR10 entry exists
        assert (128, 129) in PmRegistry._OVERRIDES
        # Without sub_type, PM=128 → style 4 (LC1)
        assert PmRegistry.get_style(128).style_id == 4
        # With sub_type=129, PM=128 → style 13 (HR10)
        assert PmRegistry.get_style(128, 129).style_id == 13


# =========================================================================
# TestRgbTable — ColorEngine.generate_table() and ColorEngine.get_table()
# =========================================================================

class TestRgbTable:
    """Test the 768-entry RGB rainbow lookup table."""

    def test_table_length(self):
        table = ColorEngine.generate_table()
        assert len(table) == 768

    def test_all_values_in_range(self):
        """All RGB components should be 0-255."""
        table = ColorEngine.generate_table()
        for r, g, b in table:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    def test_all_entries_are_tuples_of_three(self):
        table = ColorEngine.generate_table()
        for entry in table:
            assert len(entry) == 3

    def test_first_entry_is_red(self):
        """Index 0: start of Red->Yellow phase — pure red."""
        table = ColorEngine.generate_table()
        r, g, b = table[0]
        assert r == 255
        assert g == 0
        assert b == 0

    def test_phase_boundary_127_is_yellow(self):
        """Index 127: end of Red->Yellow phase — should be (255, 255, 0)."""
        table = ColorEngine.generate_table()
        r, g, b = table[127]
        assert r == 255
        assert g == 255
        assert b == 0

    def test_phase_boundary_255_is_green(self):
        """Index 255: end of Yellow->Green phase — should be (0, 255, 0)."""
        table = ColorEngine.generate_table()
        r, g, b = table[255]
        assert r == 0
        assert g == 255
        assert b == 0

    def test_phase_boundary_383_is_cyan(self):
        """Index 383: end of Green->Cyan phase — should be (0, 255, 255)."""
        table = ColorEngine.generate_table()
        r, g, b = table[383]
        assert r == 0
        assert g == 255
        assert b == 255

    def test_phase_boundary_511_is_blue(self):
        """Index 511: end of Cyan->Blue phase — should be (0, 0, 255)."""
        table = ColorEngine.generate_table()
        r, g, b = table[511]
        assert r == 0
        assert g == 0
        assert b == 255

    def test_phase_boundary_639_is_magenta(self):
        """Index 639: end of Blue->Magenta phase — should be (255, 0, 255)."""
        table = ColorEngine.generate_table()
        r, g, b = table[639]
        assert r == 255
        assert g == 0
        assert b == 255

    def test_last_entry_is_near_red(self):
        """Index 767: end of Magenta->Red phase — should be close to (255, 0, 0)."""
        table = ColorEngine.generate_table()
        r, g, b = table[767]
        assert r == 255
        assert b == 0  # blue fully gone

    def test_smooth_transitions_no_large_jumps(self):
        """Adjacent entries should differ by at most a small amount per component."""
        table = ColorEngine.generate_table()
        max_delta = 0
        for i in range(len(table) - 1):
            r1, g1, b1 = table[i]
            r2, g2, b2 = table[i + 1]
            dr = abs(r2 - r1)
            dg = abs(g2 - g1)
            db = abs(b2 - b1)
            max_delta = max(max_delta, dr, dg, db)
        # With 128 steps per phase spanning 0-255, max step is ceil(255/127) = 3
        assert max_delta <= 3

    def test_get_table_returns_cached(self):
        """ColorEngine.get_table() should return the same object on repeated calls."""
        table1 = ColorEngine.get_table()
        table2 = ColorEngine.get_table()
        assert table1 is table2

    def test_get_table_same_content_as_generate(self):
        """ColorEngine.get_table() returns same content as ColorEngine.generate_table()."""
        cached = ColorEngine.get_table()
        fresh = ColorEngine.generate_table()
        assert cached == fresh

    def test_get_table_length(self):
        assert len(ColorEngine.get_table()) == 768


# =========================================================================
# TestColorThresholds — ColorEngine.color_for_value()
# =========================================================================

class TestColorThresholds:
    """Test ColorEngine.color_for_value() with temperature and load thresholds."""

    # --- Temperature thresholds ---

    def test_temp_below_30_cyan(self):
        assert ColorEngine.color_for_value(20, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_temp_exactly_0_cyan(self):
        assert ColorEngine.color_for_value(0, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_temp_29_cyan(self):
        assert ColorEngine.color_for_value(29, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_temp_30_cyan(self):
        """30 is the first gradient stop — clamps to cyan."""
        assert ColorEngine.color_for_value(30, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_temp_49_interpolated_near_green(self):
        """49 is near the (50, green) stop — interpolated cyan→green, mostly green."""
        assert ColorEngine.color_for_value(49, ColorEngine.TEMP_GRADIENT) == (0, 255, 12)

    def test_temp_50_green(self):
        """50 is the second gradient stop — exactly green."""
        assert ColorEngine.color_for_value(50, ColorEngine.TEMP_GRADIENT) == (0, 255, 0)

    def test_temp_69_interpolated_near_yellow(self):
        """69 is near the (70, yellow) stop — interpolated green→yellow."""
        assert ColorEngine.color_for_value(69, ColorEngine.TEMP_GRADIENT) == (242, 255, 0)

    def test_temp_70_yellow(self):
        """70 is the third gradient stop — exactly yellow."""
        assert ColorEngine.color_for_value(70, ColorEngine.TEMP_GRADIENT) == (255, 255, 0)

    def test_temp_89_interpolated_near_orange(self):
        """89 is near the (90, orange) stop — interpolated yellow→orange."""
        assert ColorEngine.color_for_value(89, ColorEngine.TEMP_GRADIENT) == (255, 117, 0)

    def test_temp_90_orange(self):
        """90 is the fourth gradient stop — exactly orange."""
        assert ColorEngine.color_for_value(90, ColorEngine.TEMP_GRADIENT) == (255, 110, 0)

    def test_temp_100_red(self):
        assert ColorEngine.color_for_value(100, ColorEngine.TEMP_GRADIENT) == (255, 0, 0)

    def test_temp_negative_cyan(self):
        """Negative temperature is still below 30."""
        assert ColorEngine.color_for_value(-10, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_temp_very_high_red(self):
        assert ColorEngine.color_for_value(999, ColorEngine.TEMP_GRADIENT) == (255, 0, 0)

    # --- Load thresholds (same as temp) ---

    def test_load_thresholds_same_as_temp(self):
        """ColorEngine.LOAD_GRADIENT should be the same object as ColorEngine.TEMP_GRADIENT."""
        assert ColorEngine.LOAD_GRADIENT is ColorEngine.TEMP_GRADIENT

    def test_load_gradient_same_as_temp(self):
        """Both gradients share the same last stop (red at 100)."""
        assert ColorEngine.LOAD_GRADIENT[-1][1] == ColorEngine.TEMP_GRADIENT[-1][1]

    def test_load_0_percent_cyan(self):
        assert ColorEngine.color_for_value(0, ColorEngine.LOAD_GRADIENT) == (0, 255, 255)

    def test_load_100_percent_red(self):
        assert ColorEngine.color_for_value(100, ColorEngine.LOAD_GRADIENT) == (255, 0, 0)

    # --- Float boundary precision ---

    def test_float_just_below_30(self):
        assert ColorEngine.color_for_value(29.999, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)

    def test_float_just_at_30(self):
        """30.0 is the first gradient stop — clamps to cyan."""
        assert ColorEngine.color_for_value(30.0, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)


# =========================================================================
# TestPresetColors — PRESET_COLORS list
# =========================================================================

class TestPresetColors:
    """Test PRESET_COLORS constant."""

    def test_has_8_entries(self):
        assert len(PRESET_COLORS) == 8

    def test_all_entries_are_rgb_tuples(self):
        for color in PRESET_COLORS:
            assert len(color) == 3

    def test_all_values_in_range(self):
        for r, g, b in PRESET_COLORS:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    def test_first_color_red_pink(self):
        assert PRESET_COLORS[0] == (255, 0, 42)

    def test_last_color_white(self):
        assert PRESET_COLORS[7] == (255, 255, 255)

    def test_green_present(self):
        assert (0, 255, 0) in PRESET_COLORS

    def test_yellow_present(self):
        assert (255, 255, 0) in PRESET_COLORS

    def test_cyan_present(self):
        assert (0, 255, 255) in PRESET_COLORS


# =========================================================================
# TestLedPacketBuilder — header, init, and LED packets
# =========================================================================

class TestLedPacketBuilderHeader:
    """Test LedPacketBuilder.build_header()."""

    def test_header_length_is_20(self):
        header = LedPacketBuilder.build_header(0)
        assert len(header) == LED_HEADER_SIZE

    def test_magic_bytes(self):
        header = LedPacketBuilder.build_header(0)
        assert header[0:4] == bytes([0xDA, 0xDB, 0xDC, 0xDD])

    def test_command_byte_is_data(self):
        """build_header always sets cmd=2 (LED data)."""
        header = LedPacketBuilder.build_header(0)
        assert header[12] == LED_CMD_DATA

    def test_reserved_bytes_4_to_11_are_zero(self):
        header = LedPacketBuilder.build_header(100)
        assert header[4:12] == b'\x00' * 8

    def test_reserved_bytes_13_to_15_are_zero(self):
        header = LedPacketBuilder.build_header(100)
        assert header[13:16] == b'\x00' * 3

    def test_reserved_bytes_18_to_19_are_zero(self):
        header = LedPacketBuilder.build_header(100)
        assert header[18:20] == b'\x00' * 2

    def test_payload_length_encoding_small(self):
        """Payload length 90 = 0x5A → byte 16=0x5A, byte 17=0x00."""
        header = LedPacketBuilder.build_header(90)
        assert header[16] == 90
        assert header[17] == 0

    def test_payload_length_encoding_large(self):
        """Payload length 372 = 0x0174 → byte 16=0x74, byte 17=0x01."""
        header = LedPacketBuilder.build_header(372)
        assert header[16] == 0x74
        assert header[17] == 0x01

    def test_payload_length_encoding_zero(self):
        header = LedPacketBuilder.build_header(0)
        assert header[16] == 0
        assert header[17] == 0

    def test_payload_length_encoding_max_leds(self):
        """124 LEDs * 3 bytes = 372."""
        header = LedPacketBuilder.build_header(124 * 3)
        assert header[16] == (372 & 0xFF)
        assert header[17] == (372 >> 8) & 0xFF

    def test_returns_bytes_not_bytearray(self):
        header = LedPacketBuilder.build_header(0)
        assert isinstance(header, bytes)


class TestLedPacketBuilderInit:
    """Test LedPacketBuilder.build_init_packet()."""

    def test_packet_length_is_64(self):
        pkt = LedPacketBuilder.build_init_packet()
        assert len(pkt) == HID_REPORT_SIZE

    def test_magic_bytes(self):
        pkt = LedPacketBuilder.build_init_packet()
        assert pkt[0:4] == LED_MAGIC

    def test_command_byte_is_init(self):
        pkt = LedPacketBuilder.build_init_packet()
        assert pkt[12] == LED_CMD_INIT

    def test_rest_is_zeros(self):
        pkt = LedPacketBuilder.build_init_packet()
        # bytes 4-11 should be zero
        assert pkt[4:12] == b'\x00' * 8
        # bytes 13-63 should be zero
        assert pkt[13:] == b'\x00' * 51

    def test_byte_by_byte_first_20(self):
        """Verify first 20 bytes match expected layout exactly."""
        pkt = LedPacketBuilder.build_init_packet()
        expected = bytes([
            0xDA, 0xDB, 0xDC, 0xDD,  # magic
            0, 0, 0, 0,              # reserved
            0, 0, 0, 0,              # reserved
            1, 0, 0, 0,              # cmd=1
            0, 0, 0, 0,              # reserved
        ])
        assert pkt[:20] == expected

    def test_returns_bytes(self):
        pkt = LedPacketBuilder.build_init_packet()
        assert isinstance(pkt, bytes)


class TestLedPacketBuilderLedPacket:
    """Test LedPacketBuilder.build_led_packet()."""

    def test_total_length_single_led(self):
        """1 LED = 20-byte header + 3-byte payload = 23 bytes."""
        pkt = LedPacketBuilder.build_led_packet([(255, 0, 0)])
        assert len(pkt) == LED_HEADER_SIZE + 3

    def test_total_length_30_leds(self):
        """30 LEDs = 20 + 90 = 110 bytes."""
        colors = [(255, 0, 0)] * 30
        pkt = LedPacketBuilder.build_led_packet(colors)
        assert len(pkt) == LED_HEADER_SIZE + 90

    def test_total_length_124_leds(self):
        """124 LEDs = 20 + 372 = 392 bytes."""
        colors = [(0, 0, 255)] * 124
        pkt = LedPacketBuilder.build_led_packet(colors)
        assert len(pkt) == LED_HEADER_SIZE + 372

    def test_color_scaling_by_0_4(self):
        """Each RGB component should be multiplied by 0.4."""
        pkt = LedPacketBuilder.build_led_packet([(255, 128, 64)])
        # After header (20 bytes), payload starts
        r = pkt[20]
        g = pkt[21]
        b = pkt[22]
        assert r == int(255 * 0.4)  # 102
        assert g == int(128 * 0.4)  # 51
        assert b == int(64 * 0.4)   # 25

    def test_color_scaling_pure_white(self):
        """White (255, 255, 255) → (102, 102, 102) at 100% brightness."""
        pkt = LedPacketBuilder.build_led_packet([(255, 255, 255)])
        assert pkt[20] == 102
        assert pkt[21] == 102
        assert pkt[22] == 102

    def test_color_scaling_black(self):
        """Black (0, 0, 0) stays (0, 0, 0)."""
        pkt = LedPacketBuilder.build_led_packet([(0, 0, 0)])
        assert pkt[20] == 0
        assert pkt[21] == 0
        assert pkt[22] == 0

    def test_brightness_50_percent(self):
        """brightness=50 applies 50% multiplier on top of 0.4x scale."""
        pkt = LedPacketBuilder.build_led_packet([(255, 255, 255)], brightness=50)
        # 255 * 0.5 * 0.4 = 51.0
        assert pkt[20] == 51
        assert pkt[21] == 51
        assert pkt[22] == 51

    def test_brightness_0_percent(self):
        """brightness=0 → all LEDs dark."""
        pkt = LedPacketBuilder.build_led_packet([(255, 255, 255)], brightness=0)
        assert pkt[20] == 0
        assert pkt[21] == 0
        assert pkt[22] == 0

    def test_brightness_clamped_above_100(self):
        """brightness > 100 is clamped to 100."""
        pkt_100 = LedPacketBuilder.build_led_packet([(200, 200, 200)], brightness=100)
        pkt_200 = LedPacketBuilder.build_led_packet([(200, 200, 200)], brightness=200)
        assert pkt_100[20:] == pkt_200[20:]

    def test_brightness_clamped_below_0(self):
        """brightness < 0 is clamped to 0."""
        pkt = LedPacketBuilder.build_led_packet([(255, 255, 255)], brightness=-50)
        assert pkt[20] == 0
        assert pkt[21] == 0
        assert pkt[22] == 0

    def test_is_on_per_led(self):
        """LEDs with is_on=False should output (0, 0, 0)."""
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        is_on = [True, False, True]
        pkt = LedPacketBuilder.build_led_packet(colors, is_on=is_on)
        # LED 0: on → scaled
        assert pkt[20] == int(255 * 0.4)
        # LED 1: off → 0
        assert pkt[23] == 0
        assert pkt[24] == 0
        assert pkt[25] == 0
        # LED 2: on → scaled
        assert pkt[28] == int(255 * 0.4)

    def test_global_on_false(self):
        """global_on=False → all LEDs output (0, 0, 0) regardless of is_on."""
        colors = [(255, 255, 255)] * 3
        pkt = LedPacketBuilder.build_led_packet(colors, global_on=False)
        # All payload bytes should be 0
        payload = pkt[LED_HEADER_SIZE:]
        assert all(b == 0 for b in payload)

    def test_global_on_false_overrides_is_on_true(self):
        """global_on=False overrides per-LED on state."""
        colors = [(255, 0, 0), (0, 255, 0)]
        is_on = [True, True]
        pkt = LedPacketBuilder.build_led_packet(colors, is_on=is_on, global_on=False)
        payload = pkt[LED_HEADER_SIZE:]
        assert all(b == 0 for b in payload)

    def test_empty_colors(self):
        """Empty color list → 20-byte header with 0 payload."""
        pkt = LedPacketBuilder.build_led_packet([])
        assert len(pkt) == LED_HEADER_SIZE
        assert pkt[16] == 0  # payload length lo
        assert pkt[17] == 0  # payload length hi

    def test_header_payload_length_field_correct(self):
        """Header payload length should match actual payload size."""
        colors = [(100, 200, 50)] * 10
        pkt = LedPacketBuilder.build_led_packet(colors)
        payload_len = pkt[16] | (pkt[17] << 8)
        assert payload_len == 30  # 10 * 3

    def test_header_magic_preserved(self):
        """Header magic bytes should be correct even in full packet."""
        colors = [(255, 0, 0)] * 5
        pkt = LedPacketBuilder.build_led_packet(colors)
        assert pkt[0:4] == LED_MAGIC

    def test_multiple_leds_sequential(self):
        """Verify multiple LEDs are laid out sequentially in payload."""
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        pkt = LedPacketBuilder.build_led_packet(colors)
        base = LED_HEADER_SIZE
        # Red LED
        assert pkt[base] == int(255 * 0.4)
        assert pkt[base + 1] == 0
        assert pkt[base + 2] == 0
        # Green LED
        assert pkt[base + 3] == 0
        assert pkt[base + 4] == int(255 * 0.4)
        assert pkt[base + 5] == 0
        # Blue LED
        assert pkt[base + 6] == 0
        assert pkt[base + 7] == 0
        assert pkt[base + 8] == int(255 * 0.4)


# =========================================================================
# TestLedHidSender — handshake and send_led_data
# =========================================================================

class TestLedHidSenderHandshake:
    """Test LedHidSender.handshake()."""

    def test_successful_handshake(self):
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=3)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert isinstance(info, LedHandshakeInfo)
        assert info.pm == 3
        transport.write.assert_called_once()
        transport.read.assert_called_once()

    def test_handshake_sends_init_packet(self):
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response()

        sender = LedHidSender(transport)
        sender.handshake()

        write_args = transport.write.call_args
        assert write_args[0][0] == EP_WRITE_02
        assert len(write_args[0][1]) == HID_REPORT_SIZE  # 64

    def test_handshake_reads_from_ep01(self):
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response()

        sender = LedHidSender(transport)
        sender.handshake()

        read_args = transport.read.call_args
        assert read_args[0][0] == EP_READ_01
        assert read_args[0][1] == LED_RESPONSE_SIZE

    def test_handshake_extracts_pm(self):
        """pm byte at response[5] (Windows data[6]) should be extracted."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=48)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.pm == 48

    def test_handshake_extracts_sub_type(self):
        """sub_type byte at response[4] (Windows data[5]) should be extracted."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=1, sub_type=7)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.sub_type == 7

    def test_handshake_resolves_style(self):
        """Style should be resolved from PM byte."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=16)  # PA120

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.style is not None
        assert info.style.style_id == 2  # PA120 → style 2
        assert info.style.led_count == 84

    def test_handshake_resolves_model_name(self):
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=3)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.model_name == "AX120_DIGITAL"

    def test_handshake_unknown_pm_model_name(self):
        """Unknown PM should produce a fallback model name."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=200)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert "Unknown" in info.model_name or "200" in info.model_name

    def test_handshake_bad_magic_warns(self):
        """Response with wrong magic bytes should warn but still succeed.

        Windows DeviceDataReceived1 doesn't validate magic — we match that.
        """
        transport = _make_mock_transport()
        resp = bytearray(LED_RESPONSE_SIZE)
        resp[0:4] = b'\xFF\xFF\xFF\xFF'  # bad magic
        resp[12] = 1
        transport.read.return_value = bytes(resp)

        sender = LedHidSender(transport)
        info = sender.handshake()
        assert info is not None

    def test_handshake_bad_cmd_byte_warns(self):
        """Response with cmd != 1 should warn but still succeed.

        Windows DeviceDataReceived1 doesn't validate cmd byte either.
        """
        transport = _make_mock_transport()
        resp = bytearray(LED_RESPONSE_SIZE)
        resp[0:4] = LED_MAGIC
        resp[12] = 2  # should be 1
        transport.read.return_value = bytes(resp)

        sender = LedHidSender(transport)
        info = sender.handshake()
        assert info is not None

    def test_handshake_short_response_raises(self):
        """Response shorter than 7 bytes should raise RuntimeError after retries."""
        transport = _make_mock_transport()
        transport.read.return_value = b'\xDA\xDB\xDC\xDD\x00\x00'  # 6 bytes (< 7)

        sender = LedHidSender(transport)
        with pytest.raises(RuntimeError, match="too short"):
            sender.handshake()

    def test_handshake_timing(self):
        """Verify C# Sleep(50) + Sleep(200) timing is called."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response()

        sender = LedHidSender(transport)
        with patch("trcc.device_led.time.sleep") as mock_sleep:
            sender.handshake()
            calls = mock_sleep.call_args_list
            assert len(calls) == 2
            assert calls[0] == call(DELAY_PRE_INIT_S)
            assert calls[1] == call(DELAY_POST_INIT_S)

    def test_handshake_pm_sub_offset_matches_windows(self):
        """PM at raw[5] and SUB at raw[4] — matches Windows Report ID offset.

        Windows HID API prepends Report ID at data[0], so:
            data[6] = raw resp[5] = PM
            data[5] = raw resp[4] = SUB
        """
        transport = _make_mock_transport()
        resp = bytearray(LED_RESPONSE_SIZE)
        resp[0:4] = LED_MAGIC
        resp[4] = 0x42   # SUB at raw[4]
        resp[5] = 0x80   # PM at raw[5] (128 = LC1)
        resp[6] = 0xFF   # noise — should NOT be read as PM
        resp[12] = 1
        transport.read.return_value = bytes(resp)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.pm == 0x80
        assert info.sub_type == 0x42

    def test_handshake_stores_raw_response(self):
        """Handshake should store first 64 bytes of response for diagnostics."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(pm=3)

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert len(info.raw_response) == 64
        assert info.raw_response[0:4] == LED_MAGIC

    def test_handshake_hr10_via_sub_type(self):
        """HR10 device (PM=128, SUB=129) resolved via SUB_TYPE_OVERRIDES."""
        transport = _make_mock_transport()
        transport.read.return_value = _make_valid_handshake_response(
            pm=128, sub_type=129
        )

        sender = LedHidSender(transport)
        info = sender.handshake()

        assert info.pm == 128
        assert info.sub_type == 129
        assert info.style.style_id == 13
        assert info.model_name == "HR10_2280_PRO_DIGITAL"


class TestLedHidSenderSendLedData:
    """Test LedHidSender.send_led_data() chunking and transport calls."""

    def test_send_single_chunk(self):
        """Packet <= 64 bytes should result in exactly 1 write."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        # 20-byte header + 3 bytes = 23 bytes (fits in one 64-byte chunk)
        packet = b'\xAB' * 23
        result = sender.send_led_data(packet)

        assert result is True
        assert transport.write.call_count == 1

    def test_send_exact_64_bytes(self):
        """Exactly 64 bytes = 1 write, no padding."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = b'\xCC' * 64
        sender.send_led_data(packet)

        assert transport.write.call_count == 1
        written_data = transport.write.call_args[0][1]
        assert len(written_data) == 64
        assert written_data == packet

    def test_send_65_bytes_two_chunks(self):
        """65 bytes = 2 chunks (64 + 1 padded to 64)."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = b'\xDD' * 65
        sender.send_led_data(packet)

        assert transport.write.call_count == 2

    def test_send_128_bytes_two_chunks(self):
        """128 bytes = exactly 2 chunks of 64."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = b'\xEE' * 128
        sender.send_led_data(packet)

        assert transport.write.call_count == 2

    def test_chunk_padding(self):
        """Last chunk should be zero-padded to 64 bytes."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        # 70 bytes = chunk1 (64 bytes) + chunk2 (6 bytes + 58 padding)
        packet = b'\xFF' * 70
        sender.send_led_data(packet)

        second_call = transport.write.call_args_list[1]
        written_data = second_call[0][1]
        assert len(written_data) == 64
        assert written_data[:6] == b'\xFF' * 6
        assert written_data[6:] == b'\x00' * 58

    def test_first_chunk_data_correct(self):
        """First chunk should contain first 64 bytes of packet."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = bytes(range(70))  # 0x00..0x45
        sender.send_led_data(packet)

        first_call = transport.write.call_args_list[0]
        written_data = first_call[0][1]
        assert written_data == bytes(range(64))

    def test_writes_to_correct_endpoint(self):
        """All chunks should go to EP_WRITE_02."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = b'\xAA' * 200
        sender.send_led_data(packet)

        for c in transport.write.call_args_list:
            assert c[0][0] == EP_WRITE_02

    def test_writes_with_correct_timeout(self):
        """All chunks should use DEFAULT_TIMEOUT_MS."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        packet = b'\xAA' * 100
        sender.send_led_data(packet)

        for c in transport.write.call_args_list:
            assert c[0][2] == DEFAULT_TIMEOUT_MS

    def test_send_cooldown(self):
        """Should sleep SEND_COOLDOWN_S after successful send."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        with patch("trcc.device_led.time.sleep") as mock_sleep:
            sender.send_led_data(b'\xAA' * 20)
            mock_sleep.assert_called_once_with(SEND_COOLDOWN_S)

    def test_concurrent_send_guard(self):
        """Second send while first is in progress should return False."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        # Simulate send in progress
        sender._sending = True
        result = sender.send_led_data(b'\xAA' * 20)

        assert result is False
        transport.write.assert_not_called()

    def test_sending_flag_reset_after_send(self):
        """_sending should be False after send completes."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        sender.send_led_data(b'\xAA' * 20)
        assert sender._sending is False

    def test_sending_flag_reset_on_error(self):
        """_sending should be False even if write raises."""
        transport = _make_mock_transport()
        transport.write.side_effect = OSError("USB error")
        sender = LedHidSender(transport)

        result = sender.send_led_data(b'\xAA' * 20)
        assert result is False
        assert sender._sending is False

    def test_write_exception_returns_false(self):
        """Transport write exception should result in False return."""
        transport = _make_mock_transport()
        transport.write.side_effect = OSError("USB disconnected")
        sender = LedHidSender(transport)

        result = sender.send_led_data(b'\xAA' * 20)
        assert result is False

    def test_is_sending_property(self):
        """is_sending property should reflect _sending state."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        assert sender.is_sending is False
        sender._sending = True
        assert sender.is_sending is True

    def test_realistic_30_led_packet(self):
        """Realistic 30-LED packet: 20 + 90 = 110 bytes → 2 chunks."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        colors = [(255, 0, 0)] * 30
        packet = LedPacketBuilder.build_led_packet(colors)
        assert len(packet) == 110

        sender.send_led_data(packet)
        assert transport.write.call_count == 2  # ceil(110/64) = 2

    def test_realistic_124_led_packet(self):
        """Realistic 124-LED packet: 20 + 372 = 392 bytes → 7 chunks."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        colors = [(128, 64, 32)] * 124
        packet = LedPacketBuilder.build_led_packet(colors)
        assert len(packet) == 392

        sender.send_led_data(packet)
        expected_chunks = math.ceil(392 / 64)
        assert transport.write.call_count == expected_chunks  # 7

    def test_empty_packet(self):
        """Empty packet (0 bytes) should succeed with no writes."""
        transport = _make_mock_transport()
        sender = LedHidSender(transport)

        result = sender.send_led_data(b'')
        assert result is True
        transport.write.assert_not_called()


# =========================================================================
# TestSendLedColors — public convenience function
# =========================================================================

class TestSendLedColors:
    """Test send_led_colors() convenience function."""

    def test_basic_send(self):
        transport = _make_mock_transport()
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        result = send_led_colors(transport, colors)
        assert result is True
        assert transport.write.call_count >= 1

    def test_send_with_brightness(self):
        transport = _make_mock_transport()
        colors = [(255, 255, 255)]
        result = send_led_colors(transport, colors, brightness=50)
        assert result is True

    def test_send_with_global_off(self):
        transport = _make_mock_transport()
        colors = [(255, 255, 255)] * 5
        result = send_led_colors(transport, colors, global_on=False)
        assert result is True

    def test_send_with_is_on(self):
        transport = _make_mock_transport()
        colors = [(255, 0, 0), (0, 255, 0)]
        is_on = [True, False]
        result = send_led_colors(transport, colors, is_on=is_on)
        assert result is True

    def test_send_returns_false_on_transport_error(self):
        transport = _make_mock_transport()
        transport.write.side_effect = OSError("fail")
        colors = [(255, 0, 0)]
        result = send_led_colors(transport, colors)
        assert result is False

    def test_send_builds_correct_packet(self):
        """Verify send_led_colors passes correct args to builder."""
        transport = _make_mock_transport()
        colors = [(100, 200, 50)]
        send_led_colors(transport, colors, brightness=75, global_on=True)

        # The first write call should contain the packet data
        written_data = transport.write.call_args_list[0][0][1]
        # Verify it starts with magic bytes (from header)
        assert written_data[0:4] == LED_MAGIC

    def test_send_empty_colors(self):
        """Empty color list should produce header-only packet."""
        transport = _make_mock_transport()
        result = send_led_colors(transport, [])
        # Header-only (20 bytes) → 0 remaining → no write calls
        assert result is True


# =========================================================================
# TestLedHandshakeInfo — dataclass
# =========================================================================

class TestLedHandshakeInfo:
    """Test LedHandshakeInfo dataclass fields and defaults."""

    def test_required_field_pm(self):
        info = LedHandshakeInfo(pm=48)
        assert info.pm == 48

    def test_default_sub_type(self):
        info = LedHandshakeInfo(pm=1)
        assert info.sub_type == 0

    def test_default_style_is_none(self):
        info = LedHandshakeInfo(pm=1)
        assert info.style is None

    def test_default_model_name(self):
        info = LedHandshakeInfo(pm=1)
        assert info.model_name == ""

    def test_all_fields_set(self):
        style = LED_STYLES[2]
        info = LedHandshakeInfo(
            pm=16,
            sub_type=5,
            style=style,
            model_name="PA120_DIGITAL",
        )
        assert info.pm == 16
        assert info.sub_type == 5
        assert info.style is style
        assert info.model_name == "PA120_DIGITAL"


# =========================================================================
# TestConstants — sanity checks on module-level constants
# =========================================================================

class TestLedConstants:
    """Verify LED device constants match the C# source values."""

    def test_led_vid(self):
        assert LED_VID == 0x0416

    def test_led_pid(self):
        assert LED_PID == 0x8001

    def test_led_magic(self):
        assert LED_MAGIC == bytes([0xDA, 0xDB, 0xDC, 0xDD])

    def test_led_header_size(self):
        assert LED_HEADER_SIZE == 20

    def test_led_cmd_init(self):
        assert LED_CMD_INIT == 1

    def test_led_cmd_data(self):
        assert LED_CMD_DATA == 2

    def test_hid_report_size(self):
        assert HID_REPORT_SIZE == 64

    def test_color_scale(self):
        assert LED_COLOR_SCALE == 0.4

    def test_send_cooldown(self):
        assert SEND_COOLDOWN_S == 0.030

    def test_led_init_size(self):
        assert LED_INIT_SIZE == 64

    def test_led_response_size(self):
        assert LED_RESPONSE_SIZE == 64

    def test_delay_pre_init(self):
        assert DELAY_PRE_INIT_S == 0.050

    def test_delay_post_init(self):
        assert DELAY_POST_INIT_S == 0.200

    def test_temp_gradient_last_stop_is_red(self):
        assert ColorEngine.TEMP_GRADIENT[-1][1] == (255, 0, 0)

    def test_temp_thresholds_length(self):
        assert len(ColorEngine.TEMP_GRADIENT) == 5

    def test_temp_thresholds_ascending(self):
        """Threshold values should be in ascending order."""
        values = [t[0] for t in ColorEngine.TEMP_GRADIENT]
        assert values == sorted(values)


# =========================================================================
# TestRemapLedColors — LED index remapping
# =========================================================================

class TestRemapLedColors:
    """Test LED color remapping from logical to physical wire order."""

    def test_unknown_style_returns_identity(self):
        """Styles without a remap table return colors unchanged."""
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        assert remap_led_colors(colors, style_id=1) is colors

    def test_style_2_table_length(self):
        """Style 2 (PA120_DIGITAL) remap table has exactly 84 entries."""
        assert len(LED_REMAP_TABLES[2]) == 84

    def test_style_3_table_length(self):
        """Style 3 (AK120_DIGITAL) remap table has exactly 64 entries."""
        assert len(LED_REMAP_TABLES[3]) == 64

    def test_style_4_table_length(self):
        """Style 4 (LC1) remap table has exactly 31 entries."""
        assert len(LED_REMAP_TABLES[4]) == 31

    def test_style_13_shares_style_4(self):
        """Style 13 (HR10) shares the same remap as style 4 (LC1)."""
        assert LED_REMAP_TABLES[13] is LED_REMAP_TABLES[4]

    def test_style_2_remaps_correctly(self):
        """Style 2 first physical positions get Cpu2 then Cpu1 colors."""
        # Logical indices: Cpu2=3, Cpu1=2
        colors = [(0, 0, 0)] * 84
        colors[2] = (10, 20, 30)   # Cpu1 at logical 2
        colors[3] = (40, 50, 60)   # Cpu2 at logical 3
        remapped = remap_led_colors(colors, style_id=2)
        assert remapped[0] == (40, 50, 60)  # Physical 0 = Cpu2
        assert remapped[1] == (10, 20, 30)  # Physical 1 = Cpu1

    def test_style_2_gpu_at_end(self):
        """Style 2 last two physical positions are Gpu1 and Gpu2."""
        colors = [(0, 0, 0)] * 84
        colors[4] = (100, 0, 0)    # Gpu1 at logical 4
        colors[5] = (0, 100, 0)    # Gpu2 at logical 5
        remapped = remap_led_colors(colors, style_id=2)
        assert remapped[82] == (100, 0, 0)  # Physical 82 = Gpu1
        assert remapped[83] == (0, 100, 0)  # Physical 83 = Gpu2

    def test_style_2_uniform_color_unchanged_count(self):
        """Uniform color (all same) remaps to same colors in different order."""
        color = (255, 0, 0)
        colors = [color] * 84
        remapped = remap_led_colors(colors, style_id=2)
        assert len(remapped) == 84
        assert all(c == color for c in remapped)

    def test_style_4_first_positions(self):
        """Style 4 (LC1) first physical positions: GNo=2, MTNo=1."""
        colors = [(0, 0, 0)] * 34  # Need up to index 37
        colors[1] = (10, 10, 10)   # MTNo at logical 1
        colors[2] = (20, 20, 20)   # GNo at logical 2
        remapped = remap_led_colors(colors, style_id=4)
        assert remapped[0] == (20, 20, 20)  # Physical 0 = GNo
        assert remapped[1] == (10, 10, 10)  # Physical 1 = MTNo

    def test_out_of_range_index_returns_black(self):
        """If remap table references an index beyond colors list, return black."""
        # Style 2 references index 81 (LEDC11), but we only provide 10 colors
        colors = [(255, 0, 0)] * 10
        remapped = remap_led_colors(colors, style_id=2)
        # Most entries will be black since indices > 9 aren't in colors
        assert len(remapped) == 84
        # Index 3 (Cpu2) → physical 0 should still have the color
        assert remapped[0] == (255, 0, 0)
        # Index 81 (LEDC11) → physical 25 should be black
        assert remapped[25] == (0, 0, 0)

    def test_remap_preserves_per_led_colors(self):
        """Each LED's color is placed at the correct physical position."""
        # Give each logical LED a unique color
        colors = [(i, i, i) for i in range(84)]
        remapped = remap_led_colors(colors, style_id=2)
        # Physical 0 should have logical 3's color (Cpu2)
        assert remapped[0] == (3, 3, 3)
        # Physical 2 should have logical 14's color (LEDF1)
        assert remapped[2] == (14, 14, 14)

    def test_all_remap_tables_match_led_count(self):
        """Each remap table length matches its style's led_count."""
        for style_id, table in LED_REMAP_TABLES.items():
            style = LED_STYLES[style_id]
            assert len(table) == style.led_count, (
                f"Style {style_id} ({style.model_name}): "
                f"remap has {len(table)} entries but led_count={style.led_count}"
            )

    def test_all_remap_indices_in_range(self):
        """All remap table indices are valid logical LED indices."""
        for style_id, table in LED_REMAP_TABLES.items():
            for i, idx in enumerate(table):
                assert 0 <= idx < 100, (
                    f"Style {style_id} position {i}: "
                    f"index {idx} out of range"
                )
