"""Led.send applies the wire-remap automatically.

Drives a full handshake + send through a scripted FakeBulkTransport
and asserts the wire bytes reflect the per-style remap.
"""
from __future__ import annotations

from trcc.adapters.device.led import (
    _COLOR_SCALE,
    _HID_REPORT_SIZE,
    _MAGIC,
    Led,
    LedPayload,
)
from trcc.core.led_protocol import (
    LED_REMAP_TABLES,
    remap_led_colors,
)
from trcc.core.models import Kind, LedStyle, ProductInfo, Wire

from .conftest import FakeBulkTransport


def _led_info() -> ProductInfo:
    return ProductInfo(
        vid=0x0416, pid=0x8001,
        vendor="Winbond",
        product="LED Controller (FormLED)",
        wire=Wire.LED, kind=Kind.LED,
        device_type=1,
    )


def _scripted_handshake(pm: int, sub: int = 0) -> bytes:
    buf = bytearray(_HID_REPORT_SIZE)
    buf[0:4] = _MAGIC
    buf[4] = sub
    buf[5] = pm
    buf[12] = 1
    return bytes(buf)


def _connected_led(pm: int) -> tuple[Led, FakeBulkTransport]:
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake(pm))
    led = Led(_led_info(), transport)
    led.connect()
    # Drop the handshake writes from the recording so .writes only
    # holds whatever the test sends.
    transport.writes.clear()
    return led, transport


def _decode_first_packet_colors(
    writes: list[tuple[int, bytes]],
    count: int,
) -> list[tuple[int, int, int]]:
    """Reconstruct the post-scale RGB array from the wire packets.

    Led builds one header + body chunked into 64-byte HID reports;
    bytes 20.. of the first chunk + subsequent chunks form the body
    laid out as RRGGBB per LED.  We undo the brightness*0.4 scale to
    get back to the post-remap logical color.
    """
    body = b"".join(chunk for _, chunk in writes)[20:20 + count * 3]
    colors: list[tuple[int, int, int]] = []
    for i in range(count):
        r = body[i * 3]
        g = body[i * 3 + 1]
        b = body[i * 3 + 2]
        colors.append((r, g, b))
    return colors


def test_send_remaps_using_pa120_handshake_style() -> None:
    """A PA120 device's send reorders colors via the LedStyle.PA120 table."""
    led, transport = _connected_led(pm=16)   # PM 16 → PA120
    assert led.led_handshake is not None
    assert led.led_handshake.style is LedStyle.PA120

    # Unscaled distinct colors so post-scale we can identify which
    # logical index landed at each physical position.  Stay above the
    # 0.4 scale rounding threshold by using multiples of 100.
    logical = [(i + 1, 0, 0) for i in range(84)]
    led.send(LedPayload(colors=list(logical), brightness=100))

    sent = _decode_first_packet_colors(transport.writes, 84)
    table = LED_REMAP_TABLES[LedStyle.PA120]
    for physical_idx, logical_idx in enumerate(table):
        if logical_idx >= len(logical):
            continue
        expected_r = int(logical[logical_idx][0] * _COLOR_SCALE)
        assert sent[physical_idx][0] == expected_r, (
            f"physical {physical_idx} should carry logical {logical_idx}: "
            f"expected R={expected_r}, got {sent[physical_idx]}"
        )


def test_send_passthrough_for_style_without_remap_table() -> None:
    """AX120 has no remap table — colors must reach the wire unchanged."""
    led, transport = _connected_led(pm=1)   # PM 1 → FROZEN_HORIZON_PRO / AX120
    assert led.led_handshake is not None
    assert led.led_handshake.style is LedStyle.AX120

    logical = [(200, 0, 0)] * 30
    led.send(LedPayload(colors=list(logical), brightness=100))

    sent = _decode_first_packet_colors(transport.writes, 30)
    for c in sent:
        assert c == (int(200 * _COLOR_SCALE), 0, 0)


def test_send_unknown_pm_passes_through() -> None:
    """Unknown PM → style=None → no remap, payload reaches wire as-is."""
    led, transport = _connected_led(pm=99)   # unknown
    assert led.led_handshake is not None
    assert led.led_handshake.style is None

    logical = [(150, 0, 0), (0, 150, 0)]
    led.send(LedPayload(colors=list(logical), brightness=100))

    sent = _decode_first_packet_colors(transport.writes, 2)
    expected = [
        (int(150 * _COLOR_SCALE), 0, 0),
        (0, int(150 * _COLOR_SCALE), 0),
    ]
    assert sent == expected


def test_send_remaps_is_on_mask_alongside_colors() -> None:
    """is_on arrives in logical order; the wire must see it remapped too.

    Light a single logical LED and verify that the only lit physical
    positions are those the PA120 table maps to logical 0.
    """
    led, transport = _connected_led(pm=16)

    logical_colors = [(200, 0, 0)] * 84
    logical_is_on = [False] * 84
    logical_is_on[0] = True   # only logical 0 is on

    led.send(LedPayload(
        colors=list(logical_colors),
        is_on=list(logical_is_on),
        brightness=100,
    ))

    sent = _decode_first_packet_colors(transport.writes, 84)
    table = LED_REMAP_TABLES[LedStyle.PA120]
    scaled = int(200 * _COLOR_SCALE)
    for physical_i, logical_i in enumerate(table):
        if logical_i == 0:
            assert sent[physical_i] == (scaled, 0, 0), (
                f"physical {physical_i} should reflect logical 0 = on"
            )
        else:
            assert sent[physical_i] == (0, 0, 0), (
                f"physical {physical_i} (logical {logical_i}) should be dark"
            )


def test_send_all_off_is_on_zeros_every_wire_led() -> None:
    """is_on=[False]*N produces zero RGB everywhere regardless of remap."""
    led, transport = _connected_led(pm=16)
    led.send(LedPayload(
        colors=[(200, 0, 0)] * 84,
        is_on=[False] * 84,
        brightness=100,
    ))
    sent = _decode_first_packet_colors(transport.writes, 84)
    assert all(c == (0, 0, 0) for c in sent)


# Sanity: the legacy helper is exposed at the same place callers can import.
def test_remap_module_exports() -> None:
    # remap tables + function live in core/led_protocol.py (pure wire-order
    # data) so the LED adapter imports them downward instead of reaching
    # up into services/ (the inverse-import fixed in the SOLID/DRY pass).
    from trcc.core import led_protocol
    assert "remap_led_colors" in led_protocol.__all__
    assert "LED_REMAP_TABLES" in led_protocol.__all__
    assert "LED_REMAP_SUB_TABLES" in led_protocol.__all__
    assert remap_led_colors is led_protocol.remap_led_colors
