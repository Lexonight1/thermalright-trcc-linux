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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..core.logs import per_frame
from ..core.models import (
    FollowColors,
    FollowMapping,
    RamEffectSettings,
    RgbFollowMode,
    RgbMirrorDevice,
)
from ..core.ports import RamLights, RgbMirror
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
                 on_sent: Sent | None = None) -> None:
        log.debug("RgbMirrorService.__init__: retry %.1f s", retry_s)
        self._make = make_mirror
        self._retry_s = retry_s
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
                  colors: FollowColors = FollowColors.VIVID) -> None:
        """Stop following; start again with *mode* unless it is OFF.

        *host* and *port* are OpenRGB's SDK server; RAM ignores them.
        *source* is the device that leads -- empty for the first LED cooler
        whose colours arrive; an LCD's key to follow its picture.  *targets*
        are the refs of the devices that follow -- empty for every one.
        *colors* is how a region of a picture becomes one colour.
        """
        log.info("RgbMirrorService.configure: %s %s:%d source=%r %s %s "
                 "targets=%s", mode.value, host, port, source, mapping.value,
                 colors.value, targets or "all")
        with self._switch:
            self._stop_following()
            if mode is RgbFollowMode.OFF:
                return
            if mode is RgbFollowMode.RAM:
                with self._bus:
                    mirror: RgbMirror = self._ram_lights()
            else:
                mirror = self._make(mode, host, port)
            with self._cond:
                self._mirror = mirror
                self._running, self._lead, self._retry_at = True, source, 0.0
                self._source, self._mapping = source, mapping
                self._targets, self._how = targets, colors
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
            mirror = following or self._make(RgbFollowMode.OPENRGB, host, port)
            try:
                self._openrgb, self._openrgb_error = mirror.devices(), ""
            except OSError as e:
                self._openrgb = None
                self._openrgb_error = f"{type(e).__name__}: {e}"
                log.info("scan_openrgb: none -- %s", self._openrgb_error)
            finally:
                if following is None:
                    mirror.close()

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
        """Stop the worker and drop the follower.  Caller holds the switch."""
        log.debug("_stop_following: thread=%s", self._thread is not None)
        with self._cond:
            self._running = False
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._mirror is not None and self._mirror is not self._ram:
            self._mirror.close()
        self._mirror = None
        self._status = MirrorStatus()

    # ── The worker ────────────────────────────────────────────────────

    def _run(self) -> None:
        log.debug("RgbMirrorService._run: started")
        while True:
            with self._cond:
                while self._running and self._pending is None:
                    self._cond.wait()
                if not self._running or self._pending is None:
                    return
                (columns, picture), self._pending = self._pending, None
                mirror = self._mirror
            if mirror is not None and time.monotonic() >= self._retry_at:
                with self._bus:
                    self._send(mirror, columns or ((),), picture)

    def _send(self, mirror: RgbMirror, columns: Columns,
              picture: bool) -> None:
        """Device *i* gets column *i* -- one column goes to every device.

        A *picture*'s columns are screen colours, un-gamma'd for the LEDs;
        a cooler's are LED colours already.
        """
        frame_log.debug("RgbMirrorService._send: %d column(s) picture=%s",
                        len(columns), picture)
        leds = tuple(map(for_leds, columns)) if picture else columns
        try:
            devices = tuple(d for d in mirror.devices()
                            if not self._targets or d.ref in self._targets)
            self._device_count = len(devices) or self._device_count
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
