"""Finding motherboard RGB the way OpenRGB does -- only when a user asks.

OpenRGB spots a board's RGB controller by its USB HID ids (vendor, product,
interface, usage page, usage) and names it by the board's DMI name.  The ids
are its own registrations, generated into ``core/rgb_catalog.py``; the
interfaces come from ``Platform.rgb_hardware``, which opens nothing.  A
controller TRCC has a driver for is then asked for its layout -- read
requests only -- and listed light by light; any other is listed as found and
never written to, since another program may be driving it.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..core.logs import per_frame
from ..core.models import (
    BoardIdentity,
    FoundBoard,
    HidInterface,
    RgbControllerId,
    RgbMirrorDevice,
)
from ..core.ports import RgbHardware, RgbMirror
from ..core.rgb_catalog import MOTHERBOARD_CONTROLLERS

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)        # matches: every row x every interface


@dataclass(frozen=True, slots=True)
class BoardScan:
    """What a Find saw: lights TRCC drives, controllers it only found, and
    why a driver could not be read, if one could not."""
    driven: tuple[RgbMirrorDevice, ...] = ()
    found: tuple[RgbMirrorDevice, ...] = ()
    error: str = ""


def matches(row: RgbControllerId, interface: HidInterface) -> bool:
    """Whether *interface* is the controller *row* registers; a field the
    row leaves None matches any."""
    frame_log.debug("matches: %04x:%04x vs %04x:%04x", row.vid, row.pid,
                    interface.vid, interface.pid)
    return ((row.vid, row.pid) == (interface.vid, interface.pid)
            and row.interface in (None, interface.interface)
            and row.usage_page in (None, interface.usage_page)
            and row.usage in (None, interface.usage))


def spot(interfaces: Sequence[HidInterface], board: BoardIdentity | None,
         catalog: Sequence[RgbControllerId] = MOTHERBOARD_CONTROLLERS
         ) -> tuple[FoundBoard, ...]:
    """Every catalog controller among *interfaces*, once per device, named
    as OpenRGB names it: the brand, then the board's DMI name."""
    found: dict[tuple[int, int], FoundBoard] = {}
    for interface in interfaces:
        row = next((r for r in catalog if matches(r, interface)), None)
        if row is not None and (interface.vid, interface.pid) not in found:
            label = f"{row.name.split()[0]} {board.name}" if board else row.name
            found[interface.vid, interface.pid] = FoundBoard(row, interface,
                                                             label)
    log.info("spot: %d interface(s) -> %s", len(interfaces),
             [f"{b.label} ({b.controller.name})" for b in found.values()])
    return tuple(found.values())


def found_light(board: FoundBoard) -> RgbMirrorDevice:
    """A controller TRCC does not drive, as one light: its LEDs unknown."""
    controller = board.controller
    log.debug("found_light: %s", board.label)
    name = (board.label if board.label == controller.name
            else f"{board.label} ({controller.name})")
    return RgbMirrorDevice(0, name, 0, f"usb/{controller.vid:04x}:"
                                       f"{controller.pid:04x}")


class MotherboardFinder:
    """Finds the motherboard RGB controllers OpenRGB can find.

    *hardware* is ``Platform.rgb_hardware``; *driver* is TRCC's driver for a
    controller, or None where it has none.  A driver is opened for the
    layout and closed again: Find leaves nothing open.
    """

    def __init__(self, hardware: Callable[[], RgbHardware],
                 driver: Callable[[FoundBoard], RgbMirror | None]) -> None:
        log.debug("MotherboardFinder.__init__")
        self._hardware = hardware
        self._driver = driver
        self._lock = threading.Lock()
        self._last: BoardScan | None = None

    def last(self) -> BoardScan | None:
        """The last Find's result, None before any -- touches nothing."""
        log.debug("MotherboardFinder.last: %s", self._last is not None)
        return self._last

    def scan(self) -> BoardScan:
        """Look for the boards' controllers -- only ever because a user
        asked.  A driver that cannot be read says why, and is not listed as
        found: "found" means another program drives it."""
        log.info("MotherboardFinder.scan")
        with self._lock:
            hardware = self._hardware()
            driven: list[RgbMirrorDevice] = []
            found: list[RgbMirrorDevice] = []
            errors: list[str] = []
            for board in spot(hardware.hid_interfaces(), hardware.board()):
                if (driver := self._driver(board)) is None:
                    found.append(found_light(board))
                    continue
                try:
                    driven += driver.devices()
                except OSError as e:
                    # Not "found": that says another program drives it.  The
                    # page keeps the last Find's lights and shows *e*.
                    log.warning("MotherboardFinder.scan: %s could not be "
                                "read -- %s", board.label, e)
                    errors.append(f"{board.label}: {e}")
                finally:
                    driver.close()
            self._last = BoardScan(tuple(driven), tuple(found),
                                   "; ".join(errors))
        log.info("MotherboardFinder.scan: %d light(s) driven, %d found only%s",
                 len(driven), len(found),
                 f" -- {self._last.error}" if self._last.error else "")
        return self._last

    def driver(self) -> RgbMirror:
        """The motherboard RGB TRCC drives, for following its lights --
        ``OSError`` when none is plugged in.  Spotted afresh from what the
        system lists (nothing is opened here), so a board is followed
        whether or not a Find ran since the App started."""
        hardware = self._hardware()
        for board in spot(hardware.hid_interfaces(), hardware.board()):
            if (driver := self._driver(board)) is not None:
                log.info("MotherboardFinder.driver: %s", board.label)
                return driver
        log.warning("MotherboardFinder.driver: no motherboard RGB TRCC drives")
        raise OSError("no motherboard RGB that TRCC drives was found")
