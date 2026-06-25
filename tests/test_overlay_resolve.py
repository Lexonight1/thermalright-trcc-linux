"""resolve_overlay_elements — the single effective overlay layout.

Legacy held ONE overlay config and replaced it; the cutover split it into
three persisted sources (user edits / applied mask / theme) that were
wrongly stacked at render time.  These tests lock the precedence that
restores legacy's single-layout semantics: exactly one source wins and is
returned, never added on top of another.

Precedence: user (if any) > mask (if not None) > theme["elements"].
"""
from __future__ import annotations

import pytest

from trcc.core.models import OverlayElement
from trcc.services.overlay import (
    orient_overlay_elements,
    resolve_overlay_elements,
)


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


# ── orient_overlay_elements — synthesise the missing portrait variant ──
#
# A local theme saved landscape-only (DC rotation=0) viewed at 90/270 has no
# portrait variant on disk, so the landscape DC's coords (x up to the native
# width) clip on the narrower portrait canvas.  The transform rotates the
# CENTRE coords into the transposed canvas so nothing clips — exactly the
# coords a cloud portrait DC would carry. (#dc-clip-90)

_LANDSCAPE = (320, 240)   # native (w, h); transposed canvas is 240×320


def _elem(x: int, y: int) -> dict:
    return {"id": "m", "type": "metric", "x": x, "y": y, "metric": "cpu:freq"}


@pytest.mark.parametrize("degrees", [90, 270])
def test_right_edge_coord_fits_portrait_canvas(degrees: int) -> None:
    """The clipping case: x=250 on a 320-wide landscape DC lands inside the
    240-wide portrait canvas after the rotation (was the cut-off ``796 MHz``)."""
    [out] = orient_overlay_elements([_elem(250, 120)], _LANDSCAPE, degrees)
    assert 0 <= out["x"] < 240, f"x={out['x']} must fit the 240-wide canvas"
    assert 0 <= out["y"] < 320, f"y={out['y']} must fit the 320-tall canvas"


def test_90_and_270_are_exact_opposite_rotations() -> None:
    """90 and 270 share the transposed canvas but place a point on opposite
    sides — the reason orientation is in the overlay cache key."""
    src = _elem(250, 120)
    [at90] = orient_overlay_elements([src], _LANDSCAPE, 90)
    [at270] = orient_overlay_elements([src], _LANDSCAPE, 270)
    assert (at90["x"], at90["y"]) != (at270["x"], at270["y"])
    # 90: (h-1-y, x) = (119, 250) ; 270: (y, w-1-x) = (120, 69)
    assert (at90["x"], at90["y"]) == (240 - 1 - 120, 250)
    assert (at270["x"], at270["y"]) == (120, 320 - 1 - 250)


def test_every_landscape_corner_stays_in_bounds() -> None:
    """Sweep the landscape extents — no element can ever land off-canvas."""
    corners = [_elem(x, y) for x in (0, 319) for y in (0, 239)]
    for degrees in (90, 270):
        for out in orient_overlay_elements(corners, _LANDSCAPE, degrees):
            assert 0 <= out["x"] < 240 and 0 <= out["y"] < 320


def test_preserves_every_other_field() -> None:
    """Only x/y change; type, metric, id, etc. pass through untouched."""
    [out] = orient_overlay_elements([_elem(44, 60)], _LANDSCAPE, 90)
    assert out["id"] == "m" and out["type"] == "metric"
    assert out["metric"] == "cpu:freq"


def test_returns_new_dicts_not_mutating_input() -> None:
    src = _elem(44, 60)
    orient_overlay_elements([src], _LANDSCAPE, 90)
    assert (src["x"], src["y"]) == (44, 60)


def test_empty_list_is_noop() -> None:
    assert orient_overlay_elements([], _LANDSCAPE, 90) == []
