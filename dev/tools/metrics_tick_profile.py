"""Where does ONE metrics tick's time go?  Measured inside the real GUI.

The refresh interval is the biggest single user-facing CPU lever on this app,
so the question is what one tick actually costs and which stage owns it.

**No figures are quoted in this docstring on purpose.**  They used to be, and
they went stale without anyone noticing: the header claimed ~5.8% of a core
per Hz and a 16.6 ms sysfs sweep long after ``c3c07256`` (every tick read the
sensors twice) and ``fa4805d0`` (board temps re-read per tick) had moved both.
A number with no commit and no box beside it is unfalsifiable prose, and this
file is an instrument -- it should PRODUCE numbers, not assert them.  Record
what you measure in ``memory/`` with the commit you measured at, and re-run
this rather than trusting a past run.

Guessing has already produced two wrong answers here: the GUI fan-out is
ALREADY visibility-gated (``trcc_app.py`` checks ``isVisible()`` per panel),
and per-frame logging was fixed without moving this number.

So: time every stage, in the shipping process, at the real cadence.

    PYTHONPATH=src python3.12 dev/tools/metrics_tick_profile.py           # tray
    PYTHONPATH=src python3.12 dev/tools/metrics_tick_profile.py --shown
    PYTHONPATH=src python3.12 dev/tools/metrics_tick_profile.py --selftest

**Run ``--selftest`` before trusting any number this prints.**  It checks the
clock against a known answer from outside the app -- that burning CPU is
counted and that SLEEPING is not.  That distinction is the whole instrument:
this tool timed WALL until ``5dddca87``, and wall time counts every moment a
thread sat descheduled, which made the sweep look like the biggest block when
it is not.

Needs a real device.  Prints a breakdown every REPORT_S to stdout and keeps
running; Ctrl-C or SIGTERM to stop.  Timings are wall time on the calling
thread -- the sweep runs on the poll thread and the publish on the loop
thread, so their totals are NOT additive with each other, only within a stage.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

# In-process on purpose: this measures the App's work in THIS process, and the
# production default would hand it a proxy to a daemon doing the work elsewhere.
# In-process measurement, whatever the shell exports: through the shared App
# this would time a different process.  ``main`` refuses while one runs.
os.environ["TRCC_DAEMON"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

REPORT_S = 20.0

_lock = threading.Lock()
_cpu: dict[str, float] = defaultdict(float)
_wall: dict[str, float] = defaultdict(float)
_count: dict[str, int] = defaultdict(int)


def _thread_cpu() -> float:
    """CPU time burned by THIS thread.

    ``perf_counter`` was the first cut and it is the wrong clock here: the
    sweep runs on the poll thread while the render loop composites at 15 fps,
    so wall time counts every moment the thread sat descheduled.  Measured the
    same sweep two ways -- 47-53 ms wall inside the GUI against 16.4 ms of
    actual CPU -- and the wall figure got quoted beside CPU%-derived numbers,
    which made the sweep look like the biggest block when it is not.
    """
    return time.clock_gettime(time.CLOCK_THREAD_CPUTIME_ID)


def _record(stage: str, cpu: float, wall: float) -> None:
    with _lock:
        _cpu[stage] += cpu
        _wall[stage] += wall
        _count[stage] += 1


def _timed(stage: str, fn):
    """Wrap *fn* so every call adds its CPU (and wall) time to *stage*."""
    def wrapper(*a, **k):
        c0, w0 = _thread_cpu(), time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            _record(stage, _thread_cpu() - c0, time.perf_counter() - w0)
    return wrapper


def _handler_name(handler) -> str:
    """A stable, readable name for a bus subscriber."""
    owner = getattr(handler, "__self__", None)
    if owner is not None:
        return f"{type(owner).__name__}.{getattr(handler, '__name__', '?')}"
    return getattr(handler, "__qualname__", repr(handler))[:58]


def install_probes() -> None:
    """Patch every stage of the tick.  Import-time order matters: patch the
    CLASS, not an instance, so the objects the composition root builds later
    are already wrapped."""
    from trcc.adapters.sensors import aggregator as agg
    from trcc.core import events as ev
    from trcc.core import ports
    from trcc.services import metrics_personalize as mp

    agg.BaselineSensors._poll_once = _timed(          # poll thread
        "sweep: BaselineSensors._poll_once", agg.BaselineSensors._poll_once)
    ports.SensorEnumerator.read_all = _timed(
        "read_all (cache)", ports.SensorEnumerator.read_all)
    ports.SensorEnumerator.snapshot = _timed(
        "snapshot (build DTO)", ports.SensorEnumerator.snapshot)
    mp.personalize_readings = _timed(
        "personalize_readings", mp.personalize_readings)
    mp.personalize_metrics = _timed(
        "personalize_metrics", mp.personalize_metrics)

    real_publish = ev.EventBus.publish

    def publish(self, event):                          # per-subscriber timing
        name = type(event).__name__
        if name != "SensorsUpdated":
            return real_publish(self, event)
        c0, w0 = _thread_cpu(), time.perf_counter()
        for handler in list(self._handlers[type(event)]):
            hc, hw = _thread_cpu(), time.perf_counter()
            try:
                handler(event)
            except Exception:
                logging.getLogger(__name__).exception("handler failed")
            _record(f"  subscriber: {_handler_name(handler)}",
                    _thread_cpu() - hc, time.perf_counter() - hw)
        _record("publish SensorsUpdated (all subscribers)",
                _thread_cpu() - c0, time.perf_counter() - w0)

    ev.EventBus.publish = publish

    # The bus forwarder emits a QUEUED Qt signal, so everything below runs on
    # the GUI thread AFTER publish() has returned -- outside the per-subscriber
    # timing, and therefore missing from the first version of this tool.
    from trcc.ui.gui import trcc_app as ta
    from trcc.ui.gui import uc_system_info as usi

    ta.TRCCApp._on_bus_sensors_updated = _timed(
        "GUI: TRCCApp._on_bus_sensors_updated",
        ta.TRCCApp._on_bus_sensors_updated)
    ta.TRCCApp._fan_out_metrics = _timed(
        "GUI:   _fan_out_metrics", ta.TRCCApp._fan_out_metrics)
    usi.UCSystemInfo.update_from_metrics = _timed(
        "GUI:     UCSystemInfo.update_from_metrics",
        usi.UCSystemInfo.update_from_metrics)


def report_forever() -> None:
    started = time.monotonic()
    while True:
        time.sleep(REPORT_S)
        elapsed = time.monotonic() - started
        with _lock:
            rows = sorted(_cpu.items(), key=lambda kv: -kv[1])
            counts, wall = dict(_count), dict(_wall)
        print(f"\n=== metrics tick breakdown after {elapsed:.0f}s ===", flush=True)
        print(f"{'stage':<46}{'calls':>6}{'cpu ms/call':>13}"
              f"{'wall ms/call':>14}{'cpu ms/s':>10}", flush=True)
        for stage, total in rows:
            n = max(counts[stage], 1)
            print(f"{stage:<46}{counts[stage]:>6}{1000*total/n:>13.2f}"
                  f"{1000*wall[stage]/n:>14.2f}{1000*total/elapsed:>10.2f}",
                  flush=True)


def _selftest() -> int:
    """A known answer from OUTSIDE the app, measured the SAME way as the app.

    Two properties, and the second is the one that matters.  A clock that
    counts BLOCKED time reports a thread's idle wait as cost, which is exactly
    how ``cProfile``'s ``tottime`` attributed 15.6 s to an ``ioctl`` that burns
    no CPU, and how this tool itself read 47-53 ms for a 16.4 ms sweep before
    ``5dddca87``.

      1. burning ~200 ms of CPU must READ as ~200 ms
      2. sleeping ~200 ms must read as ~0 ms

    If (2) fails, this is a wall clock wearing a CPU clock's name and no
    number this tool prints can be trusted.
    """
    burn_budget = 0.200

    t0 = _thread_cpu()
    end = time.perf_counter() + burn_budget
    x = 0
    while time.perf_counter() < end:          # burn, do not sleep
        x += 1
    burned = _thread_cpu() - t0

    t1 = _thread_cpu()
    time.sleep(burn_budget)                   # block, do not burn
    slept = _thread_cpu() - t1

    print(f"  burn {burn_budget * 1000:.0f} ms of CPU -> clock reports "
          f"{burned * 1000:6.1f} ms   (want ~{burn_budget * 1000:.0f})")
    print(f"  sleep {burn_budget * 1000:.0f} ms        -> clock reports "
          f"{slept * 1000:6.1f} ms   (want ~0)")

    ok = True
    if not 0.5 * burn_budget <= burned <= 1.5 * burn_budget:
        print("  FAIL: burned CPU is not being counted")
        ok = False
    if slept > 0.25 * burn_budget:
        print("  FAIL: sleep counted as cost — this is a WALL clock, and every "
              "number this tool prints is inflated by descheduled time")
        ok = False
    print("  selftest PASSED" if ok else "  selftest FAILED")
    return 0 if ok else 1


def _refuse_while_trcc_runs(what: str) -> None:
    """Exit if a TRCC App owns the panels -- see ``_mock_bootstrap``."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _mock_bootstrap import refuse_while_trcc_runs

    refuse_while_trcc_runs(what)


def main() -> int:
    install_probes()
    from trcc.adapters.infra.logging import configure_logging
    from trcc.adapters.system import current_platform
    from trcc.core.logs import levels_for
    from trcc.ui.gui import launch

    ladder = levels_for(0)                     # what a user runs: no -v
    configure_logging(current_platform().paths().log_file(),
                      level=ladder.file, stderr_level=ladder.terminal,
                      per_frame=ladder.per_frame)
    threading.Thread(target=report_forever, daemon=True).start()
    if "--selftest" in sys.argv:
        return _selftest()
    _refuse_while_trcc_runs("metrics_tick_profile")
    return launch(start_hidden="--shown" not in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
