"""ASUS Aura motherboard lighting, driven directly over USB HID.

The board's RGB headers -- the 5 V ARGB ones a fan plugs into, the 12 V RGB
ones, and the LEDs on the board itself -- all hang off one USB controller
(0b05:19af on a PRIME Z790-V AX).  It is a HID device of its own, so reaching
it opens nothing else: unlike RGB memory, there is no shared bus to guard.

What can be read back is the controller's own description: its firmware and
its config table -- how many ARGB headers, 12 V headers and onboard LEDs it
has.  **Not** what is plugged into an ARGB header: that is a one-way data
line, so a fan's LED count is the user's to give, and each header's count
arrives here from outside (``argb_leds``).

Every report is 65 bytes: report id 0xEC, then 64 of payload.  A request
(0x82 firmware, 0xB0 config table) is answered on the same pipe; replies are
read with a timeout -- OpenRGB's blocks forever on a silent device -- and
read until whole -- all 65 bytes: through the kernel's hidraw a reply is
one read, through libusb (the hidapi wheel pip installs) it arrives as 32,
32 and 1 (measured on the maintainer's board, 2026-10-10).  Stopping short
leaves the last byte at the front of the next reply.

**Direct**: colours go out in packets of at most 20 LEDs per channel, the
last one flagged APPLY; a channel shows them once it has been switched to
the "direct" mode (0xFF) -- what following an LCD sends many times a second.

**Credit.**  Adapted, with thanks, from OpenRGB
(https://gitlab.com/CalcProgrammer1/OpenRGB, GPL-2.0-or-later),
``Controllers/AsusAuraUSBController/`` at commit b190d3e5 (2026-10-09), by
Martin Hartl (inlart) and Adam Honse (CalcProgrammer1):

* ``AsusAuraUSBControllerDetect.cpp`` -- the motherboard product ids and the
  report id 0xEC;
* ``AsusAuraUSBController.cpp`` ``GetFirmwareVersion`` / ``GetConfigTable``
  -- the 0x82 and 0xB0 requests, the 0x02 / 0x30 reply markers, where the
  version and the table sit; ``SendDirect`` -- the 0x40 packet, 20 LEDs a
  packet, the 0x80 APPLY flag and the 0x10 flag past 255 LEDs;
* ``AsusAuraMainboardController.cpp`` -- reading the table (onboard LEDs at
  0x1B, 12 V headers at 0x1D, ARGB headers at 0x02), the channel order
  (onboard first, direct channel 0x04; then each ARGB header), and the 0x35
  effect packet that switches a channel to direct (mode 0xFF).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ...core.led_models import stretch
from ...core.logs import per_frame
from ...core.models import RgbMirrorDevice
from ...core.ports import BulkTransport, RgbMirror

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]

AURA_VID = 0x0B05
#: The motherboard controllers, OpenRGB's ``AuraMainboardController``.
MAINBOARD_PIDS = (0x18F3, 0x1939, 0x19AF, 0x1AA6, 0x1BED)
#: Every report the controller takes and sends.
REPORT_ID = 0xEC
PAYLOAD = 64
#: How long a request may go unanswered.
READ_TIMEOUT_MS = 500

_FIRMWARE, _FIRMWARE_REPLY = 0x82, 0x02
_CONFIG, _CONFIG_REPLY = 0xB0, 0x30
_EFFECT, _DIRECT_MODE = 0x35, 0xFF
_DIRECT, _APPLY, _PAST_255 = 0x40, 0x80, 0x10
_LEDS_PER_PACKET = 20
_ONBOARD_CHANNEL = 0x04
#: Where the table sits in a config reply, and what it says where.
_TABLE_AT = 4
_ARGB_HEADERS, _ONBOARD_LEDS, _RGB_HEADERS = 0x02, 0x1B, 0x1D


@dataclass(frozen=True, slots=True)
class AuraLayout:
    """What the controller says it has."""
    firmware: str
    onboard_leds: int
    rgb_headers: int
    argb_headers: int


def request(opcode: int) -> bytes:
    """A request's payload: *opcode*, then zeros."""
    log.debug("request: 0x%02x", opcode)
    return bytes([opcode]).ljust(PAYLOAD, b"\x00")


def parse_firmware(reply: bytes) -> str:
    """The firmware name in a 0x82 reply; ``OSError`` for anything else."""
    log.debug("parse_firmware: %s", reply[:18].hex())
    if len(reply) < 18 or reply[1] != _FIRMWARE_REPLY:
        raise OSError(f"Aura: not a firmware reply: {reply[:4].hex() or 'nothing'}")
    return reply[2:18].split(b"\x00", 1)[0].decode("ascii", "replace")


def parse_config(reply: bytes, firmware: str) -> AuraLayout:
    """The layout in a 0xB0 reply; ``OSError`` for anything else.

    A 12 V header count above the onboard LEDs is impossible, and read as
    none -- as OpenRGB reads it.
    """
    log.debug("parse_config: %s", reply[:40].hex())
    if len(reply) < _TABLE_AT + _RGB_HEADERS + 1 or reply[1] != _CONFIG_REPLY:
        raise OSError(f"Aura: not a config reply: {reply[:4].hex() or 'nothing'}")
    table = reply[_TABLE_AT:]
    onboard, rgb = table[_ONBOARD_LEDS], table[_RGB_HEADERS]
    return AuraLayout(firmware=firmware, onboard_leds=onboard,
                      rgb_headers=rgb if rgb <= onboard else 0,
                      argb_headers=table[_ARGB_HEADERS])


