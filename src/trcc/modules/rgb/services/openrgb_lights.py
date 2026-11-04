"""OpenRGB drives the coolers -- automatically, the way WLED takes realtime
colours (#160, #333).

No LED mode and no switch: with RGB Lighting on, each cooler has its own
localhost DDP port, and colours that arrive there show over the cooler's own
mode; :data:`LIVE_FOR_S` after the last one, the cooler's mode is back.  The
user adds a DDP device in OpenRGB with that port and the cooler's LED count
-- OpenRGB has no Thermalright driver (measured on its master, 2026-11-04),
so it cannot find a cooler by itself.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable

from trcc.core.logs import per_frame
from trcc.core.models import CoolerLights
from trcc.core.ports import LedSource
from trcc.modules.rgb.core.ports import DdpListener

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: How long after OpenRGB's last packet a cooler still shows its colours.
#: WLED returns to its own effect a second or two after realtime data stops
#: (its users' reports; no documented default found) -- this is that.
LIVE_FOR_S = 1.5


class OpenRgbLights(LedSource):
    """One :class:`DdpListener` per connected cooler; its colours are live
    while packets keep coming."""

    def __init__(self, open_port: Callable[[int], DdpListener],
                 port_for: Callable[[str], int],
                 clock: Callable[[], float] = time.monotonic) -> None:
        log.debug("OpenRgbLights.__init__")
        self._open_port, self._port_for, self._clock = open_port, port_for, clock
        self._ports: dict[str, DdpListener] = {}
        self._coolers: dict[str, CoolerLights] = {}
        self._lock = threading.Lock()

    def watch(self, coolers: Iterable[CoolerLights]) -> None:
        """Listen for exactly *coolers*: a port for each new one, the port of
        one gone closed."""
        wanted = {c.key: c for c in coolers}
        with self._lock:
            for key in [k for k in self._ports if k not in wanted]:
                log.info("OpenRgbLights.watch: %s gone -- closing its port", key)
                self._ports.pop(key).close()
            for key in [k for k in wanted if k not in self._ports]:
                self._ports[key] = self._open_port(self._port_for(key))
                log.info("OpenRgbLights.watch: %s on port %d", key,
                         self._ports[key].port)
            self._coolers = wanted
        log.debug("OpenRgbLights.watch: %d cooler(s)", len(wanted))

    def live(self, key: str, count: int) -> list[tuple[int, int, int]] | None:
        """Cooler *key*'s colours while OpenRGB is sending; None otherwise."""
        listener = self._ports.get(key)
        last = listener.last_packet if listener is not None else None
        if listener is None or last is None or self._clock() - last > LIVE_FOR_S:
            frame_log.debug("OpenRgbLights.live: %s quiet", key)
            return None
        frame_log.debug("OpenRgbLights.live: %s live", key)
        return listener.colors(count)

    def addresses(self) -> tuple[tuple[CoolerLights, int, str], ...]:
        """Each cooler, its port and why it could not open ("" when open) --
        what OpenRGB's DDP device must name."""
        with self._lock:
            found = tuple((cooler, self._ports[key].port, self._ports[key].error)
                          for key, cooler in self._coolers.items()
                          if key in self._ports)
        log.debug("OpenRgbLights.addresses: %d", len(found))
        return found

    def close(self) -> None:
        with self._lock:
            ports, self._ports, self._coolers = self._ports, {}, {}
        for listener in ports.values():
            listener.close()
        log.info("OpenRgbLights.close: %d port(s) closed", len(ports))
