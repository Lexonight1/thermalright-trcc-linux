"""Display-tweak Commands: SetFitMode / EnableOverlay / SetSplitMode.

Each is a per-device settings write + cache invalidation + EventBus
publish. Validation:
  * SetFitMode parses "width" | "height" | "stretch" into the FitMode
    enum, rejects anything else.
  * EnableOverlay is a straight bool.
  * SetSplitMode accepts 0, 1, 2, or 3; rejects everything else.

Scene-cache invalidation only happens when a renderer is wired into
the App; pure settings writes still succeed otherwise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import (
    EnableOverlay,
    SetFitMode,
    SetSplitMode,
)
from trcc.core.events import (
    FitModeChanged,
    OverlayChanged,
    SplitModeChanged,
)
from trcc.core.models import FitMode

from .conftest import FakePlatform

_KEY = "0402:3922"


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


# ── SetFitMode ────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode_str,expected_enum", [
    ("width", FitMode.WIDTH),
    ("height", FitMode.HEIGHT),
    ("stretch", FitMode.STRETCH),
])
def test_set_fit_mode_accepts_valid(
    app: App, mode_str: str, expected_enum: FitMode,
) -> None:
    result = app.dispatch(SetFitMode(key=_KEY, mode=mode_str))

    assert result.ok is True
    assert result.mode == expected_enum.value
    assert app.settings.for_device(_KEY).fit_mode is expected_enum


@pytest.mark.parametrize("bad", ["", "fit", "WIDTH", "stretch ", "fill"])
def test_set_fit_mode_rejects_invalid(app: App, bad: str) -> None:
    result = app.dispatch(SetFitMode(key=_KEY, mode=bad))

    assert result.ok is False
    assert "must be one of" in result.message
    # Default unchanged
    assert app.settings.for_device(_KEY).fit_mode is FitMode.WIDTH


def test_set_fit_mode_publishes_event(app: App) -> None:
    events: list[FitModeChanged] = []
    app.events.subscribe(FitModeChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetFitMode(key=_KEY, mode="stretch"))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].mode == "stretch"


def test_set_fit_mode_rejection_does_not_publish(app: App) -> None:
    events: list[FitModeChanged] = []
    app.events.subscribe(FitModeChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetFitMode(key=_KEY, mode="garbage"))

    assert events == []


# ── EnableOverlay ─────────────────────────────────────────────────────


@pytest.mark.parametrize("state", [True, False])
def test_enable_overlay_persists_state(app: App, state: bool) -> None:
    result = app.dispatch(EnableOverlay(key=_KEY, enabled=state))

    assert result.ok is True
    assert result.enabled is state
    assert app.settings.for_device(_KEY).overlay_enabled is state


def test_enable_overlay_message_uses_enabled_disabled_wording(app: App) -> None:
    r_on = app.dispatch(EnableOverlay(key=_KEY, enabled=True))
    r_off = app.dispatch(EnableOverlay(key=_KEY, enabled=False))

    assert "enabled" in r_on.message
    assert "disabled" in r_off.message


def test_enable_overlay_publishes_event(app: App) -> None:
    events: list[OverlayChanged] = []
    app.events.subscribe(OverlayChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(EnableOverlay(key=_KEY, enabled=False))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].enabled is False


# ── SetSplitMode ──────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_set_split_mode_accepts_valid(app: App, mode: int) -> None:
    result = app.dispatch(SetSplitMode(key=_KEY, mode=mode))

    assert result.ok is True
    assert result.mode == mode
    assert app.settings.for_device(_KEY).split_mode == mode


@pytest.mark.parametrize("bad", [-1, 4, 5, 99, 100])
def test_set_split_mode_rejects_invalid(app: App, bad: int) -> None:
    result = app.dispatch(SetSplitMode(key=_KEY, mode=bad))

    assert result.ok is False
    assert "0 (off), 1, 2, or 3" in result.message
    # Default unchanged
    assert app.settings.for_device(_KEY).split_mode == 0


def test_set_split_mode_message_when_disabled(app: App) -> None:
    result = app.dispatch(SetSplitMode(key=_KEY, mode=0))

    assert result.ok is True
    assert "disabled" in result.message


def test_set_split_mode_publishes_event(app: App) -> None:
    events: list[SplitModeChanged] = []
    app.events.subscribe(SplitModeChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetSplitMode(key=_KEY, mode=2))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].mode == 2


# ── Cross-Command isolation: each touches its own setting ───────────


def test_each_setting_independent_per_device(app: App) -> None:
    """Setting fit_mode on one device doesn't leak into another."""
    app.dispatch(SetFitMode(key="0402:3922", mode="stretch"))
    app.dispatch(SetFitMode(key="0416:5302", mode="height"))

    assert app.settings.for_device("0402:3922").fit_mode is FitMode.STRETCH
    assert app.settings.for_device("0416:5302").fit_mode is FitMode.HEIGHT


