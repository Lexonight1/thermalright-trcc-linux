"""Other RGB follows the cooler (#160): the App service, Command and Query."""
from __future__ import annotations

import threading
import time
from functools import partial
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, RenderLed, RgbFollow, SetRgbFollow
from trcc.core.events import LedColorsChanged
from trcc.core.models import (
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbMirrorDevice,
)
from trcc.core.ports import RamLights, SmBus
from trcc.services.rgb_mirror import RgbMirrorService

from .mock_platform import (
    MockPlatform,
    ScriptedCorsairStick,
    ScriptedSmBus,
    scripted_ram,
)

OFF, OPENRGB, RAM_MODE = RgbFollowMode
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

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        if self.down:
            raise ConnectionRefusedError("nothing on 6742")
        self.scanned = True
        return (STRIP, RAM)

    def found(self) -> tuple[RgbMirrorDevice, ...] | None:
        return (STRIP, RAM) if self.scanned else None

    def apply_effect(self, device, settings) -> None:  # type: ignore[no-untyped-def]
        self.effects.append((device.name, settings))

    def show(self, device, colors) -> None:  # type: ignore[no-untyped-def]
        self.shown.append((device.name, tuple(colors)))

    def close(self) -> None:
        self.closed += 1
        self.scanned = False


def _until(check, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert check(), "timed out"


def _service(retry_s: float = 10.0) -> tuple[RgbMirrorService, list[FakeMirror]]:
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    return RgbMirrorService(make, retry_s=retry_s), made


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


def test_the_command_saves_the_setting_and_starts_following(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
    assert off.message == "Nothing follows the cooler"
    assert app.dispatch(RgbFollow()).mode is OFF
    app.close()


def test_the_saved_choice_follows_again_at_start(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, _ = _app(tmp_path)
    app.dispatch(SetRgbFollow(mode=RAM_MODE))
    app.close()
    again, made = _app(tmp_path)
    assert [m.mode for m in made] == [RAM_MODE]
    again.close()


def test_an_unknown_saved_choice_is_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, made = _app(tmp_path)
    app.settings.app.rgb_follow = "rainbow"
    assert app.settings.rgb_follow_mode() is OFF
    app.close()


def test_a_port_out_of_range_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, made = _app(tmp_path)
    result = app.dispatch(SetRgbFollow(mode=OPENRGB, port=70000))
    assert not result.ok
    assert result.message == "port out of range (1-65535): 70000"
    assert made == [] and app.settings.app.rgb_follow == "off"
    app.close()


def test_an_led_render_reaches_the_follower(tmp_path) -> None:  # type: ignore[no-untyped-def]
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


def test_the_app_lights_the_ram_its_platform_hands_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
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


def test_a_platform_without_an_smbus_refuses(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The base port refuses; only an OS that has the bus opens it."""
    from .conftest import FakePlatform

    with pytest.raises(OSError, match="no SMBus access on FakePlatform"):
        FakePlatform(tmp_path).smbuses()


def test_linux_opens_every_smbus_or_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
    assert [b.number for b in linux.LinuxOS.smbuses(object())] == [3, 5]  # type: ignore[arg-type]
    monkeypatch.setattr(linux, "find_smbus", lambda: (3, 7))
    with pytest.raises(PermissionError):
        linux.LinuxOS.smbuses(object())  # type: ignore[arg-type]
    assert opened[-1].number == 3 and opened[-1].closed
    monkeypatch.setattr(linux, "find_smbus", lambda: ())
    with pytest.raises(OSError, match="i2c-dev"):
        linux.LinuxOS.smbuses(object())  # type: ignore[arg-type]


# ── Every UI: the switch, its state from the App, and the event ────────────

def test_the_new_window_switch_follows_the_app(tmp_path, qtbot) -> None:  # type: ignore[no-untyped-def]
    """qtgui's Advanced tab: a toggle dispatches; a refresh only shows."""
    from trcc.ui.qtgui.panels.led import AdvancedTab

    app, made = _app(tmp_path)
    tab = AdvancedTab(app, lambda: "")
    qtbot.addWidget(tab)
    tab.show_openrgb()
    assert not tab._openrgb_check.isChecked()
    tab._openrgb_check.setChecked(True)
    assert app.settings.app.rgb_follow == "openrgb" and made
    assert tab._openrgb_status.text() == "Waiting for the cooler's colours"
    # Ticking RAM unticks OpenRGB: one choice.
    tab._ram_check.setChecked(True)
    assert app.settings.app.rgb_follow == "ram"
    assert tab._ram_check.isChecked() and not tab._openrgb_check.isChecked()
    # Another UI turns it off: the refresh shows it and sends nothing.
    app.dispatch(SetRgbFollow(mode=OFF))
    before = len(made)
    tab.show_openrgb()
    assert not tab._ram_check.isChecked() and len(made) == before
    app.close()


#: Every UCAbout this module builds, alive for the whole session.  A UCAbout
#: starts an update-check thread whose target is a bound method, so the thread
#: holds the panel: when the test's own reference goes first, the panel's LAST
#: reference is dropped on that thread and Qt destroys a widget off the GUI
#: thread -- SIGSEGV in pytest-qt's event processing, 5 runs in 10 (measured
#: 2026-10-08).  The app keeps its one UCAbout for its lifetime, so it cannot.
_KEEP_ALIVE: list = []


def test_the_classic_window_switch_and_status(qapp) -> None:  # type: ignore[no-untyped-def]
    """Built like the existing UCAbout test (``qapp``, assets set, never handed
    to qtbot to destroy) -- and kept alive: see ``_KEEP_ALIVE``."""
    from trcc.core.results import RgbFollowResult
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_about import UCAbout

    del qapp
    set_assets_dir(_PKG_ASSETS_DIR)
    about = UCAbout()
    _KEEP_ALIVE.append(about)
    asked: list = []
    about.rgb_follow_changed.connect(lambda *a: asked.append(a))
    about._openrgb_addr.setText("192.168.1.5:6800")
    about.openrgb_btn.click()
    assert asked == [("openrgb", "192.168.1.5", 6800)]
    about.openrgb_btn.click()                  # off
    about._openrgb_addr.setText("http://x")
    about.openrgb_btn.click()                  # a bad address sends nothing
    assert len(asked) == 2 and not about.openrgb_btn.isChecked()
    about.ram_btn.click()
    assert asked[-1] == ("ram", "", 0)
    about.show_openrgb(RgbFollowResult(
        ok=True, mode=OPENRGB, host="127.0.0.1", port=6742, connected=True,
        devices=("Motherboard", "RAM")))
    assert about.openrgb_btn.isChecked() and not about.ram_btn.isChecked()
    assert about._openrgb_status.text() == "Following on: Motherboard, RAM"
    about.show_openrgb(RgbFollowResult(
        ok=True, mode=RAM_MODE,
        error="PermissionError: [Errno 13] Permission denied"))
    assert about.ram_btn.isChecked() and not about.openrgb_btn.isChecked()
    assert about._openrgb_status.text() == (
        "Corsair RAM not reachable — "
        "PermissionError: [Errno 13] Permission denied")


def test_a_change_from_any_ui_reaches_the_windows(qtbot) -> None:  # type: ignore[no-untyped-def]
    from trcc.core.events import EventBus, RgbFollowChanged
    from trcc.ui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    with qtbot.waitSignal(bridge.app_settings_changed, timeout=1000) as sig:
        bus.publish(RgbFollowChanged(mode=RAM_MODE))
    assert isinstance(sig.args[0], RgbFollowChanged)


def test_the_new_window_sends_the_address_typed(tmp_path, qtbot) -> None:  # type: ignore[no-untyped-def]
    from trcc.ui.qtgui.panels.led import AdvancedTab

    app, made = _app(tmp_path)
    tab = AdvancedTab(app, lambda: "")
    qtbot.addWidget(tab)
    tab._openrgb_addr.setText("10.0.0.2:7000")
    tab._openrgb_check.setChecked(True)
    assert made[-1].address == ("10.0.0.2", 7000)
    assert (app.settings.app.openrgb_host, app.settings.app.openrgb_port) == (
        "10.0.0.2", 7000)
    app.close()


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


def _ram_service(bus: ScriptedSmBus) -> RgbMirrorService:
    from trcc.adapters.rgb import make_mirror
    return RgbMirrorService(partial(make_mirror, smbuses=lambda: (bus,)))


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


def test_linux_will_not_open_a_bus_whose_spd_hub_is_stuck(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A stuck SPD hub on the bus is no place to add traffic: the follower and
    the effects are refused, with the cure, until a power-off clears it.

    MUTATION CHECK: drop the stuck-hub refusal from ``LinuxOS.smbuses``."""
    from trcc.adapters.system import linux

    opened: list[int] = []
    monkeypatch.setattr(linux, "LinuxSmBus", lambda n: opened.append(n))
    monkeypatch.setattr(linux, "find_smbus", lambda: (3,))
    monkeypatch.setattr(linux, "silent_memory_sensors", lambda: ["3-0051"])
    with pytest.raises(OSError, match="3-0051 are not answering.*power-off"):
        linux.LinuxOS.smbuses(object())  # type: ignore[arg-type]
    assert opened == []
    # A stuck sensor on ANOTHER bus is not this bus's business.
    monkeypatch.setattr(linux, "silent_memory_sensors", lambda: ["9-0050"])
    assert len(linux.LinuxOS.smbuses(object())) == 1  # type: ignore[arg-type]


# ── RAM lighting: the opt-in grant, through the App ────────────────────────

class _FakeRamAccess:
    """Answers like the Linux adapter, without pkexec or /etc."""

    def __init__(self) -> None:
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.status_ = RamAccessStatus(RamAccessState.OFF, "RAM lighting is off")
        self.cancel = False
        self.calls: list[str] = []

    def status(self):  # type: ignore[no-untyped-def]
        return self.status_

    def enable(self):  # type: ignore[no-untyped-def]
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.calls.append("enable")
        if self.cancel:
            return RamAccessStatus(RamAccessState.OFF,
                                   "Cancelled -- nothing was changed")
        self.status_ = RamAccessStatus(RamAccessState.ON, "RAM lighting is on")
        return self.status_

    def disable(self):  # type: ignore[no-untyped-def]
        from trcc.core.models import RamAccessState, RamAccessStatus
        self.calls.append("disable")
        self.status_ = RamAccessStatus(RamAccessState.OFF, "RAM lighting is off")
        return self.status_


def _ram_app(tmp_path: Path) -> tuple[App, _FakeRamAccess, list]:
    from trcc.core.events import RamLightingChanged
    access = _FakeRamAccess()
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    platform.ram_access = lambda: access  # type: ignore[method-assign]
    app = App(platform)
    seen: list = []
    app.events.subscribe(RamLightingChanged, seen.append)
    return app, access, seen


def test_switching_ram_lighting_tells_every_ui(tmp_path) -> None:  # type: ignore[no-untyped-def]
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


def test_a_closed_password_prompt_is_not_ok(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from trcc.core.commands import SetRamLighting
    from trcc.core.models import RamAccessState

    app, access, _seen = _ram_app(tmp_path)
    access.cancel = True
    result = app.dispatch(SetRamLighting(enabled=True))
    assert (result.ok, result.state, result.message) == (
        False, RamAccessState.OFF, "Cancelled -- nothing was changed")
    app.close()


def test_a_client_waits_for_the_password_as_long_as_the_app_does(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The answer comes when the password is typed or the prompt closed --
    never a fixed 30 s that reports a failure that has not happened.

    MUTATION CHECK: drop ``WAITS_ON_USER`` from ``SetRamLighting``."""
    from trcc import ipc
    from trcc.core.commands import RamLighting, SetRamLighting
    from trcc.proxy import AppProxy

    waited: list[object] = []

    def answer(envelope, *, timeout):  # type: ignore[no-untyped-def]
        waited.append(timeout)
        return {"type": "RamLightingResult", "ok": True}

    monkeypatch.setattr(ipc, "one_shot_request", answer)
    proxy = AppProxy(timeout=30.0)
    proxy.dispatch(SetRamLighting(enabled=True))
    proxy.dispatch(RamLighting())
    assert waited == [None, 30.0]



# ── Following an LCD's picture, on the lights the user picked ──────────────

class _Frame:
    """A FrameSent as the follower reads it: a key and a surface."""

    def __init__(self, key: str, surface: object = "surface") -> None:
        self.key, self.surface = key, surface


def _grid(surface, cols: int, rows: int):  # type: ignore[no-untyped-def]
    """Column c is colour (c, c, c), whatever the surface."""
    return [[(c, c, c) for c in range(cols)] for _ in range(rows)]


def _following(source: str = "0402:3922", **kw) -> tuple[RgbMirrorService, FakeMirror]:  # type: ignore[no-untyped-def]
    service, made = _service()
    service.configure(RAM_MODE, "", 0, source, **kw)
    return service, made[-1] if made else service._ram  # type: ignore[return-value]


def test_halves_give_each_light_its_own_column_of_the_picture() -> None:
    from trcc.core.models import FollowMapping
    service, mirror = _following(mapping=FollowMapping.HALVES)
    service.on_frame(_Frame("0402:3922"), _grid)
    _until(lambda: len(mirror.shown) >= 2)
    assert mirror.shown[:2] == [("Strip", ((0, 0, 0),) * 10),
                                ("RAM", ((1, 1, 1),) * 10)]
    service.stop()


def test_single_gives_every_light_the_one_colour() -> None:
    from trcc.core.models import FollowMapping
    service, mirror = _following(mapping=FollowMapping.SINGLE)
    service.on_frame(_Frame("0402:3922"), _grid)
    _until(lambda: len(mirror.shown) >= 2)
    assert {colors for _name, colors in mirror.shown[:2]} == {((0, 0, 0),) * 10}
    service.stop()


def test_another_devices_frames_and_an_led_coolers_colours_are_ignored() -> None:
    service, mirror = _following()
    service.on_frame(_Frame("87ad:70db"), _grid)
    service.on_colors(_colors("0416:8001", (9, 9, 9)))
    time.sleep(0.1)
    assert mirror.shown == []
    service.stop()


def test_a_fast_video_is_sampled_at_most_twenty_times_a_second() -> None:
    """MUTATION CHECK: drop the FRAME_INTERVAL_S check in ``on_frame``."""
    service, _mirror = _following()
    sampled: list[int] = []

    def counting(surface, cols, rows):  # type: ignore[no-untyped-def]
        sampled.append(cols)
        return _grid(surface, cols, rows)

    for _ in range(10):                      # ten frames inside 50 ms
        service.on_frame(_Frame("0402:3922"), counting)
    assert len(sampled) == 1
    service.stop()


def test_only_the_picked_lights_follow() -> None:
    """MUTATION CHECK: drop the targets filter in ``_send``."""
    service, mirror = _following(targets=("ram",))
    service.on_frame(_Frame("0402:3922"), _grid)
    _until(lambda: bool(mirror.shown))
    time.sleep(0.05)
    assert [name for name, _c in mirror.shown] == ["RAM"]
    assert mirror.shown[0][1] == ((0, 0, 0),) * 10   # the only light: column 0
    service.stop()


def test_the_choice_is_kept_and_a_damaged_targets_setting_means_all(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
    app.settings.app.rgb_follow_targets = "Board"        # a hand-edited string
    assert app.settings.rgb_follow_targets() == ()
    app.close()


def test_the_app_follows_an_lcd_with_the_real_renderer(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
