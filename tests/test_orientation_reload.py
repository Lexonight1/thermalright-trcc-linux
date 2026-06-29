"""``App._on_orientation_changed`` — reload the active theme + mask from the
orientation-keyed resolution dir on rotation.

Ports the C# rotation handler (``UpDateUCComboBox1``: recompute the
resolution-keyed theme dir → reload the theme), which the cutover dropped.
Non-square panels keep theme / web-mask catalogs per oriented resolution
(``theme1280480`` ↔ ``theme4801280``, ``web/zt1280480`` ↔ ``web/zt4801280``);
the active content must follow a rotation, theme first then the user mask.

These tests isolate the observer: ``app.dispatch`` is spied so we assert which
reload Commands fire with which rotated paths, not LoadTheme/ApplyMask internals
(covered by their own suites).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.app import App
from trcc.core.commands import ApplyMask, LoadTheme
from trcc.core.events import OrientationChanged

from .conftest import FakePlatform

_KEY = "0402:3922"
_NAME = "Theme1"
_MASK_ID = "001a"


def _seed(dir_path: Path, *files: str) -> Path:
    """Create *dir_path* with placeholder *files* and return it."""
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in files:
        (dir_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return dir_path


@pytest.fixture
def app(tmp_home: Path) -> App:
    a = App(platform=FakePlatform(tmp_home))
    # A connected non-square 1280x480 device — only ``profile.resolution`` is
    # read by the observer, so a minimal stand-in is enough.
    a.devices[_KEY] = SimpleNamespace(  # type: ignore[assignment]
        profile=SimpleNamespace(resolution=(1280, 480)),
    )
    return a


@pytest.fixture
def spy(app: App) -> list[Any]:
    """Record dispatched Commands without executing them."""
    calls: list[Any] = []

    def _record(cmd: Any) -> None:
        calls.append(cmd)
        return None

    app.dispatch = _record  # type: ignore[method-assign]
    return calls


def _data(app: App) -> Path:
    return app.platform.paths().data_dir()


# ── oriented_theme_path — shared resolver (RestoreLastTheme + rotation) ──

def test_oriented_theme_path_picks_portrait_for_rotated_degrees(app: App) -> None:
    """Landscape stored path + explicit 90/270 degrees → portrait variant
    (the RestoreLastTheme connect-restore fix path)."""
    from trcc.core.commands._helpers import oriented_theme_path
    land = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    port = _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    assert oriented_theme_path(app, _KEY, land, degrees=270) == port


def test_oriented_theme_path_reads_settings_orientation_when_unset(
    app: App,
) -> None:
    """No explicit degrees → uses the device's persisted orientation, so
    RestoreLastTheme (after _restore_rotation) picks the matching dir."""
    from trcc.core.commands._helpers import oriented_theme_path
    land = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    port = _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    app.settings.set_orientation(_KEY, 90)
    assert oriented_theme_path(app, _KEY, land) == port


def test_oriented_theme_path_landscape_at_zero(app: App) -> None:
    from trcc.core.commands._helpers import oriented_theme_path
    land = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    assert oriented_theme_path(app, _KEY, land, degrees=0) == land


def test_oriented_theme_path_falls_back_when_variant_absent(app: App) -> None:
    """No portrait dir on disk → keep the stored path (renderer pixel-rotates)."""
    from trcc.core.commands._helpers import oriented_theme_path
    land = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    assert oriented_theme_path(app, _KEY, land, degrees=270) == land


def test_oriented_theme_path_preserves_user_tree_over_shipped(app: App) -> None:
    """A user-saved theme + a shipped theme share a name (they coexist).
    Re-rooting must keep the USER's tree, not swap to the shipped same-name
    theme — else restart/restore loses the user's last preview (the reported
    bug: shipped-first resolver clobbered the user selection)."""
    from trcc.core.commands._helpers import oriented_theme_path
    paths = app.platform.paths()
    _seed(paths.theme_dir(1280, 480) / _NAME, "00.png")                   # shipped
    user = _seed(paths.user_theme_dir(1280, 480) / _NAME, "00.png")       # user-saved
    assert oriented_theme_path(app, _KEY, user, degrees=0) == user


def test_oriented_theme_path_preserves_user_tree_when_rotated(app: App) -> None:
    """Rotating a user-saved theme picks the USER portrait variant, even when a
    shipped portrait theme of the same name also exists."""
    from trcc.core.commands._helpers import oriented_theme_path
    paths = app.platform.paths()
    land_user = _seed(paths.user_theme_dir(1280, 480) / _NAME, "00.png")
    port_user = _seed(paths.user_theme_dir(480, 1280) / _NAME, "00.png")
    _seed(paths.theme_dir(480, 1280) / _NAME, "00.png")                   # shipped portrait
    assert oriented_theme_path(app, _KEY, land_user, degrees=270) == port_user


# ── mask reload ─────────────────────────────────────────────────────────


def test_rotation_reloads_mask_to_portrait_variant(
    app: App, spy: list[Any],
) -> None:
    land = _seed(_data(app) / "web" / "zt1280480" / _MASK_ID, "01.png")
    port = _seed(_data(app) / "web" / "zt4801280" / _MASK_ID, "01.png")
    app.settings.set_mask_path(_KEY, str(land / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    masks = [c for c in spy if isinstance(c, ApplyMask)]
    assert len(masks) == 1
    assert masks[0].path == port            # cloud_mask_dir(480, 1280)/001a
    assert masks[0].key == _KEY


def test_rotation_back_to_landscape_reloads_landscape_mask(
    app: App, spy: list[Any],
) -> None:
    _seed(_data(app) / "web" / "zt1280480" / _MASK_ID, "01.png")
    port = _seed(_data(app) / "web" / "zt4801280" / _MASK_ID, "01.png")
    app.settings.set_mask_path(_KEY, str(port / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=0))

    masks = [c for c in spy if isinstance(c, ApplyMask)]
    assert len(masks) == 1
    assert masks[0].path == _data(app) / "web" / "zt1280480" / _MASK_ID


def test_no_portrait_mask_variant_no_reload(app: App, spy: list[Any]) -> None:
    land = _seed(_data(app) / "web" / "zt1280480" / _MASK_ID, "01.png")
    # NO zt4801280 variant on disk → fall back (renderer pixel-rotates).
    app.settings.set_mask_path(_KEY, str(land / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    assert [c for c in spy if isinstance(c, ApplyMask)] == []


def test_hidden_mask_not_reloaded(app: App, spy: list[Any]) -> None:
    land = _seed(_data(app) / "web" / "zt1280480" / _MASK_ID, "01.png")
    _seed(_data(app) / "web" / "zt4801280" / _MASK_ID, "01.png")
    app.settings.set_mask_path(_KEY, str(land / "01.png"))
    app.settings.set_mask_visible(_KEY, False)   # not visible → leave it

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    assert [c for c in spy if isinstance(c, ApplyMask)] == []


def test_user_uploaded_mask_not_reloaded(app: App, tmp_home: Path,
                                         spy: list[Any]) -> None:
    """A native-res user mask (not under web/zt*) has no per-orientation
    variant — it must be left alone on rotation."""
    user_mask = _seed(tmp_home / "my_masks", "01.png")
    app.settings.set_mask_path(_KEY, str(user_mask / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    assert [c for c in spy if isinstance(c, ApplyMask)] == []


# ── theme reload ────────────────────────────────────────────────────────


def test_rotation_reloads_theme_to_portrait_variant(
    app: App, spy: list[Any],
) -> None:
    land = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    port = _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    # LoadTheme stores current_theme as the loaded theme's absolute path.
    app.settings.set_current_theme(_KEY, str(land.resolve()))

    app.events.publish(OrientationChanged(key=_KEY, degrees=270))

    themes = [c for c in spy if isinstance(c, LoadTheme)]
    assert len(themes) == 1
    assert themes[0].path == port           # theme_dir(480, 1280)/Theme1


def test_theme_already_in_rotated_dir_no_reload(
    app: App, spy: list[Any],
) -> None:
    port = _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    # Already loaded from the portrait dir → rotating to portrait is a no-op.
    app.settings.set_current_theme(_KEY, str(port.resolve()))

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    assert [c for c in spy if isinstance(c, LoadTheme)] == []


# ── ordering + guards ───────────────────────────────────────────────────


def test_theme_reloads_before_mask(app: App, spy: list[Any]) -> None:
    """Theme first, then the user mask — so the explicitly-applied mask wins
    over the theme's bundled one (LoadTheme re-applies the bundled mask)."""
    land_t = _seed(_data(app) / "theme1280480" / _NAME, "00.png")
    _seed(_data(app) / "theme4801280" / _NAME, "00.png")
    land_m = _seed(_data(app) / "web" / "zt1280480" / _MASK_ID, "01.png")
    _seed(_data(app) / "web" / "zt4801280" / _MASK_ID, "01.png")
    app.settings.set_current_theme(_KEY, str(land_t.resolve()))
    app.settings.set_mask_path(_KEY, str(land_m / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    kinds = [type(c).__name__ for c in spy]
    assert "LoadTheme" in kinds and "ApplyMask" in kinds
    assert kinds.index("LoadTheme") < kinds.index("ApplyMask")


def test_disconnected_device_no_reload(app: App, spy: list[Any]) -> None:
    app.devices.pop(_KEY, None)
    _seed(_data(app) / "web" / "zt4801280" / _MASK_ID, "01.png")
    app.settings.set_mask_path(
        _KEY, str(_data(app) / "web" / "zt1280480" / _MASK_ID / "01.png"))
    app.settings.set_mask_visible(_KEY, True)

    app.events.publish(OrientationChanged(key=_KEY, degrees=90))

    assert spy == []
