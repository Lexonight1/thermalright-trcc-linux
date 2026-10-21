"""Other RGB follows the cooler (#160): the App service, Command and Query."""
from __future__ import annotations

import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest

from trcc.adapters.rgb.asus_aura import effect_packet
from trcc.app import App
from trcc.core.commands import (
    BlinkAuraHeader,
    ConnectDevice,
    RenderLed,
    RgbFollow,
    ScanRgbLights,
    SetAuraLeds,
    SetRamEffect,
    SetRgbFollow,
)
from trcc.core.events import LedColorsChanged
from trcc.core.models import (
    MAX_ARGB_LEDS,
    LightKind,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbMirrorDevice,
)
from trcc.core.ports import RamAccess, RamLights, SmBus
from trcc.services.rgb_mirror import BLINK_COLOR, BLINK_STEPS, RgbMirrorService

from .mock_platform import (
    MockPlatform,
    ScriptedCorsairStick,
    ScriptedRgbHardware,
    ScriptedSmBus,
    scripted_ram,
)

OFF, OPENRGB, RAM_MODE, BOARD = RgbFollowMode
STRIP = RgbMirrorDevice(0, "Strip", 4, "strip")
RAM = RgbMirrorDevice(1, "RAM", 2, "ram")


class FakeMirror(RamLights):
    """Records what it is shown and the effects it keeps; ``down`` makes
    every call fail.  A ``RamLights`` because the service's RAM factory
    promises one -- the follow half alone would not keep its contract."""

    def __init__(self, mode: RgbFollowMode = RgbFollowMode.OPENRGB,
                 host: str = "", port: int = 0) -> None:
        self.mode = mode
        self.address = (host, port)
        self.shown: list[tuple[str, tuple]] = []
        self.down = False
        self.closed = 0
        self.scanned = False
        self.effects: list[tuple[str, RamEffectSettings]] = []
        #: "release" and "close", in the order they came.
        self.steps: list[str] = []
        self.release_fails = False

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        if self.down:
            raise ConnectionRefusedError("nothing on 6742")
        self.scanned = True
        return (STRIP, RAM)

    def found(self) -> tuple[RgbMirrorDevice, ...] | None:
        return (STRIP, RAM) if self.scanned else None

    def apply_effect(self, device, settings) -> None:
        self.effects.append((device.name, settings))

    def show(self, device, colors) -> None:
        self.shown.append((device.name, tuple(colors)))

    def release(self) -> None:
        self.steps.append("release")
        if self.release_fails:
            raise ConnectionResetError("gone mid-release")

    def close(self) -> None:
        self.steps.append("close")
        self.closed += 1
        self.scanned = False


def _until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert check(), "timed out"


def _service(retry_s: float = 10.0,
             on_sent=None) -> tuple[RgbMirrorService, list[FakeMirror]]:
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    return RgbMirrorService(make, retry_s=retry_s, on_sent=on_sent), made


def _colors(key: str, *rgb: tuple[int, int, int]) -> LedColorsChanged:
    return LedColorsChanged(key=key, color_count=len(rgb), colors=rgb)


def test_the_leading_coolers_colours_reach_every_device() -> None:
    service, made = _service()
    service.configure(OPENRGB, "127.0.0.1", 6742)
    service.on_colors(_colors("0416:8001", (9, 8, 7)))
    _until(lambda: len(made[0].shown) == 2)
    assert made[0].address == ("127.0.0.1", 6742)
    assert made[0].shown == [("Strip", ((9, 8, 7),)), ("RAM", ((9, 8, 7),))]
    assert service.status.connected and service.status.devices == ("Strip", "RAM")
    assert service.status.lead == "0416:8001"
    service.stop()


def test_stopping_hands_the_lights_back_then_lets_go() -> None:
    """The service decides it, through the port alone -- no chip.
    MUTATION CHECK: drop the _release call in _stop_following."""
    service, made = _service()
    service.configure(BOARD, "", 0)
    service.stop()
    assert made[0].steps == ["release", "close"]


def test_lights_that_cannot_be_handed_back_are_still_let_go() -> None:
    service, made = _service()
    service.configure(BOARD, "", 0)
    made[0].release_fails = True
    service.stop()
    assert made[0].steps == ["release", "close"]


def test_nothing_to_follow_says_why_and_does_not_fail() -> None:
    """With "board" saved and no board plugged in, the App still starts."""
    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        raise OSError("no motherboard RGB that TRCC drives was found")

    service = RgbMirrorService(make)
    service.configure(BOARD, "", 0)
    assert (service.status.mode, service.status.error) == (
        BOARD, "no motherboard RGB that TRCC drives was found")
    service.stop()


def test_the_app_lights_the_motherboard_and_hands_it_back(tmp_path: Path) -> None:
    """The maintainer's board, scripted reply for reply: header 1's 8 LEDs
    take the cooler's colours through effect channel 1 -- the one the chip
    says drives it -- and stopping puts channel 1 back on its Rainbow,
    unsaved."""
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.rgb = ScriptedRgbHardware()
    app = App(platform)
    app.dispatch(ScanRgbLights())                     # Find lists the headers
    assert app.dispatch(SetAuraLeds(header=1, leds=8)).ok
    assert app.dispatch(SetRgbFollow(mode=BOARD, targets=("aura/argb1",))).ok
    app.events.publish(_colors("0416:8001", (255, 0, 0)))
    _until(lambda: bool(platform.aura.reports(0x40)))
    assert platform.aura.reports(0x40)[0][:28] == (
        bytes([0x40, 0x80, 0, 8]) + bytes([255, 0, 0] * 8))
    app.dispatch(SetRgbFollow(mode=OFF))
    assert platform.aura.reports(0x35) == [effect_packet(1, 0xFF),
                                           effect_packet(1, 0x05)]
    assert platform.aura.reports(0x3F) == []
    app.close()


def test_a_second_cooler_does_not_fight_the_lead() -> None:
    service, made = _service()
    service.configure(OPENRGB, "h", 1)
    service.on_colors(_colors("lead", (1, 1, 1)))
    _until(lambda: len(made[0].shown) == 2)
    service.on_colors(_colors("other", (2, 2, 2)))
    time.sleep(0.2)                       # the worker's chance to send it
    assert len(made[0].shown) == 2, "a non-leading cooler was followed"
    service.on_colors(_colors("lead", (3, 3, 3)))
    _until(lambda: len(made[0].shown) == 4)
    assert {c for _, c in made[0].shown} == {((1, 1, 1),), ((3, 3, 3),)}
    service.stop()


def test_nothing_is_sent_while_following_is_off() -> None:
    service, made = _service()
    service.on_colors(_colors("k", (1, 2, 3)))
    assert made == [] and service.status.mode is OFF


def test_an_unreachable_openrgb_is_reported_and_retried() -> None:
    service, made = _service(retry_s=0.05)
    service.configure(OPENRGB, "h", 1)
    made[0].down = True
    service.on_colors(_colors("k", (1, 2, 3)))
    _until(lambda: service.status.error != "")
    assert service.status.error == "ConnectionRefusedError: nothing on 6742"
    assert not service.status.connected
    made[0].down = False
    time.sleep(0.1)
    service.on_colors(_colors("k", (4, 5, 6)))
    _until(lambda: service.status.connected)
    assert made[0].shown[-1] == ("RAM", ((4, 5, 6),))
    service.stop()


def test_stop_closes_the_connection() -> None:
    service, made = _service()
    service.configure(OPENRGB, "h", 1)
    service.stop()
    assert made[0].closed == 1 and service.status.mode is OFF


def test_switching_follower_closes_the_first() -> None:
    """One at a time: OpenRGB, then RAM -- never both on the same sticks."""
    service, made = _service()
    service.configure(OPENRGB, "h", 1)
    service.configure(RAM_MODE, "h", 1)
    assert [m.mode for m in made] == [OPENRGB, RAM_MODE]
    assert made[0].closed == 1 and made[1].closed == 0
    assert service.status.mode is RAM_MODE
    service.stop()


# ── Through the App ─────────────────────────────────────────────────────────

def _app(tmp_path: Path) -> tuple[App, list[FakeMirror]]:
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path),
              make_rgb_mirror=make)
    return app, made


