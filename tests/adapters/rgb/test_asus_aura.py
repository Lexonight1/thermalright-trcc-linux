"""ASUS Aura motherboard lighting, driven directly -- on a scripted controller.

Every expected packet is worked out from OpenRGB's AsusAuraUSBController
(b190d3e5) by hand, not from this code.
"""
from __future__ import annotations

import pytest

from tests.mock_platform import MockPlatform, ScriptedAuraController
from trcc.adapters.rgb.asus_aura import (
    AURA_VID,
    REPORT_ID,
    AuraLayout,
    AuraMainboard,
    direct_packets,
    effect_packet,
    parse_config,
    parse_firmware,
    request,
)
from trcc.core.models import Wire


def _board(**kw: int) -> tuple[AuraMainboard, ScriptedAuraController]:
    controller = ScriptedAuraController(**kw)
    return AuraMainboard(lambda: controller, {1: 12, 2: 8}), controller


def test_requests_are_the_opcode_then_zeros() -> None:
    assert request(0xB0) == bytes([0xB0]) + bytes(63)


#: The maintainer's PRIME Z790-V AX on the glass: 1 onboard LED, a 12 V count
#: of 2 that exceeds it (so none, as OpenRGB reads it), 3 ARGB headers
#: (2026-10-10) -- every header on effect channel 1, both channels running
#: effect 0x05, Rainbow (its 0x31/0x32 records, 2026-10-15).
MAINTAINERS = AuraLayout("AULA3-AR32-0304", 1, 0, 3,
                         header_effects={1: 1, 2: 1, 3: 1},
                         effects={0: 0x05, 1: 0x05})
#: What Find may send: firmware, config, and the record select/fetch pairs.
READS = {0x82, 0xB0, 0x31, 0xB1, 0x32, 0xB2}


def test_the_maintainers_board_reads_as_it_did_on_the_glass() -> None:
    board, _ = _board()
    assert board.layout() == MAINTAINERS
    assert [d.ref for d in board.devices()] == [
        "aura/onboard", "aura/argb1", "aura/argb2", "aura/argb3"]


def test_a_reply_in_32_byte_pieces_is_read_whole() -> None:
    """Through libusb -- pip's hidapi wheel -- the board's 65-byte replies
    came as 32, 32 and 1: read short, the config request got the firmware
    reply's leftovers (on the glass, twice: its second half, then its last
    byte)."""
    controller = ScriptedAuraController(piece=32)
    board = AuraMainboard(lambda: controller)
    assert board.layout() == MAINTAINERS


def test_the_layout_is_read_once_with_read_requests_only() -> None:
    board, controller = _board(argb_headers=3, onboard_leds=4, rgb_headers=1)
    layout = board.layout()
    assert (layout.onboard_leds, layout.rgb_headers, layout.argb_headers) == (
        4, 1, 3)
    assert {w[0] for _ep, w in controller.writes} <= READS
    sent = len(controller.writes)
    board.layout()                                   # read once
    assert len(controller.writes) == sent


def test_each_header_uses_the_effect_channel_the_chip_reports() -> None:
    """Every ARGB header shares effect channel 1 on the maintainer's board;
    OpenRGB's mapping gave header 2 channel 2, which runs nothing -- so a fan
    on header 2 or 3 never left its effect.  MUTATION CHECK: give each
    header ``len(channels)`` again in _make_channels."""
    board, controller = _board()
    board.show(board.devices()[2], [(0, 255, 0)])     # header 2, 8 LEDs
    assert controller.reports(0x35) == [effect_packet(1, 0xFF)]
    assert controller.reports(0x40)[0][:4] == bytes([0x40, 0x81, 0, 8])


def test_a_chip_without_the_tables_keeps_openrgbs_mapping() -> None:
    board, controller = _board()
    controller.records = {0x31: [], 0x32: []}
    assert (board.layout().header_effects, board.layout().effects) == ({}, {})
    board.show(board.devices()[2], [(0, 255, 0)])
    assert controller.reports(0x35) == [effect_packet(2, 0xFF)]


def test_release_hands_each_direct_channel_back_to_its_effect_unsaved() -> None:
    """Once per channel TRCC switched to direct, to the effect the chip
    reported -- never a save (0x3F).  MUTATION CHECK: skip the write in
    AuraMainboard.release."""
    board, controller = _board()
    board.show(board.devices()[1], [(255, 0, 0)])     # header 1
    board.show(board.devices()[2], [(255, 0, 0)])     # header 2, same channel
    board.release()
    board.release()                                   # nothing left to hand
    assert controller.reports(0x35) == [effect_packet(1, 0xFF),
                                        effect_packet(1, 0x05)]
    assert controller.reports(0x3F) == []


