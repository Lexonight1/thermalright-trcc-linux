"""The per-element unit toggle (``show_unit``) is universal across UIs.

Mirrors the Windows unit-switch (myModeSub == 1): a metric element either draws
the unit glyph after the number or the bare number (unit baked into the art).
The single ``UpdateOverlayElement`` Command carries the toggle, so the GUI /
CLI / API all flip it the same way and it round-trips through the model and the
API response chain.
"""
from __future__ import annotations

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import UpdateOverlayElement
from trcc.core.commands._helpers import _element_to_entry
from trcc.core.models import OverlayElement


def _app_with_metric(fake_platform, show_unit: bool = True) -> tuple[App, str]:  # type: ignore[no-untyped-def]
    app = App(fake_platform, renderer=QtRenderer())
    key = "0402:3922"
    app.settings.set_user_overlay_elements(key, [
        OverlayElement.from_dict({
            "id": "el_temp", "type": "metric", "metric": "cpu:temp",
            "x": 10, "y": 10, "format": "{value:.0f}°C", "show_unit": show_unit,
        }),
    ])
    return app, key


def test_update_toggles_show_unit(fake_platform) -> None:  # type: ignore[no-untyped-def]
    app, key = _app_with_metric(fake_platform, show_unit=True)

    result = app.dispatch(
        UpdateOverlayElement(key=key, element_id="el_temp", show_unit=False),
    )
    assert result.ok
    assert result.element is not None
    assert result.element.show_unit is False

    stored = app.settings.for_device(key).user_overlay_elements[0]
    assert stored.show_unit is False


def test_update_can_turn_show_unit_back_on(fake_platform) -> None:  # type: ignore[no-untyped-def]
    app, key = _app_with_metric(fake_platform, show_unit=False)

    result = app.dispatch(
        UpdateOverlayElement(key=key, element_id="el_temp", show_unit=True),
    )
    assert result.ok
    assert app.settings.for_device(key).user_overlay_elements[0].show_unit is True


def test_update_without_show_unit_leaves_it_unchanged(fake_platform) -> None:  # type: ignore[no-untyped-def]
    """Omitting show_unit (None) must not clobber the stored value."""
    app, key = _app_with_metric(fake_platform, show_unit=False)

    app.dispatch(UpdateOverlayElement(key=key, element_id="el_temp", size=40))
    stored = app.settings.for_device(key).user_overlay_elements[0]
    assert stored.show_unit is False  # untouched
    assert stored.size == 40


def test_show_unit_survives_model_and_entry_round_trip() -> None:
    """model → to_dict → from_dict and model → OverlayElementEntry keep it."""
    el = OverlayElement.from_dict({
        "id": "e", "type": "metric", "metric": "gpu:primary:temp",
        "format": "{value:.0f}°C", "show_unit": False,
    })
    assert OverlayElement.from_dict(el.to_dict()).show_unit is False
    assert _element_to_entry(el).show_unit is False