def test_the_command_saves_the_setting_and_starts_following(tmp_path) -> None:
    app, made = _app(tmp_path)
    result = app.dispatch(SetRgbFollow(mode=OPENRGB, port=7000))
    assert result.ok and result.mode is OPENRGB
    assert result.message == "OpenRGB at 127.0.0.1:7000 follows the cooler"
    assert (result.host, result.port) == ("127.0.0.1", 7000)
    assert made[0].address == ("127.0.0.1", 7000)
    assert app.settings.app.rgb_follow == "openrgb"
    assert app.settings.app.openrgb_port == 7000
    ram = app.dispatch(SetRgbFollow(mode=RAM_MODE))
    assert ram.ok and ram.mode is RAM_MODE
    assert ram.message == "Corsair RAM follows the cooler"
    assert made[-1].mode is RAM_MODE and made[0].closed == 1
    off = app.dispatch(SetRgbFollow(mode=OFF))
    assert off.ok and off.mode is OFF and off.port == 7000
    assert off.message == (
        "Nothing follows -- TRCC sends no colours to other lights")
    assert app.dispatch(RgbFollow()).mode is OFF
    app.close()


def test_the_saved_choice_follows_again_when_the_session_starts(tmp_path) -> None:
    app, _ = _app(tmp_path)
    app.dispatch(SetRgbFollow(mode=RAM_MODE))
    app.close()
    again, made = _app(tmp_path)
    again.resume_following()                # start_session's last step
    assert [m.mode for m in made] == [RAM_MODE]
    again.close()


def test_an_app_without_a_session_touches_no_lights(tmp_path) -> None:
    """``trcc kill`` builds an App only to send StopDaemon: built, it took
    the board -- or the RAM's SMBus -- from the running App (2026-10-21).

    MUTATION CHECK: configure the follower in ``App.__init__`` again -- the
    RAM follower is made."""
    app, _ = _app(tmp_path)
    app.dispatch(SetRgbFollow(mode=RAM_MODE))
    app.close()
    again, made = _app(tmp_path)
    assert made == []
    again.close()


def test_an_unknown_saved_choice_is_off(tmp_path) -> None:
    app, made = _app(tmp_path)
    app.settings.app.rgb_follow = "rainbow"
    assert app.settings.rgb_follow_mode() is OFF
    app.close()


def test_a_port_out_of_range_is_refused(tmp_path) -> None:
    app, made = _app(tmp_path)
    result = app.dispatch(SetRgbFollow(mode=OPENRGB, port=70000))
    assert not result.ok
    assert result.message == "port out of range (1-65535): 70000"
    assert made == [] and app.settings.app.rgb_follow == "off"
    app.close()


def test_an_led_render_reaches_the_follower(tmp_path) -> None:
    """The whole chain: RenderLed -> LedColorsChanged -> the follower."""
    app, made = _app(tmp_path)
    assert app.dispatch(ConnectDevice(key="0416:8001")).ok
    app.dispatch(SetRgbFollow(mode=OPENRGB))
    rendered = app.dispatch(RenderLed(key="0416:8001"))
    assert rendered.ok
    _until(lambda: len(made[0].shown) >= 2)
    name, colors = made[0].shown[0]
    assert name == "Strip"
    assert list(colors) == rendered.colors
    app.close()



# ── The bus is the platform's: nothing else can reach the RAM ──────────────

class _PlatformWithRam(MockPlatform):
    """A stand-in fleet that also brings two scripted Corsair sticks."""

    def __init__(self, root: Path) -> None:
        super().__init__([{"vid": "0416", "pid": "8001", "pm": 1}], root)
        self.ram = scripted_ram()

    def smbuses(self) -> tuple[SmBus, ...]:
        return (self.ram,)


def test_the_app_lights_the_ram_its_platform_hands_out(tmp_path) -> None:
    """The App builds the RAM follower from ``platform.smbuses`` -- the one
    way a test or a mock fleet keeps its writes off the host's memory."""
    platform = _PlatformWithRam(tmp_path)
    app = App(platform)
    assert app.dispatch(SetRgbFollow(mode=RAM_MODE)).ok
    app.events.publish(_colors("0416:8001", (255, 0, 0)))
    _until(lambda: all(c.blocks for c in platform.ram.chips.values()))
    red = bytes([10] + [255, 0, 0] * 10)
    assert [c.blocks[0][1][:-1] for c in platform.ram.chips.values()] == [red, red]
    app.close()


def test_a_platform_without_an_smbus_refuses(tmp_path) -> None:
    """The base port refuses; only an OS that has the bus opens it."""
    from .conftest import FakePlatform

    with pytest.raises(OSError, match="no SMBus access on FakePlatform"):
        FakePlatform(tmp_path).smbuses()


def test_linux_opens_every_smbus_or_none(monkeypatch) -> None:
    """A bus that fails to open closes the ones already open, and no bus at
    all names the module the user is missing."""
    from trcc.adapters.system import linux

    opened: list[_Bus] = []

    class _Bus(ScriptedSmBus):
        def __init__(self, number: int) -> None:
            if number == 7:
                raise PermissionError(13, "Permission denied", "/dev/i2c-7")
            super().__init__({}, number)
            opened.append(self)

    monkeypatch.setattr(linux, "LinuxSmBus", _Bus)
    # Never the host's own sensors: a stuck hub on the dev box failed this.
    monkeypatch.setattr(linux, "silent_memory_sensors", lambda: [])
    monkeypatch.setattr(linux, "find_smbus", lambda: (3, 5))
    assert [b.number for b in linux.LinuxOS().smbuses()] == [3, 5]
    monkeypatch.setattr(linux, "find_smbus", lambda: (3, 7))
    with pytest.raises(PermissionError):
        linux.LinuxOS().smbuses()
    assert opened[-1].number == 3 and opened[-1].closed
    monkeypatch.setattr(linux, "find_smbus", lambda: ())
    with pytest.raises(OSError, match="i2c-dev"):
        linux.LinuxOS().smbuses()


# ── Every UI hears of a change; the page's own tests are test_rgb_page_view

def test_a_change_from_any_ui_reaches_the_windows(qtbot) -> None:
    from trcc.core.events import EventBus, RgbFollowChanged
    from trcc.ui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    with qtbot.waitSignal(bridge.app_settings_changed, timeout=1000) as sig:
        bus.publish(RgbFollowChanged(mode=RAM_MODE))
    assert isinstance(sig.args[0], RgbFollowChanged)


def test_the_address_field_parses_like_the_api() -> None:
    from trcc.ui.presentation.openrgb_address import parse_openrgb_address

    assert parse_openrgb_address("127.0.0.1:6742") == ("127.0.0.1", 6742)
    assert parse_openrgb_address(" pc.lan:7000 ") == ("pc.lan", 7000)
    assert parse_openrgb_address("localhost") == ("localhost", 6742)
    for bad in ("http://x", "a b:1", "h:0", "h:70000", "h:x", ":6742"):
        assert parse_openrgb_address(bad) is None, bad


# ── One owner of the RAM: follow and effects share it, one at a time ───────

class _SlowStickBus(ScriptedSmBus):
    """Two scripted sticks; the first effect byte holds the bus for a moment,
    the window a follow write would land in if nothing kept them apart."""

    def __init__(self) -> None:
        super().__init__({0x19: ScriptedCorsairStick(),
                          0x1B: ScriptedCorsairStick()})
        self.effect_started = threading.Event()

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        super().write_byte_data(address, register, value)
        if register == 0x20 and not self.effect_started.is_set():
            self.effect_started.set()
            time.sleep(0.1)


def _no_board() -> FakeMirror:
    raise OSError("no motherboard RGB that TRCC drives was found")


def _no_openrgb(host: str, port: int) -> FakeMirror:
    raise AssertionError("RAM following never asks for OpenRGB")


def _ram_service(bus: ScriptedSmBus) -> RgbMirrorService:
    from trcc.adapters.rgb import make_mirror
    return RgbMirrorService(partial(make_mirror, smbuses=lambda: (bus,),
                                    board=_no_board, openrgb=_no_openrgb))


