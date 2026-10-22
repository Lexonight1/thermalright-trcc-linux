"""TRCC's own effect clock: the lights it drives run a built-in effect or a
linked colour, drawn here a frame at a time.

The follower (``rgb_mirror``) sends what this draws when its source is
``EFFECT_SOURCE``.  A built-in effect is drawn from its settings
(``effect_frames``); a linked colour from the App's last raw sensor sample,
through ``LIGHT_GRADIENTS`` -- and written only when it changes, since a RAM
stick's write is on the SMBus.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

from ..core.led_models import Gradient, linked_color
from ..core.logs import per_frame
from ..core.models import (
    LightEffect,
    LinkedEffect,
    LinkedMetric,
    RamEffect,
    RamEffectSettings,
    RgbMirrorDevice,
)
from .effect_frames import effect_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]
Columns = tuple[tuple[Rgb, ...], ...]
#: The App's last raw sensor sample, None before its first.
Readings = Callable[[], Mapping[str, float] | None]

#: A moving effect is drawn this often -- as often as an LCD is sampled --
#: and a Static or linked one once a second.
EFFECT_INTERVAL_S = 1 / 20
STATIC_INTERVAL_S = 1.0
#: A linked colour is written when it changes, and again this often in case
#: the light lost it.
LINKED_RESEND_S = 30.0

_GREEN, _YELLOW, _ORANGE, _RED = (0, 255, 0), (255, 255, 0), (255, 110, 0), (255, 0, 0)
#: The scale a linked light shows -- the maintainer's (2026-10-21): green is
#: fine, yellow light, orange heavy, red very heavy or too hot.  Load in %,
#: temperature in °C.  The LED cooler keeps the Windows app's own scale
#: (``ColorEngine``), cyan to red.
LIGHT_GRADIENTS: dict[LinkedMetric, Gradient] = {
    "load": ((25.0, _GREEN), (50.0, _YELLOW), (75.0, _ORANGE), (90.0, _RED)),
    "temp": ((50.0, _GREEN), (65.0, _YELLOW), (80.0, _ORANGE), (90.0, _RED)),
}


class EffectClock:
    """Each light's effect, by ref, and the colours it shows now."""

    def __init__(self, readings: Readings | None = None) -> None:
        log.debug("EffectClock.__init__: readings=%s", readings is not None)
        self._readings = readings
        self.effects: Mapping[str, LightEffect] = {}
        self._start = 0.0
        self._last: dict[str, Rgb] = {}      # a linked light's last colour
        self._sent: dict[str, tuple[tuple[Rgb, ...], float]] = {}

    def set(self, effects: Mapping[str, LightEffect]) -> None:
        """Run *effects* from now -- empty to stop; every light is sent afresh."""
        log.info("EffectClock.set: %s", effects)
        self.effects, self._start, self._sent = dict(effects), time.monotonic(), {}

    def interval(self) -> float:
        """How long until the next frame: once a second when nothing moves."""
        moving = any(isinstance(e, RamEffectSettings) and e.effect is not RamEffect.STATIC
                     for e in self.effects.values())
        frame_log.debug("EffectClock.interval: moving=%s", moving)
        return EFFECT_INTERVAL_S if moving else STATIC_INTERVAL_S

    def columns(self, devices: tuple[RgbMirrorDevice, ...]) -> Columns:
        """Each of *devices*' colours now -- device *i* is the *i*-th light,
        so a left / right effect travels across them."""
        t = time.monotonic() - self._start
        readings = (self._readings() if self._readings else None) or {}
        frame_log.debug("EffectClock.columns: %d light(s) t=%.2f", len(devices), t)
        return tuple(self._column(d.ref, i, len(devices), t, readings)
                     for i, d in enumerate(devices))

    def _column(self, ref: str, i: int, count: int, t: float,
                readings: Mapping[str, float]) -> tuple[Rgb, ...]:
        """Light *ref*'s colours: its effect drawn at *t*, or its linked colour
        -- the last one while its reading is missing, dark before any."""
        effect = self.effects.get(ref)
        frame_log.debug("EffectClock._column: %s %s", ref, effect)
        if isinstance(effect, LinkedEffect):
            color = linked_color(effect.metric, effect.source, readings,
                                 LIGHT_GRADIENTS[effect.metric])
            if color is not None:
                self._last[ref] = color
            return (self._last.get(ref, (0, 0, 0)),)
        if effect is None:
            return ((0, 0, 0),)
        return tuple(effect_frame(effect, t, i, count))

    def unchanged(self, ref: str, column: tuple[Rgb, ...]) -> bool:
        """Whether light *ref* runs a linked colour it was last sent, and
        recently -- then nothing is written."""
        if not isinstance(self.effects.get(ref), LinkedEffect):
            return False
        now = time.monotonic()
        sent = self._sent.get(ref)
        if sent is not None and sent[0] == column and now - sent[1] < LINKED_RESEND_S:
            frame_log.debug("EffectClock.unchanged: %s still %s", ref, column[0])
            return True
        log.debug("EffectClock.unchanged: %s -> %s", ref, column[0])
        self._sent[ref] = (column, now)
        return False
