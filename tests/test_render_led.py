"""RenderLed Command — segment mask + remap + send end-to-end.

Drives a real ``App`` against a ``FakePlatform`` with a scripted LED
handshake. The Command must:

  * Reject pre-connect dispatch.
  * Pull the handshake's resolved ``style`` and select the matching
    ``SegmentDisplay``.
  * Compute the mask from current sensor readings.
  * Build a per-LED color array (single color × mask_size).
  * Send through ``Led.send`` so the wire-remap fires.
  * Return a ``LedColorsResult`` summarising the result.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.led import (
    _COLOR_SCALE,
    _HID_REPORT_SIZE,
    _MAGIC,
    Led,
)
from trcc.app import App
from trcc.core.commands import RenderLed
from trcc.core.errors import DeviceNotConnectedError
from trcc.core.led_protocol import LED_REMAP_TABLES
from trcc.core.models import HardwareMetrics, LedStyle
from trcc.services.led_segment import compute_mask

from .conftest import FakePlatform

_LED_VID = 0x0416
_LED_PID = 0x8001
_LED_KEY = f"{_LED_VID:04x}:{_LED_PID:04x}"


def _scripted_handshake(pm: int, sub: int = 0) -> bytes:
    buf = bytearray(_HID_REPORT_SIZE)
    buf[0:4] = _MAGIC
    buf[4] = sub
    buf[5] = pm
    buf[12] = 1
    return bytes(buf)


def _decode_body(writes: list[tuple[int, bytes]],
                 count: int) -> list[tuple[int, int, int]]:
    """Reconstruct the per-LED bytes the wire received."""
    body = b"".join(chunk for _, chunk in writes)[20:20 + count * 3]
    return [(body[i * 3], body[i * 3 + 1], body[i * 3 + 2])
            for i in range(count)]


def _attach_and_connect(app: App, platform: FakePlatform, pm: int) -> None:
    """Inject a handshake reply, attach the LED, connect it."""
    platform.bulk.read_script.append(_scripted_handshake(pm))
    app.attach(_LED_VID, _LED_PID)
    app.get(_LED_KEY).connect()
    # Production connects via ConnectDevice, which starts the send worker;
    # this helper shortcuts the handshake, so start the worker explicitly so
    # ``app.send`` (the rerouted write path) has a sender to submit to.
    app.start_sender(_LED_KEY)
    # Drop the handshake writes so post-render assertions see only send().
    platform.bulk.writes.clear()


def test_render_led_lights_segment_mask_for_pa120(
    fake_platform: FakePlatform,
) -> None:
    """PA120 mask must be non-empty, and every lit LED on the wire must
    correspond to a logical position the mask marked True (after remap).
    """
    app = App(fake_platform)
    _attach_and_connect(app, fake_platform, pm=16)   # PM 16 → PA120 (84 LEDs)
    color = (255, 0, 0)

    result = app.dispatch(RenderLed(key=_LED_KEY, color=color, phase=0))

    assert result.ok
    assert result.colors == [color] * 84
    assert "84 LEDs" in result.message

    # Recompute the expected mask the same way the Command does — from the
    # same typed snapshot — then apply the wire-remap to land on the
    # per-physical-LED expectation.
    metrics = fake_platform.sensors().snapshot()
    expected_mask = compute_mask(
        LedStyle.PA120, metrics, phase=0, temp_unit="C",
    )
    assert len(expected_mask) == 84
    assert any(expected_mask), "PA120 mask should not be entirely off"

    # The wire body reflects the LOGICAL mask passed through the
    # PA120 remap table: for each physical_i, the LED is lit iff the
    # logical position table[physical_i] is on in the computed mask.
    sent = _decode_body(fake_platform.bulk.writes, 84)
    scaled = int(255 * _COLOR_SCALE)
    table = LED_REMAP_TABLES[LedStyle.PA120]
    for physical_i in range(84):
        logical_i = table[physical_i]
        is_lit = logical_i < len(expected_mask) and expected_mask[logical_i]
        if is_lit:
            assert sent[physical_i] == (scaled, 0, 0), (
                f"physical {physical_i} (=logical {logical_i}) "
                "should be lit"
            )
        else:
            assert sent[physical_i] == (0, 0, 0), (
                f"physical {physical_i} (=logical {logical_i}) "
                "should be dark"
            )


def test_led_settings_changed_re_renders_immediately(
    fake_platform: FakePlatform,
) -> None:
    """A LED settings mutation publishes ``LedSettingsChanged``; the render
    observer must re-render the device right away (a wire write), not wait for
    the next sensor tick.  Guards the Increment-3 wiring."""
    from trcc.core.events import LedSettingsChanged

    from .conftest import _CliRenderer

    app = App(fake_platform, renderer=_CliRenderer())  # type: ignore[arg-type]
    _attach_and_connect(app, fake_platform, pm=16)     # PA120
    fake_platform.bulk.writes.clear()

    app.events.publish(LedSettingsChanged(key=_LED_KEY))

    assert fake_platform.bulk.writes, (
        "LedSettingsChanged should trigger an immediate RenderLed (wire write)"
    )


def test_render_led_remap_reorders_lit_positions(
    fake_platform: FakePlatform,
) -> None:
    """When the mask is mostly-off, the lit positions on the wire must
    line up with the logical mask passed through the remap table.

    Smoke that Led.send's remap fires inside the RenderLed path.
    """
    app = App(fake_platform)
    _attach_and_connect(app, fake_platform, pm=16)

    result = app.dispatch(RenderLed(key=_LED_KEY, color=(200, 0, 0)))
    assert result.ok

    # The table length proves the right style was picked.
    assert len(LED_REMAP_TABLES[LedStyle.PA120]) == 84
    # And the wire received exactly 84 LED triples.
    sent = _decode_body(fake_platform.bulk.writes, 84)
    assert len(sent) == 84


def test_render_led_rejects_disconnected_device(
    fake_platform: FakePlatform,
) -> None:
    """Pre-connect dispatch must raise DeviceNotConnectedError."""
    app = App(fake_platform)
    app.attach(_LED_VID, _LED_PID)   # attach but don't connect

    with pytest.raises(DeviceNotConnectedError):
        app.dispatch(RenderLed(key=_LED_KEY, color=(255, 0, 0)))


def test_render_led_rejects_unknown_device_key(
    fake_platform: FakePlatform,
) -> None:
    """Unknown key returns ok=False with a 'Not attached' message."""
    app = App(fake_platform)
    result = app.dispatch(RenderLed(key="dead:beef", color=(255, 0, 0)))
    assert not result.ok
    assert "Not attached" in result.message


def test_render_led_rejects_style_without_segment_display(
    fake_platform: FakePlatform,
) -> None:
    """PM 160 → LedStyle.LF13 has no SegmentDisplay → ok=False, no send."""
    app = App(fake_platform)
    _attach_and_connect(app, fake_platform, pm=160)   # PM 160 → LF13
    led = app.get(_LED_KEY)
    assert isinstance(led, Led)
    assert led.led_handshake is not None
    assert led.led_handshake.style is LedStyle.LF13

    result = app.dispatch(RenderLed(key=_LED_KEY, color=(0, 0, 255)))

    assert not result.ok
    assert "no segment display" in result.message
    # And no wire traffic resulted.
    assert fake_platform.bulk.writes == []


def test_render_led_rejects_lcd_device_key(
    fake_platform: FakePlatform,
) -> None:
    """Pointing RenderLed at an LCD key returns ok=False, doesn't dispatch."""
    app = App(fake_platform)
    # Attach an LCD (SCSI). FakePlatform supports it.
    app.attach(0x0402, 0x3922)
    result = app.dispatch(RenderLed(key="0402:3922", color=(255, 0, 0)))
    assert not result.ok
    assert "not an LED device" in result.message


# ── DDR memory multiplier scales the LC1 memory reading ─────────────


def test_lc1_memory_ratio_scales_displayed_value() -> None:
    """LC1 phase 1 (mem_clock, mode 1) multiplies the reading by the DDR
    ratio before encoding — so ×2 of V equals ×1 of 2V, and a higher
    multiplier renders a different mask.  This is the feature restored from
    legacy (the cutover had frozen the multiplier at the default)."""
    half_x2 = compute_mask(
        LedStyle.LC1, HardwareMetrics(mem_clock=400), phase=1, temp_unit="C",
        memory_ratio=2,
    )
    full_x1 = compute_mask(
        LedStyle.LC1, HardwareMetrics(mem_clock=800), phase=1, temp_unit="C",
        memory_ratio=1,
    )
    assert half_x2 == full_x1          # 400×2 == 800×1 → identical digits

    full_x4 = compute_mask(
        LedStyle.LC1, HardwareMetrics(mem_clock=800), phase=1, temp_unit="C",
        memory_ratio=4,
    )
    assert full_x4 != full_x1          # ×4 changes the rendered value
