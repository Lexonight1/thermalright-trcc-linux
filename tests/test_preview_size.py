"""``PreviewSize`` — the #136 compose-vs-fallback rule, asked instead of computed.

The gui used to gather a ``ProductInfo``, a ``Theme``, a ``DeviceProfile`` and
the ``DisplayService`` itself and hand them to a Presentation Model — four
reaches past the bus in one expression, and a UI holding domain objects that
CLAUDE.md forbids.  These cases are ported from
``test_preview_geometry.py::composed_preview_size`` so the rule keeps its
coverage now that it lives behind the Query.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, PreviewSize
from trcc.core.models import Theme

from .conftest import FakePlatform, _CliRenderer

_KEY = "0402:3922"


@pytest.fixture
def app(tmp_home: Path) -> App:
    # The composed path goes through DisplayService, which needs a
    # renderer; the fallback path does not.
    return App(platform=FakePlatform(tmp_home),
               renderer=_CliRenderer())    # type: ignore[arg-type]


@pytest.fixture
def connected(app: App) -> App:
    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


def _theme(app: App) -> None:
    app.active_themes[_KEY] = Theme(
        path=Path("/nonexistent"), name="T", resolution=(320, 320), config={},
    )


def test_no_device_is_unknown_not_zero(app: App) -> None:
    """``ok=False`` means "keep your current preview".

    0x0 is not a fallback — it collapses the bezel.  The gui used to hold a
    cached canvas for exactly this moment; the Query declines instead, and the
    View leaves the widget alone.
    """
    result = app.dispatch(PreviewSize(key=_KEY))

    assert result.ok is False
    assert (result.width, result.height) == (0, 0)
    assert "keep the current preview" in result.message


def test_a_theme_defers_to_the_composed_canvas(connected: App) -> None:
    """Device + theme → the DisplayService composes it.

    That path folds portrait composition and the user orientation exactly as
    the wire frame does, so the preview asset and label match the panel (#136).
    """
    _theme(connected)

    result = connected.dispatch(PreviewSize(key=_KEY))

    assert result.ok is True
    assert result.composed is True
    assert (result.width, result.height) == (320, 320)


def test_without_a_theme_it_swaps_the_canvas_for_the_orientation(
    connected: App,
) -> None:
    """No theme → the device's own canvas, swapped for 90/270.

    The display is NOT consulted; ``composed`` reports which rule ran so a
    caller can tell the two apart.
    """
    connected.settings.for_device(_KEY).orientation = 90

    result = connected.dispatch(PreviewSize(key=_KEY))

    assert result.ok is True
    assert result.composed is False
    # 320x320 is square, so the swap is identity — the NEXT test is the one
    # that would catch a swap that did not happen.
    assert (result.width, result.height) == (320, 320)


@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
def test_a_square_panel_is_the_same_at_every_orientation(
    connected: App, orientation: int,
) -> None:
    """Ported from the geometry tests: square canvases swap to themselves."""
    connected.settings.for_device(_KEY).orientation = orientation

    result = connected.dispatch(PreviewSize(key=_KEY))

    assert (result.width, result.height) == (320, 320)


def test_the_orientation_swap_is_real_on_a_non_square_canvas(
    connected: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case a square panel cannot prove.

    A 320x320 fixture makes the swap invisible, so this drives a non-square
    canvas through the same path — otherwise every assertion above passes
    against a Query that ignored orientation entirely.
    """
    from trcc.core import protocol

    device = connected.devices[_KEY]
    profile = device.profile
    assert profile is not None
    monkeypatch.setattr(
        type(profile), "resolution", property(lambda self: (854, 480)),
    )
    del protocol

    connected.settings.for_device(_KEY).orientation = 0
    assert connected.dispatch(PreviewSize(key=_KEY)).width == 854

    connected.settings.for_device(_KEY).orientation = 90
    landscape_swapped = connected.dispatch(PreviewSize(key=_KEY))
    assert (landscape_swapped.width, landscape_swapped.height) == (480, 854)