def test_a_follow_write_never_lands_inside_an_effect() -> None:
    """MUTATION CHECK -- drop ``with self._bus`` around the worker's send and
    the colour block lands between the effect's bytes."""
    bus = _SlowStickBus()
    service = _ram_service(bus)
    service.configure(RAM_MODE, "", 0)
    service.on_colors(_colors("0416:8001", (1, 2, 3)))
    _until(lambda: bool(bus.chips[0x19].blocks))
    effect = threading.Thread(target=service.apply_effect, args=(
        ("i2c-3/0x19",), RamEffectSettings(effect=RamEffect.RAINBOW)))
    effect.start()
    assert bus.effect_started.wait(5)
    service.on_colors(_colors("0416:8001", (4, 5, 6)))
    effect.join(5)
    _until(lambda: len(bus.chips[0x19].blocks) >= 2)
    start = bus.touched.index(("write", 0x19, 0x0B))
    end = bus.touched.index(("read", 0x19, 0x30), start)
    inside = bus.touched[start:end + 1]
    assert inside == [("write", 0x19, 0x0B), ("write", 0x19, 0x21),
                      *[("write", 0x19, 0x20)] * 20, ("read", 0x19, 0x42),
                      ("write", 0x19, 0x82), ("read", 0x19, 0x30)]
    service.close()


def test_two_ui_switching_at_once_leave_one_follower() -> None:
    """Each UI's Command runs on its own thread.  Unlocked, both stopped
    nothing, both started a worker, and the first was orphaned for good.

    MUTATION CHECK -- drop ``with self._switch`` in ``configure``."""
    made: list[FakeMirror] = []

    def slow_make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        time.sleep(0.05)
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    service = RgbMirrorService(slow_make)
    switches = [threading.Thread(target=service.configure,
                                 args=(OPENRGB, "h", n)) for n in (1, 2)]
    for t in switches:
        t.start()
    for t in switches:
        t.join(5)
    workers = [t for t in threading.enumerate() if t.name == "trcc-rgb-mirror"]
    assert len(workers) == 1
    assert len(made) == 2 and made[0].closed == 1 and made[1].closed == 0
    service.stop()
    assert not [t for t in threading.enumerate() if t.name == "trcc-rgb-mirror"]


def test_the_stick_list_never_asks_the_bus() -> None:
    """MUTATION CHECK -- answer ``ram_sticks`` with ``devices()`` and the
    driver made (but not yet scanned) by switching RAM on probes the bus."""
    bus = ScriptedSmBus({0x19: ScriptedCorsairStick()})
    service = _ram_service(bus)
    assert service.ram_sticks() is None
    service.configure(RAM_MODE, "", 0)      # makes the driver, scans nothing
    service.stop()
    assert service.ram_sticks() is None
    assert bus.touched == []
    found = service.scan_ram()
    touched = len(bus.touched)
    assert [s.ref for s in found] == ["i2c-3/0x19"]
    assert service.ram_sticks() == found
    assert len(bus.touched) == touched
    service.close()


def test_an_effect_goes_to_the_named_sticks_only() -> None:
    bus = scripted_ram()
    service = _ram_service(bus)
    rainbow = RamEffectSettings(effect=RamEffect.RAINBOW)
    applied = service.apply_effect(("i2c-3/0x1b",), rainbow)
    assert [s.ref for s in applied] == ["i2c-3/0x1b"]
    assert bus.chips[0x1B].effect and not bus.chips[0x19].effect
    every = service.apply_effect((), rainbow)
    assert [s.ref for s in every] == ["i2c-3/0x19", "i2c-3/0x1b"]
    service.close()


def test_an_unknown_stick_writes_nothing() -> None:
    bus = scripted_ram()
    service = _ram_service(bus)
    with pytest.raises(ValueError, match="no stick i2c-3/0x55"):
        service.apply_effect(("i2c-3/0x19", "i2c-3/0x55"),
                             RamEffectSettings(effect=RamEffect.RAINBOW))
    # The scan's own info-select writes (0x61, 0x21) happen; no effect does.
    assert not [t for t in bus.touched if t[2] in (0x0B, 0x20, 0x82)]
    assert not [c.effect for c in bus.chips.values() if c.effect]
    service.close()


def test_stopping_follow_keeps_the_ram_closing_releases_it() -> None:
    bus = scripted_ram()
    service = _ram_service(bus)
    service.configure(RAM_MODE, "", 0)
    service.on_colors(_colors("0416:8001", (9, 9, 9)))
    _until(lambda: bool(bus.chips[0x19].blocks))
    service.stop()
    assert not bus.closed and service.ram_sticks() is not None
    service.close()
    assert bus.closed and service.ram_sticks() is None


def test_linux_will_not_open_a_bus_whose_spd_hub_is_stuck(monkeypatch) -> None:
    """A stuck SPD hub on the bus is no place to add traffic: the follower and
    the effects are refused, with the cure, until a power-off clears it.

    MUTATION CHECK: drop the stuck-hub refusal from ``LinuxOS.smbuses``."""
    from trcc.adapters.system import linux

    opened: list[int] = []
    monkeypatch.setattr(linux, "LinuxSmBus", lambda n: opened.append(n))
    monkeypatch.setattr(linux, "find_smbus", lambda: (3,))
    monkeypatch.setattr(linux, "silent_memory_sensors", lambda: ["3-0051"])
    with pytest.raises(OSError, match="3-0051 are not answering.*power-off"):
        linux.LinuxOS().smbuses()
    assert opened == []
    # A stuck sensor on ANOTHER bus is not this bus's business.
    monkeypatch.setattr(linux, "silent_memory_sensors", lambda: ["9-0050"])
    assert len(linux.LinuxOS().smbuses()) == 1


# ── RAM lighting: the opt-in grant, through the App ────────────────────────

class _FakeRamAccess(RamAccess):
    """Answers like the Linux adapter, without pkexec or /etc."""

    def __init__(self) -> None:
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.status_ = RamAccessStatus(RamAccessState.OFF, "RAM lighting is off")
        self.cancel = False
        self.calls: list[str] = []

    def status(self):
        return self.status_

    def enable(self):
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.calls.append("enable")
        if self.cancel:
            return RamAccessStatus(RamAccessState.OFF,
                                   "Cancelled -- nothing was changed")
        self.status_ = RamAccessStatus(RamAccessState.ON, "RAM lighting is on")
        return self.status_

    def disable(self):
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.calls.append("disable")
        self.status_ = RamAccessStatus(RamAccessState.OFF, "RAM lighting is off")
        return self.status_


def _ram_app(tmp_path: Path) -> tuple[App, _FakeRamAccess, list]:
    from trcc.core.events import RamLightingChanged
    access = _FakeRamAccess()
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.ram_access = lambda: access
    app = App(platform)
    seen: list = []
    app.events.subscribe(RamLightingChanged, seen.append)
    return app, access, seen


def test_switching_ram_lighting_tells_every_ui(tmp_path) -> None:
    from trcc.core.commands import RamLighting, SetRamLighting
    from trcc.core.models import RamAccessState

    app, access, seen = _ram_app(tmp_path)
    assert app.dispatch(RamLighting()).state is RamAccessState.OFF
    on = app.dispatch(SetRamLighting(enabled=True))
    assert (on.ok, on.state, on.message) == (True, RamAccessState.ON,
                                             "RAM lighting is on")
    off = app.dispatch(SetRamLighting(enabled=False))
    assert (off.ok, off.state) == (True, RamAccessState.OFF)
    assert [e.state for e in seen] == [RamAccessState.ON, RamAccessState.OFF]
    assert access.calls == ["enable", "disable"]
    app.close()


def test_a_closed_password_prompt_is_not_ok(tmp_path) -> None:
    from trcc.core.commands import SetRamLighting
    from trcc.core.models import RamAccessState

    app, access, _seen = _ram_app(tmp_path)
    access.cancel = True
    result = app.dispatch(SetRamLighting(enabled=True))
    assert (result.ok, result.state, result.message) == (
        False, RamAccessState.OFF, "Cancelled -- nothing was changed")
    app.close()


