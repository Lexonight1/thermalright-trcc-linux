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

Adding a check: define a function returning ``HealthCheckResult`` and add
it to the list in ``run_health_checks``.  The doctor + report bundlers both
go through that function, so registration = inclusion.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from ...core.diagnostics import HealthCheckResult, HealthReport, Severity
from ...core.ports import Paths, Platform
from ..sensors.nvml import NVML_RELOAD_HINT, nvml_init_state
from .install import collect_install_info

log = logging.getLogger(__name__)

# DTOs moved to ``core.diagnostics`` (pure data the Diagnostics port speaks);
# re-exported here so existing ``from ...health import HealthReport`` keeps working.
__all__ = [
    "HealthCheckResult",
    "HealthReport",
    "Severity",
    "detect_package_manager",
    "nvidia_gpu_present",
    "package_install_hint",
    "quick_subprocess",
    "run_health_checks",
]


# =========================================================================
# Individual checks
# =========================================================================


def check_install_integrity() -> HealthCheckResult:
    """Is the running trcc the one the user thinks they installed?

    Two failures both present to the user as "I upgraded and nothing
    changed", and neither is visible from the outside:

    * stale bytecode — Python serves a cached .pyc whose recorded mtime+size
      still match a since-edited source (FAIL: every other diagnostic, and
      the version itself, is then untrustworthy);
    * duplicate binaries on PATH — the upgrade landed on one, the other keeps
      running (WARN: legitimate for venv/pipx users, so not fatal).
    """
    log.info("check_install_integrity: called")
    try:
        info = collect_install_info()
    except Exception as e:
        log.exception("check_install_integrity: collection failed: %s", e)
        return HealthCheckResult(
            name="install-integrity", severity="WARN",
            message=f"Could not inspect the install: {e}",
        )
    if info.bytecode_stale:
        return HealthCheckResult(
            name="install-integrity", severity="FAIL",
            message=(
                f"Stale bytecode — running {info.version} but "
                f"{info.module_path} says {info.source_version}"
            ),
            fix_hint=(
                "Python is serving a cached .pyc that no longer matches the "
                "source. Delete the __pycache__ directories under "
                f"{info.module_path}, or reinstall trcc-linux."
            ),
        )
    if info.duplicates:
        found = ", ".join(str(e.path) for e in info.executables)
        return HealthCheckResult(
            name="install-integrity", severity="WARN",
            message=f"{len(info.executables)} trcc on PATH: {found}",
            fix_hint=(
                f"'{info.executables[0].path}' is the one that runs. An "
                "upgrade applied to the other will appear to do nothing. "
                "Remove whichever you don't want."
            ),
        )
    return HealthCheckResult(
        name="install-integrity", severity="OK",
        message=f"{info.version} via {info.installer} ({info.interpreter})",
    )


def check_python_version(platform: Platform) -> HealthCheckResult:
    """Python ≥ 3.11 is the project minimum (match-statement + slots)."""
    log.info("check_python_version: called")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return HealthCheckResult(
            name="python-version", severity="OK",
            message=f"Python {major}.{minor}",
        )
    return HealthCheckResult(
        name="python-version", severity="FAIL",
        message=f"Python {major}.{minor} is below the 3.11 minimum",
        fix_hint=platform.software_install_hint("python"),
    )


def check_log_writable(paths: Paths) -> HealthCheckResult:
    """The log file's parent dir must be writable for diagnostics to land."""
    log.info("check_log_writable: called")
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
    log.info("check_config_writable: called")
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
    log.info("check_devices_visible: called")
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
            fix_hint=platform.no_devices_hint(),
        )
    return HealthCheckResult(
        name="devices-visible", severity="OK",
        message=f"{len(devices)} device(s) detected",
    )


def check_sensors_enumerable(platform: Platform) -> HealthCheckResult:
    """At least one sensor (CPU temp / RAM) should be readable."""
    log.info("check_sensors_enumerable: called")
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


def nvidia_gpu_present() -> bool:
    """Cheap probe: is a discrete NVIDIA (PCI vendor ``0x10de``) GPU present?

    Reads ``/sys/bus/pci/devices/*/vendor`` — no subprocess, and works even
    when the driver / pynvml are absent.  That's the point: detect the card
    independently of the reader so we can advise *installing* the reader.
    Non-Linux falls back to the driver proc node.
    """
    if sys.platform != "linux":
        return Path("/proc/driver/nvidia/version").exists()
    pci_root = Path("/sys/bus/pci/devices")
    if not pci_root.is_dir():
        return False
    for dev in pci_root.iterdir():
        try:
            if dev.joinpath("vendor").read_text().strip().lower() != "0x10de":
                continue
            # PCI class 0x03xxxx == display controller (skip non-GPU NVIDIA
            # functions like the bundled HDMI-audio device).
            if dev.joinpath("class").read_text().strip().lower().startswith("0x03"):
                return True
        except OSError:
            continue
    return False


