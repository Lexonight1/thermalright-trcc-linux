"""#334 -- a device key typed in uppercase is the same device.

The reporter drove a daemon-owned 87ad:70db through the API as ``87AD:70DB``.
Nothing turned that into the key the App holds, so ``EnsureConnected`` missed
the connected panel, ``ConnectDevice`` attached a second device over its entry
(the first still held the USB claim: "in use by another process"), the panel
was unreachable until a restart, and his brightness went into a settings entry
of its own that nothing reads.

Each test below pins one link of that chain.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.core.models import canonical_device_key
from trcc.services.settings import Settings

from .conftest import FakePaths

PANEL = "87ad:70db"
TYPED = "87AD:70DB"


# ── the one spelling ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("typed", "held"), [
    ("87AD:70DB", "87ad:70db"),
    ("87ad:70db", "87ad:70db"),
    ("87Ad:70dB", "87ad:70db"),
    # The unit is opaque: the ids are lower-cased, the unit never touched.
    ("87AD:70DB@1-13", "87ad:70db@1-13"),
    ("87ad:70db@Bus3", "87ad:70db@Bus3"),
])
def test_a_device_key_comes_back_in_its_one_spelling(typed: str,
                                                      held: str) -> None:
    assert canonical_device_key(typed) == held


@pytest.mark.parametrize("other", [
    "rgb",              # a module's key
    "data",             # GetPaths' key
    "cpu:temp",         # a metric
    "",
    "87AD:70DB@",       # no unit after the @: not a key, left for the
    "87AD7:0DB",        # Command to reject as malformed, as it always did
])
def test_anything_else_is_left_exactly_as_it_was(other: str) -> None:
    assert canonical_device_key(other) == other


def test_a_command_holds_the_key_canonical_however_it_was_built() -> None:
    """In-process construction AND the socket's decode -- the App sees one."""
    from trcc.core.commands import SetModuleEnabled, SleepDevice
    from trcc.ipc import decode_command

    decoded = decode_command({"command": "SleepDevice",
                              "kwargs": {"key": TYPED}})

    assert SleepDevice(key=TYPED).key == PANEL
    assert decoded == SleepDevice(key=PANEL)
    assert SetModuleEnabled(key="rgb", enabled=True).key == "rgb"


# ── the reporter's requests, against a connected panel ───────────────────


def _connected_app(tmp_path: Path):
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.app import App

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    app = App(platform=MockPlatform([{"type": "lcd", "name": "Mjolnir Vision",
                                      "vid": "87ad", "pid": "70db", "pm": 5,
                                      "sub": 1, "resolution": "320x240"}],
                                    tmp_path),
              send_scheduler=SyncSendScheduler(),
              renderer=_CliRenderer())
    app.discover_and_connect()
    assert list(app.devices) == [PANEL] and app.devices[PANEL].is_connected
    return app


def test_the_api_drives_the_connected_panel_under_an_uppercase_key(
        tmp_path: Path) -> None:
    """THE #334 repro: sleep + brightness as he sent them."""
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    app = _connected_app(tmp_path)
    panel = app.devices[PANEL]

    with loopback_client(build_app(trcc=app)) as client:
        found = client.get(f"/devices/{TYPED}")
        sleep = client.post(f"/devices/{TYPED}/display/sleep")
        bright = client.post(f"/devices/{TYPED}/display/brightness",
                             json={"percent": 25})

    assert (found.status_code, found.json()["key"]) == (200, PANEL)
    assert (sleep.status_code, bright.status_code) == (200, 200), (
        sleep.json(), bright.json())
    # The SAME device, still connected: nothing was attached over it.
    assert list(app.devices) == [PANEL]
    assert app.devices[PANEL] is panel and panel.is_connected
    # The brightness reached the settings the panel reads, and no others.
    assert app.settings.device_keys() == (PANEL,)
    assert app.settings.for_device(PANEL).brightness == 25