def test_a_client_waits_for_the_password_as_long_as_the_app_does(monkeypatch) -> None:
    """The answer comes when the password is typed or the prompt closed --
    never a fixed 30 s that reports a failure that has not happened.

    MUTATION CHECK: drop ``WAITS_ON_USER`` from ``SetRamLighting``."""
    from trcc import ipc
    from trcc.core.commands import RamLighting, SetRamLighting
    from trcc.proxy import AppProxy

    waited: list[object] = []

    def answer(envelope, *, timeout):
        waited.append(timeout)
        return {"type": "RamLightingResult", "ok": True}

    monkeypatch.setattr(ipc, "one_shot_request", answer)
    proxy = AppProxy(timeout=30.0)
    proxy.dispatch(SetRamLighting(enabled=True))
    proxy.dispatch(RamLighting())
    assert waited == [None, 30.0]



# ── Following an LCD's picture, on the lights the user picked ──────────────

RED, BLUE = (255, 0, 0), (0, 0, 255)      # 0 and 255 pass the LED gamma as-is


def _pixels(left: tuple[int, int, int] = RED,
            right: tuple[int, int, int] = BLUE, width: int = 40,
            height: int = 20):
    """A ``raw_argb32`` stand-in: *left* half, *right* half, whatever the
    surface -- in the host's native ARGB32 order, as Qt lays it out."""
    import sys

    def word(rgb: tuple[int, int, int]) -> bytes:
        r, g, b = rgb
        return (0xFF000000 | r << 16 | g << 8 | b).to_bytes(4, sys.byteorder)

    line = word(left) * (width // 2) + word(right) * (width // 2)
    return lambda surface: (line * height, width, height, width * 4)


def _following(source: str = "0402:3922", **kw) -> tuple[RgbMirrorService, FakeMirror]:
    service, made = _service()
    service.configure(RAM_MODE, "", 0, source, **kw)
    mirror = made[-1] if made else service._ram
    assert isinstance(mirror, FakeMirror)
    return service, mirror


def test_halves_give_each_light_its_own_edge_of_the_picture() -> None:
    from trcc.core.models import FollowMapping
    service, mirror = _following(mapping=FollowMapping.HALVES)
    service.on_frame("0402:3922", "surface", _pixels())
    _until(lambda: len(mirror.shown) >= 2)
    assert mirror.shown[:2] == [("Strip", (RED,) * 10), ("RAM", (BLUE,) * 10)]
    service.stop()


def test_single_gives_every_light_the_one_colour() -> None:
    from trcc.core.models import FollowMapping
    service, mirror = _following(mapping=FollowMapping.SINGLE)
    service.on_frame("0402:3922", "surface", _pixels())
    _until(lambda: len(mirror.shown) >= 2)
    (_a, first), (_b, second) = mirror.shown[:2]
    assert first == second
    service.stop()


def test_a_pictures_colours_reach_the_leds_ungammad_and_are_reported_as_seen(
        ) -> None:
    """(152, 18, 23) on screen is (82, 1, 1) of LED light -- the pair seen on
    the maintainer's sticks.  The report carries the screen colour, so a
    window's preview looks like the lights."""
    sent: list = []
    service, made = _service(on_sent=lambda source, columns: sent.append(
        (source, columns)))
    service.configure(RAM_MODE, "", 0, "0402:3922")
    mirror = made[-1] if made else service._ram
    assert isinstance(mirror, FakeMirror)
    dark_red = (152, 18, 23)
    service.on_frame("0402:3922", "surface", _pixels(dark_red, dark_red))
    _until(lambda: bool(sent))
    assert mirror.shown[0][1] == ((82, 1, 1),) * 10
    assert sent[0] == ("0402:3922", ((dark_red,) * 10, (dark_red,) * 10))
    service.stop()


def test_a_coolers_colours_are_sent_as_they_are() -> None:
    service, made = _service()
    service.configure(RAM_MODE, "", 0)
    mirror = made[-1] if made else service._ram
    assert isinstance(mirror, FakeMirror)
    service.on_colors(_colors("0416:8001", (152, 18, 23)))
    _until(lambda: bool(mirror.shown))
    assert mirror.shown[0][1] == ((152, 18, 23),)
    service.stop()


def test_another_devices_frames_and_an_led_coolers_colours_are_ignored() -> None:
    service, mirror = _following()
    service.on_frame("87ad:70db", "surface", _pixels())
    service.on_colors(_colors("0416:8001", (9, 9, 9)))
    time.sleep(0.1)
    assert mirror.shown == []
    service.stop()


def test_a_fast_video_is_sampled_at_most_twenty_times_a_second() -> None:
    """MUTATION CHECK: drop the FRAME_INTERVAL_S check in ``on_frame``."""
    service, _mirror = _following()
    read: list[object] = []
    pixels = _pixels()

    def counting(surface):
        read.append(surface)
        return pixels(surface)

    for _ in range(10):                      # ten frames inside 50 ms
        service.on_frame("0402:3922", "surface", counting)
    assert len(read) == 1
    service.stop()


def test_only_the_picked_lights_follow() -> None:
    """MUTATION CHECK: drop the targets filter in ``_send``."""
    service, mirror = _following(targets=("ram",))
    service.on_frame("0402:3922", "surface", _pixels())
    _until(lambda: bool(mirror.shown))
    time.sleep(0.05)
    assert [name for name, _c in mirror.shown] == ["RAM"]
    service.stop()


def test_the_choice_is_kept_and_a_damaged_targets_setting_means_all(tmp_path) -> None:
    from trcc.core.models import FollowMapping

    app, made = _app(tmp_path)
    result = app.dispatch(SetRgbFollow(mode=OPENRGB, source="0402:3922",
                                       mapping=FollowMapping.SINGLE,
                                       targets=("Board",)))
    assert (result.source, result.mapping, result.targets) == (
        "0402:3922", FollowMapping.SINGLE, ("Board",))
    kept = app.dispatch(SetRgbFollow(mode=OFF))         # None keeps each
    assert (kept.source, kept.targets) == ("0402:3922", ("Board",))
    assert not app.dispatch(SetRgbFollow(mode=OFF, source="nope")).ok
    # a hand-edited string: the wrong type on purpose, so cast past the checker
    app.settings.app.rgb_follow_targets = cast(list[str], "Board")
    assert app.settings.rgb_follow_targets() == ()
    app.close()


def test_the_colours_choice_is_kept_reaches_the_follower_and_damage_is_vivid(
        tmp_path) -> None:
    """MUTATION CHECK: drop ``colors`` from ``configure`` in SetRgbFollow."""
    from trcc.core.models import FollowColors

    app, _made = _app(tmp_path)
    assert app.settings.rgb_follow_colors() is FollowColors.VIVID   # default
    result = app.dispatch(SetRgbFollow(mode=OPENRGB, source="0402:3922",
                                       colors=FollowColors.SMOOTH))
    assert result.colors is FollowColors.SMOOTH
    assert app.rgb_mirror._how is FollowColors.SMOOTH
    kept = app.dispatch(SetRgbFollow(mode=OPENRGB))         # None keeps it
    assert kept.colors is FollowColors.SMOOTH
    app.settings.app.rgb_follow_colors = "garish"           # hand-edited
    assert app.settings.rgb_follow_colors() is FollowColors.VIVID
    app.close()


def test_the_app_follows_an_lcd_with_the_real_renderer(tmp_path) -> None:
    """FrameSent -> App -> the renderer's sampler -> one column per light."""
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.events import FrameSent

    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path),
              renderer=QtRenderer(), make_rgb_mirror=make)
    assert app.dispatch(SetRgbFollow(mode=OPENRGB, source="0402:3922")).ok
    frame = QImage(320, 320, QImage.Format.Format_ARGB32)
    frame.fill(QColor(0, 0, 255))
    left = QImage(160, 320, QImage.Format.Format_ARGB32)
    left.fill(QColor(255, 0, 0))
    from PySide6.QtGui import QPainter
    painter = QPainter(frame)
    painter.drawImage(0, 0, left)
    painter.end()
    app.events.publish(FrameSent(key="0402:3922", bytes_sent=1, surface=frame))
    _until(lambda: len(made[-1].shown) >= 2)
    strip, ram = made[-1].shown[:2]
    assert strip[1][0][0] > 200 and strip[1][0][2] < 60     # left half: red
    assert ram[1][0][2] > 200 and ram[1][0][0] < 60         # right half: blue
    app.close()


