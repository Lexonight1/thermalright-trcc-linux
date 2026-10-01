"""The screencast region is per-device App state, seeded from the theme.

The C# keeps JpX/JpY/JpW/JpH (+ myYcbk, hide the frame) in every theme DC: it
reads them on every theme and mask load (FormCZTV.cs:6805), edits them from
the X/Y/W/H fields, captures them with its own axis rule, and writes them
back on save (:7290).  We parsed them and dropped them, so a gui Start without
typed numbers sent 0x0 -- while 0 of the 3411 theme DCs on disk are 0x0.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeMic
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ApplyMask,
    ConnectDevice,
    ExportDcTheme,
    LcdSnapshot,
    LoadTheme,
    SaveTheme,
    SetOrientation,
    SetScreencastRegion,
    StartScreencast,
)
from trcc.core.events import ScreencastRegionChanged
from trcc.core.geometry import oriented_canvas, screencast_axes
from trcc.core.protocol import FBL_PROFILES, get_profile
from trcc.services import _dc as Dc

_KEY = "87ad:70db"
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db", "resolution": "854x480",
         "pm": 11, "sub": 5}


# ── The C#'s axis rule (FormCZTV.cs:3100-3545) ───────────────────────

_NATIVES = sorted({p.resolution for p in FBL_PROFILES.values()})


@pytest.mark.parametrize("native", _NATIVES)
@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
def test_the_box_follows_the_csharp_rule(native: tuple[int, int], orientation: int) -> None:
    """(JpH, JpW) on a square panel always and on any panel at 0/180;
    (JpW, JpH) only for a non-square panel at 90/270."""
    swapped = native[0] == native[1] or orientation in (0, 180)
    box = screencast_axes((1, 2, 30, 40), native, orientation)

    assert box == ((1, 2, 40, 30) if swapped else (1, 2, 30, 40))
    assert screencast_axes(box, native, orientation) == (1, 2, 30, 40), "not its own inverse"


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
def test_the_box_and_the_canvas_it_fills_share_a_shape(fbl: int, orientation: int) -> None:
    """The C# sizes its canvas (GIFSize) to the capture box, so a region is
    never letterboxed into a canvas of the other shape.  Ours disagreed on 7
    of 20 panels until 2026-09-30 -- the base-90 family at 90/270 and FBL 60
    at every angle."""
    profile = get_profile(fbl)
    cw, ch = oriented_canvas(profile, orientation)
    *_, bw, bh = screencast_axes((0, 0, 240, 320), profile.resolution, orientation)

    assert cw == ch or (cw > ch) == (bw > bh), (
        f"fbl {fbl} at {orientation}: canvas {cw}x{ch}, capture box {bw}x{bh}")


# ── The App ───────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    tasks: list[str] = []
    monkeypatch.setattr(App, "add_task", lambda self, task: tasks.append(task.key))
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.audio = FakeMic()                        # type: ignore[assignment]
    app.tasks = tasks                            # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.devices[_KEY].profile.resolution == (854, 480)
    # This SKU is portrait-MOUNTED, so the App starts it at 90 degrees; the
    # expectations below are written for 0.
    assert app.dispatch(SetOrientation(key=_KEY, degrees=0)).ok
    yield app
    app.close()


def _box(app: App) -> Any:
    return app.dispatch(LcdSnapshot(key=_KEY)).screencast_rect


def _theme(tmp_path: Path, name: str, **config: Any) -> Path:
    from PySide6.QtGui import QImage
    folder = tmp_path / name
    folder.mkdir()
    (folder / "trcc.json").write_text(json.dumps(
        {"name": name, "width": 854, "height": 480, "elements": [], **config}),
        encoding="utf-8")
    img = QImage(854, 480, QImage.Format.Format_RGB888)
    img.fill(0x203040)
    img.save(str(folder / "00.png"))
    return folder


def test_a_new_device_shows_the_csharp_default(app: App) -> None:
    """JpW 240 x JpH 320, captured long-side across at 0 degrees."""
    assert _box(app) == (0, 0, 320, 240)
    assert app.dispatch(LcdSnapshot(key=_KEY)).screencast_hide_border is True


def test_setting_the_region_stores_it_in_the_dcs_axes(app: App) -> None:
    heard: list[Any] = []
    app.events.subscribe(ScreencastRegionChanged, heard.append)

    assert app.dispatch(SetScreencastRegion(key=_KEY, x=10, y=20, w=480, h=270)).ok

    assert app.settings.for_device(_KEY).screencast_rect == (10, 20, 270, 480)
    assert _box(app) == (10, 20, 480, 270)
    assert [(e.x, e.y, e.w, e.h, e.hide_border) for e in heard] == [(10, 20, 480, 270, True)]


def test_rotating_shows_the_same_region_turned(app: App) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=10, y=20, w=480, h=270))
    app.dispatch(SetOrientation(key=_KEY, degrees=90))

    assert _box(app) == (10, 20, 270, 480)


def test_hide_border_is_stored_and_none_keeps_it(app: App) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=0, y=0, w=320, h=240, hide_border=False))
    app.dispatch(SetScreencastRegion(key=_KEY, x=5, y=0, w=320, h=240))

    assert app.settings.for_device(_KEY).screencast_hide_border is False


@pytest.mark.parametrize("box", [(-1, 0, 10, 10), (0, 0, 10000, 10)])
def test_out_of_range_values_change_nothing(app: App, box: tuple[int, ...]) -> None:
    assert not app.dispatch(SetScreencastRegion(key=_KEY, x=box[0], y=box[1],
                                                w=box[2], h=box[3])).ok
    assert app.settings.for_device(_KEY).screencast_rect is None


def test_a_running_cast_moves_without_restarting(app: App) -> None:
    assert app.dispatch(StartScreencast(key=_KEY, x=0, y=0, w=320, h=240, audio=True)).ok
    started = list(app.tasks)                    # type: ignore[attr-defined]

    app.dispatch(SetScreencastRegion(key=_KEY, x=50, y=60, w=320, h=240))

    assert app.settings.for_device(_KEY).screencast_region == (50, 60, 320, 240, True)
    assert app.tasks == started, "the cast was restarted"  # type: ignore[attr-defined]


def test_setting_the_region_never_starts_a_cast(app: App) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=50, y=60, w=320, h=240))

    assert app.settings.for_device(_KEY).screencast_region is None


def test_start_with_no_region_casts_the_stored_one(app: App) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=7, y=8, w=480, h=270))

    result = app.dispatch(StartScreencast(key=_KEY))

    assert result.ok, result.message
    assert app.settings.for_device(_KEY).screencast_region == (7, 8, 480, 270, False)


def test_start_with_a_region_stores_it(app: App) -> None:
    """The C# has ONE Jp*: what was cast is what a save keeps."""
    app.dispatch(StartScreencast(key=_KEY, x=1, y=2, w=480, h=270))

    assert _box(app) == (1, 2, 480, 270)


