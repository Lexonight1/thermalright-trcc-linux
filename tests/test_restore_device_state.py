"""RestoreDeviceState — the unified display-state restore Command (#150).

The shared "make this device renderable from persisted settings" path every UI
dispatches at its display-start entry (GUI connect, CLI ``display play`` /
``keepalive``, API ``restore-theme``).  Closes the terminal-only gap where a
fresh CLI process had no in-memory theme, so ``display play`` failed
"No active theme" and ``keepalive`` failed "No cached frame".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.theme.filesystem import FileContentStore
from trcc.app import App
from trcc.core.commands import RestoreDeviceState

from .conftest import FakePlatform

# Registry product — resolves to native (320, 320) without attaching a device.
_KEY = "0402:3922"
_RES = (320, 320)


def _write_theme(directory: Path, name: str) -> Path:
    """Minimal next/-shape theme dir that FileContentStore.load parses."""
    theme_dir = directory / name
    theme_dir.mkdir(parents=True)
    (theme_dir / "trcc.json").write_text(
        json.dumps({"name": name, "width": 320, "height": 320, "elements": []}),
        encoding="utf-8",
    )
    (theme_dir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return theme_dir


@pytest.fixture
def app(tmp_home: Path) -> App:
    from trcc.adapters.render.qt import QtRenderer

    a = App(platform=FakePlatform(tmp_home))
    a.set_renderer(QtRenderer())
    return a


# ── Idempotent ───────────────────────────────────────────────────────


def test_noop_when_active_theme_already_present(app: App, tmp_home: Path) -> None:
    existing = FileContentStore().load(_write_theme(tmp_home, "already"))
    app.active_themes[_KEY] = existing

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert result.message == "Display state already active"
    assert app.active_themes[_KEY] is existing   # untouched


# ── Persisted theme wins ─────────────────────────────────────────────


def test_restores_the_persisted_theme(app: App, tmp_home: Path) -> None:
    theme_dir = _write_theme(tmp_home, "persisted")
    app.settings.set_current_theme(_KEY, str(theme_dir.resolve()))
    assert _KEY not in app.active_themes

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "persisted"


# ── No persisted theme → first available (GUI first-install parity) ──


def test_autoloads_first_theme_when_none_persisted(app: App) -> None:
    theme_root = app.platform.paths().theme_dir(*_RES)
    _write_theme(theme_root, "Alpha")
    _write_theme(theme_root, "Bravo")
    assert not app.settings.for_device(_KEY).current_theme
    assert _KEY not in app.active_themes

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name in {"Alpha", "Bravo"}


def test_fails_cleanly_when_no_theme_available(app: App) -> None:
    # No persisted theme, no themes installed for this resolution.
    assert _KEY not in app.active_themes

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert not result.ok
    assert "No theme available" in result.message
    assert _KEY not in app.active_themes


# ── Persisted background video is replayed over the theme ────────────


def test_replays_persisted_background_best_effort(app: App, tmp_home: Path) -> None:
    """A persisted background_path is replayed after the theme; a missing/
    undecodable video is best-effort and never fails the restore."""
    theme_dir = _write_theme(tmp_home, "withbg")
    app.settings.set_current_theme(_KEY, str(theme_dir.resolve()))
    app.settings.set_background_path(_KEY, str(tmp_home / "nonexistent.mp4"))

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    # Theme restored; the bogus background replay is swallowed, not fatal.
    assert result.ok
    assert app.active_themes[_KEY].name == "withbg"
