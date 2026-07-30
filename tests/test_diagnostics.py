"""Diagnostics — health checks, doctor, debug report bundle."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from trcc.adapters.diagnostics import health as health_mod
from trcc.adapters.diagnostics.debug_report import build_debug_report
from trcc.adapters.diagnostics.doctor import (
    render_doctor_output,
    run_doctor,
)
from trcc.adapters.diagnostics.health import (
    HealthCheckResult,
    check_gpu_sensors,
    check_log_writable,
    check_python_version,
    package_install_hint,
    run_health_checks,
)
from trcc.adapters.infra.logging import configure_logging, tail_log

# =========================================================================
# Logging adapter
# =========================================================================


def test_configure_logging_creates_writable_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "trcc.log"
    configure_logging(log_file)
    import logging
    logging.getLogger("trcc.test").warning("hello-from-test")
    assert log_file.is_file()
    body = log_file.read_text(encoding="utf-8")
    assert "hello-from-test" in body


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    """Calling configure_logging twice should not pile up handlers."""
    import logging
    log_file = tmp_path / "trcc.log"
    configure_logging(log_file)
    initial = len(logging.getLogger().handlers)
    configure_logging(log_file)
    after = len(logging.getLogger().handlers)
    assert initial == after


def test_latest_log_holds_only_the_current_run(tmp_path: Path) -> None:
    """``<stem>.latest.log`` must be truncated per run — the whole point of it.

    It was not, for a long time: ``RotatingFileHandler`` SILENTLY discards
    ``mode="w"`` when ``maxBytes > 0`` (CPython forces ``"a"``), so the
    per-run file quietly accumulated days of runs.  Reading a stale window as
    the current run caused repeated misdiagnoses — the file contained what you
    expected because an EARLIER run had written it.
    """
    import logging

    log_file = tmp_path / "trcc.log"
    latest = tmp_path / "trcc.latest.log"

    configure_logging(log_file)
    logging.getLogger("trcc.test").warning("run-one-marker")
    assert "run-one-marker" in latest.read_text(encoding="utf-8")

    # A second process/init: the previous run's lines must be GONE.
    configure_logging(log_file)
    logging.getLogger("trcc.test").warning("run-two-marker")

    body = latest.read_text(encoding="utf-8")
    assert "run-two-marker" in body
    assert "run-one-marker" not in body, (
        "latest.log still holds the previous run — it is append-only again, "
        "and any diagnosis reading it can land on a stale window"
    )
    # The cumulative history file keeps BOTH — that is its job.
    history = log_file.read_text(encoding="utf-8")
    assert "run-one-marker" in history and "run-two-marker" in history


def test_tail_log_handles_missing_file(tmp_path: Path) -> None:
    assert tail_log(tmp_path / "absent.log") == []


def test_tail_log_returns_last_n_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(500)))
    tail = tail_log(log_file, n_lines=10)
    assert len(tail) == 10
    assert tail[-1] == "line 499"


# =========================================================================
# Health checks
# =========================================================================


def test_python_version_check_passes_on_311_plus(fake_platform) -> None:
    result = check_python_version(fake_platform)
    assert result.severity == "OK"
    assert "Python" in result.message


def test_each_os_platform_answers_its_own_install_hint() -> None:
    """The cutover Linux-hardcoded these; now each OS answers via the ABC."""
    from trcc.adapters.system.bsd import BSDPlatform
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "winget" in WindowsPlatform().software_install_hint("ffmpeg")
    assert "brew" in MacOSPlatform().software_install_hint("ffmpeg")
    assert "pkg install" in BSDPlatform().software_install_hint("ffmpeg")
    # Unknown tool falls back to the generic ABC default, never crashes.
    assert "PATH" in WindowsPlatform().software_install_hint("nonesuch")


def test_each_os_platform_answers_its_own_no_devices_hint() -> None:
    from trcc.adapters.system.bsd import BSDPlatform
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "WinUSB" in WindowsPlatform().no_devices_hint()
    assert "macOS" in MacOSPlatform().no_devices_hint()
    assert "usbconfig" in BSDPlatform().no_devices_hint()
    # No Linux-isms (udev) leaking onto the non-Linux platforms.
    assert "udev" not in WindowsPlatform().no_devices_hint()
    assert "udev" not in MacOSPlatform().no_devices_hint()


def test_each_os_platform_answers_its_own_permission_denied_hint() -> None:
    """EACCES USB hint moved off a core sys.platform sniff onto the Platform port."""
    from trcc.adapters.system.bsd import BSDPlatform
    from trcc.adapters.system.linux import LinuxPlatform
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "udev" in LinuxPlatform().permission_denied_hint()
    assert "WinUSB" in WindowsPlatform().permission_denied_hint()
    macos_hint = MacOSPlatform().permission_denied_hint()
    assert "sudo" in macos_hint or "Privacy" in macos_hint
    assert BSDPlatform().permission_denied_hint()           # non-empty
    # No Linux-isms leaking onto the non-Linux platforms.
    assert "udev" not in WindowsPlatform().permission_denied_hint()
    assert "udev" not in MacOSPlatform().permission_denied_hint()


def test_log_writable_check_passes_on_tmp_dir(
    fake_platform, tmp_home: Path,
) -> None:
    del tmp_home
    paths = fake_platform.paths()
    result = check_log_writable(paths)
    assert result.severity == "OK"


def test_run_health_checks_returns_full_report(fake_platform) -> None:
    report = run_health_checks(fake_platform)
    names = {c.name for c in report.checks}
    # Sanity — every registered check shows up
    expected = {
        "python-version", "log-writable", "config-writable",
        "devices-visible", "sensors-enumerable", "gpu-sensors", "ffmpeg",
        "pyside6", "udev-rules", "7z",
    }
    assert expected <= names


def test_health_report_aggregates_severities() -> None:
    """The HealthReport.worst_severity ladder is FAIL > WARN > OK."""
    from trcc.adapters.diagnostics.health import HealthReport

    a = HealthCheckResult(name="a", severity="OK", message="")
    b = HealthCheckResult(name="b", severity="WARN", message="")
    c = HealthCheckResult(name="c", severity="FAIL", message="")
    assert HealthReport(checks=[a]).worst_severity == "OK"
    assert HealthReport(checks=[a, b]).worst_severity == "WARN"
    assert HealthReport(checks=[a, b, c]).worst_severity == "FAIL"


def test_gpu_check_ok_when_nvml_initialized(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "nvml_init_state", lambda: (True, True, None))
    result = check_gpu_sensors(fake_platform)
    assert result.severity == "OK"
    assert "NVML initialized" in result.message


def test_gpu_check_ok_when_no_nvidia_card(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "nvml_init_state", lambda: (False, False, None))
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: False)
    result = check_gpu_sensors(fake_platform)
    assert result.severity == "OK"
    assert "No discrete NVIDIA GPU" in result.message


def test_gpu_check_warns_when_reader_missing(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "nvml_init_state", lambda: (False, False, None))
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    result = check_gpu_sensors(fake_platform)
    assert result.severity == "WARN"
    assert "pynvml reader is not installed" in result.message
    # Hint now comes from the DI'd platform's software_install_hint("pynvml")
    # rather than a Linux-hardcoded package name.
    assert "pynvml" in result.fix_hint


def test_gpu_check_warns_with_reload_hint_on_init_failure(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = "NVMLError_LibRmVersionMismatch: RM has detected an NVML/RM version mismatch"
    monkeypatch.setattr(health_mod, "nvml_init_state", lambda: (True, False, err))
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    result = check_gpu_sensors(fake_platform)
    assert result.severity == "WARN"
    assert err in result.message
    assert "modprobe" in result.fix_hint


def test_package_install_hint_returns_a_string() -> None:
    """Hint never crashes — even when no package manager is detected,
    it returns the generic 'install via your package manager' fallback."""
    hint = package_install_hint("ffmpeg")
    assert isinstance(hint, str)
    assert "ffmpeg" in hint


# =========================================================================
# Doctor
# =========================================================================


def test_run_doctor_returns_exit_code(fake_platform) -> None:
    result = run_doctor(fake_platform)
    # FakePlatform yields no devices → WARN, not FAIL, so exit_code == 0.
    assert result.exit_code in (0, 1)


def test_render_doctor_output_includes_summary(fake_platform) -> None:
    result = run_doctor(fake_platform)
    rendered = render_doctor_output(result.report)
    assert "checks total" in rendered


# =========================================================================
# Debug report bundle
# =========================================================================


def test_build_debug_report_returns_filled_struct(fake_platform) -> None:
    report = build_debug_report(fake_platform)
    assert report.timestamp
    assert "distro" in report.platform_info
    assert "config_dir" in report.paths
    # FakePlatform has no devices → empty list, but no scan error.
    assert report.devices_error == ""
    assert isinstance(report.devices, list)


def test_debug_report_renders_paste_ready_text(fake_platform) -> None:
    report = build_debug_report(fake_platform)
    text = report.render_text()
    # Markers a reporter / triager visually scans for.
    for header in ("Platform", "Paths", "Devices", "Sensors", "Health", "Log tail"):
        assert f"## {header}" in text


def test_debug_report_writes_to_disk(fake_platform, tmp_path: Path) -> None:
    from trcc.adapters.diagnostics.debug_report import write_debug_report

    report = build_debug_report(fake_platform)
    out = tmp_path / "debug.txt"
    written = write_debug_report(report, out)
    assert written == out
    body = out.read_text(encoding="utf-8")
    assert "Paths" in body


def test_debug_report_captures_live_handshake(tmp_path: Path) -> None:
    """A connected LCD device's exact PM / SUB / fbl / resolution / raw bytes
    are captured live — the byte the report previously couldn't produce because
    the connect-time log line scrolls out of the tail (the #176/#186 blocker)."""
    from tests.mock_platform import MockPlatform

    # GrandVision 360 (bulk, registry fbl 72) with a pinned PM=50 handshake.
    platform = MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 50}], tmp_path)
    report = build_debug_report(platform)

    assert len(report.devices) == 1
    dev = report.devices[0]
    assert dev["key"] == "87ad:70db"
    assert dev["hs_pm"] == "50"
    assert dev["hs_sub"] == "0"
    # Resolution is whatever the resolver returns for PM=50 today — the report
    # surfaces the ground truth so the *resolver* bug is visible, not hidden.
    assert re.fullmatch(r"\d+x\d+", dev["hs_resolution"])
    assert dev["hs_raw"]  # first handshake bytes, hex — ground truth for offsets

    text = report.render_text()
    assert "handshake: PM=50 SUB=0" in text
    assert f"resolution={dev['hs_resolution']}" in text