def test_start_refuses_a_stored_empty_region(app: App) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=0, y=0, w=0, h=0))

    assert not app.dispatch(StartScreencast(key=_KEY)).ok


def test_a_settings_reload_keeps_it(app: App, tmp_path: Path) -> None:
    from trcc.services.settings import Settings
    app.dispatch(SetScreencastRegion(key=_KEY, x=10, y=20, w=480, h=270, hide_border=False))

    reloaded = Settings(app.platform.paths()).for_device(_KEY)

    assert reloaded.screencast_rect == (10, 20, 270, 480), "came back as a list"
    assert reloaded.screencast_hide_border is False


def test_storing_the_region_leaves_the_background_alone(app: App) -> None:
    app.settings.set_background_path(_KEY, "/x/bg.png")
    app.dispatch(SetScreencastRegion(key=_KEY, x=10, y=20, w=480, h=270))

    assert app.settings.for_device(_KEY).background_path == "/x/bg.png"


# ── Seeding, as the C# reads Jp* from every DC it loads ──────────────


def test_a_theme_seeds_the_region(app: App, tmp_path: Path) -> None:
    theme = _theme(tmp_path, "T", screencast_rect=[3, 4, 270, 480],
                   screencast_border=False)

    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok

    assert _box(app) == (3, 4, 480, 270)
    assert app.settings.for_device(_KEY).screencast_hide_border is False


def test_a_theme_without_a_region_keeps_the_devices(app: App, tmp_path: Path) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=10, y=20, w=480, h=270))
    for name, extra in (("Bare", {}), ("Empty", {"screencast_rect": [0, 0, 0, 0]})):
        app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, name, **extra)))

    assert _box(app) == (10, 20, 480, 270)


def test_a_restore_keeps_the_users_edit(app: App, tmp_path: Path) -> None:
    theme = _theme(tmp_path, "T", screencast_rect=[3, 4, 270, 480])
    app.dispatch(LoadTheme(key=_KEY, path=theme))
    app.dispatch(SetScreencastRegion(key=_KEY, x=99, y=0, w=480, h=270))

    app.dispatch(LoadTheme(key=_KEY, path=theme, reset_overrides=False))

    assert _box(app) == (99, 0, 480, 270)