def direct_packets(channel: int, colors: Sequence[Rgb]) -> list[bytes]:
    """*colors* for direct *channel*, 20 LEDs a packet, the last one APPLY."""
    frame_log.debug("direct_packets: channel 0x%02x, %d LED(s)", channel,
                    len(colors))
    packets = []
    for start in range(0, len(colors), _LEDS_PER_PACKET):
        chunk = colors[start:start + _LEDS_PER_PACKET]
        command = channel | (_APPLY if start + len(chunk) >= len(colors) else 0)
        offset = start
        if offset > 255:
            command, offset = command | _PAST_255, offset - 256
        body = bytes([_DIRECT, command, offset, len(chunk)])
        body += bytes(c for rgb in chunk for c in rgb)
        packets.append(body.ljust(PAYLOAD, b"\x00"))
    return packets


def effect_packet(effect_channel: int, mode: int) -> bytes:
    """Switch *effect_channel* to *mode* (0xFF: direct)."""
    log.debug("effect_packet: channel %d mode 0x%02x", effect_channel, mode)
    return bytes([_EFFECT, effect_channel, 0x00, 0x00, mode]).ljust(
        PAYLOAD, b"\x00")


@dataclass(frozen=True, slots=True)
class _Channel:
    device: RgbMirrorDevice
    effect: int                  # the channel 0x35 names
    direct: int                  # the channel 0x40 names


class AuraMainboard(RgbMirror):
    """An ASUS Aura motherboard controller, driven directly.

    *open_hid* is the Platform's opener, bound to the controller with report
    id 0xEC -- a stand-in platform hands out a scripted controller, so a test
    or a mock run never reaches a real one.  *argb_leds* says how many LEDs
    each ARGB header drives (header number from 1); a header not in it has
    none and is listed with 0.
    """

    def __init__(self, open_hid: Callable[[], BulkTransport],
                 argb_leds: Mapping[int, int] | None = None) -> None:
        log.debug("AuraMainboard.__init__: argb_leds=%s", argb_leds)
        self._open = open_hid
        self._argb_leds = dict(argb_leds or {})
        self._hid: BulkTransport | None = None
        self._layout: AuraLayout | None = None
        self._channels: tuple[_Channel, ...] = ()
        self._direct: set[int] = set()     # effect channels already direct

    def layout(self) -> AuraLayout:
        """What the controller has, read once -- two requests, no colours."""
        if self._layout is None:
            hid = self._connected()
            firmware = parse_firmware(self._ask(hid, _FIRMWARE))
            self._layout = parse_config(self._ask(hid, _CONFIG), firmware)
            log.info("AuraMainboard: %s -- %d onboard LED(s), %d 12V header(s),"
                     " %d ARGB header(s)", self._layout.firmware,
                     self._layout.onboard_leds, self._layout.rgb_headers,
                     self._layout.argb_headers)
        return self._layout

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        """The board's own LEDs (if any), then each ARGB header."""
        if not self._channels:
            self._channels = self._make_channels(self.layout())
        devices = tuple(c.device for c in self._channels)
        log.debug("AuraMainboard.devices: %s", [d.ref for d in devices])
        return devices

    def show(self, device: RgbMirrorDevice, colors: Sequence[Rgb]) -> None:
        """*colors* on *device*, stretched to its LEDs; a header with none
        is skipped."""
        channel = next((c for c in self._channels if c.device == device), None)
        if channel is None:
            raise OSError(f"Aura: no channel {device.ref!r}")
        if not device.led_count or not colors:
            frame_log.debug("AuraMainboard.show: %s has no LEDs", device.ref)
            return
        hid = self._connected()
        if channel.effect not in self._direct:
            log.info("AuraMainboard: %s to direct mode", device.ref)
            hid.write(0, effect_packet(channel.effect, _DIRECT_MODE))
            self._direct.add(channel.effect)
        for packet in direct_packets(channel.direct,
                                     stretch(list(colors), device.led_count)):
            hid.write(0, packet)
        frame_log.debug("AuraMainboard.show: %s %d LED(s)", device.ref,
                        device.led_count)

    def close(self) -> None:
        log.debug("AuraMainboard.close: open=%s", self._hid is not None)
        if self._hid is not None:
            self._hid.close()
        self._hid, self._layout, self._channels = None, None, ()
        self._direct = set()

    def _connected(self) -> BulkTransport:
        if self._hid is None:
            hid = self._open()
            log.info("AuraMainboard: opening the controller")
            hid.open()
            self._hid = hid
        return self._hid

    def _ask(self, hid: BulkTransport, opcode: int) -> bytes:
        """Send request *opcode*; its whole reply, report id first -- read
        until all 65 bytes have come, or nothing more does."""
        hid.write(0, request(opcode))
        reply, reads = b"", 0
        while len(reply) < PAYLOAD + 1:
            piece = hid.read(0, PAYLOAD + 1, READ_TIMEOUT_MS)
            reads += 1
            if not piece:
                break
            reply += piece
        log.debug("AuraMainboard._ask: 0x%02x -> %d byte(s) in %d read(s)",
                  opcode, len(reply), reads)
        return reply

    def _make_channels(self, layout: AuraLayout) -> tuple[_Channel, ...]:
        """Onboard first (direct channel 0x04), then each ARGB header."""
        log.debug("AuraMainboard._make_channels: %s", layout)
        channels: list[_Channel] = []
        if layout.onboard_leds:
            channels.append(_Channel(RgbMirrorDevice(
                0, "ASUS Aura -- board LEDs", layout.onboard_leds,
                "aura/onboard"), effect=0, direct=_ONBOARD_CHANNEL))
        for header in range(1, layout.argb_headers + 1):
            channels.append(_Channel(RgbMirrorDevice(
                len(channels), f"ASUS Aura -- ARGB header {header}",
                self._argb_leds.get(header, 0), f"aura/argb{header}"),
                effect=len(channels), direct=header - 1))
        return tuple(channels)
