"""``ui.presentation.device_presentation`` — the toolkit-free backbone.

Pure pytest, no QApplication: ``presentation_for`` is a function over the
normalized device row (``ProductInfo``), so a graphical UI joins against it
instead of re-deriving device→view.  Parametrized over the real registry so
it can't drift from the device tables.
"""
from __future__ import annotations

from trcc.core.models import Kind
from trcc.core.registry import ALL_DEVICES, find_product
from trcc.ui.presentation import DevicePresentation, presentation_for
from trcc.ui.presentation.device_presentation import VIEW_FORM, VIEW_LED


def test_presentation_matches_kind_for_every_registered_device() -> None:
    """Every device's presentation is derived from its row — never drifts."""
    for info in ALL_DEVICES.values():
        p = presentation_for(info.kind)
        assert p.kind is info.kind
        if info.kind is Kind.LED:
            assert p.view_name == VIEW_LED
            assert p.shows_metric_gauges is True
        else:
            assert p.view_name == VIEW_FORM
            assert p.shows_metric_gauges is False


def test_led_device_presents_led_view_with_gauges() -> None:
    info = find_product(0x0416, 0x8001)  # LED controller
    assert info is not None
    assert presentation_for(info.kind) == DevicePresentation(
        kind=Kind.LED, view_name=VIEW_LED, shows_metric_gauges=True,
    )


def test_lcd_device_presents_form_view() -> None:
    info = find_product(0x0402, 0x3922)  # SCSI LCD (verified path)
    assert info is not None
    assert presentation_for(info.kind) == DevicePresentation(
        kind=Kind.LCD, view_name=VIEW_FORM, shows_metric_gauges=False,
    )
