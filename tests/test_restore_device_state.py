"""RestoreDeviceState — the one restore Command — and the session prime.

Every UI's "restore" is ``RestoreDeviceState`` (#150): the CLI ``restore-theme``
/ ``play`` / ``keepalive``, the API ``restore-theme``, qtgui's button.  It
ALWAYS restores.  "Only when the panel shows nothing" is the automatic caller's
rule — ``App._prime``, which a long-lived session runs on connect and when
themes land, before any UI hears the event (P4b: each UI used to prime its own
way, and the API not at all, #148).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.theme.filesystem import FileContentStore
from trcc.app import App
from trcc.core.commands import ConnectDevice, RestoreDeviceState
from trcc.core.events import DataInstalled, DeviceConnected

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


def _save(app: App, tmp_home: Path, name: str = "saved") -> None:
    """Persist *name* as the device's theme, the way LoadTheme leaves it."""
    app.settings.set_current_theme(
        _KEY, str(_write_theme(tmp_home, name).resolve()))


# ── An explicit restore always restores ──────────────────────────────


def test_restores_even_over_an_active_theme(app: App, tmp_home: Path) -> None:
    """The user asked, so the saved theme comes back whatever is showing.  It
    used to no-op here, so ``restore-theme`` did nothing over the API while the
    CLI's ``RestoreLastTheme`` reloaded."""
    _save(app, tmp_home)
    app.active_themes[_KEY] = FileContentStore().load(
        _write_theme(tmp_home, "other"))

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "saved"


def test_restores_over_a_pushed_frame_and_releases_the_hold(
        app: App, tmp_home: Path) -> None:
    _save(app, tmp_home)
    app.held.add(_KEY)

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "saved"
    assert _KEY not in app.held


