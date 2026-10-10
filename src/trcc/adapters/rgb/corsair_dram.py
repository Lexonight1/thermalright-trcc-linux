"""Corsair RGB memory, driven directly with no OpenRGB running (#160).

The sticks' lighting controller sits on the chipset SMBus beside the memory's
own SPD hub, at 0x18-0x1F or 0x58-0x5F.  TRCC finds it by READING two id
registers, confirms it from its device-info block (checksum, Corsair's vendor
id, a known product id, protocol 4 or newer), and only then writes colours.
It never addresses 0x50-0x57, where the SPD hubs live.

Two ways to light a stick.  **Direct**: a packet of colours, shown at once
and kept nowhere -- what following the cooler sends many times a second.
**Effect**: one of the stick's own animations, written to its configuration
and SAVED there, so it outlives TRCC and a reboot.  An effect is written only
when asked for, never on a timer.

**Credit.**  Adapted, with thanks, from OpenRGB
(https://gitlab.com/CalcProgrammer1/OpenRGB, GPL-2.0-or-later),
``Controllers/CorsairDRAMController/`` at commit 9515475 (2026-10-08), by
Adam Honse (CalcProgrammer1) and Erik Gilling (konkers):

* ``CorsairDRAMControllerDetect.cpp`` -- the addresses, in that order, the
  id registers 0x43 / 0x44 and the values they hold, the 10 ms between tries;
* ``CorsairDRAMController.cpp`` ``ReadDeviceInfo`` -- 0x61 and 0x21, 32 reads
  of 0x40, the CRC-8 at 0x42, where the vendor, product and protocol sit;
  ``SetLEDColors`` -- the direct packet (LED count, R G B per LED, CRC-8) and
  its two block writes, 0x31 then 0x32; ``SetEffect`` -- the 20-byte effect,
  its mode / speed / direction / colour bytes, written a byte at a time
  through 0x20 and committed with 0x82 only when the stick's CRC agrees;
  ``SetColorsPerLED`` -- the same path for per-LED colours (R G B 0xFF);
  ``WaitReady`` -- polling 0x30 until bit 3 clears;
* ``RGBController_CorsairDRAM.cpp`` -- each effect's speed, colours,
  directions and brightness, and the defaults it sends for what an effect
  does not take (direction "left", brightness 0, speed slow);
* ``CorsairDRAMDevices.cpp`` -- each product id's name, LED count, and
  whether its LEDs run in reverse.

The packet is the one OpenRGB 0.9's ``CorsairDominatorPlatinumController``
sends too; it was confirmed on the maintainer's sticks before this was
written.  Protocol 1-3 sticks need OpenRGB's slower per-byte path, which is
not ported: they are listed in the log and left alone.
"""
from __future__ import annotations

import errno
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ...core.led_models import stretch
from ...core.logs import per_frame
from ...core.models import (
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
    RamEffect,
    RamEffectSettings,
    RgbMirrorDevice,
)
from ...core.ports import SMBUS_BLOCK_MAX, RamLights, SmBus
from ...core.ram_effects import effect_problem

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Where the controller answers: the DDR5 range first, then DDR4's.
ADDRESSES: tuple[int, ...] = (*range(0x58, 0x60), *range(0x18, 0x20))
_PROBE_GAP_S = 0.010

_REG_ID_A, _IDS_A = 0x43, frozenset({0x1A, 0x1B, 0x1C})
_REG_ID_B, _IDS_B = 0x44, frozenset({0x01, 0x03, 0x04})
_REG_GET_DEVICE_INFO = 0x61
_REG_BINARY_START = 0x21
_REG_GET_BINARY_DATA = 0x40
_REG_GET_CHECKSUM = 0x42
_REG_COLOR_BLOCK_1 = 0x31
_REG_COLOR_BLOCK_2 = 0x32
_INFO_SIZE = 32
_REG_RESET_BUFFER = 0x0B
_REG_SET_BINARY_DATA = 0x20
_REG_STATUS, _STATUS_BUSY = 0x30, 0x08
_REG_WRITE_CONFIGURATION = 0x82
_CONFIG_EFFECT, _CONFIG_COLORS = 1, 2
#: How long a stick may stay off the bus while it saves.  The maintainer's
#: Vengeance DDR5 (0x0701) refuses EVERY access for 118 ms after a commit
#: (measured twice, 2026-10-10) -- OpenRGB polls 50 ms and ignores the rest.
_SAVE_TIMEOUT_S, _READY_GAP_S = 1.0, 0.005

