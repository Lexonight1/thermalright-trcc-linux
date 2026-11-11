"""OpenRGB drives a cooler -- automatically (#160, #333).

With RGB Lighting on, each cooler has its own localhost DDP port; colours
OpenRGB sends there show over the cooler's own mode, and the mode is back
``LIVE_FOR_S`` after they stop.  No LED mode, no switch.  The packets are
shaped exactly as OpenRGB's sender writes them (``Controllers/DDPController``,
OpenRGB 1.0): flags 0x41 (version 1, push), data type 1, destination 1, a
big-endian offset and length, then RGB.
"""
from __future__ import annotations

import socket
import time
from typing import Any, cast

import pytest

from trcc.app import App
from trcc.core.commands import RenderLed, SetLedBrightness, SetLedMode
from trcc.core.led_models import LEDMode
from trcc.core.models import CoolerLights
from trcc.core.ports import LedSource
from trcc.modules.rgb.adapters.ddp import DdpPort, apply_packet
from trcc.modules.rgb.core.ports import DdpListener
from trcc.modules.rgb.services.openrgb_lights import LIVE_FOR_S, OpenRgbLights

from ..conftest import FakePlatform
from ..test_render_led import _LED_KEY, _attach_and_connect


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


def _arrives(port: DdpPort, colors: int = 2) -> list[tuple[int, int, int]]:
    """The port's colours once the first packet landed (3 s at most)."""
    deadline = time.monotonic() + 3
    while port.last_packet is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return port.colors(colors)


def test_a_port_takes_real_packets_and_lets_go() -> None:
    """A real socket on localhost: OpenRGB's packet in, the colours out and
    when, the port closed.  Black past what was sent."""
    port = DdpPort(0)
    try:
        assert (port.error, port.last_packet) == ("", None)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(_packet(b"\x0a\x14\x1e"), ("127.0.0.1", port.port))
        assert _arrives(port) == [(10, 20, 30), (0, 0, 0)]
        assert port.last_packet is not None
    finally:
        port.close()
    assert port._sock is None


def test_only_this_computer_can_drive_a_cooler() -> None:
    """Bound to 127.0.0.1: nothing on the network can set a cooler's
    colours.  MUTATION CHECK: bind 0.0.0.0."""
    port = DdpPort(0)
    try:
        assert port._sock is not None
        assert port._sock.getsockname()[0] == "127.0.0.1"
    finally:
        port.close()


def test_a_taken_port_says_so_and_listens_to_nothing() -> None:
    """Another program on the port: said, in the words the RGB page shows;
    no listener, no crash."""
    holder = DdpPort(0)
    try:
        taken = DdpPort(holder.port)
        assert taken.error.startswith(f"port {holder.port} is taken")
        assert (taken._sock, taken.last_packet) == (None, None)
        taken.close()
    finally:
        holder.close()


# ── OpenRgbLights: a port per cooler, live while packets come ──────────────

class _Listener(DdpListener):
    """A port, scripted: *colour* on every LED, the last packet at *last*."""

    def __init__(self, port: int) -> None:
        self._port, self.closed = port, 0
        self.last: float | None = None
        self.colour = (1, 2, 3)

    @property
    def port(self) -> int:
        return self._port

    @property
    def error(self) -> str:
        return ""

    @property
    def last_packet(self) -> float | None:
        return self.last

    def colors(self, count: int) -> list[tuple[int, int, int]]:
        return [self.colour] * count

    def close(self) -> None:
        self.closed += 1


def _cooler(key: str, leds: int = 30) -> CoolerLights:
    return CoolerLights(key=key, name=key, led_count=leds)


def _lights(clock: list[float]) -> tuple[OpenRgbLights, dict[int, _Listener]]:
    """Ports handed out from 4048 in order of first sight, on a clock the
    test moves."""
    opened: dict[int, _Listener] = {}
    given: dict[str, int] = {}

    def port_for(key: str) -> int:
        return given.setdefault(key, 4048 + len(given))

    def open_port(port: int) -> _Listener:
        opened[port] = _Listener(port)
        return opened[port]

    return OpenRgbLights(open_port, port_for, clock=lambda: clock[0]), opened


def test_each_cooler_gets_its_own_port_and_a_gone_one_is_closed() -> None:
    lights, opened = _lights([0.0])
    lights.watch([_cooler("a"), _cooler("b")])
    lights.watch([_cooler("b")])

    assert sorted(opened) == [4048, 4049]
    assert (opened[4048].closed, opened[4049].closed) == (1, 0)
    assert [(c.key, port) for c, port, _ in lights.addresses()] == [("b", 4049)]


def test_colours_are_live_while_packets_come_and_the_mode_is_back_after() -> None:
    """None before any packet; live within ``LIVE_FOR_S`` of the last; None
    after -- the cooler shows its own mode again, the way WLED does.
    MUTATION CHECK: drop the age test in ``OpenRgbLights.live``."""
    clock = [100.0]
    lights, opened = _lights(clock)
    lights.watch([_cooler("a")])
    before = lights.live("a", 2)
    opened[4048].last = 100.0
    clock[0] = 100.0 + LIVE_FOR_S
    during = lights.live("a", 2)
    clock[0] += 0.01

    assert (before, during, lights.live("a", 2), lights.live("b", 2)) == (
        None, [(1, 2, 3)] * 2, None, None)


def test_close_closes_every_port() -> None:
    lights, opened = _lights([0.0])
    lights.watch([_cooler("a"), _cooler("b")])
    lights.close()
    assert [p.closed for p in opened.values()] == [1, 1]
    assert lights.addresses() == ()


