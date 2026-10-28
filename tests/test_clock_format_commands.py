"""SetTimeFormat / SetDateFormat edit clock ELEMENTS — the C# keeps the format
per element (myModeSub, UCXiTongXianShiSub.cs:248) and has no global one.

They were a global preference drawn over every element; now they are the
CLI/API's "all my clocks" edit: every time (or date) element on ``key``, or on
every device when ``key`` is None.  Nothing else in the layout moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

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
    UpdateOverlayElement,
)
from trcc.core.events import OverlayChanged

_A, _B = "0402:3922", "87ad:70db"


class _Clock(TypedDict, total=False):
    """The clock fields each element below sets."""

    source: str
    format: str


@pytest.fixture
def app(tmp_path: Path) -> App:
    app = App(platform=FakePlatform(tmp_path))
    app.set_renderer(QtRenderer())
    for key in (_A, _B):
        for eid, kind in (("t", _Clock(source="time", format="%H:%M")),
                          ("d", _Clock(source="date", format="%m/%d")),
                          ("w", _Clock(source="weekday"))):
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


# ── A clock source the renderer cannot draw is refused, not saved blank ────
#
# clock_text returns "" for any source but time / date / weekday, so an element
# saved with "Time" or "clock" drew nothing forever, and only a per-frame DEBUG
# line said why.  The `type: ignore` on AddOverlayElement's source hid it
# (2026-10-20).


@pytest.mark.parametrize("bad", ["Time", "clock", ""])
def test_an_unknown_clock_source_is_refused_and_nothing_is_saved(
        app: App, bad: str) -> None:
    """MUTATION CHECK: drop either Command's _is_clock_source check -> fails."""
    before = _formats(app, _A)

    added = app.dispatch(AddOverlayElement(
        key=_A, element_id="bad", type="clock", source=bad))
    updated = app.dispatch(UpdateOverlayElement(
        key=_A, element_id="t", source=bad))

    expected = (f"Invalid clock source {bad!r} (expected one of time, weekday, "
                "date)")
    assert (added.ok, added.message) == (False, expected)
    assert (updated.ok, updated.message) == (False, expected)
    assert _formats(app, _A) == before


def test_every_drawable_clock_source_is_accepted(app: App) -> None:
    for source in ("time", "weekday", "date"):
        assert app.dispatch(UpdateOverlayElement(
            key=_A, element_id="t", source=source)).ok


def test_a_source_on_a_non_clock_element_is_ignored_not_refused(app: App) -> None:
    """Only a clock draws ``source``.  The first cut of the check refused a
    metric element saved with source="metric" -- test_theme_persistence caught
    it (2026-10-20).

    MUTATION CHECK: refuse an unknown source for every type -> fails."""
    added = app.dispatch(AddOverlayElement(
        key=_A, element_id="m", type="metric", metric="cpu:temp", source="metric"))
    assert added.ok
    assert added.element is not None and added.element.source == "time"


# ── An element type nothing draws is refused, by the Literal's own spelling ──
#
# The three types were spelled out at both checks in the Commands, again in
# qtgui's editor and in the CLI help -- four copies of OverlayElementType.  All
# now read the Literal (2026-10-20); this branch had no test at all.


def test_an_unknown_element_type_is_refused_and_nothing_is_saved(app: App) -> None:
    """MUTATION CHECK: let AddOverlayElement accept any type -> fails."""
    before = _formats(app, _A)

    added = app.dispatch(AddOverlayElement(key=_A, element_id="box", type="box"))

    assert (added.ok, added.message) == (
        False, "Invalid element type 'box' (expected one of text, metric, clock, spectrum)")
    assert _formats(app, _A) == before