_EFFECT_BYTES: dict[RamEffect, int] = {
    RamEffect.COLOR_SHIFT: 0x00, RamEffect.COLOR_PULSE: 0x01,
    RamEffect.RAINBOW_WAVE: 0x03, RamEffect.COLOR_WAVE: 0x04,
    RamEffect.VISOR: 0x05, RamEffect.RAIN: 0x06, RamEffect.MARQUEE: 0x07,
    RamEffect.RAINBOW: 0x08, RamEffect.SEQUENTIAL: 0x09,
    RamEffect.STATIC: 0x10,
}
_SPEED_BYTES = {EffectSpeed.SLOW: 0x00, EffectSpeed.MEDIUM: 0x01,
                EffectSpeed.FAST: 0x02}
_DIRECTION_BYTES = {
    EffectDirection.UP: 0x00, EffectDirection.DOWN: 0x01,
    EffectDirection.LEFT: 0x02, EffectDirection.RIGHT: 0x03,
    EffectDirection.VERTICAL: 0x01, EffectDirection.HORIZONTAL: 0x03,
}
#: What OpenRGB sends for an effect with no direction: its mode's default,
#: ``MODE_DIRECTION_LEFT``, mapped to the stick's "left".
_NO_DIRECTION = _DIRECTION_BYTES[EffectDirection.LEFT]
#: The stick's "pick the colours yourself" / "use mine" byte.
_RANDOM, _CUSTOM = 0x00, 0x01
_BLACK = (0, 0, 0)

CORSAIR_VID = 0x1B1C
#: The first protocol that takes a whole direct-colour packet.
DIRECT_PROTOCOL = 4


@dataclass(frozen=True, slots=True)
class DramModel:
    name: str
    led_count: int
    reverse: bool


def _models() -> dict[int, DramModel]:
    """Product id -> model, as ``CorsairDRAMDevices.cpp`` lists them."""
    log.debug("_models")
    table = (
        ("Corsair Vengeance RGB Pro DDR4", 10, False, (0x0100, 0x0101)),
        ("Corsair Dominator Platinum RGB DDR4", 12, True, (0x0200, 0x0201)),
        ("Corsair Vengeance RGB Pro SL DDR4", 10, False, (0x0300, 0x0301)),
        ("Corsair Vengeance RGB RS DDR4", 6, False, (0x0400, 0x0401)),
        ("Corsair Dominator Platinum RGB DDR5", 12, True, (0x0600, 0x0601)),
        ("Corsair Dominator Titanium RGB DDR5", 12, True,
         (0x0800, 0x0801, 0x0810, 0x0811)),
        ("Corsair Vengeance RGB DDR5", 10, False,
         (0x0700, 0x0701, 0x0900, 0x0901, 0x0910, 0x0911)),
        ("Corsair Vengeance Shugo Series DDR5", 10, False,
         (0x0A00, 0x0A01, 0x0A10, 0x0A11)),
        ("Corsair Vengeance RGB RS DDR5", 6, False, (0x0B00, 0x0B01)),
    )
    return {pid: DramModel(name, leds, reverse)
            for name, leds, reverse, pids in table for pid in pids}


MODELS: dict[int, DramModel] = _models()


def crc8(data: bytes) -> int:
    """CRC-8, polynomial 0x07, initial 0 -- the sticks' checksum."""
    frame_log.debug("crc8: %d byte(s)", len(data))
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def direct_packet(colors: Sequence[tuple[int, int, int]],
                  model: DramModel) -> bytes:
    """LED count, then R G B for each LED, then the CRC-8 of all before it."""
    frame_log.debug("direct_packet: %s %d colour(s)", model.name, len(colors))
    leds = stretch(colors, model.led_count)
    if model.reverse:
        leds.reverse()
    body = bytes([model.led_count]) + bytes(c for rgb in leds for c in rgb)
    return body + bytes([crc8(body)])



def effect_packet(settings: RamEffectSettings) -> bytes:
    """The 20-byte effect configuration, byte for byte as OpenRGB's.

    ``ValueError`` when *settings* asks for something the effect does not
    take: a direction it cannot move in, too few colours, a brightness
    outside 0-255.
    """
    traits = EFFECT_TRAITS[settings.effect]
    log.debug("effect_packet: %s", settings)
    if (problem := effect_problem(settings)) is not None:
        raise ValueError(problem)
    direction = (_DIRECTION_BYTES[settings.direction or traits.directions[0]]
                 if traits.directions else _NO_DIRECTION)
    random = traits.random and settings.random_colors
    colors: list[tuple[int, int, int]] = [_BLACK, _BLACK]
    if traits.colors and settings.effect is not RamEffect.STATIC and not random:
        colors[:traits.colors] = settings.colors[:traits.colors]
    brightness = settings.brightness if traits.brightness else 0
    return bytes([
        _EFFECT_BYTES[settings.effect],
        _SPEED_BYTES[settings.speed] if traits.speed else 0x00,
        _RANDOM if random else _CUSTOM,
        direction,
        *colors[0], brightness,
        *colors[1], brightness,
        *bytes(8),
    ])


