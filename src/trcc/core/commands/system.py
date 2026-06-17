"""Sensors, diagnostics, setup, config, snapshots, update, slideshow, platform Commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from .._version import is_newer
from ..errors import (
    DeviceNotFoundError,
    HttpFetchError,
)
from ..events import (
    DateFormatChanged,
    GpuDeviceChanged,
    LanguageChanged,
    RefreshIntervalChanged,
    TempUnitChanged,
    TimeFormatChanged,
)
from ..models import MAX_REFRESH_INTERVAL_S, MIN_REFRESH_INTERVAL_S
from ..results import (
    AutostartResult,
    ControlCenterSnapshotResult,
    DateFormatResult,
    DebugReportPayload,
    DiskEntry,
    DisksListResult,
    DoctorResultPayload,
    FirstRunStatusResult,
    FontsListResult,
    GpuDeviceResult,
    GpuEntry,
    GpuReaderInstallResult,
    GpuReaderStatusResult,
    GpusListResult,
    HealthReportResult,
    KeepaliveResult,
    LanguageEntry,
    LanguageResult,
    LanguagesListResult,
    PlatformInfoResult,
    QuickstartResult,
    QuickstartStepEntry,
    RefreshIntervalResult,
    SensorInfoEntry,
    SensorsListResult,
    SensorsResult,
    SetupResult,
    SlideshowResult,
    TempUnitResult,
    TimeFormatResult,
    UpdateCheckResult,
    UpgradeResult,
)
from ._base import Command
from ._helpers import (
    _GPU_READER_INSTALL_COMMANDS,
    _UPGRADE_COMMANDS,
    _autostart_path,
    _health_entries,
    _slideshow_snapshot,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SetTimeFormat(Command[TimeFormatResult]):
    """Set the LCD-overlay clock format (12h or 24h).

    ``key=None`` (the default) sets the global ``AppSettings.time_format``
    and fans out to every device; a specific ``key`` sets just that
    device (per-device override).  Either way one
    :class:`TimeFormatChanged` is published per affected device so
    ``DeviceRenderObserver`` re-renders immediately.

    Distinct from :class:`SetClockFormat` (LED-segment LC2-style
    displays, which write ``led_clock_24h``).
    """
    fmt: str   # "12h" or "24h"
    key: str | None = None

    def execute(self, app: App) -> TimeFormatResult:
        log.info("SetTimeFormat.execute: fmt=%s key=%s", self.fmt, self.key)
        if self.fmt not in ("12h", "24h"):
            log.warning("SetTimeFormat.execute: invalid fmt %r", self.fmt)
            return TimeFormatResult(
                ok=False, key=self.key or "", fmt=self.fmt,
                message=f"fmt must be '12h' or '24h', got {self.fmt!r}",
            )
        if self.key is None:
            keys = app.settings.set_global_time_format(self.fmt)  # type: ignore[arg-type]
            scope = f"global ({len(keys)} device(s))"
        else:
            app.settings.set_time_format(self.key, self.fmt)  # type: ignore[arg-type]
            keys = [self.key]
            scope = self.key
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(TimeFormatChanged(key=key, fmt=self.fmt))
        return TimeFormatResult(
            ok=True, key=self.key or "", fmt=self.fmt,
            message=f"time format set to {self.fmt} for {scope}",
        )

@dataclass(frozen=True, slots=True)
class SetDateFormat(Command[DateFormatResult]):
    """Set the LCD-overlay date pattern.

    ``key=None`` (the default) sets the global default + fans out to
    every device; a specific ``key`` sets just that device.  Pattern
    uses ICU-ish tokens (``yyyy/MM/dd``, ``dd.MM.yyyy``) translated by
    ``_clock._translate_date_pattern`` to a Python strftime string.
    """
    fmt: str
    key: str | None = None

    def execute(self, app: App) -> DateFormatResult:
        log.info("SetDateFormat.execute: fmt=%r key=%s", self.fmt, self.key)
        if not self.fmt:
            log.warning("SetDateFormat.execute: empty fmt")
            return DateFormatResult(
                ok=False, key=self.key or "", fmt=self.fmt,
                message="fmt must not be empty",
            )
        if self.key is None:
            keys = app.settings.set_global_date_format(self.fmt)
            scope = f"global ({len(keys)} device(s))"
        else:
            app.settings.set_date_format(self.key, self.fmt)
            keys = [self.key]
            scope = self.key
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(DateFormatChanged(key=key, fmt=self.fmt))
        return DateFormatResult(
            ok=True, key=self.key or "", fmt=self.fmt,
            message=f"date format set to {self.fmt!r} for {scope}",
        )

@dataclass(frozen=True, slots=True)
class ListFonts(Command[FontsListResult]):
    """List font families Qt can find on the system.

    Uses ``QFontDatabase.families()`` — same source the GUI uses for
    its font picker.  Returns an empty list (not an error) when Qt
    isn't installed, so headless callers can probe safely.

    Headless callers (CLI / API / tests) reach this with no
    ``QGuiApplication`` instance.  ``QFontDatabase.families()``
    segfaults inside ``libQt6Gui`` when called before the GUI
    application initialises the font subsystem — bypass that by
    bringing up an offscreen ``QGuiApplication`` first, idempotently.
    """

    def execute(self, app: App) -> FontsListResult:
        del app
        try:
            from PySide6.QtGui import (  # type: ignore[import-not-found]
                QFontDatabase,
                QGuiApplication,
            )
        except ImportError:
            return FontsListResult(
                ok=True, fonts=[],
                message="Qt not available — no fonts enumerable",
            )

        # libQt6Gui's font subsystem needs a QGuiApplication to be
        # alive; without one, ``QFontDatabase.families()`` aborts the
        # process (no Python exception to catch).  Spin one up offscreen
        # if the caller didn't.  Idempotent — re-creating would raise.
        if QGuiApplication.instance() is None:
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            try:
                QGuiApplication([])
            except RuntimeError as e:
                return FontsListResult(
                    ok=False, fonts=[],
                    message=f"QGuiApplication init failed: {e}",
                )

        try:
            fonts = sorted(QFontDatabase.families())
        except RuntimeError as e:
            return FontsListResult(
                ok=False, fonts=[],
                message=f"QFontDatabase error: {e}",
            )
        return FontsListResult(
            ok=True, fonts=fonts,
            message=f"{len(fonts)} font(s)",
        )

@dataclass(frozen=True, slots=True)
class ListDisks(Command[DisksListResult]):
    """Enumerate disks via psutil — used by SetDiskIndex callers."""

    def execute(self, app: App) -> DisksListResult:
        del app
        disks: list[DiskEntry] = []
        try:
            import psutil  # type: ignore[import-untyped]
        except ImportError:
            return DisksListResult(
                ok=True, disks=[],
                message="psutil not available — no disk enumeration",
            )
        try:
            partitions = psutil.disk_partitions(all=False)
        except (OSError, RuntimeError) as e:
            return DisksListResult(
                ok=False, disks=[],
                message=f"disk enumeration failed: {e}",
            )
        for index, p in enumerate(partitions):
            disks.append(DiskEntry(
                index=index, device=p.device, mountpoint=p.mountpoint,
            ))
        return DisksListResult(
            ok=True, disks=disks,
            message=f"{len(disks)} disk(s)",
        )

@dataclass(frozen=True, slots=True)
class ListGpus(Command[GpusListResult]):
    """Enumerate every GPU the sensors aggregator exposes."""

    def execute(self, app: App) -> GpusListResult:
        sensors = app.platform.sensors()
        # `gpus()` is the SensorEnumerator port method — every enumerator
        # implements it and returns ``list[GpuSource]`` (empty if none).
        # The old `getattr(sensors, "_gpus") or getattr(sensors, "gpus")`
        # grabbed the bound METHOD as the fallback whenever `_gpus` was
        # empty, then tried to iterate it → "'method' object is not
        # iterable" crash on any machine with no GpuSource discovered.
        gpus = [
            GpuEntry(key=g.key, name=g.name, is_discrete=g.is_discrete)
            for g in sensors.gpus()
        ]
        return GpusListResult(
            ok=True, gpus=gpus,
            message=f"{len(gpus)} GPU(s) detected",
        )

@dataclass(frozen=True, slots=True)
class ControlCenterSnapshot(Command[ControlCenterSnapshotResult]):
    """App-wide settings snapshot.

    Polled by UIs to refresh state — logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG

    def execute(self, app: App) -> ControlCenterSnapshotResult:
        a = app.settings.app
        return ControlCenterSnapshotResult(
            ok=True,
            language=a.language,
            temp_unit=a.temp_unit,
            active_device=a.active_device,
            active_gpu=a.active_gpu,
            refresh_interval_s=a.refresh_interval_s,
            hdd_enabled=a.hdd_enabled,
            message="App settings snapshot",
        )

