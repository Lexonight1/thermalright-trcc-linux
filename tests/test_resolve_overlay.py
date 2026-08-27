"""ResolveOverlay — asking what is actually on a device's screen.

The read side of overlay.  Seven overlay Commands mutate; none could
answer, so both Qt skins reached past the bus and cli/api could not ask at
all.

The load-bearing case is a theme-supplied layout.  A theme's elements come
from a ``config1.dc`` parse and carry NO id — every shipped theme is in
that state — so "flash element 3" had nothing to name, and the id the GUI
invented (a bare index) matched nothing.  ``test_theme_layout_ids_are_``
``flashable`` is that bug, reproduced against real parsed DC bytes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    EnableOverlay,
    FlashOverlayElement,
    ResolveOverlay,
    SetOverlayConfig,
)
from trcc.core.models import OverlayElement, Theme
from trcc.services.overlay import effective_overlay_layout, overlay_source

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "0416", "pid": "5302",
         "resolution": "320x320", "pm": 51, "sub": 0}
_VID, _PID = 0x0416, 0x5302
_KEY = "0416:5302"

# The shape a config1.dc parse produces: no "id" key anywhere.
_THEME_ELEMENTS = [
    {"type": "metric", "metric": "cpu:temp", "x": 10, "y": 20, "size": 24},
    {"type": "text", "text": "CPU", "x": 10, "y": 50, "size": 16},
    {"type": "clock", "source": "time", "x": 10, "y": 80, "size": 20},
]


@pytest.fixture
def app(tmp_path: Path) -> App:
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(_VID, _PID)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


def _load_theme(app: App, tmp_path: Path, elements: list[dict]) -> Theme:
    theme = Theme(
        path=tmp_path / "theme", name="Theme1", resolution=(320, 320),
        config={"elements": elements},
    )
    app.active_themes[_KEY] = theme
    return theme


# ── The bug this Command exists to close ─────────────────────────────────


def test_theme_layout_ids_are_flashable(app: App, tmp_path: Path) -> None:
    """A stock theme, nothing edited, no mask — the default state.

    Before: the theme's elements had no id, the GUI fell back to the bare
    index, and FlashOverlayElement answered "element '2' not found".
    """
    _load_theme(app, tmp_path, _THEME_ELEMENTS)

    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.ok
    assert layout.source == "theme"
    assert [e.id for e in layout.elements] == ["el_0", "el_1", "el_2"]

    # The id handed out here must be the id looked up there.
    for entry in layout.elements:
        flashed = app.dispatch(FlashOverlayElement(
            key=_KEY, element_id=entry.id,
        ))
        assert flashed.ok, flashed.message


def test_ids_are_stable_across_calls(app: App, tmp_path: Path) -> None:
    """Positional, not random — a uuid would differ on the second resolve
    and the id a UI is holding would stop matching."""
    _load_theme(app, tmp_path, _THEME_ELEMENTS)

    first = app.dispatch(ResolveOverlay(key=_KEY)).elements
    second = app.dispatch(ResolveOverlay(key=_KEY)).elements

    assert [e.id for e in first] == [e.id for e in second]


def test_a_real_id_is_never_shadowed_by_a_mint() -> None:
    """A theme element literally named ``el_0`` must keep the name, and the
    id-less element at index 0 must be given a different one."""
    config = {"elements": [
        {"type": "text", "text": "no id", "x": 0, "y": 0},
        {"type": "text", "text": "owns el_0", "x": 0, "y": 0, "id": "el_0"},
    ]}

    out = effective_overlay_layout(config, None)

    assert out[1]["id"] == "el_0"
    assert out[0]["id"] != "el_0"
    assert len({e["id"] for e in out}) == 2


# ── Precedence: one layer wins, never stacked ────────────────────────────


def test_user_layer_wins_and_reports_itself(app: App, tmp_path: Path) -> None:
    _load_theme(app, tmp_path, _THEME_ELEMENTS)
    app.dispatch(SetOverlayConfig(key=_KEY, elements=(
        {"id": "mine", "type": "text", "text": "edited", "x": 1, "y": 2},
    )))

    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.source == "user"
    assert [e.id for e in layout.elements] == ["mine"]


def test_an_adopted_mask_layout_wins_over_the_theme(
    app: App, tmp_path: Path,
) -> None:
    """A mask REPLACES the theme's layout, as it always did.

    It used to do so from a ``mask_overlay_elements`` layer the resolver
    ranked ahead of the theme.  ``ApplyMask`` now adopts the mask's layout
    into the device's one working layer (the C# reads a mask's config1.dc
    into the same array a theme load fills), so there is no mask layer left
    to rank — and the observable answer is identical.
    """
    _load_theme(app, tmp_path, _THEME_ELEMENTS)
    app.settings.set_user_overlay_elements(_KEY, [
        OverlayElement(id="m1", type="text", text="mask", x=3, y=4),
    ])

    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.source == "user"
    assert [e.id for e in layout.elements] == ["m1"]


def test_an_emptied_layout_is_distinguishable_from_no_layout(
    app: App, tmp_path: Path,
) -> None:
    """Both answer zero elements; ``source`` is what tells them apart.

    ``[]`` is a layout the user emptied and it draws nothing; ``None`` is no
    layout of its own and the theme's shows.  Conflating the two is #276.
    """
    _load_theme(app, tmp_path, _THEME_ELEMENTS)
    app.settings.set_user_overlay_elements(_KEY, [])

    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.ok
    assert layout.elements == []
    assert layout.source == "user"


# ── The empty answers, which are not failures ────────────────────────────


def test_no_theme_is_ok_and_empty(app: App) -> None:
    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.ok
    assert layout.elements == []
    assert layout.theme_name == ""


def test_unknown_device_is_ok_not_a_failure(app: App) -> None:
    """Overlay layout is a settings concern, not a device-attachment one —
    and ok=False escalates to WARNING on every poll."""
    layout = app.dispatch(ResolveOverlay(key="dead:beef"))

    assert layout.ok
    assert layout.elements == []


def test_disabled_overlay_still_reports_its_elements(
    app: App, tmp_path: Path,
) -> None:
    """``enabled`` reports state; it does not filter.  The renderer gates on
    the same flag, so a caller needs both to know what is on the glass."""
    _load_theme(app, tmp_path, _THEME_ELEMENTS)
    assert app.dispatch(EnableOverlay(key=_KEY, enabled=False)).ok

    layout = app.dispatch(ResolveOverlay(key=_KEY))

    assert layout.ok
    assert layout.enabled is False
    assert len(layout.elements) == 3
    assert "overlay disabled" in layout.message


# ── overlay_source: the precedence, reported rather than applied ─────────


@pytest.mark.parametrize(("user", "expected"), [
    # ``None`` = this device has no layout of its own, so the theme's shows.
    # ``[]``   = it HAS one and the user emptied it — that still wins, and
    # draws nothing.  Conflating those two is #276.
    #
    # There is no mask arm any more: a mask is not a layer, it is a source
    # that ApplyMask adopts INTO this one (2.1.6 FormCZTV.cs:5935 reads a
    # mask's config1.dc into the same array a theme load fills).
    (None, "theme"),
    ([], "user"),
    ([OverlayElement(id="u", type="text")], "user"),
])
def test_overlay_source_names_the_winning_layer(
    user: list[OverlayElement] | None, expected: str,
) -> None:
    assert overlay_source(user) == expected


# ── The seed guard the qtgui editor reads ────────────────────────────────
#
# ``overlay_editor.refresh`` adopts the active layout into the editable user
# layer when the device has none of its own.  It used to decide that with
# ``if not settings.user_overlay_elements`` — a TRUTHINESS test, which cannot
# tell "this device has no layer" from "the user emptied it".  So deleting the
# last element re-seeded it straight back from the theme, which is #276's
# second symptom in the reporter's own words: "whichever one I delete LAST
# still appears".
#
# ``source`` answers the question the truthiness test could not.


def test_an_emptied_user_layer_reports_user_not_theme(
    app: App, tmp_path: Path,
) -> None:
    """``[]`` is a user layer that is empty — NOT the absence of one.

    This is the distinction the editor's seed guard now reads.  If ``source``
    ever came back "theme" here, the editor would re-seed an emptied layout
    and the deleted elements would return.
    """
    _load_theme(app, tmp_path, [{"type": "text", "text": "from-theme"}])
    app.settings.for_device(_KEY).user_overlay_elements = []

    result = app.dispatch(ResolveOverlay(key=_KEY))

    assert result.source == "user", (
        "an emptied user layer must report itself — reporting 'theme' is what "
        "makes a deleted element come back"
    )
    assert result.elements == []


def test_no_user_layer_reports_theme_so_the_editor_seeds(
    app: App, tmp_path: Path,
) -> None:
    """``None`` means the device has no layout of its own — seed it.

    The other half of the same distinction: this is the case where adopting
    the theme layout into the editable layer is CORRECT.
    """
    _load_theme(app, tmp_path, [{"type": "text", "text": "from-theme"}])
    app.settings.for_device(_KEY).user_overlay_elements = None

    result = app.dispatch(ResolveOverlay(key=_KEY))

    assert result.source == "theme"
    assert len(result.elements) == 1
    assert result.elements[0].id, "an adopted element must be addressable"


def test_every_entry_field_survives_the_round_trip_the_editor_makes() -> None:
    """The editor seeds by ``asdict(entry)`` -> ``SetOverlayConfig``.

    A hand-written mapping is where a field goes missing silently: ``font``
    was once added to ``to_dict`` and never read back, so every user element
    lost its font on restart.  All three element types, not just the one to
    hand.
    """
    import dataclasses

    from trcc.core.results import OverlayElementEntry

    for entry in (
        OverlayElementEntry(id="t", type="text", text="hi", font="X", bold=True),
        OverlayElementEntry(id="m", type="metric", metric="cpu:temp",
                            format="{value}", show_unit=False, font="Y"),
        OverlayElementEntry(id="c", type="clock", source="date",
                            format="%Y", size=22, color="#abcdef"),
    ):
        back = OverlayElement.from_dict(dataclasses.asdict(entry))
        for field in dataclasses.fields(entry):
            assert getattr(back, field.name) == getattr(entry, field.name), (
                f"{entry.type} element lost {field.name!r} on the round trip"
            )
