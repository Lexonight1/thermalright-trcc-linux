#!/usr/bin/env python3
"""Smoke: per-device keepalive — volatile Bulk resends, SCSI stays flat.

Drives the real App + send workers on scripted USB (no hardware): connect a
Bulk (854x480, volatile) and a SCSI (320x320, non-volatile) device, push one
solid-colour frame to each through the rerouted ``app.send`` path, then idle ~1 s
and count wire writes.  The Bulk worker must keepalive-resend the cached frame
(~150 ms cadence); the SCSI worker must not write again.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_keepalive.py
Gate: prints ``PASS`` and exits 0.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import DEV_TRCC  # also puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform

_BULK = "87ad:70db"   # scripted 854x480 — volatile
_SCSI = "0402:3922"   # 320x320 — non-volatile
_SPECS = [
    {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5},
    {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100},
]
_IDLE_S = 1.0


def _write_count(device: Any) -> int:
    """Number of wire writes recorded by a device's fake transport."""
    transport = device._transport
    if hasattr(transport, "writes"):
        return len(transport.writes)        # bulk
    return len(transport.sent)              # scsi


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import logging

    from trcc.adapters.infra.logging import configure_logging
    platform = MockPlatform(_SPECS, DEV_TRCC)
    configure_logging(platform.paths().log_file(), level=logging.INFO)

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from trcc._boot import trcc
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, SendColor

    app = cast(App, trcc(platform=cast(Any, platform), renderer=QtRenderer()))
    try:
        for key in (_BULK, _SCSI):
            assert app.dispatch(ConnectDevice(key=key)).ok, f"connect {key}"
            vid, pid = (int(p, 16) for p in key.split(":"))
            app.dispatch(SendColor(key=key, r=10, g=20, b=30))

        bulk, scsi = app.devices[_BULK], app.devices[_SCSI]
        bulk_before, scsi_before = _write_count(bulk), _write_count(scsi)
        time.sleep(_IDLE_S)
        bulk_after, scsi_after = _write_count(bulk), _write_count(scsi)
    finally:
        app.close()

    bulk_growth = bulk_after - bulk_before
    scsi_growth = scsi_after - scsi_before
    print(f"Bulk (volatile):     {bulk_before} -> {bulk_after}  (+{bulk_growth})")
    print(f"SCSI (non-volatile): {scsi_before} -> {scsi_after}  (+{scsi_growth})")

    # ~1 s idle at 150 ms cadence ⇒ several Bulk resends; SCSI must stay flat.
    ok = bulk_growth >= 3 and scsi_growth == 0
    if ok:
        print("PASS — Bulk keepalive resends; SCSI silent")
        return 0
    print("FAIL — expected Bulk to resend (>=3) and SCSI flat (0)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