def test_the_app_follows_the_picture_under_the_overlay_and_says_what_it_sent(
        tmp_path, monkeypatch) -> None:
    """The App samples ``background_surface`` -- the frame without its text --
    and publishes the colours sent, for every window's preview."""
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.events import FrameSent, RgbFollowSent

    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    def filled(rgb: tuple[int, int, int]) -> QImage:
        image = QImage(320, 320, QImage.Format.Format_ARGB32)
        image.fill(QColor(*rgb))
        return image

    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path),
              renderer=QtRenderer(), make_rgb_mirror=make)
    under = filled(BLUE)
    monkeypatch.setattr(app.display, "background_surface",
                        lambda key: under if key == "0402:3922" else None)
    reported: list[RgbFollowSent] = []
    app.events.subscribe(RgbFollowSent, reported.append)
    assert app.dispatch(SetRgbFollow(mode=OPENRGB, source="0402:3922")).ok
    app.events.publish(FrameSent(key="0402:3922", bytes_sent=1,
                                 surface=filled((255, 255, 255))))  # "text"
    _until(lambda: bool(reported))
    assert {colors for _name, colors in made[-1].shown[:2]} == {(BLUE,) * 10}
    assert reported[0] == RgbFollowSent(source="0402:3922",
                                        columns=((BLUE,) * 10, (BLUE,) * 10))
    app.close()


# ── The RGB page's Commands: lights, Find, effects ─────────────────────────

def _lights_app(tmp_path: Path) -> tuple[App, _PlatformWithRam, list]:
    from trcc.core.events import RgbFollowChanged, RgbLightsChanged
    platform = _PlatformWithRam(tmp_path)
    app = App(platform)
    seen: list = []
    app.events.subscribe(RgbLightsChanged, seen.append)
    app.events.subscribe(RgbFollowChanged, seen.append)
    return app, platform, seen


def _not_board(lights):
    """RAM and OpenRGB lights only -- the stand-in fleet's board is listed
    beside them, and has tests of its own."""
    return [light for light in lights
            if light.kind.value not in ("board", "found")]


def test_the_lights_are_read_without_the_bus_until_find(tmp_path) -> None:
    from trcc.core.commands import RgbLights, ScanRgbLights

    app, platform, seen = _lights_app(tmp_path)
    before = app.dispatch(RgbLights())
    assert (before.scanned, before.lights) == (False, ())
    assert platform.ram.touched == []
    found = app.dispatch(ScanRgbLights())
    assert found.ok and found.scanned
    assert [(light.kind.value, light.ref) for light in _not_board(found.lights)
            ] == [("ram", "i2c-3/0x19"), ("ram", "i2c-3/0x1b")]
    assert found.message.startswith("Found 2 RGB memory stick(s); OpenRGB: ")
    assert found.openrgb_error            # no OpenRGB in a test: said, not hidden
    assert [type(e).__name__ for e in seen] == ["RgbLightsChanged"]
    touched = len(platform.ram.touched)
    assert app.dispatch(RgbLights()).lights == found.lights
    assert len(platform.ram.touched) == touched
    app.close()


def test_a_find_is_listed_again_after_a_restart_without_the_bus(tmp_path) -> None:
    """The sticks keep their own effect through a reboot; the page must not
    forget them.  Read back through the FILE, by a second App.

    MUTATION CHECK: drop ``set_found_lights`` from ScanRgbLights."""
    from trcc.core.commands import RgbLights, ScanRgbLights

    app, _platform, _seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    app.close()
    again, platform, _seen = _lights_app(tmp_path)
    listed = again.dispatch(RgbLights())
    assert [(light.ref, light.led_count, light.remembered)
            for light in _not_board(listed.lights)] == [("i2c-3/0x19", 10, True),
                                            ("i2c-3/0x1b", 10, True)]
    assert listed.scanned is False
    assert platform.ram.touched == []
    assert all(not light.remembered
               for light in again.dispatch(ScanRgbLights()).lights)
    again.close()


def test_a_find_replaces_its_own_kind_and_a_failed_one_keeps_it(tmp_path) -> None:
    """A stick taken out is forgotten; a bus that cannot be read forgets
    nothing; RAM's Find leaves OpenRGB's record alone."""
    from trcc.core.commands import ScanRgbLights
    from trcc.core.models import LightKind, RgbMirrorDevice

    app, platform, _seen = _lights_app(tmp_path)
    fan = RgbMirrorDevice(0, "Fan hub", 8, "Fan hub")
    app.settings.set_found_lights(LightKind.OPENRGB, (fan,))
    app.dispatch(ScanRgbLights())
    del platform.ram.chips[0x1B]
    app.dispatch(ScanRgbLights())
    assert [s.ref for s in app.settings.found_lights(LightKind.RAM)] == [
        "i2c-3/0x19"]
    assert app.settings.found_lights(LightKind.OPENRGB) == (fan,)

    def stuck() -> tuple[SmBus, ...]:
        raise OSError("the memory's SPD hub(s) 3-0051 are not answering")

    app.close()
    platform = _PlatformWithRam(tmp_path)
    platform.smbuses = stuck
    again = App(platform)               # the App takes the bus from here
    failed = again.dispatch(ScanRgbLights())
    assert not failed.ok
    assert [(light.ref, light.remembered) for light in _not_board(failed.lights)
            ] == [("i2c-3/0x19", True), ("Fan hub", True)]
    assert [s.ref for s in again.settings.found_lights(LightKind.RAM)] == [
        "i2c-3/0x19"]
    again.close()


def test_a_damaged_found_light_is_dropped_not_guessed(tmp_path) -> None:
    from trcc.core.models import LightKind

    app, _platform, _seen = _lights_app(tmp_path)
    app.settings.app.rgb_lights = [
        {"kind": "ram", "ref": "i2c-3/0x19", "name": "A", "led_count": 10},
        {"kind": "ram", "ref": "", "name": "no ref", "led_count": 10},
        {"kind": "ram", "ref": "i2c-3/0x1a", "name": "B", "led_count": "10"},
        {"kind": "ram", "ref": "i2c-3/0x1b", "name": "C", "led_count": -1},
        {"kind": "board", "ref": "aura/argb2", "name": "unset", "led_count": 0},
        {"ref": "i2c-3/0x1c", "name": "no kind", "led_count": 10},
        cast(dict[str, Any], "not even a dict")]  # damaged on purpose
    assert [(s.index, s.ref) for s in app.settings.found_lights(LightKind.RAM)
            ] == [(0, "i2c-3/0x19")]
    app.settings.app.rgb_lights = cast(list[dict[str, Any]], "not a list")  # damaged on purpose
    assert app.settings.found_lights(LightKind.RAM) == ()
    app.close()


def test_an_effect_is_saved_on_the_sticks_and_remembered(tmp_path) -> None:
    from trcc.core.commands import RgbLights, ScanRgbLights, SetRamEffect
    from trcc.core.models import EffectSpeed, RamEffect

    app, platform, _seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    result = app.dispatch(SetRamEffect(effect=RamEffect.RAINBOW_WAVE,
                                       refs=("i2c-3/0x1b",),
                                       speed=EffectSpeed.FAST))
    assert result.ok and result.message == "rainbow-wave saved on 1 stick(s)"
    assert platform.ram.chips[0x1B].effect[:2] == bytes([0x03, 0x02])
    assert platform.ram.chips[0x19].effect == b""
    lights = {light.ref: light.effect for light in app.dispatch(RgbLights()).lights}
    assert lights["i2c-3/0x19"] is None
    saved = lights["i2c-3/0x1b"]
    assert isinstance(saved, RamEffectSettings)      # a stick's, not a linked colour
    assert saved.speed is EffectSpeed.FAST
    app.close()


def test_an_effect_the_stick_cannot_take_changes_nothing(tmp_path) -> None:
    """Refused BEFORE following stops: a bad request turns nothing off.

    MUTATION CHECK: check the effect after stopping the follow."""
    from trcc.core.commands import ScanRgbLights, SetRamEffect
    from trcc.core.models import EffectDirection, RamEffect

    app, platform, seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    app.dispatch(SetRgbFollow(mode=RAM_MODE))
    seen.clear()
    result = app.dispatch(SetRamEffect(
        effect=RamEffect.RAIN, direction=EffectDirection.LEFT,
        colors=((1, 1, 1), (2, 2, 2))))
    assert (result.ok, result.message) == (False, "rain cannot move left")
    assert app.settings.rgb_follow_mode() is RAM_MODE
    assert seen == []
    assert not any(c.effect for c in platform.ram.chips.values())
    app.close()


