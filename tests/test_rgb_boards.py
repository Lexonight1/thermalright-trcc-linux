"""Finding motherboard RGB the way OpenRGB does -- matching, naming, Find."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from trcc.adapters.rgb import make_board_driver
from trcc.app import App
from trcc.core.commands import RgbLights, ScanRgbLights
from trcc.core.errors import PermissionError_, TransportError
from trcc.core.models import (
    BoardIdentity,
    FoundBoard,
    HidInterface,
    LightKind,
    RgbControllerId,
    RgbMirrorDevice,
)
from trcc.core.ports import RgbMirror, Transport
from trcc.services.rgb_boards import MotherboardFinder, matches, spot

from .conftest import FakeBulkTransport
from .mock_platform import (
    AURA_INTERFACE,
    MAINTAINER_BOARD,
    MockPlatform,
    ScriptedAuraController,
    ScriptedRgbHardware,
)

#: Every request a Find may send the Aura controller: firmware, config,
#: and the select/fetch pairs of its header and effect-channel records --
#: never a colour (0x40), an effect (0x35) or a save (0x3F).
READ_REQUESTS = {b"\x82", b"\xb0", b"\x31", b"\xb1", b"\x32", b"\xb2"}

MSI = HidInterface(0x1462, 0x7E06, 0, 0x0001, 0x0000, "MSI MYSTIC LIGHT")
MSI_BOARD = BoardIdentity("Micro-Star International Co., Ltd.",
                          "MAG B650 TOMAHAWK WIFI (MS-7D75)")


def _platform(root: Path, *interfaces: HidInterface,
              board: BoardIdentity | None = MAINTAINER_BOARD) -> MockPlatform:
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], root)
    platform.rgb = ScriptedRgbHardware(board, interfaces)
    return platform


# ── Matching, as OpenRGB's HID detectors match ─────────────────────────────

def test_a_row_matches_only_on_the_fields_it_names() -> None:
    row = RgbControllerId(0x0B05, 0x19AF, "ASUS Aura Motherboard", "d",
                          usage_page=0xFF72, usage=0x00A1)
    assert matches(row, AURA_INTERFACE)
    other_page = HidInterface(0x0B05, 0x19AF, 2, 0x0001, 0x00A1)
    assert not matches(row, other_page)     # the keyboard-like interface
    any_field = RgbControllerId(0x0B05, 0x19AF, "x", "d")
    assert matches(any_field, other_page)


def test_spot_names_a_controller_as_openrgb_does_once_per_device() -> None:
    """The brand, then the board's DMI name; a second interface of the same
    device is the same controller."""
    second = HidInterface(0x0B05, 0x19AF, 3, 0xFF72, 0x00A1)
    found = spot((AURA_INTERFACE, second), MAINTAINER_BOARD)
    assert [(b.label, b.controller.name) for b in found] == [
        ("ASUS PRIME Z790-V AX", "ASUS Aura Motherboard")]
    assert spot((MSI,), None)[0].label == "MSI Mystic Light MS_7E06"
    assert spot((HidInterface(0x413C, 0x2113, 0, 1, 6),), MAINTAINER_BOARD) == ()


def test_only_the_aura_motherboard_gets_a_trcc_driver() -> None:
    aura, msi = spot((AURA_INTERFACE, MSI), MSI_BOARD)
    opened: list = []

    def open_transport(*a: object, **k: object) -> Transport:
        opened.append(a)
        return FakeBulkTransport()

    driver = make_board_driver(aura, open_transport=open_transport,
                               argb_leds=lambda: {1: 8})
    assert driver is not None and opened == []        # made, not yet opened
    assert make_board_driver(msi, open_transport=open_transport,
                             argb_leds=dict) is None


# ── Find, through the App ──────────────────────────────────────────────────

def test_find_lists_the_boards_lights_and_a_restart_remembers_them(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The maintainer's board: its LED and three ARGB headers, read with the
    two read requests and nothing else; an MSI controller listed, not driven.

    MUTATION CHECK: drop the BOARD/FOUND save in ScanRgbLights."""
    platform = _platform(tmp_path, AURA_INTERFACE, MSI)
    app = App(platform)
    found = app.dispatch(ScanRgbLights())
    board = [(light.kind, light.ref, light.name, light.led_count)
             for light in found.lights
             if light.kind in (LightKind.BOARD, LightKind.FOUND)]
    assert board == [
        (LightKind.BOARD, "aura/onboard", "ASUS PRIME Z790-V AX -- board LEDs", 1),
        (LightKind.BOARD, "aura/argb1", "ASUS PRIME Z790-V AX -- ARGB header 1", 0),
        (LightKind.BOARD, "aura/argb2", "ASUS PRIME Z790-V AX -- ARGB header 2", 0),
        (LightKind.BOARD, "aura/argb3", "ASUS PRIME Z790-V AX -- ARGB header 3", 0),
        (LightKind.FOUND, "usb/1462:7e06",
         "MSI PRIME Z790-V AX (MSI Mystic Light MS_7E06)", 0)]
    assert found.message.endswith(
        "; motherboard: 4 light(s), 1 controller(s) TRCC does not drive")
    assert {bytes(w[1])[:1] for w in platform.aura.writes} <= READ_REQUESTS
    assert not platform.aura.is_open                   # Find leaves nothing open
    app.close()
    again = App(_platform(tmp_path))
    listed = again.dispatch(RgbLights())
    assert [(light.ref, light.remembered) for light in listed.lights
            if light.kind in (LightKind.BOARD, LightKind.FOUND)] == [
        ("aura/onboard", True), ("aura/argb1", True), ("aura/argb2", True),
        ("aura/argb3", True), ("usb/1462:7e06", True)]
    again.close()


