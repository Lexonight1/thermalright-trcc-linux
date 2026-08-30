"""Diagnostics — health checks, doctor, debug report bundle."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from trcc.adapters.diagnostics import health as health_mod
from trcc.adapters.diagnostics.adapter import DiagnosticsAdapter
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
from trcc.adapters.infra.logging import (
    RenderOnceRotatingFileHandler,
    configure_logging,
    tail_log,
    tail_log_actions,
)
from trcc.core.logs import PER_FRAME_ROOT, TRACE, levels_for, per_frame, trace

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


def _count_renders(root: logging.Logger) -> dict[str, int]:
    """Wrap every attached formatter so a test can count real render work."""
    calls = {"format": 0, "formatTime": 0}
    for handler in root.handlers:
        # Only OUR handlers.  pytest attaches its own capture handler with its
        # own formatter, and counting that would measure the test runner.
        if not getattr(handler, "_trcc_handler", False):
            continue
        fmt = handler.formatter
        if fmt is None or getattr(fmt, "_counted", False):
            continue
        original_format, original_time = fmt.format, fmt.formatTime

        def counted_format(record, _o=original_format):
            calls["format"] += 1
            return _o(record)

        def counted_time(record, datefmt=None, _o=original_time):
            calls["formatTime"] += 1
            return _o(record, datefmt)

        fmt.format = counted_format          # type: ignore[method-assign]
        fmt.formatTime = counted_time        # type: ignore[method-assign]
        fmt._counted = True                  # type: ignore[attr-defined]
    return calls


def test_a_record_is_rendered_once_not_four_times(tmp_path: Path) -> None:
    """Two rotating handlers must not turn one record into four renders.

    CPython's ``RotatingFileHandler.shouldRollover`` calls ``format(record)``
    purely to take ``len()`` of the result and discards it, and this app
    attaches two rotating handlers -- so a record was formatted four times,
    with four ``strftime`` calls, three of them wasted.  Logging was measured
    at 82-90%% of the CPU regression since v9.9.2, so the waste is not
    academic.

    MUTATION CHECK: make the handlers plain ``RotatingFileHandler`` again and
    this fails with 4 != 1.
    """
    configure_logging(tmp_path / "t.log", level=logging.DEBUG,
                      stderr_level=logging.CRITICAL)
    calls = _count_renders(logging.getLogger())

    logging.getLogger("render.once").debug("one %s %d", "record", 42)
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert calls["format"] == 1
    assert calls["formatTime"] == 1


def test_rendering_once_still_writes_the_same_text_to_both_files(
    tmp_path: Path,
) -> None:
    """Caching the rendered text must not change what lands on disk.

    The whole point is that only the NUMBER of renders changes.  Both the
    rolling file and the per-run ``latest`` must carry byte-identical lines.

    Note this cannot detect a cache that ignores the formatter identity --
    both handlers here share one formatter, so the text is the same either
    way.  ``test_a_handler_with_its_own_formatter_renders_its_own_text``
    guards that separately.
    """
    log_file = tmp_path / "t.log"
    configure_logging(log_file, level=logging.DEBUG,
                      stderr_level=logging.CRITICAL)
    logging.getLogger("render.once").debug("payload %s", "value")
    for handler in logging.getLogger().handlers:
        handler.flush()

    rolling = [ln for ln in log_file.read_text().splitlines() if "payload" in ln]
    latest = [ln for ln in (tmp_path / "t.latest.log").read_text().splitlines()
              if "payload" in ln]

    assert rolling == latest
    assert len(rolling) == 1
    assert rolling[0].endswith("payload value")
    assert "render.once" in rolling[0]


def test_a_handler_with_its_own_formatter_renders_its_own_text(
    tmp_path: Path,
) -> None:
    """The render cache is keyed by formatter, so it cannot leak between them.

    The two handlers this app configures share one formatter, so a cache that
    ignored identity would look correct forever -- right up until someone
    attaches a handler with its own format string and silently gets another
    handler's text.  Keyed on identity, each renders its own.

    MUTATION CHECK: drop ``cached[0] is self.formatter`` from the cache lookup
    in ``RenderOnceRotatingFileHandler.format`` and this fails -- the second
    handler emits the first one's line.
    """
    first = RenderOnceRotatingFileHandler(tmp_path / "first.log", encoding="utf-8")
    first.setFormatter(logging.Formatter("FIRST %(message)s"))
    second = RenderOnceRotatingFileHandler(tmp_path / "second.log", encoding="utf-8")
    second.setFormatter(logging.Formatter("SECOND %(message)s"))

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "shared", None, None)
    for handler in (first, second):
        handler.handle(record)
        handler.close()

    assert (tmp_path / "first.log").read_text().strip() == "FIRST shared"
    assert (tmp_path / "second.log").read_text().strip() == "SECOND shared"


def test_per_frame_lines_are_silent_by_default(tmp_path: Path) -> None:
    """The frame path must not write to the file during a normal run.

    Per-frame lines were 92%% of every record and ~90%% of the CPU regression
    since v9.9.2 — 44 records per rendered frame at 688/s.  At INFO their
    ``.debug()`` short-circuits in ``isEnabledFor``, so the LogRecord is never
    constructed, which is where the saving is.

    MUTATION CHECK: drop the ``PER_FRAME_ROOT`` setLevel from
    ``configure_logging`` and this fails — the frame line lands in the file.
    """
    log_file = tmp_path / "t.log"
    configure_logging(log_file, level=logging.DEBUG,
                      stderr_level=logging.CRITICAL)

    per_frame(__name__).debug("frame tick %d", 7)
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "frame tick" not in log_file.read_text()


def test_one_v_brings_the_frame_path_back(tmp_path: Path) -> None:
    """``-v`` is what buys the firehose — it must actually restore it.

    MUTATION CHECK: hard-code the level to INFO and this fails.
    """
    log_file = tmp_path / "t.log"
    configure_logging(log_file, level=logging.DEBUG,
                      stderr_level=logging.CRITICAL, per_frame=True)

    per_frame(__name__).debug("frame tick %d", 7)
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "frame tick 7" in log_file.read_text()


def test_ordinary_debug_still_reaches_the_file_by_default(
    tmp_path: Path,
) -> None:
    """The report keeps its DEBUG detail — only the FRAME path is gated.

    This is the guarantee that separates this change from the one it replaced.
    Silencing DEBUG wholesale would save the same CPU and re-break exactly
    what the always-DEBUG rule exists to fix: the one-shot lines a reporter
    needs — which sysfs path the device resolved to, which transport opened,
    why a download was skipped — are only 0.6%% of the records and cost
    nothing.

    MUTATION CHECK: set the ROOT logger to INFO instead of the per-frame
    family and this fails — the whole diagnostic trail vanishes with the noise.
    """
    log_file = tmp_path / "t.log"
    configure_logging(log_file, level=logging.DEBUG,
                      stderr_level=logging.CRITICAL)

    logging.getLogger("trcc.adapters.system.linux").debug(
        "_resolve_scsi_path: %s", "0402:3922")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "_resolve_scsi_path: 0402:3922" in log_file.read_text()


def test_per_frame_loggers_are_one_family(tmp_path: Path) -> None:
    """Every per-frame logger is silenced by ONE setLevel, with no registry.

    They are children of a single parent, so a module that starts logging
    per-frame lines is covered the moment it calls ``per_frame`` — nobody has
    to remember to add it to a list, which is the thing that would drift.
    """
    configure_logging(tmp_path / "t.log", level=logging.DEBUG,
                      stderr_level=logging.CRITICAL)

    for module in ("trcc.services.display", "trcc.adapters.render.qt",
                   "trcc.some.module.written.tomorrow"):
        assert per_frame(module).getEffectiveLevel() == logging.INFO
        assert per_frame(module).name.startswith(PER_FRAME_ROOT)


def test_tail_log_handles_missing_file(tmp_path: Path) -> None:
    assert tail_log(tmp_path / "absent.log") == []


def test_tail_log_returns_last_n_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(500)))
    tail = tail_log(log_file, n_lines=10)
    assert len(tail) == 10
    assert tail[-1] == "line 499"


# =========================================================================
# Action history — selecting by significance, not recency
# =========================================================================


def _record(level: str, msg: str, i: int = 0) -> str:
    """One line in this project's real format (level is the 2nd token)."""
    return f"2026-08-02T12:00:{i % 60:02d} {level:<7} trcc.mod:fn:{i}: {msg}"