@dataclass(frozen=True, slots=True)
class ReadSensors(Command[SensorsResult]):
    """Return current sensor readings — personalized to user prefs.

    Pulls descriptor metadata (label / unit / category) from
    ``discover()`` and fresh values from ``read_all()``, applies user
    prefs through :func:`metrics_personalize.personalize_readings`,
    then merges so every returned ``SensorReading`` carries the
    personalized value.

    Same conversion + filter path as ``MetricsLoop._publish_once`` —
    one-shot callers (CLI / API / tests / GUI view-switch one-shot)
    receive the same shape the periodic broadcast carries.  When the
    user disables HDD, ``disk:*`` readings are excluded entirely
    (matches legacy's ``_populated.discard`` semantics); when the
    user picks °F, temp readings carry °F values AND ``unit="°F"``
    (so callers that key off ``.unit`` don't have to know temp_unit
    separately).

    Polled per refresh tick — logged at DEBUG so a default INFO run
    isn't drowned.
    """

    LOG_LEVEL: ClassVar[int] = logging.DEBUG

    def execute(self, app: App) -> SensorsResult:
        from ...services.metrics_personalize import (
            personalize_metrics,
            personalize_readings,
        )
        from ..models import SensorReading

        enum = app.platform.sensors()
        descriptors = enum.discover()
        raw = enum.read_all()
        s = app.settings.app
        personalized = personalize_readings(
            raw,
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Typed snapshot from the SAME enumerator, personalized the same
        # way — one-shot callers (GUI view-switch) get the typed object
        # the periodic broadcast carries, so periodic + one-shot agree.
        metrics = personalize_metrics(
            enum.snapshot(),
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Filter descriptors to only those that survived
        # personalization (HDD-disable drops disk:* keys entirely so
        # callers don't see them at value=0).  Temperature unit
        # override: if the sensor is a temp and the user picked °F,
        # the value is already in °F — adjust .unit so callers that
        # render unit suffixes don't mislabel.
        readings: list[SensorReading] = []
        is_fahrenheit = s.temp_unit == "F"
        for d in descriptors:
            if d.sensor_id not in personalized:
                continue
            unit = d.unit
            if is_fahrenheit and d.sensor_id.endswith(":temp") and unit == "°C":
                unit = "°F"
            readings.append(SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=personalized[d.sensor_id],
                unit=unit,
                label=d.label,
            ))
        return SensorsResult(
            ok=True,
            message=f"{len(readings)} sensor(s)",
            readings=readings,
            metrics=metrics,
        )

@dataclass(frozen=True, slots=True)
class ListSensors(Command[SensorsListResult]):
    """Enumerate every sensor the platform knows — descriptors only.

    Distinct from :class:`ReadSensors` which carries fresh values.
    Use this for sensor-picker dropdowns / API discovery endpoints
    that only need ``(sensor_id, category, unit, label)`` and don't
    want to pay the polling cost.  No personalisation: returns the
    raw sensor identity regardless of user prefs (hdd_enabled,
    temp_unit) — those affect *values*, not which sensors exist.
    """

    def execute(self, app: App) -> SensorsListResult:
        log.info("ListSensors.execute")
        descriptors = app.platform.sensors().discover()
        entries = [
            SensorInfoEntry(
                sensor_id=d.sensor_id,
                category=d.category,
                unit=d.unit,
                label=d.label,
            )
            for d in descriptors
        ]
        return SensorsListResult(
            ok=True,
            sensors=entries,
            message=f"{len(entries)} sensor(s) registered",
        )

@dataclass(frozen=True, slots=True)
class RunSetup(Command[SetupResult]):
    """OS-specific one-time setup (udev, WinUSB guide, etc.)."""
    interactive: bool = True

    def execute(self, app: App) -> SetupResult:
        # Check permissions AFTER setup so the warnings reflect what setup just
        # installed — not the pre-install state.  Snapshotting before setup
        # made a first run report "udev rules not installed" right next to
        # "exit code 0" (legacy's run_setup checks-then-installs, never reports
        # a pre-install warning as a result).
        code = app.platform.setup(interactive=self.interactive)
        warnings = app.platform.check_permissions()
        return SetupResult(
            ok=code == 0,
            message=f"Setup completed with exit code {code}",
            exit_code=code,
            warnings=warnings,
        )

@dataclass(frozen=True, slots=True)
class RunHealthCheck(Command[HealthReportResult]):
    """Run the full health check suite and return the structured report.

    Cheap (every check times out fast) — safe to call from a GUI panel
    on a refresh button.
    """

    def execute(self, app: App) -> HealthReportResult:
        from ...adapters.diagnostics.health import run_health_checks
        report = run_health_checks(app.platform)
        return HealthReportResult(
            ok=report.fail_count == 0,
            checks=_health_entries(report.checks),
            fail_count=report.fail_count,
            warn_count=report.warn_count,
            worst_severity=report.worst_severity,
            message=(f"{report.fail_count} fail / {report.warn_count} warn"
                     f" / {len(report.checks)} checks"),
        )

@dataclass(frozen=True, slots=True)
class RunDoctor(Command[DoctorResultPayload]):
    """Run health checks + render a CLI-friendly summary + exit code."""

    def execute(self, app: App) -> DoctorResultPayload:
        from ...adapters.diagnostics.doctor import (
            render_doctor_output,
            run_doctor,
        )
        doctor = run_doctor(app.platform)
        return DoctorResultPayload(
            ok=doctor.is_healthy,
            checks=_health_entries(doctor.report.checks),
            fail_count=doctor.report.fail_count,
            warn_count=doctor.report.warn_count,
            exit_code=doctor.exit_code,
            rendered=render_doctor_output(doctor.report),
            message=("Healthy" if doctor.is_healthy
                     else f"{doctor.report.fail_count} check(s) failed"),
        )

@dataclass(frozen=True, slots=True)
class GenerateDebugReport(Command[DebugReportPayload]):
    """Build a debug report bundle for the user to paste into a GitHub issue.

    With ``output_path`` set, the bundle lands on disk at that path and
    the rendered text comes back in the result.  With no output_path,
    the bundle is rendered into memory only — useful for the API to
    return the text body directly.
    """
    output_path: Path | None = None
    log_tail_lines: int = 1000

    def execute(self, app: App) -> DebugReportPayload:
        from ...adapters.diagnostics.debug_report import (
            build_debug_report,
            write_debug_report,
        )
        report = build_debug_report(
            app.platform, log_tail_lines=self.log_tail_lines,
        )
        rendered = report.render_text()
        out: str = ""
        if self.output_path is not None:
            try:
                written = write_debug_report(report, self.output_path)
            except OSError as e:
                return DebugReportPayload(
                    ok=False, output_path=str(self.output_path),
                    rendered_text=rendered,
                    message=f"Generated report but write failed: {e}",
                )
            out = str(written)
        return DebugReportPayload(
            ok=True, output_path=out, rendered_text=rendered,
            message=(f"Wrote debug report to {out}" if out
                     else "Generated debug report (in-memory)"),
        )

@dataclass(frozen=True, slots=True)
class RunQuickstart(Command[QuickstartResult]):
    """Walk the new-user happy path: doctor → scan.

    Each step's outcome is returned as a structured entry so any UI
    renders the same sequence.  Stops at the first FAIL.  Doesn't
    attempt a hardware handshake on its own — callers decide whether
    to ``ConnectDevice`` on the first-found device based on user
    confirmation.
    """

    def execute(self, app: App) -> QuickstartResult:
        report = app.quickstart.run_all()
        steps = [
            QuickstartStepEntry(
                name=s.name, status=s.status,
                message=s.message, next_step_hint=s.next_step_hint,
            )
            for s in report.steps
        ]
        return QuickstartResult(
            ok=report.completed_ok or not report.failed_step,
            steps=steps,
            completed_ok=report.completed_ok,
            device_key=report.device_key_connected,
            message=(
                "Quickstart complete." if report.completed_ok
                else (
                    f"Quickstart stopped at: {report.failed_step.name}"
                    if report.failed_step
                    else "Quickstart finished with warnings."
                )
            ),
        )

@dataclass(frozen=True, slots=True)
class GetFirstRunStatus(Command[FirstRunStatusResult]):
    """Has trcc finished onboarding on this machine?

    GUI uses this on launch to decide whether to surface a welcome
    screen; CLI users see it via ``trcc system first-run-status``.
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        return FirstRunStatusResult(
            ok=True,
            is_first_run=app.first_run.is_first_run(),
            marker_path=str(app.first_run.marker_path),
            message=(
                "Welcome — looks like this is your first run."
                if app.first_run.is_first_run()
                else "Setup already completed previously."
            ),
        )

@dataclass(frozen=True, slots=True)
class MarkFirstRunDone(Command[FirstRunStatusResult]):
    """Tell next/ the onboarding flow has been completed.

    GUI calls this after the welcome panel; CLI users almost never
    need to call it directly (the doctor / setup commands could choose
    to mark it, but we keep that intentional rather than implicit).
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        app.first_run.mark_completed()
        return FirstRunStatusResult(
            ok=True, is_first_run=False,
            marker_path=str(app.first_run.marker_path),
            message="First-run marker written.",
        )

@dataclass(frozen=True, slots=True)
class CheckForUpdate(Command[UpdateCheckResult]):
    """Ask GitHub Releases whether a newer trcc-linux is available.

    Network call — uses the App's shared HttpFetcher.  Comparison is
    coarse semver (X.Y.Z) tolerant of v-prefix and pre-release suffixes.
    """

    def execute(self, app: App) -> UpdateCheckResult:
        from ... import __version__ as next_version_module

        local = getattr(next_version_module, "__version__", "0.0.0")
        try:
            latest = app.github_releases.latest()
        except HttpFetchError as e:
            return UpdateCheckResult(
                ok=False, local_version=local,
                message=f"Update check failed: {e}",
            )
        available = is_newer(latest.version, local)
        msg = (
            f"Update available: {latest.tag} (you have {local})"
            if available
            else f"Up to date at {local}"
        )
        return UpdateCheckResult(
            ok=True,
            local_version=local,
            latest_version=latest.version,
            latest_tag=latest.tag,
            release_url=latest.html_url,
            update_available=available,
            message=msg,
        )

@dataclass(frozen=True, slots=True)
class RunUpgrade(Command[UpgradeResult]):
    """Run the OS package-manager upgrade for trcc-linux.

    Maps the detected package manager to the right command and spawns a
    subprocess.  We never pipe untrusted input — the argv is a fixed
    list per package manager.  ``dry_run=True`` returns the command
    without executing it so UIs can show the user what would run.
    """
    dry_run: bool = False

    def execute(self, app: App) -> UpgradeResult:
        del app
        import subprocess

        from ...adapters.diagnostics.health import detect_package_manager

        pm = detect_package_manager()
        if pm is None:
            return UpgradeResult(
                ok=False, package_manager="",
                message="No supported package manager detected on this system",
            )
        cmd = _UPGRADE_COMMANDS.get(pm)
        if cmd is None:
            return UpgradeResult(
                ok=False, package_manager=pm,
                message=f"No upgrade recipe for package manager {pm!r}",
            )
        if self.dry_run:
            return UpgradeResult(
                ok=True, package_manager=pm, command=list(cmd),
                message=f"Would run: {' '.join(cmd)}",
            )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=600.0, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            return UpgradeResult(
                ok=False, package_manager=pm, command=list(cmd),
                message=f"Upgrade subprocess failed: {type(e).__name__}: {e}",
            )
        return UpgradeResult(
            ok=proc.returncode == 0,
            package_manager=pm,
            command=list(cmd),
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            message=(f"Upgrade completed (exit {proc.returncode})"
                     if proc.returncode == 0
                     else f"Upgrade failed (exit {proc.returncode})"),
        )

@dataclass(frozen=True, slots=True)
class GetGpuReaderStatus(Command[GpuReaderStatusResult]):
    """Report whether the NVIDIA sensor reader (pynvml) should be offered for install.

    The GUI dispatches this at startup: if ``offer_install`` is True (an
    NVIDIA GPU is present but pynvml isn't installed) it surfaces a consent
    prompt to install it.  ``init_failed`` (reader present, ``nvmlInit``
    failed) is the version-mismatch/reboot case — NOT installable, so not
    offered (the doctor's WARN already guides the user to reboot/reload).
    Pure read; safe to call headless.
    """

    def execute(self, app: App) -> GpuReaderStatusResult:
        del app
        from ...adapters.diagnostics.health import nvidia_gpu_present
        from ...adapters.sensors.nvml import nvml_init_state
        reader_available, initialized, _ = nvml_init_state()
        present = nvidia_gpu_present()
        offer = present and not reader_available
        init_failed = present and reader_available and not initialized
        log.info(
            "GetGpuReaderStatus.execute: nvidia_present=%s reader=%s "
            "initialized=%s offer_install=%s",
            present, reader_available, initialized, offer,
        )
        return GpuReaderStatusResult(
            ok=True,
            nvidia_present=present,
            reader_installed=reader_available,
            init_failed=init_failed,
            offer_install=offer,
            message=(
                "NVIDIA GPU present, reader missing — install offered"
                if offer else
                "NVIDIA reader present"
                if reader_available else
                "No NVIDIA GPU detected"
            ),
        )


@dataclass(frozen=True, slots=True)
class InstallGpuReader(Command[GpuReaderInstallResult]):
    """Install the NVIDIA NVML python reader (pynvml) via the package manager.

    Mirrors :class:`RunUpgrade` — detect the PM, map to a fixed ``pkexec``
    argv (never interpolate input), run it.  GUI-triggered after explicit
    user consent.  pynvml was imported (and failed) at process start, so the
    newly-installed reader only takes effect on the next launch — the message
    says so rather than pretending it comes live.  ``dry_run=True`` returns
    the command without executing it so UIs/tests can preview.
    """
    dry_run: bool = False

    def execute(self, app: App) -> GpuReaderInstallResult:
        del app
        import subprocess

        from ...adapters.diagnostics.health import detect_package_manager

        log.info("InstallGpuReader.execute: dry_run=%s", self.dry_run)
        pm = detect_package_manager()
        if pm is None:
            log.warning("InstallGpuReader.execute: no package manager detected")
            return GpuReaderInstallResult(
                ok=False, message="No supported package manager detected on this system",
            )
        cmd = _GPU_READER_INSTALL_COMMANDS.get(pm)
        if cmd is None:
            log.warning("InstallGpuReader.execute: no recipe for pm=%r", pm)
            return GpuReaderInstallResult(
                ok=False, package_manager=pm,
                message=(f"No auto-install recipe for {pm!r} — install the NVML "
                         "python bindings (pynvml / nvidia-ml-py) manually"),
            )
        if self.dry_run:
            return GpuReaderInstallResult(
                ok=True, package_manager=pm, command=list(cmd),
                message=f"Would run: {' '.join(cmd)}",
            )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600.0, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            log.exception("InstallGpuReader.execute: subprocess failed")
            return GpuReaderInstallResult(
                ok=False, package_manager=pm, command=list(cmd),
                message=f"Install subprocess failed: {type(e).__name__}: {e}",
            )
        ok = proc.returncode == 0
        log.info("InstallGpuReader.execute: pm=%s exit=%d", pm, proc.returncode)
        return GpuReaderInstallResult(
            ok=ok, package_manager=pm, command=list(cmd), exit_code=proc.returncode,
            message=("GPU sensor reader installed — restart trcc to enable GPU metrics"
                     if ok else
                     f"Install failed (exit {proc.returncode})"),
        )


@dataclass(frozen=True, slots=True)
class ConfigureSlideshow(Command[SlideshowResult]):
    """Set the slideshow theme list + interval for a device.

    Either field can be omitted to leave it untouched.  Resets the
    SlideshowService cursor so the next tick picks up the new list
    from index 0.
    """
    key: str
    themes: tuple[str, ...] | None = None
    interval_s: float | None = None

    def execute(self, app: App) -> SlideshowResult:
        if self.interval_s is not None and self.interval_s < 1.0:
            return SlideshowResult(
                ok=False, key=self.key,
                message=(f"interval_s must be >= 1, got {self.interval_s}"),
            )
        app.settings.configure_slideshow(
            self.key,
            themes=list(self.themes) if self.themes is not None else None,
            interval_s=self.interval_s,
        )
        app.slideshow.reset(self.key)
        return _slideshow_snapshot(app.settings, self.key)

@dataclass(frozen=True, slots=True)
class SetSlideshow(Command[SlideshowResult]):
    """Toggle the slideshow on or off without changing the theme list."""
    key: str
    enabled: bool

    def execute(self, app: App) -> SlideshowResult:
        app.settings.set_slideshow_enabled(self.key, self.enabled)
        if self.enabled:
            app.slideshow.reset(self.key)
        return _slideshow_snapshot(app.settings, self.key)

@dataclass(frozen=True, slots=True)
class KeepAliveLoop(Command[KeepaliveResult]):
    """Confirm or hold a device's screen keepalive.

    Bulk/LY firmware reverts to the logo after ~2-3 s without a fresh frame.
    The per-device send worker now keepalive-resends the cached frame
    automatically (intrinsic, ~150 ms) for as long as the App lives, so this
    Command no longer runs the resend loop itself — it verifies a frame is
    cached + the device connected, then:

      * ``count >= 1`` — returns immediately ("keepalive active"); the worker
        is already doing the work.  (API / one-shot.)
      * ``count == 0`` — blocks until ``KeyboardInterrupt`` so a headless CLI
        process (and its worker) stays alive.  ``trcc display keepalive <key>``.

    ``interval_s`` now only sets the block-loop sleep granularity;
    ``metric_interval_s`` is accepted for API compatibility but unused — the
    metrics observer already re-renders on its own cadence.
    """
    key: str
    count: int = 0
    interval_s: float = 0.150
    metric_interval_s: float = 1.0

    def execute(self, app: App) -> KeepaliveResult:
        import time

        if self.count < 0:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"count must be >= 0, got {self.count}",
            )
        sender = app.senders.get(self.key)
        if sender is None or sender.last() is None:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=("No cached frame for keepalive — render at least "
                         "once first"),
            )
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return KeepaliveResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"{self.key} not connected — dispatch ConnectDevice first",
            )

        if self.count >= 1:
            return KeepaliveResult(
                ok=True, key=self.key,
                message="Keepalive active — the send worker resends the frame",
            )
        # Foreground CLI form: the worker keeps the screen alive; we just keep
        # the process (and thus the worker) running until Ctrl-C.
        try:
            while True:
                time.sleep(max(0.05, self.interval_s))
        except KeyboardInterrupt:
            return KeepaliveResult(
                ok=True, key=self.key, message="Keepalive stopped",
            )

