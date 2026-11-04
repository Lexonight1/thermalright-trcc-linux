"""The OpenRGB LED mode: a cooler shows the colours OpenRGB sends over DDP (#160).

The packets are shaped exactly as OpenRGB's sender writes them
(``Controllers/DDPController``, OpenRGB 1.0): flags 0x41 (version 1, push),
data type 1, destination 1, a big-endian offset and length, then RGB.
"""
from __future__ import annotations

import socket
import time
from typing import Any

import pytest

from trcc.adapters.infra.ddp_receiver import DdpReceiver, apply_packet
from trcc.app import App
from trcc.core.commands import RenderLed, SetLedBrightness, SetLedMode
from trcc.core.led_models import LedDeviceSettings, LEDMode, LedRuntimeState
from trcc.core.ports import LedColorStream, LedSource
from trcc.services.led_effects import LEDEffectEngine

from .conftest import FakePlatform
from .test_render_led import _LED_KEY, _attach_and_connect


def _packet(rgb: bytes, offset: int = 0, flags: int = 0x41,
            timecode: bytes = b"") -> bytes:
    """One DDP packet as OpenRGB builds it."""
    return (bytes([flags, 1, 1, 1]) + offset.to_bytes(4, "big")
            + len(rgb).to_bytes(2, "big") + timecode + rgb)


@pytest.mark.parametrize(("packets", "frame"), [
    ([_packet(b"\x01\x02\x03\x04\x05\x06")], b"\x01\x02\x03\x04\x05\x06"),
    # A frame bigger than one packet arrives in pieces, each at its offset.
    ([_packet(b"\x01\x02\x03"), _packet(b"\x04\x05\x06", offset=3)],
     b"\x01\x02\x03\x04\x05\x06"),
    # The 14-byte header: flag 0x10 adds a timecode.
    ([_packet(b"\x07\x08\x09", flags=0x51, timecode=b"\0\0\0\0")], b"\x07\x08\x09"),
])
def test_a_packet_lands_at_its_offset(packets: list[bytes], frame: bytes) -> None:
    got = bytearray()
    assert all(apply_packet(got, p) for p in packets)
    assert bytes(got) == frame


@pytest.mark.parametrize("packet", [
    b"\x41\x01",                                   # shorter than a header
    _packet(b"\x01\x02\x03", flags=0x81),          # version 2
    _packet(b"\x01\x02\x03", offset=10**9),        # past any cooler
])
def test_anything_else_is_ignored(packet: bytes) -> None:
    frame = bytearray(b"\x09\x09\x09")
    assert (apply_packet(frame, packet), bytes(frame)) == (False, b"\x09\x09\x09")


def test_the_receiver_takes_real_packets_and_lets_go() -> None:
    """A real socket on localhost: OpenRGB's packet in, the colours out,
    the port closed on stop.  Black past what was sent."""
    receiver = DdpReceiver(port=0)
    try:
        assert receiver.colors(2) == [(0, 0, 0), (0, 0, 0)]      # starts it
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(_packet(b"\x0a\x14\x1e"), ("127.0.0.1", receiver.port))
        deadline = time.monotonic() + 3
        while receiver.colors(2)[0] == (0, 0, 0) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert receiver.colors(2) == [(10, 20, 30), (0, 0, 0)]
    finally:
        receiver.stop()
    assert receiver.listening is False


def test_only_this_computer_can_drive_the_cooler() -> None:
    """Bound to 127.0.0.1: nothing on the network can set the cooler's
    colours.  MUTATION CHECK: bind 0.0.0.0."""
    receiver = DdpReceiver(port=0)
    try:
        receiver.colors(1)
        assert receiver._sock is not None
        assert receiver._sock.getsockname()[0] == "127.0.0.1"
    finally:
        receiver.stop()


class _Stream(LedColorStream):
    """OpenRGB, scripted: one colour on every LED."""

    def __init__(self, color: tuple[int, int, int] = (10, 20, 30)) -> None:
        self.color, self.on, self.stops = color, False, 0

    def colors(self, count: int) -> list[tuple[int, int, int]]:
        self.on = True
        return [self.color] * count

    def stop(self) -> None:
        self.on, self.stops = False, self.stops + 1

    @property
    def listening(self) -> bool:
        return self.on


