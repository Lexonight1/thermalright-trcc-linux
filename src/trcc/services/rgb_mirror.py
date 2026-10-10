"""The cooler leads; another RGB system's devices follow (#160).

Every LED render publishes ``LedColorsChanged`` with the cooler's colours.
While following is on, this service sends the newest of them to every device
of the chosen follower (OpenRGB, or Corsair RAM directly -- each an
``RgbMirror``) from its own thread: a slow or absent follower never holds a
render.  Only the newest
colours are kept -- a frame that could not be sent in time is replaced, not
queued.

One cooler leads: the first whose colours arrive after following starts.
Two LED coolers following at once would alternate on every device.

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

from ..core.logs import per_frame
from ..core.models import RamEffectSettings, RgbFollowMode, RgbMirrorDevice
from ..core.ports import RamLights, RgbMirror

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class MirrorStatus:
    """What following is doing, for a Query to report."""
    mode: RgbFollowMode = RgbFollowMode.OFF
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
                 retry_s: float = RETRY_S) -> None:
        log.debug("RgbMirrorService.__init__: retry %.1f s", retry_s)
        self._make = make_mirror
        self._retry_s = retry_s
        self._cond = threading.Condition()
        self._pending: tuple[Rgb, ...] | None = None
        self._mirror: RgbMirror | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lead = ""
        self._retry_at = 0.0
        self._status = MirrorStatus()
        self._bus = threading.Lock()        # one use of the bus at a time
        self._switch = threading.Lock()     # one configure / stop at a time
        self._ram: RamLights | None = None  # shared by following and effects

    @property
    def status(self) -> MirrorStatus:
        frame_log.debug("RgbMirrorService.status: %s", self._status)
        return self._status

    def configure(self, mode: RgbFollowMode, host: str, port: int) -> None:
        """Stop following; start again with *mode* unless it is OFF.

        *host* and *port* are OpenRGB's SDK server; RAM ignores them.
        """
        log.info("RgbMirrorService.configure: %s %s:%d", mode.value, host,
                 port)
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
                self._running, self._lead, self._retry_at = True, "", 0.0
                self._status = MirrorStatus(mode=mode)
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
            self._pending = tuple(colors)
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
                if not self._running:
                    return
                colors, self._pending = self._pending, None
                mirror = self._mirror
            if mirror is not None and time.monotonic() >= self._retry_at:
                with self._bus:
                    self._send(mirror, colors or ())

    def _send(self, mirror: RgbMirror, colors: tuple[Rgb, ...]) -> None:
        frame_log.debug("RgbMirrorService._send: %d colour(s)", len(colors))
        try:
            devices = mirror.devices()
            for device in devices:
                mirror.show(device, colors)
        except OSError as e:
            self._failed(mirror, e)
            return
        self._connected(devices)

    def _connected(self, devices: tuple[RgbMirrorDevice, ...]) -> None:
        names = tuple(d.name for d in devices)
        if not self._status.connected or self._status.devices != names:
            log.info("RgbMirrorService: following on %d device(s): %s",
                     len(names), ", ".join(names))
        else:
            frame_log.debug("RgbMirrorService: sent to %d device(s)",
                            len(names))
        self._status = MirrorStatus(mode=self._status.mode, connected=True,
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
        self._status = MirrorStatus(mode=self._status.mode, lead=self._lead,
                                    error=message)
