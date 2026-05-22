"""Health checks — quick probes that surface common reporter issues.

Each check returns a ``HealthCheckResult`` so callers (Doctor command,
GUI about panel, GitHub-issue debug bundle) can render them uniformly.
Checks are intentionally cheap (no subprocess that takes >1s); deeper
diagnostics live in legacy's interactive ``device_debug`` flows which
next/ doesn't port — those are GUI features, not CLI-on-headless ones.

Severity ladder:
  * ``OK`` — everything in scope works.
  * ``WARN`` — works on this machine but reporter should know.
  * ``FAIL`` — feature won't work until this is fixed.

Adding a check: define a function returning ``HealthCheckResult`` and
add it to ``ALL_CHECKS``.  The doctor + report bundlers iterate that
tuple, so registration = inclusion.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ...core.ports import Paths, Platform

log = logging.getLogger(__name__)


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
    """The full set of checks plus a one-line summary."""
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


# =========================================================================
# Individual checks
# =========================================================================


def check_python_version() -> HealthCheckResult:
    """Python ≥ 3.11 is the project minimum (match-statement + slots)."""
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return HealthCheckResult(
            name="python-version", severity="OK",
            message=f"Python {major}.{minor}",
        )
    return HealthCheckResult(
        name="python-version", severity="FAIL",
        message=f"Python {major}.{minor} is below the 3.11 minimum",
        fix_hint="Install Python 3.11+ via your distro package manager",
    )


def check_log_writable(paths: Paths) -> HealthCheckResult:
    """The log file's parent dir must be writable for diagnostics to land."""
    log_path = paths.log_file()
    parent = log_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".trcc_health_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return HealthCheckResult(
            name="log-writable", severity="FAIL",
            message=f"Cannot write to {parent}: {e}",
            fix_hint=f"chmod the directory or pick a different log path: {parent}",
        )
    return HealthCheckResult(
        name="log-writable", severity="OK",
        message=f"Log directory writable: {parent}",
    )


def check_config_writable(paths: Paths) -> HealthCheckResult:
    config_dir = paths.config_dir()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        probe = config_dir / ".trcc_health_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return HealthCheckResult(
            name="config-writable", severity="FAIL",
            message=f"Cannot write to {config_dir}: {e}",
            fix_hint=f"chmod the directory or pick a different config path: {config_dir}",
        )
    return HealthCheckResult(
        name="config-writable", severity="OK",
        message=f"Config directory writable: {config_dir}",
    )


def check_devices_visible(platform: Platform) -> HealthCheckResult:
    """A device-less scan is WARN not FAIL — the user might not have plugged in yet."""
    try:
        devices = platform.scan_devices()
    except (OSError, RuntimeError) as e:
        return HealthCheckResult(
            name="devices-visible", severity="FAIL",
            message=f"USB scan raised {type(e).__name__}: {e}",
            fix_hint="Check libusb is installed and the user has USB permissions",
        )
    if not devices:
        return HealthCheckResult(
            name="devices-visible", severity="WARN",
            message="No Thermalright devices detected on USB",
            fix_hint=("Plug in a supported device and re-run; check udev "
                      "rules with `trcc system doctor` if the device is "
                      "physically attached"),
        )
    return HealthCheckResult(
        name="devices-visible", severity="OK",
        message=f"{len(devices)} device(s) detected",
    )


def check_sensors_enumerable(platform: Platform) -> HealthCheckResult:
    """At least one sensor (CPU temp / RAM) should be readable."""
    try:
        descriptors = platform.sensors().discover()
    except (OSError, RuntimeError) as e:
        return HealthCheckResult(
            name="sensors-enumerable", severity="FAIL",
            message=f"Sensor enumeration raised {type(e).__name__}: {e}",
            fix_hint="Check psutil / hwmon dependencies are installed",
        )
    if not descriptors:
        return HealthCheckResult(
            name="sensors-enumerable", severity="WARN",
            message="No sensors discovered (overlay metrics will be empty)",
            fix_hint="Install psutil; on Linux also enable hwmon/lm-sensors",
        )
    return HealthCheckResult(
        name="sensors-enumerable", severity="OK",
        message=f"{len(descriptors)} sensor descriptor(s) available",
    )