def test_an_effect_stops_ram_following_first_and_says_so(tmp_path) -> None:
    from trcc.core.commands import ScanRgbLights, SetRamEffect
    from trcc.core.models import RamEffect

    app, _platform, seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    app.dispatch(SetRgbFollow(mode=RAM_MODE, source="0402:3922"))
    seen.clear()
    result = app.dispatch(SetRamEffect(effect=RamEffect.RAINBOW))
    assert result.message == ("rainbow saved on 2 stick(s) -- RAM following "
                              "turned off")
    assert app.settings.rgb_follow_mode() is OFF
    assert app.settings.app.rgb_follow_source == "0402:3922"   # kept for later
    assert [type(e).__name__ for e in seen] == ["RgbFollowChanged",
                                                "RgbLightsChanged"]
    app.close()


def test_a_bus_that_cannot_be_used_is_said_not_hidden(tmp_path) -> None:
    """No access, a stuck SPD hub: the scan says why, and nothing is listed."""
    from trcc.core.commands import ScanRgbLights

    platform = _PlatformWithRam(tmp_path)

    def stuck() -> tuple[SmBus, ...]:
        raise OSError("the memory's SPD hub(s) 3-0051 are not answering")

    platform.smbuses = stuck
    app = App(platform)                 # the App takes the bus from here
    result = app.dispatch(ScanRgbLights())
    assert not result.ok
    assert result.message.startswith("RAM: the memory's SPD hub(s) 3-0051")
    assert [light for light in result.lights if light.kind.value == "ram"] == []
    app.close()


def test_a_damaged_saved_effect_is_unknown_not_a_guess(tmp_path) -> None:
    app, _platform, _seen = _lights_app(tmp_path)
    app.settings.app.ram_effects = {
        "i2c-3/0x19": {"effect": "disco"},
        "i2c-3/0x1b": cast(dict[str, Any], "not even a dict")}  # damaged on purpose
    assert app.settings.light_effect("i2c-3/0x19") is None
    assert app.settings.light_effect("i2c-3/0x1b") is None
    app.close()


def test_find_at_a_new_address_saves_it_and_moves_the_follower(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page's address box reaches the App through Find, so the setting,
    the scan and an OpenRGB follower never point at different servers."""
    from trcc.core.commands import ScanRgbLights
    from trcc.core.models import RgbFollowMode

    app, _platform, _seen = _lights_app(tmp_path)
    moved: list = []
    real = app.rgb_mirror.configure
    monkeypatch.setattr(app.rgb_mirror, "configure", lambda *a, **k: (
        moved.append(a[:3]), real(*a, **k)))
    app.dispatch(ScanRgbLights(host="10.0.0.5", port=6800))
    assert (app.settings.app.openrgb_host, app.settings.app.openrgb_port) == (
        "10.0.0.5", 6800)
    assert moved == []                    # not following OpenRGB: nothing moves
    app.settings.set_rgb_follow(RgbFollowMode.OPENRGB, "10.0.0.5", 6800, "",
                                app.settings.rgb_follow_mapping(), (),
                                colors=app.settings.rgb_follow_colors())
    app.dispatch(ScanRgbLights(host="10.0.0.6"))
    assert moved == [(RgbFollowMode.OPENRGB, "10.0.0.6", 6800)]
    app.dispatch(ScanRgbLights())         # empty keeps the saved address
    assert app.settings.app.openrgb_host == "10.0.0.6"
    refused = app.dispatch(ScanRgbLights(port=70000))
    assert not refused.ok
    assert refused.message == "port out of range (1-65535): 70000"
    assert app.settings.app.openrgb_port == 6800
    app.close()


# ── Blink: which header is which ─────────────────────────────────────

DIM, OFF_RGB = BLINK_COLOR, (0, 0, 0)
BLINKED = [("Strip", (DIM,)), ("Strip", (OFF_RGB,))] * (BLINK_STEPS // 2)


def test_a_blink_flashes_the_light_then_hands_the_board_back() -> None:
    """Not following: a driver opened for the blink, released and closed.

    MUTATION CHECK: drop the ``_release`` call in ``blink``."""
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    service = RgbMirrorService(make, blink_step_s=0)
    assert service.blink("strip") == STRIP
    assert [m.mode for m in made] == [BOARD]
    assert made[0].shown == BLINKED
    assert made[0].steps == ["release", "close"]


def test_blinking_a_light_the_board_lacks_is_refused_and_closes() -> None:
    made: list[FakeMirror] = []
    service = RgbMirrorService(lambda *a: made.append(FakeMirror(*a))
                               or made[-1], blink_step_s=0)
    with pytest.raises(ValueError, match="no light 'aura/argb9'"):
        service.blink("aura/argb9")
    assert made[0].shown == [] and made[0].steps == ["close"]


def test_a_blink_while_following_borrows_the_followers_driver() -> None:
    """One driver, kept open and not released: the follower paints next.

    MUTATION CHECK: always make a fresh driver in ``blink``."""
    made: list[FakeMirror] = []
    service = RgbMirrorService(lambda *a: made.append(FakeMirror(*a))
                               or made[-1], blink_step_s=0)
    service.configure(BOARD, "", 0)
    service.blink("strip")
    assert len(made) == 1 and made[0].shown == BLINKED
    assert made[0].steps == [] and service.status.mode is BOARD
    service.stop()


def test_the_app_blinks_a_header_whose_leds_are_not_set(tmp_path: Path) -> None:
    """The maintainer's board, scripted: header 2 has no count, so the most
    a header drives (120) blink dim white then off, through effect channel 1,
    which goes back to its Rainbow -- unsaved, and nothing left open.

    MUTATION CHECK: match the Aura channel by device again, not by ref."""
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.rgb = ScriptedRgbHardware()
    app = App(platform)
    app.rgb_mirror._blink_step_s = 0
    result = app.dispatch(BlinkAuraHeader(header=2))
    assert (result.ok, result.message) == (
        True, "ASUS PRIME Z790-V AX -- ARGB header 2 blinked")
    direct = platform.aura.reports(0x40)
    per_show = MAX_ARGB_LEDS // 20
    assert len(direct) == BLINK_STEPS * per_show
    assert {bytes(p[1:2]) for p in direct} == {b"\x01", b"\x81"}  # header 2
    assert [p[4:7] for p in direct[::per_show]] == [
        bytes(DIM), bytes(OFF_RGB)] * (BLINK_STEPS // 2)
    assert platform.aura.reports(0x35) == [effect_packet(1, 0xFF),
                                           effect_packet(1, 0x05)]
    assert platform.aura.reports(0x3F) == []
    assert not platform.aura.is_open
    refused = app.dispatch(BlinkAuraHeader(header=9))
    assert (refused.ok, refused.message) == (
        False, "the motherboard has no ARGB header 9")
    app.close()


# ── OpenRGB is reached through the platform ──────────────────────────

def test_a_stand_in_platform_reaches_no_openrgb(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch
                                                ) -> None:
    """Find and following on a test platform say OpenRGB is not there.
    They used to open 127.0.0.1:6742 and list whatever real OpenRGB the
    machine ran.  Every connect is recorded and refused, so even a broken
    build reaches nothing.

    MUTATION CHECK: build ``OpenRgbMirror`` in ``make_mirror`` again."""
    import socket
    opened: list[object] = []

    def refuse(_sock: object, address: object) -> None:
        opened.append(address)
        raise ConnectionRefusedError("refused by the test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    app, platform, _ = _lights_app(tmp_path)
    found = app.dispatch(ScanRgbLights())
    assert found.openrgb_error == (
        f"OSError: no OpenRGB client on {type(platform).__name__}")
    app.dispatch(SetRgbFollow(mode=OPENRGB, host="127.0.0.1", port=6742))
    assert app.dispatch(RgbFollow()).error == (
        f"no OpenRGB client on {type(platform).__name__}")
    assert opened == []
    app.close()


def test_every_os_platform_names_the_real_openrgb_client() -> None:
    from trcc.adapters.rgb.openrgb import OpenRgbMirror
    from trcc.adapters.system import host_platform_class
    from trcc.core.ports import Platform

    assert Platform.OPENRGB is None
    assert host_platform_class().OPENRGB is OpenRgbMirror


# ── TRCC's own effect as the source ─────────────────────────────────────

def _effect(effect: RamEffect, *colors: tuple[int, int, int]) -> RamEffectSettings:
    return RamEffectSettings(effect=effect, colors=colors)


def _running(effects: dict, targets: tuple[str, ...] = ()) -> tuple[RgbMirrorService, FakeMirror]:
    from trcc.core.models import EFFECT_SOURCE
    service, made = _service()
    service.configure(BOARD, "", 0, EFFECT_SOURCE, targets=targets,
                      effects=effects)
    return service, made[-1]


def test_each_light_runs_its_own_effect() -> None:
    """A header's fan can be one colour and the next header's another.

    MUTATION CHECK: give every light the first light's effect -- RAM shows red."""
    service, mirror = _running({"strip": _effect(RamEffect.STATIC, RED),
                                "ram": _effect(RamEffect.STATIC, BLUE)})
    _until(lambda: len(mirror.shown) >= 2)
    service.stop()
    assert mirror.shown[:2] == [("Strip", (RED,) * 10), ("RAM", (BLUE,) * 10)]


def test_an_effect_reaches_only_its_targets() -> None:
    service, mirror = _running({"strip": _effect(RamEffect.STATIC, RED)},
                               targets=("strip",))
    _until(lambda: bool(mirror.shown))
    service.stop()
    assert {name for name, _ in mirror.shown} == {"Strip"}


def test_a_moving_effect_is_drawn_frame_after_frame() -> None:
    """Rainbow changes as it runs; a Static colour is sent once a second.

    MUTATION CHECK: wait for a pending frame as a follower does -- nothing
    is ever sent, since nothing arrives."""
    service, mirror = _running({"strip": _effect(RamEffect.RAINBOW)},
                               targets=("strip",))
    _until(lambda: len(mirror.shown) >= 4)
    service.stop()
    assert len({colors for _, colors in mirror.shown[:4]}) > 1


def test_a_cooler_does_not_paint_over_the_effect() -> None:
    service, mirror = _running({"strip": _effect(RamEffect.STATIC, RED)},
                               targets=("strip",))
    service.on_colors(_colors("0416:8001", BLUE))
    _until(lambda: len(mirror.shown) >= 2)
    service.stop()
    assert all(colors == (RED,) * 10 for _, colors in mirror.shown)


def test_effects_without_the_effect_source_are_ignored() -> None:
    """Following a cooler with effects passed by mistake follows the cooler."""
    service, made = _service()
    service.configure(BOARD, "", 0, "", effects={"strip": _effect(RamEffect.STATIC, RED)})
    mirror = made[-1]
    service.on_colors(_colors("0416:8001", BLUE))
    _until(lambda: bool(mirror.shown))
    service.stop()
    assert mirror.shown[0] == ("Strip", (BLUE,))


# ── SetAuraEffect: the board's lights run TRCC's built-in effects ────────

def _board_app(tmp_path: Path) -> tuple[App, MockPlatform]:
    """The maintainer's board, scripted, Found, header 1 at 8 LEDs."""
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.rgb = ScriptedRgbHardware()
    app = App(platform)
    app.dispatch(ScanRgbLights())
    assert app.dispatch(SetAuraLeds(header=1, leds=8)).ok
    return app, platform


def _header_shows(platform: MockPlatform, header: int, rgb: tuple[int, int, int],
                  leds: int) -> bool:
    """Whether a direct packet painted *header*'s *leds* LEDs *rgb*."""
    want = bytes([0x40, 0x80 | (header - 1), 0, leds]) + bytes(rgb * leds)
    return any(p[:4 + 3 * leds] == want for p in platform.aura.reports(0x40))


def test_a_static_colour_lights_the_header_and_is_kept(tmp_path: Path) -> None:
    """The maintainer's exhaust fan: header 1 turns red, nothing is saved on
    the board (no 0x3F), and every UI lists the header's effect.

    MUTATION CHECK: drop the effects from _refollow_board -- the header
    stays dark."""
    from trcc.core.commands import RgbLights, SetAuraEffect
    from trcc.core.models import EFFECT_SOURCE
    app, platform = _board_app(tmp_path)
    result = app.dispatch(SetAuraEffect(effect=RamEffect.STATIC,
                                        refs=("aura/argb1",), colors=(RED,)))
    assert (result.ok, result.message) == (True, "static on 1 motherboard light(s)")
    _until(lambda: _header_shows(platform, 1, RED, 8))
    assert platform.aura.reports(0x3F) == []
    assert app.settings.app.rgb_follow_source == EFFECT_SOURCE
    header = next(light for light in app.dispatch(RgbLights()).lights
                  if light.ref == "aura/argb1")
    assert header.effect == RamEffectSettings(effect=RamEffect.STATIC, colors=(RED,))
    app.close()


def test_the_effect_runs_again_when_the_session_starts(tmp_path: Path) -> None:
    from trcc.core.commands import SetAuraEffect
    app, platform = _board_app(tmp_path)
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb1",),
                               colors=(BLUE,)))
    app.close()
    platform.aura.writes.clear()
    again = App(platform)
    again.resume_following()                # start_session's last step
    _until(lambda: _header_shows(platform, 1, BLUE, 8))
    again.close()