def color_data(colors: Sequence[tuple[int, int, int]],
               model: DramModel) -> bytes:
    """Per-LED colours for the stick's saved configuration: R G B 0xFF."""
    log.debug("color_data: %s %d colour(s)", model.name, len(colors))
    leds = stretch(colors, model.led_count)
    if model.reverse:
        leds.reverse()
    return bytes(c for rgb in leds for c in (*rgb, 0xFF))

@dataclass(frozen=True, slots=True)
class _Stick:
    bus: int
    address: int
    model: DramModel
    device: RgbMirrorDevice


class CorsairDramMirror(RamLights):
    """Every Corsair RGB stick on the chipset SMBus, driven directly.

    *open_buses* is ``Platform.smbuses``: the platform opens the bus, so a
    stand-in platform's scripted sticks are all a test or mock run can reach.
    """

    def __init__(self, open_buses: Callable[[], tuple[SmBus, ...]],
                 probe_gap_s: float = _PROBE_GAP_S) -> None:
        log.debug("CorsairDramMirror.__init__")
        self._open, self._gap = open_buses, probe_gap_s
        self._buses: dict[int, SmBus] = {}
        self._sticks: tuple[_Stick, ...] | None = None

    def found(self) -> tuple[RgbMirrorDevice, ...] | None:
        """The last scan's sticks, None before any -- no bus access."""
        log.debug("CorsairDramMirror.found: scanned=%s", self._sticks is not None)
        return None if self._sticks is None else tuple(
            s.device for s in self._sticks)

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        """The sticks, found once; again only after ``close``."""
        sticks = self._found()
        frame_log.debug("CorsairDramMirror.devices: %d", len(sticks))
        return tuple(s.device for s in sticks)

    def show(self, device: RgbMirrorDevice,
             colors: Sequence[tuple[int, int, int]]) -> None:
        stick = self._found()[device.index]
        packet = direct_packet(colors, stick.model)
        frame_log.debug("CorsairDramMirror.show: 0x%02x %d byte(s)",
                        stick.address, len(packet))
        bus = self._buses[stick.bus]
        bus.write_block_data(stick.address, _REG_COLOR_BLOCK_1,
                             packet[:SMBUS_BLOCK_MAX])
        if len(packet) > SMBUS_BLOCK_MAX:
            bus.write_block_data(stick.address, _REG_COLOR_BLOCK_2,
                                 packet[SMBUS_BLOCK_MAX:])

    def apply_effect(self, device: RgbMirrorDevice,
                     settings: RamEffectSettings) -> None:
        """Save *settings* on the stick as its own effect.

        Static is the one effect with colours of its own: its colour data is
        written after it, the same checked way.  ``ValueError`` for settings
        the effect does not take, before anything is written; ``OSError`` if
        the stick does not take it.
        """
        stick = self._found()[device.index]
        packet = effect_packet(settings)
        log.info("CorsairDramMirror.apply_effect: 0x%02x %s %s",
                 stick.address, settings.effect.value, packet.hex())
        bus = self._buses[stick.bus]
        self._configure(bus, stick.address, packet, _CONFIG_EFFECT)
        if settings.effect is RamEffect.STATIC:
            colors = color_data(settings.colors or ((255, 255, 255),),
                                stick.model)
            self._configure(bus, stick.address, colors, _CONFIG_COLORS)

    def close(self) -> None:
        log.debug("CorsairDramMirror.close: %d bus(es)", len(self._buses))
        for bus in self._buses.values():
            bus.close()
        self._buses, self._sticks = {}, None

    # ── Saving a configuration ────────────────────────────────────────

    def _configure(self, bus: SmBus, address: int, data: bytes,
                   which: int) -> None:
        """Fill the stick's buffer with *data*, then commit it as *which*.

        Committed only when the stick's checksum of what it received matches
        ours -- a byte lost on the bus is never saved.
        """
        log.debug("_configure: 0x%02x config %d, %d byte(s)", address, which,
                  len(data))
        bus.write_byte_data(address, _REG_RESET_BUFFER, 0x00)
        bus.write_byte_data(address, _REG_BINARY_START, 0x00)
        for value in data:
            bus.write_byte_data(address, _REG_SET_BINARY_DATA, value)
        received = bus.read_byte_data(address, _REG_GET_CHECKSUM)
        if received != crc8(data):
            raise OSError(f"0x{address:02x} received config {which} with "
                          f"checksum 0x{received:02x}, sent 0x{crc8(data):02x}"
                          " -- not saved")
        bus.write_byte_data(address, _REG_WRITE_CONFIGURATION, which)
        self._wait_ready(bus, address)

    def _wait_ready(self, bus: SmBus, address: int) -> None:
        """Until the stick has stored the configuration.

        A saving stick is off the bus -- it refuses every read -- so a refused
        read means "still saving", not an error.  ``OSError`` only if it is
        not back and ready within ``_SAVE_TIMEOUT_S``.
        """
        start, refused = time.monotonic(), 0
        while time.monotonic() - start < _SAVE_TIMEOUT_S:
            try:
                status = bus.read_byte_data(address, _REG_STATUS)
            except OSError:
                refused += 1
            else:
                if not status & _STATUS_BUSY:
                    log.info("_wait_ready: 0x%02x saved in %d ms (%d refused "
                             "read(s), status 0x%02x)", address,
                             (time.monotonic() - start) * 1000, refused, status)
                    return
            time.sleep(_READY_GAP_S)
        log.warning("_wait_ready: 0x%02x not ready %d ms after saving (%d "
                    "refused read(s))", address, _SAVE_TIMEOUT_S * 1000, refused)
        raise OSError(errno.ETIMEDOUT,
                      f"0x{address:02x} was not ready "
                      f"{int(_SAVE_TIMEOUT_S * 1000)} ms after saving")

    # ── Finding the sticks ────────────────────────────────────────────

    def _found(self) -> tuple[_Stick, ...]:
        frame_log.debug("_found: scanned=%s", self._sticks is not None)
        if self._sticks is None:
            self._sticks = self._scan()
        return self._sticks

    def _scan(self) -> tuple[_Stick, ...]:
        self._buses = {bus.number: bus for bus in self._open()}
        log.debug("_scan: bus(es) %s", sorted(self._buses))
        found: list[_Stick] = []
        for number, bus in self._buses.items():
            for address in ADDRESSES:
                if stick := self._identify(bus, number, address, len(found)):
                    found.append(stick)
                time.sleep(self._gap)
        log.info("CorsairDramMirror: %d stick(s): %s", len(found),
                 ", ".join(s.device.name for s in found) or "none")
        return tuple(found)

    def _identify(self, bus: SmBus, number: int, address: int,
                  index: int) -> _Stick | None:
        """The stick at *address*, or None: nothing there, or not one we drive."""
        try:
            if (bus.read_byte_data(address, _REG_ID_A) not in _IDS_A
                    or bus.read_byte_data(address, _REG_ID_B) not in _IDS_B):
                log.debug("_identify: i2c-%d 0x%02x is not Corsair RGB",
                          number, address)
                return None
        except OSError as e:
            log.debug("_identify: i2c-%d 0x%02x silent (%s)", number, address, e)
            return None
        try:
            info = self._device_info(bus, address)
        except OSError as e:
            log.warning("CorsairDramMirror: i2c-%d 0x%02x looks like Corsair "
                        "RGB but its device info failed (%s) -- left alone",
                        number, address, e)
            return None
        vid, pid = info[0] | info[1] << 8, info[2] | info[3] << 8
        protocol, model = info[28], MODELS.get(pid)
        where = f"i2c-{number} 0x{address:02x}"
        log.info("_identify: %s vid=0x%04x pid=0x%04x protocol=%d model=%s",
                 where, vid, pid, protocol, model.name if model else "unknown")
        if vid != CORSAIR_VID or model is None or protocol < DIRECT_PROTOCOL:
            log.warning("CorsairDramMirror: leaving %s alone -- vid 0x%04x, "
                        "pid 0x%04x, protocol %d is not a stick TRCC can "
                        "drive directly", where, vid, pid, protocol)
            return None
        device = RgbMirrorDevice(index=index, name=f"{model.name} ({where})",
                                 led_count=model.led_count,
                                 ref=f"i2c-{number}/0x{address:02x}")
        return _Stick(number, address, model, device)

    def _device_info(self, bus: SmBus, address: int) -> bytes:
        """The 32-byte info block; ``OSError`` if its checksum is wrong."""
        log.debug("_device_info: 0x%02x", address)
        bus.write_byte_data(address, _REG_GET_DEVICE_INFO, 0x00)
        bus.write_byte_data(address, _REG_BINARY_START, 0x00)
        info = bytes(bus.read_byte_data(address, _REG_GET_BINARY_DATA)
                     for _ in range(_INFO_SIZE))
        reported = bus.read_byte_data(address, _REG_GET_CHECKSUM)
        if crc8(info) != reported:
            raise OSError(f"0x{address:02x}: device info checksum 0x{reported:02x}"
                          f", computed 0x{crc8(info):02x}")
        return info
