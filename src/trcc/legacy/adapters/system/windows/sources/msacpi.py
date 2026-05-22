"""ACPI thermal-zone sensor source — last-resort, native, always-on.

Reads ``MSAcpi_ThermalZoneTemperature`` from the ``root\\wmi`` namespace.
The only CPU/system temperature path that ships with Windows itself —
no driver, no install, no admin.  Coverage is hardware-dependent: many
modern consumer systems return motherboard temp only or nothing at all;
on systems where it works, it's our floor when LHM/HWiNFO can't load.

Why include it even though it's flaky:  *graceful degradation*.  When
Microsoft Defender quarantines a kernel driver (LHM's WinRing0, HWiNFO's
own driver), this tier still lights up.  We never return zero sensors.

ACPI thermal zone values arrive in tenths of a kelvin
(``Temperature = current_temp_decikelvin``); we convert to °C in the
poll path.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from trcc.legacy.adapters.system.windows.sources._base import WindowsSensorSource
from trcc.legacy.core.models import SensorInfo

if TYPE_CHECKING:
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator

log = logging.getLogger(__name__)

_MSACPI_NAMESPACE = "root\\wmi"


def _wmi_root_wmi() -> Any:
    """Return a handle to ``root\\wmi`` or ``None`` if WMI isn't usable here."""
    try:
        from trcc.legacy.adapters.system._windows_wmi import wmi_handle
        return wmi_handle(namespace=_MSACPI_NAMESPACE)
    except ImportError:
        log.debug("wmi package unavailable — MSAcpi tier disabled")
    except Exception as e:
        log.debug("root\\wmi handle failed: %s", e)
    return None


@WindowsSensorSource.register('msacpi')
class MSAcpiSource(WindowsSensorSource):
    """ACPI thermal-zone source — bundled with Windows, no driver needed."""

    priority = 30  # Last-resort, runs after LHM/HWiNFO tiers.
    name = "MSAcpi (ACPI thermal zones)"
    provides_gpu = False  # ACPI exposes system/CPU zones, not GPU.

    __slots__ = ("_handle", "_handle_factory", "_zones")

    def __init__(
        self,
        handle_factory: Callable[[], Any] | None = None,
    ) -> None:
        # DI seam — production binds to the real ``root\\wmi`` handle factory;
        # tests inject a stub returning a MagicMock with the rows they want.
        self._handle_factory: Callable[[], Any] = (
            handle_factory if handle_factory is not None else _wmi_root_wmi
        )
        self._handle: Any = None
        # Cache the InstanceName list seen during discover() so poll()
        # doesn't have to re-enumerate every tick.
        self._zones: list[str] = []

    def probe(self) -> bool:
        """Truthy when ``root\\wmi`` returns at least one thermal zone row."""
        self._handle = self._handle_factory()
        if self._handle is None:
            return False
        try:
            zones = list(self._handle.MSAcpi_ThermalZoneTemperature())
        except Exception as e:
            log.debug("MSAcpi_ThermalZoneTemperature query failed: %s", e)
            return False
        if len(zones) == 0:
            log.debug("MSAcpi tier: namespace responded but 0 zones — skipping")
            return False
        self._zones = [str(z.InstanceName) for z in zones]
        return True

    def contribute(self, enum: WindowsSensorEnumerator) -> None:
        for inst in self._zones:
            sid = f'wmi:thermal:{inst}'
            label = _pretty_zone_label(inst)
            enum._sensors.append(
                SensorInfo(sid, label, 'temperature', '°C', 'wmi'),
            )
        log.info("MSAcpi discovery: %d thermal zone(s)", len(self._zones))
        enum._register_poll(self.poll)

    def poll(self, enum: WindowsSensorEnumerator,
             readings: dict[str, float]) -> None:
        if self._handle is None:
            return
        try:
            for z in self._handle.MSAcpi_ThermalZoneTemperature():
                # ACPI reports tenths of a kelvin.  °C = (decikelvin / 10) − 273.15.
                raw = float(z.CurrentTemperature)
                celsius = (raw / 10.0) - 273.15
                readings[f'wmi:thermal:{z.InstanceName}'] = celsius
        except Exception as e:
            log.debug("MSAcpi poll failed: %s", e)


def _pretty_zone_label(instance_name: str) -> str:
    """Strip the ACPI path noise off an InstanceName for the UI.

    Example::

        ACPI\\ThermalZone\\TZ00_0 → Thermal Zone TZ00
    """
    leaf = instance_name.rsplit('\\', 1)[-1]
    stripped = leaf.rstrip('_0').strip()
    cleaned = stripped if stripped != '' else leaf
    return f"Thermal Zone {cleaned}" if cleaned != '' else "Thermal Zone"


__all__ = ['MSAcpiSource']
