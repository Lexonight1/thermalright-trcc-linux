"""The Module ABC: a feature switched on or off, hooks around its work.

``enable`` runs pre_enable -> _enable -> post_enable, ``disable`` runs
pre_disable -> _disable -> post_disable; ``SetModuleEnabled`` and
``ListModules`` are what every UI's Modules page sends.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from trcc.adapters.modules import MODULES, BaseModule
from trcc.app import App
from trcc.core.commands import Command, ListModules, SetModuleEnabled
from trcc.core.errors import ModuleUnavailable
from trcc.core.events import Event, FrameSent, ModuleChanged
from trcc.core.models import PanelPicture, Wire
from trcc.core.ports import Module, ModuleHost
from trcc.core.results import Result

from .conftest import FakePlatform

#: Every hook call, in order, across the fakes.
CALLS: list[str] = []


class _Recording(Module):
    """Records each step; ``key`` comes from the dict the App is given."""
    title, summary = "Recording", "Records its hooks."

    def pre_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_enable")

    def _enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:enable")

    def post_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:post_enable")

    def pre_disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_disable")

    def _disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:disable")

    def post_disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:post_disable")

    def start(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:start")

    def stop(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:stop")


class _On(_Recording):
    key = "on"


class _Off(_Recording):
    key, default_on = "off", False


class _Refuses(_Recording):
    """Cannot run here: its pre_enable says why."""
    key, default_on = "refuses", False

    def pre_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_enable")
        raise ModuleUnavailable("needs a thing this computer lacks")

    def problem(self) -> str:
        return "needs a thing this computer lacks"


_FAKES: dict[str, type[Module]] = {"on": _On, "off": _Off, "refuses": _Refuses}


def _app(tmp_home: Path) -> App:
    CALLS.clear()
    return App(platform=FakePlatform(tmp_home), modules=_FAKES)


def test_a_default_on_module_is_switched_on_at_start(tmp_home: Path) -> None:
    app = _app(tmp_home)
    assert (app.modules.on, CALLS) == (
        {"on"}, ["on:pre_enable", "on:enable", "on:post_enable"])


def test_the_hooks_run_in_order_around_the_work(tmp_home: Path) -> None:
    """MUTATION CHECK: call _disable before pre_disable in Module.disable."""
    app = _app(tmp_home)
    CALLS.clear()
    assert app.dispatch(SetModuleEnabled(key="off", enabled=True)).ok
    assert app.dispatch(SetModuleEnabled(key="off", enabled=False)).ok
    assert CALLS == ["off:pre_enable", "off:enable", "off:post_enable",
                     "off:pre_disable", "off:disable", "off:post_disable"]


def test_a_module_that_cannot_run_refuses_and_nothing_changes(
        tmp_home: Path) -> None:
    """pre_enable raising stops it before its work.  MUTATION CHECK: catch
    the refusal inside Module.enable and carry on."""
    app = _app(tmp_home)
    CALLS.clear()
    result = app.dispatch(SetModuleEnabled(key="refuses", enabled=True))
    assert (result.ok, result.message, CALLS, app.modules.is_on("refuses")) == (
        False, "needs a thing this computer lacks", ["refuses:pre_enable"], False)
    assert app.settings.module_enabled("refuses", False) is False


def test_the_choice_survives_a_restart(tmp_home: Path) -> None:
    """Switched off and on again by hand: a new App starts that way."""
    app = _app(tmp_home)
    app.dispatch(SetModuleEnabled(key="on", enabled=False))
    app.dispatch(SetModuleEnabled(key="off", enabled=True))
    app.close()
    again = _app(tmp_home)
    assert again.modules.on == {"off"}


def test_every_ui_hears_a_switch_and_sees_every_module(tmp_home: Path) -> None:
    app = _app(tmp_home)
    heard: list[tuple[str, bool]] = []

    def on_module(e: Event) -> None:
        assert isinstance(e, ModuleChanged)
        heard.append((e.key, e.enabled))
    app.events.subscribe(ModuleChanged, on_module)
    app.dispatch(SetModuleEnabled(key="off", enabled=True))
    listed = {m.key: (m.enabled, m.problem)
              for m in app.dispatch(ListModules()).modules}
    assert heard == [("off", True)]
    # "refuses" is off and was never loaded, so its code has not said why.
    assert listed == {"on": (True, ""), "off": (True, ""), "refuses": (False, "")}
    app.dispatch(SetModuleEnabled(key="refuses", enabled=True))   # tried: loaded
    [refuses] = [m for m in app.dispatch(ListModules()).modules if m.key == "refuses"]
    assert (refuses.enabled, refuses.problem) == (
        False, "needs a thing this computer lacks")


def test_closing_the_app_switches_modules_off_but_keeps_the_choice(
        tmp_home: Path) -> None:
    app = _app(tmp_home)
    CALLS.clear()
    app.close()
    assert CALLS == ["on:pre_disable", "on:disable", "on:post_disable"]
    assert app.settings.module_enabled("on", False) is False  # never chosen
    assert _app(tmp_home).modules.on == {"on"}           # still default on


def test_a_module_starts_with_the_session_not_when_an_app_is_built(
        tmp_home: Path) -> None:
    """Building an App only registers: a one-shot CLI command's App must not
    start a module's hardware (the 2026-10-21 ``trcc kill`` board grab).

    MUTATION CHECK: start modules in ``enable_saved`` -- "on:start" appears
    before the session."""
    app = _app(tmp_home)
    assert "on:start" not in CALLS
    try:
        app.start_session()
        assert CALLS[-1] == "on:start"
        CALLS.clear()
        assert app.dispatch(SetModuleEnabled(key="off", enabled=True)).ok
        assert CALLS == ["off:pre_enable", "off:enable", "off:post_enable",
                         "off:start"]                   # inside a session: at once
        CALLS.clear()
        assert app.dispatch(SetModuleEnabled(key="off", enabled=False)).ok
        assert CALLS == ["off:stop", "off:pre_disable", "off:disable",
                         "off:post_disable"]            # stopped before it goes
    finally:
        app.close()


class _Crashes(_Recording):
    """A module with a bug: its work raises while switching on."""
    key, title = "crashes", "Crashes"

    def _enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:enable")
        raise RuntimeError("boom")


class _CrashesOnStart(_Recording):
    key, title = "late", "Late"

    def start(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:start")
        raise RuntimeError("no hardware")


def test_a_crashing_module_neither_breaks_the_app_nor_stays_half_on(
        tmp_home: Path) -> None:
    """A bug in a module is the module's problem: the App still builds, the
    module is off and what it registered is taken back out, and the switch
    says why.  MUTATION CHECK: drop the ``except Exception`` in
    ``ModuleRunner._enable`` -- the App cannot be built."""
    CALLS.clear()
    app = App(platform=FakePlatform(tmp_home), modules={"crashes": _Crashes})
    assert app.modules.on == frozenset()
    assert "crashes:disable" in CALLS                   # undone
    result = app.dispatch(SetModuleEnabled(key="crashes", enabled=True))
    assert (result.ok, result.message) == (False, "Crashes did not switch on: boom")


def test_a_module_that_fails_to_start_is_switched_back_off(
        tmp_home: Path) -> None:
    CALLS.clear()
    app = App(platform=FakePlatform(tmp_home), modules={"late": _CrashesOnStart})
    try:
        app.start_session()
        assert app.modules.on == frozenset()
        assert CALLS == ["late:pre_enable", "late:enable", "late:post_enable",
                         "late:start",                  # raised: undone below
                         "late:pre_disable", "late:disable", "late:post_disable"]
    finally:
        app.close()


class _Listens(_Recording):
    """Hears ModuleChanged -- and never unsubscribes, as a careless module."""
    key, title, capabilities = "listens", "Listens", ("events",)

    def _enable(self, host: ModuleHost) -> None:
        host.subscribe(ModuleChanged, self._heard)

    def _heard(self, event: ModuleChanged) -> None:
        CALLS.append(f"heard:{event.key}")


class _Undeclared(_Listens):
    key, title, capabilities = "undeclared", "Undeclared", ()


def test_a_module_hears_events_until_it_is_switched_off(tmp_home: Path) -> None:
    """The scope takes back what the module subscribed to, even when the
    module forgets.  MUTATION CHECK: drop ``release()`` from
    ``ModuleRunner._disable`` -- it still hears after it is off."""
    app = App(platform=FakePlatform(tmp_home), modules={"listens": _Listens})
    CALLS.clear()
    app.events.publish(ModuleChanged(key="x", enabled=True))
    assert CALLS == ["heard:x"]
    assert app.dispatch(SetModuleEnabled(key="listens", enabled=False)).ok
    CALLS.clear()
    app.events.publish(ModuleChanged(key="y", enabled=True))
    assert CALLS == []


def test_a_capability_must_be_declared(tmp_home: Path) -> None:
    app = App(platform=FakePlatform(tmp_home), modules={"undeclared": _Undeclared})
    assert app.modules.on == frozenset()
    result = app.dispatch(SetModuleEnabled(key="undeclared", enabled=True))
    assert (result.ok, result.message) == (
        False, "Undeclared asked for 'events' without declaring it in its module.json")


RAN: list[str] = []


@dataclass(frozen=True, slots=True)
class _ListensCommand(Command[Result]):
    """A Command that belongs to the "listens" module."""
    MODULE: ClassVar[str] = "listens"

    def execute(self, app: App) -> Result:
        RAN.append("listens")
        return Result(ok=True, message="ran")


@dataclass(frozen=True, slots=True)
class _OrphanCommand(Command[Result]):
    MODULE: ClassVar[str] = "nope"

    def execute(self, app: App) -> Result:
        RAN.append("nope")
        return Result(ok=True, message="ran")


def test_a_modules_command_runs_only_while_it_is_on(tmp_home: Path) -> None:
    """Refused before it runs, in every UI alike, with where to turn it on.
    MUTATION CHECK: drop ``self._module_off(cmd)`` from ``App.dispatch`` --
    the Command runs while its module is off."""
    app = App(platform=FakePlatform(tmp_home), modules={"listens": _Listens})
    RAN.clear()
    assert app.dispatch(_ListensCommand()).message == "ran"
    app.dispatch(SetModuleEnabled(key="listens", enabled=False))
    refused = app.dispatch(_ListensCommand())
    assert (refused.ok, refused.message, RAN) == (
        False, "Listens is off -- turn it on in Modules", ["listens"])
    missing = app.dispatch(_OrphanCommand())
    assert (missing.ok, missing.message, RAN) == (
        False, "the nope module is not installed -- install it in Modules",
        ["listens"])


def test_an_unknown_module_is_refused(tmp_home: Path) -> None:
    result = _app(tmp_home).dispatch(SetModuleEnabled(key="nope", enabled=True))
    assert (result.ok, result.message) == (False, "no module 'nope'")


def test_a_module_registers_by_the_key_in_its_class_line() -> None:
    class _Registered(BaseModule, key="test-registered"):
        title = "Registered"

        def _enable(self, host: ModuleHost) -> None: ...

        def _disable(self, host: ModuleHost) -> None: ...

    try:
        assert (MODULES["test-registered"], _Registered.key) == (
            _Registered, "test-registered")
    finally:
        MODULES._table.pop("test-registered", None)



@pytest.fixture
def registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
               module_root: Callable[[Path], Path]) -> str:
    """The ONLY installed module, for the faces that build their own App: a
    folder with a manifest and an ``app.py`` that registers it when imported."""
    import json

    import trcc.modules as package

    folder = tmp_path / "modules" / "face"
    folder.mkdir(parents=True)
    (folder / "app.py").write_text(
        "from trcc.adapters.modules import BaseModule\n\n"
        "class Face(BaseModule, key='face'):\n"
        "    title, summary, default_on = 'Face', 'Reached from every UI.', False\n"
        "    def _enable(self, host): pass\n"
        "    def _disable(self, host): pass\n")
    (folder / "module.json").write_text(json.dumps({
        "key": "face", "title": "Face", "summary": "Reached from every UI.",
        "default_on": False}))
    module_root(folder.parent)
    monkeypatch.setattr(package, "__path__", [str(folder.parent)])
    return "face"


def test_the_cli_lists_and_switches(cli_runner: Any, registered: str,
                                    cli_app: App) -> None:
    """The CLI is a remote for the one App.  Here that App is the test's own
    in-process one (a stand-in platform never shares the user's daemon), so
    the module is registered before ``cli_app`` builds it -- as every real
    module is, at import."""
    from trcc.ui.cli.main import app

    listed = cli_runner.invoke(app, ["system", "modules", "list"])
    on = cli_runner.invoke(app, ["system", "modules", "on", registered])
    nope = cli_runner.invoke(app, ["system", "modules", "on", "no-such"])
    assert (listed.exit_code, on.exit_code, nope.exit_code) == (0, 0, 1)
    assert "[off] face             Face -- Reached from every UI." in listed.output
    assert (on.output.strip(), nope.output.strip().splitlines()[-1]) == (
        "Face on", "no module 'no-such'")


def test_the_api_lists_and_switches(fake_platform: FakePlatform,
                                    registered: str) -> None:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    with loopback_client(build_app(trcc=App(platform=fake_platform,
                                            renderer=QtRenderer()))) as client:
        before = client.get("/system/modules").json()
        switched = client.post(f"/system/modules/{registered}",
                               json={"enabled": True})
        missing = client.post("/system/modules/no-such", json={"enabled": True})
    assert [(m["key"], m["enabled"]) for m in before["modules"]] == [
        (registered, False)]
    assert (switched.status_code, switched.json()["enabled"],
            missing.status_code) == (200, True, 404)


def test_a_module_reaches_the_app_only_through_its_host() -> None:
    """Hexagonal: a module is handed a ModuleHost, never the App -- a fake
    host is all it needs."""
    class _Host(ModuleHost):
        def __init__(self) -> None:
            self.published: list[object] = []

        def publish(self, event: object) -> None:
            self.published.append(event)

        def subscribe(self, event_type: Any, handler: Any) -> None: ...

        def unsubscribe(self, event_type: Any, handler: Any) -> None: ...

    class _Announces(Module):
        key = "announces"

        def _enable(self, host: ModuleHost) -> None:
            host.publish(ModuleChanged(key=self.key, enabled=True))

        def _disable(self, host: ModuleHost) -> None: ...

    host = _Host()
    _Announces().enable(host)
    assert host.published == [ModuleChanged(key="announces", enabled=True)]


# ── The Modules page, in both windows ────────────────────────────────

def _switch(key: str, enabled: bool) -> SetModuleEnabled:
    return SetModuleEnabled(key=key, enabled=enabled)


def test_the_page_lists_switches_and_follows_other_uis(
        qtbot: Any, tmp_home: Path) -> None:
    """The shared list: a tile per module, its switch sends SetModuleEnabled,
    and a switch made in another UI shows.  MUTATION CHECK: drop the
    refresh in ModulesView.on_event."""
    from trcc.ui.qt_modules import ModulesView

    app = _app(tmp_home)
    view = ModulesView(app.dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    assert {k: s.isChecked() for k, s in view.switches.items()} == {
        "off": False, "on": True, "refuses": False}
    view.switches["off"].click()                        # this UI
    assert app.modules.on == {"on", "off"}
    app.dispatch(SetModuleEnabled(key="on", enabled=False))   # another UI
    view.on_event(ModuleChanged(key="on", enabled=False))
    assert {k: s.isChecked() for k, s in view.switches.items()} == {
        "off": True, "on": False, "refuses": False}


def test_a_refused_switch_stays_off(qtbot: Any, tmp_home: Path) -> None:
    from trcc.ui.qt_modules import ModulesView

    view = ModulesView(_app(tmp_home).dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    view.switches["refuses"].click()
    assert view.switches["refuses"].isChecked() is False


def test_a_refusal_is_shown_on_its_tile(qtbot: Any, tmp_home: Path) -> None:
    """A failure shows where the user is looking: the tile says why, until
    a switch succeeds.  It used to reach only the log.

    MUTATION CHECK: drop ``self.refused[key] = ...`` in ``_on_switch``."""
    from PySide6.QtWidgets import QLabel

    from trcc.ui.qt_modules import ModulesView

    app = App(platform=FakePlatform(tmp_home), modules={"undeclared": _Undeclared})
    view = ModulesView(app.dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    view.switches["undeclared"].click()
    notes = [label.text() for label in view.findChildren(QLabel)
             if label.objectName() == "moduleProblem"]
    assert notes == ["Undeclared asked for 'events' without declaring it in its module.json"]


def test_a_tile_shows_its_symbol_and_opens_its_guide(
        qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QLabel, QPushButton

    from trcc.core.models import ModuleInfo
    from trcc.core.results import ModulesResult
    from trcc.ui.qt_modules import ModulesView

    icon = tmp_path / "icon.svg"
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
                    '<circle cx="20" cy="20" r="18" fill="gold"/></svg>')
    guide = "https://example.invalid/GUIDE_MODULES.md#face"
    tile = ModuleInfo(key="face", title="Face", summary="s", enabled=True,
                      icon=str(icon), guide=guide)
    view = ModulesView(lambda _cmd: ModulesResult(modules=(tile,)), ListModules, _switch)
    qtbot.addWidget(view)
    [symbol] = [w for w in view.findChildren(QLabel) if w.objectName() == "moduleIcon"]
    assert not symbol.pixmap().isNull()
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    [link] = [b for b in view.findChildren(QPushButton) if b.objectName() == "moduleGuide"]
    link.click()
    assert (link.text(), opened) == ("Guide ›", [guide])


def test_no_modules_says_so(qtbot: Any, tmp_home: Path) -> None:
    from PySide6.QtWidgets import QLabel

    from trcc.ui.qt_modules import NONE_YET, ModulesView

    view = ModulesView(App(platform=FakePlatform(tmp_home), modules={}).dispatch,
                       ListModules, _switch)
    qtbot.addWidget(view)
    assert [w.text() for w in view.findChildren(QLabel)] == [NONE_YET]


def test_the_control_center_bar_opens_the_modules_page(
        qtbot: Any, fake_platform: FakePlatform) -> None:
    """Control Center · Modules · About -- Modules opens its own page."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.trcc_app import TRCCApp

    window = TRCCApp(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(window)
    keys = [key for _w, key in window._i18n_labels]
    assert keys.index("Control Center") < keys.index("Modules") < keys.index("About")
    window._show_view("about")
    window.uc_about.modules_link.click()
    assert (window.uc_modules.isVisibleTo(window),
            window.uc_about.isVisibleTo(window)) == (True, False)
    window.close()


def test_qtgui_has_the_modules_page_above_about() -> None:
    from trcc.ui.qtgui.panels.sidebar import _ENTRIES

    keys = [entry.key for entry in _ENTRIES]
    assert keys.index("modules") == keys.index("about") - 1


def test_a_switch_turns_a_module_off_too(qtbot: Any, tmp_home: Path) -> None:
    """MUTATION CHECK: a switch that only ever sends enabled=True."""
    from trcc.ui.qt_modules import ModulesView

    app = _app(tmp_home)
    view = ModulesView(app.dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    view.switches["on"].click()
    assert app.modules.on == set()


# ── frames, readings, hardware, password: what RGB reaches through its host ──

class _Watches(_Recording):
    """Watches every panel's pictures -- and never unwatches, as a careless
    module.  Reads the pixels only of panel ``read``."""
    key, title, capabilities = "watches", "Watches", ("frames",)
    SEEN: ClassVar[list[tuple[str, Any]]] = []

    def _enable(self, host: ModuleHost) -> None:
        host.frames.watch(self._picture)

    def _picture(self, picture: PanelPicture) -> None:
        pixels = picture.argb32() if picture.key == "read" else None
        _Watches.SEEN.append((picture.key, pixels))


class _Renderer:
    """Just the one call a picture's pixels come from, counted."""

    def __init__(self) -> None:
        self.read: list[object] = []

    def raw_argb32(self, surface: object) -> tuple[bytes, int, int, int]:
        self.read.append(surface)
        return b"\x01\x02\x03\x04", 1, 1, 4


def _watching(tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
              ) -> tuple[App, _Renderer, list[str]]:
    """An App with ``_Watches`` on; the pixel reads and the picture lookups
    it makes, counted."""
    from trcc.adapters.render.qt import QtRenderer

    _Watches.SEEN.clear()
    real = QtRenderer()
    app = App(platform=FakePlatform(tmp_home), renderer=real,
              modules={"watches": _Watches})
    renderer = _Renderer()
    monkeypatch.setattr(real, "raw_argb32", renderer.raw_argb32)
    looked: list[str] = []

    def background_surface(key: str) -> None:
        looked.append(key)

    monkeypatch.setattr(app.display, "background_surface", background_surface)
    return app, renderer, looked


def test_a_module_sees_each_picture_and_reads_pixels_only_when_it_asks(
        tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each sent frame reaches the watcher as that panel's picture; the
    pixels are converted only for the one it reads.  MUTATION CHECK: read
    ``renderer.raw_argb32(surface)`` eagerly in ``App._picture_sent``."""
    app, renderer, _ = _watching(tmp_home, monkeypatch)
    shown, read = object(), object()
    app.events.publish(FrameSent(key="shown", bytes_sent=1, surface=shown))
    app.events.publish(FrameSent(key="read", bytes_sent=1, surface=read))
    assert _Watches.SEEN == [("shown", None), ("read", (b"\x01\x02\x03\x04", 1, 1, 4))]
    assert renderer.read == [read]


def test_switched_off_a_module_sees_no_pictures_and_the_app_stops_hearing_frames(
        tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scope takes the watcher back even when the module forgets, and
    with no watcher the App stops hearing frames for modules at all.
    MUTATION CHECK: drop the watchers from ``ModuleScope.release`` -- it
    still sees; or keep ``FrameSent`` subscribed in ``unwatch_frames``."""
    app, _, _ = _watching(tmp_home, monkeypatch)
    heard = app.events.subscriber_count(FrameSent)
    app.events.publish(FrameSent(key="a", bytes_sent=1, surface=object()))
    assert app.dispatch(SetModuleEnabled(key="watches", enabled=False)).ok
    app.events.publish(FrameSent(key="b", bytes_sent=1, surface=object()))
    assert _Watches.SEEN == [("a", None)]
    assert app.events.subscriber_count(FrameSent) == heard - 1


class _Reaches(_Recording):
    """Asks the host for readings, hardware and the password, once."""
    key, title = "reaches", "Reaches"
    capabilities = ("readings", "hardware", "password")
    GOT: ClassVar[dict[str, Any]] = {}

    def _enable(self, host: ModuleHost) -> None:
        try:
            buses: Any = host.hardware.smbuses()
        except OSError as e:          # the Platform's own refusal, passed on
            buses = str(e)
        _Reaches.GOT.update(
            readings=host.readings.latest(), smbuses=buses,
            ids=host.hardware.ids(), hid=host.hardware.open_hid(0x0B05, 0x19AF, 0xEC),
            access=host.password.memory_access())


def test_readings_hardware_and_the_password_come_from_the_platform(
        tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each is the Platform's own answer -- a stand-in's scripted twin under
    test, so a module never reaches real hardware.  The HID transport is a
    lighting controller's: HID reports, its report id.  MUTATION CHECK:
    drop ``hid_reports=True`` from ``App.open_hid``."""
    platform = FakePlatform(tmp_home)
    opened: list[tuple[Any, ...]] = []

    def open_transport(wire: Wire, vid: int, pid: int, serial: str | None = None,
                       unit: str = "", *, hid_reports: bool = False,
                       report_id: int = 0) -> Any:
        opened.append((wire, vid, pid, hid_reports, report_id))
        return platform.bulk

    monkeypatch.setattr(platform, "open_transport", open_transport)
    _Reaches.GOT.clear()
    App(platform=platform, modules={"reaches": _Reaches})
    got = _Reaches.GOT
    assert opened == [(Wire.HID, 0x0B05, 0x19AF, True, 0xEC)]
    assert (got["readings"], got["smbuses"], got["hid"]) == (
        None, "no SMBus access on FakePlatform", platform.bulk)
    assert type(got["ids"]) is type(platform.rgb_hardware())
    assert type(got["access"]) is type(platform.ram_access())


def test_the_readings_are_the_last_sweep_and_read_only(tmp_home: Path) -> None:
    """MUTATION CHECK: return ``self.last_raw_readings`` itself."""
    app = App(platform=FakePlatform(tmp_home), modules={})
    app.last_raw_readings = {"cpu:temp": 41.0}
    latest = app.latest_readings()
    assert latest == {"cpu:temp": 41.0}
    with pytest.raises(TypeError):
        cast("dict[str, float]", latest)["cpu:temp"] = 99.0


@pytest.mark.parametrize("capability", ["frames", "readings", "hardware", "password"])
def test_each_new_capability_must_be_declared(tmp_home: Path,
                                              capability: str) -> None:
    """MUTATION CHECK: return the port without ``_need`` in ``_port``."""

    class Asks(_Recording):
        key, title, capabilities = "asks", "Asks", ()

        def _enable(self, host: ModuleHost) -> None:
            getattr(host, capability)

    app = App(platform=FakePlatform(tmp_home), modules={"asks": Asks})
    result = app.dispatch(SetModuleEnabled(key="asks", enabled=True))
    assert (result.ok, result.message) == (
        False, f"Asks asked for {capability!r} without declaring it in its module.json")


# ── a module's gui view: a whole page behind its own sidebar button ──────────

_VIEW_MODULE = {
    "module.json": '{"key": "viewer", "title": "Viewer", "summary": "A view.",'
                   ' "default_on": true, "capabilities": []}\n',
    "__init__.py": "",
    "app.py": "from trcc.adapters.modules import BaseModule\n\n\n"
              "class Viewer(BaseModule, key='viewer'):\n"
              "    title, summary = 'Viewer', 'A view.'\n\n"
              "    def _enable(self, host) -> None:\n        pass\n\n"
              "    def _disable(self, host) -> None:\n        pass\n",
    "ui/__init__.py": "",
    "ui/gui/__init__.py":
        "from PySide6.QtWidgets import QLabel, QWidget\n"
        "from trcc.ui.gui.module_view import ModuleView\n\n\n"
        "class ViewerView(QWidget, ModuleView):\n"
        "    BUTTON_TEXT, TOOLTIP = 'Viewer', 'The viewer'\n\n"
        "    def __init__(self, app, bridge) -> None:\n"
        "        super().__init__()\n"
        "        self.label = QLabel('a view', self)\n"
        "        self.languages = []\n"
        "        bridge.add_preview(self.label)\n\n"
        "    def set_language(self, lang: str) -> None:\n"
        "        self.languages.append(lang)\n\n\n"
        "VIEW = ViewerView\n",
}


@pytest.fixture
def viewer(module_root: Callable[[Path], Path], tmp_path: Path) -> Path:
    """A module whose gui adds a VIEW, installed as a built-in is."""
    root = tmp_path / "modules"
    for rel, text in _VIEW_MODULE.items():
        (root / "viewer" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "viewer" / rel).write_text(text, encoding="utf-8")
    return module_root(root)


def test_the_gui_shows_a_modules_view_behind_its_button_while_it_is_on(
        viewer: Path, qtbot: Any, tmp_home: Path) -> None:
    """On: a button in the sidebar's module slot opens the view, the rest of
    the window hidden, its Help and Close still on top.  Off: the button
    hides and the window goes home.  MUTATION CHECK: skip the hiding in
    ``_sync_module_views`` and the button stays; drop the raises in
    ``_add_module_view`` and the view covers Help."""
    from trcc.adapters.modules import specs
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.constants import Layout
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(platform=FakePlatform(tmp_home), renderer=QtRenderer(),
              modules=specs(viewer))
    window = TRCCApp(app=app)
    qtbot.addWidget(window)
    window.show()
    button = window.uc_device.view_buttons["viewer"]
    view: Any = window._module_views["viewer"]
    button.click()
    opened = (view.isVisible(), window.uc_about.isVisible(),
              window.form_container.isVisible(), button.isChecked())
    # The window's own Help and Close stay above it, as on every built-in page.
    help_btn = window.form1_help_btn
    on_top = help_btn.parentWidget().childAt(help_btn.geometry().center())
    app.dispatch(SetModuleEnabled(key="viewer", enabled=False))
    window._sync_modules()
    assert (button.geometry().getRect(), button.text(), button.toolTip(),
            view.languages[-1]) == (Layout.SIDEBAR_MODULE_SLOTS[0], "Viewer",
                                    "The viewer", "en")
    assert opened == (True, False, False, True)
    assert on_top is help_btn
    assert (button.isVisibleTo(window), view.isVisible(),
            window.uc_system_info.isVisible()) == (False, False, True)
    window.close()


def test_a_modules_preview_takes_frames_only_while_it_is_on_screen(
        qtbot: Any, tmp_home: Path) -> None:
    """``add_preview`` joins the window's own previews -- and
    ``follow_previews`` (the window's) never drops it.  MUTATION CHECK:
    read only ``self._previews`` in ``BusBridge._sync_frames``."""
    from PySide6.QtWidgets import QWidget

    from trcc.ui.bus_bridge import BusBridge

    app = App(platform=FakePlatform(tmp_home), modules={})
    bridge = BusBridge(app.events)
    window, other = QWidget(), QWidget()
    preview, mine = QWidget(window), QWidget(window)
    qtbot.addWidget(window)
    qtbot.addWidget(other)
    bridge.add_preview(mine)
    bridge.follow_previews(preview)
    window.show()
    preview.hide()
    taking = bridge._frames_on
    mine.hide()
    assert (taking, bridge._frames_on) == (True, False)


def test_a_modules_own_event_reaches_its_page_on_the_module_signal(
        qtbot: Any, tmp_home: Path) -> None:
    """The bridge cannot name a module's events; a page forwards each to
    ``module_event``.  MUTATION CHECK: forward to ``settings_changed``
    instead -- an event with no ``key`` would reach every LCD handler."""
    from trcc.ui.bus_bridge import BusBridge

    app = App(platform=FakePlatform(tmp_home), modules={})
    bridge = BusBridge(app.events)
    bridge.forward(ModuleChanged, "module_event")
    with qtbot.waitSignal(bridge.module_event, timeout=1000) as got:
        app.events.publish(ModuleChanged(key="rgb", enabled=True))
    assert got.args == [ModuleChanged(key="rgb", enabled=True)]