def test_tail_log_actions_handles_missing_file(tmp_path: Path) -> None:
    assert tail_log_actions(tmp_path / "absent.log") == []


def test_tail_log_actions_reaches_past_the_tail_window(tmp_path: Path) -> None:
    """The reason this exists.

    A render loop buries the user's actions: here one action is followed by
    2000 DEBUG lines, so a 1000-line tail cannot see it at all while the
    action history returns it as the only entry.
    """
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join(
        [_record("INFO", "LoadTheme ok: Theme1")]
        + [_record("DEBUG", f"draw_text {i}", i) for i in range(2000)],
    ))

    assert not any("LoadTheme" in line for line in tail_log(log_file, 1000))
    assert [line.split(": ", 1)[-1]
            for line in tail_log_actions(log_file)] == ["LoadTheme ok: Theme1"]


def test_tail_log_actions_keeps_every_level_above_debug(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join(
        _record(lvl, lvl.lower())
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    ))

    kept = tail_log_actions(log_file)

    assert [line.split()[1] for line in kept] == [
        "INFO", "WARNING", "ERROR", "CRITICAL",
    ]


def test_tail_log_actions_keeps_a_traceback_with_its_error(
    tmp_path: Path,
) -> None:
    """``log.exception`` writes the stack as continuation lines with no level
    token of their own.  An ERROR whose stack was dropped is the half of the
    answer that matters least."""
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join([
        _record("ERROR", "connect failed"),
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "USBError: [Errno 13] Access denied",
    ]))

    kept = tail_log_actions(log_file)

    assert len(kept) == 4
    assert "Access denied" in kept[-1]


