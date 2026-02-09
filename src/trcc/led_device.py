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
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

from .hid_device import (
    DEFAULT_TIMEOUT_MS,
    EP_READ_01,
    EP_WRITE_02,
    TYPE2_MAGIC,
    UsbTransport,
)

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

# pm byte (from firmware handshake receive[6]) → style mapping
# From FormLEDInit: if (NO == 1) nowLedStyle = 1, if (NO == 16) nowLedStyle = 2, etc.
PM_TO_STYLE = {
    1: 1,    # FROZEN_HORIZON_PRO → style 1
    2: 1,    # FROZEN_MAGIC_PRO → style 1
    3: 1,    # AX120_DIGITAL → style 1
    16: 2,   # PA120_DIGITAL → style 2
    17: 2, 18: 2, 19: 2, 20: 2, 21: 2, 22: 2, 23: 2,  # PA120 variants
    24: 2, 25: 2, 26: 2, 27: 2, 28: 2, 29: 2, 30: 2, 31: 2,
    32: 3,   # AK120_DIGITAL → style 3
    48: 5,   # LF8 → style 5
    49: 5,   # LF8 variant → style 5
    80: 6,   # LF12 → style 6
    96: 7,   # LF10 → style 7
    112: 9,  # LC2 → style 9
    128: 4,  # LC1 → style 4 (also HR10, disambiguated by sub_type)
    129: 10, # LF11 → style 10
    144: 11, # LF15 → style 11
    160: 12, # LF13 → style 12
    208: 8,  # CZ1 → style 8
}

# pm byte → model name (for device button images)
PM_TO_MODEL = {
    1: "FROZEN_HORIZON_PRO",
    2: "FROZEN_MAGIC_PRO",
    3: "AX120_DIGITAL",
    16: "PA120_DIGITAL",
    23: "RK120_DIGITAL",
    32: "AK120_DIGITAL",
    48: "LF8",
    49: "LF10",  # LF10 variant via LF8 group
    80: "LF12",
    96: "LF10",
    112: "LC2",
    128: "LC1",
    129: "LF11",
    144: "LF15",
    160: "LF13",
    208: "CZ1",
}

# (pm, sub_type) → style override for devices that share a PM byte.
# HR10 2280 Pro Digital shares PM=128 with LC1 but has sub_type=129.
SUB_TYPE_OVERRIDES = {
    (128, 129): (13, "HR10_2280_PRO_DIGITAL"),  # → style 13
}

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


def get_style_for_pm(pm: int, sub_type: int = 0) -> LedDeviceStyle:
    """Get LED device style from firmware pm byte (and optional sub_type).

    Some devices share a PM byte (e.g. LC1 and HR10 both use PM=128).
    The sub_type disambiguates them via SUB_TYPE_OVERRIDES.

    Falls back to style 1 (30 LEDs) for unknown pm values.
    """
    # Check sub_type overrides first (e.g. HR10 vs LC1)
    override = SUB_TYPE_OVERRIDES.get((pm, sub_type))
    if override:
        style_id = override[0]
        return LED_STYLES[style_id]
    style_id = PM_TO_STYLE.get(pm, 1)
    return LED_STYLES[style_id]


# =========================================================================
# RGB Rainbow Table (from FormLED.cs RGBTable[768, 3])
# =========================================================================

def generate_rgb_table() -> List[Tuple[int, int, int]]:
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


# Module-level cached table
_RGB_TABLE: Optional[List[Tuple[int, int, int]]] = None


def get_rgb_table() -> List[Tuple[int, int, int]]:
    """Get the cached 768-entry RGB rainbow table."""
    global _RGB_TABLE
    if _RGB_TABLE is None:
        _RGB_TABLE = generate_rgb_table()
    return _RGB_TABLE


# =========================================================================
# Temperature/Load → Color thresholds (FormLED.cs WDLD_Timer / FZLD_Timer)
# =========================================================================

# Thresholds: (max_value, (R, G, B))
TEMP_COLOR_THRESHOLDS = [
    (30, (0, 255, 255)),    # < 30°C → Cyan
    (50, (0, 255, 0)),      # 30-49°C → Green
    (70, (255, 255, 0)),    # 50-69°C → Yellow
    (90, (255, 110, 0)),    # 70-89°C → Orange
]
TEMP_COLOR_HIGH = (255, 0, 0)  # ≥ 90°C → Red

LOAD_COLOR_THRESHOLDS = TEMP_COLOR_THRESHOLDS  # Same thresholds (0-100%)
LOAD_COLOR_HIGH = TEMP_COLOR_HIGH


def color_for_value(value: float, thresholds: list, high_color: tuple) -> Tuple[int, int, int]:
    """Map a sensor value to an RGB color using thresholds.

    Used by both temperature-linked and load-linked modes.
    """
    for threshold, color in thresholds:
        if value < threshold:
            return color
    return high_color


