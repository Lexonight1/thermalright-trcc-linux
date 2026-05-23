#!/usr/bin/env python3
"""Windows runtime smoke — exercises the real ``WindowsPlatform`` end to end.

Run on Windows (any version Win10+).  Hardware-optional — Thermalright
device probes report SKIP without one.  Output is paste-ready for an
issue.

What it actually does on Windows:
- Imports + instantiates ``WindowsPlatform``
- Calls ``detect_devices()`` (SetupAPI), reports count
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
        from trcc.adapters.system.windows_platform import WindowsPlatform  # noqa: F401
        s.ok('trcc.adapters.system.windows_platform', 'WindowsPlatform importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.windows_platform', exc)

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
    from trcc.adapters.system import PlatformFactory
    s.run('PlatformFactory.current()',
          lambda: f'returned {type(PlatformFactory.current()).__name__}')
    return s


def _probe_devices() -> Section:
    s = Section('devices')
    from trcc.adapters.system import PlatformFactory
    p = PlatformFactory.current()
    try:
        devices = list(p.detect_devices())
    except BaseException as exc:
        s.fail('detect_devices()', exc)
        return s
    if len(devices) == 0:
        s.skip('detect_devices()',
               '0 devices found — no Thermalright device plugged in (expected without hardware)')
    else:
        names = ', '.join(f'{d.vid:04x}:{d.pid:04x}' for d in devices)
        s.ok('detect_devices()', f'found {len(devices)} device(s): {names}')
    return s


def _probe_sensor_chain() -> Section:
    s = Section('sensor sources (strategy chain)')
    try:
        from trcc.adapters.system.windows.sources import WindowsSensorSource
    except BaseException as exc:
        s.fail('WindowsSensorSource import', exc)
        return s

    sources = WindowsSensorSource.in_priority_order()
    if len(sources) == 0:
        s.fail('registry',
               RuntimeError('0 sources registered — windows/sources/__init__.py guard issue?'))
        return s
    keys = list(WindowsSensorSource._registry.keys())
    s.ok('registry', f'{len(sources)} source(s): {", ".join(keys)}')

    for src in sources:
        try:
            available = src.probe()
            if available:
                s.ok(f'{src.name} (priority {src.priority})', 'probe returned True — source live')
            else:
                s.skip(f'{src.name} (priority {src.priority})', 'probe returned False — not available on this machine')
        except BaseException as exc:
            s.fail(f'{src.name} (priority {src.priority})', exc)
        finally:
            try:
                src.stop()
            except Exception:
                pass
    return s


def _probe_enumerator() -> Section:
    s = Section('sensor enumeration')
    from trcc.adapters.system import PlatformFactory
    p = PlatformFactory.current()
    try:
        enum = p._make_sensor_enumerator()
        infos = enum.discover()
        readings = enum.read_all()
        s.ok('discover() + read_all()',
             f'{len(infos)} sensors discovered, {len(readings)} live readings')

        mapping = enum.map_defaults()
        for key, label in [
            ('cpu_percent', 'CPU usage'),
            ('mem_percent', 'Memory usage'),
            ('cpu_temp',    'CPU temperature'),
            ('gpu_temp',    'GPU temperature'),
        ]:
            sensor_id = mapping.get(key, '')
            if sensor_id == '':
                level = s.skip if key in ('cpu_temp', 'gpu_temp') else s.warn
                level(f'metric:{key}',
                      f'{label} — no sensor mapped (HWiNFO/LHM not running?)')
            else:
                value = readings.get(sensor_id, None)
                if value is None:
                    s.warn(f'metric:{key}', f'mapped to {sensor_id} but no live value')
                else:
                    s.ok(f'metric:{key}', f'{label} = {value:.1f} (via {sensor_id})')
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
        from trcc.adapters.system.windows.sources.lhm import _probe_wmi_namespace
        ns = _probe_wmi_namespace()
        if ns is None:
            s.skip('LHM WMI namespace', 'root\\LibreHardwareMonitor not registered (LHM not running)')
        else:
            s.ok('LHM WMI namespace', 'root\\LibreHardwareMonitor responding')
    except BaseException as exc:
        s.warn('LHM namespace probe', short_exc(exc))

    # HWiNFO MMF probe (if running).
    try:
        from trcc.adapters.system.windows.sources.hwinfo import _HWiNFOMapping
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
