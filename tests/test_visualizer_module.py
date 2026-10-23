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


def test_the_gui_speaks_the_window_s_language(qtbot: Any, app: App) -> None:
    """Its words come from its own i18n.json: the page's title, the slider
    controls, and its row on the Modules page -- and follow a language
    change.  MUTATION CHECK: drop ``page.set_language`` from TRCCApp's
    ``_show_language`` and the controls stay German after the switch."""
    from PySide6.QtWidgets import QLabel

    from trcc.core.commands import SetLanguage
    from trcc.ui.gui.trcc_app import TRCCApp

    app.dispatch(SetLanguage(language="de"))
    window = TRCCApp(app=app)
    qtbot.addWidget(window)
    page: Any = window._module_pages["visualizer"]

    def words() -> set[str]:
        return {w.text() for w in page.findChildren(QLabel)} | {
            w.text() for w in window.uc_modules.view.findChildren(QLabel)}

    assert {"Audio-Visualisierung", "Feuer",
            "Wähle oben eine Audio-Visualisierung – ihre Regler erscheinen hier."} <= words()
    assert "Audio-Visualisierung" in {t for t in (window._module_tabs["visualizer"]
                                                   .toolTip(),)}
    window._show_language("fr")
    assert {"Visuels audio", "Feu", "Visualiseur audio"} <= words()
    window.close()


# ── its themes are kept with it ───────────────────────────────────────

def _saved_visual(app: App, tmp_path: Path, name: str = "Viz") -> Path:
    """A theme saved while the panel plays a visual -- where it landed."""
    assert app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, f"Start{name}"))).ok
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    saved = app.dispatch(SaveTheme(key=_KEY, name=name))
    assert saved.ok, saved.message
    return Path(saved.theme_path)


def _size(app: App) -> tuple[int, int]:
    """The panel's size -- a connected panel has reported it."""
    profile = app.devices[_KEY].profile
    assert profile is not None
    return profile.resolution


def _listed(app: App) -> set[str]:
    from trcc.core.commands import ListThemes

    size = _size(app)
    return {t.name for t in app.dispatch(ListThemes(resolution=size)).themes}


def test_a_visual_theme_is_kept_with_the_module_and_listed_only_while_on(
        app: App, tmp_path: Path) -> None:
    """Saved where the module keeps its themes; off, the browser does not
    list it; on, it is back.  MUTATION CHECK: list every module's folder in
    ``user_theme_dirs`` and it shows while off."""
    folder = _saved_visual(app, tmp_path)
    w, h = _size(app)
    assert folder.parent == app.platform.paths().user_module_theme_dir(
        "visualizer", w, h)
    assert "Viz" in _listed(app)
    _switch(app, False)
    assert "Viz" not in _listed(app)
    _switch(app, True)
    assert "Viz" in _listed(app)


def test_a_plain_theme_stays_in_the_ordinary_folder(app: App, tmp_path: Path) -> None:
    assert app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Start"))).ok
    saved = app.dispatch(SaveTheme(key=_KEY, name="Plain"))
    w, h = _size(app)
    assert Path(saved.theme_path).parent == app.platform.paths().user_theme_dir(w, h)


def test_a_name_is_taken_in_either_folder_and_overwrite_keeps_one(
        app: App, tmp_path: Path) -> None:
    """The name is the user's, wherever the theme is kept: a visual theme
    named like a plain one needs the overwrite, and then there is one.
    MUTATION CHECK: drop ``same_name`` from the taken check."""
    assert app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Start"))).ok
    plain = Path(app.dispatch(SaveTheme(key=_KEY, name="Mine")).theme_path)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    refused = app.dispatch(SaveTheme(key=_KEY, name="Mine"))
    assert (refused.ok, refused.target_exists) == (False, True)
    kept = Path(app.dispatch(SaveTheme(key=_KEY, name="Mine", overwrite=True)).theme_path)
    assert kept != plain and kept.is_dir() and not plain.exists()


def test_a_module_theme_is_found_by_name(app: App, tmp_path: Path) -> None:
    """Exports look where the user's themes are kept -- a module's too."""
    from trcc.core.commands import ExportTheme

    _saved_visual(app, tmp_path)
    exported = app.dispatch(ExportTheme(key=_KEY, theme_name="Viz",
                                        archive_path=tmp_path / "viz.zip"))
    assert exported.ok, exported.message
    assert (tmp_path / "viz.zip").is_file()


def test_the_api_loads_a_module_theme(app: App, tmp_path: Path) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    folder = _saved_visual(app, tmp_path)
    client = loopback_client(build_app(app))
    loaded = client.post(f"/devices/{_KEY}/display/theme", json={"path": str(folder)})
    assert loaded.status_code == 200, loaded.text


