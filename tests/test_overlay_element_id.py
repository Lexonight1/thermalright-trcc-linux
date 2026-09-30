"""Overlay elements carry a stable id, and an edit addresses by that id.

Parsed / older-config elements had no id (``from_dict`` → ``""``), and the GUI
dispatched a bare positional index -- which never matched the element's id, so
the Command silently found nothing (#150/#203).
"""
from __future__ import annotations

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import UpdateOverlayElement
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


def test_an_edit_finds_the_element_by_its_id(fake_platform) -> None:  # type: ignore[no-untyped-def]
    """An element is addressed by its real id; a bare index names nothing."""
    app = App(fake_platform, renderer=QtRenderer())
    key = "0402:3922"
    app.settings.set_user_overlay_elements(key, [
        OverlayElement.from_dict({
            "id": "el_first", "type": "metric", "metric": "cpu:temp",
            "x": 10, "y": 10, "format": "{value:.0f}",
        }),
    ])

    ok = app.dispatch(UpdateOverlayElement(key=key, element_id="el_first", x=20))
    assert ok.ok

    missing = app.dispatch(UpdateOverlayElement(key=key, element_id="0", x=20))
    assert not missing.ok  # a bare index no longer matches — that was the bug