def check_gpu_sensors(platform: Platform) -> HealthCheckResult:
    """NVIDIA GPU present but no metrics → guide the user to the fix.

    The foolproof-install check: a discrete NVIDIA card with empty GPU
    readings is almost always (a) the pynvml reader not installed, or (b) an
    NVML version mismatch after a driver update without reboot.  Both are
    actionable, so this WARNs (not FAILs) with a distro-specific hint.  No
    NVIDIA card → OK (AMD reads via hwmon, no extra package needed).
    """
    log.info("check_gpu_sensors: called")
    reader_available, initialized, last_error = nvml_init_state()
    if initialized:
        return HealthCheckResult(
            name="gpu-sensors", severity="OK",
            message="NVIDIA GPU sensors available (NVML initialized)",
        )
    if not nvidia_gpu_present():
        return HealthCheckResult(
            name="gpu-sensors", severity="OK",
            message="No discrete NVIDIA GPU detected (NVML check not applicable)",
        )
    if not reader_available:
        return HealthCheckResult(
            name="gpu-sensors", severity="WARN",
            message="NVIDIA GPU detected but the pynvml reader is not installed "
                    "— GPU metrics will be empty",
            fix_hint=platform.software_install_hint("pynvml")
                     + " (the trcc-linux package should pull this in)",
        )
    return HealthCheckResult(
        name="gpu-sensors", severity="WARN",
        message=f"NVIDIA GPU detected but NVML init failed: {last_error}",
        fix_hint=NVML_RELOAD_HINT,
    )


def check_ffmpeg_present(platform: Platform) -> HealthCheckResult:
    """ffmpeg is required for video themes; absence is WARN (still usable
    for image-only themes)."""
    log.info("check_ffmpeg_present: called")
    if shutil.which("ffmpeg"):
        return HealthCheckResult(
            name="ffmpeg", severity="OK",
            message="ffmpeg on PATH (video themes will decode)",
        )
    return HealthCheckResult(
        name="ffmpeg", severity="WARN",
        message="ffmpeg not on PATH",
        fix_hint=platform.software_install_hint("ffmpeg")
                 + " — image themes still work without it",
    )


def check_qt_importable() -> HealthCheckResult:
    """PySide6 is required for the GUI; absence is WARN if headless."""
    log.info("check_qt_importable: called")
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
    log.info("check_udev_rules_linux: called")
    if sys.platform != "linux":
        return HealthCheckResult(
            name="udev-rules", severity="OK",
            message="Not applicable on this OS",
        )
    candidate_paths = [
        Path("/etc/udev/rules.d/99-trcc-lcd.rules"),
        Path("/etc/udev/rules.d/99-trcc.rules"),
        Path("/etc/udev/rules.d/90-trcc.rules"),
        Path("/lib/udev/rules.d/99-trcc-lcd.rules"),
        Path("/lib/udev/rules.d/99-trcc.rules"),
        Path("/usr/lib/udev/rules.d/99-trcc-lcd.rules"),
        Path("/usr/lib/udev/rules.d/99-trcc.rules"),
    ]
    found = [p for p in candidate_paths if p.is_file()]
    if not found:
        return HealthCheckResult(
            name="udev-rules", severity="WARN",
            message="No TRCC udev rules found under /etc/udev/rules.d/",
            fix_hint="Run `trcc system setup` (or install via the distro "
                     "package) to lay down /etc/udev/rules.d/99-trcc-lcd.rules",
        )
    return HealthCheckResult(
        name="udev-rules", severity="OK",
        message=f"udev rules installed: {found[0]}",
    )


def check_seven_zip_present(platform: Platform) -> HealthCheckResult:
    """``7z`` is required for theme-pack extraction.  Absence is WARN —
    user can still install themes via tarballs or the cloud catalog."""
    log.info("check_seven_zip_present: called")
    if shutil.which("7z"):
        return HealthCheckResult(
            name="7z", severity="OK",
            message="7z on PATH (theme-pack extraction available)",
        )
    return HealthCheckResult(
        name="7z", severity="WARN",
        message="7z not on PATH",
        fix_hint=platform.software_install_hint("7z"),
    )


# =========================================================================
# Aggregator
# =========================================================================


def run_health_checks(platform: Platform) -> HealthReport:
    """Run every registered check; collect results into a HealthReport."""
    log.info("run_health_checks: starting")
    paths = platform.paths()
    checks: list[HealthCheckResult] = [
        # First: if this isn't the trcc they think it is, nothing below means
        # anything.
        check_install_integrity(),
        check_python_version(platform),
        check_log_writable(paths),
        check_config_writable(paths),
        check_devices_visible(platform),
        check_sensors_enumerable(platform),
        check_gpu_sensors(platform),
        check_ffmpeg_present(platform),
        check_qt_importable(),
        check_udev_rules_linux(),
        check_seven_zip_present(platform),
    ]
    for c in checks:
        log.info("  %s [%s]: %s", c.name, c.severity, c.message)
    report = HealthReport(checks=checks)
    log.info(
        "run_health_checks: done — worst=%s fail=%d warn=%d",
        report.worst_severity, report.fail_count, report.warn_count,
    )
    return report


def detect_package_manager() -> str | None:
    """Return the first available package manager name, or None.

    Lightweight version of legacy ``_detect_pkg_manager``.  Used by
    install-hint generation when a check FAILs and we want to suggest a
    distro-specific install command.
    """
    log.info("detect_package_manager: called")
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
    log.info("package_install_hint: package=%s", package)
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
    log.debug("quick_subprocess: cmd=%s timeout=%s", cmd, timeout_s)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout.strip()
