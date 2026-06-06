#!/usr/bin/env python3
"""Two-factory chain smoke — PlatformFactory + DeviceFactory dispatch.

The cutover unified legacy's separate Protocol + Device layers into one
``Device`` ABC, so the chain is two factories, not three:

  * ``PlatformFactory.current()`` picks the OS-appropriate ``Platform``
    subclass via ``@PlatformFactory.register(sys.platform)``.
  * ``DeviceFactory.for_wire(wire)`` returns the ``Device`` subclass for
    a wire via ``@DeviceFactory.register(Wire.X)``.

No real USB activity — this asserts registry population + dispatch only,
so it runs anywhere ``ruff + pyright`` does.  Add a check when you add a
new OS or wire registration.

Run:
    PYTHONPATH=src python dev/smoke_factories.py

Exit code 0 on full green, 1 on any divergence.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    from trcc.adapters.device import DeviceFactory
    from trcc.adapters.device.bulk_lcd import BulkLcd
    from trcc.adapters.device.hid_lcd import HidLcd
    from trcc.adapters.device.led import Led
    from trcc.adapters.device.ly_lcd import LyLcd
    from trcc.adapters.device.scsi_lcd import ScsiLcd
    from trcc.adapters.system import PlatformFactory
    from trcc.core.errors import DeviceNotFoundError
    from trcc.core.models import Wire
    from trcc.core.ports import Device, Platform

    failures: list[str] = []

    # 1. PlatformFactory registry has all four OS keys.
    expected_os = {"linux", "win32", "darwin", "bsd"}
    actual_os = set(PlatformFactory._REGISTRY)
    if actual_os != expected_os:
        failures.append(
            f"PlatformFactory registry = {sorted(actual_os)} "
            f"(expected {sorted(expected_os)})",
        )
    else:
        print(f"OK  PlatformFactory registry → {sorted(actual_os)}")

    # 2. current() builds a Platform subclass for THIS OS.
    real_platform = PlatformFactory.current()
    if not isinstance(real_platform, Platform):
        failures.append(
            f"PlatformFactory.current → {type(real_platform).__name__} "
            "is not a Platform subclass",
        )
    else:
        print(f"OK  PlatformFactory.current → {type(real_platform).__name__}")

    # 3. DeviceFactory registry binds every wire to the right class.
    expected_device: dict[Wire, type[Device]] = {
        Wire.SCSI: ScsiLcd,
        Wire.HID: HidLcd,
        Wire.BULK: BulkLcd,
        Wire.LY: LyLcd,
        Wire.LED: Led,
    }
    for wire, expected_cls in expected_device.items():
        try:
            actual = DeviceFactory.for_wire(wire)
        except DeviceNotFoundError as e:
            failures.append(f"DeviceFactory.for_wire({wire.value}) raised {e}")
            continue
        if actual is not expected_cls:
            failures.append(
                f"DeviceFactory.for_wire({wire.value}) → {actual.__name__} "
                f"(expected {expected_cls.__name__})",
            )
        else:
            print(f"OK  DeviceFactory.for_wire({wire.value}) → {actual.__name__}")

    # 4. for_wire raises (not returns None) on an unregistered wire.
    class _FakeWire:
        value = "nonexistent"

    try:
        DeviceFactory.for_wire(_FakeWire())  # type: ignore[arg-type]
    except DeviceNotFoundError:
        print("OK  DeviceFactory.for_wire(unregistered) → DeviceNotFoundError")
    else:
        failures.append(
            "DeviceFactory.for_wire(unregistered) did not raise "
            "DeviceNotFoundError",
        )

    if failures:
        print()
        for line in failures:
            print(f"FAIL  {line}")
        print(f"\n{len(failures)} check(s) failed.")
        return 1
    print("\nAll factory-chain checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
