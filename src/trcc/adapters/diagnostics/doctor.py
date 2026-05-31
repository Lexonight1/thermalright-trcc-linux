"""Doctor — run health checks, print colored output, exit non-zero on FAIL.

CLI-friendly counterpart to ``DebugReport`` — the doctor *advises* the
user interactively while the report *bundles* state for paste-in.  Both
sit on top of the same ``HealthCheckResult`` data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ...core.ports import Platform
from .health import HealthReport, run_health_checks

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """What the doctor decided.

    ``exit_code`` is 0 when nothing FAILed, 1 otherwise — wraps the
    standard "broken / not broken" CLI convention so scripts can branch.
    """
    report: HealthReport
    exit_code: int

    @property
    def is_healthy(self) -> bool:
        return self.exit_code == 0


def run_doctor(platform: Platform) -> DoctorResult:
    """Run every health check; map to an exit code."""
    log.info("run_doctor: invoking health checks via %s", type(platform).__name__)
    report = run_health_checks(platform)
    code = 1 if report.fail_count else 0
    log.info("run_doctor: exit_code=%d (%d fail / %d warn)",
             code, report.fail_count, report.warn_count)
    return DoctorResult(report=report, exit_code=code)


def render_doctor_output(report: HealthReport) -> str:
    """Plain-text rendering for CLI use (no ANSI — terminals diverge enough
    that we leave colorization to typer.style at the call site)."""
    log.info("render_doctor_output: checks=%d", len(report.checks))
    lines: list[str] = []
    for c in report.checks:
        lines.append(f"[{c.severity:4}] {c.name:22}  {c.message}")
        if c.fix_hint and c.severity != "OK":
            lines.append(f"        hint: {c.fix_hint}")
    summary = (
        f"\nResult: {report.fail_count} fail / "
        f"{report.warn_count} warn / "
        f"{len(report.checks)} checks total"
    )
    return "\n".join(lines) + summary