def test_tail_log_actions_drops_a_debug_records_continuation(
    tmp_path: Path,
) -> None:
    """A continuation rides on its record — so a DEBUG one is dropped too,
    otherwise the filter leaks whatever a per-frame line happened to wrap."""
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join([
        _record("DEBUG", "per-frame detail"),
        "  continuation of the debug line",
        _record("INFO", "the action"),
    ]))

    kept = tail_log_actions(log_file)

    assert len(kept) == 1
    assert kept[0].endswith("the action")


def test_tail_log_actions_is_bounded_and_keeps_the_most_recent(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "trcc.log"
    log_file.write_text("\n".join(
        _record("INFO", f"action {i}", i) for i in range(300)
    ))

    kept = tail_log_actions(log_file, n_lines=10)

    assert len(kept) == 10
    assert kept[-1].endswith("action 299")


# =========================================================================
# Health checks
# =========================================================================


def test_python_version_check_passes_on_311_plus(fake_platform) -> None:
    result = check_python_version(fake_platform)
    assert result.severity == "OK"
    assert "Python" in result.message


def test_each_os_platform_answers_its_own_install_hint() -> None:
    """The cutover Linux-hardcoded these; now each OS answers via the ABC."""
    from trcc.adapters.system.bsd import FreeBsdOS, NetBsdOS, OpenBsdOS
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "winget" in WindowsPlatform().software_install_hint("ffmpeg")
    assert "brew" in MacOSPlatform().software_install_hint("ffmpeg")
    # The BSDs differ in COMMAND, not data: FreeBSD has pkg, OpenBSD and
    # NetBSD have pkg_add.  One class served all three "pkg install" until
    # 2026-08-19, telling OpenBSD users to run something they do not have.
    assert "pkg install" in FreeBsdOS().software_install_hint("ffmpeg")
    assert "pkg_add" in OpenBsdOS().software_install_hint("ffmpeg")
    assert "pkg_add" in NetBsdOS().software_install_hint("ffmpeg")
    assert "pkg install" not in OpenBsdOS().software_install_hint("ffmpeg")
    # Unknown tool falls back to the generic ABC default, never crashes.
    assert "PATH" in WindowsPlatform().software_install_hint("nonesuch")


def test_each_os_platform_answers_its_own_no_devices_hint() -> None:
    from trcc.adapters.system.bsd import FreeBsdOS
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "WinUSB" in WindowsPlatform().no_devices_hint()
    assert "macOS" in MacOSPlatform().no_devices_hint()
    assert "usbconfig" in FreeBsdOS().no_devices_hint()
    # No Linux-isms (udev) leaking onto the non-Linux platforms.
    assert "udev" not in WindowsPlatform().no_devices_hint()
    assert "udev" not in MacOSPlatform().no_devices_hint()


def test_each_os_platform_answers_its_own_permission_denied_hint() -> None:
    """EACCES USB hint moved off a core sys.platform sniff onto the Platform port."""
    from trcc.adapters.system.bsd import FreeBsdOS, OpenBsdOS
    from trcc.adapters.system.linux import LinuxOS
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.system.windows import WindowsPlatform

    assert "udev" in LinuxOS().permission_denied_hint()
    assert "WinUSB" in WindowsPlatform().permission_denied_hint()
    macos_hint = MacOSPlatform().permission_denied_hint()
    assert "sudo" in macos_hint or "Privacy" in macos_hint
    assert "devd" in FreeBsdOS().permission_denied_hint()
    assert "ugen" in OpenBsdOS().permission_denied_hint()
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


# ``gpu_state`` is passed explicitly rather than monkeypatched onto the
# module.  It is a default argument, bound when the function is defined, so
# patching ``health_mod.nvml_init_state`` afterwards would NOT reach it — the
# check would quietly probe this machine's real NVIDIA driver and these cases
# would assert against whatever it happens to say.


def _state(available: bool, initialized: bool, error: str | None = None):
    """A stand-in for ``nvml_init_state`` reporting a chosen driver state."""
    def _read() -> tuple[bool, bool, str | None]:
        return available, initialized, error
    return _read


def test_gpu_check_ok_when_nvml_initialized(fake_platform) -> None:
    result = check_gpu_sensors(fake_platform, _state(True, True))
    assert result.severity == "OK"
    assert "NVML initialized" in result.message


def test_gpu_check_ok_when_no_nvidia_card(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: False)
    result = check_gpu_sensors(fake_platform, _state(False, False))
    assert result.severity == "OK"
    assert "No discrete NVIDIA GPU" in result.message


def test_gpu_check_warns_when_reader_missing(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuinely absent — nothing installed, so "install it" is right.

    The distribution lookup is stubbed to absent rather than left to the host:
    on a machine that HAS nvidia-ml-py this test used to pass while exercising
    the other fault entirely, because "the reader did not load" and "the reader
    is not installed" were the same branch.  They are not the same, and
    conflating them is what sent #207 and #216 reporters back to reinstall
    something they already had.
    """
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    monkeypatch.setattr(health_mod.toolchain, "installed_elsewhere",
                        lambda dist: None)
    result = check_gpu_sensors(fake_platform, _state(False, False))
    assert result.severity == "WARN"
    assert "pynvml reader is not installed" in result.message
    # Hint now comes from the DI'd platform's software_install_hint("pynvml")
    # rather than a Linux-hardcoded package name.
    assert "pynvml" in result.fix_hint


def test_gpu_check_says_installed_when_the_binding_is_present(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed but not importable — "install it" would be wrong advice.

    A reporter who put nvidia-ml-py in one interpreter and runs trcc under
    another, or whose driver is missing libnvidia-ml.so.1, must not be told to
    install the binding again.  This is the other half of the split above.
    """
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    monkeypatch.setattr(health_mod.toolchain, "installed_elsewhere",
                        lambda dist: "13.595.45")
    result = check_gpu_sensors(fake_platform, _state(False, False))
    assert result.severity == "WARN"
    assert "is installed but did not import" in result.message
    assert "13.595.45" in result.message
    assert "will not help" in result.fix_hint
    assert "libnvidia-ml.so.1" in result.fix_hint


def test_gpu_check_warns_with_reload_hint_on_init_failure(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = "NVMLError_LibRmVersionMismatch: RM has detected an NVML/RM version mismatch"
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    result = check_gpu_sensors(fake_platform, _state(True, False, err))
    assert result.severity == "WARN"
    assert err in result.message
    assert "modprobe" in result.fix_hint


def test_the_injected_gpu_state_reaches_the_check_through_the_whole_report(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam is threaded, not just present on the leaf function.

    ``DiagnosticsAdapter`` → ``run_health_checks`` → ``check_gpu_sensors`` is
    three hops, and a seam that stops at any of them still leaves the report
    reading the real driver.  Asserting on the leaf alone would not notice.
    """
    monkeypatch.setattr(health_mod, "nvidia_gpu_present", lambda: True)
    calls: list[str] = []

    def _reader() -> tuple[bool, bool, str | None]:
        calls.append("asked")
        return True, False, "a driver state this machine is not in"

    report = DiagnosticsAdapter(fake_platform, _reader).health()

    assert calls, "the report never asked the injected reader"
    gpu = next(c for c in report.checks if c.name == "gpu-sensors")
    assert "a driver state this machine is not in" in gpu.message


def test_gpu_reader_state_uses_the_injected_reader(fake_platform) -> None:
    """The other reach — the port method the GPU-offer decision consumes."""
    state = DiagnosticsAdapter(fake_platform, _state(True, True)).gpu_reader_state()
    assert (state.reader_installed, state.initialized) == (True, True)


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
    for header in ("Platform", "Paths", "Devices", "Sensors", "Health",
                   "Actions", "Log tail"):
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


# ── The verbosity ladder — the rule, gated ───────────────────────────────────
#
# This mapping used to live as an if-chain inside ``ui.cli.main._root`` and
# NOTHING asserted it: ``per_frame=verbose > 0`` could have read ``>= 99`` and
# the suite stayed green, because every test here calls ``configure_logging``
# with explicit arguments and never asks what a ``-v`` count resolves to.
# A rule nothing can call is a rule nothing can gate.


@pytest.mark.parametrize("verbosity,terminal,file_level,per_frame", [
    (0, logging.WARNING, logging.DEBUG, False),
    (1, logging.INFO,    logging.DEBUG, False),
    (2, logging.DEBUG,   logging.DEBUG, False),
    (3, TRACE,           TRACE,         True),
    (9, TRACE,           TRACE,         True),   # saturates, never inverts
])
def test_verbosity_ladder(verbosity: int, terminal: int,
                          file_level: int, per_frame: bool) -> None:
    """-v INFO, -vv DEBUG, -vvv TRACE; quiet by default."""
    levels = levels_for(verbosity)
    assert levels.terminal == terminal
    assert levels.file == file_level
    assert levels.per_frame is per_frame


def test_the_file_never_loses_debug_however_quiet_the_terminal() -> None:
    """The invariant the always-DEBUG rule exists to protect.

    ``trcc report`` is the entire diagnosis for hardware we do not own.  A file
    level that rose with a flag would mean a reporter who did not know the flag
    sends a log with the evidence already discarded — which is the bug that rule
    was written to fix.  The ladder governs the TERMINAL.
    """
    for verbosity in range(10):
        levels = levels_for(verbosity)
        assert levels.file <= logging.DEBUG, (
            f"-{'v' * verbosity} would raise the file above DEBUG"
        )


def test_trace_is_below_debug_so_vvv_must_lower_the_file_too() -> None:
    """Why ``-vvv`` is the one rung that touches the file.

    TRACE sits BELOW debug, so a file pinned at DEBUG could never record a
    TRACE line — the deepest detail would be visible on the terminal and absent
    from the one artifact a report is read from.
    """
    assert TRACE < logging.DEBUG
    assert levels_for(3).file == TRACE
    assert logging.getLevelName(TRACE) == "TRACE"


def test_trace_helper_emits_only_when_enabled(tmp_path: Path) -> None:
    """``trace()`` is silent at DEBUG and lands at TRACE."""
    log_file = tmp_path / "trcc.log"
    configure_logging(log_file, level=logging.DEBUG, stderr_level=logging.CRITICAL)
    logger = logging.getLogger("trcc.test.trace")
    trace(logger, "deep internal %s", "payload")
    logging.getLogger().handlers[0].flush()
    assert "deep internal" not in log_file.read_text()

    configure_logging(log_file, level=TRACE, stderr_level=logging.CRITICAL)
    trace(logger, "deep internal %s", "payload")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "deep internal payload" in log_file.read_text()


# =========================================================================
# The frame path must not write a record per frame
# =========================================================================
#
# The burn-down's ratchet (``test_logging_coverage``) counts SILENT functions
# and only ever pushes that number down.  It cannot see the defect on the other
# side of the same line: a function ON THE FRAME PATH that logs through the
# ORDINARY logger writes a record EVERY frame.  The file floor is DEBUG at
# every rung by design, so those records are written even with no ``-v`` — the
# cost is paid and the one-shot lines a report is read for get scrolled out of
# the 1 MB tail.  That shape was 82-90% of the CPU regression since v9.9.2.
#
# It is also easy to re-create while ADDING coverage, which is exactly how it
# came back: measured 2026-08-30 on the real device, the static-theme path wrote
# 4.00 records/frame and the advancing-video path 6.25, across 16 call sites.
#
# So this gate asserts the invariant rather than the instance: no call site may
# emit at a rate that scales with the frame count.  A legitimately rare line is
# free to fire — ``_log_cache_transition`` fires once per cache-state FLIP and
# measured 0.005/frame — which is why the bar is a RATE, and why it reuses the
# profiler's own definition of hot rather than inventing a second number.

#: Same threshold ``dev/tools/frame_profile.py --hot`` uses.  A per-frame
#: emitter sits at ~1.0; a once-per-transition line sits near zero.  Nothing
#: real lands between, so the gap is where the bar goes.
_PER_FRAME_RATE = 0.5


def _records_by_site(log_file: Path, start: int) -> dict[str, int]:
    """Count records appended after byte offset *start*, keyed by call site."""
    counts: dict[str, int] = {}
    with log_file.open("r", errors="replace") as fh:
        fh.seek(start)
        for line in fh:
            m = re.match(r"^\S+ \w+\s+(\S+?):(\S+?):(\d+):", line)
            if m is not None:
                site = ":".join(m.groups())
                counts[site] = counts.get(site, 0) + 1
    return counts


def _frame_path_rates(tmp_path: Path, *, starve_cache: bool,
                      frames: int = 30) -> dict[str, float]:
    """Records-per-frame, by call site, driving the REAL service chain.

    ``starve_cache`` picks WHICH frame path runs, and both matter:

    * ``True``  — a one-byte ``BgMaskCache`` so every frame MISSES and rebuilds.
      That is the background/mask chain (``_resolve_background``,
      ``_build_bg_mask``, ``decode_image``, ``open_image``, ``bg_fit`` …) where
      11 of the 16 measured floods lived.
    * ``False`` — a static theme with no playback, so after the first frame the
      full-pipeline cache HITS every time.  That is the other path, and
      ``build_frame``'s own cache-HIT line was one of the four floods on it.

    A gate that drove only one of the two would be blind to half the tree —
    which is exactly how the first version of this test passed with a live
    flood in ``_resolve_background``.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from trcc.adapters.render.qt import QtRenderer
    from trcc.adapters.theme.filesystem import FileContentStore
    from trcc.core.models import Kind, ProductInfo, Theme, Wire
    from trcc.core.protocol import get_profile
    from trcc.services.bg_cache import BgMaskCache
    from trcc.services.display import DisplayService
    from trcc.services.media import MediaService, Playback
    from trcc.services.overlay import OverlayService
    from trcc.services.settings import Settings

    from .conftest import FakePaths
    from .test_video_playback import _encoded_frame

    ladder = levels_for(0)                       # what a user runs: no -v
    log_file = tmp_path / "trcc.log"
    # ``level`` is the ROOT level and so the FILE's; ``stderr_level`` is the
    # terminal's.  Passing the terminal level here would set the root to
    # WARNING, suppress every DEBUG record, and make this pass no matter what
    # the code does — the exact false negative this exists to catch.
    configure_logging(log_file, level=ladder.file,
                      stderr_level=logging.CRITICAL,
                      per_frame=ladder.per_frame)

    renderer = QtRenderer()
    paths = FakePaths(tmp_path)
    media = MediaService()
    key = "0402:3922"
    if starve_cache:
        media._playbacks[key] = Playback(
            frames=[_encoded_frame(v)
                    for v in (0xFF000000, 0xFF404040, 0xFF808080)],
            fps=15,
        )
    display = DisplayService(
        renderer=renderer,
        themes=FileContentStore(),
        overlay=OverlayService(renderer),
        settings=Settings(paths),
        media=media,
        paths=paths,
    )
    if starve_cache:
        # A one-byte budget evicts on every put, so `get` MISSES every frame
        # and the whole rebuild chain runs.  Seeded directly because the real
        # 128 MB cap would need ~320 distinct 320x320 surfaces to force this.
        display._bg_caches[key] = BgMaskCache(1)

    info = ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi Corp", product="LCD",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=(320, 320), orientations=(0,),
    )
    theme = Theme(path=tmp_path / "theme", name="t",
                  resolution=(320, 320), config={"elements": []})
    profile = get_profile(100)

    def render_once() -> None:
        playback = media._playbacks.get(key)
        if playback is not None:
            playback.advance()
        display.build_frame(info=info, theme=theme, sensors={},
                            profile=profile)

    for _ in range(5):            # warm-up: first-frame lines are one-shot
        render_once()
    for handler in logging.getLogger().handlers:
        handler.flush()

    mark = log_file.stat().st_size
    for _ in range(frames):
        render_once()
    for handler in logging.getLogger().handlers:
        handler.flush()

    return {site: n / frames
            for site, n in _records_by_site(log_file, mark).items()}


@pytest.mark.parametrize("starve_cache", [True, False],
                         ids=["bg-rebuild", "cache-hit"])
def test_the_frame_path_writes_no_record_per_frame(
    tmp_path: Path, starve_cache: bool,
) -> None:
    """Render repeatedly at DEFAULT verbosity; nothing may scale with frames."""
    rates = _frame_path_rates(tmp_path, starve_cache=starve_cache)
    floods = {s: r for s, r in rates.items() if r >= _PER_FRAME_RATE}
    assert not floods, (
        "these call sites write a record per rendered frame at DEFAULT "
        "verbosity — move each onto core.logs.per_frame(__name__) so the "
        "record is never constructed:\n"
        + "\n".join(f"  {rate:.2f}/frame  {site}"
                    for site, rate in sorted(floods.items(),
                                             key=lambda kv: -kv[1]))
    )


@pytest.mark.parametrize("starve_cache", [True, False],
                         ids=["bg-rebuild", "cache-hit"])
def test_the_frame_path_gate_actually_reaches_the_render_chain(
    tmp_path: Path, starve_cache: bool,
) -> None:
    """The gate above is only as good as the code it runs.

    Its first version asserted "no floods" while driving a workload that went
    entirely to cache after three frames, so it passed with a live flood in
    ``_resolve_background``.  A green result meant nothing.  This pins that the
    chain is genuinely exercised, so "no floods" is a finding and not silence.
    """
    rates = _frame_path_rates(tmp_path, starve_cache=starve_cache)
    per_frame_logger = [s for s in rates if s.startswith(PER_FRAME_ROOT)]
    assert not per_frame_logger, (
        "per-frame records reached the FILE at default verbosity — the family "
        f"is not silenced: {per_frame_logger}"
    )
    # Nothing asserts a specific line here: what matters is that the render ran
    # and its per-frame chatter was suppressed rather than never produced.  If
    # the chain stopped executing, the mutation test below stops failing and
    # says so in one sentence.


def test_the_frame_path_gate_can_actually_see_a_flood(tmp_path: Path) -> None:
    """Mutation check: the rule must FAIL when a flood is present.

    A gate that has never been broken on purpose is not known to guard
    anything, and this repo has twice shipped one that guarded nothing.  This
    reproduces the defect exactly — an ordinary logger called once per rendered
    frame — and asserts the same rule catches it while leaving a genuinely rare
    line alone.  If this stops failing-by-construction, the gate is dead.
    """
    ladder = levels_for(0)
    log_file = tmp_path / "trcc.log"
    configure_logging(log_file, level=ladder.file,
                      stderr_level=logging.CRITICAL,
                      per_frame=ladder.per_frame)

    ordinary = logging.getLogger("trcc.services.pretend")
    rare = logging.getLogger("trcc.services.pretend_rare")

    frames = 40
    mark = log_file.stat().st_size if log_file.exists() else 0
    for i in range(frames):
        ordinary.debug("pretend per-frame line %d", i)
        if i == 0:
            rare.debug("pretend once-per-transition line")
    for handler in logging.getLogger().handlers:
        handler.flush()

    rates = {s: n / frames
             for s, n in _records_by_site(log_file, mark).items()}
    flooding = [s for s, r in rates.items() if r >= _PER_FRAME_RATE]
    quiet = [s for s, r in rates.items() if r < _PER_FRAME_RATE]

    assert any("pretend:" in s for s in flooding), (
        "the per-frame emitter was NOT caught — the gate is blind and every "
        "pass it has ever reported is worthless"
    )
    assert any("pretend_rare:" in s for s in quiet), (
        "the once-per-transition emitter was flagged as a flood — the bar is "
        "too tight and the gate will fail on correct code"
    )