def test_fails_with_the_connect_reason_when_the_device_cannot_connect(
        app: App, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LoadTheme succeeds with no device ("saved"), so without this a CLI loop
    primed by a restore ticked forever against a panel that was not there."""
    _save(app, tmp_home)

    def refuse(*_a: object, **_k: object) -> None:
        raise OSError("permission denied on /dev/sg1")

    monkeypatch.setattr(app.platform, "open_transport", refuse)

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert app.connect_issue(_KEY) == "permission denied on /dev/sg1"
    assert not result.ok
    assert result.message == (
        f"Not attached: {_KEY} — permission denied on /dev/sg1")
    assert _KEY not in app.active_themes


# ── Persisted theme wins ─────────────────────────────────────────────


def test_restores_the_persisted_theme(app: App, tmp_home: Path) -> None:
    theme_dir = _write_theme(tmp_home, "persisted")
    app.settings.set_current_theme(_KEY, str(theme_dir.resolve()))
    assert _KEY not in app.active_themes

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "persisted"


# ── No persisted theme → Theme1 (user decision, 2026-09-26) ──────────


def test_the_fallback_is_theme1_by_name_not_the_first_listed(app: App) -> None:
    """The listing is a lexical sort: ``Custom`` lists first, ``Theme10``
    before ``Theme2``.  Theme1 is chosen by NAME."""
    theme_root = app.platform.paths().theme_dir(*_RES)
    for name in ("Custom", "Theme10", "Theme1"):
        _write_theme(theme_root, name)
    assert not app.settings.for_device(_KEY).current_theme

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "Theme1"


def test_without_a_theme1_the_first_listed_theme_is_loaded(app: App) -> None:
    theme_root = app.platform.paths().theme_dir(*_RES)
    _write_theme(theme_root, "Bravo")
    _write_theme(theme_root, "Alpha")

    result = app.dispatch(RestoreDeviceState(key=_KEY))

    assert result.ok
    assert app.active_themes[_KEY].name == "Alpha"


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


# ── The session prime (App._prime) ───────────────────────────────────


def test_a_one_shot_app_never_primes_on_connect(app: App, tmp_home: Path) -> None:
    """No session → no prime: ``trcc color`` must not load a theme first."""
    _save(app, tmp_home)

    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    assert _KEY not in app.active_themes


def test_a_session_primes_a_panel_as_it_connects(app: App, tmp_home: Path) -> None:
    _save(app, tmp_home)
    app.start_session()
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        assert app.active_themes[_KEY].name == "saved"
    finally:
        app.close()


def test_a_panel_connected_before_the_session_is_primed_when_it_starts(
        app: App, tmp_home: Path) -> None:
    """gui's splash coldplug connects BEFORE the session exists, so those
    connects prime nothing and ``start_session`` skips a coldplug that already
    ran — the session must prime what is already attached."""
    _save(app, tmp_home)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok     # the splash worker
    app.discover_and_connect()                           # marks the coldplug done
    assert _KEY not in app.active_themes

    app.start_session()
    try:
        assert app.active_themes[_KEY].name == "saved"
    finally:
        app.close()


def test_a_ui_hears_device_connected_only_after_the_prime(
        app: App, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bus calls handlers in subscription order and the App subscribes
    first, so a UI never sees a blank panel it would restore itself — the
    race that made gui and qtgui's own restores necessary.

    The install is switched off: the suite's runner is synchronous, so its
    ``DataInstalled`` fired INSIDE the connect and primed the panel through the
    other door — this test passed with the subscription order reversed.  In
    production the install lands later, on its own thread."""
    monkeypatch.setattr(app.data_install_runner, "submit",
                        lambda *_a, **_k: None)
    seen: list[str | None] = []
    app.events.subscribe(DeviceConnected, lambda e: seen.append(
        t.name if (t := app.active_themes.get(e.key)) else None))
    _save(app, tmp_home)
    app.start_session()
    try:
        app.dispatch(ConnectDevice(key=_KEY))
        assert seen == ["saved"]
    finally:
        app.close()


def test_themes_landing_prime_a_blank_panel(app: App) -> None:
    """First install: the panel connects before its themes exist, and shows
    Theme1 when the download lands — every UI, not only gui's auto-load."""
    app.start_session()
    try:
        app.dispatch(ConnectDevice(key=_KEY))
        assert _KEY not in app.active_themes
        _write_theme(app.platform.paths().theme_dir(*_RES), "Theme1")

        app.events.publish(DataInstalled(resolution=_RES, ok=True))

        assert app.active_themes[_KEY].name == "Theme1"
    finally:
        app.close()


@pytest.mark.parametrize("state", ["active", "held"])
def test_the_prime_never_overrides_a_panel(
        app: App, tmp_home: Path, state: str) -> None:
    """Automatic, so it only fills a blank panel: an active theme and a pushed
    frame both stay."""
    _save(app, tmp_home)
    app.dispatch(ConnectDevice(key=_KEY))
    other = FileContentStore().load(_write_theme(tmp_home, "other"))
    if state == "active":
        app.active_themes[_KEY] = other
    else:
        app.held.add(_KEY)
    app.start_session()
    try:
        app.events.publish(DataInstalled(resolution=_RES, ok=True))
        assert app.active_themes.get(_KEY) is (other if state == "active"
                                              else None)
    finally:
        app.close()


def test_the_api_runs_a_session(tmp_home: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    """#148: the API was the one UI with no session, so a panel it served sat
    blank until something polled ``/tick`` (which then restored on every
    poll).  A panel that connects while the API serves shows its saved display.

    A RESTART, like the reporter's: one App saves the theme, the API starts on
    the same paths, and everything after is asked through the bus the routes
    hold — ``has_active_theme`` is True only if the session primed the panel
    (the saved theme is the only theme on disk; there is no Theme1 here).
    """
    import uvicorn

    from trcc.core.commands import ListDevices
    from trcc.ui._uis import ApiUI

    _save(App(platform=FakePlatform(tmp_home)), tmp_home)
    shown: list[bool] = []

    def serve(self: uvicorn.Server, sockets: object = None) -> None:
        bus = self.config.app.state.trcc
        bus.dispatch(ConnectDevice(key=_KEY))
        shown.extend(d.has_active_theme
                     for d in bus.dispatch(ListDevices()).devices
                     if d.key == _KEY)
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "run", serve)

    assert ApiUI().start(FakePlatform(tmp_home)) == 0
    assert shown == [True]