class _Unreadable(RgbMirror):
    """A driver whose controller will not answer."""

    def __init__(self) -> None:
        self.closed = False

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        raise OSError("permission denied: /dev/hidraw0")

    def show(self, device: RgbMirrorDevice, colors) -> None:  # type: ignore[no-untyped-def]
        raise AssertionError("Find never sends colours")

    def close(self) -> None:
        self.closed = True


def test_a_board_that_cannot_be_read_says_why_and_is_not_called_found() -> None:
    """"Found" tells the user another program drives it -- not why TRCC
    could not read it.  MUTATION CHECK: append found_light(board) again in
    MotherboardFinder.scan's except."""
    unreadable = _Unreadable()

    def driver(board: FoundBoard) -> RgbMirror:
        return unreadable

    finder = MotherboardFinder(lambda: ScriptedRgbHardware(), driver)
    scan = finder.scan()
    assert (scan.driven, scan.found) == ((), ())
    assert scan.error == ("ASUS PRIME Z790-V AX: permission denied: "
                          "/dev/hidraw0")
    assert unreadable.closed
    assert finder.last() == scan


def test_a_failed_board_read_keeps_the_last_find(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = App(_platform(tmp_path, AURA_INTERFACE))
    app.dispatch(ScanRgbLights())
    saved = app.settings.found_lights(LightKind.BOARD)
    app.rgb_boards = MotherboardFinder(lambda: ScriptedRgbHardware(),
                                       lambda board: _Unreadable())
    failed = app.dispatch(ScanRgbLights())
    assert not failed.ok
    assert failed.message.endswith("; motherboard: ASUS PRIME Z790-V AX: "
                                   "permission denied: /dev/hidraw0")
    assert app.settings.found_lights(LightKind.BOARD) == saved
    app.close()


class _RefusingAura(ScriptedAuraController):
    """The maintainer's controller, but its open raises what the real HID
    transport raises -- not the builtin OSError a fake would."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def open(self) -> bool:
        raise self.error


@pytest.mark.parametrize("error", [
    PermissionError_("permission denied: /dev/hidraw0"),
    TransportError("no such device"),
    ImportError("no hidapi"),
])
def test_a_controller_that_refuses_to_open_leaves_find_working(
        tmp_path: Path, error: Exception) -> None:
    """The real transport raises PermissionError_ (a TrccError) or
    ImportError, and they escaped Find: the RAM and OpenRGB records were
    saved, no RgbLightsChanged, and the window said "Could not reach TRCC".
    Now Find answers, says why, and the last Find's board lights stay.
    MUTATION CHECK: drop the except in AuraMainboard._connected."""
    platform = _platform(tmp_path, AURA_INTERFACE)
    app = App(platform)
    app.dispatch(ScanRgbLights())
    platform.aura = _RefusingAura(error)
    failed = app.dispatch(ScanRgbLights())
    assert not failed.ok
    assert failed.message.endswith(
        f"; motherboard: ASUS PRIME Z790-V AX: cannot open the RGB "
        f"controller: {error}")
    assert [(light.ref, light.kind, light.remembered) for light in failed.lights
            if light.kind in (LightKind.BOARD, LightKind.FOUND)] == [
        ("aura/onboard", LightKind.BOARD, True),
        ("aura/argb1", LightKind.BOARD, True),
        ("aura/argb2", LightKind.BOARD, True),
        ("aura/argb3", LightKind.BOARD, True)]
    app.close()


def test_no_board_rgb_says_none_found(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = App(_platform(tmp_path))
    found = app.dispatch(ScanRgbLights())
    assert found.message.endswith("; motherboard: none found")
    assert not [light for light in found.lights
                if light.kind in (LightKind.BOARD, LightKind.FOUND)]
    app.close()


# ── An ARGB header's LED count -- the user's to give ───────────────────────

def test_a_headers_led_count_is_saved_and_listed_after_a_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Refused before anything changes for a header not listed or a count
    out of range; saved and re-read -- read requests only -- otherwise.

    MUTATION CHECK: skip the header check (set any header)."""
    from trcc.core.commands import SetAuraLeds

    platform = _platform(tmp_path, AURA_INTERFACE)
    app = App(platform)
    assert app.dispatch(SetAuraLeds(header=1, leds=8)).message == (
        "no ARGB header 1 -- press Find lights to list the board's headers")
    app.dispatch(ScanRgbLights())
    for header, leds, message in (
            (9, 8, "no ARGB header 9 -- press Find lights to list the "
                   "board's headers"),
            (1, 121, "an ARGB header drives 0-120 LEDs, not 121")):
        refused = app.dispatch(SetAuraLeds(header=header, leds=leds))
        assert (refused.ok, refused.message) == (False, message)
    assert app.settings.aura_argb_leds() == {}
    result = app.dispatch(SetAuraLeds(header=1, leds=8))
    assert (result.ok, result.message) == (True, "ARGB header 1: 8 LED(s)")
    assert {bytes(w[1])[:1] for w in platform.aura.writes} <= READ_REQUESTS
    app.close()
    again = App(_platform(tmp_path))
    assert [(light.ref, light.led_count) for light in
            again.dispatch(RgbLights()).lights
            if light.kind is LightKind.BOARD][1] == ("aura/argb1", 8)
    assert again.settings.aura_argb_leds() == {1: 8}
    again.close()


def test_a_damaged_led_count_is_dropped_not_guessed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = App(_platform(tmp_path))
    # cast: the entries are deliberately ill-typed, as a hand-edited
    # trcc.json can be -- the field's type is exactly what is being violated.
    app.settings.app.aura_argb_leds = cast(
        "dict[str, int]",
        {"1": 8, "2": "8", "x": 3, "3": 999, "0": 4, "4": -1})
    assert app.settings.aura_argb_leds() == {1: 8}
    app.close()
