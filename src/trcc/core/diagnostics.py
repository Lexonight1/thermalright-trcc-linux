"""Diagnostics domain DTOs — pure data crossing the ``Diagnostics`` port.

These were defined in the diagnostics *adapters* (health/doctor), which forced
core Command bodies to import the adapter to name the return types.  They are
pure dataclasses (no I/O, no framework deps), so they belong in core — the
adapters now re-export them for backwards compatibility, and the ``Diagnostics``
port (``core/ports.py``) speaks them.

See [[project_architecture_boundary_gate]].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Severity ladder for a single health check.
Severity = Literal["OK", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """One check's outcome — name, severity, message, optional fix hint."""
    name: str
    severity: Severity
    message: str
    fix_hint: str = ""


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The full set of checks plus derived severity aggregates."""
    checks: list[HealthCheckResult] = field(default_factory=list)

    @property
    def worst_severity(self) -> Severity:
        if any(c.severity == "FAIL" for c in self.checks):
            return "FAIL"
        if any(c.severity == "WARN" for c in self.checks):
            return "WARN"
        return "OK"

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "WARN")


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """What the doctor decided.

    ``exit_code`` is 0 when nothing FAILed, 1 otherwise — wraps the standard
    "broken / not broken" CLI convention so scripts can branch.
    """
    report: HealthReport
    exit_code: int

    @property
    def is_healthy(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class GpuReaderState:
    """Whether the NVIDIA NVML reader (pynvml) is present and initialised.

    ``nvidia_present`` — an NVIDIA card was detected.  ``reader_installed`` —
    pynvml importable.  ``initialized`` — ``nvmlInit`` succeeded.  Surfaced by
    the doctor's GPU health check (reader present / needs a reboot) without core
    importing the sensor adapter.
    """
    nvidia_present: bool
    reader_installed: bool
    initialized: bool
