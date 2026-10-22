"""RGB Lighting's saved settings -- its own section of TRCC's settings file
(``module_settings["rgb"]``), read and written through its host.

The same names and the same checks they had as app-wide settings, before
RGB was a module: a value this build cannot read is never guessed -- an
unknown mode is off, a damaged record dropped.  Moved there once, by the
settings file's schema 7.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any, cast, get_args

from trcc.core._colors import rgb_rows
from trcc.core.models import LinkedMetric, SensorLink, parse_device_key
from trcc.core.ports import SettingsPort

from ..core.models import (
    EFFECT_SOURCE,
    MAX_ARGB_LEDS,
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    LightEffect,
    LightKind,
    LinkedEffect,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbMirrorDevice,
)

log = logging.getLogger(__name__)


def rgb_rows_or_none(value: Any) -> Any:
    """A saved colour list's rows; anything but a list is no rows."""
    log.debug("rgb_rows_or_none: %r", type(value).__name__)
    return rgb_rows(value) if isinstance(value, list) else []


def _linked_effect(ref: str, record: dict[str, Any]) -> LinkedEffect | None:
    """A saved linked colour; one this build cannot read is None."""
    metric, source = record.get("linked"), record.get("source", "cpu")
    log.debug("_linked_effect: %s %r %r", ref, metric, source)
    if metric not in get_args(LinkedMetric) or source not in get_args(SensorLink):
        log.warning("_linked_effect: %s's linked colour %r/%r is unreadable -- "
                    "shown as unknown", ref, metric, source)
        return None
    return LinkedEffect(metric=cast(LinkedMetric, metric),
                        source=cast(SensorLink, source))


@dataclass(slots=True)
class RgbPrefs:
    """What RGB Lighting keeps between runs."""

    # What follows the cooler (#160): an ``RgbFollowMode`` value.  "openrgb"
    # sends the LED cooler's colours to the devices of the OpenRGB SDK server
    # at host:port; "ram" to Corsair RGB memory directly.  Off until the user
    # picks one -- either takes over those devices' lighting.
    rgb_follow: str = RgbFollowMode.OFF.value
    openrgb_host: str = "127.0.0.1"
    openrgb_port: int = 6742
    # The device that leads: empty for the first LED cooler, as before; an
    # LCD's key to follow its picture, mapped by ``rgb_follow_mapping`` (a
    # ``FollowMapping`` value).
    rgb_follow_source: str = ""
    rgb_follow_mapping: str = FollowMapping.HALVES.value
    # How a region of that picture becomes a light's colour (a
    # ``FollowColors`` value): its brightest pixels, or its average.
    rgb_follow_colors: str = FollowColors.VIVID.value
    # Refs of the lights that follow (a stick's "i2c-3/0x19", an OpenRGB
    # device's name); empty for every one.
    rgb_follow_targets: list[str] = field(default_factory=list)
    # The built-in effect TRCC last gave each light, by ref -- a RAM stick's
    # (saved on the stick) or an ARGB header's (``aura/argb1``, run by TRCC).
    # What it set, not read back.  Plain JSON; ``light_effect`` validates it
    # on the way out.  Named before headers had effects; kept, so a settings
    # file from then still loads.
    ram_effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The lights the last Find found -- kind, ref, name and LED count each --
    # so a restart lists them without reading a bus.  ``found_lights``
    # validates.
    rgb_lights: list[dict[str, Any]] = field(default_factory=list)
    # How many LEDs each ASUS Aura ARGB header drives, by header number from
    # 1 (JSON keys are text).  The board cannot say: the header's data line
    # runs one way.  ``aura_argb_leds`` validates.
    aura_argb_leds: dict[str, int] = field(default_factory=dict)


def _prefs(raw: Mapping[str, Any]) -> RgbPrefs:
    """The saved section as :class:`RgbPrefs`: each name it knows, as saved
    -- the getters below check each value as they read it."""
    known = {f.name for f in fields(RgbPrefs)}
    log.debug("_prefs: %s", sorted(set(raw) & known))
    return RgbPrefs(**{name: value for name, value in raw.items() if name in known})