# =========================================================================
# Handshake response
# =========================================================================

@dataclass
class LedHandshakeInfo:
    """Information extracted from LED device handshake response.

    Attributes:
        pm: Product model byte (receive[6]) — identifies device type.
        sub_type: Sub-type byte (receive[5]).
        style: Resolved LedDeviceStyle for this device.
        model_name: Human-readable model name.
    """
    pm: int
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
        """Perform LED device handshake.

        Sends init packet (cmd=1), reads response, extracts pm byte.

        Returns:
            LedHandshakeInfo with pm, sub_type, and resolved style.

        Raises:
            RuntimeError: If handshake fails.
        """
        init_pkt = LedPacketBuilder.build_init_packet()

        time.sleep(DELAY_PRE_INIT_S)
        self._transport.write(EP_WRITE_02, init_pkt, DEFAULT_TIMEOUT_MS)
        time.sleep(DELAY_POST_INIT_S)

        resp = self._transport.read(EP_READ_01, LED_RESPONSE_SIZE, DEFAULT_TIMEOUT_MS)

        if len(resp) < 20:
            raise RuntimeError(f"LED handshake failed: response too short ({len(resp)} bytes)")

        # Validate magic echo (same as HID Type 2)
        if resp[0:4] != LED_MAGIC:
            raise RuntimeError(
                f"LED handshake failed: bad magic "
                f"(got {resp[0:4].hex()}, expected {LED_MAGIC.hex()})"
            )

        if resp[12] != 1:
            raise RuntimeError(f"LED handshake failed: bad cmd byte (got {resp[12]}, expected 1)")

        pm = resp[6]
        sub_type = resp[5]
        style = get_style_for_pm(pm, sub_type)

        # Check sub_type override for model name (e.g. HR10 vs LC1)
        override = SUB_TYPE_OVERRIDES.get((pm, sub_type))
        if override:
            model_name = override[1]
        else:
            model_name = PM_TO_MODEL.get(pm, f"Unknown (pm={pm})")

        return LedHandshakeInfo(
            pm=pm,
            sub_type=sub_type,
            style=style,
            model_name=model_name,
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


def _led_probe_cache_path() -> Path:
    """Return the path to the LED probe cache file."""
    config_dir = Path.home() / '.config' / 'trcc'
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'led_probe_cache.json'


def _probe_cache_key(vid: int, pid: int, usb_path: str = '') -> str:
    """Build a cache key that disambiguates devices sharing VID:PID.

    LC1 and HR10 both use 0416:8001.  When both are connected, the USB
    bus path (e.g. "2-1.4") distinguishes them.
    """
    if usb_path:
        return f"{vid:04x}_{pid:04x}_{usb_path}"
    return f"{vid:04x}_{pid:04x}"


def _save_probe_cache(vid: int, pid: int, info: LedHandshakeInfo,
                      usb_path: str = '') -> None:
    """Cache a successful probe result to disk."""
    import json
    try:
        cache_path = _led_probe_cache_path()
        cache = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
        key = _probe_cache_key(vid, pid, usb_path)
        cache[key] = {
            'pm': info.pm,
            'sub_type': info.sub_type,
            'model_name': info.model_name,
            'style_id': info.style.style_id if info.style else 1,
        }
        cache_path.write_text(json.dumps(cache))
    except Exception as e:
        log.debug("Failed to save probe cache: %s", e)


def _load_probe_cache(vid: int, pid: int,
                      usb_path: str = '') -> Optional[LedHandshakeInfo]:
    """Load a cached probe result from disk."""
    import json
    try:
        cache_path = _led_probe_cache_path()
        if not cache_path.exists():
            return None
        cache = json.loads(cache_path.read_text())
        # Try bus-path-specific key first, then fall back to VID:PID-only
        key = _probe_cache_key(vid, pid, usb_path)
        entry = cache.get(key)
        if not entry and usb_path:
            entry = cache.get(_probe_cache_key(vid, pid))
        if not entry:
            return None
        pm = entry['pm']
        sub_type = entry['sub_type']
        style = get_style_for_pm(pm, sub_type)
        return LedHandshakeInfo(
            pm=pm,
            sub_type=sub_type,
            style=style,
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
    cached = _load_probe_cache(vid, pid, usb_path)
    if cached is not None:
        return cached

    transport = None
    try:
        from .hid_device import PYUSB_AVAILABLE, HIDAPI_AVAILABLE
        if PYUSB_AVAILABLE:
            from .hid_device import PyUsbTransport
            transport = PyUsbTransport(vid, pid)
        elif HIDAPI_AVAILABLE:
            from .hid_device import HidApiTransport
            transport = HidApiTransport(vid, pid)
        else:
            return None

        transport.open()
        sender = LedHidSender(transport)
        info = sender.handshake()
        if info:
            _save_probe_cache(vid, pid, info, usb_path)
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
