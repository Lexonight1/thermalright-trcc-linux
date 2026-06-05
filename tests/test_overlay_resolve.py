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
    assert resolve_overlay_elements(_THEME, None, []) == _THEME["elements"]


def test_empty_theme_resolves_to_empty_list() -> None:
    assert resolve_overlay_elements({}, None, []) == []
    assert resolve_overlay_elements({"elements": None}, None, []) == []


def test_mask_replaces_theme_not_stacks() -> None:
    mask = [_el("m0", "mask")]
    result = resolve_overlay_elements(_THEME, mask, [])
    assert result == [m.to_dict() for m in mask]
    # REPLACE, not add: the theme element must not appear.
    assert all(e["text"] != "theme" for e in result)


def test_empty_mask_list_still_overrides_theme() -> None:
    # mask_elements is the empty list (not None) — an applied mask with no
    # metric layout REPLACES the theme with nothing, it does not fall back.
    assert resolve_overlay_elements(_THEME, [], []) == []


def test_user_replaces_theme() -> None:
    user = [_el("u0", "user")]
    result = resolve_overlay_elements(_THEME, None, user)
    assert result == [u.to_dict() for u in user]
    assert all(e["text"] != "theme" for e in result)


def test_user_wins_over_mask_and_theme() -> None:
    user = [_el("u0", "user")]
    mask = [_el("m0", "mask")]
    result = resolve_overlay_elements(_THEME, mask, user)
    assert result == [u.to_dict() for u in user]
    assert all(e["text"] not in ("mask", "theme") for e in result)


def test_returns_flat_dicts_not_overlay_elements() -> None:
    user = [_el("u0", "user", x=5, y=7)]
    result = resolve_overlay_elements(_THEME, None, user)
    assert result == [{"id": "u0", "type": "text", "x": 5, "y": 7,
                       "color": "#ffffff", "size": 16, "bold": False,
                       "italic": False, "text": "user"}]


def test_returned_theme_list_is_a_copy() -> None:
    # Callers may pass the result into a {**config, "elements": result} dict;
    # mutating it must not corrupt the source theme config.
    result = resolve_overlay_elements(_THEME, None, [])
    result.append({"id": "x", "type": "text"})
    assert len(_THEME["elements"]) == 1
