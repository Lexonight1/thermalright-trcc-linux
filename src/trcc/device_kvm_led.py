#!/usr/bin/env python3
"""
KVM LED protocol layer for 10-channel ARGB LED controllers (FormKVMALED6 equivalent).

Packet format: all packets start with [0xDC, 0xDD] header, followed by command,
channel index, and mode byte. Data payload varies by command type.

Protocol reverse-engineered from FormKVMALED6.cs (TRCC 2.0.3).
No VID:PID known yet — backend-only, no UI. Will be activated when a KVM LED
device is identified during HID handshake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# =========================================================================
# Constants (from FormKVMALED6.cs)
# =========================================================================

HEADER = bytes([0xDC, 0xDD])

# Commands (byte[2])
CMD_ONOFF = 0x00
CMD_STATE_QUERY = 0x01
CMD_LED = 0x10
CMD_SCENE_SAVE = 0x68

# Channel count
NUM_CHANNELS = 10

# proMode.dc file structure
PROMODE_HEADER = 0xDC
PROMODE_SIZE = 61  # 1 header + 10 on/off + 10 mode + 10 brightness + 30 rgb

# Default channel enable flags (channel 8 disabled by default)
DEFAULT_CHANNEL_ENABLES = [1, 1, 1, 1, 1, 1, 1, 1, 0, 1]


# =========================================================================
# Data model
# =========================================================================

@dataclass
class KvmChannelState:
    """Per-channel LED state."""
    on: bool = True
    mode: int = 1
    brightness: int = 100
    r: int = 255
    g: int = 0
    b: int = 0


@dataclass
class KvmLedState:
    """Complete KVM LED controller state (10 channels)."""
    channels: List[KvmChannelState] = field(default_factory=lambda: [
        KvmChannelState() for _ in range(NUM_CHANNELS)
    ])
    channel_enables: List[int] = field(
        default_factory=lambda: list(DEFAULT_CHANNEL_ENABLES)
    )


# =========================================================================
# Packet builder
# =========================================================================

class KvmPacketBuilder:
    """Builds HID packets for KVM LED protocol commands."""

    @staticmethod
    def build_onoff(state: KvmLedState, mode: int = 0) -> bytes:
        """Build on/off command packet (CMD_ONOFF = 0x00).

        Packet: [0xDC, 0xDD, 0x00, 0x00, mode, onoff[0..9]]
        Total: 15 bytes (5 header + 10 data).
        """
        pkt = bytearray(15)
        pkt[0:2] = HEADER
        pkt[2] = CMD_ONOFF
        pkt[3] = 0x00  # channel index (broadcast)
        pkt[4] = mode
        for i, ch in enumerate(state.channels):
            pkt[5 + i] = 1 if ch.on else 0
        return bytes(pkt)

    @staticmethod
    def build_led(state: KvmLedState, channel: int = 0,
                  mode: int = 0) -> bytes:
        """Build LED data command packet (CMD_LED = 0x10).

        Packet: [0xDC, 0xDD, 0x10, channel, mode,
                 brightness, speed, R, G, B, 0, 0, 0, enables[0..9]]
        Total: 23 bytes (5 header + 18 data).
        """
        ch = state.channels[channel] if 0 <= channel < NUM_CHANNELS else KvmChannelState()
        pkt = bytearray(23)
        pkt[0:2] = HEADER
        pkt[2] = CMD_LED
        pkt[3] = channel
        pkt[4] = mode
        # Data payload (18 bytes)
        pkt[5] = ch.brightness
        pkt[6] = 1   # speed (default)
        pkt[7] = ch.r
        pkt[8] = ch.g
        pkt[9] = ch.b
        # pkt[10..12] = reserved zeros
        for i in range(NUM_CHANNELS):
            pkt[13 + i] = state.channel_enables[i]
        return bytes(pkt)

    @staticmethod
    def build_scene_save(state: KvmLedState, scene: int = 0) -> bytes:
        """Build scene save command packet (CMD_SCENE_SAVE = 0x68).

        Packet: [0xDC, 0xDD, 0x68, scene, mode,
                 onoff[10], modes[10], brightness[10], rgb[30]]
        Total: 65 bytes (5 header + 60 data).
        """
        pkt = bytearray(65)
        pkt[0:2] = HEADER
        pkt[2] = CMD_SCENE_SAVE
        pkt[3] = scene
        pkt[4] = state.channels[0].mode if state.channels else 0
        offset = 5
        # On/off (10 bytes)
        for i, ch in enumerate(state.channels):
            pkt[offset + i] = 1 if ch.on else 0
        offset += NUM_CHANNELS
        # Modes (10 bytes)
        for i, ch in enumerate(state.channels):
            pkt[offset + i] = ch.mode
        offset += NUM_CHANNELS
        # Brightness (10 bytes)
        for i, ch in enumerate(state.channels):
            pkt[offset + i] = ch.brightness
        offset += NUM_CHANNELS
        # RGB (30 bytes: 10 channels × 3)
        for i, ch in enumerate(state.channels):
            pkt[offset + i * 3] = ch.r
            pkt[offset + i * 3 + 1] = ch.g
            pkt[offset + i * 3 + 2] = ch.b
        return bytes(pkt)

    @staticmethod
    def build_state_query() -> bytes:
        """Build state query command packet (CMD_STATE_QUERY = 0x01).

        Packet: [0xDC, 0xDD, 0x01, 0x00, 0x00]
        Total: 5 bytes (header only, no data payload).
        """
        pkt = bytearray(5)
        pkt[0:2] = HEADER
        pkt[2] = CMD_STATE_QUERY
        return bytes(pkt)


# =========================================================================
# Persistence (proMode.dc)
# =========================================================================

class KvmProModePersistence:
    """Read/write proMode.dc config files for KVM LED state.

    File format (61 bytes):
        byte[0]     = 0xDC (header marker)
        byte[1-10]  = on/off per channel
        byte[11-20] = mode per channel
        byte[21-30] = brightness per channel
        byte[31-60] = RGB per channel (3 bytes each × 10)
    """

    @staticmethod
    def save(state: KvmLedState, path: Path) -> None:
        """Write state to proMode.dc file."""
        data = bytearray(PROMODE_SIZE)
        data[0] = PROMODE_HEADER
        for i, ch in enumerate(state.channels):
            data[1 + i] = 1 if ch.on else 0
            data[11 + i] = ch.mode
            data[21 + i] = ch.brightness
            data[31 + i * 3] = ch.r
            data[32 + i * 3] = ch.g
            data[33 + i * 3] = ch.b
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        log.debug("Saved KVM LED config to %s", path)

    @staticmethod
    def load(path: Path) -> Optional[KvmLedState]:
        """Load state from proMode.dc file. Returns None if invalid."""
        if not path.is_file():
            return None
        data = path.read_bytes()
        if len(data) < PROMODE_SIZE or data[0] != PROMODE_HEADER:
            log.warning("Invalid proMode.dc: size=%d header=0x%02X",
                        len(data), data[0] if data else 0)
            return None
        state = KvmLedState()
        for i in range(NUM_CHANNELS):
            ch = state.channels[i]
            ch.on = data[1 + i] != 0
            ch.mode = data[11 + i]
            ch.brightness = data[21 + i]
            ch.r = data[31 + i * 3]
            ch.g = data[32 + i * 3]
            ch.b = data[33 + i * 3]
        return state

    @staticmethod
    def save_scene(state: KvmLedState, scene: int, base_dir: Path) -> None:
        """Write state to a numbered scene file ({scene}proMode.dc)."""
        path = base_dir / f"{scene}proMode.dc"
        KvmProModePersistence.save(state, path)

    @staticmethod
    def load_scene(scene: int, base_dir: Path) -> Optional[KvmLedState]:
        """Load state from a numbered scene file."""
        path = base_dir / f"{scene}proMode.dc"
        return KvmProModePersistence.load(path)
