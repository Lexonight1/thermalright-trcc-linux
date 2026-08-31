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

import logging
from dataclasses import dataclass

from ...core.models import Kind

log = logging.getLogger(__name__)

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


def presentation_for(kind: Kind) -> DevicePresentation:
    """Map a device :class:`Kind` to its presentation contract.

    Both graphical UIs agree on what a device presents without re-deriving it.

    **Takes the Kind, not the whole ``ProductInfo``.**  It only ever consulted
    ``info.kind``, and demanding the domain object meant a caller had to hold
    one — which is what kept ``trcc_app`` reaching ``app.devices`` for a live
    ``Device`` just to build a handler.  ``DeviceStateResult`` and
    ``DeviceEntry`` both carry ``kind``, so the narrower signature is what lets
    the composition root work from a Result instead.  It is also trivially
    testable: no ``ProductInfo`` to construct.
    """
    if kind is Kind.LED:
        log.info("presentation_for: %s → view=%s gauges=True (LED)",
                 kind.name, VIEW_LED)
        return DevicePresentation(
            kind=Kind.LED, view_name=VIEW_LED, shows_metric_gauges=True,
        )
    log.info("presentation_for: %s → view=%s gauges=False (LCD form)",
             kind.name, VIEW_FORM)
    return DevicePresentation(
        kind=Kind.LCD, view_name=VIEW_FORM, shows_metric_gauges=False,
    )
