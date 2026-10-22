"""Adapters for other RGB systems that follow the cooler (#160)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from trcc.core.models import MAINBOARD_DETECTOR
from trcc.core.ports import BulkTransport, SmBus, Transport
from trcc.modules.rgb.adapters.asus_aura import REPORT_ID, AuraMainboard
from trcc.modules.rgb.adapters.corsair_dram import CorsairDramMirror
from trcc.modules.rgb.core.models import FoundBoard, RgbFollowMode
from trcc.modules.rgb.core.ports import RgbMirror

log = logging.getLogger(__name__)


def make_mirror(mode: RgbFollowMode, host: str, port: int, *,
                smbuses: Callable[[], tuple[SmBus, ...]],
                board: Callable[[], RgbMirror],
                openrgb: Callable[[str, int], RgbMirror]) -> RgbMirror:
    """The follower for *mode*: OpenRGB at *host*:*port* through *openrgb*
    (the host's stand-in-aware client), Corsair RAM on the buses *smbuses*
    opens (``host.hardware.smbuses``), or the motherboard's RGB *board* hands out
    (``MotherboardFinder.driver``)."""
    log.info("make_mirror: %s", mode.value)
    match mode:
        case RgbFollowMode.OPENRGB:
            return openrgb(host, port)
        case RgbFollowMode.RAM:
            return CorsairDramMirror(smbuses)
        case RgbFollowMode.BOARD:
            return board()
    raise ValueError(f"nothing follows in mode {mode.value!r}")


def make_board_driver(board: FoundBoard, *,
                      open_hid: Callable[[int, int, int], Transport],
                      argb_leds: Callable[[], Mapping[int, int]]
                      ) -> RgbMirror | None:
    """TRCC's driver for the controller Find spotted, or None for one TRCC
    does not drive -- it is listed as found and never written to.

    *open_hid* is the host's (``host.hardware.open_hid``: vid, pid, report
    id), so a stand-in platform's scripted controller is all a test or mock
    run reaches.  *argb_leds* reads
    the user's LED count per ARGB header, at the time of the Find.
    """
    if board.controller.detector != MAINBOARD_DETECTOR:
        log.info("make_board_driver: %s (%s) -- no TRCC driver", board.label,
                 board.controller.name)
        return None
    found = board.interface

    def opened() -> BulkTransport:
        log.info("make_board_driver.opened: %04x:%04x interface %d (%s)",
                 found.vid, found.pid, found.interface, found.path)
        transport = open_hid(found.vid, found.pid, REPORT_ID)
        if not isinstance(transport, BulkTransport):
            raise OSError(f"{board.label}: no HID transport "
                          f"({type(transport).__name__})")
        return transport

    counts = argb_leds()
    log.info("make_board_driver: %s -> AuraMainboard, ARGB LEDs %s",
             board.label, counts)
    return AuraMainboard(opened, counts, label=board.label)
