#!/usr/bin/env python3
"""Linux runtime smoke — exercises the real ``LinuxPlatform`` end to end.

Run on a Linux box (any distro).  No Thermalright hardware required —
hardware-dependent probes report SKIP, not FAIL.  Output is a
structured report a reporter can paste into a GitHub issue.

What it actually does on Linux:
- Imports + instantiates ``LinuxPlatform``
- Calls ``detect_devices()`` and reports count (SKIP-style if 0)
- Builds the sensor enumerator, runs ``discover()`` + ``read_all()``
- Verifies hwmon directory exists, RAPL readable, pyusb importable,
  optional pynvml works if an NVIDIA GPU is present

Usage::

    PYTHONPATH=src python3 dev/smoke_linux.py

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
        from trcc.adapters.system.linux_platform import LinuxPlatform  # noqa: F401
        s.ok('trcc.adapters.system.linux_platform', 'LinuxPlatform importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.linux_platform', exc)

    for mod, note in [
        ('pyusb', 'libusb backend for raw USB'),
        ('psutil', 'CPU / memory / disk / net base'),
    ]:
        try:
            __import__('usb.core' if mod == 'pyusb' else mod)
            s.ok(mod, note)
        except BaseException as exc:
            s.fail(mod, exc)

    # Optional — NVIDIA only
    try:
        import pynvml  # noqa: F401
        s.ok('pynvml', 'NVIDIA NVML wrapper available')
    except ImportError:
        s.skip('pynvml', 'not installed (NVIDIA GPU optional)')
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
               'returned [] — no Thermalright device plugged in (expected without hardware)')
    else:
        names = ', '.join(f'{d.vid:04x}:{d.pid:04x}' for d in devices)
        s.ok('detect_devices()', f'found {len(devices)} device(s): {names}')
    return s


def _probe_sensors() -> Section:
    s = Section('sensors')
    from trcc.adapters.system import PlatformFactory
    p = PlatformFactory.current()
    try:
        enum = p._make_sensor_enumerator()
        s.ok('make_sensor_enumerator()',
             f'returned {type(enum).__name__}')
    except BaseException as exc:
        s.fail('make_sensor_enumerator()', exc)
        return s

    try:
        infos = enum.discover()
        if len(infos) == 0:
            s.warn('discover()', 'returned [] — no sensors found')
        else:
            sources = sorted({info.source for info in infos})
            s.ok('discover()',
                 f'{len(infos)} sensors across {len(sources)} source(s): '
                 f'{", ".join(sources)}')
    except BaseException as exc:
        s.fail('discover()', exc)
        return s

    try:
        readings = enum.read_all()
        if len(readings) == 0:
            s.warn('read_all()', 'returned {} — no readings')
        else:
            s.ok('read_all()',
                 f'{len(readings)} live values')
    except BaseException as exc:
        s.fail('read_all()', exc)
        return s

    # Common metric expectations
    mapping = enum.map_defaults()
    for key, label in [
        ('cpu_percent', 'CPU usage'),
        ('mem_percent', 'Memory usage'),
        ('cpu_temp',    'CPU temperature'),
    ]:
        sensor_id = mapping.get(key, '')
        if sensor_id == '':
            level = s.skip if key == 'cpu_temp' else s.warn
            level(f'metric:{key}',
                  f'{label} — no sensor mapped (lm-sensors / hwmon module loaded?)')
        else:
            value = readings.get(sensor_id, None)
            if value is None:
                s.warn(f'metric:{key}',
                       f'mapped to {sensor_id} but no live reading')
            else:
                s.ok(f'metric:{key}', f'{label} = {value:.1f} (via {sensor_id})')
    return s


def _probe_linux_specifics() -> Section:
    s = Section('linux-specific')

    hwmon = Path('/sys/class/hwmon')
    if hwmon.is_dir():
        chips = sorted(p.name for p in hwmon.iterdir() if p.is_dir())
        s.ok('hwmon', f'{len(chips)} chip(s): {", ".join(chips[:4])}{"..." if len(chips) > 4 else ""}')
    else:
        s.warn('hwmon', f'{hwmon} not present — kernel without CONFIG_HWMON?')

    rapl = Path('/sys/class/powercap/intel-rapl:0/energy_uj')
    if not rapl.exists():
        s.skip('rapl', 'no Intel RAPL on this CPU (AMD or kernel <3.13)')
    else:
        try:
            with rapl.open() as f:
                _val = int(f.read().strip())
            s.ok('rapl', f'readable as user (energy_uj = {_val} µJ)')
        except PermissionError:
            s.warn('rapl', "exists but not user-readable — run 'sudo trcc setup-rapl'")
        except BaseException as exc:
            s.fail('rapl', exc)

    udev = Path('/etc/udev/rules.d/99-trcc-lcd.rules')
    if udev.is_file():
        s.ok('udev rule', f'{udev} present')
    else:
        s.skip('udev rule', f'{udev} absent — run "sudo trcc setup-udev" for non-root device access')
    return s


def main() -> int:
    if not require_os('linux'):
        return 0
    print_header('Linux')
    sections = [
        _probe_imports(),
        _probe_platform(),
        _probe_devices(),
        _probe_sensors(),
        _probe_linux_specifics(),
    ]
    for s in sections:
        print_section(s)
    return print_summary_and_exit(sections)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # last-resort safety net for non-system exits
        print(f"\n  FATAL: {short_exc(exc)}")
        sys.exit(2)
