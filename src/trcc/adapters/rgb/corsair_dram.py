"""Corsair RGB memory follows the cooler, with no OpenRGB running (#160).

The sticks' lighting controller sits on the chipset SMBus beside the memory's
own SPD hub, at 0x18-0x1F or 0x58-0x5F.  TRCC finds it by READING two id
registers, confirms it from its device-info block (checksum, Corsair's vendor
id, a known product id, protocol 4 or newer), and only then writes colours.
It never addresses 0x50-0x57, where the SPD hubs live.

**Credit.**  Adapted, with thanks, from OpenRGB
(https://gitlab.com/CalcProgrammer1/OpenRGB, GPL-2.0-or-later),
``Controllers/CorsairDRAMController/`` at commit 9515475 (2026-10-08), by
Adam Honse (CalcProgrammer1) and Erik Gilling (konkers):

* ``CorsairDRAMControllerDetect.cpp`` -- the addresses, in that order, the
  id registers 0x43 / 0x44 and the values they hold, the 10 ms between tries;
* ``CorsairDRAMController.cpp`` ``ReadDeviceInfo`` -- 0x61 and 0x21, 32 reads
  of 0x40, the CRC-8 at 0x42, where the vendor, product and protocol sit;
  ``SetLEDColors`` -- the direct packet (LED count, R G B per LED, CRC-8) and
  its two block writes, 0x31 then 0x32;
* ``CorsairDRAMDevices.cpp`` -- each product id's name, LED count, and
  whether its LEDs run in reverse.

The packet is the one OpenRGB 0.9's ``CorsairDominatorPlatinumController``
sends too; it was confirmed on the maintainer's sticks before this was
written.  Protocol 1-3 sticks need OpenRGB's slower per-byte path, which is
not ported: they are listed in the log and left alone.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ...core.logs import per_frame
from ...core.models import RgbMirrorDevice
from ...core.ports import SMBUS_BLOCK_MAX, RgbMirror, SmBus
from .openrgb import stretch

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


@dataclass(frozen=True, slots=True)
class _Stick:
    bus: int
    address: int
    model: DramModel
    device: RgbMirrorDevice


class CorsairDramMirror(RgbMirror):
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

    def close(self) -> None:
        log.debug("CorsairDramMirror.close: %d bus(es)", len(self._buses))
        for bus in self._buses.values():
            bus.close()
        self._buses, self._sticks = {}, None

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
                                 led_count=model.led_count)
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
