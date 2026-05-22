#!/usr/bin/env python3
"""Sleep/resume cycle smoke — proves #144 (Tee86 post-suspend) is fixed.

Drives a Trcc through the full suspend → resume flow without real
hardware.  MockPlatform captures the power callbacks that Linux's
systemd-logind would invoke; the test fires them manually.

Asserts the contract published in trcc.core.events.Topic:

  1. SYSTEM_SUSPENDED event fires before devices clear
  2. DEVICE_LIST is published as ()  (empty) after cleanup
  3. Protocol factory cache is cleared
  4. SYSTEM_RESUMED event fires after rediscover
  5. Devices are repopulated by discover()
  6. DEVICE_LIST is published with the new device tuple

Failure exits 1.  Pass exits 0.

Usage::
    PYTHONPATH=src python3 dev/smoke_sleep_cycle.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / 'src'))
sys.path.insert(0, str(_REPO))

from tests.mock_platform import DEFAULT_DEVICES, MockPlatform  # type: ignore[import-not-found]

from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
from trcc.legacy.core.events import Topic
from trcc.legacy.core.trcc import Trcc

_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: str = '') -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        msg = f"  FAIL  {label}" + (f" — {detail}" if detail else '')
        print(msg)
        _FAILURES.append(label)


def main() -> int:
    print("=== Trcc sleep/resume cycle smoke ===\n")

    # Build a Trcc with 2 mock devices (1 LCD, 1 LED)
    platform = MockPlatform(list(DEFAULT_DEVICES))
    trcc = Trcc(platform=platform)
    trcc.discover()

    # Capture events
    events: list[tuple[str, object]] = []
    trcc.events.subscribe(Topic.SYSTEM_SUSPENDED, lambda: events.append(('suspended', None)))
    trcc.events.subscribe(Topic.SYSTEM_RESUMED, lambda: events.append(('resumed', None)))
    trcc.events.subscribe(Topic.DEVICE_LIST, lambda devs: events.append(('device_list', tuple(devs))))

    print("step 1: initial state after discover()")
    initial_lcd = len(trcc._lcd_devices)
    initial_led = len(trcc._led_devices)
    initial_total = initial_lcd + initial_led
    initial_cache = len(DeviceProtocolFactory._protocols)
    _check(initial_total >= 1, f"discovered {initial_total} device(s) total "
           f"(lcd={initial_lcd} led={initial_led})")
    print(f"           cache={initial_cache} protocols")

    print("\nstep 2: fire suspend")
    platform.fire_suspend()

    _check(events[0][0] == 'suspended',
           "SYSTEM_SUSPENDED fires first",
           f"got events: {[e[0] for e in events]}")
    _check(any(e[0] == 'device_list' and e[1] == () for e in events),
           "DEVICE_LIST published as () after suspend cleanup")
    _check(len(trcc._lcd_devices) == 0,
           "_lcd_devices cleared")
    _check(len(trcc._led_devices) == 0,
           "_led_devices cleared")
    _check(len(DeviceProtocolFactory._protocols) == 0,
           "DeviceProtocolFactory cache cleared")

    print("\nstep 3: fire resume")
    pre_resume_events = len(events)
    platform.fire_resume()

    new_events = events[pre_resume_events:]
    _check(any(e[0] == 'device_list' and len(e[1]) >= 1 for e in new_events),
           "DEVICE_LIST republished after resume with devices")
    _check(any(e[0] == 'resumed' for e in new_events),
           "SYSTEM_RESUMED fires after rediscover")
    _check(len(trcc._lcd_devices) == initial_lcd,
           f"_lcd_devices repopulated to {initial_lcd}",
           f"got {len(trcc._lcd_devices)}")
    _check(len(trcc._led_devices) == initial_led,
           f"_led_devices repopulated to {initial_led}",
           f"got {len(trcc._led_devices)}")

    print("\nstep 4: order check — suspended fires before device_list=(); resumed fires after rediscover")
    suspended_idx = next((i for i, e in enumerate(events) if e[0] == 'suspended'), -1)
    empty_list_idx = next(
        (i for i, e in enumerate(events) if e[0] == 'device_list' and e[1] == ()), -1)
    resumed_idx = next((i for i, e in enumerate(events) if e[0] == 'resumed'), -1)
    populated_list_idx = next(
        (i for i, e in enumerate(events)
         if e[0] == 'device_list' and len(e[1]) >= 1 and i > suspended_idx), -1)

    _check(suspended_idx >= 0 and empty_list_idx > suspended_idx,
           "ordering: SYSTEM_SUSPENDED → DEVICE_LIST=()",
           f"suspended_idx={suspended_idx} empty_list_idx={empty_list_idx}")
    _check(populated_list_idx > suspended_idx and resumed_idx > populated_list_idx,
           "ordering: DEVICE_LIST(populated) → SYSTEM_RESUMED",
           f"populated={populated_list_idx} resumed={resumed_idx}")

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL: {len(_FAILURES)} assertion(s)")
        for f in _FAILURES:
            print(f"  - {f}")
        print("=" * 60)
        return 1
    print("PASS: all assertions passed")
    print("=" * 60)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