def check_ffmpeg_present() -> HealthCheckResult:
    """ffmpeg is required for video themes; absence is WARN (still usable
    for image-only themes)."""
    if shutil.which("ffmpeg"):
        return HealthCheckResult(
            name="ffmpeg", severity="OK",
            message="ffmpeg on PATH (video themes will decode)",
        )
    return HealthCheckResult(
        name="ffmpeg", severity="WARN",
        message="ffmpeg not on PATH",
        fix_hint="Install ffmpeg via your distro package manager — "
                 "image themes still work without it",
    )


def check_qt_importable() -> HealthCheckResult:
    """PySide6 is required for the GUI; absence is WARN if headless."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return HealthCheckResult(
            name="pyside6", severity="WARN",
            message="PySide6 not installed (GUI mode unavailable)",
            fix_hint="pip install PySide6 — only needed for `trcc gui`",
        )
    return HealthCheckResult(
        name="pyside6", severity="OK",
        message="PySide6 importable",
    )


def check_udev_rules_linux() -> HealthCheckResult:
    """Linux-only: look for installed udev rules under /etc/udev/rules.d/."""
    if sys.platform != "linux":
        return HealthCheckResult(
            name="udev-rules", severity="OK",
            message="Not applicable on this OS",
        )
    candidate_paths = [
        Path("/etc/udev/rules.d/99-trcc.rules"),
        Path("/etc/udev/rules.d/90-trcc.rules"),
        Path("/lib/udev/rules.d/99-trcc.rules"),
    ]
    found = [p for p in candidate_paths if p.is_file()]
    if not found:
        return HealthCheckResult(
            name="udev-rules", severity="WARN",
            message="No TRCC udev rules found under /etc/udev/rules.d/",
            fix_hint="Run `trcc system setup` (or install via the distro "
                     "package) to lay down /etc/udev/rules.d/99-trcc.rules",
        )
    return HealthCheckResult(
        name="udev-rules", severity="OK",
        message=f"udev rules installed: {found[0]}",
    )


def check_seven_zip_present() -> HealthCheckResult:
    """``7z`` is required for theme-pack extraction.  Absence is WARN —
    user can still install themes via tarballs or the cloud catalog."""
    if shutil.which("7z"):
        return HealthCheckResult(
            name="7z", severity="OK",
            message="7z on PATH (theme-pack extraction available)",
        )
    return HealthCheckResult(
        name="7z", severity="WARN",
        message="7z not on PATH",
        fix_hint="Install p7zip-full / p7zip / 7zip via your package manager",
    )


# =========================================================================
# Aggregator
# =========================================================================


def run_health_checks(platform: Platform) -> HealthReport:
    """Run every registered check; collect results into a HealthReport."""
    paths = platform.paths()
    checks: list[HealthCheckResult] = [
        check_python_version(),
        check_log_writable(paths),
        check_config_writable(paths),
        check_devices_visible(platform),
        check_sensors_enumerable(platform),
        check_ffmpeg_present(),
        check_qt_importable(),
        check_udev_rules_linux(),
        check_seven_zip_present(),
    ]
    return HealthReport(checks=checks)


def detect_package_manager() -> str | None:
    """Return the first available package manager name, or None.

    Lightweight version of legacy ``_detect_pkg_manager``.  Used by
    install-hint generation when a check FAILs and we want to suggest a
    distro-specific install command.
    """
    candidates = ["dnf", "apt", "pacman", "zypper", "xbps-install", "apk"]
    for binary in candidates:
        if shutil.which(binary):
            return binary
    return None


_PM_INSTALL_COMMANDS: dict[str, str] = {
    "dnf":          "sudo dnf install {pkg}",
    "apt":          "sudo apt install {pkg}",
    "pacman":       "sudo pacman -S {pkg}",
    "zypper":       "sudo zypper install {pkg}",
    "xbps-install": "sudo xbps-install {pkg}",
    "apk":          "sudo apk add {pkg}",
}


def package_install_hint(package: str) -> str:
    """One-line install hint for a missing package, distro-specific."""
    pm = detect_package_manager()
    template = _PM_INSTALL_COMMANDS.get(pm or "", "")
    if template:
        return template.format(pkg=package)
    return f"Install {package} via your package manager"


def quick_subprocess(cmd: list[str], timeout_s: float = 2.0) -> str:
    """Best-effort run a probe command and return its stdout.

    Returns empty string on any failure — health checks treat that as
    "not available" rather than propagating.  Used by checks that want
    to ask the system "tell me your version" without crashing the doctor.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout.strip()
