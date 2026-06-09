"""Device presentation backbone — what a device presents, toolkit-free.

The single source of "given a device (resolved from its vid/pid + handshake
into a :class:`ProductInfo`), what should a graphical UI present for it?" — its
kind, which view it occupies, and whether it shows the LED metric gauges.

Derived purely from ``ProductInfo`` (no Qt, no widgets), so the graphical
front-ends (``ui/gui``, ``ui/qtgui``) bind their own toolkit widgets to ONE
shared contract instead of each re-deriving device→view from ``device.is_led``.
The unified UI's backbone: a device decides what it presents, once, here.

Unit-testable with plain ``pytest`` — there is no toolkit here.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...core.models import Kind, ProductInfo

#: View identifiers the graphical UIs switch their panel stack on.
VIEW_LED = "led"
VIEW_FORM = "form"


@dataclass(frozen=True, slots=True)
class DevicePresentation:
    """What a device presents in a graphical UI — the toolkit-free contract.

    ``kind``: LED vs LCD (from :class:`ProductInfo`).
    ``view_name``: which panel the UI shows — ``"led"`` (segment panel) or
    ``"form"`` (the theme/preview form).
    ``shows_metric_gauges``: whether the view has the M1-M6 CPU/GPU gauges that
    a metrics tick must populate (LED segment panels do; the LCD form renders
    metrics into the composited frame instead).
    """
    kind: Kind
    view_name: str
    shows_metric_gauges: bool


def presentation_for(info: ProductInfo) -> DevicePresentation:
    """Map a device's :class:`ProductInfo` to its presentation contract.

    Keyed on ``info.kind`` (resolved from the vid/pid + handshake), so both
    graphical UIs agree on what a device presents without re-deriving it.
    """
    if info.kind is Kind.LED:
        return DevicePresentation(
            kind=Kind.LED, view_name=VIEW_LED, shows_metric_gauges=True,
        )
    return DevicePresentation(
        kind=Kind.LCD, view_name=VIEW_FORM, shows_metric_gauges=False,
    )
