"""RGB Lighting as a module: what moving it out of the App must not change,
and what being a module adds -- it can be off.

Its Commands, follower, drivers and pages keep their own tests
(``test_rgb_mirror.py``, ``test_rgb_boards.py``, ``tests/adapters/rgb/``,
``test_rgb_page_view.py``); these are the seams the move made.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tests.conftest import FakePaths, rgb_module
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import SetModuleEnabled
from trcc.core.events import FrameSent
from trcc.core.models import RamAccessState, RamAccessStatus
from trcc.core.ports import RamAccess
from trcc.modules.rgb.app import STILL_GRANTED
from trcc.modules.rgb.core import RgbFollow, RgbLights, SetRgbFollow
from trcc.modules.rgb.core.models import FollowColors, FollowMapping, RgbFollowMode
from trcc.services.settings import Settings

from .test_rgb_mirror import FakeMirror

_SPEC = {"vid": "0416", "pid": "8001", "pm": 1}
_LCD = "0402:3922"


def _app(tmp_path: Path) -> tuple[App, list[FakeMirror]]:
    """An App on the mock whose follower drives fakes."""
    made: list[FakeMirror] = []

    def make(mode: RgbFollowMode, host: str, port: int) -> FakeMirror:
        made.append(FakeMirror(mode, host, port))
        return made[-1]

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    rgb_module(app).mirror._make = make
    return app, made


def test_a_settings_file_from_before_keeps_what_follows(tmp_path: Path) -> None:
    """Schema 6 -> 7: RGB's app-wide settings move, under the same names,
    into its own section -- and the module reads them back as they were.
    MUTATION CHECK: drop the ``schema < 7`` fold in ``_migrate_app`` and the
    RAM follows nothing after the upgrade."""
    saved = {"rgb_follow": "ram", "openrgb_host": "pc.lan", "openrgb_port": 7000,
             "rgb_follow_source": _LCD, "rgb_follow_mapping": "single",
             "rgb_follow_colors": "smooth", "rgb_follow_targets": ["i2c-3/0x19"],
             "ram_effects": {"i2c-3/0x19": {"effect": "rainbow"}},
             "rgb_lights": [{"kind": "ram", "ref": "i2c-3/0x19", "name": "Stick",
                             "led_count": 10}],
             "aura_argb_leds": {"1": 8}}
    (tmp_path / "trcc.json").write_text(json.dumps({
        "schema": 6, "app": {"temp_unit": "F", **saved},
        "devices": {}, "led_devices": {}}), encoding="utf-8")

    settings = Settings(FakePaths(tmp_path))
    assert settings.module_settings("rgb") == saved
    assert settings.app.temp_unit == "F"                 # the rest stays put
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    rgb = rgb_module(app).settings
    assert (rgb.rgb_follow_mode(), rgb.rgb_follow_mapping(), rgb.rgb_follow_colors(),
            rgb.rgb_follow_targets(), rgb.aura_argb_leds(), rgb.prefs.openrgb_host) == (
        RgbFollowMode.RAM, FollowMapping.SINGLE, FollowColors.SMOOTH,
        ("i2c-3/0x19",), {1: 8}, "pc.lan")
    app.close()
    assert json.loads((tmp_path / "trcc.json").read_text())["schema"] == 7


def test_the_panels_pictures_are_watched_only_while_a_device_leads(
        tmp_path: Path) -> None:
    """Following an LCD watches its pictures; following the cooler, or
    nothing, watches none -- so a frame costs RGB nothing then.  MUTATION
    CHECK: watch whenever following is on, whatever leads."""
    app, _made = _app(tmp_path)
    heard = app.events.subscriber_count(FrameSent)
    assert app.dispatch(SetRgbFollow(mode=RgbFollowMode.OPENRGB, source=_LCD)).ok
    following_an_lcd = app.events.subscriber_count(FrameSent)
    assert app.dispatch(SetRgbFollow(mode=RgbFollowMode.OPENRGB, source="")).ok
    following_the_cooler = app.events.subscriber_count(FrameSent)
    assert app.dispatch(SetRgbFollow(mode=RgbFollowMode.OFF, source=_LCD)).ok
    assert (following_an_lcd, following_the_cooler,
            app.events.subscriber_count(FrameSent)) == (heard + 1, heard, heard)
    app.close()


def test_switched_off_it_stops_following_and_answers_that_it_is_off(
        tmp_path: Path) -> None:
    """Off: the follower stops (its thread ends, the lights are let go), no
    picture is watched, and its Commands say where to turn it on.
    MUTATION CHECK: leave ``mirror.close`` out of both ``RgbModule.stop``
    and ``_disable`` (either alone still closes it -- by design: the second
    lets go of a follower a one-off Command started with no session)."""
    app, made = _app(tmp_path)
    app.start_session()
    assert app.dispatch(SetRgbFollow(mode=RgbFollowMode.OPENRGB, source=_LCD)).ok
    assert app.dispatch(RgbFollow()).ok
    heard = app.events.subscriber_count(FrameSent)
    assert app.dispatch(SetModuleEnabled(key="rgb", enabled=False)).ok
    names = {t.name for t in threading.enumerate()}
    off = app.dispatch(RgbLights())
    assert ("trcc-rgb-mirror" in names, made[-1].closed >= 1,
            app.events.subscriber_count(FrameSent)) == (False, True, heard - 1)
    assert (off.ok, off.message) == (False, "RGB Lighting is off -- turn it on in Modules")
    app.close()


# ── switching it off takes back the memory's permission ──────────────

class _Grant(RamAccess):
    """TRCC's RAM-lighting permission, recorded.  *after* is what removing it
    leaves: OFF, or ON again when the password prompt is closed."""

    def __init__(self, state: RamAccessState,
                 after: RamAccessState = RamAccessState.OFF,
                 command: str = "") -> None:
        self.state, self.after, self.removed = state, after, 0
        self.command = command

    def status(self) -> RamAccessStatus:
        return RamAccessStatus(self.state, f"now {self.state.value}")

    def enable(self) -> RamAccessStatus:
        raise AssertionError("switching off never grants")

    def disable(self) -> RamAccessStatus:
        self.removed += 1
        self.state = self.after
        return RamAccessStatus(self.state, f"now {self.state.value}", self.command)


def _granted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, grant: _Grant) -> App:
    """An App on the mock whose memory permission is *grant*."""
    app, _made = _app(tmp_path)
    monkeypatch.setattr(app.platform, "ram_access", lambda: grant)
    return app


@pytest.mark.parametrize("state", [RamAccessState.ON, RamAccessState.NOT_APPLIED])
def test_switching_rgb_off_removes_trccs_ram_permission(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: RamAccessState) -> None:
    """With RGB off nothing in TRCC could take the permission back, so it
    goes with the switch -- installed but not yet picked up (NOT_APPLIED)
    too.  MUTATION CHECK: drop ``NOT_APPLIED`` from ``_TRCC_GRANT``."""
    grant = _Grant(state)
    app = _granted(tmp_path, monkeypatch, grant)
    result = app.dispatch(SetModuleEnabled(key="rgb", enabled=False))
    assert (result.ok, result.note, grant.removed, grant.state) == (
        True, "", 1, RamAccessState.OFF)
    app.close()


def test_closing_the_app_keeps_the_ram_permission(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A logout, a reboot, ``trcc kill``: RGB goes off with the App, and the
    permission the user gave stays -- no password prompt either.
    MUTATION CHECK: move the removal into ``RgbModule._disable``."""
    grant = _Grant(RamAccessState.ON)
    app = _granted(tmp_path, monkeypatch, grant)
    app.start_session()
    app.close()
    assert (grant.removed, grant.state) == (0, RamAccessState.ON)