def test_each_header_keeps_its_own_effect(tmp_path: Path) -> None:
    """Header 2 turning blue leaves header 1 red.

    MUTATION CHECK: target only the lights named now -- header 1 goes dark."""
    from trcc.core.commands import SetAuraEffect
    app, platform = _board_app(tmp_path)
    assert app.dispatch(SetAuraLeds(header=2, leds=4)).ok
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb1",),
                               colors=(RED,)))
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb2",),
                               colors=(BLUE,)))
    platform.aura.writes.clear()
    _until(lambda: _header_shows(platform, 1, RED, 8)
           and _header_shows(platform, 2, BLUE, 4))
    app.close()


def test_a_new_led_count_applies_while_the_effect_runs(tmp_path: Path) -> None:
    """The count the maintainer had was 4, left over from a test -- correcting
    it must reach the running header without a restart."""
    from trcc.core.commands import SetAuraEffect
    app, platform = _board_app(tmp_path)
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb1",),
                               colors=(RED,)))
    _until(lambda: _header_shows(platform, 1, RED, 8))
    assert app.dispatch(SetAuraLeds(header=1, leds=12)).ok
    _until(lambda: _header_shows(platform, 1, RED, 12))
    app.close()


def test_stopping_hands_the_header_back_to_the_boards_effect(tmp_path: Path) -> None:
    from trcc.core.commands import SetAuraEffect
    app, platform = _board_app(tmp_path)
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb1",),
                               colors=(RED,)))
    _until(lambda: _header_shows(platform, 1, RED, 8))
    app.dispatch(SetRgbFollow(mode=OFF))
    assert platform.aura.reports(0x35)[-1] == effect_packet(1, 0x05)
    app.close()


@pytest.mark.parametrize(("refs", "kw", "message"), [
    (("aura/argb2",), {"colors": (RED,)},
     "give ASUS PRIME Z790-V AX -- ARGB header 2 its number of LEDs first"),
    (("aura/argb9",), {"colors": (RED,)},
     "no motherboard light aura/argb9 -- found aura/argb1, aura/argb2, "
     "aura/argb3, aura/onboard"),
    (("aura/argb1",), {"colors": (RED,), "brightness": 300},
     "brightness is 0-255, not 300"),
])
def test_what_cannot_run_is_refused_before_anything_changes(
        tmp_path: Path, refs, kw, message) -> None:
    from trcc.core.commands import SetAuraEffect
    app, platform = _board_app(tmp_path)
    before = (app.settings.rgb_follow_mode(), list(platform.aura.writes))
    result = app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=refs, **kw))
    assert (result.ok, result.message) == (False, message)
    assert (app.settings.rgb_follow_mode(), list(platform.aura.writes)) == before
    app.close()


# ── SetLinkedColor: a light coloured by a reading ───────────────────────