# ── Persistence ──────────────────────────────────────────────────────


def test_display_tweaks_persist_across_app_restart(
    app: App, tmp_home: Path,
) -> None:
    app.dispatch(SetFitMode(key=_KEY, mode="stretch"))
    app.dispatch(EnableOverlay(key=_KEY, enabled=False))
    app.dispatch(SetSplitMode(key=_KEY, mode=2))

    app2 = App(platform=FakePlatform(tmp_home))

    dev = app2.settings.for_device(_KEY)
    assert dev.fit_mode is FitMode.STRETCH
    assert dev.overlay_enabled is False
    assert dev.split_mode == 2


# ── the Dynamic Island, rendered for real (#149) ───────────────────────────
#
# Every renderer fake in this suite stubbed ``flip_horizontal``, so the real
# ``QImage.mirrored(horizontal=...)`` call — a keyword PySide6 rejects — never
# ran, and every split-mode frame on a SUB 3 panel raised in the field.  These
# render through the REAL QtRenderer on a mock Levita.


def _levita(tmp_path: Path, sub: int):
    """A connected 1600x720 panel (87ad:70db PM 64) showing a white image."""
    from PySide6.QtGui import QColor, QImage  # type: ignore[import-not-found]

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import LoadImage

    from .mock_platform import MockPlatform

    spec = {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 64, "sub": sub}
    levita = App(platform=MockPlatform([spec], tmp_path), renderer=QtRenderer())
    levita.discover_and_connect()
    image = QImage(1600, 720, QImage.Format.Format_RGB888)
    image.fill(QColor(255, 255, 255))
    image.save(str(tmp_path / "white.png"))
    levita.dispatch(LoadImage(key="87ad:70db", path=tmp_path / "white.png"))
    return levita


def _island_assets(levita: App, mode: int) -> list[str]:
    """Build one frame in split ``mode`` and return the island asset(s) loaded."""
    loaded: list[str] = []
    real = levita.display._load_split_asset

    def spy(name: str):
        loaded.append(name)
        return real(name)

    levita.display._load_split_asset = spy       # type: ignore[method-assign]
    levita.dispatch(SetSplitMode(key="87ad:70db", mode=mode))
    device = levita.devices["87ad:70db"]
    frame = levita.display.build_frame(
        info=device.info, theme=levita.active_themes["87ad:70db"],
        sensors={}, profile=device.profile)
    assert frame, f"split mode {mode} produced no frame"
    return loaded


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_every_split_mode_renders_on_a_sub3_panel(tmp_path: Path, mode: int) -> None:
    """The freeze: each of these raised AttributeError, so nothing was sent."""
    assert _island_assets(_levita(tmp_path, sub=3), mode)


@pytest.mark.parametrize(("sub", "mode", "asset"), [
    (3, 2, "split_overlay_b_180.png"),   # the C#'s SUB 3 arm: 180° round
    (1, 2, "split_overlay_b.png"),       # any other SUB: as authored
    (3, 1, "split_overlay_a.png"),       # only style 2 has the arm
    (3, 3, "split_overlay_c.png"),
])
def test_the_island_asset_follows_the_csharp_sub3_rule(
    tmp_path: Path, sub: int, mode: int, asset: str,
) -> None:
    """UCScreenImage.cs: ``myLddVal == 2 && myLddValSub == 3`` draws the 180°
    asset at 0°; ``myLddValSub = pmSub`` (FormCZTV.cs:893).  It never mirrors."""
    assert _island_assets(_levita(tmp_path, sub=sub), mode) == [asset]