def test_the_mode_shows_the_stream_and_no_stream_is_dark() -> None:
    settings = LedDeviceSettings(mode=LEDMode.OPENRGB)
    lit = LEDEffectEngine(_Stream()).tick(settings, LedRuntimeState(), {},
                                          led_count=3)
    dark = LEDEffectEngine().tick(settings, LedRuntimeState(), {}, led_count=3)
    assert (lit, dark) == ([(10, 20, 30)] * 3, [(0, 0, 0)] * 3)


def _openrgb_app(fake_platform: FakePlatform) -> tuple[App, _Stream]:
    from .conftest import _CliRenderer

    stream = _Stream()
    app = App(fake_platform, renderer=_CliRenderer(),
              led_stream=stream)
    _attach_and_connect(app, fake_platform, pm=1)        # AX120: digits
    return app, stream


def test_a_cooler_in_the_mode_lights_with_openrgbs_colours(
        fake_platform: FakePlatform) -> None:
    """The whole chain: the Command every UI sends, then a render.
    MUTATION CHECK: drop the OPENRGB case in LEDEffectEngine._tick_mode."""
    app, _stream = _openrgb_app(fake_platform)
    assert app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.OPENRGB)).ok
    # The cooler's own brightness still applies on top: at 100, exact.
    assert app.dispatch(SetLedBrightness(key=_LED_KEY, percent=100)).ok
    colors = app.dispatch(RenderLed(key=_LED_KEY)).colors
    assert colors and set(colors) == {(10, 20, 30)}


class _Gradient(_Stream):
    """OpenRGB sending a different colour to every LED: pixel i = (i, 0, 0)."""

    def colors(self, count: int) -> list[tuple[int, int, int]]:
        self.on = True
        return [(i, 0, 0) for i in range(count)]


@pytest.mark.parametrize("pm", [16, 1])        # PA120 (four zones), AX120 (digits)
def test_each_led_shows_its_own_pixel(fake_platform: FakePlatform, pm: int) -> None:
    """LED i shows OpenRGB's pixel i, on a zoned cooler and a digit display
    alike.  Measured before: a PA120 showed 4 distinct colours of 84 -- every
    zone repeated the start of the frame.  MUTATION CHECK: hand a zone the
    first N pixels instead of its own indices."""
    from .conftest import _CliRenderer

    app = App(fake_platform, renderer=_CliRenderer(),
              led_stream=_Gradient())
    _attach_and_connect(app, fake_platform, pm=pm)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.OPENRGB))
    app.dispatch(SetLedBrightness(key=_LED_KEY, percent=100))
    colors = app.dispatch(RenderLed(key=_LED_KEY)).colors
    lit = [(i, c) for i, c in enumerate(colors) if c != (0, 0, 0)]
    assert len(lit) > len(colors) // 2
    assert all(c == (i, 0, 0) for i, c in lit)


def test_the_listener_closes_once_no_cooler_shows_openrgb(
        fake_platform: FakePlatform) -> None:
    """No open port while nothing needs it.  MUTATION CHECK: drop the stop
    in LedAnimationLoop.tick."""
    app, stream = _openrgb_app(fake_platform)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.OPENRGB))
    app.led_animation_loop.tick()                       # renders: listening
    assert (stream.listening, stream.stops) == (True, 0)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.RAINBOW))
    app.led_animation_loop.tick()
    assert (stream.listening, stream.stops) == (False, 1)


def test_the_cli_switches_a_cooler_to_openrgb(cli_runner: Any, cli_app: App) -> None:
    """``trcc led mode KEY openrgb``: the CLI's name for it, persisted."""
    from trcc.ui.cli.main import app

    result = cli_runner.invoke(app, ["led", "mode", "0416:8001", "openrgb"])
    assert result.exit_code == 0, result.output
    assert cli_app.settings.for_led("0416:8001").mode is LEDMode.OPENRGB


# ── Live colours a module receives (LedSource): over the mode, while they come ──

