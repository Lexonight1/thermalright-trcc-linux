"""Mask Commands: ApplyMask / SetMaskPosition / SetMaskVisible.

Each writes to per-device settings + publishes a typed event. ApplyMask
adds path validation (must exist, be a file, have an image extension);
SetMaskPosition validates non-negative coordinates and supports clearing
to None; SetMaskVisible is a straight bool toggle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ApplyMask, SetMaskPosition, SetMaskVisible
from trcc.core.events import (
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
)

from .conftest import FakePlatform

_KEY = "0402:3922"


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


@pytest.fixture
def mask_png(tmp_home: Path) -> Path:
    """A valid mask file the ApplyMask Command can resolve."""
    path = tmp_home / "test_mask.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")   # PNG magic, content unused
    return path


# ── ApplyMask ─────────────────────────────────────────────────────────


def test_apply_mask_accepts_valid_image(app: App, mask_png: Path) -> None:
    result = app.dispatch(ApplyMask(key=_KEY, path=mask_png))

    assert result.ok is True
    # Path is stored resolved (absolute)
    assert result.path == str(mask_png.resolve())
    assert app.settings.for_device(_KEY).mask_path == str(mask_png.resolve())


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".bmp", ".webp"])
def test_apply_mask_accepts_image_extensions(
    app: App, tmp_home: Path, ext: str,
) -> None:
    path = tmp_home / f"mask{ext}"
    path.write_bytes(b"x")
    result = app.dispatch(ApplyMask(key=_KEY, path=path))
    assert result.ok is True, f"{ext} should be accepted"


def test_apply_mask_rejects_missing_file(app: App, tmp_home: Path) -> None:
    bogus = tmp_home / "does_not_exist.png"
    result = app.dispatch(ApplyMask(key=_KEY, path=bogus))

    assert result.ok is False
    assert "does not exist" in result.message


def test_apply_mask_rejects_empty_directory(app: App, tmp_home: Path) -> None:
    """A directory without ``01.png`` (the legacy canonical mask file)
    is rejected — empty dirs aren't masks."""
    dir_path = tmp_home / "fake.png"
    dir_path.mkdir()
    result = app.dispatch(ApplyMask(key=_KEY, path=dir_path))

    assert result.ok is False
    assert "legacy mask directory" in result.message


def test_apply_mask_accepts_legacy_mask_directory(
    app: App, tmp_home: Path,
) -> None:
    """A directory containing ``01.png`` (legacy cloud-mask layout) is
    accepted, with the inner file stored as the resolved mask path."""
    dir_path = tmp_home / "000a"
    dir_path.mkdir()
    mask_file = dir_path / "01.png"
    mask_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = app.dispatch(ApplyMask(key=_KEY, path=dir_path))

    assert result.ok is True
    assert result.path == str(mask_file.resolve())


@pytest.mark.parametrize("bad_ext", [".txt", ".pdf", ".exe", ".html", ""])
def test_apply_mask_rejects_non_image_extensions(
    app: App, tmp_home: Path, bad_ext: str,
) -> None:
    path = tmp_home / f"badmask{bad_ext}"
    path.write_bytes(b"x")
    result = app.dispatch(ApplyMask(key=_KEY, path=path))

    assert result.ok is False
    assert "neither a supported image file" in result.message


def test_apply_mask_publishes_event(app: App, mask_png: Path) -> None:
    events: list[MaskApplied] = []
    app.events.subscribe(MaskApplied, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(ApplyMask(key=_KEY, path=mask_png))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].path == str(mask_png.resolve())


def test_apply_mask_rejection_does_not_publish(
    app: App, tmp_home: Path,
) -> None:
    events: list[MaskApplied] = []
    app.events.subscribe(MaskApplied, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(ApplyMask(key=_KEY, path=tmp_home / "nope.png"))

    assert events == []
    assert app.settings.for_device(_KEY).mask_path is None


# ── SetMaskPosition ───────────────────────────────────────────────────


@pytest.mark.parametrize("x,y", [
    (0, 0),
    (10, 20),
    (100, 50),
    (320, 240),
])
def test_set_mask_position_accepts_non_negative(
    app: App, x: int, y: int,
) -> None:
    result = app.dispatch(SetMaskPosition(key=_KEY, x=x, y=y))

    assert result.ok is True
    assert result.position == (x, y)
    assert app.settings.for_device(_KEY).mask_position == (x, y)


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (-5, -10)])
def test_set_mask_position_rejects_negative(
    app: App, x: int, y: int,
) -> None:
    result = app.dispatch(SetMaskPosition(key=_KEY, x=x, y=y))

    assert result.ok is False
    assert "non-negative" in result.message


def test_set_mask_position_both_none_clears(app: App) -> None:
    """x=None y=None clears the stored position."""
    app.dispatch(SetMaskPosition(key=_KEY, x=10, y=20))
    assert app.settings.for_device(_KEY).mask_position == (10, 20)

    result = app.dispatch(SetMaskPosition(key=_KEY, x=None, y=None))

    assert result.ok is True
    assert result.position is None
    assert app.settings.for_device(_KEY).mask_position is None
    assert "cleared" in result.message


@pytest.mark.parametrize("x,y", [(10, None), (None, 20)])
def test_set_mask_position_partial_none_rejected(
    app: App, x: int | None, y: int | None,
) -> None:
    """Either both set or both omitted — partial is an error."""
    result = app.dispatch(SetMaskPosition(key=_KEY, x=x, y=y))

    assert result.ok is False
    assert "both be set or both omitted" in result.message


def test_set_mask_position_publishes_event(app: App) -> None:
    events: list[MaskPositionChanged] = []
    app.events.subscribe(MaskPositionChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetMaskPosition(key=_KEY, x=50, y=100))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].position == (50, 100)


# ── SetMaskVisible ────────────────────────────────────────────────────


@pytest.mark.parametrize("visible", [True, False])
def test_set_mask_visible_persists_state(app: App, visible: bool) -> None:
    result = app.dispatch(SetMaskVisible(key=_KEY, visible=visible))

    assert result.ok is True
    assert result.visible is visible
    assert app.settings.for_device(_KEY).mask_visible is visible


def test_set_mask_visible_message_uses_shown_hidden_wording(app: App) -> None:
    r_on = app.dispatch(SetMaskVisible(key=_KEY, visible=True))
    r_off = app.dispatch(SetMaskVisible(key=_KEY, visible=False))

    assert "shown" in r_on.message
    assert "hidden" in r_off.message


def test_set_mask_visible_publishes_event(app: App) -> None:
    events: list[MaskVisibilityChanged] = []
    app.events.subscribe(MaskVisibilityChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetMaskVisible(key=_KEY, visible=False))

    assert len(events) == 1
    assert events[0].key == _KEY
    assert events[0].visible is False


# ── Persistence ──────────────────────────────────────────────────────


def test_mask_settings_persist_across_app_restart(
    app: App, tmp_home: Path, mask_png: Path,
) -> None:
    app.dispatch(ApplyMask(key=_KEY, path=mask_png))
    app.dispatch(SetMaskPosition(key=_KEY, x=15, y=25))
    app.dispatch(SetMaskVisible(key=_KEY, visible=False))

    app2 = App(platform=FakePlatform(tmp_home))

    dev = app2.settings.for_device(_KEY)
    assert dev.mask_path == str(mask_png.resolve())
    assert dev.mask_position == (15, 25)
    assert dev.mask_visible is False