def test_a_coolers_port_is_kept_across_runs(tmp_home: Any) -> None:
    """OpenRGB's DDP device names the port: a restart, or another cooler
    plugged in, must not move it."""
    from trcc.core.ports import SettingsPort
    from trcc.modules.rgb.services.settings import RgbSettings

    store: dict[str, Any] = {}

    class _Port:
        def app(self) -> dict[str, Any]:
            return dict(store)

        def save_app(self, data: dict[str, Any]) -> None:
            store.clear()
            store.update(data)

    first = RgbSettings(cast("SettingsPort", _Port()))
    assert (first.ddp_port("a", 4048), first.ddp_port("b", 4048)) == (4048, 4049)
    again = RgbSettings(cast("SettingsPort", _Port()))
    assert (again.ddp_port("b", 4048), again.ddp_port("c", 4048)) == (4049, 4050)


# ── The OpenRGB mode is retired ───────────────────────────────────────────

@pytest.mark.parametrize("zone", [False, True])
def test_a_cooler_saved_in_the_openrgb_mode_loads_as_static(zone: bool) -> None:
    """9.12 saved OpenRGB as mode 6; it is automatic now, so 6 loads as
    STATIC -- not dropped to the field's default, which for a whole cooler is
    RAINBOW.  (A zone's default is STATIC already, so only the cooler case can
    catch a lost migration.)  MUTATION CHECK: drop the retired-mode branch of
    ``_led_mode`` -> the cooler case fails."""
    from trcc.services.settings import _led_settings_from_dict, _led_zone_from_dict

    loaded = (_led_zone_from_dict({"mode": 6}) if zone
              else _led_settings_from_dict({"mode": 6}))
    assert loaded.mode is LEDMode.STATIC


def test_the_cli_says_openrgb_is_automatic_now(cli_runner: Any, cli_app: App) -> None:
    from trcc.core.led_models import OPENRGB_IS_AUTOMATIC
    from trcc.ui.cli.main import app

    result = cli_runner.invoke(app, ["led", "mode", "0416:8001", "openrgb"])

    assert result.exit_code != 0
    assert " ".join(OPENRGB_IS_AUTOMATIC.split()[:6]) in " ".join(result.output.split())


def test_the_modes_are_the_c_sharp_six() -> None:
    """The row the art has six tiles for (#333)."""
    from trcc.core.led_models import LED_MODE_LABELS
    assert ([m.name for m in LEDMode], LED_MODE_LABELS) == (
        ["STATIC", "BREATHING", "COLORFUL", "RAINBOW", "TEMP_LINKED", "LOAD_LINKED"],
        ["Solid", "Breathe", "Color Cycle", "Rainbow", "Temp Linked", "Load Linked"])


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
    from ..conftest import _CliRenderer

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


# ── Where OpenRGB finds each cooler (OpenRgbPorts) ─────────────────────────

@pytest.mark.parametrize(("coolers", "lines"), [
    ((), ("No cooler is connected -- OpenRGB has none to light.",)),
    (((("PA120 DIGITAL", 84, 4048, "")),
      ("AX120 DIGITAL", 30, 4049, "port 4049 is taken (Address in use)")),
     ("OpenRGB can light your coolers: in OpenRGB, add a DDP device for each, "
      "with these values.  Its colours show while OpenRGB sends them.",
      "This needs OpenRGB 1.0 or newer.",
      "PA120 DIGITAL: IP 127.0.0.1, port 4048, 84 LEDs",
      "AX120 DIGITAL: no port for OpenRGB -- port 4049 is taken (Address in use)")),
])
def test_the_lines_say_what_to_type_into_openrgb(
        coolers: tuple[tuple[str, int, int, str], ...],
        lines: tuple[str, ...]) -> None:
    from trcc.modules.rgb.core.models import CoolerPort
    from trcc.modules.rgb.core.results import OpenRgbPortsResult
    from trcc.modules.rgb.ui.presentation.openrgb_ports import openrgb_port_lines

    result = OpenRgbPortsResult(coolers=tuple(
        CoolerPort(key=name, name=name, led_count=leds, port=port, error=error)
        for name, leds, port, error in coolers))
    assert openrgb_port_lines(result) == lines


def test_the_query_names_each_connected_coolers_port_and_leds(
        fake_platform: FakePlatform) -> None:
    """Through the App: a connected AX120 -> its own open port and the 30 LEDs
    a render draws -- what OpenRGB's DDP device must say."""
    from trcc.modules.rgb.core.commands import OpenRgbPorts

    from ..conftest import _CliRenderer, rgb_module

    app = App(fake_platform, renderer=_CliRenderer())
    _attach_and_connect(app, fake_platform, pm=1)
    lights = rgb_module(app).lights
    lights.watch(app.led_coolers())
    try:
        result = app.dispatch(OpenRgbPorts())
    finally:
        lights.close()

    (cooler,) = result.coolers
    assert (result.ok, cooler.key, cooler.led_count, cooler.error) == (
        True, _LED_KEY, 30, "")
    assert cooler.port > 0


def test_the_port_is_read_once_at_bind_never_from_a_closing_socket() -> None:
    """``port`` asked the socket each time, and a ``close`` on another thread
    could clear and close it between the check and the ask -- EBADF on the
    receive thread (ci_local, 2026-11-10).  It is kept from the bind now, so
    it stays the OS's pick after a close.  MUTATION CHECK: read
    ``getsockname`` in ``port`` again and, closed, it answers the asked 0."""
    from trcc.modules.rgb.adapters.ddp import DdpPort

    listener = DdpPort(0)
    bound = listener.port
    listener.close()
    assert bound != 0 and listener.port == bound