def test_switched_on_mid_session_its_tab_plays_on_the_panel(
        qtbot: Any, app: App) -> None:
    """His find on real glass (2026-10-22): the visualizer switched on from
    the Modules page -- no panel active there -- then back to the panel, a
    tile clicked: "no panel to play on -- dropped".  The tab was added
    without the panel, and coming back to the same panel never told it.
    MUTATION CHECK: drop the module-page call from ``_activate_device``."""
    from trcc.modules.visualizer.ui.presentation.visual_gallery import VisualItem
    from trcc.ui.gui.trcc_app import TRCCApp

    _switch(app, False)
    window = TRCCApp(app=app)
    qtbot.addWidget(window)
    window.replay_initial_devices()
    window._show_view("modules")
    _switch(app, True)
    qtbot.waitUntil(lambda: "visualizer" in window._module_pages, timeout=3000)
    window._activate_device(_KEY)
    page: Any = window._module_pages["visualizer"]
    page._on_play(VisualItem(visual="trcc-pipes", preset="", name="Pipes"))
    assert _choice(app)[0] == "visualizer"


def test_a_switch_re_lists_the_themes_in_every_window(qtbot: Any, app: App) -> None:
    """MUTATION CHECK: drop ModuleChanged -> themes_changed in the bridge."""
    from trcc.ui.bus_bridge import BusBridge

    bridge = BusBridge(app.events)
    with qtbot.waitSignal(bridge.themes_changed, timeout=2000):
        _switch(app, False)


# ── the one-time move of the themes saved before it was a module ─────

def _old_theme(folder: Path, name: str, background: str | None = None) -> Path:
    """A saved theme as the ordinary folder kept it."""
    theme = folder / name
    theme.mkdir(parents=True)
    config: dict[str, Any] = {"name": name, "width": 320, "height": 320,
                              "elements": []}
    if background:
        config["background"] = background
    (theme / "trcc.json").write_text(json.dumps(config))
    return theme


def _old_layout(root: Path) -> tuple[Path, Path]:
    """A user's data from before: a plain theme and a visual one in the
    ordinary folder, a theme of the visual one's name already kept with the
    module, and the panel showing the visual one."""
    data = root / "user" / "data"
    stored = data / "visual_background" / "x"
    stored.mkdir(parents=True)
    (stored / "config.json").write_text(json.dumps({"visual": "trcc-pipes"}))
    ordinary = data / "theme320320"
    _old_theme(ordinary, "Plain")
    viz = _old_theme(ordinary, "Viz", "visual_background/x")
    _old_theme(data / "modules" / "visualizer" / "theme320320", "Viz")
    shown = str(viz.resolve())
    (root / "trcc.json").write_text(json.dumps({
        "schema": 6, "app": {}, "led_devices": {},
        "devices": {_KEY: {"current_theme": shown, "orientation_slots": {
            "theme320320": {"current_theme": shown}}}}}), encoding="utf-8")
    return ordinary, data / "modules" / "visualizer" / "theme320320"


def test_the_themes_saved_before_move_once_and_the_panel_follows(
        tmp_path: Path) -> None:
    """The visual theme moves -- as "Viz (2)": nothing is overwritten -- the
    plain one stays, and every saved path to the moved one follows it.  A
    second start moves nothing, even a visual theme that appeared since.
    MUTATION CHECK: drop ``rename_theme_path`` and the panel still points
    at the old folder (it would load Theme1 and drop its visual)."""
    ordinary, kept = _old_layout(tmp_path)

    first = App(MockPlatform([_SPEC], tmp_path))
    moved = kept / "Viz (2)"
    s = first.settings.for_device(_KEY)
    assert moved.is_dir() and (kept / "Viz").is_dir()
    assert (ordinary / "Plain").is_dir() and not (ordinary / "Viz").exists()
    assert s.current_theme == str(moved.resolve())
    assert s.orientation_slots["theme320320"].current_theme == str(moved.resolve())
    first.close()

    _old_theme(ordinary, "Later", "visual_background/x")
    second = App(MockPlatform([_SPEC], tmp_path))
    assert (ordinary / "Later").is_dir()
    second.close()


def test_a_theme_that_cannot_move_stays_where_it_was(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    from trcc.adapters.theme.filesystem import FileContentStore

    ordinary, _kept = _old_layout(tmp_path)

    def refuse(self: Any, theme_dir: Path, into: Path) -> Path:
        raise PermissionError("read-only")
    monkeypatch.setattr(FileContentStore, "move_theme", refuse)
    with caplog.at_level("WARNING", logger="trcc.services.migration"):
        app = App(MockPlatform([_SPEC], tmp_path))
    assert (ordinary / "Viz").is_dir()
    assert app.settings.for_device(_KEY).current_theme == str(
        (ordinary / "Viz").resolve())
    assert any("could not move" in r.getMessage() for r in caplog.records)
    app.close()
