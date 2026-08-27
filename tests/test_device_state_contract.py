"""``DeviceState``'s three answers, pinned.

This Query is what the whole "UIs ask the bus" burn-down leans on, and its
docstring promised a behaviour it never had: "a KNOWN but unconnected device is
``ok=True, connected=False``", which reads as registry-known.  It is not —
``app.devices`` holds ATTACHED devices and the one branch returns ``ok=False``
when that lookup misses.  Nothing broke, because every caller already coded to
the real behaviour, but a plan built on the docstring would have been wrong.

These tests exist so the prose cannot drift from the code again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, DeviceState
from trcc.core.registry import find_product

from .conftest import FakePlatform

_KEY = "0402:3922"
_VID, _PID = 0x0402, 0x3922


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


def test_the_registry_knows_this_key() -> None:
    """The premise of the next test — otherwise it proves nothing."""
    assert find_product(_VID, _PID) is not None


def test_a_registry_known_but_unattached_device_is_not_ok(app: App) -> None:
    """The correction.  Registry-known is NOT enough — it must be ATTACHED.

    A caller cannot read identity off a device that isn't there, so ``ok=False``
    is the honest answer.  Three callers depend on exactly this: the CLI exits
    1, the preview WebSocket closes 1008, and the resolution route falls back
    to 0x0.  Making this ``ok=True`` would have that socket accept a device it
    cannot stream a single frame from.
    """
    assert _KEY not in app.devices

    result = app.dispatch(DeviceState(key=_KEY))

    assert result.ok is False
    assert "no device attached" in result.message.lower()
    assert result.vendor == "", "no identity is reported without a device"


def test_an_unknown_key_is_also_not_ok(app: App) -> None:
    """Unknown and unattached give the same answer — there is one branch."""
    result = app.dispatch(DeviceState(key="dead:beef"))

    assert result.ok is False
    assert result.connected is False


def test_an_attached_device_reports_identity_and_geometry(app: App) -> None:
    """The ok=True path: identity from the registry, geometry from handshake."""
    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    result = app.dispatch(DeviceState(key=_KEY))

    assert result.ok is True
    assert result.connected is True
    assert result.vendor and result.product
    assert result.resolution == (320, 320)
    assert result.native_resolution != (0, 0)


def test_handshake_fields_are_none_not_zero_before_a_handshake(app: App) -> None:
    """``None`` and ``0`` are different answers and the Result keeps them apart.

    "We have not asked the hardware yet" must be distinguishable from "this
    panel is 0x0" — a UI drawing a preview branches on it.
    """
    result = app.dispatch(DeviceState(key=_KEY))

    assert result.resolution is None
    assert result.pm_byte is None
    assert result.rotate is None


def test_it_reports_the_inspector_fields(app: App) -> None:
    """The five added for the qtgui device inspector (2026-08-27).

    Each is something a UI displays, which is the bar every field on this
    Result is held to.  ``byte_order`` is the one worth naming: it is a
    PROPERTY on ``DeviceProfile``, not a dataclass field, so it does not come
    along with a mechanical field walk and has to be resolved deliberately.

    MUTATION CHECK: drop any of the five from the Query and this fails.
    """
    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    result = app.dispatch(DeviceState(key=_KEY))
    device = app.devices[_KEY]
    profile = device.profile
    assert profile is not None

    assert result.byte_order == profile.byte_order
    assert result.byte_order in (">", "<"), "a real endianness, not a default"
    assert result.encode_base == profile.encode_base
    assert result.encode_invert == profile.encode_invert
    assert result.encode_baseline == profile.encode_baseline
    assert result.serial == (getattr(device.handshake, "serial", "") or "")
