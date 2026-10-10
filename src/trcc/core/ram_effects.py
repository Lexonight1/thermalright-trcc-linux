"""Whether an effect can take the settings asked of it -- the one rule set.

``EFFECT_TRAITS`` (``core/models``) says what each effect takes; this is the
check against it, used by the RAM driver before it builds a packet and by
``SetRamEffect`` before it changes anything -- so a request the stick cannot
take turns nothing off and writes nothing.
"""
from __future__ import annotations

import logging

from .models import EFFECT_TRAITS, RamEffect, RamEffectSettings

log = logging.getLogger(__name__)


def effect_problem(settings: RamEffectSettings) -> str | None:
    """Why *settings* is not something its effect can take, or None."""
    traits = EFFECT_TRAITS[settings.effect]
    log.debug("effect_problem: %s", settings)
    if not 0 <= settings.brightness <= 255:
        return f"brightness is 0-255, not {settings.brightness}"
    if (settings.direction is not None and traits.directions
            and settings.direction not in traits.directions):
        return (f"{settings.effect.value} cannot move "
                f"{settings.direction.value}")
    random = traits.random and settings.random_colors
    if (traits.colors and not random and settings.effect is not RamEffect.STATIC
            and len(settings.colors) < traits.colors):
        return (f"{settings.effect.value} takes {traits.colors} colour(s), "
                f"got {len(settings.colors)}")
    if any(not (len(c) == 3 and all(0 <= v <= 255 for v in c))
           for c in settings.colors):
        return "a colour is three values of 0-255"
    return None
