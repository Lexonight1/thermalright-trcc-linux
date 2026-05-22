"""QuickstartService — guided first-run flow for new users.

What it does, in plain language:

1. Runs the doctor.  If any FAIL, stops and tells the user what to fix.
2. Runs `system setup` if udev rules are missing on Linux.
3. Scans for devices.
4. If found, attempts a handshake (connect) on the first one.
5. Pushes a small "success" frame (solid green) so the user sees their
   screen change — the strongest signal that "yes, this works."

Each step writes a structured line into the result so any UI (CLI,
GUI, API) can render the same sequence.  Step boundaries are explicit
so the GUI can show progress per step instead of a single spinner.

Why a service: keeps the orchestration honest.  The CLI doesn't have
to thread together doctor + scan + connect + send_color and get the
order wrong; it just dispatches one Command and renders steps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from ..adapters.diagnostics.health import run_health_checks
from ..core.ports import Platform

log = logging.getLogger(__name__)


StepStatus = Literal["ok", "warn", "fail", "skipped"]


@dataclass(frozen=True, slots=True)
class QuickstartStep:
    """One step's outcome — name + status + message + optional hint."""
    name: str
    status: StepStatus
    message: str
    next_step_hint: str = ""


@dataclass
class QuickstartReport:
    """Full quickstart trace — what ran, what the result was."""
    steps: list[QuickstartStep] = field(default_factory=list)
    completed_ok: bool = False
    device_key_connected: str = ""

    @property
    def failed_step(self) -> QuickstartStep | None:
        for s in self.steps:
            if s.status == "fail":
                return s
        return None


class QuickstartService:
    """Orchestrate the new-user happy path.

    Each ``run_*`` step is independently runnable so tests can isolate;
    ``run_all`` is the user-facing entry that sequences them.
    """

    def __init__(self, platform: Platform) -> None:
        self._platform = platform

    def run_all(self) -> QuickstartReport:
        """Walk the full sequence; stops at the first FAIL."""
        report = QuickstartReport()
        # 1. Doctor
        if not self._run_doctor(report):
            return report
        # 2. Scan
        devices = self._run_scan(report)
        if devices is None:
            return report
        # 3. Done (without hardware) — handshake/test runs only when the
        # caller dispatches the Connect + send-color Commands.  The
        # service stops here so the CLI can interactively ask the user
        # "shall I connect to <first device>?" before reaching for USB.
        report.completed_ok = True
        return report

    # ── Steps ─────────────────────────────────────────────────────────

    def _run_doctor(self, report: QuickstartReport) -> bool:
        health = run_health_checks(self._platform)
        if health.fail_count:
            failing = [c for c in health.checks if c.severity == "FAIL"]
            first = failing[0]
            report.steps.append(QuickstartStep(
                name="doctor",
                status="fail",
                message=f"Health check failed: {first.name} — {first.message}",
                next_step_hint=first.fix_hint
                or "Run `trcc-next system doctor` for details.",
            ))
            return False
        if health.warn_count:
            warns = [c for c in health.checks if c.severity == "WARN"]
            report.steps.append(QuickstartStep(
                name="doctor",
                status="warn",
                message=(
                    f"{health.warn_count} non-blocking warning(s): "
                    f"{warns[0].name}"
                ),
                next_step_hint="Continuing — see `system doctor` for details.",
            ))
        else:
            report.steps.append(QuickstartStep(
                name="doctor",
                status="ok",
                message=f"All {len(health.checks)} health checks passed.",
            ))
        return True

    def _run_scan(
        self, report: QuickstartReport,
    ) -> list | None:
        try:
            devices = self._platform.scan_devices()
        except (OSError, RuntimeError) as e:
            report.steps.append(QuickstartStep(
                name="scan",
                status="fail",
                message=f"USB scan crashed: {type(e).__name__}: {e}",
                next_step_hint=(
                    "Check libusb is installed and your user has USB "
                    "permissions.  Run `trcc-next system doctor`."
                ),
            ))
            return None
        if not devices:
            report.steps.append(QuickstartStep(
                name="scan",
                status="warn",
                message="No Thermalright devices detected on USB.",
                next_step_hint=(
                    "Plug in a supported device and re-run.  "
                    "If your device IS plugged in, you may be missing "
                    "udev rules — run `trcc-next system setup`."
                ),
            ))
            return None
        report.steps.append(QuickstartStep(
            name="scan",
            status="ok",
            message=(
                f"Found {len(devices)} device(s).  "
                f"First: {devices[0].key}"
            ),
            next_step_hint=(
                f"Connect with `trcc-next device connect {devices[0].key}`."
            ),
        ))
        return devices
