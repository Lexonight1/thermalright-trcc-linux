"""``ListDevices`` — the primitive that was missing.

``DeviceState`` answers about ONE device and needs its key, so nothing could
produce the key list itself without reaching ``app.devices`` — an
``AttributeError`` under ``TRCC_DAEMON=1``.  A device picker and a render
ticker both reached past the bus for something as ordinary as "which devices
are there".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, ListDevices
from trcc.core.models import Theme

from .conftest import FakePlatform

_KEY = "0402:3922"


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


@pytest.fixture
def attached(app: App) -> App:
    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


def test_an_empty_fleet_is_ok_not_an_error(app: App) -> None:
    """Nothing plugged in is a normal answer.

    ``ok=False`` escalates to WARNING through ``App.dispatch``, which would
    warn on every poll of a machine with no device — and the render ticker
    polls this continuously.
    """
    result = app.dispatch(ListDevices())

    assert result.ok is True
    assert result.devices == []


def test_an_attached_device_reports_its_identity(attached: App) -> None:
    result = attached.dispatch(ListDevices())

    assert [d.key for d in result.devices] == [_KEY]
    entry = result.devices[0]
    assert entry.vendor and entry.product
    assert entry.wire and entry.kind
    assert entry.connected is True


def test_has_active_theme_tracks_the_loaded_theme(attached: App) -> None:
    """The field the render ticker drives off.

    It used to read ``app.active_themes`` directly to find which devices to
    render; this is the same fact, asked instead of reached for.

    MUTATION CHECK: hard-code ``has_active_theme=False`` in the Query and the
    ticker renders nothing — this fails.
    """
    assert attached.dispatch(ListDevices()).devices[0].has_active_theme is False

    attached.active_themes[_KEY] = Theme(
        path=Path("/nonexistent"), name="T", resolution=(320, 320), config={},
    )

    assert attached.dispatch(ListDevices()).devices[0].has_active_theme is True


def test_it_does_not_scan_the_bus(app: App, monkeypatch) -> None:
    """A read must not have side effects.

    ``DiscoverDevices`` probes USB and ATTACHES what it finds.  Using it to
    populate a combo box or drive a ticker would make a read do work — which
    is exactly why this Query exists beside it.

    Watches ``Platform.scan_devices`` rather than comparing ``app.devices``:
    the first version did the latter and PASSED against a mutation that made
    this Query scan, because the test platform has nothing to discover, so a
    scan changed nothing.  Assert the call, not its consequence.
    """
    scans: list[int] = []
    real = type(app.platform).scan_devices

    def counting_scan(self, *a: object, **k: object):
        scans.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(type(app.platform), "scan_devices", counting_scan)

    app.dispatch(ListDevices())

    assert scans == [], "ListDevices probed the bus — it must only report"


def test_devices_come_back_in_a_stable_order(attached: App) -> None:
    """A combo box rebuilt on every refresh must not reshuffle itself."""
    first = [d.key for d in attached.dispatch(ListDevices()).devices]
    second = [d.key for d in attached.dispatch(ListDevices()).devices]

    assert first == second == sorted(first)