def test_a_closed_prompt_still_switches_rgb_off_and_says_how_to_remove_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grant = _Grant(RamAccessState.ON, after=RamAccessState.ON)
    app = _granted(tmp_path, monkeypatch, grant)
    result = app.dispatch(SetModuleEnabled(key="rgb", enabled=False))
    assert (result.ok, result.enabled, result.note, grant.removed) == (
        True, False, STILL_GRANTED, 1)
    assert app.dispatch(RgbLights()).ok is False             # RGB is off
    app.close()


def test_a_permission_trcc_cannot_remove_here_says_what_will(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No polkit, or NixOS's declared grant: switching RGB on and off again
    would not help, so the note is the status's own instruction.
    MUTATION CHECK: always return ``STILL_GRANTED``."""
    grant = _Grant(RamAccessState.ON, after=RamAccessState.ON,
                   command="programs.trcc-linux.ramLighting = false;")
    app = _granted(tmp_path, monkeypatch, grant)
    result = app.dispatch(SetModuleEnabled(key="rgb", enabled=False))
    assert (result.ok, result.enabled, result.note) == (True, False, "now on")
    app.close()


@pytest.mark.parametrize("state", [RamAccessState.ELSEWHERE, RamAccessState.OFF,
                                   RamAccessState.NO_BUS, RamAccessState.UNSUPPORTED])
def test_a_permission_trcc_did_not_grant_is_left_alone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: RamAccessState) -> None:
    """Another rule's access (OpenRGB's) is not TRCC's to take, and with
    none there is nothing to ask the password for."""
    grant = _Grant(state)
    app = _granted(tmp_path, monkeypatch, grant)
    result = app.dispatch(SetModuleEnabled(key="rgb", enabled=False))
    assert (result.ok, result.note, grant.removed) == (True, "", 0)
    app.close()