def test_an_uppercase_connect_reconnects_the_one_panel(tmp_path: Path) -> None:
    """``POST .../connect`` -- the other route in his report."""
    from trcc.core.commands import ConnectDevice

    app = _connected_app(tmp_path)
    result = app.dispatch(ConnectDevice(key=TYPED))

    assert result.ok, result.message
    assert list(app.devices) == [PANEL] and app.devices[PANEL].is_connected


def test_attach_never_overwrites_an_attached_device(tmp_path: Path) -> None:
    """The amplifier: an overwrite orphaned the device holding the USB claim,
    so nothing could reach the panel again until a restart."""
    from trcc.core.errors import TransportError

    app = _connected_app(tmp_path)
    panel = app.devices[PANEL]

    with pytest.raises(TransportError, match="already attached"):
        app.attach(0x87AD, 0x70DB)

    assert app.devices[PANEL] is panel and panel.is_connected


# ── settings he already has on disk ──────────────────────────────────────


def _load(tmp_path: Path, devices: dict) -> Settings:
    (tmp_path / "trcc.json").write_text(json.dumps(
        {"schema": 7, "app": {}, "devices": devices, "led_devices": {}}),
        encoding="utf-8")
    return Settings(FakePaths(tmp_path))


def test_the_entry_the_panel_used_wins_over_an_uppercase_twin(
        tmp_path: Path) -> None:
    """His file: both entries.  The lowercase one is what the panel ran on."""
    settings = _load(tmp_path, {PANEL: {"brightness": 100},
                                TYPED: {"brightness": 25}})

    assert settings.device_keys() == (PANEL,)
    assert settings.for_device(PANEL).brightness == 100


def test_a_lone_uppercase_entry_is_kept_under_the_canonical_key(
        tmp_path: Path) -> None:
    settings = _load(tmp_path, {TYPED: {"brightness": 25}})

    assert settings.device_keys() == (PANEL,)
    assert settings.for_device(PANEL).brightness == 25


# ── a key that names no device makes no settings ─────────────────────────
#
# About forty Commands reached their device's settings before anything looked
# at the key, so one sent with an empty key -- a window whose selection was
# still empty -- filed a default entry under '' that every save wrote out.


@pytest.mark.parametrize("make", [
    lambda s: s.for_device(""),
    lambda s: s.for_led(""),
    lambda s: s.seed_mount_orientation("", portrait_mounted=False),
    lambda s: s.for_device("not-a-device"),
], ids=["for_device", "for_led", "seed_mount_orientation", "malformed"])
def test_settings_make_no_entry_for_a_key_naming_no_device(tmp_path: Path,
                                                            make) -> None:
    from trcc.core.errors import NotADeviceKey

    settings = Settings(FakePaths(tmp_path))
    with pytest.raises(NotADeviceKey):
        make(settings)

    assert settings.device_keys() == ()
    assert settings._led_devices == {}


def test_an_entry_already_saved_under_no_device_is_dropped(tmp_path: Path) -> None:
    (tmp_path / "trcc.json").write_text(json.dumps(
        {"schema": 7, "app": {},
         "devices": {"": {"brightness": 100}, PANEL: {"brightness": 40}},
         "led_devices": {"": {}, "0416:8001": {}}}), encoding="utf-8")
    settings = Settings(FakePaths(tmp_path))

    assert settings.device_keys() == (PANEL,)
    assert list(settings._led_devices) == ["0416:8001"]


@pytest.mark.parametrize("command", ["SetBrightness", "EnableOverlay",
                                     "SetLedBrightness", "ToggleLed"])
def test_a_command_with_no_device_is_refused_not_raised(tmp_path: Path,
                                                         command: str) -> None:
    """Every UI gets the Command's own failed Result -- never the exception."""
    from trcc.core import commands

    built = {"SetBrightness": {"percent": 50}, "EnableOverlay": {"enabled": True},
             "SetLedBrightness": {"percent": 50}, "ToggleLed": {"on": True}}
    app = _connected_app(tmp_path)
    result = app.dispatch(getattr(commands, command)(key="", **built[command]))

    assert result.ok is False
    assert "not a device" in result.message, result.message
    assert "" not in app.settings.device_keys()
    assert "" not in app.settings._led_devices