class _Live(LedSource):
    """A module's source: *frame* for cooler *for_key*, None for any other --
    or for every cooler once it is quiet."""

    def __init__(self, frame: Any = None, for_key: str = _LED_KEY) -> None:
        self.frame, self.for_key, self.closed = frame, for_key, 0

    def live(self, key: str, count: int) -> list[tuple[int, int, int]] | None:
        if key != self.for_key or self.frame is None:
            return None
        return [self.frame(i) for i in range(count)]

    def close(self) -> None:
        self.closed += 1


def _live_app(fake_platform: FakePlatform, pm: int,
              source: _Live) -> App:
    from .conftest import _CliRenderer

    app = App(fake_platform, renderer=_CliRenderer())
    _attach_and_connect(app, fake_platform, pm=pm)
    app.add_led_source("probe", source)
    app.dispatch(SetLedBrightness(key=_LED_KEY, percent=100))
    return app


@pytest.mark.parametrize("pm", [16, 1])        # PA120 (four zones), AX120 (digits)
def test_live_colours_show_over_the_mode_led_by_led(
        fake_platform: FakePlatform, pm: int) -> None:
    """No mode to pick: while a module has live colours for the cooler, LED i
    shows pixel i -- on a zoned cooler and a digit display alike -- whatever
    the cooler's own mode is.  MUTATION CHECK: drop the live branch of
    ``LEDEffectEngine.tick_multi_zone`` (PA120) or ``tick`` (AX120)."""
    app = _live_app(fake_platform, pm, _Live(lambda i: (i, 0, 0)))
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.RAINBOW))

    colors = app.dispatch(RenderLed(key=_LED_KEY)).colors

    lit = [(i, c) for i, c in enumerate(colors) if c != (0, 0, 0)]
    assert len(lit) > len(colors) // 2
    assert all(c == (i, 0, 0) for i, c in lit)


def test_a_quiet_source_leaves_the_cooler_its_own_mode(
        fake_platform: FakePlatform) -> None:
    """None -- nothing arrived lately -- and the cooler shows its mode again."""
    source = _Live(lambda i: (7, 8, 9))
    app = _live_app(fake_platform, 1, source)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.STATIC))
    live = app.dispatch(RenderLed(key=_LED_KEY)).colors
    source.frame = None

    own = app.dispatch(RenderLed(key=_LED_KEY)).colors

    assert set(live) == {(7, 8, 9)} and (7, 8, 9) not in set(own)


def test_a_cooler_with_live_colours_ticks_fast(fake_platform: FakePlatform) -> None:
    """A STATIC cooler is not animated -- unless live colours are arriving,
    which move as fast as they are sent.  MUTATION CHECK: drop ``live`` from
    ``LedAnimationLoop.animating_keys``."""
    source = _Live(lambda i: (1, 2, 3))
    app = _live_app(fake_platform, 1, source)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.STATIC))
    live = app.led_animation_loop.animating_keys()
    source.frame = None

    assert (live, app.led_animation_loop.animating_keys()) == ([_LED_KEY], [])


def test_a_module_gives_live_colours_through_lights_and_takes_them_back(
        fake_platform: FakePlatform) -> None:
    """The ``lights`` capability: a module adds its source; switched off, the
    source is closed and the coolers go back to their own modes."""
    from trcc.core.models import ModuleManifest
    from trcc.services.modules import ModuleScope

    app = App(fake_platform)
    source = _Live(lambda i: (4, 5, 6))
    scope = ModuleScope(app, ModuleManifest(key="rgb", title="RGB", summary="s",
                                            capabilities=("lights",)))
    scope.lights.add_source(source)
    shown = app.live_led_colors(_LED_KEY, 2)

    scope.release(end_sources=False)

    assert (shown, app.live_led_colors(_LED_KEY, 2), source.closed) == (
        [(4, 5, 6)] * 2, None, 1)


def test_lights_is_refused_to_a_module_that_did_not_declare_it(
        fake_platform: FakePlatform) -> None:
    from trcc.core.errors import ModuleUnavailable
    from trcc.core.models import ModuleManifest
    from trcc.services.modules import ModuleScope

    scope = ModuleScope(App(fake_platform), ModuleManifest(
        key="x", title="X", summary="s", capabilities=("events",)))

    with pytest.raises(ModuleUnavailable):
        scope.lights.add_source(_Live())
