"""System Commands — the unified UI surface (CLI/GUI/API/daemon all dispatch
these).  These tests pin behaviour at the ``app.dispatch(Command)`` boundary so
the hexagonal burn-down (moving logic behind injected ports) cannot change what
a UI sees.

Step 2 of the burn-down: ``ListFonts`` must enumerate fonts through the
*injected* ``Renderer`` port, NOT by importing PySide6 inside core.  The
delegation test injects a fake renderer and asserts the command uses it — the
DI/manipulability proof.
"""
from __future__ import annotations

from types import SimpleNamespace

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ListFonts, RunHealthCheck


def test_list_fonts_delegates_to_the_injected_renderer_port() -> None:
    """RED→GREEN driver: the command must read fonts from ``app.renderer``.

    Inject a fake renderer returning a sentinel list; if the command delegates
    to the DI'd port, the result is that exact list.  (Today the command does
    ``del app`` and imports PySide6 directly, so it ignores the injected
    renderer — this is the breach being closed.)
    """
    fake_renderer = SimpleNamespace(list_fonts=lambda: ["Sentinel Sans", "Sentinel Serif"])
    app_stub = SimpleNamespace(renderer=fake_renderer)

    result = ListFonts().execute(app_stub)  # type: ignore[arg-type]

    assert result.ok
    assert result.fonts == ["Sentinel Sans", "Sentinel Serif"]
    assert "2 font" in result.message


def test_list_fonts_with_no_renderer_returns_empty_gracefully(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A rendererless App yields an empty list, never an error.

    Uses a real ``App`` built with ``renderer=None`` (whose ``renderer``
    property raises) so the command's graceful-degradation path is exercised
    against the real port contract, not a stub.
    """
    app = App(MockPlatform([], tmp_path), renderer=None)
    try:
        result = app.dispatch(ListFonts())
        assert result.ok
        assert result.fonts == []
    finally:
        app.close()


def test_list_fonts_dispatch_returns_a_list(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Characterisation through the real App + QtRenderer dispatch path."""
    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        result = app.dispatch(ListFonts())
        assert result.ok
        assert isinstance(result.fonts, list)
    finally:
        app.close()


def test_qtrenderer_list_fonts_returns_list() -> None:
    """The Renderer port's concrete impl enumerates families (or [] headless)."""
    fonts = QtRenderer().list_fonts()
    assert isinstance(fonts, list)
    assert all(isinstance(f, str) for f in fonts)


# ── Step 3: Diagnostics port ─────────────────────────────────────────────────
# The diagnostics commands (RunHealthCheck/RunDoctor/GenerateDebugReport/
# GetGpuReaderStatus/RunUpgrade/InstallGpuReader) must reach health/doctor/
# debug-report/package-manager/gpu-reader through an INJECTED ``Diagnostics``
# port, not by importing adapters inside core Command bodies.

def _fake_diagnostics() -> object:
    """A ``Diagnostics`` returning sentinels so delegation is provable."""
    from trcc.core.diagnostics import (
        DoctorResult,
        GpuReaderState,
        HealthCheckResult,
        HealthReport,
    )
    from trcc.core.ports import Diagnostics

    class _FakeDiag(Diagnostics):
        def health(self) -> HealthReport:
            return HealthReport(checks=[HealthCheckResult("sentinel", "OK", "ok")])

        def doctor(self) -> DoctorResult:
            return DoctorResult(report=self.health(), exit_code=0)

        def render_doctor(self, report: HealthReport) -> str:
            return "SENTINEL DOCTOR"

        def debug_report(self, log_tail_lines: int) -> str:
            return "SENTINEL REPORT"

        def write_debug_report(self, rendered: str, path):  # type: ignore[no-untyped-def]
            path.write_text(rendered, encoding="utf-8")
            return path

        def package_manager(self) -> str | None:
            return "sentinel-pm"

        def gpu_reader_state(self) -> GpuReaderState:
            return GpuReaderState(
                nvidia_present=True, reader_installed=False, initialized=False,
            )

    return _FakeDiag()


def test_app_exposes_a_diagnostics_port(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Conformance: ``App.diagnostics`` is an injected ``Diagnostics`` port."""
    from trcc.core.ports import Diagnostics

    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        assert isinstance(app.diagnostics, Diagnostics)
    finally:
        app.close()


def test_run_health_check_delegates_to_the_diagnostics_port(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """RED→GREEN driver: dispatch reads health from the injected port.

    The fake returns exactly ONE check; the real health suite returns ~10, so a
    result with a single check proves the command used the injected port.
    """
    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        app.diagnostics = _fake_diagnostics()  # type: ignore[assignment]
        result = app.dispatch(RunHealthCheck())
        assert result.ok
        assert len(result.checks) == 1
    finally:
        app.close()


def test_run_upgrade_dry_run_reads_pm_from_the_port(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The package manager comes from the injected port, not a direct import."""
    from trcc.core.commands import RunUpgrade

    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        app.diagnostics = _fake_diagnostics()  # type: ignore[assignment]
        result = app.dispatch(RunUpgrade(dry_run=True))
        # The sentinel pm has no upgrade recipe (so ok=False), but it lands in
        # ``package_manager`` — proving the command read it from the injected port.
        assert result.package_manager == "sentinel-pm"
    finally:
        app.close()


def test_diagnostics_commands_still_dispatch_through_real_adapter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Characterisation: the real wiring still answers each command (safety net)."""
    from trcc.core.commands import GenerateDebugReport, GetGpuReaderStatus, RunDoctor

    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(RunHealthCheck()).ok in (True, False)   # runs, returns a report
        assert isinstance(app.dispatch(RunDoctor()).rendered, str)
        assert app.dispatch(GenerateDebugReport()).rendered_text
        assert app.dispatch(GetGpuReaderStatus()).ok
    finally:
        app.close()
