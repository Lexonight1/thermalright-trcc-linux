"""A panel whose module is not installed says so, in every UI.

The App keeps each panel's background choice by module KEY, and a key
outlasts the module: a fresh install, or an upgrade from a version that
shipped one as a core feature, leaves the choice without the code.

Measured 2026-11-01, with the Visualizer absent and a panel that chose it:
the choice survived, and **nothing told anyone**.  ``ListModules`` answered
with no modules at all, so no window, CLI or API had anything to show, and
the single log line called the module "off" -- which sends a reporter looking
for a switch that does not exist.  An absent module is in no other answer, so
only the App can say it: ``App.modules_wanted`` joins the saved choices with
the modules it has, ``ListModules`` carries it, and every UI reads it there.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, ListModules, RestoreDeviceState
from trcc.core.models import ModuleBackground, ThemeBackground, WantedModule
from trcc.ui.presentation.module_catalog import wanted_line

from .conftest import renderable_theme
from .mock_platform import MockPlatform

_KEY = "0402:3922"
_OTHER = "87ad:70db"
_SPECS = [{"type": "lcd", "name": "FW", "vid": "0402", "pid": "3922", "fbl": 100},
          {"type": "lcd", "name": "HR10", "vid": "87ad", "pid": "70db", "pm": 72,
           "resolution": "480x480"}]


def _hide_the_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                      module_root: Any) -> None:
    """No modules at all -- a user's install before they install one."""
    import trcc.modules as package

    empty = tmp_path / "nomodules"
    empty.mkdir(exist_ok=True)
    module_root(empty)
    monkeypatch.setattr(package, "__path__", [str(empty)])


@pytest.fixture
def no_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
               module_root: Any) -> None:
    """Hidden before anything builds an App: a face registers the modules it
    finds at construction, so hiding them afterwards leaves them registered."""
    _hide_the_modules(tmp_path, monkeypatch, module_root)


@pytest.fixture
def chose_the_visualizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         module_root: Any) -> App:
    """An upgrader: a panel that plays a visual, and no Visualizer here."""
    _hide_the_modules(tmp_path, monkeypatch, module_root)
    app = App(MockPlatform(_SPECS, tmp_path / "root"), renderer=QtRenderer())
    theme = renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme1")
    app.settings.set_current_theme(_KEY, str(theme))
    app.settings.set_background(_KEY, ModuleBackground("visualizer"))
    return app


def test_the_app_names_the_module_a_panel_chose_and_does_not_have(
        chose_the_visualizer: App) -> None:
    """MUTATION CHECK: drop the ``not in have`` test in ``modules_wanted``."""
    assert chose_the_visualizer.modules_wanted() == (
        WantedModule(key="visualizer", panels=(_KEY,)),)


def test_a_module_that_is_here_is_not_wanted(tmp_path: Path) -> None:
    """The Visualizer present, and the same choice: nothing to tell anyone --
    the module itself resumes the panel."""
    app = App(MockPlatform(_SPECS, tmp_path / "root"), renderer=QtRenderer())
    app.settings.set_background(_KEY, ModuleBackground("visualizer"))
    assert app.modules_wanted() == ()


def test_every_panel_that_chose_it_is_named(chose_the_visualizer: App) -> None:
    """Attached or not: a choice survives an unplug, so the saved settings
    are asked, never the bus."""
    chose_the_visualizer.settings.set_background(_OTHER, ModuleBackground("visualizer"))
    assert chose_the_visualizer.modules_wanted() == (
        WantedModule(key="visualizer", panels=(_KEY, _OTHER)),)


def test_a_panel_that_chose_nothing_wants_nothing(chose_the_visualizer: App) -> None:
    chose_the_visualizer.settings.set_background(_KEY, ThemeBackground())
    assert chose_the_visualizer.modules_wanted() == ()


def test_the_modules_list_carries_it_to_every_ui(chose_the_visualizer: App) -> None:
    """``ListModules`` is what every Modules page reads.  MUTATION CHECK:
    stop passing ``wanted=`` in ``ListModules.execute``."""
    listed = chose_the_visualizer.dispatch(ListModules())
    assert (listed.modules, listed.wanted) == (
        (), (WantedModule(key="visualizer", panels=(_KEY,)),))


