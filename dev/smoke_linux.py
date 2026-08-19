#!/usr/bin/env python3
"""Linux runtime smoke — exercises the real ``LinuxOS`` end to end.

Run on a Linux box (any distro).  No Thermalright hardware required —
hardware-dependent probes report SKIP, not FAIL.  Output is a
structured report a reporter can paste into a GitHub issue.

What it actually does on Linux:
- Imports + instantiates ``LinuxOS``
- Calls ``scan_devices()`` and reports count (SKIP-style if 0)
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
        from trcc.adapters.system.linux import LinuxOS  # noqa: F401
        s.ok('trcc.adapters.system.linux', 'LinuxOS importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.linux', exc)

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
               'returned [] — no Thermalright device plugged in (expected without hardware)')
    else:
        names = ', '.join(f'{d.vid:04x}:{d.pid:04x}' for d in devices)
        s.ok('scan_devices()', f'found {len(devices)} device(s): {names}')
    return s


def _probe_sensors() -> Section:
    s = Section('sensors')
    from trcc.adapters.system import current_platform
    p = current_platform()
    try:
        enum = p.sensors()
        s.ok('sensors()',
             f'returned {type(enum).__name__}')
    except BaseException as exc:
        s.fail('sensors()', exc)
        return s

    try:
        infos = enum.discover()
        if len(infos) == 0:
            s.warn('discover()', 'returned [] — no sensors found')
        else:
            categories = sorted({info.category for info in infos})
            s.ok('discover()',
                 f'{len(infos)} sensors across {len(categories)} categor'
                 f'{"y" if len(categories) == 1 else "ies"}: '
                 f'{", ".join(categories)}')
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

    # Common metric expectations — the canonical sensor ids the aggregator
    # publishes (read_all() is keyed by these dotted ids).
    for sensor_id, label in [
        ('cpu:usage',      'CPU usage'),
        ('memory:percent', 'Memory usage'),
        ('cpu:temp',       'CPU temperature'),
    ]:
        value = readings.get(sensor_id, None)
        if value is None:
            level = s.skip if sensor_id == 'cpu:temp' else s.warn
            level(f'metric:{sensor_id}',
                  f'{label} — no live reading (lm-sensors / hwmon module loaded?)')
        else:
            s.ok(f'metric:{sensor_id}', f'{label} = {value:.1f} (via {sensor_id})')
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
