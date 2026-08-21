#!/usr/bin/env python3
"""BSD runtime smoke — exercises the real ``BSDPlatform`` end to end.

Run on FreeBSD, OpenBSD, NetBSD, or DragonflyBSD.  Hardware-optional.
Output is paste-ready for an issue.

What it actually does on BSD:
- Imports + instantiates ``BSDPlatform``
- Calls ``scan_devices()`` (libusb / usbconfig)
- Builds the sensor enumerator
- Runs the OS-specific sysctl probes (``dev.cpu.N.temperature``, ``hw.sensors``)
- Verifies usbconfig / libusb20 / pyusb all line up

Usage::

    PYTHONPATH=src python3 dev/smoke_bsd.py

Exit 0 = no FAIL probes.  Exit 1 = at least one FAIL.
"""
from __future__ import annotations

import shutil
import subprocess
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
        from trcc.adapters.system.bsd import BsdOS  # noqa: F401
        s.ok('trcc.adapters.system.bsd', 'BsdOS importable')
    except BaseException as exc:
        s.fail('trcc.adapters.system.bsd', exc)

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
        s.skip('pynvml', 'not installed (uncommon on BSD — proprietary driver required)')
    return s


def _probe_binaries() -> Section:
    s = Section('system binaries')
    for binary in ['sysctl', 'usbconfig']:
        path = shutil.which(binary)
        if path is None:
            if binary == 'sysctl':
                s.fail(binary, FileNotFoundError(f'{binary} not on PATH'))
            else:
                s.skip(binary, f'{binary} not on PATH (optional but recommended)')
        else:
            s.ok(binary, path)
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
            ('cpu_temp',    'CPU temperature (sysctl)'),
            ('gpu_temp',    'GPU temperature (NVIDIA only on BSD)'),
        ]:
            value = getattr(metrics, key, None)
            if not value:
                level = s.skip if key in ('cpu_temp', 'gpu_temp') else s.warn
                level(f'metric:{key}',
                      f'{label} — no sensor mapped (kernel module loaded? coretemp/amdtemp?)')
            else:
                s.ok(f'metric:{key}', f'{label} = {value:.1f}')
    except BaseException as exc:
        s.fail('enumerator', exc)
    return s


def _probe_bsd_specifics() -> Section:
    s = Section('bsd-specific')

    sysctl = shutil.which('sysctl')
    if sysctl is None:
        s.fail('sysctl', FileNotFoundError('sysctl not on PATH — kernel without sysctl?'))
        return s

    # Per-CPU temperature
    try:
        result = subprocess.run(
            [sysctl, '-n', 'dev.cpu.0.temperature'],
            capture_output=True, text=True, timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() != '':
            s.ok('dev.cpu.0.temperature', result.stdout.strip())
        else:
            s.skip('dev.cpu.0.temperature',
                   'not present — load coretemp (Intel) or amdtemp (AMD) kernel module')
    except subprocess.SubprocessError as exc:
        s.fail('dev.cpu.0.temperature', exc)

    # hw.sensors framework — OpenBSD primarily
    try:
        result = subprocess.run(
            [sysctl, '-N', 'hw.sensors'],
            capture_output=True, text=True, timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() != '':
            lines = result.stdout.strip().splitlines()
            s.ok('hw.sensors framework', f'{len(lines)} sensor node(s) present')
        else:
            s.skip('hw.sensors framework',
                   'not present — OpenBSD has it; FreeBSD usually does not unless ported')
    except subprocess.SubprocessError as exc:
        s.warn('hw.sensors framework', short_exc(exc))
    return s


def main() -> int:
    # Match any *bsd platform string (FreeBSD = 'freebsd', OpenBSD = 'openbsd', etc.)
    if not require_os('bsd'):
        return 0
    print_header('BSD')
    sections = [
        _probe_imports(),
        _probe_binaries(),
        _probe_platform(),
        _probe_devices(),
        _probe_sensors(),
        _probe_bsd_specifics(),
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
