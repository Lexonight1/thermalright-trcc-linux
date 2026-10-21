"""The Audio Visualizer as a module: a mini hexagon on the App's ports.

What being a module changes, end to end on the stand-in platform: its
Commands are refused while it is off; switching it off ends every panel it
draws but keeps each one's choice, and switching it on draws them again; a
theme whose background it draws waits for it while it is off and says so;
stopping a panel's visual works with it off; a host offers only what its
manifest declares; and a settings file from before it was a module keeps
every panel playing what it played.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtGui import QImage

import trcc.modules.visualizer.app as visualizer_app
from tests.conftest import FakePaths, show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ClearModuleSource,
    ConnectDevice,
    LcdSnapshot,
    LoadTheme,
    SaveTheme,
    SetModuleEnabled,
)
from trcc.core.errors import ModuleUnavailable
from trcc.core.models import ModuleManifest
from trcc.core.ports import BackgroundKind, ModuleServices
from trcc.modules.visualizer.core import ListVisuals, PlayVisual
from trcc.services.modules import ModuleScope
from trcc.services.settings import Settings

from .test_visuals import _FakeReader

_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_OFF = "Audio Visualizer is off -- turn it on in Modules"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    """A session App with one panel and a theme, the reader scripted."""
    monkeypatch.setattr(visualizer_app, "VisualReader", _FakeReader)
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.start_session()
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    yield app
    app.close()


def _switch(app: App, on: bool) -> None:
    assert app.dispatch(SetModuleEnabled(key="visualizer", enabled=on)).ok


def _choice(app: App) -> tuple[str | None, dict[str, Any]]:
    s = app.settings.for_device(_KEY)
    return s.module_source, s.module_settings.get("visualizer", {})


def test_it_is_a_built_in_module_on_by_default(app: App) -> None:
    assert app.modules.is_on("visualizer")
    assert app.modules.manifest("visualizer").backgrounds == ("visual_background",)


def test_its_commands_are_refused_while_it_is_off(app: App) -> None:
    """In every UI the same: the App refuses them before they run.
    MUTATION CHECK: drop MODULE from PlayVisual and it reaches a module that
    is not on."""
    _switch(app, False)
    played = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    listed = app.dispatch(ListVisuals())
    assert (played.ok, played.message) == (False, _OFF)
    assert (listed.ok, listed.message) == (False, _OFF)


def test_off_ends_every_panel_it_draws_and_on_draws_them_again(app: App) -> None:
    """Switching off keeps each panel's choice (the user's decision 2), so
    on resumes it -- with its sliders.  MUTATION CHECK: drop the end_sources
    in ModuleScope.release and the visual keeps playing while off."""
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes", settings={"growth": 2.0}))
    reader: Any = app.media.source(_KEY)
    _switch(app, False)
    assert reader is not None and reader.closed
    assert app.media.source(_KEY) is None
    assert _choice(app) == ("visualizer", {"visual": "trcc-pipes", "preset": "",
                                           "settings": {"growth": 2.0}})
    _switch(app, True)
    again: Any = app.media.source(_KEY)
    assert again is not None and again is not reader
    assert dict(again.spec.settings) == {"growth": 2.0}


def test_stopping_works_while_it_is_off(app: App) -> None:
    """ClearModuleSource is the App's: it forgets the choice with the module
    off, so on brings nothing back.  MUTATION CHECK: make it a module
    Command and the App refuses it here."""
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    _switch(app, False)
    assert app.dispatch(ClearModuleSource(key=_KEY, module="visualizer")).ok
    assert _choice(app)[0] is None
    _switch(app, True)
    assert app.media.source(_KEY) is None


def _theme(root: Path, name: str) -> Path:
    folder = root / "themes" / name
    folder.mkdir(parents=True)
    (folder / "trcc.json").write_text(json.dumps(
        {"name": name, "width": 320, "height": 320, "elements": []}))
    picture = QImage(320, 320, QImage.Format.Format_RGB888)
    picture.fill(0x336699)
    picture.save(str(folder / "00.png"))
    return folder


def test_a_visual_theme_waits_for_it_and_says_so(app: App, tmp_path: Path) -> None:
    """Loaded while it is off: the theme shows (mask and readings), the
    load says what it needs, and switching it on plays the THEME's visual --
    not the panel's earlier choice.  MUTATION CHECK: drop the pending branch
    in App.resume_background and the earlier choice plays instead."""
    assert app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Start"))).ok
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes", settings={"growth": 2.0}))
    saved = app.dispatch(SaveTheme(key=_KEY, name="Viz"))
    app.dispatch(PlayVisual(key=_KEY, visual="cavalier-bars"))   # a later choice
    _switch(app, False)

    loaded = app.dispatch(LoadTheme(key=_KEY, path=Path(saved.theme_path)))

    assert (loaded.ok, loaded.needs_module) == (True, "Audio Visualizer")
    assert loaded.message.endswith("-- its background needs Audio Visualizer: "
                                   "turn it on in Modules")
    assert app.media.source(_KEY) is None
    assert app.settings.for_device(_KEY).pending_background is not None
    _switch(app, True)
    playing: Any = app.media.source(_KEY)
    assert playing is not None
    assert (playing.spec.kind, dict(playing.spec.settings)) == (
        "trcc-pipes", {"growth": 2.0})
    assert app.settings.for_device(_KEY).pending_background is None


def test_the_snapshot_names_the_module_drawing(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    snapshot = app.dispatch(LcdSnapshot(key=_KEY))
    assert (snapshot.display_source, snapshot.module_title) == (
        "module", "Audio Visualizer")


def _scope(**fields: Any) -> ModuleScope:
    manifest = ModuleManifest(key="viz", title="Viz", summary="s", **fields)
    return ModuleScope(cast("ModuleServices", object()), manifest)


def test_a_host_offers_only_what_the_manifest_declares() -> None:
    """MUTATION CHECK: drop ``_need`` from ``ModuleScope._port``."""
    scope = _scope(capabilities=("panels",))
    assert scope.panels is not None
    with pytest.raises(ModuleUnavailable, match="asked for 'background' without "
                                                "declaring it in its module.json"):
        _ = scope.background


def test_a_background_kind_must_be_declared() -> None:
    class Kind:
        prefix = "rain_background"

    scope = _scope(capabilities=("themes",), backgrounds=("visual_background",))
    with pytest.raises(ModuleUnavailable, match="draws 'rain_background' "
                                                "backgrounds without declaring"):
        scope.themes.add_kind(cast("BackgroundKind", Kind()))


def test_a_settings_file_from_before_keeps_every_panel_playing(tmp_path: Path) -> None:
    """Schema 5 -> 6: a panel's visual becomes the visualizer's own section
    and its background source that module; the centre picture moves too.
    MUTATION CHECK: drop ``_fold_visual`` and the panel loses its visual."""
    (tmp_path / "trcc.json").write_text(json.dumps({
        "schema": 5,
        "app": {"visual_center": "/pictures/bob.png", "visual_center_shown": False,
                "visual_center_fire": 0.4},
        "devices": {_KEY: {"visual": "milkdrop", "visual_preset": "a/b.milk",
                           "visual_settings": {"x": 1.0}},
                    "87ad:70db": {"background_path": "/pictures/sky.png"}},
        "led_devices": {}}), encoding="utf-8")

    s = Settings(FakePaths(tmp_path))

    playing = s.for_device(_KEY)
    assert (playing.module_source, playing.module_settings) == ("visualizer", {
        "visualizer": {"visual": "milkdrop", "preset": "a/b.milk",
                       "settings": {"x": 1.0}}})
    assert s.for_device("87ad:70db").module_source is None
    assert s.module_settings("visualizer") == {
        "center": "/pictures/bob.png", "center_shown": False, "center_fire": 0.4}
