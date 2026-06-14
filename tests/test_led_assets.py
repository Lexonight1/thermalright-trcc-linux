"""LED control panel renders its zone buttons + carousel (GUI behaviour).

These drive the REAL View binding — construct ``UCLedControl`` and call
``initialize()`` exactly as ``LEDHandler.show`` does — then assert what the user
would actually see: each zone button gets its background image and is visible,
and the carousel ("Circulate") shows for multi-zone styles.

This is the test that would have caught the shipped bug: the cutover left
``LedStyleSpec.zone_assets`` empty, so ``_apply_zone_images`` early-returned and
every zone button rendered BLANK.  A pure-data "does the asset resolve" check
passes while the panel is visibly broken — so we exercise the panel itself.
"""
from __future__ import annotations

import pytest

from trcc.core.led_models import LED_STYLES, LEGACY_STYLE_ID

_MULTIZONE = [s for s in LED_STYLES if LED_STYLES[s].zone_count > 1]
_NOZONE = [s for s in LED_STYLES if LED_STYLES[s].zone_count == 0]


def _panel(qtbot):
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_led_control import UCLedControl
    set_assets_dir(_PKG_ASSETS_DIR)
    panel = UCLedControl()
    qtbot.addWidget(panel)
    return panel


@pytest.mark.parametrize("style", _MULTIZONE, ids=lambda s: s.name)
def test_multizone_panel_zone_buttons_render(style, qtbot) -> None:
    """Every zone button (up to zone_count) shows + carries its background
    image — not the blank flat fallback."""
    spec = LED_STYLES[style]
    panel = _panel(qtbot)
    panel.initialize(LEGACY_STYLE_ID[style], spec.segment_count,
                     spec.zone_count, model=spec.model_name)

    for i in range(spec.zone_count):
        btn = panel._zone_buttons[i]
        assert btn.isVisibleTo(panel), f"{style.name} zone[{i}] not visible"
        assert "background-image" in btn.styleSheet(), (
            f"{style.name} zone[{i}] rendered BLANK — no zone image applied"
        )
    # "Circulate" carousel is the multi-zone control row.
    assert panel._carousel_btn.isVisibleTo(panel), \
        f"{style.name} carousel/Circulate not visible"


@pytest.mark.parametrize("style", _NOZONE, ids=lambda s: s.name)
def test_singlezone_panel_hides_zone_buttons(style, qtbot) -> None:
    """A zero-zone style (LC2/LF13) shows no zone buttons or carousel."""
    spec = LED_STYLES[style]
    panel = _panel(qtbot)
    panel.initialize(LEGACY_STYLE_ID[style], spec.segment_count,
                     spec.zone_count, model=spec.model_name)

    assert not any(b.isVisibleTo(panel) for b in panel._zone_buttons), \
        f"{style.name} should show no zone buttons"
    assert not panel._carousel_btn.isVisibleTo(panel), \
        f"{style.name} should show no carousel"


def test_every_zone_asset_resolves_to_a_bundled_png() -> None:
    """Fast data guard (no Qt): every referenced zone image is bundled."""
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR
    missing = sorted({
        name
        for spec in LED_STYLES.values()
        for pair in spec.zone_assets
        for name in pair
        if not (_PKG_ASSETS_DIR / f"{name}.png").exists()
    })
    assert not missing, f"zone-button assets with no bundled .png: {missing}"