class RgbSettings:
    """RGB Lighting's settings, through its host's ``settings`` port --
    thread-safe, each change saved at once."""

    def __init__(self, port: SettingsPort) -> None:
        log.debug("RgbSettings.__init__")
        self._port = port
        self._lock = threading.RLock()
        self._prefs = _prefs(port.app())

    @property
    def prefs(self) -> RgbPrefs:
        """The saved values themselves (read them; change them through the
        setters, which save)."""
        log.debug("RgbSettings.prefs")
        return self._prefs

    def _save(self) -> None:
        log.debug("RgbSettings._save")
        self._port.save_app(asdict(self._prefs))

    def rgb_follow_mode(self) -> RgbFollowMode:
        """What follows the cooler; a value this build does not know is OFF."""
        value = self._prefs.rgb_follow
        log.debug("rgb_follow_mode: %r", value)
        try:
            return RgbFollowMode(value)
        except ValueError:
            log.warning("rgb_follow_mode: unknown %r in the settings -- off",
                        value)
            return RgbFollowMode.OFF

    def rgb_follow_mapping(self) -> FollowMapping:
        """How an LCD's picture maps onto the followers; unknown is halves."""
        value = self._prefs.rgb_follow_mapping
        log.debug("rgb_follow_mapping: %r", value)
        try:
            return FollowMapping(value)
        except ValueError:
            log.warning("rgb_follow_mapping: unknown %r in the settings -- "
                        "halves", value)
            return FollowMapping.HALVES

    def rgb_follow_colors(self) -> FollowColors:
        """How a region of an LCD's picture becomes a light's colour; unknown
        is vivid."""
        value = self._prefs.rgb_follow_colors
        log.debug("rgb_follow_colors: %r", value)
        try:
            return FollowColors(value)
        except ValueError:
            log.warning("rgb_follow_colors: unknown %r in the settings -- "
                        "vivid", value)
            return FollowColors.VIVID

    def light_effect(self, ref: str) -> LightEffect | None:
        """The effect TRCC last gave light *ref* -- a stick's or an ARGB
        header's built-in effect, or a header's linked colour -- or None; a
        damaged record is None too, never a guess."""
        record = (self._prefs.ram_effects.get(ref)
                  if isinstance(self._prefs.ram_effects, dict) else None)
        log.debug("light_effect: %s -> %r", ref, record)
        if not isinstance(record, dict):
            return None
        if "linked" in record:
            return _linked_effect(ref, record)
        colors = rgb_rows_or_none(record.get("colors", []))
        try:
            return RamEffectSettings(
                effect=RamEffect(record["effect"]),
                speed=EffectSpeed(record.get("speed", EffectSpeed.MEDIUM.value)),
                direction=(EffectDirection(record["direction"])
                           if record.get("direction") else None),
                colors=tuple(colors) if isinstance(colors, list) else (),
                random_colors=bool(record.get("random_colors", False)),
                brightness=int(record.get("brightness", 255)))
        except (KeyError, ValueError, TypeError) as e:
            log.warning("light_effect: %s's saved effect is unreadable (%s) -- "
                        "shown as unknown", ref, e)
            return None

    def set_light_effect(self, ref: str, effect: LightEffect) -> None:
        """Remember the effect TRCC gave light *ref*, a stick or a header."""
        log.info("set_light_effect: %s %s", ref, effect)
        record: dict[str, Any]
        if isinstance(effect, LinkedEffect):
            record = {"linked": effect.metric, "source": effect.source}
        else:
            record = {"effect": effect.effect.value, "speed": effect.speed.value,
                      "direction": (effect.direction.value if effect.direction
                                    else None),
                      "colors": [list(c) for c in effect.colors],
                      "random_colors": effect.random_colors,
                      "brightness": effect.brightness}
        with self._lock:
            if not isinstance(self._prefs.ram_effects, dict):
                self._prefs.ram_effects = {}
            self._prefs.ram_effects[ref] = record
            self._save()

    def found_lights(self, kind: LightKind) -> tuple[RgbMirrorDevice, ...]:
        """The lights of *kind* the last Find found -- no bus.  A damaged
        entry is dropped, never guessed."""
        records = (self._prefs.rgb_lights
                   if isinstance(self._prefs.rgb_lights, list) else [])
        lights: list[RgbMirrorDevice] = []
        for record in records:
            if not (isinstance(record, dict)
                    and isinstance(ref := record.get("ref"), str) and ref
                    and isinstance(name := record.get("name"), str)
                    and type(count := record.get("led_count")) is int
                    and count >= 0 and isinstance(record.get("kind"), str)):
                log.warning("found_lights: unreadable entry %r -- dropped",
                            record)
            elif record["kind"] == kind.value:
                lights.append(RgbMirrorDevice(len(lights), name, count, ref))
        log.debug("found_lights: %s -> %s", kind.value,
                  [light.ref for light in lights])
        return tuple(lights)

    def set_found_lights(self, kind: LightKind,
                         lights: Sequence[RgbMirrorDevice]) -> None:
        """Remember what a Find found of *kind* -- all of it, so a light that
        is gone is forgotten; other kinds' records are kept."""
        log.info("set_found_lights: %s %s", kind.value,
                 [light.ref for light in lights])
        with self._lock:
            kept = [r for r in self._prefs.rgb_lights
                    if isinstance(r, dict) and r.get("kind") != kind.value
                    ] if isinstance(self._prefs.rgb_lights, list) else []
            self._prefs.rgb_lights = kept + [
                {"kind": kind.value, "ref": light.ref, "name": light.name,
                 "led_count": light.led_count} for light in lights]
            self._save()

    def aura_argb_leds(self) -> dict[int, int]:
        """LEDs per Aura ARGB header, by header number; a damaged entry is
        dropped, never guessed."""
        raw = self._prefs.aura_argb_leds
        counts = {int(header): leds for header, leds in
                  (raw.items() if isinstance(raw, dict) else ())
                  if str(header).isdigit() and int(header) > 0
                  and type(leds) is int and 0 <= leds <= MAX_ARGB_LEDS}
        if isinstance(raw, dict) and len(counts) != len(raw):
            log.warning("aura_argb_leds: unreadable entries in %r dropped", raw)
        log.debug("aura_argb_leds: %s", counts)
        return counts

    def set_aura_leds(self, header: int, leds: int) -> None:
        """Remember how many LEDs Aura ARGB *header* drives."""
        log.info("set_aura_leds: header %d -> %d LED(s)", header, leds)
        with self._lock:
            if not isinstance(self._prefs.aura_argb_leds, dict):
                self._prefs.aura_argb_leds = {}
            self._prefs.aura_argb_leds[str(header)] = leds
            self._save()

    def rgb_follow_targets(self) -> tuple[str, ...]:
        """The lights that follow (refs); anything not a list of text is all.

        Settings load JSON as it is, so a hand-edited string here would
        otherwise become one target per character."""
        value = self._prefs.rgb_follow_targets
        log.debug("rgb_follow_targets: %r", value)
        if not isinstance(value, list) or not all(isinstance(t, str)
                                                  for t in value):
            log.warning("rgb_follow_targets: %r is not a list of names -- "
                        "every light follows", value)
            return ()
        return tuple(value)

    def follow_effects(self) -> dict[str, LightEffect]:
        """Each followed light's built-in effect, by ref, while the source
        is TRCC's effect clock -- empty for any other source.  What the
        follower is configured with, wherever following starts."""
        if self._prefs.rgb_follow_source != EFFECT_SOURCE:
            log.debug("follow_effects: source %r -- none",
                      self._prefs.rgb_follow_source)
            return {}
        effects = {ref: effect for ref in self.rgb_follow_targets()
                   if (effect := self.light_effect(ref)) is not None}
        log.debug("follow_effects: %s", effects)
        return effects

    def set_rgb_follow(self, mode: RgbFollowMode, host: str, port: int,
                       source: str, mapping: FollowMapping,
                       targets: tuple[str, ...], *,
                       colors: FollowColors) -> None:
        """Pick what follows, OpenRGB's *host*:*port*, and the device that
        leads (#160): empty *source* for the first LED cooler, else a key.
        *colors* is required by name, so no caller drops the saved one."""
        log.info("set_rgb_follow: %s %s:%d source=%r %s %s targets=%s",
                 mode.value, host, port, source, mapping.value, colors.value,
                 list(targets) or "all")
        if not 0 < port < 65536:
            raise ValueError(f"port out of range (1-65535): {port}")
        if source and source != EFFECT_SOURCE:
            parse_device_key(source)       # ValueError for a malformed key
        with self._lock:
            self._prefs.rgb_follow = mode.value
            self._prefs.openrgb_host = host
            self._prefs.openrgb_port = port
            self._prefs.rgb_follow_source = source
            self._prefs.rgb_follow_mapping = mapping.value
            self._prefs.rgb_follow_colors = colors.value
            self._prefs.rgb_follow_targets = list(targets)
            self._save()
