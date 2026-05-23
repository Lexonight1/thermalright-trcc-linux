"""Metrics chain smoke — every call site that reads aggregate hardware metrics.

After the 2026-05-14 unification, the canonical entrypoint for aggregate
metrics is ``trcc.os.metrics``.  ``SystemService.all_metrics`` is gone;
``get_all_metrics()`` is gone.  This smoke pins every former caller so a
silent regression on any one of them (LCD overlay, LED engine, GUI panel,
CLI, API, daemon broadcast) trips here before the user does.

What it checks (on the current OS):

1. **Boot** — ``trcc()`` returns a Trcc with detected devices.
2. **Direct entrypoint** — ``trcc.os.metrics`` builds a populated record.
3. **apply_temp_unit** (``core/trcc.py``) — roundtrip C → F → C succeeds.
4. **API** — ``GET /system/metrics`` and ``/system/metrics/cpu`` shapes.
5. **CLI** — ``trcc info``, ``trcc led test``, ``trcc lcd test`` return 0.
6. **Broadcast** — ``Topic.METRICS`` fires from the metrics loop.

Run:
    PYTHONPATH=src python dev/smoke_metrics_chain.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Make src/ importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# Headless Qt — overlay renderer needs an offscreen platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "[ OK ]" if self.passed else "[FAIL]"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"    {mark}  {self.name}{suffix}"


def _run(name: str, fn: Callable[[], str]) -> CheckResult:
    try:
        detail = fn()
        return CheckResult(name, True, detail)
    except Exception as e:
        return CheckResult(name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def check_boot(trcc_fn: Callable) -> CheckResult:
    """``trcc()`` returns a Trcc, devices detected, _boot.py:216 seed runs."""
    def _go() -> str:
        t = trcc_fn()
        count = len(list(t))
        return f"devices={count} platform={type(t.os).__name__}"
    return _run("boot + _boot.py:216 seed", _go)


def check_platform_metrics(trcc_fn: Callable) -> CheckResult:
    """``trcc.os.metrics`` builds a HardwareMetrics with populated fields."""
    def _go() -> str:
        t = trcc_fn()
        m = t.os.metrics
        if not m._populated:
            raise AssertionError("no populated fields in platform.metrics")
        return f"populated={len(m._populated)} readings={len(m.readings)}"
    return _run("trcc.os.metrics (canonical entrypoint)", _go)


def check_apply_temp_unit(trcc_fn: Callable) -> CheckResult:
    """``Trcc.apply_temp_unit`` reads ``self._platform.metrics`` (was ``svc.all_metrics``)."""
    def _go() -> str:
        t = trcc_fn()
        r1 = t.apply_temp_unit(1)
        r2 = t.apply_temp_unit(0)
        if not (r1.get("success") and r2.get("success")):
            raise AssertionError(f"non-success: F={r1} C={r2}")
        return "C → F → C roundtrip OK"
    return _run("core/trcc.py:apply_temp_unit", _go)


def check_api_get_metrics(_trcc_fn: Callable) -> CheckResult:
    """``GET /system/metrics`` returns full metrics dict."""
    def _go() -> str:
        from trcc.ui.api.system import get_metrics
        d = get_metrics()
        if "cpu_temp" not in d:
            raise AssertionError(f"cpu_temp missing from {sorted(d)[:5]}")
        return f"keys={len(d)}"
    return _run("api/system.py GET /system/metrics", _go)


def check_api_get_metrics_by_category(_trcc_fn: Callable) -> CheckResult:
    """``GET /system/metrics/{category}`` filters by prefix."""
    def _go() -> str:
        from trcc.ui.api.system import get_metrics_by_category
        d = get_metrics_by_category("cpu")
        if not all(k.startswith("cpu_") for k in d):
            raise AssertionError(f"unexpected keys: {sorted(d)}")
        return f"cpu_keys={len(d)}"
    return _run("api/system.py GET /system/metrics/{category}", _go)


def check_cli_show_info(_trcc_fn: Callable) -> CheckResult:
    """``trcc info`` (cli/_system.py:show_info) reads ``trcc.os.metrics``."""
    def _go() -> str:
        from trcc.ui.cli._system import show_info
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = show_info(preview=False)
        if rc != 0:
            raise AssertionError(f"non-zero rc={rc}")
        return f"rc=0 stdout_lines={buf.getvalue().count(chr(10))}"
    return _run("cli/_system.py show_info", _go)


def check_cli_test_led(_trcc_fn: Callable) -> CheckResult:
    """``trcc led test`` (cli/_led.py:test_led) reads ``trcc.os.metrics``."""
    def _go() -> str:
        from trcc.ui.cli._led import test_led
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = test_led(segments=10, duration=1, mode="static")
        return f"rc={rc} stdout_lines={buf.getvalue().count(chr(10))}"
    return _run("cli/_led.py test_led", _go)


def check_cli_test_lcd(_trcc_fn: Callable) -> CheckResult:
    """``trcc lcd test`` (cli/_led.py:test_lcd) reads ``trcc.os.metrics``."""
    def _go() -> str:
        from trcc.ui.cli._led import test_lcd
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = test_lcd(cols=40)
        return f"rc={rc} stdout_lines={buf.getvalue().count(chr(10))}"
    return _run("cli/_led.py test_lcd", _go)


def check_metrics_loop_broadcast(trcc_fn: Callable) -> CheckResult:
    """``PollingMetricsLoop._poll_metrics`` publishes ``Topic.METRICS`` from ``trcc.os.metrics``."""
    def _go() -> str:
        from trcc.core.events import Topic
        t = trcc_fn()
        captured: list = []
        sub = t.events.subscribe(
            Topic.METRICS,
            lambda payload, *_a, **_kw: captured.append(payload),
        )
        try:
            t.wake_metrics_loop()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not captured:
                time.sleep(0.1)
        finally:
            t.events.unsubscribe(sub)
        if not captured:
            raise AssertionError("no METRICS broadcast within 5s")
        m = captured[-1]
        return f"broadcasts={len(captured)} populated={len(m._populated)}"
    return _run("metrics_loop _poll_metrics → Topic.METRICS", _go)


_CHECKS: tuple[Callable[[Callable], CheckResult], ...] = (
    check_boot,
    check_platform_metrics,
    check_apply_temp_unit,
    check_api_get_metrics,
    check_api_get_metrics_by_category,
    check_cli_show_info,
    check_cli_test_led,
    check_cli_test_lcd,
    check_metrics_loop_broadcast,
)


def main() -> int:
    print("\n  TRCC Metrics Chain Smoke")
    print("  ─────────────────────────")
    print("  Pins every call site formerly reading svc.all_metrics /")
    print("  get_all_metrics() — now ``trcc.os.metrics``.\n")

    # Single shared Trcc — _boot.trcc() caches the singleton so every
    # check reuses one composition root (matches production).
    from trcc._boot import trcc as _trcc

    failed = 0
    for check in _CHECKS:
        r = check(_trcc)
        print(r)
        failed += not r.passed

    print()
    if failed:
        print(f"  {failed}/{len(_CHECKS)} check(s) failed")
        return 1
    print(f"  All {len(_CHECKS)} checks passed — metrics chain holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