def test_the_log_says_not_installed_rather_than_off(
        chose_the_visualizer: App, caplog: pytest.LogCaptureFixture) -> None:
    """The one line a report would carry said "is off", and a switch that is
    not there cannot be turned on.  MUTATION CHECK: take the not-installed
    branch out of ``resume_background``."""
    app = chose_the_visualizer
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    with caplog.at_level(logging.INFO):
        app.dispatch(RestoreDeviceState(key=_KEY))

    about = [r for r in caplog.records if "resume_background" in r.getMessage()
             and "visualizer" in r.getMessage()]
    assert [r.levelname for r in about] == ["WARNING"], [r.getMessage() for r in about]
    assert "NOT INSTALLED" in about[0].getMessage()
    assert "is off" not in about[0].getMessage()


def test_a_restore_with_no_saved_theme_keeps_the_choice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module_root: Any) -> None:
    """Themes on disk, none saved: the restore falls back to Theme1 by itself
    -- and loaded it as if the user had PICKED it, which ends a module's
    background, so the choice was gone before anyone could be told.  Measured
    2026-11-03 upgrading from v9.11.2 with the theme data arriving after the
    choice.  MUTATION CHECK: drop ``self.picked`` from LoadTheme's test."""
    _hide_the_modules(tmp_path, monkeypatch, module_root)
    app = App(MockPlatform(_SPECS, tmp_path / "root"), renderer=QtRenderer())
    renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme1")
    app.settings.set_background(_KEY, ModuleBackground("visualizer"))
    assert app.settings.for_device(_KEY).current_theme is None
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert (app.settings.for_device(_KEY).chosen_module, app.modules_wanted()) == (
        "visualizer", (WantedModule(key="visualizer", panels=(_KEY,)),))


@pytest.mark.parametrize(("panels", "said"), [
    ((_KEY,), f"Your panel {_KEY} uses visualizer — install it to get it back"),
    ((_KEY, _OTHER), "2 of your panels use visualizer — install it to get them back"),
])
def test_the_words_name_one_panel_and_count_several(
        panels: tuple[str, ...], said: str) -> None:
    """A list of USB ids tells a user nothing their panel count does not."""
    assert wanted_line(WantedModule("visualizer", panels), str) == said


def test_the_cli_says_it(no_modules: None, cli_runner: Any, cli_app: App) -> None:
    """MUTATION CHECK: drop the ``listed.wanted`` loop from ``cli/module.py``."""
    from trcc.ui.cli.main import app as cli

    cli_app.settings.set_background(_KEY, ModuleBackground("visualizer"))

    out = cli_runner.invoke(cli, ["system", "modules", "list"]).output
    assert f"Your panel {_KEY} uses visualizer" in out, out


def test_the_window_says_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            module_root: Any, qtbot: Any) -> None:
    """The one shared Modules view both windows use.  A wanted module has no
    tile -- there is nothing of it here to make one from -- so the line sits
    above them, beside the button that installs it.  MUTATION CHECK: drop the
    ``wanted`` loop from ``ModulesView.refresh``."""
    from PySide6.QtWidgets import QLabel

    from .test_gui_follows_the_app import _open

    _hide_the_modules(tmp_path, monkeypatch, module_root)
    app = App(MockPlatform(_SPECS, tmp_path / "root"), renderer=QtRenderer())
    app.settings.set_background(_KEY, ModuleBackground("visualizer"))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    win = _open(app, qtbot, key=_KEY)
    try:
        view = win.uc_modules.view
        view.refresh()

        def shown() -> list[str]:
            # ``_clear`` hides a tile and calls deleteLater, so a replaced
            # label is still a child until the event loop runs.
            return [w.text() for w in view.findChildren(QLabel)
                    if w.objectName() == "modulesWanted" and not w.isHidden()]

        qtbot.waitUntil(lambda: len(shown()) == 1, timeout=2000)
        assert shown() == [f"Your panel {_KEY} uses visualizer — install it to get it back"]
    finally:
        win.close()


def test_the_api_serves_it(no_modules: None, tmp_path: Path) -> None:
    """The route returns the Result itself, so a new field on the Result
    reaches a client with no API code -- reasoned, then measured, because
    reasoning is not evidence.  MUTATION CHECK: stop passing ``wanted=`` in
    ``ListModules.execute``."""
    from trcc.ui.api.main import build_app

    from .conftest import FakePlatform
    from .test_api_routes import loopback_client

    api_app = App(platform=FakePlatform(tmp_path), renderer=QtRenderer())
    api_app.settings.set_background(_KEY, ModuleBackground("visualizer"))
    with loopback_client(build_app(trcc=api_app)) as client:
        body = client.get("/system/modules").json()

    assert body["modules"] == []
    assert body["wanted"] == [{"key": "visualizer", "panels": [_KEY]}]
