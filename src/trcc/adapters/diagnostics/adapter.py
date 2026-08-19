"""Concrete ``Diagnostics`` port — wraps the diagnostics functions.

The health/doctor/debug-report/gpu-reader logic already lives as module-level
functions that consume the ``Platform`` port.  This adapter binds them to one
platform instance and exposes them through the core ``Diagnostics`` ABC, so core
Commands + the quickstart service depend on the port, never on these modules.

The probe functions are called **module-qualified** (``_health.foo()``) rather
than imported by name so a test can ``monkeypatch.setattr`` the module attribute
and have the adapter see it — the attribute resolves at call time.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ...core.diagnostics import DoctorResult, GpuReaderState, HealthReport
from ...core.ports import Diagnostics, Platform
from ..sensors import nvml as _nvml
from . import debug_report as _debug_report
from . import doctor as _doctor
from . import health as _health

log = logging.getLogger(__name__)


class DiagnosticsAdapter(Diagnostics):
    """Platform-bound diagnostics, exposed through the core port."""

    def __init__(
        self, platform: Platform,
        gpu_state: _nvml.GpuStateFn = _nvml.nvml_init_state,
    ) -> None:
        self._platform = platform
        self._gpu_state = gpu_state
        log.info("DiagnosticsAdapter: platform=%s gpu_state=%s",
                 type(platform).__name__, getattr(gpu_state, "__name__", gpu_state))

    def health(self) -> HealthReport:
        log.info("DiagnosticsAdapter.health")
        return _health.run_health_checks(self._platform, self._gpu_state)

    def doctor(self) -> DoctorResult:
        log.info("DiagnosticsAdapter.doctor")
        return _doctor.run_doctor(self._platform)

    def render_doctor(self, report: HealthReport) -> str:
        log.info("DiagnosticsAdapter.render_doctor: %d check(s)", len(report.checks))
        return _doctor.render_doctor_output(report)

    def debug_report(self, log_tail_lines: int) -> str:
        log.info("DiagnosticsAdapter.debug_report: log_tail_lines=%d", log_tail_lines)
        return _debug_report.build_debug_report(
            self._platform, log_tail_lines=log_tail_lines,
        ).render_text()

    def write_debug_report(self, rendered: str, path: Path) -> Path:
        log.info("DiagnosticsAdapter.write_debug_report: path=%s", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return path

    def package_manager(self) -> str | None:
        """The OS's own answer; None when it has no manager of ours."""
        pm = self._platform.package_manager() if self._platform else None
        log.info("DiagnosticsAdapter.package_manager: %s", pm)
        return pm or None

    def gpu_reader_state(self) -> GpuReaderState:
        reader_installed, initialized, _ = self._gpu_state()
        present = _health.nvidia_gpu_present()
        log.info(
            "DiagnosticsAdapter.gpu_reader_state: present=%s installed=%s init=%s",
            present, reader_installed, initialized,
        )
        return GpuReaderState(
            nvidia_present=present,
            reader_installed=reader_installed,
            initialized=initialized,
        )
