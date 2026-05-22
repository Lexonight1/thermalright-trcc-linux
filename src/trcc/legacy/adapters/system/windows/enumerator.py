"""WindowsSensorEnumerator — walks the registered ``WindowsSensorSource`` chain.

Mirrors the macOS / Linux / BSD enumerators in shape (one
``SensorEnumeratorBase`` subclass per OS) but delegates per-source work
to ``@WindowsSensorSource.register``-decorated strategies in ``sources/``.

Discovery order:

  1. ``_discover_psutil_base()``      — always (CPU usage, memory, disk, net)
  2. Windows-side psutil temps        — ``sensors_temperatures()`` if exposed
  3. Each registered source, in priority order — probe + contribute if live
  4. ``_discover_nvidia()``           — only if no source covered GPU
  5. ``_discover_computed()``         — date/time

Per-tick polling walks the same chain of bound callbacks plus the base
helpers (psutil, NVIDIA, computed I/O).

Adding a new source: drop a new module under ``sources/`` decorated with
``@WindowsSensorSource.register('key')``.  Zero touchpoints here.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import psutil

from trcc.legacy.adapters.system._base import SensorEnumeratorBase
from trcc.legacy.adapters.system.windows.sources import WindowsSensorSource
from trcc.legacy.core.models import SensorInfo

log = logging.getLogger(__name__)

PollFn = Callable[["WindowsSensorEnumerator", dict[str, float]], None]


class WindowsSensorEnumerator(SensorEnumeratorBase):
    """Windows sensor enumerator — orchestrates the source chain."""

    def __init__(self) -> None:
        super().__init__()
        self._live_sources: list[WindowsSensorSource] = []
        self._poll_callbacks: list[PollFn] = []

    # ══════════════════════════════════════════════════════════════════════
    # Discovery
    # ══════════════════════════════════════════════════════════════════════

    def discover(self) -> list[SensorInfo]:
        self._sensors.clear()
        self._live_sources.clear()
        self._poll_callbacks.clear()

        self._discover_psutil_base()
        self._discover_psutil_temps()

        gpu_covered = False
        for source in WindowsSensorSource.in_priority_order():
            if not source.probe():
                continue
            source.contribute(self)
            self._live_sources.append(source)
            if source.provides_gpu:
                # A source declares ``provides_gpu = True`` if its
                # ``contribute()`` registers GPU sensors when present.
                # An LHMSource without an LHM-managed GPU sets its own
                # ``gpu_seen`` flag to False; honour it so the base NVIDIA
                # fallback can still run.
                if getattr(source, 'gpu_seen', True):
                    gpu_covered = True

        if not gpu_covered:
            self._discover_nvidia()

        self._discover_computed()
        log.info("Windows sensor discovery: %d sensors across %d source(s)",
                 len(self._sensors), len(self._live_sources))
        return self._sensors

    def _discover_psutil_temps(self) -> None:
        """Register psutil's ``sensors_temperatures()`` chips if exposed.

        On Windows this is usually empty (psutil has no built-in temp
        sources for the OS), but we honour the API in case a future
        psutil release adds Windows support — no harm if the dict is
        empty.
        """
        if not hasattr(psutil, 'sensors_temperatures'):
            return
        temps = psutil.sensors_temperatures()
        for chip, entries in temps.items():
            for i, entry in enumerate(entries):
                sid = f'psutil:temp:{chip}:{i}'
                label = entry.label or f'{chip} temp{i}'
                self._sensors.append(
                    SensorInfo(sid, label, 'temperature', '°C', 'psutil'),
                )

    # ══════════════════════════════════════════════════════════════════════
    # Polling
    # ══════════════════════════════════════════════════════════════════════

    def _register_poll(self, fn: PollFn) -> None:
        """Bind a source's per-tick poll callback.  Called from ``contribute()``."""
        self._poll_callbacks.append(fn)

    def _poll_platform(self, readings: dict[str, float]) -> None:
        # Windows-side psutil temps (kept inline — base handles cross-platform).
        if hasattr(psutil, 'sensors_temperatures'):
            for chip, entries in psutil.sensors_temperatures().items():
                for i, entry in enumerate(entries):
                    readings[f'psutil:temp:{chip}:{i}'] = entry.current
        # Walk every live source's poll callback.
        for fn in self._poll_callbacks:
            try:
                fn(self, readings)
            except Exception:
                log.debug("source poll failed", exc_info=True)

    def _on_stop(self) -> None:
        for source in self._live_sources:
            try:
                source.stop()
            except Exception:
                log.debug("source stop failed", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════
    # GPU enumeration — ask sources first, then fall back
    # ══════════════════════════════════════════════════════════════════════

    def get_gpu_list(self) -> list[tuple[str, str]]:
        """Enumerate GPUs across all live sources, then pynvml, then WMI.

        Each live source may expose an optional ``gpu_list()`` returning
        ``[(key, display_name), ...]``.  We walk them in priority order;
        the first non-empty list wins.  Falls through to pynvml (NVIDIA)
        and finally ``Win32_VideoController`` for universal coverage.
        """
        for src in self._live_sources:
            source_gpus = _source_gpu_list(src)
            if len(source_gpus) > 0:
                return source_gpus
        nvidia_gpus = super().get_gpu_list()
        if len(nvidia_gpus) > 0:
            return nvidia_gpus
        return _wmi_video_controller_gpus()

    # ══════════════════════════════════════════════════════════════════════
    # Mapping — metric_key → sensor_id with chain-aware priority
    # ══════════════════════════════════════════════════════════════════════

    # Priority order for picking the best sensor when multiple sources
    # provide the same metric.  ``hwinfo`` is highest because the HWiNFO
    # SHM struct yields the most accurate readings; ``wmi`` (MSAcpi) is
    # the lowest-information last-resort.
    _SOURCE_PRIORITY: tuple[str, ...] = ('hwinfo', 'lhm', 'nvidia', 'psutil', 'wmi')

    def _build_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        self._map_common(mapping)

        mapping['cpu_temp'] = (
            self._first_temp_named('package')
            or self._first_temp_named('cpu')
            or self._first_temp(source='psutil')
        )
        mapping['cpu_power'] = (
            self._first_named('package', category='power')
            or self._first_named('cpu', category='power')
        )

        # GPU mapping — pick the best source that has a GPU temp registered.
        gpu_src = self._first_gpu_source()
        if gpu_src:
            mapping['gpu_temp'] = self._first_named('gpu', category='temperature', source=gpu_src) \
                                  or self._first_temp(source=gpu_src)
            mapping['gpu_usage'] = (
                self._first_named('gpu', category='usage', source=gpu_src)
                or self._first_named('gpu', category='gpu_busy', source=gpu_src)
                or self._first(category='gpu_busy', source=gpu_src)
            )
            mapping['gpu_clock'] = self._first_named('gpu', category='clock', source=gpu_src) \
                                   or self._first(category='clock', source=gpu_src)
            mapping['gpu_power'] = self._first_named('gpu', category='power', source=gpu_src) \
                                   or self._first(category='power', source=gpu_src)
        else:
            mapping['gpu_temp'] = ''
            mapping['gpu_usage'] = ''
            mapping['gpu_clock'] = ''
            mapping['gpu_power'] = ''

        mapping['mem_temp'] = self._first_named('memory', category='temperature')
        mapping['disk_temp'] = (
            self._first_named('drive', category='temperature')
            or self._first_named('ssd', category='temperature')
            or self._first_named('nvme', category='temperature')
        )

        self._map_fans(mapping, fan_sources=('hwinfo', 'lhm', 'nvidia'))
        return mapping

    # ── Mapping helpers ────────────────────────────────────────────────────

    def _first(self, *, category: str = '', source: str = '') -> str:
        """First sensor with the given category (and optional source)."""
        return self._find_first(self._sensors, category=category, source=source)

    def _first_temp(self, *, source: str = '') -> str:
        return self._find_first(self._sensors, category='temperature', source=source)

    def _first_temp_named(self, name_contains: str) -> str:
        """First temperature whose name contains ``name_contains``.

        Walks _SOURCE_PRIORITY in order so HWiNFO beats LHM beats psutil.
        """
        for src in self._SOURCE_PRIORITY:
            sid = self._find_first(
                self._sensors,
                source=src,
                category='temperature',
                name_contains=name_contains,
            )
            if sid != '':
                return sid
        return ''

    def _first_named(
        self, name_contains: str,
        *, category: str = '', source: str = '',
    ) -> str:
        """Find by name across either a single source or the priority chain."""
        if source != '':
            return self._find_first(
                self._sensors, source=source,
                category=category, name_contains=name_contains,
            )
        for src in self._SOURCE_PRIORITY:
            sid = self._find_first(
                self._sensors, source=src,
                category=category, name_contains=name_contains,
            )
            if sid != '':
                return sid
        return ''

    def _first_gpu_source(self) -> str:
        """Pick the best source that has any GPU-named temperature sensor."""
        for src in self._SOURCE_PRIORITY:
            if self._find_first(
                self._sensors, source=src,
                category='temperature', name_contains='gpu',
            ) != '':
                return src
            # NVIDIA sensors are registered with source='nvidia' and have
            # GPU in their name implicitly (e.g. "GeForce RTX 4090 Temp"),
            # but they may also exist under a different format if the
            # nvidia label doesn't include "GPU".  Allow plain-source match.
            if src == 'nvidia' and self._find_first(
                self._sensors, source='nvidia', category='temperature',
            ) != '':
                return 'nvidia'
        return ''


# ══════════════════════════════════════════════════════════════════════════
# Helpers — kept module-level so the class body stays focused
# ══════════════════════════════════════════════════════════════════════════

def _source_gpu_list(src: WindowsSensorSource) -> list[tuple[str, str]]:
    """Call ``src.gpu_list()`` if defined; swallow errors to empty list."""
    fn = getattr(src, 'gpu_list', None)
    if fn is None:
        return []
    try:
        return list(fn())
    except Exception:
        log.debug("source %s.gpu_list() failed", src.name, exc_info=True)
        return []


def _wmi_video_controller_gpus() -> list[tuple[str, str]]:
    """Last-resort GPU enumeration via ``Win32_VideoController``.

    Win32_VideoController.AdapterRAM is a 32-bit unsigned int — it caps at
    4 GiB on cards with more VRAM (a known WMI limitation, not a code
    bug).  Just show the name; sensor data lives in LHM/HWiNFO, not here.
    """
    try:
        from trcc.legacy.adapters.system._windows_wmi import wmi_handle
        w = wmi_handle()
        out: list[tuple[str, str]] = []
        for i, vc in enumerate(w.Win32_VideoController()):
            if vc.Name is None:
                continue
            stripped = str(vc.Name).strip()
            display = stripped if stripped != '' else f'GPU {i}'
            if stripped != '':
                out.append((f'wmi:{i}', display))
        return out
    except ImportError:
        log.debug("wmi package unavailable — no WMI GPU enumeration")
        return []
    except Exception:
        log.debug("Win32_VideoController query failed", exc_info=True)
        return []


__all__ = ['WindowsSensorEnumerator']
