#!/usr/bin/env python3
"""
HID LED protocol layer for RGB LED controller devices (FormLED equivalent).

Device1 in Windows TRCC — VID 0x0416, PID 0x8001 — uses 64-byte HID reports
for RGB LED color control. The handshake uses the same DA/DB/DC/DD magic as
HID Type 2 LCD devices, but LED data packets use cmd=2 with per-LED RGB payload.

Protocol reverse-engineered from FormLED.cs and UCDevice.cs (TRCC 2.0.3).

The ``UsbTransport`` ABC from hid_device.py is reused for transport.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from .core.models import HandshakeResult
from .device_hid import (
    DEFAULT_TIMEOUT_MS,
    EP_READ_01,
    EP_WRITE_02,
    HANDSHAKE_MAX_RETRIES,
    HANDSHAKE_RETRY_DELAY_S,
    HANDSHAKE_TIMEOUT_MS,
    TYPE2_MAGIC,
    UsbTransport,
)

log = logging.getLogger(__name__)

# =========================================================================
# Constants (from FormLED.cs / UCDevice.cs)
# =========================================================================

# LED device VID/PID (device1 in UCDevice.cs)
LED_VID = 0x0416
LED_PID = 0x8001  # UsbHidDevice(1046, 32769, hidNameList1, 64)

# Handshake magic (same as HID Type 2, imported from hid_device)
LED_MAGIC = TYPE2_MAGIC

# Packet structure
LED_HEADER_SIZE = 20
LED_CMD_INIT = 1      # header[12] = 1 for handshake
LED_CMD_DATA = 2      # header[12] = 2 for LED data

# HID report size (UCDevice.cs: ThreadSendDeviceData1, 64-byte chunks)
HID_REPORT_SIZE = 64

# Color scaling factor (FormLED.cs SendHidVal: (float)(int)color * 0.4f)
LED_COLOR_SCALE = 0.4

# Timing (UCDevice.cs: Thread.Sleep(30) after ThreadSendDeviceData1 completes)
SEND_COOLDOWN_S = 0.030

# Handshake init packet size (device1 uses 64-byte reports, not 512)
LED_INIT_SIZE = 64
LED_RESPONSE_SIZE = 64

# Handshake timing (same as HID Type 2)
DELAY_PRE_INIT_S = 0.050    # Thread.Sleep(50) before init
DELAY_POST_INIT_S = 0.200   # Thread.Sleep(200) after init


# =========================================================================
# Device styles (from FormLED.cs FormLEDInit, lines 1598-1750)
# =========================================================================

@dataclass
class LedDeviceStyle:
    """LED device configuration derived from FormLEDInit pm→nowLedStyle.

    Attributes:
        style_id: Internal style number (nowLedStyle in Windows).
        led_count: Total addressable LEDs (LedCountValN).
        segment_count: Logical segments (LedCountValNs).
        zone_count: Number of independent zones (1=single, 2-4=multi).
        model_name: Human-readable model name.
        preview_image: Device background asset name (D{Name}.png).
        background_base: Localized background base (D0{Name}).
    """
    style_id: int
    led_count: int
    segment_count: int
    zone_count: int = 1
    model_name: str = ""
    preview_image: str = ""
    background_base: str = "D0数码屏"


# All LED styles from FormLED.cs FormLEDInit and UCScreenLED.cs constants
LED_STYLES = {
    1: LedDeviceStyle(1, 30, 10, 1, "AX120_DIGITAL", "DAX120_DIGITAL", "D0数码屏"),
    2: LedDeviceStyle(2, 84, 18, 4, "PA120_DIGITAL", "DPA120_DIGITAL", "D0数码屏4区域"),
    3: LedDeviceStyle(3, 64, 10, 2, "AK120_DIGITAL", "DAK120_DIGITAL", "D0数码屏"),
    4: LedDeviceStyle(4, 31, 14, 1, "LC1", "DLC1", "D0LC1"),
    5: LedDeviceStyle(5, 93, 23, 2, "LF8", "DLF8", "D0LF8"),
    6: LedDeviceStyle(6, 124, 72, 2, "LF12", "DLF12", "D0LF12"),
    7: LedDeviceStyle(7, 116, 12, 3, "LF10", "DLF10", "D0LF10"),
    8: LedDeviceStyle(8, 18, 13, 4, "CZ1", "DCZ1", "D0CZ1"),
    9: LedDeviceStyle(9, 61, 31, 1, "LC2", "DLC2", "D0LC2"),
    10: LedDeviceStyle(10, 38, 17, 1, "LF11", "DLF11", "D0LF11"),
    11: LedDeviceStyle(11, 93, 72, 2, "LF15", "DLF15", "D0LF15"),
    12: LedDeviceStyle(12, 62, 62, 1, "LF13", "DLF13", "D0rgblf13"),
    # HR10 2280 Pro Digital — NVMe SSD heatsink with ARGB digital display.
    # Shares PM=128 and LED config with LC1 (31 LEDs, 14 segments, 1 zone).
    # Distinguished from LC1 by sub_type=129 in handshake response.
    13: LedDeviceStyle(13, 31, 14, 1, "HR10_2280_PRO_DIGITAL", "DAX120_DIGITAL", "D0数码屏"),
}

# -------------------------------------------------------------------------
# PM registry — single source of truth for PM → (style, model, button image)
# -------------------------------------------------------------------------

class PmEntry(NamedTuple):
    """PM registry entry mapping a firmware PM byte to device metadata."""
    style_id: int
    model_name: str
    button_image: str
    preview_image: str = ""  # PM-specific preview; empty = use style default


class PmRegistry:
    """Encapsulates all PM-to-device metadata lookups.

    Maps firmware PM bytes (from HID handshake) to device style, model name,
    and button image.  Handles sub-type overrides (e.g. HR10 vs LC1 on PM=128)
    and PA120 variant range (PMs 17-31).
    """

    # (pm, sub_type) → PmEntry override for devices that share a PM byte.
    _OVERRIDES: dict[tuple[int, int], PmEntry] = {
        (128, 129): PmEntry(13, "HR10_2280_PRO_DIGITAL", "A1HR10 2280 PRO DIGITAL"),
    }

    # PM → PmEntry base registry (built once at class load time).
    _REGISTRY: dict[int, PmEntry] = {
        1:   PmEntry(1, "FROZEN_HORIZON_PRO", "A1FROZEN HORIZON PRO", "DFROZEN_HORIZON_PRO"),
        2:   PmEntry(1, "FROZEN_MAGIC_PRO", "A1FROZEN MAGIC PRO", "DFROZEN_MAGIC_PRO"),
        3:   PmEntry(1, "AX120_DIGITAL", "A1AX120 DIGITAL"),
        16:  PmEntry(2, "PA120_DIGITAL", "A1PA120 DIGITAL"),
        23:  PmEntry(2, "RK120_DIGITAL", "A1RK120 DIGITAL"),
        32:  PmEntry(3, "AK120_DIGITAL", "A1AK120 Digital"),
        48:  PmEntry(5, "LF8", "A1LF8"),
        49:  PmEntry(5, "LF10", "A1LF10"),
        80:  PmEntry(6, "LF12", "A1LF12"),
        96:  PmEntry(7, "LF10", "A1LF10"),
        112: PmEntry(9, "LC2", "A1LC2"),
        128: PmEntry(4, "LC1", "A1LC1"),
        129: PmEntry(10, "LF11", "A1LF11"),
        144: PmEntry(11, "LF15", "A1LF15"),
        160: PmEntry(12, "LF13", "A1LF13"),
        208: PmEntry(8, "CZ1", "A1CZ1"),
        # PA120 variants (PMs 17-22, 24-31) all map to style 2.
        **{pm: PmEntry(2, "PA120_DIGITAL", "A1PA120 DIGITAL")
           for pm in range(17, 32) if pm not in (23,)},
    }

    # PM → style_id convenience mapping (used by cli.py, debug_report.py).
    PM_TO_STYLE: dict[int, int] = {pm: e.style_id for pm, e in _REGISTRY.items()}

    @classmethod
    def resolve(cls, pm: int, sub_type: int = 0) -> Optional[PmEntry]:
        """Resolve PM + SUB to a PmEntry, checking overrides first."""
        return cls._OVERRIDES.get((pm, sub_type)) or cls._REGISTRY.get(pm)

    @classmethod
    def get_button_image(cls, pm: int, sub: int = 0) -> Optional[str]:
        """Resolve LED device button image from PM byte.

        Returns None if PM is unknown.
        """
        entry = cls.resolve(pm, sub)
        return entry.button_image if entry else None

    @classmethod
    def get_model_name(cls, pm: int, sub_type: int = 0) -> str:
        """Get human-readable model name for a PM + SUB byte combo.

        Checks overrides first (e.g. HR10 vs LC1), then base registry.
        Falls back to "Unknown (pm=N)" for unrecognized PMs.
        """
        entry = cls.resolve(pm, sub_type)
        return entry.model_name if entry else f"Unknown (pm={pm})"

    @classmethod
    def get_style(cls, pm: int, sub_type: int = 0) -> LedDeviceStyle:
        """Get LED device style from firmware PM byte (and optional sub_type).

        Falls back to style 1 (30 LEDs) for unknown PM values.
        """
        entry = cls.resolve(pm, sub_type)
        return LED_STYLES[entry.style_id if entry else 1]

    @classmethod
    def get_preview_image(cls, pm: int, sub_type: int = 0) -> str:
        """Get device preview image name, PM-specific or style default."""
        entry = cls.resolve(pm, sub_type)
        if entry and entry.preview_image:
            return entry.preview_image
        style = cls.get_style(pm, sub_type)
        return style.preview_image


# Preset colors from FormLED.cs ucColor1_ChangeColor handlers
# Note: Windows ucColor1Delegate has swapped B,G params (R,B,G order)
PRESET_COLORS: List[Tuple[int, int, int]] = [
    (255, 0, 42),     # C1: Red-pink
    (255, 110, 0),    # C2: Orange
    (255, 255, 0),    # C3: Yellow
    (0, 255, 0),      # C4: Green
    (0, 255, 255),    # C5: Cyan
    (0, 91, 255),     # C6: Blue
    (214, 0, 255),    # C7: Purple
    (255, 255, 255),  # C8: White
]


# =========================================================================
# LED index remapping tables (from FormLED.cs SendHidVal)
# =========================================================================
# Each style has a hardware-specific mapping from logical LED indices to
# physical wire positions.  Windows builds colors in logical order, then
# remaps before sending.  Without this step, colors land on wrong LEDs.
#
# Table format: tuple of logical LED indices, one per physical position.
# Physical position i on the device wire gets colors from logical LED table[i].

# Style 2: PA120_DIGITAL (84 LEDs, 4 zones)
_REMAP_STYLE_2: tuple[int, ...] = (
    # Cpu2 Cpu1 | Zone 1: F A B G E D C
    3, 2, 14, 9, 10, 15, 13, 12, 11,
    # Zone 2: F A B G E D C
    21, 16, 17, 22, 20, 19, 18,
    # Zone 3: F A B G E D C
    28, 23, 24, 29, 27, 26, 25,
    # Zone 4: F A B G E D C
    36, 31, 32, 37, 35, 34, 33,
    # Zone 5: F A B G E D C
    43, 38, 39, 44, 42, 41, 40,
    # BFB BFB1
    8, 8,
    # Zone 10 (reversed): C D E G B A F
    75, 76, 77, 79, 74, 73, 78,
    # Zone 9 (reversed): C D E G B A F
    68, 69, 70, 72, 67, 66, 71,
    # B12 C12 SSD1 HSD1
    82, 83, 6, 7,
    # Zone 8 (reversed): C D E G B A F
    61, 62, 63, 65, 60, 59, 64,
    # Zone 7 (reversed): C D E G B A F
    54, 55, 56, 58, 53, 52, 57,
    # Zone 6 (reversed): C D E G B A F
    47, 48, 49, 51, 46, 45, 50,
    # Gpu1 Gpu2 SSD HSD C11 B11
    4, 5, 6, 7, 81, 80,
)

# Style 3: AK120_DIGITAL (64 LEDs, 2 zones)
_REMAP_STYLE_3: tuple[int, ...] = (
    # WATT | Zone 3: C D E G B A F
    1, 25, 26, 27, 29, 24, 23, 28,
    # B2 Cpu1 | Zone 2: A F G C D E
    17, 2, 16, 21, 22, 18, 19, 20,
    # Zone 1: B A F G C D E
    10, 9, 14, 15, 11, 12, 13,
    # Zone 4: F A B G E D C
    36, 31, 32, 37, 35, 34, 33,
    # Zone 5: F A B G E D C
    43, 38, 39, 44, 42, 41, 40,
    # Zone 6: F A B G E D C
    50, 45, 46, 51, 49, 48, 47,
    # SSD HSD BFB
    6, 7, 8,
    # Zone 8: C D E G B A F
    61, 62, 63, 65, 60, 59, 64,
    # C7 Gpu1 | Zone 7: D E G B A F
    54, 4, 55, 56, 58, 53, 52, 57,
    # B9 C9
    67, 68,
)

# Style 4: LC1 (31 LEDs, 1 zone) — also base for HR10 (style 13)
_REMAP_STYLE_4: tuple[int, ...] = (
    # GNo MTNo | Zone 4: C D E G B A | SSD
    2, 1, 33, 34, 35, 37, 32, 31, 6,
    # F4 | Zone 3: C D E G B A F
    36, 25, 26, 27, 29, 24, 23, 28,
    # Zone 2: C D E G B A F
    18, 19, 20, 22, 17, 16, 21,
    # Zone 1: C D E G B A F
    11, 12, 13, 15, 10, 9, 14,
)

# Style → remap table.  Styles not listed use identity mapping (no remap).
LED_REMAP_TABLES: dict[int, tuple[int, ...]] = {
    2: _REMAP_STYLE_2,   # PA120_DIGITAL (84 LEDs)
    3: _REMAP_STYLE_3,   # AK120_DIGITAL (64 LEDs)
    4: _REMAP_STYLE_4,   # LC1 (31 LEDs)
    13: _REMAP_STYLE_4,  # HR10 shares LC1 layout
}


def remap_led_colors(
    colors: List[Tuple[int, int, int]],
    style_id: int,
) -> List[Tuple[int, int, int]]:
    """Remap LED colors from logical to physical wire order.

    Each LED device style has a hardware-specific mapping from logical LED
    indices (used by the GUI) to physical wire positions (sent to device).
    Windows applies this remap in FormLED.cs SendHidVal before sending.

    Args:
        colors: LED colors in logical order (index = logical LED number).
        style_id: Device style ID (from LedDeviceStyle.style_id).

    Returns:
        Colors reordered for the physical device wire.  If no remap table
        exists for this style, returns the input unchanged.
    """
    table = LED_REMAP_TABLES.get(style_id)
    if table is None:
        return colors
    black = (0, 0, 0)
    return [colors[idx] if idx < len(colors) else black for idx in table]


# =========================================================================
# Color engine — RGB rainbow table + sensor-to-color gradient mapping
# =========================================================================

class ColorEngine:
    """Encapsulates all LED color computation.

    - 768-entry RGB rainbow table (FormLED.cs RGBTable[768, 3])
    - Temperature/load → color gradient with smooth interpolation
    """

    # Gradient stops: (value, (R, G, B)) — linearly interpolated between stops.
    TEMP_GRADIENT: List[Tuple[float, Tuple[int, int, int]]] = [
        (30, (0, 255, 255)),    # Cyan
        (50, (0, 255, 0)),      # Green
        (70, (255, 255, 0)),    # Yellow
        (90, (255, 110, 0)),    # Orange
        (100, (255, 0, 0)),     # Red
    ]

    LOAD_GRADIENT = TEMP_GRADIENT  # Same gradient (0-100%)

    _cached_table: Optional[List[Tuple[int, int, int]]] = None

    @staticmethod
    def generate_table() -> List[Tuple[int, int, int]]:
        """Generate the 768-entry RGB rainbow lookup table.

        Matches FormLED.cs RGBTable initialization — smooth HSV hue cycle
        through 768 steps covering all rainbow colors.

        The table cycles through:
            0-127:   Red→Yellow     (R=255, G increases 0→255)
            128-255: Yellow→Green   (R decreases 255→0, G=255)
            256-383: Green→Cyan     (G=255, B increases 0→255)
            384-511: Cyan→Blue      (G decreases 255→0, B=255)
            512-639: Blue→Magenta   (R increases 0→255, B=255)
            640-767: Magenta→Red    (B decreases 255→0, R=255)
        """
        table = []
        phase_len = 128  # 768 / 6 phases

        for i in range(768):
            phase = i // phase_len
            offset = i % phase_len
            t = int(255 * offset / (phase_len - 1)) if phase_len > 1 else 0

            if phase == 0:    # Red → Yellow
                r, g, b = 255, t, 0
            elif phase == 1:  # Yellow → Green
                r, g, b = 255 - t, 255, 0
            elif phase == 2:  # Green → Cyan
                r, g, b = 0, 255, t
            elif phase == 3:  # Cyan → Blue
                r, g, b = 0, 255 - t, 255
            elif phase == 4:  # Blue → Magenta
                r, g, b = t, 0, 255
            else:             # Magenta → Red
                r, g, b = 255, 0, 255 - t

            table.append((r, g, b))

        return table

    @classmethod
    def get_table(cls) -> List[Tuple[int, int, int]]:
        """Get the cached 768-entry RGB rainbow table."""
        if cls._cached_table is None:
            cls._cached_table = cls.generate_table()
        return cls._cached_table

    @staticmethod
    def _lerp(
        c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float,
    ) -> Tuple[int, int, int]:
        """Linearly interpolate between two RGB colors (t=0->c1, t=1->c2)."""
        t = max(0.0, min(1.0, t))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    @staticmethod
    def color_for_value(
        value: float,
        gradient: List[Tuple[float, Tuple[int, int, int]]],
        high_color: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[int, int, int]:
        """Map a sensor value to an RGB color with smooth gradient interpolation.

        Linearly interpolates between adjacent gradient stops.
        Clamps to first/last color outside the gradient range.

        Args:
            value: Sensor reading (temperature C, load %, etc.).
            gradient: List of (threshold, (R, G, B)) stops.
            high_color: Ignored (backward compat). Last gradient stop used instead.
        """
        if value <= gradient[0][0]:
            return gradient[0][1]
        if value >= gradient[-1][0]:
            return gradient[-1][1]

        for i in range(len(gradient) - 1):
            lo_val, lo_color = gradient[i]
            hi_val, hi_color = gradient[i + 1]
            if lo_val <= value <= hi_val:
                t = (value - lo_val) / (hi_val - lo_val)
                return ColorEngine._lerp(lo_color, hi_color, t)

        return gradient[-1][1]




# =========================================================================
# Handshake response
# =========================================================================

@dataclass
class LedHandshakeInfo(HandshakeResult):
    """LED-specific handshake info (extends HandshakeResult).

    Attributes:
        pm: Product model byte (raw resp[5], Windows data[6]) — identifies device type.
        sub_type: Sub-type byte (raw resp[4], Windows data[5]).
        style: Resolved LedDeviceStyle for this device.
        model_name: Human-readable model name.
    """
    pm: int = 0
    sub_type: int = 0
    style: Optional[LedDeviceStyle] = None
    model_name: str = ""


# =========================================================================
# Packet builder (from FormLED.cs SendHidVal)
# =========================================================================

class LedPacketBuilder:
    """Builds LED HID packets matching FormLED.cs SendHidVal.

    Packet structure:
        [20-byte header] + [N * 3 bytes RGB payload]

    Header layout (from FormLED.cs SendHidVal, line 4309):
        Bytes 0-3:   0xDA, 0xDB, 0xDC, 0xDD  (magic)
        Bytes 4-11:  0x00 * 8                  (reserved)
        Byte  12:    command (1=init, 2=LED data)
        Bytes 13-15: 0x00 * 3                  (reserved)
        Bytes 16-17: payload length (little-endian uint16)
        Bytes 18-19: 0x00 * 2                  (reserved)

    RGB payload: N LEDs × 3 bytes (R, G, B), each scaled by 0.4.
    """

    @staticmethod
    def build_header(payload_length: int) -> bytes:
        """Build the 20-byte LED packet header.

        Args:
            payload_length: Length of RGB payload in bytes.

        Returns:
            20-byte header.
        """
        header = bytearray(LED_HEADER_SIZE)
        # Magic bytes
        header[0:4] = LED_MAGIC
        # Command = LED data
        header[12] = LED_CMD_DATA
        # Payload length (little-endian uint16)
        header[16] = payload_length & 0xFF
        header[17] = (payload_length >> 8) & 0xFF
        return bytes(header)

    @staticmethod
    def build_init_packet() -> bytes:
        """Build the handshake init packet (cmd=1).

        Same as HidDeviceType2 init but in a 64-byte packet:
            [0xDA, 0xDB, 0xDC, 0xDD, 0*8, 0x01, 0*7]
        Padded to HID_REPORT_SIZE (64 bytes).
        """
        header = bytearray(HID_REPORT_SIZE)
        header[0:4] = LED_MAGIC
        header[12] = LED_CMD_INIT
        return bytes(header)

    @staticmethod
    def build_led_packet(
        led_colors: List[Tuple[int, int, int]],
        is_on: Optional[List[bool]] = None,
        global_on: bool = True,
        brightness: int = 100,
    ) -> bytes:
        """Build complete LED data packet from per-LED RGB colors.

        Args:
            led_colors: List of (R, G, B) tuples, one per LED.
            is_on: Per-LED on/off state. None means all on.
            global_on: Global on/off switch. False → all LEDs off.
            brightness: Global brightness 0-100 (applied as multiplier).

        Returns:
            Complete packet (header + RGB payload) ready for chunking.
        """
        led_count = len(led_colors)
        payload_length = led_count * 3
        header = LedPacketBuilder.build_header(payload_length)

        brightness_factor = max(0, min(100, brightness)) / 100.0

        payload = bytearray(payload_length)
        for i, (r, g, b) in enumerate(led_colors):
            led_is_on = global_on and (is_on[i] if is_on is not None else True)

            if led_is_on:
                # Apply brightness and 0.4x scaling (FormLED.cs SendHidVal)
                scaled_r = int(r * brightness_factor * LED_COLOR_SCALE)
                scaled_g = int(g * brightness_factor * LED_COLOR_SCALE)
                scaled_b = int(b * brightness_factor * LED_COLOR_SCALE)
                payload[i * 3] = min(255, max(0, scaled_r))
                payload[i * 3 + 1] = min(255, max(0, scaled_g))
                payload[i * 3 + 2] = min(255, max(0, scaled_b))
            # else: remains 0,0,0 (off)

        return header + bytes(payload)


# =========================================================================
# LED HID sender (from UCDevice.cs ThreadSendDeviceData1)
# =========================================================================

class LedHidSender:
    """Sends LED packets via UsbTransport with 64-byte report chunking.

    Matches UCDevice.cs ThreadSendDeviceData1 (lines 983-1026):
    - Splits packet into 64-byte HID reports
    - Thread.Sleep(30) cooldown after complete send
    - Concurrent-send guard (isSendUsbThread0)
    """

    def __init__(self, transport: UsbTransport):
        self._transport = transport
        self._sending = False

    def handshake(self) -> LedHandshakeInfo:
        """Perform LED device handshake with retry.

        Sends init packet (cmd=1), reads response, extracts pm byte.
        Retries up to HANDSHAKE_MAX_RETRIES times.

        Windows DeviceDataReceived1() does NOT validate magic or command
        bytes in the response — it accepts any non-empty response. We
        warn but still accept responses with unexpected magic/command.

        Returns:
            LedHandshakeInfo with pm, sub_type, and resolved style.

        Raises:
            RuntimeError: If handshake fails after all retries.
        """
        init_pkt = LedPacketBuilder.build_init_packet()
        last_err: Optional[Exception] = None

        for attempt in range(1, HANDSHAKE_MAX_RETRIES + 1):
            try:
                time.sleep(DELAY_PRE_INIT_S)
                self._transport.write(EP_WRITE_02, init_pkt, HANDSHAKE_TIMEOUT_MS)
                time.sleep(DELAY_POST_INIT_S)

                resp = self._transport.read(
                    EP_READ_01, LED_RESPONSE_SIZE, HANDSHAKE_TIMEOUT_MS,
                )

                if len(resp) < 7:
                    log.warning(
                        "LED handshake attempt %d/%d: response too short (%d bytes)",
                        attempt, HANDSHAKE_MAX_RETRIES, len(resp),
                    )
                    last_err = RuntimeError(
                        f"LED handshake failed: response too short ({len(resp)} bytes)"
                    )
                    time.sleep(HANDSHAKE_RETRY_DELAY_S)
                    continue

                # Warn but don't reject if magic doesn't match
                # (Windows DeviceDataReceived1 doesn't validate magic)
                if resp[0:4] != LED_MAGIC:
                    log.warning(
                        "LED handshake: unexpected magic (got %s, expected %s)",
                        resp[0:4].hex(), LED_MAGIC.hex(),
                    )
                if len(resp) > 12 and resp[12] != 1:
                    log.warning(
                        "LED handshake: unexpected cmd byte (got %d, expected 1)",
                        resp[12],
                    )

                # PM and SUB extraction — matches Windows UCDevice.cs offsets.
                # Windows HID API prepends Report ID at data[0], so:
                #   data[6] = raw resp[5] = PM (product model byte)
                #   data[5] = raw resp[4] = SUB (sub-variant byte)
                # Previous code used resp[6]/resp[5] (off by one) which read
                # zeros on AX120 devices (shadowepaxeor-glitch PM=0 was wrong).
                pm = resp[5]
                sub_type = resp[4]
                style = PmRegistry.get_style(pm, sub_type)
                model_name = PmRegistry.get_model_name(pm, sub_type)

                return LedHandshakeInfo(
                    model_id=pm,
                    pm=pm,
                    sub_type=sub_type,
                    style=style,
                    model_name=model_name,
                    raw_response=bytes(resp[:64]),
                )

            except Exception as e:
                log.warning(
                    "LED handshake attempt %d/%d failed: %s",
                    attempt, HANDSHAKE_MAX_RETRIES, e,
                )
                last_err = e
                if attempt < HANDSHAKE_MAX_RETRIES:
                    time.sleep(HANDSHAKE_RETRY_DELAY_S)

        raise last_err or RuntimeError(
            f"LED handshake failed after {HANDSHAKE_MAX_RETRIES} attempts"
        )

    def send_led_data(self, packet: bytes) -> bool:
        """Send an LED data packet, chunked into 64-byte HID reports.

        Args:
            packet: Complete LED packet (header + RGB payload).

        Returns:
            True if all chunks were sent successfully.
        """
        if self._sending:
            return False

        self._sending = True
        try:
            remaining = len(packet)
            offset = 0

            while remaining > 0:
                chunk_size = min(remaining, HID_REPORT_SIZE)
                chunk = packet[offset:offset + chunk_size]

                # Pad last chunk to report size if needed
                if len(chunk) < HID_REPORT_SIZE:
                    chunk = chunk + b'\x00' * (HID_REPORT_SIZE - len(chunk))

                self._transport.write(EP_WRITE_02, chunk, DEFAULT_TIMEOUT_MS)
                remaining -= chunk_size
                offset += chunk_size

            # Cooldown after send (UCDevice.cs Thread.Sleep(30))
            time.sleep(SEND_COOLDOWN_S)
            return True

        except Exception:
            return False
        finally:
            self._sending = False

    @property
    def is_sending(self) -> bool:
        """Whether a send is currently in progress."""
        return self._sending

    def close(self) -> None:
        """Release resources (transport is managed externally)."""
        self._sending = False


# =========================================================================
# Public API
# =========================================================================

def send_led_colors(
    transport: UsbTransport,
    led_colors: List[Tuple[int, int, int]],
    is_on: Optional[List[bool]] = None,
    global_on: bool = True,
    brightness: int = 100,
) -> bool:
    """Build and send LED color data to an LED device.

    Convenience function combining LedPacketBuilder and LedHidSender.

    Args:
        transport: Open USB transport to the device.
        led_colors: List of (R, G, B) tuples, one per LED.
        is_on: Per-LED on/off state. None means all on.
        global_on: Global on/off switch.
        brightness: Global brightness 0-100.

    Returns:
        True if the send succeeded.
    """
    packet = LedPacketBuilder.build_led_packet(
        led_colors, is_on, global_on, brightness
    )
    sender = LedHidSender(transport)
    return sender.send_led_data(packet)


# =========================================================================
# LED probe cache — persists handshake results across restarts
# =========================================================================
# The firmware only responds to the HID handshake once per power cycle.
# Caching the result avoids consuming the one-shot handshake during
# detection, so the actual LedProtocol.handshake() still works.


class _LedProbeCache:
    """Disk-backed cache for LED handshake results.

    Keyed by VID:PID:usb_path so multiple identical-PID devices
    (e.g. LC1 + HR10) are disambiguated by bus position.
    """

    @staticmethod
    def _path() -> Path:
        config_dir = Path.home() / '.config' / 'trcc'
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / 'led_probe_cache.json'

    @staticmethod
    def _key(vid: int, pid: int, usb_path: str = '') -> str:
        if usb_path:
            return f"{vid:04x}_{pid:04x}_{usb_path}"
        return f"{vid:04x}_{pid:04x}"

    @classmethod
    def save(cls, vid: int, pid: int, info: LedHandshakeInfo,
             usb_path: str = '') -> None:
        """Cache a successful probe result to disk."""
        import json
        try:
            cache_path = cls._path()
            cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
            cache[cls._key(vid, pid, usb_path)] = {
                'pm': info.pm,
                'sub_type': info.sub_type,
                'model_name': info.model_name,
                'style_id': info.style.style_id if info.style else 1,
            }
            cache_path.write_text(json.dumps(cache))
        except Exception as e:
            log.debug("Failed to save probe cache: %s", e)

    @classmethod
    def load(cls, vid: int, pid: int,
             usb_path: str = '') -> Optional[LedHandshakeInfo]:
        """Load a cached probe result from disk."""
        import json
        try:
            cache_path = cls._path()
            if not cache_path.exists():
                return None
            cache = json.loads(cache_path.read_text())
            # Try bus-path-specific key first, then fall back to VID:PID-only
            entry = cache.get(cls._key(vid, pid, usb_path))
            if not entry and usb_path:
                entry = cache.get(cls._key(vid, pid))
            if not entry:
                return None
            pm = entry['pm']
            sub_type = entry['sub_type']
            return LedHandshakeInfo(
                pm=pm,
                sub_type=sub_type,
                style=PmRegistry.get_style(pm, sub_type),
                model_name=entry['model_name'],
            )
        except Exception as e:
            log.debug("Failed to load probe cache: %s", e)
            return None


def probe_led_model(vid: int = LED_VID, pid: int = LED_PID,
                    usb_path: str = '') -> Optional[LedHandshakeInfo]:
    """Probe an LED device to discover its model via HID handshake.

    Checks the disk cache first (keyed by VID:PID:bus_path).  Only
    performs a live USB handshake when no cached result exists, since
    the firmware only responds to the handshake once per power cycle.

    Args:
        vid: USB vendor ID.
        pid: USB product ID.
        usb_path: USB bus path (e.g. "2-1.4") for cache disambiguation.

    Returns:
        LedHandshakeInfo with pm, sub_type, style, and model_name,
        or None if the probe fails and no cached result exists.
    """
    # Cache-first: avoid consuming the one-shot handshake unnecessarily.
    cached = _LedProbeCache.load(vid, pid, usb_path)
    if cached is not None:
        return cached

    transport = None
    try:
        from .device_factory import DeviceProtocolFactory
        transport = DeviceProtocolFactory.create_usb_transport(vid, pid)
        transport.open()
        sender = LedHidSender(transport)
        info = sender.handshake()
        if info:
            _LedProbeCache.save(vid, pid, info, usb_path)
        return info
    except Exception as e:
        log.debug("LED probe failed for %04x:%04x: %s", vid, pid, e)
        return None
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
