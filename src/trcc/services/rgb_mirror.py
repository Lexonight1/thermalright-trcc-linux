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
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core.logs import per_frame
from ..core.models import RgbFollowMode, RgbMirrorDevice
from ..core.ports import RgbMirror

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
        self.stop()
        if mode is RgbFollowMode.OFF:
            return
        with self._cond:
            self._mirror = self._make(mode, host, port)
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
        """Stop the thread and drop the connection."""
        log.debug("RgbMirrorService.stop: running=%s", self._running)
        with self._cond:
            self._running = False
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._mirror is not None:
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
