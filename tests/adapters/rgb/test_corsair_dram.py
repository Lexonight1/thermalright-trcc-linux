"""Corsair RGB memory follows the cooler directly (#160) -- on a fake SMBus.

``CORSAIR_STICK_INFO`` is the info block the maintainer's own stick at 0x19
returned (2026-10-08), and the 38-byte red packet is the one that turned it red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock_platform import (
    CORSAIR_STICK_INFO,
    ScriptedCorsairStick,
    ScriptedSmBus,
)
from trcc.adapters.rgb.corsair_dram import (
    ADDRESSES,
    MODELS,
    CorsairDramMirror,
    crc8,
    direct_packet,
)
from trcc.adapters.rgb.smbus import find_smbus

SPD_HUBS = range(0x50, 0x58)


def _mirror(chips: dict[int, ScriptedCorsairStick]) -> tuple[CorsairDramMirror, ScriptedSmBus]:
    bus = ScriptedSmBus(chips)
    return CorsairDramMirror(lambda: (bus,), probe_gap_s=0), bus


def test_the_checksum_is_crc8_smbus() -> None:
    assert crc8(b"123456789") == 0xF4                 # CRC-8/SMBUS check value
    assert crc8(CORSAIR_STICK_INFO) == 0xEE                    # what the stick reported


def test_the_packet_that_turned_the_sticks_red() -> None:
    """12 LEDs of red: byte for byte the i2cset lines run on real glass."""
    dominator = MODELS[0x0600]
    packet = direct_packet([(255, 0, 0)], dominator)
    assert packet == bytes([0x0C] + [0xFF, 0, 0] * 12 + [0x09])


def test_reverse_models_run_their_leds_backwards() -> None:
    first_red = [(255, 0, 0)] + [(0, 0, 255)] * 11
    forward = direct_packet(first_red, MODELS[0x0700])      # 10, forward
    backward = direct_packet(first_red, MODELS[0x0600])     # 12, reversed
    assert forward[1:4] == bytes([255, 0, 0])
    assert backward[-4:-1] == bytes([255, 0, 0])


def test_the_maintainers_sticks_are_found_and_named() -> None:
    mirror, bus = _mirror({0x19: ScriptedCorsairStick(), 0x1B: ScriptedCorsairStick()})
    devices = mirror.devices()
    assert [d.name for d in devices] == [
        "Corsair Vengeance RGB DDR5 (i2c-3 0x19)",
        "Corsair Vengeance RGB DDR5 (i2c-3 0x1b)"]
    assert [d.led_count for d in devices] == [10, 10]
    assert mirror.devices() == devices
    assert sum(1 for t in bus.touched if t[0] == "read" and t[2] == 0x43) == len(
        ADDRESSES), "found once, not again on the second call"


def test_the_spd_hubs_are_never_touched() -> None:
    mirror, bus = _mirror({0x19: ScriptedCorsairStick()})
    mirror.devices()
    mirror.show(mirror.devices()[0], [(1, 2, 3)])
    assert not [t for t in bus.touched if t[1] in SPD_HUBS]


def test_nothing_is_written_to_an_address_that_is_not_corsair() -> None:
    other = ScriptedCorsairStick(ids=(0x00, 0x04))
    mirror, bus = _mirror({0x19: ScriptedCorsairStick(), 0x1A: other})
    mirror.devices()
    written = {t[1] for t in bus.touched if t[0] != "read"}
    assert written == {0x19}


def test_ten_leds_go_in_one_block_twelve_in_two() -> None:
    mirror, bus = _mirror({0x19: ScriptedCorsairStick()})
    stick = mirror.devices()[0]
    mirror.show(stick, [(255, 0, 0)])
    assert bus.chips[0x19].blocks == [
        (0x31, bytes([10] + [255, 0, 0] * 10 + [crc8(bytes([10] + [255, 0, 0] * 10))]))]
    dominator = CORSAIR_STICK_INFO[:2] + b"\x00\x06" + CORSAIR_STICK_INFO[4:]
    mirror, bus = _mirror({0x19: ScriptedCorsairStick(dominator)})
    mirror.show(mirror.devices()[0], [(255, 0, 0)])
    assert bus.chips[0x19].blocks == [
        (0x31, bytes([0x0C] + [0xFF, 0, 0] * 10 + [0xFF])),
        (0x32, bytes([0, 0, 0xFF, 0, 0, 0x09]))]


@pytest.mark.parametrize("info, checksum, why", [
    (CORSAIR_STICK_INFO, 0x00, "a wrong checksum"),
    (CORSAIR_STICK_INFO[:28] + b"\x03" + CORSAIR_STICK_INFO[29:], None, "protocol 3"),
    (CORSAIR_STICK_INFO[:2] + b"\x99\x99" + CORSAIR_STICK_INFO[4:], None, "an unknown product"),
    (b"\x00\x00" + CORSAIR_STICK_INFO[2:], None, "not Corsair's vendor id"),
], ids=["bad-checksum", "protocol-3", "unknown-product", "not-corsair"])
def test_a_stick_it_cannot_drive_is_left_alone(info, checksum, why) -> None:  # type: ignore[no-untyped-def]
    mirror, bus = _mirror({0x19: ScriptedCorsairStick(info, checksum)})
    assert mirror.devices() == (), why
    assert not [t for t in bus.touched if t[0] == "block"], why


def test_no_smbus_is_an_error_to_retry() -> None:
    def refuse() -> tuple[ScriptedSmBus, ...]:
        raise OSError("no SMBus controller found")
    with pytest.raises(OSError, match="no SMBus"):
        CorsairDramMirror(refuse).devices()


def test_close_releases_the_bus_and_finds_again() -> None:
    mirror, bus = _mirror({0x19: ScriptedCorsairStick()})
    mirror.devices()
    mirror.close()
    assert bus.closed
    assert len(mirror.devices()) == 1


def test_only_the_chipset_smbus_is_probed(tmp_path: Path) -> None:
    for n, name in ((0, "NVIDIA i2c adapter 1 at 1:00.0"),
                    (3, "SMBus I801 adapter at efa0"),
                    (7, "SMBus PIIX4 adapter port 0 at 0b00"),
                    (9, "AUX B/DDI B/PHY B"),
                    ("MSFT8000:00", "SMBus-looking client, not a bus")):
        (tmp_path / f"i2c-{n}").mkdir()
        (tmp_path / f"i2c-{n}" / "name").write_text(name + "\n", encoding="utf-8")
    assert find_smbus(tmp_path) == (3, 7)


def test_the_bus_is_found_where_current_kernels_list_it() -> None:
    """``/sys/class/i2c-adapter`` is gone on kernel 7.2; the bus list is here."""
    from trcc.adapters.rgb.smbus import ADAPTERS
    assert Path("/sys/bus/i2c/devices") == ADAPTERS
