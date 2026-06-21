"""Diagnostics — health checks, doctor, debug report bundle."""
from __future__ import annotations

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


# =========================================================================
# GPU reader install (app detects + installs)
# =========================================================================


def _patch_gpu_probes(
    monkeypatch: pytest.MonkeyPatch, *, present: bool, state: tuple,
) -> None:
    """Patch the SOURCE modules — the Command imports these function-locally,
    so the source attribute is what the lookup resolves at call time."""
    monkeypatch.setattr(
        "trcc.adapters.sensors.nvml.nvml_init_state", lambda: state,
    )
    monkeypatch.setattr(
        "trcc.adapters.diagnostics.health.nvidia_gpu_present", lambda: present,
    )


def test_get_gpu_reader_status_offers_install_when_reader_missing(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.core.commands import GetGpuReaderStatus

    _patch_gpu_probes(monkeypatch, present=True, state=(False, False, None))
    result = _trcc_app.dispatch(GetGpuReaderStatus())
    assert result.offer_install is True
    assert result.nvidia_present is True
    assert result.reader_installed is False
    assert result.init_failed is False


def test_get_gpu_reader_status_no_offer_when_no_card(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.core.commands import GetGpuReaderStatus

    _patch_gpu_probes(monkeypatch, present=False, state=(False, False, None))
    result = _trcc_app.dispatch(GetGpuReaderStatus())
    assert result.offer_install is False
    assert result.nvidia_present is False


def test_get_gpu_reader_status_no_offer_on_version_mismatch(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader present but nvmlInit failed → reboot case, not an install offer."""
    from trcc.core.commands import GetGpuReaderStatus

    _patch_gpu_probes(monkeypatch, present=True, state=(True, False, "mismatch"))
    result = _trcc_app.dispatch(GetGpuReaderStatus())
    assert result.offer_install is False
    assert result.init_failed is True


def _force_not_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the interpreter to look non-venv so the OS-package path is tested
    deterministically regardless of where the suite runs (CI may be a venv)."""
    import sys
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)


def test_install_gpu_reader_dry_run_builds_pkexec_command(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.core.commands import InstallGpuReader

    _force_not_venv(monkeypatch)
    monkeypatch.setattr(
        "trcc.adapters.diagnostics.health.detect_package_manager", lambda: "dnf",
    )
    result = _trcc_app.dispatch(InstallGpuReader(dry_run=True))
    assert result.ok is True
    assert result.package_manager == "dnf"
    assert result.command == ["pkexec", "dnf", "install", "-y", "python3-pynvml"]


def test_install_gpu_reader_no_recipe_falls_back_to_guide(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.core.commands import InstallGpuReader

    _force_not_venv(monkeypatch)
    monkeypatch.setattr(
        "trcc.adapters.diagnostics.health.detect_package_manager", lambda: "apk",
    )
    result = _trcc_app.dispatch(InstallGpuReader(dry_run=True))
    assert result.ok is False
    assert "manually" in result.message


def test_install_gpu_reader_uses_pip_in_a_venv(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In a virtualenv, install nvidia-ml-py via pip into THIS interpreter —
    the OS package manager would target system python the venv can't see (#161)."""
    import sys

    from trcc.core.commands import InstallGpuReader

    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(sys, "prefix", "/home/u/.venv")
    result = _trcc_app.dispatch(InstallGpuReader(dry_run=True))
    assert result.ok is True
    assert result.command == [sys.executable, "-m", "pip", "install", "nvidia-ml-py"]


def test_install_gpu_reader_no_package_manager(
    _trcc_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.core.commands import InstallGpuReader

    monkeypatch.setattr(
        "trcc.adapters.diagnostics.health.detect_package_manager", lambda: None,
    )
    result = _trcc_app.dispatch(InstallGpuReader(dry_run=True))
    assert result.ok is False
    assert "No supported package manager" in result.message
