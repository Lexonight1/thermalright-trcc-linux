"""MSAcpi WMI thermal-zone sensor — Windows last-resort temperature.

``MSAcpi_ThermalZoneTemperature`` lives in ``root\\wmi`` and is the only
CPU/system temperature path shipped with Windows itself — no driver, no
admin, no install.  Hardware coverage is uneven (many modern consumer
boards return motherboard-only, some return nothing), but it's the
graceful-degradation floor under HWiNFO + LHM.

ACPI reports temperature in deci-Kelvin; this module converts to °C.

Wire-format ported from legacy ``src/trcc/adapters/system/windows/
sources/msacpi.py``; reshaped into a single ``WmiAcpiCpu`` exposing the
``CpuSource`` ABC so it can sit in a ``CpuSourceChain`` next to native
sensors.  Tests inject a ``handle_factory`` for full DI coverage on
non-Windows boxes.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ...core.ports import CpuSource

log = logging.getLogger(__name__)


_MSACPI_NAMESPACE = "root\\wmi"

# COM objects are apartment-bound — a handle created on one thread can't be
# read from another (issue #131).  Cache the handle in thread-local so each
# thread (the sensor-poll thread in particular) gets one born in its own
# apartment.  The poll thread enters its COM apartment via
# Platform.worker_thread_context() before the first read.
_handle_local = threading.local()


def _default_handle_factory() -> Any:
    """Per-thread ``root\\wmi`` WMI handle.  Returns ``None`` when unavailable."""
    handle = getattr(_handle_local, "msacpi_ns", None)
    if handle is not None:
        return handle
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        handle = wmi.WMI(namespace=_MSACPI_NAMESPACE)
    except ImportError:
        handle = None
    except Exception as e:
        log.debug("MSAcpi WMI handle failed: %s", e)
        handle = None
    _handle_local.msacpi_ns = handle
    return handle


class WmiAcpiCpu(CpuSource):
    """CPU temperature via ACPI thermal zones.

    Probes the namespace once at construction.  When ACPI returns zero
    thermal zones (common on workstations), ``temp()`` returns None
    forever — the chain falls through to the next source.
    """

    name_default = "MSAcpi thermal zone"

    def __init__(
        self,
        *,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        # Defer the handle to first read so it is born on the READING
        # thread's apartment (the poll thread), not the construction
        # thread's.  The zone count is apartment-agnostic, cached once.
        self._handle_factory = handle_factory
        self._probed = False
        self._zone_count = 0

    @property
    def name(self) -> str:
        return self.name_default

    def _ensure_probed(self, handle: Any) -> None:
        """Cache the zone count once — avoids re-querying on every poll."""
        if self._probed:
            return
        self._probed = True
        if handle is None:
            return
        try:
            self._zone_count = len(list(handle.MSAcpi_ThermalZoneTemperature()))
        except Exception:
            log.debug("MSAcpi probe failed", exc_info=True)
            self._zone_count = 0
        if self._zone_count > 0:
            log.info("MSAcpi: %d thermal zone(s) available", self._zone_count)

    def temp(self) -> float | None:
        """Return the hottest thermal zone in °C, or None.

        Multiple zones (CPU + chipset + ambient) are common; the hottest
        is the most useful single number for an overlay.
        """
        handle = self._handle_factory()
        self._ensure_probed(handle)
        if handle is None or self._zone_count == 0:
            return None
        try:
            zones = list(handle.MSAcpi_ThermalZoneTemperature())
        except Exception:
            log.debug("MSAcpi temp read failed", exc_info=True)
            return None
        hottest: float | None = None
        for zone in zones:
            try:
                deci_kelvin = float(zone.CurrentTemperature)
            except (TypeError, ValueError):
                continue
            celsius = (deci_kelvin / 10.0) - 273.15
            if hottest is None or celsius > hottest:
                hottest = celsius
        return hottest

    # ACPI thermal zones only expose temperature; everything else is None
    # and falls through to the next chain entry.

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None
