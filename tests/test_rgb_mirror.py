"""OpenRGB follows the cooler (#160): the App service, Command and Query."""
from __future__ import annotations

import time
from pathlib import Path

from trcc.app import App
from trcc.core.commands import ConnectDevice, OpenRgbSync, RenderLed, SetOpenRgbSync
from trcc.core.events import LedColorsChanged
from trcc.core.models import RgbMirrorDevice
from trcc.core.ports import RgbMirror
from trcc.services.rgb_mirror import RgbMirrorService

from .mock_platform import MockPlatform

STRIP = RgbMirrorDevice(0, "Strip", 4)
RAM = RgbMirrorDevice(1, "RAM", 2)


class FakeMirror(RgbMirror):
    """Records what it is shown; ``down`` makes every call fail."""

    def __init__(self, host: str = "", port: int = 0) -> None:
        self.address = (host, port)
        self.shown: list[tuple[str, tuple]] = []
        self.down = False
        self.closed = 0

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        if self.down:
            raise ConnectionRefusedError("nothing on 6742")
        return (STRIP, RAM)

    def show(self, device, colors) -> None:  # type: ignore[no-untyped-def]
        self.shown.append((device.name, tuple(colors)))

    def close(self) -> None:
        self.closed += 1


def _until(check, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert check(), "timed out"


def _service(retry_s: float = 10.0) -> tuple[RgbMirrorService, list[FakeMirror]]:
    made: list[FakeMirror] = []

    def make(host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(host, port))
        return made[-1]

    return RgbMirrorService(make, retry_s=retry_s), made


def _colors(key: str, *rgb: tuple[int, int, int]) -> LedColorsChanged:
    return LedColorsChanged(key=key, color_count=len(rgb), colors=rgb)


def test_the_leading_coolers_colours_reach_every_device() -> None:
    service, made = _service()
    service.configure(True, "127.0.0.1", 6742)
    service.on_colors(_colors("0416:8001", (9, 8, 7)))
    _until(lambda: len(made[0].shown) == 2)
    assert made[0].address == ("127.0.0.1", 6742)
    assert made[0].shown == [("Strip", ((9, 8, 7),)), ("RAM", ((9, 8, 7),))]
    assert service.status.connected and service.status.devices == ("Strip", "RAM")
    assert service.status.lead == "0416:8001"
    service.stop()


def test_a_second_cooler_does_not_fight_the_lead() -> None:
    service, made = _service()
    service.configure(True, "h", 1)
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
    assert made == [] and not service.status.enabled


def test_an_unreachable_openrgb_is_reported_and_retried() -> None:
    service, made = _service(retry_s=0.05)
    service.configure(True, "h", 1)
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
    service.configure(True, "h", 1)
    service.stop()
    assert made[0].closed == 1 and not service.status.enabled


# ── Through the App ─────────────────────────────────────────────────────────

def _app(tmp_path: Path) -> tuple[App, list[FakeMirror]]:
    made: list[FakeMirror] = []

    def make(host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(host, port))
        return made[-1]

    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path),
              make_rgb_mirror=make)
    return app, made


def test_the_command_saves_the_setting_and_starts_following(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, made = _app(tmp_path)
    result = app.dispatch(SetOpenRgbSync(enabled=True, port=7000))
    assert result.ok and result.enabled
    assert (result.host, result.port) == ("127.0.0.1", 7000)
    assert made[0].address == ("127.0.0.1", 7000)
    assert app.settings.app.openrgb_enabled and app.settings.app.openrgb_port == 7000
    off = app.dispatch(SetOpenRgbSync(enabled=False))
    assert off.ok and not off.enabled and off.port == 7000
    assert not app.dispatch(OpenRgbSync()).enabled
    app.close()


def test_a_port_out_of_range_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, made = _app(tmp_path)
    result = app.dispatch(SetOpenRgbSync(enabled=True, port=70000))
    assert not result.ok
    assert result.message == "port out of range (1-65535): 70000"
    assert made == [] and not app.settings.app.openrgb_enabled
    app.close()


def test_an_led_render_reaches_the_follower(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The whole chain: RenderLed -> LedColorsChanged -> the follower."""
    app, made = _app(tmp_path)
    assert app.dispatch(ConnectDevice(key="0416:8001")).ok
    app.dispatch(SetOpenRgbSync(enabled=True))
    rendered = app.dispatch(RenderLed(key="0416:8001"))
    assert rendered.ok
    _until(lambda: len(made[0].shown) >= 2)
    name, colors = made[0].shown[0]
    assert name == "Strip"
    assert list(colors) == rendered.colors
    app.close()


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
    assert app.settings.app.openrgb_enabled and made
    assert tab._openrgb_status.text() == (
        "Waiting for the cooler's colours (127.0.0.1:6742)")
    # Another UI turns it off: the refresh shows it and sends nothing.
    app.dispatch(SetOpenRgbSync(enabled=False))
    before = len(made)
    tab.show_openrgb()
    assert not tab._openrgb_check.isChecked() and len(made) == before
    app.close()


def test_the_classic_window_switch_and_status(qapp) -> None:  # type: ignore[no-untyped-def]
    """Built like the existing UCAbout test (``qapp``, assets set, never handed
    to qtbot to destroy): destroying a UCAbout at teardown crashed an xdist
    worker once in a full run (pytest-qt's event processing, SIGSEGV)."""
    from trcc.core.results import OpenRgbSyncResult
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_about import UCAbout

    del qapp
    set_assets_dir(_PKG_ASSETS_DIR)
    about = UCAbout()
    asked: list = []
    about.openrgb_toggle_changed.connect(lambda *a: asked.append(a))
    about._openrgb_addr.setText("192.168.1.5:6800")
    about.openrgb_btn.click()
    assert asked == [(True, "192.168.1.5", 6800)]
    about.openrgb_btn.click()                  # off
    about._openrgb_addr.setText("http://x")
    about.openrgb_btn.click()                  # a bad address sends nothing
    assert len(asked) == 2 and not about.openrgb_btn.isChecked()
    about.show_openrgb(OpenRgbSyncResult(
        ok=True, enabled=True, host="127.0.0.1", port=6742, connected=True,
        devices=("Motherboard", "RAM")))
    assert about.openrgb_btn.isChecked()
    assert about._openrgb_status.text() == "Following on: Motherboard, RAM"
    about.show_openrgb(OpenRgbSyncResult(
        ok=True, enabled=True, host="127.0.0.1", port=6742,
        error="ConnectionRefusedError: [Errno 111] Connection refused"))
    assert about._openrgb_status.text() == (
        "OpenRGB not reachable at 127.0.0.1:6742 — "
        "ConnectionRefusedError: [Errno 111] Connection refused")


def test_a_change_from_any_ui_reaches_the_windows(qtbot) -> None:  # type: ignore[no-untyped-def]
    from trcc.core.events import EventBus, OpenRgbSyncChanged
    from trcc.ui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    with qtbot.waitSignal(bridge.app_settings_changed, timeout=1000) as sig:
        bus.publish(OpenRgbSyncChanged(enabled=True))
    assert isinstance(sig.args[0], OpenRgbSyncChanged)


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
