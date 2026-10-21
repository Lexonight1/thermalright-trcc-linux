"""The cooler leads; another RGB system's devices follow (#160).

Every LED render publishes ``LedColorsChanged`` with the cooler's colours.
While following is on, this service sends the newest of them to every device
of the chosen follower (OpenRGB, or Corsair RAM directly -- each an
``RgbMirror``) from its own thread: a slow or absent follower never holds a
render.  Only the newest
colours are kept -- a frame that could not be sent in time is replaced, not
queued.

One cooler leads: the first whose colours arrive after following starts --
or the device the user picked as the source.  An LCD can be the source too:
its frames are sampled into a column of colours per device, so with the
"halves" mapping the left edge of the panel lights the first stick and the
right edge the second, top to bottom; with "single", one colour each.  What is
sampled is the frame's background -- not the overlay's text -- and the colours
are un-gamma'd for the LEDs (``core.follow_colors``).  Every send is reported
with the screen colours it carried, so a window's preview shows the truth.

The source can also be TRCC's own effect clock (``EFFECT_SOURCE``): the
motherboard's lights each run the built-in effect they were given, drawn here
(``effect_frames``) a frame at a time -- the board has no such effects of its
own that it runs per header, so TRCC draws them and sends them in direct mode.

The same service owns the RAM for its built-in effects.  Commands arrive on
one thread per connected UI and the follower sends from its own, so every use
of the bus -- a follow send, an effect, a scan -- holds one lock: an effect is
some 25 transfers, and a follow write landing between two of them would go to
whichever stick the bus was last pointed at.  Switching following on or off
holds another, so two UIs switching at once cannot leave a worker running
that nothing can stop.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..core.logs import per_frame
from ..core.models import (
    EFFECT_SOURCE,
    MAX_ARGB_LEDS,
    FollowColors,
    FollowMapping,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbMirrorDevice,
)
from ..core.ports import RamLights, RgbMirror
from .effect_frames import effect_frame
from .follow_colors import for_leds, frame_columns

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]
Columns = tuple[tuple[Rgb, ...], ...]
#: A surface's ARGB32 pixels: ``(bytes, width, height, stride)``.  The
#: Renderer port's ``raw_argb32``.
Pixels = Callable[[Any], tuple[bytes, int, int, int]]
#: Told of every send: the device that led, and the screen colours sent.
Sent = Callable[[str, Columns], None]

#: Rows a frame is sampled into: one per LED of a 10-LED stick; each device
#: stretches its column to its own LED count.
FRAME_ROWS = 10
#: An LCD frame is sampled at most this often -- a 15-30 fps video gives
#: every one it can, a still theme the rare frame it sends.
FRAME_INTERVAL_S = 1 / 20
#: TRCC's own effect is drawn this often -- as often as an LCD is sampled --
#: and a Static one once a second, which puts it back after a board reset.
EFFECT_INTERVAL_S = FRAME_INTERVAL_S
STATIC_INTERVAL_S = 1.0
#: A blink: dim white -- full white on a long strip can draw more than an
#: ARGB header's 3 A -- then off, every 250 ms, for 2 s.
BLINK_COLOR = (64, 64, 64)
BLINK_STEP_S = 0.25
BLINK_STEPS = 8


@dataclass(frozen=True, slots=True)
class MirrorStatus:
    """What following is doing, for a Query to report."""
    mode: RgbFollowMode = RgbFollowMode.OFF
    source: str = ""
    connected: bool = False
    devices: tuple[str, ...] = ()
    lead: str = ""
    error: str = ""


class RgbMirrorService:
    """Sends the leading cooler's colours to the other system's devices."""

    #: Seconds before trying again after the other system was unreachable.
    RETRY_S = 10.0

    def __init__(self,
                 make_mirror: Callable[[RgbFollowMode, str, int], RgbMirror],
                 *,
                 retry_s: float = RETRY_S,
                 blink_step_s: float = BLINK_STEP_S,
                 on_sent: Sent | None = None) -> None:
        log.debug("RgbMirrorService.__init__: retry %.1f s, blink step %.2f s",
                  retry_s, blink_step_s)
        self._make = make_mirror
        self._retry_s = retry_s
        self._blink_step_s = blink_step_s
        self._on_sent = on_sent
        self._cond = threading.Condition()
        # One column of colours per device, newest only; a single column
        # (an LED cooler's colours) goes to every device.  With it, whether
        # they are a picture's -- screen colours, un-gamma'd before sending.
        self._pending: tuple[Columns, bool] | None = None
        self._source = ""
        self._mapping = FollowMapping.HALVES
        self._how = FollowColors.VIVID
        self._targets: tuple[str, ...] = ()
        # Each light's built-in effect, by ref, while the source is TRCC's
        # effect clock -- empty otherwise; and when that clock started.
        self._effects: Mapping[str, RamEffectSettings] = {}
        self._effect_start = 0.0
        self._last_frame = 0.0
        self._device_count = 2
        self._mirror: RgbMirror | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lead = ""
        self._retry_at = 0.0
        self._status = MirrorStatus()
        self._bus = threading.Lock()        # one use of the bus at a time
        self._switch = threading.Lock()     # one configure / stop at a time
        self._ram: RamLights | None = None  # shared by following and effects
        # OpenRGB's devices from the last scan, None before any; and why the
        # last scan could not list them.
        self._openrgb: tuple[RgbMirrorDevice, ...] | None = None
        self._openrgb_error = ""

    @property
    def status(self) -> MirrorStatus:
        frame_log.debug("RgbMirrorService.status: %s", self._status)
        return self._status

    def configure(self, mode: RgbFollowMode, host: str, port: int,
                  source: str = "",
                  mapping: FollowMapping = FollowMapping.HALVES,
                  targets: tuple[str, ...] = (), *,
                  colors: FollowColors = FollowColors.VIVID,
                  effects: Mapping[str, RamEffectSettings] | None = None) -> None:
        """Stop following; start again with *mode* unless it is OFF.

        *host* and *port* are OpenRGB's SDK server; RAM ignores them.
        *source* is the device that leads -- empty for the first LED cooler
        whose colours arrive; an LCD's key to follow its picture.  *targets*
        are the refs of the devices that follow -- empty for every one.
        *colors* is how a region of a picture becomes one colour.
        *effects* are each light's built-in effect, by ref, used when
        *source* is ``EFFECT_SOURCE``; a light without one is left dark.
        """
        effects = dict(effects or {}) if source == EFFECT_SOURCE else {}
        log.info("RgbMirrorService.configure: %s %s:%d source=%r %s %s "
                 "targets=%s effects=%s", mode.value, host, port, source,
                 mapping.value, colors.value, targets or "all",
                 {ref: e.effect.value for ref, e in effects.items()})
        with self._switch:
            self._stop_following()
            if mode is RgbFollowMode.OFF:
                return
            if mode is RgbFollowMode.RAM:
                with self._bus:
                    mirror: RgbMirror = self._ram_lights()
            else:
                try:
                    mirror = self._make(mode, host, port)
                except OSError as e:     # e.g. no motherboard RGB to follow
                    log.warning("RgbMirrorService.configure: nothing to "
                                "follow in %s -- %s", mode.value, e)
                    self._status = MirrorStatus(mode=mode, source=source,
                                                error=str(e))
                    return
            with self._cond:
                self._mirror = mirror
                self._running, self._lead, self._retry_at = True, source, 0.0
                self._source, self._mapping = source, mapping
                self._targets, self._how = targets, colors
                self._effects, self._effect_start = effects, time.monotonic()
                self._last_frame = 0.0
                self._status = MirrorStatus(mode=mode, source=source)
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="trcc-rgb-mirror")
            self._thread.start()

    def on_colors(self, event: object) -> None:
        """``LedColorsChanged`` -> the newest colours to send."""
        key = getattr(event, "key", "")
        colors = getattr(event, "colors", ())
        frame_log.debug("RgbMirrorService.on_colors: %s %d colour(s)", key,
                        len(colors))
        with self._cond:
            if not self._running or not colors:
                return
            if not self._lead:
                self._lead = key
                log.info("RgbMirrorService: %s leads", key)
            if key != self._lead:
                return
            self._pending = ((tuple(colors),), False)
            self._cond.notify()

    def on_frame(self, key: str, surface: Any, pixels: Pixels) -> None:
        """A frame of LCD *key* -> a column of colours per device, when *key*
        leads.  *surface* is the picture to follow -- its background, without
        the overlay's text.

        Sampled on the publishing thread, outside the lock.
        """
        with self._cond:
            if (not self._running or not self._source or key != self._source
                    or surface is None):
                return
            now = time.monotonic()
            if now - self._last_frame < FRAME_INTERVAL_S:
                return
            self._last_frame = now
            columns = (1 if self._mapping is FollowMapping.SINGLE
                       else max(1, self._device_count))
            how = self._how
        sampled = frame_columns(*pixels(surface), columns, FRAME_ROWS, how)
        frame_log.debug("RgbMirrorService.on_frame: %s %dx%d %s", key, columns,
                        FRAME_ROWS, how.value)
        with self._cond:
            if self._running:
                self._pending = (sampled, True)
                self._cond.notify()

    def stop(self) -> None:
        """Stop following.  The RAM stays open for its effects."""
        log.debug("RgbMirrorService.stop: running=%s", self._running)
        with self._switch:
            self._stop_following()

    def close(self) -> None:
        """Stop following and release the RAM -- the App is closing."""
        log.info("RgbMirrorService.close: ram=%s", self._ram is not None)
        self.stop()
        with self._bus:
            if self._ram is not None:
                self._ram.close()
                self._ram = None

    # ── The RAM: what it holds, and its effects ───────────────────────

    def ram_sticks(self) -> tuple[RgbMirrorDevice, ...] | None:
        """The sticks the last scan found, None before any.  No bus access:
        a page can show this as often as it likes."""
        ram = self._ram
        found = None if ram is None else ram.found()
        log.debug("RgbMirrorService.ram_sticks: %s",
                  None if found is None else len(found))
        return found

    def scan_ram(self) -> tuple[RgbMirrorDevice, ...]:
        """Look for the sticks again -- only ever because a user asked."""
        log.info("RgbMirrorService.scan_ram")
        with self._bus:
            ram = self._ram_lights()
            ram.close()
            return ram.devices()

    def apply_effect(self, refs: tuple[str, ...],
                     settings: RamEffectSettings) -> tuple[RgbMirrorDevice, ...]:
        """Save *settings* on the sticks named by *refs* -- every stick when
        empty.  Returns the sticks it was saved on.

        ``ValueError`` for a stick no scan found, or settings the effect cannot
        take -- either before anything is written; ``OSError`` from the bus.
        """
        log.info("RgbMirrorService.apply_effect: %s on %s",
                 settings.effect.value, refs or "every stick")
        with self._bus:
            ram = self._ram_lights()
            sticks = ram.devices()
            if unknown := set(refs) - {s.ref for s in sticks}:
                raise ValueError(f"no stick {', '.join(sorted(unknown))} -- "
                                 f"found {[s.ref for s in sticks] or 'none'}")
            targets = tuple(s for s in sticks if not refs or s.ref in refs)
            for stick in targets:
                ram.apply_effect(stick, settings)
        return targets

    def openrgb_lights(self) -> tuple[tuple[RgbMirrorDevice, ...] | None, str]:
        """OpenRGB's devices from the last scan (None before any), and why
        that scan failed -- no network traffic."""
        log.debug("RgbMirrorService.openrgb_lights: %s",
                  None if self._openrgb is None else len(self._openrgb))
        return self._openrgb, self._openrgb_error

    def scan_openrgb(self, host: str, port: int) -> None:
        """Ask OpenRGB at *host*:*port* for its devices -- a user's "Find".

        Listing takes no device over (``OpenRgbMirror`` switches one to direct
        mode only when it is sent colours), so a scan changes no lighting.
        """
        log.info("RgbMirrorService.scan_openrgb: %s:%d", host, port)
        with self._bus:
            following = (self._mirror if self._status.mode is
                         RgbFollowMode.OPENRGB else None)
            mirror: RgbMirror | None = following
            try:
                # Made inside: a platform with no OpenRGB client says so.
                mirror = mirror or self._make(RgbFollowMode.OPENRGB, host, port)
                self._openrgb, self._openrgb_error = mirror.devices(), ""
            except OSError as e:
                self._openrgb = None
                self._openrgb_error = f"{type(e).__name__}: {e}"
                log.info("scan_openrgb: none -- %s", self._openrgb_error)
            finally:
                if following is None and mirror is not None:
                    mirror.close()

    # ── The motherboard: which header is which ───────────────────────

    def blink(self, ref: str) -> RgbMirrorDevice:
        """Blink the motherboard light *ref* so the user can see which one
        it is.  Returns the light blinked.

        A header with no LED count blinks the most a header drives: blinking
        is how the user finds the fan, before its count is known, and colours
        past the last LED fall off the end of the strip.  While the board is
        followed the follower's own driver blinks it and the next frame
        paints over it; otherwise a driver opened for the blink hands the
        board back to its own effect and closes.

        ``ValueError`` for a light the board does not have; ``OSError`` when
        the board cannot be reached.
        """
        log.info("RgbMirrorService.blink: %s", ref)
        with self._switch:              # no configure or stop mid-blink
            following = (self._mirror if self._status.mode is
                         RgbFollowMode.BOARD else None)
            mirror = following or self._make(RgbFollowMode.BOARD, "", 0)
            try:
                with self._bus:
                    device = next((d for d in mirror.devices()
                                   if d.ref == ref), None)
                    if device is None:
                        raise ValueError(f"the motherboard has no light {ref!r}")
                    lit = replace(device, led_count=device.led_count
                                  or MAX_ARGB_LEDS)
                    log.info("RgbMirrorService.blink: %s, %d LED(s), %s",
                             ref, lit.led_count,
                             "following" if following else "own driver")
                    for step in range(BLINK_STEPS):
                        mirror.show(lit, (BLINK_COLOR if step % 2 == 0
                                          else (0, 0, 0),))
                        time.sleep(self._blink_step_s)
                    if following is None:
                        self._release(mirror)
            finally:
                if following is None:
                    mirror.close()
        return device

    def _ram_lights(self) -> RamLights:
        """The one RAM driver, made on first use.  Caller holds the bus."""
        if self._ram is None:
            made = self._make(RgbFollowMode.RAM, "", 0)
            if not isinstance(made, RamLights):
                raise TypeError(f"the RAM follower {type(made).__name__} "
                                "cannot keep effects")
            self._ram = made
            log.info("RgbMirrorService: RAM driver %s", type(made).__name__)
        return self._ram

    def _stop_following(self) -> None:
        """Stop the worker, hand the lights back to their own system, and
        drop the follower.  Caller holds the switch."""
        log.debug("_stop_following: thread=%s", self._thread is not None)
        with self._cond:
            self._running, self._effects = False, {}
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._mirror is not None:
            with self._bus:
                self._release(self._mirror)
            if self._mirror is not self._ram:
                self._mirror.close()
        self._mirror = None
        self._status = MirrorStatus()

    @staticmethod
    def _release(mirror: RgbMirror) -> None:
        """*mirror*'s lights back to their own system; an unreachable one
        cannot be handed anything, and stopping goes on."""
        log.info("RgbMirrorService._release: %s", type(mirror).__name__)
        try:
            mirror.release()
        except OSError as e:
            log.warning("RgbMirrorService._release: %s could not be handed "
                        "back -- %s", type(mirror).__name__, e)

    # ── The worker ────────────────────────────────────────────────────

    def _run(self) -> None:
        log.debug("RgbMirrorService._run: started")
        while True:
            with self._cond:
                if self._effects:               # TRCC's clock: a frame a beat
                    self._cond.wait(self._effect_interval())
                while self._running and self._pending is None and not self._effects:
                    self._cond.wait()
                if not self._running:
                    return
                pending, self._pending = self._pending, None
                mirror = self._mirror
            columns, picture = pending or (None, False)
            if mirror is not None and time.monotonic() >= self._retry_at:
                with self._bus:
                    self._send(mirror, columns, picture)

    def _effect_interval(self) -> float:
        """How long until the next effect frame: once a second when every
        light is Static, else an LCD sample's interval."""
        moving = any(e.effect is not RamEffect.STATIC
                     for e in self._effects.values())
        frame_log.debug("_effect_interval: moving=%s", moving)
        return EFFECT_INTERVAL_S if moving else STATIC_INTERVAL_S

    def _effect_columns(self, devices: tuple[RgbMirrorDevice, ...]) -> Columns:
        """Each of *devices*' effect now -- device *i* is the *i*-th of the
        lights, so a left / right effect travels across them; a light with no
        effect is dark."""
        t = time.monotonic() - self._effect_start
        frame_log.debug("_effect_columns: %d light(s) t=%.2f", len(devices), t)
        return tuple(tuple(effect_frame(self._effects[d.ref], t, i, len(devices)))
                     if d.ref in self._effects else ((0, 0, 0),)
                     for i, d in enumerate(devices))

    def _send(self, mirror: RgbMirror, columns: Columns | None,
              picture: bool) -> None:
        """Device *i* gets column *i* -- one column goes to every device.

        A *picture*'s columns are screen colours, un-gamma'd for the LEDs;
        a cooler's are LED colours already.  No *columns*: TRCC's effect
        clock -- each device's effect, drawn now.
        """
        frame_log.debug("RgbMirrorService._send: %s column(s) picture=%s",
                        "effect" if columns is None else len(columns), picture)
        try:
            devices = tuple(d for d in mirror.devices()
                            if not self._targets or d.ref in self._targets)
            self._device_count = len(devices) or self._device_count
            if columns is None:
                columns = self._effect_columns(devices)
            columns = columns or ((),)
            leds = tuple(map(for_leds, columns)) if picture else columns
            for i, device in enumerate(devices):
                mirror.show(device, leds[i % len(leds)])
        except OSError as e:
            self._failed(mirror, e)
            return
        self._connected(devices)
        if self._on_sent is not None:
            self._on_sent(self._source or self._lead, columns)

    def _connected(self, devices: tuple[RgbMirrorDevice, ...]) -> None:
        names = tuple(d.name for d in devices)
        if not self._status.connected or self._status.devices != names:
            log.info("RgbMirrorService: following on %d device(s): %s",
                     len(names), ", ".join(names))
        else:
            frame_log.debug("RgbMirrorService: sent to %d device(s)",
                            len(names))
        self._status = MirrorStatus(mode=self._status.mode,
                                    source=self._source, connected=True,
                                    devices=names, lead=self._lead)

    def _failed(self, mirror: RgbMirror, error: OSError) -> None:
        message = f"{type(error).__name__}: {error}"
        if self._status.error != message:
            log.warning("RgbMirrorService: %s unreachable (%s) — trying "
                        "again in %.0f s", self._status.mode.value, message,
                        self._retry_s)
        else:
            frame_log.debug("RgbMirrorService: still unreachable")
        mirror.close()
        self._retry_at = time.monotonic() + self._retry_s
        self._status = MirrorStatus(mode=self._status.mode,
                                    source=self._source, lead=self._lead,
                                    error=message)