def test_a_header_follows_the_cpu_temperature(tmp_path: Path) -> None:
    """80 °C on the lights' scale is orange (heavy); a missing reading keeps
    the last colour instead of going dark.

    MUTATION CHECK: read the GPU for a CPU link -- the header stays dark."""
    from trcc.core.commands import SetLinkedColor
    app, platform = _board_app(tmp_path)
    app.last_raw_readings = {"cpu:temp": 80.0, "gpu:primary:temp": 40.0}
    result = app.dispatch(SetLinkedColor(metric="temp", refs=("aura/argb1",)))
    assert (result.ok, result.message) == (
        True, "temp-linked (cpu) on 1 motherboard light(s)")
    _until(lambda: _header_shows(platform, 1, (255, 110, 0), 8))
    app.last_raw_readings = {}                    # the reading goes missing
    platform.aura.writes.clear()
    time.sleep(1.5)                               # more than one tick
    assert platform.aura.reports(0x40) == []      # held: nothing new to write
    app.close()


def test_a_linked_colour_is_kept_and_listed(tmp_path: Path) -> None:
    from trcc.core.commands import RgbLights, SetLinkedColor
    from trcc.core.models import LinkedEffect
    app, _platform = _board_app(tmp_path)
    app.dispatch(SetLinkedColor(metric="load", refs=("aura/argb1",), source="gpu"))
    header = next(light for light in app.dispatch(RgbLights()).lights
                  if light.ref == "aura/argb1")
    assert header.effect == LinkedEffect(metric="load", source="gpu")
    assert app.settings.light_effect("aura/argb1") == LinkedEffect("load", "gpu")
    app.close()


# ── A board TRCC drives is hidden from a fresh look ─────────────────────

class _HidingRgbHardware(ScriptedRgbHardware):
    """As Linux shows the board: while TRCC holds the Aura controller
    through libusb, its hidraw node -- what Find looks for -- is gone."""

    def __init__(self, platform: MockPlatform) -> None:
        super().__init__()
        self._platform = platform

    def hid_interfaces(self):
        return () if self._platform.aura.is_open else self.interfaces


def test_a_new_led_count_keeps_the_boards_lights_listed(tmp_path: Path) -> None:
    """The maintainer set header 1 to 8 LEDs while it ran an effect: the
    follower restarted and held the board, the re-read saw nothing, and
    "no board lights" was saved -- his Motherboard panel was empty after the
    next restart, header 1 could not be ticked, Load-linked never applied
    (2026-10-21).

    MUTATION CHECK: scan without the held lights -- the board list empties."""
    from trcc.core.commands import SetAuraEffect
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.rgb = _HidingRgbHardware(platform)
    app = App(platform)
    app.dispatch(ScanRgbLights())
    assert app.dispatch(SetAuraLeds(header=1, leds=4)).ok
    app.dispatch(SetAuraEffect(effect=RamEffect.STATIC, refs=("aura/argb1",),
                               colors=(RED,)))
    listed = ["aura/onboard", "aura/argb1", "aura/argb2", "aura/argb3"]
    for change in (lambda: app.dispatch(SetAuraLeds(header=1, leds=8)),
                   lambda: app.dispatch(ScanRgbLights())):
        _until(lambda: platform.aura.is_open)         # TRCC holds the board
        change()
        assert [light.ref for light in app.settings.found_lights(LightKind.BOARD)
                ] == listed
    app.close()


def test_ram_sticks_follow_the_cpu_temperature(tmp_path: Path) -> None:
    """A static colour per stick that moves with the reading -- live, so on
    the RAM follower, not saved on the stick.

    MUTATION CHECK: refuse sticks in SetLinkedColor -- not ok."""
    from trcc.core.commands import SetLinkedColor
    from trcc.core.models import EFFECT_SOURCE, LinkedEffect
    app, platform, _seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    app.last_raw_readings = {"cpu:temp": 55.0}
    result = app.dispatch(SetLinkedColor(metric="temp",
                                         refs=("i2c-3/0x19", "i2c-3/0x1b")))
    assert (result.ok, result.message) == (True, "temp-linked (cpu) on 2 stick(s)")
    assert (app.settings.rgb_follow_mode(), app.settings.app.rgb_follow_source) == (
        RgbFollowMode.RAM, EFFECT_SOURCE)
    assert app.settings.light_effect("i2c-3/0x19") == LinkedEffect("temp", "cpu")
    _until(lambda: bool(platform.ram.chips[0x19].blocks))      # direct colours
    app.close()


def test_a_linked_colour_is_written_only_when_it_changes() -> None:
    """A stick's write is on the SMBus: the same colour is not sent again
    each tick.

    MUTATION CHECK: drop the _linked_unchanged guard -- a write a tick."""
    from trcc.core.models import EFFECT_SOURCE, LinkedEffect
    readings = {"cpu:temp": 50.0}
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    service = RgbMirrorService(make, readings=lambda: readings)
    service.configure(BOARD, "", 0, EFFECT_SOURCE, targets=("strip",),
                      effects={"strip": LinkedEffect("temp", "cpu")})
    _until(lambda: len(made[-1].shown) == 1)
    time.sleep(2.2)                                # two more ticks, same colour
    assert len(made[-1].shown) == 1
    readings["cpu:temp"] = 95.0
    _until(lambda: len(made[-1].shown) == 2)
    service.stop()


@pytest.mark.parametrize(("metric", "value", "rgb"), [
    ("load", 5.0, (0, 255, 0)), ("load", 50.0, (255, 255, 0)),
    ("load", 75.0, (255, 110, 0)), ("load", 95.0, (255, 0, 0)),
    ("temp", 40.0, (0, 255, 0)), ("temp", 65.0, (255, 255, 0)),
    ("temp", 80.0, (255, 110, 0)), ("temp", 92.0, (255, 0, 0)),
])
def test_a_linked_light_reads_green_fine_to_red_too_much(metric, value, rgb) -> None:
    """His scale: green fine, yellow light, orange heavy, red very heavy or
    too hot -- not the cooler's cyan, which read as cold.

    MUTATION CHECK: use the cooler's gradient -- 5 % load is cyan."""
    from trcc.services.effect_clock import LIGHT_GRADIENTS
    from trcc.services.led_effects import linked_color
    sensor = "cpu:temp" if metric == "temp" else "cpu:usage"
    assert linked_color(metric, "cpu", {sensor: value}, LIGHT_GRADIENTS[metric]) == rgb


@pytest.mark.parametrize("linked_first", [True, False], ids=["linked-first", "rain-first"])
def test_one_stick_linked_and_the_other_on_its_own_effect(tmp_path: Path,
                                                         linked_first: bool) -> None:
    """His ask: one stick on a linked colour, the other on Rain, in either
    order.  Saving Rain on one used to stop RAM following for both.

    MUTATION CHECK: stop following for every stick again -- the linked
    stick drops out."""
    from trcc.core.commands import SetLinkedColor
    from trcc.core.models import EFFECT_SOURCE
    app, _platform, _seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    linked = SetLinkedColor(metric="temp", refs=("i2c-3/0x19",))
    rain = SetRamEffect(effect=RamEffect.RAIN, refs=("i2c-3/0x1b",),
                        colors=((255, 0, 0), (0, 0, 255)))
    for command in ((linked, rain) if linked_first else (rain, linked)):
        assert app.dispatch(command).ok
    assert (app.settings.rgb_follow_mode(), app.settings.app.rgb_follow_source,
            app.settings.rgb_follow_targets()) == (
        RgbFollowMode.RAM, EFFECT_SOURCE, ("i2c-3/0x19",))
    rained = app.settings.light_effect("i2c-3/0x1b")
    assert isinstance(rained, RamEffectSettings) and rained.effect is RamEffect.RAIN
    app.close()


def test_an_effect_on_every_stick_still_ends_following(tmp_path: Path) -> None:
    from trcc.core.commands import SetLinkedColor
    app, _platform, _seen = _lights_app(tmp_path)
    app.dispatch(ScanRgbLights())
    app.dispatch(SetLinkedColor(metric="temp", refs=("i2c-3/0x19",)))
    result = app.dispatch(SetRamEffect(effect=RamEffect.RAINBOW))
    assert result.message.endswith("-- RAM following turned off")
    assert app.settings.rgb_follow_mode() is RgbFollowMode.OFF
    app.close()
