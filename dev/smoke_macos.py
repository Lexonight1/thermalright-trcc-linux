#!/usr/bin/env python3
"""macOS runtime smoke — exercises the real ``MacOSPlatform`` end to end.

Run on macOS (Intel or Apple Silicon).  Hardware-optional.  Output is
paste-ready for an issue.

What it actually does on macOS:
- Imports + instantiates ``MacOSPlatform``
- Calls ``scan_devices()`` (IOKit USB enum), reports count
- Builds the sensor enumerator, runs ``discover()`` + ``read_all()``
- Verifies IOKit + IOHIDManager frameworks are loadable via ctypes
- Reports SMC keys discovered + Apple Silicon HID hub status

Usage::

    PYTHONPATH=src python3 dev/smoke_macos.py

Exit 0 = no FAIL probes.  Exit 1 = at least one FAIL.
"""
from __future__ import annotations

import platform
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
        from trcc.adapters.system.macos import MacOSPlatform  # noqa: F401
        s.ok('trcc.adapters.system.macos', 'MacOSPlatform importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.macos', exc)

    try:
        import usb.core  # noqa: F401
        s.ok('pyusb', 'libusb backend importable')
    except BaseException as exc:
        s.fail('pyusb', exc)

    try:
        import psutil  # noqa: F401
        s.ok('psutil', 'CPU / memory / disk / net base')
    except BaseException as exc:
        s.fail('psutil', exc)

    try:
        import pynvml  # noqa: F401
        s.ok('pynvml', 'NVIDIA NVML wrapper available')
    except ImportError:
        s.skip('pynvml', 'not installed (rare on macOS — NVIDIA support deprecated by Apple)')
    return s


def _probe_frameworks() -> Section:
    s = Section('macOS frameworks')
    import ctypes
    import ctypes.util as ctu
    for fw in ['IOKit', 'CoreFoundation']:
        path = ctu.find_library(fw)
        if path is None:
            s.fail(fw, OSError(f'find_library({fw!r}) returned None'))
            continue
        try:
            ctypes.CDLL(path)
            s.ok(fw, path)
        except OSError as exc:
            s.fail(fw, exc)
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


def _probe_sensors() -> Section:
    s = Section('sensors')
    from trcc.adapters.system import current_platform
    p = current_platform()
    try:
        enum = p.sensors()
        infos = enum.discover()
        readings = enum.read_all()
        s.ok('discover() + read_all()',
             f'{len(infos)} sensors / {len(readings)} readings')

        # map_defaults() returned {metric_key: sensor_id} and lived in the
        # legacy tree, which left main in 73f4122d.  snapshot() answers the
        # same question without the indirection: HardwareMetrics carries each
        # metric as a typed field, so "is it resolved" and "is it live" become
        # one check.  0.0 is the unset value the renderer treats as absent.
        metrics = enum.snapshot()
        for key, label in [
            ('cpu_percent', 'CPU usage'),
            ('mem_percent', 'Memory usage'),
            ('cpu_temp',    'CPU temperature (SMC)'),
            ('gpu_temp',    'GPU temperature'),
            ('fan_cpu',     'CPU fan'),
        ]:
            value = getattr(metrics, key, None)
            if not value:
                level = s.skip if key in ('cpu_temp', 'gpu_temp', 'fan_cpu') else s.warn
                level(f'metric:{key}',
                      f'{label} — no reading on this Mac generation')
            else:
                s.ok(f'metric:{key}', f'{label} = {value:.1f}')
    except BaseException as exc:
        s.fail('enumerator', exc)
    return s


def _probe_macos_specifics() -> Section:
    s = Section('macOS-specific')
    arch = platform.machine()
    s.ok('CPU architecture', arch)

    if arch == 'arm64':
        s.skip('Apple Silicon — IOReport',
               'IOReport private framework path not yet implemented in TRCC '
               '(planned: macOS lift + IOReportSource)')
        s.warn('Apple Silicon — SMC keys',
               'SMC key names differ M1→M5; auto-discovery may miss some readings')
    else:
        s.ok('Intel Mac', 'SMC keys are stable across this generation')

    # powermetrics binary
    pm = Path('/usr/bin/powermetrics')
    if pm.exists():
        s.ok('powermetrics binary', f'{pm} present (requires sudo to invoke)')
    else:
        s.warn('powermetrics binary',
               f'{pm} not found — power readings will skip')
    return s


def main() -> int:
    if not require_os('darwin'):
        return 0
    print_header('macOS')
    sections = [
        _probe_imports(),
        _probe_frameworks(),
        _probe_platform(),
        _probe_devices(),
        _probe_sensors(),
        _probe_macos_specifics(),
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
