#!/usr/bin/env python3
"""Diagnose the live metrics path for a summoned mock device.

dev/mock_gui fakes ONLY the handshake; sensors/render/observe are the real app.
This summons one device deterministically (no flaky offscreen auto-click), then
drives the REAL observe slot — builds a SensorsUpdated exactly as MetricsLoop
does and calls ``window._on_bus_sensors_updated(event)`` DIRECTLY so any
exception propagates here instead of being swallowed by Qt's queued-slot
wrapper.  Prints, flushed, every hop: connect → handler → active → fan-out →
the resolved gauge values.  Answers "why do the device's M1-M6 show --".

    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/tools/diagnose_metrics.py
    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/tools/diagnose_metrics.py 0416:8001 3
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_DEV = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DEV))
from _mock_bootstrap import bootstrap


def _say(msg: str) -> None:
    print(msg, flush=True)


def _diagnose(window, vid: int, pid: int, pm: int, sub: int) -> None:
    from trcc.adapters.repo.http import HttpFetchError, UrllibHttpFetcher
    UrllibHttpFetcher.fetch = (  # cut net: theme downloads 404-with-retry, slow
        lambda self, url, *a, **k: (_ for _ in ()).throw(HttpFetchError("net off")))

    from trcc.core.commands import ConnectDevice
    from trcc.core.events import SensorsUpdated
    from trcc.core.protocol import pm_to_fbl
    from trcc.services.metrics_personalize import (
        personalize_metrics, personalize_readings)

    app = window._app
    key = f"{vid:04x}:{pid:04x}"
    fbl = pm_to_fbl(pm, sub)
    _say(f"\n— summon {key} pm={pm} sub={sub} fbl={fbl} —")
    app.platform.set_active_reply(vid, pid, pm=pm, sub=sub, fbl=fbl)
    window._remove_handler(key)
    r = app.dispatch(ConnectDevice(key=key))
    _say(f"connect ok={getattr(r, 'ok', None)}")
    device = app.devices.get(key)
    window._add_handler(device)
    window._active_key = ""
    _say("activating…")
    window._activate_device(key)
    h = window._handlers.get(key)
    _say(f"handler={type(h).__name__} active={getattr(h, 'active', '?')} "
         f"active_key={window._active_key!r}")

    s = app.settings.app
    raw = app.platform.sensors().read_all()
    readings = personalize_readings(raw, temp_unit=s.temp_unit,
                                    hdd_enabled=s.hdd_enabled)
    metrics = personalize_metrics(app.platform.sensors().snapshot(),
                                  temp_unit=s.temp_unit, hdd_enabled=s.hdd_enabled)
    _say(f"snapshot cpu_temp={metrics.cpu_temp} cpu%={metrics.cpu_percent:.0f} "
         f"gpu_temp={metrics.gpu_temp}")
    ev = SensorsUpdated(reading_count=len(readings), readings=readings,
                        temp_unit=s.temp_unit, metrics=metrics)
    _say("calling the live observe slot _on_bus_sensors_updated(event)…")
    try:
        window._on_bus_sensors_updated(ev)
        _say("OK — observe slot returned without raising")
    except Exception:
        _say("EXCEPTION in the live observe slot:")
        traceback.print_exc()
        return

    # Show the values the panel actually renders into the M1-M6 gauges — the
    # exact text the user sees (or `--`).  This is what `update_sensor_metrics`
    # pushes via `UCInfoImage.set_value`.
    from trcc.ui.presentation.led_metrics_format import format_sensor_gauges
    unit = "°F" if s.temp_unit == "F" else "°C"
    gauges = format_sensor_gauges(metrics, unit)
    _say("rendered M1-M6 gauge text: "
         + ", ".join(f"{k}={t}{u}" for k, (_v, t, u) in gauges.items()))


def main() -> None:
    vid_pid = sys.argv[1] if len(sys.argv) > 1 else "0416:8001"
    pm = int(sys.argv[2], 0) if len(sys.argv) > 2 else 3
    sub = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0
    vid, pid = (int(x, 16) for x in vid_pid.split(":"))

    platform = bootstrap(verbosity=0, all_devices=False,
                         specs=[{"vid": f"{vid:04x}", "pid": f"{pid:04x}", "pm": pm}])

    def on_ready(window) -> None:
        import os
        try:
            _diagnose(window, vid, pid, pm, sub)
        finally:
            # Hard exit: the result is printed + flushed; skip the GUI teardown
            # (native-lib shutdown can hang offscreen) — this is a one-shot probe.
            sys.stdout.flush()
            os._exit(0)

    from trcc.ui.gui import run_gui
    run_gui(platform, single_instance=False, ipc=False, force_exit=False,
            start_hidden=True, on_ready=on_ready)


if __name__ == "__main__":
    main()