def test_debug_report_skips_handshake_for_led(tmp_path: Path) -> None:
    """An LED segment display has no frame handshake — the probe skips it
    cleanly (no ``hs_`` fields) and the report still renders."""
    from tests.mock_platform import MockPlatform

    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path)
    report = build_debug_report(platform)

    assert len(report.devices) == 1
    assert "hs_resolution" not in report.devices[0]
    assert "## Devices" in report.render_text()


# =========================================================================
# Command-level end-to-end
# =========================================================================


@pytest.fixture
def _trcc_app(fake_platform):
    """Bare App without a renderer — diagnostics never touch DisplayService."""
    from trcc.app import App
    return App(fake_platform)


def test_run_health_check_command(_trcc_app) -> None:
    from trcc.core.commands import RunHealthCheck

    result = _trcc_app.dispatch(RunHealthCheck())
    assert result.ok is (result.fail_count == 0)
    assert result.checks
    assert any(c.name == "python-version" for c in result.checks)


def test_run_doctor_command(_trcc_app) -> None:
    from trcc.core.commands import RunDoctor

    result = _trcc_app.dispatch(RunDoctor())
    assert result.rendered
    assert "checks total" in result.rendered


def test_generate_debug_report_writes_to_disk(
    _trcc_app, tmp_path: Path,
) -> None:
    from trcc.core.commands import GenerateDebugReport

    out = tmp_path / "debug.txt"
    result = _trcc_app.dispatch(GenerateDebugReport(
        output_path=out, log_tail_lines=10,
    ))
    assert result.ok is True
    assert result.output_path == str(out)
    assert out.is_file()


