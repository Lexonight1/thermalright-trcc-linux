"""resolve_overlay_elements — the single effective overlay layout.

Legacy held ONE overlay config and replaced it; the cutover split it into
three persisted sources (user edits / applied mask / theme) that were
wrongly stacked at render time.  These tests lock the precedence that
restores legacy's single-layout semantics: exactly one source wins and is
returned, never added on top of another.

Precedence: user (if any) > mask (if not None) > theme["elements"].
"""
from __future__ import annotations

from trcc.core.models import OverlayElement
from trcc.services.overlay import resolve_overlay_elements


def _el(eid: str, text: str, x: int = 0, y: int = 0) -> OverlayElement:
    return OverlayElement(id=eid, type="text", x=x, y=y, text=text)


_THEME = {"elements": [{"id": "t0", "type": "text", "x": 1, "y": 1, "text": "theme"}]}


def test_theme_only_when_no_overrides() -> None:
    assert resolve_overlay_elements(_THEME, None) == _THEME["elements"]


def test_empty_theme_resolves_to_empty_list() -> None:
    assert resolve_overlay_elements({}, None) == []
    assert resolve_overlay_elements({"elements": None}, None) == []


def test_user_replaces_theme() -> None:
    user = [_el("u0", "user")]
    result = resolve_overlay_elements(_THEME, user)
    assert result == [u.to_dict() for u in user]
    assert all(e["text"] != "theme" for e in result)


def test_the_working_layer_wins_over_the_theme() -> None:
    user = [_el("u0", "user")]
    result = resolve_overlay_elements(_THEME, user)
    assert result == [u.to_dict() for u in user]
    assert all(e["text"] != "theme" for e in result)


def test_returns_flat_dicts_not_overlay_elements() -> None:
    user = [_el("u0", "user", x=5, y=7)]
    result = resolve_overlay_elements(_THEME, user)
    assert result == [{"id": "u0", "type": "text", "x": 5, "y": 7,
                       "color": "#ffffff", "size": 16, "bold": False,
                       "italic": False, "text": "user"}]


def test_returned_theme_list_is_a_copy() -> None:
    # Callers may pass the result into a {**config, "elements": result} dict;
    # mutating it must not corrupt the source theme config.
    result = resolve_overlay_elements(_THEME, None)
    result.append({"id": "x", "type": "text"})
    assert len(_THEME["elements"]) == 1


def test_empty_user_list_draws_nothing_and_does_not_fall_back() -> None:
    """The mirror of ``test_empty_mask_list_still_overrides_theme``.

    The mask layer has always been sentinel-aware; the user layer was not, so
    an emptied layout fell through to the mask's or the theme's and the last
    element the user deleted reappeared (#276).  ``None`` = no layout of my
    own, ``[]`` = my layout is empty.
    """
    assert resolve_overlay_elements(_THEME, []) == []
    assert resolve_overlay_elements(_THEME, []) == []


def test_no_user_layer_still_falls_back() -> None:
    """The other half — a device that never established a layout gets the
    theme's, exactly as before.  This is what the v1→v2 config migration
    preserves for every existing user."""
    assert resolve_overlay_elements(_THEME, None) == _THEME["elements"]


# =========================================================================
# TASK 2 — "no reading" is TWO different diagnoses
# =========================================================================


def test_an_unsupported_metric_says_so_instead_of_blaming_the_tick(caplog):
    """A host with no such sensor must not read as a transient miss.

    Both cases arrive at ``_draw_metric`` as ``value is None``.  Warning
    identically sends a reporter (and me) hunting a transient that cannot
    happen — the element will be blank on every tick, forever.
    """
    import logging

    from trcc.adapters.render.qt import QtRenderer
    from trcc.services.overlay import OverlayService

    svc = OverlayService(QtRenderer(), unsupported=lambda: frozenset({"cpu:power"}))
    surface = svc._r.create_surface(64, 64)

    with caplog.at_level(logging.WARNING, logger="trcc.services.overlay"):
        svc._draw_metric(
            surface, {"type": "metric", "metric": "cpu:power", "x": 0, "y": 0},
            {},                                  # nothing read this tick
        )

    assert "UNSUPPORTED" in caplog.text
    assert "always be blank" in caplog.text


def test_a_supported_metric_that_missed_a_tick_says_THAT(caplog):
    """The other half — the host CAN read it, so this one may resolve."""
    import logging

    from trcc.adapters.render.qt import QtRenderer
    from trcc.services.overlay import OverlayService

    svc = OverlayService(QtRenderer(), unsupported=lambda: frozenset({"cpu:power"}))
    surface = svc._r.create_surface(64, 64)

    with caplog.at_level(logging.WARNING, logger="trcc.services.overlay"):
        svc._draw_metric(
            surface, {"type": "metric", "metric": "cpu:temp", "x": 0, "y": 0},
            {},
        )

    assert "UNSUPPORTED" not in caplog.text
    assert "this tick" in caplog.text
    assert "CAN read it" in caplog.text


def test_the_app_actually_wires_the_unsupported_set(fake_platform) -> None:
    """The injection is load-bearing and its default is SILENT.

    ``OverlayService(renderer)`` defaults to an empty set, so an App that
    forgets to pass one degrades straight back to "every miss looks
    transient" with nothing failing.  Gate the wire, not just the branch —
    the branch test passes either way.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App

    known = frozenset({"cpu:power", "disk:temp"})
    sensors = fake_platform.sensors()
    sensors.unsupported = lambda: known          # type: ignore[method-assign]

    app = App(platform=fake_platform, renderer=QtRenderer())

    assert app.display._overlay._unsupported() == known, (
        "App built an OverlayService without the host's unsupported set"
    )