def test_a_mask_seeds_the_region(app: App, tmp_path: Path) -> None:
    from PySide6.QtGui import QImage
    mask = tmp_path / "M"
    mask.mkdir()
    QImage(854, 480, QImage.Format.Format_ARGB32).save(str(mask / "01.png"))
    Dc.File(mask / "config1.dc").write(
        {"elements": [], "screencast_rect": [5, 6, 270, 480], "screencast_border": False})
    app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "T")))

    assert app.dispatch(ApplyMask(key=_KEY, path=mask)).ok

    assert _box(app) == (5, 6, 480, 270)


# ── Writing it back ───────────────────────────────────────────────────


def test_save_writes_the_devices_region_not_the_themes(app: App, tmp_path: Path) -> None:
    app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "T",
                                                screencast_rect=[3, 4, 270, 480])))
    app.dispatch(SetScreencastRegion(key=_KEY, x=11, y=12, w=480, h=270, hide_border=False))

    result = app.dispatch(SaveTheme(key=_KEY, name="Mine"))

    assert result.ok, result.message
    saved = json.loads(Path(result.theme_path, "trcc.json").read_text(encoding="utf-8"))
    assert (saved["screencast_rect"], saved["screencast_border"]) == ([11, 12, 270, 480], False)


def test_export_writes_the_live_region_not_the_saved_one(app: App, tmp_path: Path) -> None:
    """Edited AFTER the save: the C#'s export writes the live Jp* (:7433)."""
    app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "T")))
    app.dispatch(SetScreencastRegion(key=_KEY, x=11, y=12, w=480, h=270))
    saved = app.dispatch(SaveTheme(key=_KEY, name="Mine"))
    app.dispatch(SetScreencastRegion(key=_KEY, x=33, y=44, w=480, h=270))
    out = tmp_path / "out.dc"

    assert app.dispatch(ExportDcTheme(key=_KEY, theme_name="Mine", output_path=out)).ok, saved.message

    assert Dc.File(out).read()["screencast_rect"] == [33, 44, 270, 480]


def test_a_user_mask_keeps_the_region_it_was_saved_with(app: App) -> None:
    """``persist_user_mask_dc`` wrote the codec default, so re-applying a user
    mask would have reset the region the user set."""
    from trcc.core.commands._helpers import overlay_elements_to_dc, screencast_dc_flags
    app.dispatch(SetScreencastRegion(key=_KEY, x=11, y=12, w=480, h=270))

    dc = overlay_elements_to_dc([], allow_empty=True,
                                flags=screencast_dc_flags(app.settings.for_device(_KEY)))

    assert dc is not None
    path = Path(app.platform.paths().config_dir()) / "probe.dc"
    path.write_bytes(dc)
    assert Dc.File(path).read()["screencast_rect"] == [11, 12, 270, 480]


# ── The CLI and API reach the same Command ────────────────────────────


@pytest.fixture
def api(app: App) -> Iterator[Any]:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app
    with loopback_client(build_app(trcc=app)) as client:
        yield client


def test_the_api_sets_the_region(app: App, api: Any) -> None:
    response = api.post(f"/devices/{_KEY}/display/screencast/region",
                        json={"x": 7, "y": 8, "w": 480, "h": 270, "hide_border": False})

    assert response.status_code == 200, response.text
    snap = app.dispatch(LcdSnapshot(key=_KEY))
    assert (snap.screencast_rect, snap.screencast_hide_border) == ((7, 8, 480, 270), False)


def test_an_api_start_without_a_region_casts_the_stored_one(app: App, api: Any) -> None:
    app.dispatch(SetScreencastRegion(key=_KEY, x=7, y=8, w=480, h=270))

    response = api.post(f"/devices/{_KEY}/display/screencast/start", json={})

    assert response.status_code == 200, response.text
    assert app.settings.for_device(_KEY).screencast_region == (7, 8, 480, 270, False)


def test_the_api_refuses_a_region_past_the_csharp_fields(app: App, api: Any) -> None:
    response = api.post(f"/devices/{_KEY}/display/screencast/region",
                        json={"x": 0, "y": 0, "w": 10000, "h": 270})

    assert response.status_code == 422
    assert _box(app) == (0, 0, 320, 240), "the default was replaced"


@pytest.mark.parametrize(("degrees", "canvas"), [
    (0, (854, 480)), (90, (480, 854)), (180, (854, 480)), (270, (480, 854))])
def test_the_snapshot_names_the_canvas_a_cast_fills(
        app: App, degrees: int, canvas: tuple[int, int]) -> None:
    """The aspect every UI locks a region to.  It turns with the orientation,
    which the native size every UI used to lock to does not."""
    app.dispatch(SetOrientation(key=_KEY, degrees=degrees))

    assert app.dispatch(LcdSnapshot(key=_KEY)).screencast_canvas == canvas
