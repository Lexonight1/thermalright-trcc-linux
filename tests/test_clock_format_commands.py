"""SetTimeFormat / SetDateFormat edit clock ELEMENTS — the C# keeps the format
per element (myModeSub, UCXiTongXianShiSub.cs:248) and has no global one.

They were a global preference drawn over every element; now they are the
CLI/API's "all my clocks" edit: every time (or date) element on ``key``, or on
every device when ``key`` is None.  Nothing else in the layout moves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakePlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    AddOverlayElement,
    LcdSnapshot,
    ResolveOverlay,
    SetDateFormat,
    SetTimeFormat,
)
from trcc.core.events import OverlayChanged

_A, _B = "0402:3922", "87ad:70db"


@pytest.fixture
def app(tmp_path: Path) -> App:
    app = App(platform=FakePlatform(tmp_path))
    app.set_renderer(QtRenderer())
    for key in (_A, _B):
        for eid, kind in (("t", {"source": "time", "format": "%H:%M"}),
                          ("d", {"source": "date", "format": "%m/%d"}),
                          ("w", {"source": "weekday"})):
            assert app.dispatch(AddOverlayElement(
                key=key, element_id=eid, type="clock", **kind)).ok
        assert app.dispatch(AddOverlayElement(
            key=key, element_id="x", type="text", text="CPU")).ok
    return app


def _formats(app: App, key: str) -> dict[str, str]:
    return {e.id: e.format for e in
            app.dispatch(ResolveOverlay(key=key)).elements}


def test_12h_with_a_key_changes_that_devices_time_elements_only(app: App) -> None:
    before_b = _formats(app, _B)

    result = app.dispatch(SetTimeFormat(fmt="12h", key=_A))

    assert result.ok, result.message
    after = _formats(app, _A)
    assert after["t"] == "%I:%M %p"
    assert after["d"] == "%m/%d", "a date element is not a time element"
    assert _formats(app, _B) == before_b, "another device changed"


def test_no_key_reaches_every_device(app: App) -> None:
    assert app.dispatch(SetTimeFormat(fmt="12h")).ok
    assert {_formats(app, k)["t"] for k in (_A, _B)} == {"%I:%M %p"}


def test_a_date_pattern_is_stored_as_the_elements_strftime(app: App) -> None:
    """The CLI/API take ICU-ish tokens; the element carries strftime."""
    assert app.dispatch(SetDateFormat(fmt="dd.MM.yyyy", key=_A)).ok
    assert _formats(app, _A)["d"] == "%d.%m.%Y"
    assert _formats(app, _A)["t"] == "%H:%M"


def test_every_ui_hears_it_as_an_overlay_change(app: App) -> None:
    """The events every UI follows; the format events are gone."""
    heard: list[str] = []
    app.events.subscribe(OverlayChanged, lambda e: heard.append(e.key))

    app.dispatch(SetTimeFormat(fmt="24h"))

    assert sorted(heard) == sorted([_A, _B])


def test_the_snapshot_reports_the_elements_format(app: App) -> None:
    """``LcdSnapshot`` is what the qtgui status panel and ``trcc`` print."""
    app.dispatch(SetTimeFormat(fmt="12h", key=_A))
    snap = app.dispatch(LcdSnapshot(key=_A))
    # In the tokens a UI shows and sends back (``yyyy/MM/dd``), not strftime.
    assert (snap.time_format, snap.date_format) == ("12h", "MM/dd")
    assert app.dispatch(LcdSnapshot(key=_B)).time_format == "24h"


def test_an_invalid_time_format_changes_nothing(app: App) -> None:
    before = _formats(app, _A)
    assert not app.dispatch(SetTimeFormat(fmt="13h", key=_A)).ok
    assert _formats(app, _A) == before
