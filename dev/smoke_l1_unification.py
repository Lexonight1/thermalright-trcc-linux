"""L1 broadcast unification smoke — verifies the 2026-05-14 fix.

The original Windows-VM bug: the GUI sensors panel showed live psutil
readings (``psutil:cpu_percent = 2%``) while the LCD overlay, GUI preview,
and LED engine all rendered blanks for the same metrics tick.  Same
``enumerator.read_all()`` cache underneath, three pollers above, two
diverging payload shapes (raw dict vs typed DTO).

The L1 fix: ``HardwareMetrics`` now carries BOTH

  * a ``readings: dict[str, float]`` — raw values keyed by sensor id, for
    the GUI sensors panel's dict-iterating consumer
  * the typed fields (``cpu_temp``, ``gpu_temp``, …) + a ``_populated`` set —
    for LCD overlay, LED engine, GUI preview's typed consumers

Every observer reads the SAME record from a single ``Topic.METRICS``
broadcast.  No more "GUI sees data but LCD doesn't".

This smoke asserts BOTH paths are populated on the current OS.  Designed
to run on the Win 11 VM (where the bug first surfaced) but works on any
OS as a regression canary.

Run:
    PYTHONPATH=src python dev/smoke_l1_unification.py
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

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


def check_readings_dict_populated(trcc_fn) -> CheckResult:
    """GUI sensors panel path — ``metrics.readings`` has raw sensor entries."""
    try:
        m = trcc_fn().os.metrics
        if not m.readings:
            return CheckResult(
                "readings dict (GUI panel path)", False,
                "metrics.readings is empty — GUI sensors panel would show "
                "no values")
        return CheckResult(
            "readings dict (GUI panel path)", True,
            f"{len(m.readings)} raw sensor entries")
    except Exception as e:
        return CheckResult("readings dict (GUI panel path)", False,
                           f"{type(e).__name__}: {e}")


def check_populated_set(trcc_fn) -> CheckResult:
    """LCD overlay path — ``metrics._populated`` lists typed fields with data."""
    try:
        m = trcc_fn().os.metrics
        if not m._populated:
            return CheckResult(
                "_populated set (LCD overlay path)", False,
                "metrics._populated is empty — LCD overlay would render blanks")
        return CheckResult(
            "_populated set (LCD overlay path)", True,
            f"{len(m._populated)} typed fields populated")
    except Exception as e:
        return CheckResult("_populated set (LCD overlay path)", False,
                           f"{type(e).__name__}: {e}")


def check_typed_fields_match_populated(trcc_fn) -> CheckResult:
    """Every name in ``_populated`` corresponds to a non-default typed field."""
    try:
        m = trcc_fn().os.metrics
        empty_typed = [name for name in m._populated
                       if hasattr(m, name) and getattr(m, name) == 0]
        # cpu_temp/gpu_temp == 0 is allowed if the sensor genuinely
        # reports 0; only flag if EVERY populated field is zero (which
        # would indicate the typed-field path is broken).
        if empty_typed == sorted(m._populated):
            return CheckResult(
                "typed fields ↔ _populated", False,
                f"all {len(m._populated)} populated fields are 0 — "
                "typed-field write path broken")
        return CheckResult(
            "typed fields ↔ _populated", True,
            f"{len(m._populated) - len(empty_typed)}/{len(m._populated)} fields non-zero")
    except Exception as e:
        return CheckResult("typed fields ↔ _populated", False,
                           f"{type(e).__name__}: {e}")


def check_broadcast_carries_both(trcc_fn) -> CheckResult:
    """``Topic.METRICS`` broadcast delivers a record with BOTH paths populated."""
    from trcc.legacy.core.events import Topic
    try:
        t = trcc_fn()
        captured: list = []

        def _capture(payload, *_a, **_kw):
            captured.append(payload)

        sub = t.events.subscribe(Topic.METRICS, _capture)
        try:
            t.wake_metrics_loop()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not captured:
                time.sleep(0.1)
        finally:
            t.events.unsubscribe(sub)
        if not captured:
            return CheckResult("Topic.METRICS broadcast", False,
                               "no broadcast within 5s")
        broadcast = captured[-1]
        has_readings = bool(getattr(broadcast, 'readings', None))
        has_populated = bool(getattr(broadcast, '_populated', None))
        if not (has_readings and has_populated):
            return CheckResult(
                "Topic.METRICS broadcast", False,
                f"readings={has_readings} _populated={has_populated} — "
                "L1 unification regression")
        return CheckResult(
            "Topic.METRICS broadcast", True,
            f"both paths present: readings={len(broadcast.readings)} "
            f"_populated={len(broadcast._populated)}")
    except Exception as e:
        return CheckResult("Topic.METRICS broadcast", False,
                           f"{type(e).__name__}: {e}")


_CHECKS = (
    check_readings_dict_populated,
    check_populated_set,
    check_typed_fields_match_populated,
    check_broadcast_carries_both,
)


def main() -> int:
    print("\n  TRCC L1 Broadcast Unification Smoke")
    print("  ─────────────────────────────────────")
    print("  Verifies the 2026-05-14 fix: GUI panel + LCD overlay + LED")
    print("  engine all observe the SAME HardwareMetrics record.\n")
    print(f"  Platform: {sys.platform}\n")

    from trcc.legacy._boot import trcc as _trcc

    failed = 0
    for check in _CHECKS:
        r = check(_trcc)
        print(r)
        failed += not r.passed
    print()
    if failed:
        print(f"  {failed}/{len(_CHECKS)} check(s) failed — L1 regression on this OS")
        return 1
    print(f"  All {len(_CHECKS)} checks passed — L1 unification holds on {sys.platform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