def test_generate_debug_report_in_memory_only(_trcc_app) -> None:
    from trcc.core.commands import GenerateDebugReport

    result = _trcc_app.dispatch(GenerateDebugReport(output_path=None))
    assert result.ok is True
    assert result.output_path == ""
    assert "Platform" in result.rendered_text


# ── CPU power (RAPL) report section (#194) ───────────────────────────


def test_render_powercap_readable_domain() -> None:
    """A readable package domain renders its name + energy_uj mode (#194)."""
    from trcc.adapters.diagnostics.debug_report import _render_powercap

    out = _render_powercap([
        {"domain": "intel-rapl:0", "name": "package-0",
         "energy_uj": "readable (0o444)"},
    ])
    assert "intel-rapl:0" in out
    assert "package-0" in out
    assert "readable (0o444)" in out


def test_render_powercap_root_only_is_flagged() -> None:
    """A root-only energy_uj is surfaced so the reporter sees the real cause
    of a blank cpu:power — the permission, not a code bug (#194)."""
    from trcc.adapters.diagnostics.debug_report import _render_powercap

    out = _render_powercap([
        {"domain": "intel-rapl:0", "name": "package-0",
         "energy_uj": "ROOT-ONLY (0o400)"},
    ])
    assert "ROOT-ONLY (0o400)" in out


def test_render_powercap_empty_points_at_setup() -> None:
    """No domains → tell the reporter to run setup (driver not loaded) (#194)."""
    from trcc.adapters.diagnostics.debug_report import _render_powercap

    out = _render_powercap([])
    assert "intel_rapl_msr" in out
    assert "trcc setup" in out


def test_collect_powercap_returns_list() -> None:
    """Smoke: the collector never raises and returns a list (rows on Linux
    with RAPL, empty otherwise)."""
    from trcc.adapters.diagnostics.debug_report import _collect_powercap

    assert isinstance(_collect_powercap(), list)
