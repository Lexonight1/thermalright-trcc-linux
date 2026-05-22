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
import threading
import time
from pathlib import Path

from trcc.legacy.core.color import ColorEngine  # noqa: F401 — re-export
from trcc.legacy.core.models import (
    LED_REMAP_SUB_TABLES,  # noqa: F401 — re-export
    LED_REMAP_TABLES,  # noqa: F401 — re-export
    LED_STYLES,  # noqa: F401 — re-export
    PRESET_COLORS,  # noqa: F401 — re-export
    HandshakeResult,  # noqa: F401 — re-export
    LedDeviceStyle,  # noqa: F401 — re-export
    LedHandshakeInfo,
    PmEntry,  # noqa: F401 — re-export
    PmRegistry,
    remap_led_colors,  # noqa: F401 — re-export
)

from .frame import LedDevice
from .hid import (
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

# Handshake init packet size (device1 uses 64-byte reports, not 512)
LED_INIT_SIZE = 64
LED_RESPONSE_SIZE = 64

# Handshake timing (same as HID Type 2)
DELAY_PRE_INIT_S = 0.050    # Thread.Sleep(50) before init
DELAY_POST_INIT_S = 0.200   # Thread.Sleep(200) after init



# LedDeviceStyle, LED_STYLES, PmEntry, PmRegistry, PRESET_COLORS,
# LED_REMAP_TABLES, remap_led_colors, LedHandshakeInfo — all imported
# from core.models (canonical location). Re-exported for backward compat.



# ColorEngine moved to core/color.py — re-exported above for backward compat.



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
        led_colors: list[tuple[int, int, int]],
        is_on: list[bool] | None = None,
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

class LedHidSender(LedDevice):
    """Sends LED packets via UsbTransport with 64-byte report chunking.

    Matches UCDevice.cs ThreadSendDeviceData1 (lines 983-1026):
    - Splits packet into 64-byte HID reports
    - Thread.Sleep(30) cooldown after complete send
    - Concurrent-send guard (isSendUsbThread0)
    """

    def __init__(self, transport: UsbTransport):
        self._transport = transport
        self._lock = threading.Lock()

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
        last_err: Exception | None = None

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
                entry = PmRegistry.resolve(pm, sub_type)
                style_sub = entry.style_sub if entry else 0

                log.info(
                    "LED handshake: PM=%d SUB=%d style=%s model=%s style_sub=%d",
                    pm, sub_type,
                    style.style_id if style else "?",
                    model_name, style_sub,
                )

                return LedHandshakeInfo(
                    model_id=pm,
                    pm=pm,
                    sub_type=sub_type,
                    style=style,
                    model_name=model_name,
                    style_sub=style_sub,
                    raw_response=bytes(resp[:64]),
                )

            except Exception as e:
                # Retry-and-capture any failure (USB I/O, parser, etc.) — last_err is re-raised.
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

    def send_data(self, packet: bytes) -> bool:
        """Send an LED data packet, chunked into 64-byte HID reports.

        Args:
            packet: Complete LED packet (header + RGB payload).

        Returns:
            True if all chunks were sent successfully.
        """
        if not self._lock.acquire(blocking=False):
            log.debug("send_data: already sending — skipped")
            return False

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

            return True

        finally:
            self._lock.release()

    @property
    def is_sending(self) -> bool:
        """Whether a send is currently in progress."""
        return self._lock.locked()

    def close(self) -> None:
        """Release resources (transport is managed externally)."""


# =========================================================================
# Public API
# =========================================================================

def send_led_colors(
    transport: UsbTransport,
    led_colors: list[tuple[int, int, int]],
    is_on: list[bool] | None = None,
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
    return sender.send_data(packet)


# =========================================================================
# LED probe cache — persists handshake results across restarts
# =========================================================================
# The firmware only responds to the HID handshake once per power cycle.
# Caching the result avoids consuming the one-shot handshake during
# detection, so the actual LedProtocol.handshake() still works.


class _LedProbeCache:
    """Disk-backed cache for LED handshake results.

    Keyed by VID:PID:usb_path so multiple identical-PID devices
    are disambiguated by bus position.
    """

    @staticmethod
    def _path() -> Path:
        config_dir = Path.home() / '.trcc'
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
            from trcc.legacy.core.io import atomic_write_json
            atomic_write_json(cache_path, cache, indent=None)
        except (OSError, ValueError, TypeError) as e:
            log.debug("Failed to save probe cache: %s", e)

    @classmethod
    def load(cls, vid: int, pid: int,
             usb_path: str = '') -> LedHandshakeInfo | None:
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
            pm_entry = PmRegistry.resolve(pm, sub_type)
            return LedHandshakeInfo(
                pm=pm,
                sub_type=sub_type,
                style=PmRegistry.get_style(pm, sub_type),
                model_name=entry['model_name'],
                style_sub=pm_entry.style_sub if pm_entry else 0,
            )
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.debug("Failed to load probe cache: %s", e)
            return None


def probe_led_model(vid: int = LED_VID, pid: int = LED_PID,
                    usb_path: str = '') -> LedHandshakeInfo | None:
    """Probe an LED device to discover its model via HID handshake.

    Checks the disk cache first (keyed by VID:PID:bus_path).  Only
    performs a live USB handshake when no cached result exists, since
    the firmware only responds to the handshake once per power cycle.

    Args:
        vid: USB vendor ID.
        pid: USB product ID.
        usb_path: USB bus path (e.g. "usb:5:2") — used for cache
            disambiguation AND to bind the transport to the specific
            physical USB device when two LED controllers share VID:PID
            (issue #128).

    Returns:
        LedHandshakeInfo with pm, sub_type, style, and model_name,
        or None if the probe fails and no cached result exists.
    """
    from trcc.legacy.core.models import UsbAddress

    # Cache-first: avoid consuming the one-shot handshake unnecessarily.
    if (cached := _LedProbeCache.load(vid, pid, usb_path)) is not None:
        return cached

    transport = None
    try:
        from .factory import DeviceProtocolFactory
        transport = DeviceProtocolFactory.create_usb_transport(
            vid, pid, usb_address=UsbAddress.parse(usb_path),
        )
        transport.open()
        sender = LedHidSender(transport)
        info = sender.handshake()
        if info:
            _LedProbeCache.save(vid, pid, info, usb_path)
        return info
    except Exception as e:
        # Probe outer — handshake retry layer above re-raises last_err of any type.
        log.debug("LED probe failed for %04x:%04x: %s", vid, pid, e)
        return None
    finally:
        if transport is not None:
            try:
                transport.close()
            except OSError as e:
                log.debug("LED probe transport close raised: %s", e)