@dataclass(frozen=True, slots=True)
class GetPlatformInfo(Command[PlatformInfoResult]):
    """Snapshot of OS identity + paths + permission warnings.

    Used by diagnostic UIs (`trcc info`, GUI about panel).  Keeps UIs
    from reaching directly into `app.platform` — they dispatch this and
    render the Result like any other Command.
    """

    def execute(self, app: App) -> PlatformInfoResult:
        p = app.platform
        paths = p.paths()
        return PlatformInfoResult(
            ok=True,
            message=f"Platform: {p.distro_name()}",
            distro_name=p.distro_name(),
            install_method=p.install_method(),
            config_dir=str(paths.config_dir()),
            data_dir=str(paths.data_dir()),
            user_content_dir=str(paths.user_content_dir()),
            log_file=str(paths.log_file()),
            permission_warnings=p.check_permissions(),
        )

@dataclass(frozen=True, slots=True)
class GetAutostartStatus(Command[AutostartResult]):
    """Report whether auto-launch-on-login is enabled."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        enabled = mgr.is_enabled()
        path = _autostart_path(app)
        return AutostartResult(
            ok=True,
            message="enabled" if enabled else "disabled",
            enabled=enabled, path=path,
        )

@dataclass(frozen=True, slots=True)
class EnableAutostart(Command[AutostartResult]):
    """Install the OS-specific autostart entry (per-user, no sudo)."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.enable()
        return AutostartResult(
            ok=True, message="autostart enabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )

@dataclass(frozen=True, slots=True)
class DisableAutostart(Command[AutostartResult]):
    """Remove the OS-specific autostart entry."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.disable()
        return AutostartResult(
            ok=True, message="autostart disabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )

@dataclass(frozen=True, slots=True)
class SetTempUnit(Command[TempUnitResult]):
    """Set the global temperature unit ("C" or "F") and propagate to every device.

    Cross-cutting setter — keeps AppSettings.temp_unit + every connected
    device's DeviceSettings.temp_unit in lockstep so overlay renderers
    see a consistent unit regardless of which device emits the next
    frame.
    """
    unit: str

    def execute(self, app: App) -> TempUnitResult:
        if self.unit not in ("C", "F"):
            return TempUnitResult(
                ok=False, unit=self.unit,
                message=f"unit must be 'C' or 'F', got {self.unit!r}",
            )
        app.settings.set_global_temp_unit(self.unit)   # type: ignore[arg-type]
        app.events.publish(TempUnitChanged(unit=self.unit))
        return TempUnitResult(
            ok=True, unit=self.unit,
            message=f"temp unit set to {self.unit}",
        )

@dataclass(frozen=True, slots=True)
class ListLanguages(Command[LanguagesListResult]):
    """Enumerate every language code the i18n table supports.

    Pure read — no I/O.  UIs use the returned list to populate language
    pickers; CLI users see it via ``trcc system list-languages``.
    """

    def execute(self, app: App) -> LanguagesListResult:
        del app
        from ..i18n import LANGUAGE_NAMES, TRANSLATIONS
        entries: list[LanguageEntry] = []
        for code in sorted(LANGUAGE_NAMES):
            entries.append(LanguageEntry(
                code=code,
                name=LANGUAGE_NAMES[code],
                translated_keys=len(TRANSLATIONS.get(code, {})),
            ))
        return LanguagesListResult(
            ok=True, languages=entries,
            message=f"{len(entries)} language(s) registered",
        )

@dataclass(frozen=True, slots=True)
class SetLanguage(Command[LanguageResult]):
    """Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr').

    Validates against the i18n table so unknown codes are rejected with a
    structured error instead of silently persisting and breaking
    ``tr()`` lookups for every subsequent string.
    """
    language: str

    def execute(self, app: App) -> LanguageResult:
        from ..i18n import LANGUAGE_NAMES
        lang = self.language.strip()
        if not lang:
            return LanguageResult(
                ok=False, language=self.language,
                message="language code cannot be empty",
            )
        if lang not in LANGUAGE_NAMES:
            return LanguageResult(
                ok=False, language=self.language,
                message=(f"unknown language code {lang!r}; "
                         "use `system list-languages` to see supported codes"),
            )
        app.settings.set_language(lang)
        app.events.publish(LanguageChanged(language=lang))
        return LanguageResult(
            ok=True, language=lang,
            message=f"language set to {lang} ({LANGUAGE_NAMES[lang]})",
        )

@dataclass(frozen=True, slots=True)
class SetGpuDevice(Command[GpuDeviceResult]):
    """Pick the primary GPU by sensor key (e.g. 'nvidia:0', 'amd:0').

    Pass an empty string to clear the override and let
    ``SensorEnumerator.primary_gpu()`` pick automatically.
    """
    gpu_key: str

    def execute(self, app: App) -> GpuDeviceResult:
        normalized: str | None = self.gpu_key.strip() or None
        app.settings.set_active_gpu(normalized)
        app.events.publish(GpuDeviceChanged(gpu_key=normalized))
        return GpuDeviceResult(
            ok=True, gpu_key=normalized,
            message=(f"active gpu set to {normalized}" if normalized
                     else "active gpu cleared (auto)"),
        )

@dataclass(frozen=True, slots=True)
class SetRefreshInterval(Command[RefreshIntervalResult]):
    """Set the global metrics-refresh interval — when metric data is polled
    and updated (the render reads the cached snapshot between polls).

    Validated to the GUI's data-refresh-rate range
    [``MIN_REFRESH_INTERVAL_S``, ``MAX_REFRESH_INTERVAL_S``] = [1, 100] s —
    below the minimum starves the CPU (sub-second polls + sends), above the
    maximum makes the metrics feel frozen.
    """
    seconds: float

    def execute(self, app: App) -> RefreshIntervalResult:
        log.info("SetRefreshInterval.execute: seconds=%.2f", self.seconds)
        if not MIN_REFRESH_INTERVAL_S <= self.seconds <= MAX_REFRESH_INTERVAL_S:
            log.warning(
                "SetRefreshInterval.execute: out-of-range %.2f rejected "
                "(allowed [%.1f, %.1f])", self.seconds,
                MIN_REFRESH_INTERVAL_S, MAX_REFRESH_INTERVAL_S,
            )
            return RefreshIntervalResult(
                ok=False, seconds=self.seconds,
                message=(f"refresh interval must be in "
                         f"[{MIN_REFRESH_INTERVAL_S}, {MAX_REFRESH_INTERVAL_S}] "
                         f"seconds, got {self.seconds}"),
            )
        old = app.settings.app.refresh_interval_s
        app.settings.set_refresh_interval(self.seconds)
        app.events.publish(RefreshIntervalChanged(seconds=self.seconds))
        log.info(
            "SetRefreshInterval.execute: settings.app.refresh_interval_s "
            "%.2f -> %.2f (RefreshIntervalChanged published)",
            old, self.seconds,
        )
        return RefreshIntervalResult(
            ok=True, seconds=self.seconds,
            message=f"refresh interval set to {self.seconds:.2f}s",
        )