def test_release_leaves_a_channel_with_no_reported_effect_as_it_is() -> None:
    board, controller = _board()
    controller.records = {0x31: [], 0x32: []}
    board.show(board.devices()[1], [(255, 0, 0)])
    board.release()
    assert controller.reports(0x35) == [effect_packet(1, 0xFF)]


def test_a_wrong_or_missing_reply_is_an_error() -> None:
    with pytest.raises(OSError, match="not a firmware reply"):
        parse_firmware(b"")
    with pytest.raises(OSError, match="not a config reply"):
        parse_config(bytes([REPORT_ID, 0x02]) + bytes(63), "fw")


def test_more_12v_headers_than_onboard_leds_means_none() -> None:
    reply = bytearray([REPORT_ID, 0x30, 0, 0]) + bytearray(60)
    reply[4 + 0x1B], reply[4 + 0x1D] = 2, 5
    assert parse_config(bytes(reply), "fw").rgb_headers == 0


def test_onboard_leds_come_first_then_each_argb_header() -> None:
    board, _ = _board(argb_headers=2, onboard_leds=4, rgb_headers=0)
    assert [(d.index, d.ref, d.led_count) for d in board.devices()] == [
        (0, "aura/onboard", 4), (1, "aura/argb1", 12), (2, "aura/argb2", 8)]


def test_a_header_with_no_led_count_is_listed_with_none() -> None:
    board, _ = _board()
    assert board.devices()[3].led_count == 0         # header 3: not given


def test_direct_colours_go_20_a_packet_the_last_one_applied() -> None:
    colors = [(i, 0, 255 - i) for i in range(45)]
    first, second, third = direct_packets(0x01, colors)
    assert first[:4] == bytes([0x40, 0x01, 0, 20])
    assert second[:4] == bytes([0x40, 0x01, 20, 20])
    assert third[:4] == bytes([0x40, 0x81, 40, 5])
    assert third[4:19] == bytes(c for rgb in colors[40:] for c in rgb)
    assert {len(p) for p in (first, second, third)} == {64}


def test_past_255_leds_the_offset_wraps_and_says_so() -> None:
    packets = direct_packets(0x00, [(1, 2, 3)] * 300)
    assert packets[13][1:3] == bytes([0x10, 260 - 256])


def test_show_switches_a_channel_to_direct_once_then_sends_its_colours() -> None:
    board, controller = _board(argb_headers=2, onboard_leds=0)
    fan = board.devices()[0]                         # ARGB header 1, 12 LEDs
    board.show(fan, [(255, 0, 0)])
    board.show(fan, [(0, 0, 255)])
    assert controller.reports(0x35) == [effect_packet(0, 0xFF)]
    sent = controller.reports(0x40)
    assert sent[0][:4] == bytes([0x40, 0x80, 0, 12])          # channel 0, apply
    assert sent[0][4:40] == bytes([255, 0, 0] * 12)           # stretched to 12
    assert sent[1][4:40] == bytes([0, 0, 255] * 12)


def test_a_header_with_no_leds_is_never_written() -> None:
    board, controller = _board(argb_headers=3, onboard_leds=0)
    board.show(board.devices()[2], [(9, 9, 9)])
    assert controller.reports(0x35) == controller.reports(0x40) == []


def test_reports_go_out_as_report_0xec(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HidApiTransport`` used to put 0x00 in front of every write; the
    controller only takes report 0xEC."""
    from trcc.adapters.device import transport as t

    written: list[bytes] = []

    class _Handle:
        def write(self, data: bytes) -> int:
            written.append(bytes(data))
            return len(data)

    monkeypatch.setattr(t, "HIDAPI_AVAILABLE", True)
    hid = t.HidApiTransport(AURA_VID, 0x19AF, report_id=REPORT_ID)
    hid._device, hid._is_open = _Handle(), True
    hid.write(0, request(0x82))
    assert written == [bytes([0xEC, 0x82]) + bytes(63)]


def test_a_stand_in_platform_hands_out_its_scripted_controller(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The Platform is the one way to the controller, so a mock or a test can
    never reach the host's real one."""
    platform = MockPlatform([], tmp_path)
    opened = platform.open_transport(Wire.HID, AURA_VID, 0x19AF,
                                     hid_reports=True, report_id=REPORT_ID)
    assert opened is platform.aura
