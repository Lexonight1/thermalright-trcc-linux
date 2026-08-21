#!/usr/bin/env python3
"""Windows runtime smoke — exercises the real ``WindowsPlatform`` end to end.

Run on Windows (any version Win10+).  Hardware-optional — Thermalright
device probes report SKIP without one.  Output is paste-ready for an
issue.

What it actually does on Windows:
- Imports + instantiates ``WindowsPlatform``
- Calls ``scan_devices()`` (SetupAPI), reports count
- Walks the ``WindowsSensorSource`` strategy chain — each registered
  source's ``probe()`` reports live status
- Verifies the ``wmi`` package + ``pywin32`` work
- Verifies ``libusb-1.0.dll`` is findable by ctypes
- Optional: confirms LHM (PawnIO build) or HWiNFO64 are available

Usage::

    python dev\\smoke_windows.py
    PYTHONPATH=src python dev\\smoke_windows.py

Exit 0 = no FAIL probes.  Exit 1 = at least one FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src'))
sys.path.insert(0, str(_REPO_ROOT / 'dev'))

from _smoke_runtime import (
    Section,
    print_header,
    print_section,
    print_summary_and_exit,
    require_os,
    short_exc,
)


def _probe_imports() -> Section:
    s = Section('imports')
    try:
        from trcc.adapters.system.windows import WindowsPlatform  # noqa: F401
        s.ok('trcc.adapters.system.windows', 'WindowsPlatform importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.windows', exc)

    for mod, note in [
        ('wmi', 'Win32 Management Instrumentation Python wrapper'),
        ('win32api', 'pywin32 base'),
    ]:
        try:
            __import__(mod)
            s.ok(mod, note)
        except ImportError as exc:
            s.fail(mod, exc)

    try:
        import usb.core  # noqa: F401
        s.ok('pyusb', 'libusb backend importable')
    except BaseException as exc:
        s.fail('pyusb', exc)

    try:
        import pynvml  # noqa: F401
        s.ok('pynvml', 'NVIDIA NVML wrapper available')
    except ImportError:
        s.skip('pynvml', 'not installed (NVIDIA GPU optional)')

    try:
        import hid  # noqa: F401
        s.ok('hidapi', 'hidapi binding available')
    except ImportError:
        s.skip('hidapi', 'not installed (HID protocol optional)')
    return s


def _probe_dlls() -> Section:
    s = Section('native libraries')
    import ctypes
    try:
        ctypes.CDLL('libusb-1.0.dll')
        s.ok('libusb-1.0.dll', 'loadable from current DLL search path')
    except OSError as exc:
        s.fail('libusb-1.0.dll',
               OSError(f'{exc} — set os.add_dll_directory or place DLL alongside exe'))
    return s


def _probe_platform() -> Section:
    s = Section('platform')
    from trcc.adapters.system import current_platform
    s.run('current_platform()',
          lambda: f'returned {type(current_platform()).__name__}')
    return s


def _probe_devices() -> Section:
    s = Section('devices')
    from trcc.adapters.system import current_platform
    p = current_platform()
    try:
        devices = list(p.scan_devices())
    except BaseException as exc:
        s.fail('scan_devices()', exc)
        return s
    if len(devices) == 0:
        s.skip('scan_devices()',
               '0 devices found — no Thermalright device plugged in (expected without hardware)')
    else:
        names = ', '.join(f'{d.vid:04x}:{d.pid:04x}' for d in devices)
        s.ok('scan_devices()', f'found {len(devices)} device(s): {names}')
    return s


def _probe_sensor_chain() -> Section:
    """Which CPU sources are wired, and which of them answer on this machine.

    On Windows this is the most useful single diagnostic: whether CPU
    temperature comes from HWiNFO, LHM, WMI-ACPI, or falls through to psutil.

    The registry, priority ordering and probe()/stop() this used to walk are
    gone.  Sources are now ordinary objects composed into a chain by
    ``build_windows_sensors``; "priority" is list order, and "available" means
    "returns a value", which is how the chain falls through.  ``_sources`` is
    private, as ``WindowsSensorSource._registry`` was -- composition is the
    fact a per-OS smoke exists to report.
    """
    s = Section('sensor sources (chain composition)')
    try:
        from trcc.adapters.sensors.windows import build_windows_sensors
    except BaseException as exc:
        s.fail('build_windows_sensors import', exc)
        return s

    try:
        enum = build_windows_sensors()
    except BaseException as exc:
        s.fail('build_windows_sensors()', exc)
        return s

    chain = enum.cpu()
    members = list(getattr(chain, '_sources', []))
    if not members:
        s.fail('chain', RuntimeError('CPU chain built with 0 sources'))
        return s
    s.ok('chain', f'{len(members)} CPU source(s), in priority order: '
                  f'{", ".join(type(m).__name__ for m in members)}')

    for m in members:
        name = type(m).__name__
        try:
            temp = m.temp()
            if temp is None:
                s.skip(name, 'no reading — not available on this machine')
            else:
                s.ok(name, f'live: temp = {temp:.1f}')
        except BaseException as exc:
            s.fail(name, exc)

    s.ok('gpu / fan chains',
         f'{len(enum.gpus())} gpu chain(s), {len(enum.fans())} fan source(s)')
    return s


def _probe_enumerator() -> Section:
    s = Section('sensor enumeration')
    from trcc.adapters.system import current_platform
    p = current_platform()
    try:
        enum = p.sensors()
        infos = enum.discover()
        readings = enum.read_all()
        s.ok('discover() + read_all()',
             f'{len(infos)} sensors discovered, {len(readings)} live readings')

        # map_defaults() returned {metric_key: sensor_id} and lived in the
        # legacy tree, which left main in 73f4122d.  snapshot() answers the
        # same question without the indirection: HardwareMetrics carries each
        # metric as a typed field, so "is it resolved" and "is it live" become
        # one check.  0.0 is the unset value the renderer treats as absent.
        metrics = enum.snapshot()
        for key, label in [
            ('cpu_percent', 'CPU usage'),
            ('mem_percent', 'Memory usage'),
            ('cpu_temp',    'CPU temperature'),
            ('gpu_temp',    'GPU temperature'),
        ]:
            value = getattr(metrics, key, None)
            if not value:
                level = s.skip if key in ('cpu_temp', 'gpu_temp') else s.warn
                level(f'metric:{key}',
                      f'{label} — no reading (HWiNFO/LHM not running?)')
            else:
                s.ok(f'metric:{key}', f'{label} = {value:.1f}')
    except BaseException as exc:
        s.fail('enumerator', exc)
    return s


def _probe_windows_specifics() -> Section:
    s = Section('windows-specific')
    try:
        from trcc.adapters.system._windows_wmi import wmi_handle
        h = wmi_handle()
        # Try a basic query that should always succeed.
        h.Win32_OperatingSystem()
        s.ok('WMI (root\\cimv2)', 'Win32_OperatingSystem queryable')
    except ImportError as exc:
        s.fail('WMI helper', exc)
    except BaseException as exc:
        s.warn('WMI', short_exc(exc))

    # LHM namespace probe (if running).
    try:
        from trcc.adapters.sensors._lhm import _probe_wmi_namespace
        ns = _probe_wmi_namespace()
        if ns is None:
            s.skip('LHM WMI namespace', 'root\\LibreHardwareMonitor not registered (LHM not running)')
        else:
            s.ok('LHM WMI namespace', 'root\\LibreHardwareMonitor responding')
    except BaseException as exc:
        s.warn('LHM namespace probe', short_exc(exc))

    # HWiNFO MMF probe (if running).
    try:
        from trcc.adapters.sensors._hwinfo import _HWiNFOMapping
        m = _HWiNFOMapping()
        if m.open():
            s.ok('HWiNFO SHM (Global\\HWiNFO_SENS_SM2)', 'mapped successfully')
            m.close()
        else:
            s.skip('HWiNFO SHM', 'not available — HWiNFO not running or SHM disabled')
    except BaseException as exc:
        s.warn('HWiNFO probe', short_exc(exc))
    return s


def main() -> int:
    if not require_os('win'):
        return 0
    print_header('Windows')
    sections = [
        _probe_imports(),
        _probe_dlls(),
        _probe_platform(),
        _probe_devices(),
        _probe_sensor_chain(),
        _probe_enumerator(),
        _probe_windows_specifics(),
    ]
    for s in sections:
        print_section(s)
    return print_summary_and_exit(sections)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        print(f"\n  FATAL: {short_exc(exc)}")
        sys.exit(2)
