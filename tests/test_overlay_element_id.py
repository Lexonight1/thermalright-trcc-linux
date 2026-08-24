"""Overlay elements carry a stable id, and Flash addresses by that id.

Parsed / older-config elements had no id (``from_dict`` → ``""``), and the GUI
dispatched a bare positional index for flash — which never matched the element's
id, so click-to-highlight silently failed for every element (#150/#203).
"""
from __future__ import annotations

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import FlashOverlayElement
from trcc.core.models import OverlayElement


def test_from_dict_mints_a_stable_id_when_missing() -> None:
    el = OverlayElement.from_dict({"type": "text", "text": "hi"})
    assert el.id.startswith("el_") and len(el.id) > 3


def test_from_dict_preserves_an_existing_id() -> None:
    el = OverlayElement.from_dict({"id": "el_abc123", "type": "text"})
    assert el.id == "el_abc123"


def test_minted_ids_are_unique() -> None:
    a = OverlayElement.from_dict({"type": "metric", "metric": "cpu:temp"})
    b = OverlayElement.from_dict({"type": "metric", "metric": "cpu:temp"})
    assert a.id != b.id


def test_flash_finds_the_element_by_its_id(fake_platform) -> None:  # type: ignore[no-untyped-def]
    """FlashOverlayElement resolves an element by its real id (the fix)."""
    app = App(fake_platform, renderer=QtRenderer())
    key = "0402:3922"
    app.settings.set_user_overlay_elements(key, [
        OverlayElement.from_dict({
            "id": "el_first", "type": "metric", "metric": "cpu:temp",
            "x": 10, "y": 10, "format": "{value:.0f}",
        }),
    ])

    ok = app.dispatch(FlashOverlayElement(key=key, element_id="el_first"))
    assert ok.ok

    missing = app.dispatch(FlashOverlayElement(key=key, element_id="0"))
    assert not missing.ok  # a bare index no longer matches — that was the bug


def test_flash_finds_an_element_in_the_working_layer(fake_platform) -> None:  # type: ignore[no-untyped-def]
    """Flash must resolve against the layout that is actually on screen.

    Written for a bug where a mask supplied the on-screen layout while the
    user layer was empty, and Flash searched the empty one — "element not
    found" on every click.  That state can no longer exist: ``ApplyMask``
    adopts the mask's layout INTO the one working layer, so there is no
    second layer to search by mistake.

    The test is kept as the end-to-end check that a resolved id round-trips
    back through Flash, but it no longer guards the layer mismatch — that is
    now prevented by the model rather than by this assertion.
    """
    app = App(fake_platform, renderer=QtRenderer())
    key = "0402:3922"
    # State right after ApplyMask: its layout is the working layer.
    app.settings.set_user_overlay_elements(key, [
        OverlayElement.from_dict({
            "id": "el_mask2", "type": "metric", "metric": "cpu:temp",
            "x": 10, "y": 10, "format": "{value:.0f}",
        }),
    ])

    ok = app.dispatch(FlashOverlayElement(key=key, element_id="el_mask2"))
    assert ok.ok  # previously "not found" — Flash only searched the user layer
