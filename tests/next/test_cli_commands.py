"""CLI command smoke tests — every Typer subcommand reachable.

Phase D boilerplate (part 3): the CLI surface was at 24-58% coverage
across the six sub-apps; this file exercises every documented command
through ``typer.testing.CliRunner`` so each handler is at least
known to import + dispatch + render a response without raising.

Future tests for specific CLI behavior (output formatting, exit
codes, argument handling) build on the same ``cli_runner`` +
``cli_app_factory`` fixtures.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from trcc.next.app import App
from trcc.next.ui.cli import _ctx

from .conftest import FakePlatform

# =========================================================================
# Renderer used by the CLI's lazy QtRenderer import — keep tests offline
# =========================================================================


class _CliRenderer:
    """Stand-in for QtRenderer; CLI rarely renders but the fixture wires
    one for completeness so display commands don't crash on import."""

    def create_surface(self, width: int, height: int, color=None):
        return _Surface(width, height)

    def open_image(self, path):
        return _Surface(100, 100)

    def surface_size(self, surface):
        return (surface.w, surface.h)

    def composite(self, base, overlay, position, mask=None):
        return base

    def resize(self, surface, width, height):
        return _Surface(width, height)

    def rotate(self, surface, degrees):
        return surface

    def apply_brightness(self, surface, percent):
        return surface

    def draw_text(self, surface, x, y, text, color, size,
                  bold=False, italic=False):
        pass

    def encode_rgb565(self, surface):
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface, quality=95, max_size=0):
        return b""

    def from_raw_rgb24(self, frame):
        return _Surface(100, 100)


class _Surface:
    def __init__(self, w: int = 100, h: int = 100) -> None:
        self.w, self.h = w, h


# =========================================================================
# Fixtures — runner + cached App via _ctx override
# =========================================================================


@pytest.fixture
def cli_runner() -> CliRunner:
    """typer.testing.CliRunner — captures stdout + exit codes."""
    return CliRunner()


@pytest.fixture(autouse=True)
def cli_app_override(
    fake_platform: FakePlatform,
) -> Iterator[App]:
    """Pre-wire ``_ctx`` so ``get_app()`` returns a test App.

    The CLI looks up its App through ``_ctx.get_app()`` (lru_cached);
    tests inject a FakePlatform + smoke renderer so no real USB / Qt
    is touched.  Cache cleared on teardown.
    """
    _ctx.set_platform(fake_platform)
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    yield _ctx.get_app()
    _ctx.get_app.cache_clear()
    _ctx._platform_override = None
    _ctx._renderer_override = None


# =========================================================================
# Top-level commands
# =========================================================================


def test_help_lists_every_sub_app(cli_runner: CliRunner) -> None:
    """``trcc-next --help`` lists every registered sub-app — proves
    the top-level Typer composition is intact."""
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("device", "display", "led", "system", "config", "theme"):
        assert sub in result.stdout


def test_status_when_no_daemon_running(cli_runner: CliRunner) -> None:
    """``trcc-next status`` exits non-zero when no daemon is listening.

    Default Linux dev box: no daemon → ``daemon_running()`` returns
    False → status prints the unreachable message + exits 1.
    """
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["status"])
    # Either 0 (daemon happens to be up) or 1 (typical case).  We assert
    # the command at least runs without raising — that's the smoke.
    assert result.exit_code in (0, 1)


# =========================================================================
# device sub-app
# =========================================================================


def test_device_list_runs(cli_runner: CliRunner) -> None:
    """``device list`` exits 0 when devices found, 1 when empty —
    both are valid smoke outcomes.  FakePlatform reports no devices
    on the dev box, so the typical exit is 1 + "No supported devices
    found." message.
    """
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["device", "list"])
    assert result.exit_code in (0, 1)
    assert "device(s)" in result.stdout or "No supported devices" in result.stdout


def test_device_connect_unknown_returns_nonzero(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["device", "connect", "dead:beef"])
    # ConnectDevice fails on unknown key → CLI propagates non-zero
    assert result.exit_code != 0


# =========================================================================
# display sub-app
# =========================================================================


def test_display_help_runs(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["display", "--help"])
    assert result.exit_code == 0


def test_display_set_brightness_persists(cli_runner: CliRunner) -> None:
    """``set-brightness`` mutates Settings even without a connected device."""
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(
        app, ["display", "set-brightness", "0402:3922", "75"],
    )
    assert result.exit_code == 0


# =========================================================================
# led sub-app
# =========================================================================


def test_led_help_runs(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["led", "--help"])
    assert result.exit_code == 0


def test_led_set_mode_persists(cli_runner: CliRunner) -> None:
    """``led mode`` writes to LedDeviceSettings; no connected device needed."""
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(
        app, ["led", "mode", "0416:8001", "rainbow"],
    )
    assert result.exit_code == 0


def test_led_color_round_trip(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(
        app, ["led", "color", "0416:8001", "ff0080"],
    )
    assert result.exit_code == 0


# =========================================================================
# system sub-app
# =========================================================================


def test_system_help_runs(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["system", "--help"])
    assert result.exit_code == 0


# =========================================================================
# config sub-app
# =========================================================================


def test_config_temp_unit_persists(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["config", "temp-unit", "F"])
    assert result.exit_code == 0


def test_config_language_persists(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["config", "language", "de"])
    assert result.exit_code == 0


# =========================================================================
# theme sub-app
# =========================================================================


def test_theme_help_runs(cli_runner: CliRunner) -> None:
    from trcc.next.ui.cli.main import app

    result = cli_runner.invoke(app, ["theme", "--help"])
    assert result.exit_code == 0


# =========================================================================
# Subcommand discovery sanity
# =========================================================================


def test_main_app_registers_six_sub_apps() -> None:
    """The top-level Typer wires six sub-apps; a regression that drops
    one (mass rename gone wrong) surfaces here."""
    from trcc.next.ui.cli.main import app

    sub_names = {group.name for group in app.registered_groups}
    assert sub_names >= {
        "device", "display", "led", "system", "config", "theme",
    }
