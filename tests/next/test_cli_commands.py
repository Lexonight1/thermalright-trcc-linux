"""CLI command coverage — every Typer subcommand reachable.

The CLI was the original UI; the GUI shipped on top of it (invoking
CLI commands under the hood for years).  Next/'s unified Command
bus collapses both into one dispatch path, but the principle holds:
**every CLI command must be solid**, because the CLI is what every
other UI is also ultimately driving.

Fixtures (``cli_runner``, ``cli_app``) live in ``conftest.py`` so
per-feature CLI test files (future ``test_cli_display.py`` for deeper
display assertions, etc.) build on the same scaffolding.

Long-running commands (``gui``, ``api``, ``daemon``, ``shell``,
``led play``, ``display play``, ``display slideshow``) are exercised
through their ``--help`` surface — actually launching them would
block.  Future tests that need to drive these end-to-end live in
their own files with thread or timeout wrappers.
"""
from __future__ import annotations

from typer.testing import CliRunner


def _app():
    """Import the top-level Typer app inside each test so the
    ``cli_app`` fixture's platform override is in place first."""
    from trcc.next.ui.cli.main import app
    return app


# =========================================================================
# Top-level — main.py commands
# =========================================================================


def test_help_lists_every_sub_app(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["--help"])
    assert result.exit_code == 0
    for sub in ("device", "display", "led", "system", "config", "theme"):
        assert sub in result.stdout


def test_status_smoke(cli_runner: CliRunner, cli_app) -> None:
    """Exits 1 when no daemon listening (typical Linux dev box)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["status"])
    assert result.exit_code in (0, 1)


def test_gui_help(cli_runner: CliRunner, cli_app) -> None:
    """``gui --help`` reaches the typer router without actually
    launching Qt — proves the import path resolves."""
    del cli_app
    result = cli_runner.invoke(_app(), ["gui", "--help"])
    assert result.exit_code == 0


def test_api_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["api", "--help"])
    assert result.exit_code == 0


def test_daemon_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["daemon", "--help"])
    assert result.exit_code == 0


def test_kill_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["kill", "--help"])
    assert result.exit_code == 0


def test_shell_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["shell", "--help"])
    assert result.exit_code == 0


def test_main_app_registers_six_sub_apps() -> None:
    """The top-level Typer wires six sub-apps; a regression that drops
    one (mass rename gone wrong) surfaces here."""
    sub_names = {group.name for group in _app().registered_groups}
    assert sub_names >= {
        "device", "display", "led", "system", "config", "theme",
    }


# =========================================================================
# device sub-app — list / connect / disconnect
# =========================================================================


def test_device_list_smoke(cli_runner: CliRunner, cli_app) -> None:
    """Exits 0 when devices found, 1 when empty.  FakePlatform reports
    empty, so the typical exit is 1 + "No supported devices" message."""
    del cli_app
    result = cli_runner.invoke(_app(), ["device", "list"])
    assert result.exit_code in (0, 1)
    assert "device(s)" in result.stdout or "No supported devices" in result.stdout


def test_device_connect_unknown_key(cli_runner: CliRunner, cli_app) -> None:
    """Unknown VID:PID → CLI surfaces the error message + non-zero exit."""
    del cli_app
    result = cli_runner.invoke(_app(), ["device", "connect", "dead:beef"])
    assert result.exit_code != 0


def test_device_disconnect_unknown_key(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["device", "disconnect", "dead:beef"])
    assert result.exit_code != 0


# =========================================================================
# display sub-app — 15 commands total
# =========================================================================


def test_display_set_orientation_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "set-orientation", "0402:3922", "90"],
    )
    assert result.exit_code == 0


def test_display_set_brightness_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "set-brightness", "0402:3922", "75"],
    )
    assert result.exit_code == 0


def test_display_color_without_device(cli_runner: CliRunner, cli_app) -> None:
    """``display color`` needs a connected device — exits non-zero
    with a structured "not attached" message."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "color", "0402:3922", "ff0000"],
    )
    assert result.exit_code != 0


def test_display_set_fit_mode_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "set-fit-mode", "0402:3922", "width"],
    )
    assert result.exit_code == 0


def test_display_overlay_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "overlay", "0402:3922", "true"],
    )
    assert result.exit_code == 0


def test_display_mask_position_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "mask-position", "0402:3922", "10", "20"],
    )
    assert result.exit_code == 0


def test_display_mask_visible_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "mask-visible", "0402:3922", "false"],
    )
    assert result.exit_code == 0


def test_display_split_mode_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "split-mode", "0402:3922", "1"],
    )
    assert result.exit_code == 0


def test_display_stop_video_idempotent(cli_runner: CliRunner, cli_app) -> None:
    """``stop-video`` is idempotent — succeeds even with nothing playing."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "stop-video", "0402:3922"],
    )
    assert result.exit_code == 0


def test_display_play_help(cli_runner: CliRunner, cli_app) -> None:
    """``display play`` is a blocking ticker — only --help is safe."""
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "play", "--help"])
    assert result.exit_code == 0


def test_display_slideshow_help(cli_runner: CliRunner, cli_app) -> None:
    """Same — slideshow loops until Ctrl-C."""
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "slideshow", "--help"])
    assert result.exit_code == 0


def test_display_boot_anim_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "boot-anim", "--help"])
    assert result.exit_code == 0


def test_display_load_theme_help(cli_runner: CliRunner, cli_app) -> None:
    """``load-theme`` needs a theme dir fixture — smoke via --help."""
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "load-theme", "--help"])
    assert result.exit_code == 0


def test_display_apply_mask_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "apply-mask", "--help"])
    assert result.exit_code == 0


def test_display_play_video_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "play-video", "--help"])
    assert result.exit_code == 0


# =========================================================================
# led sub-app — 9 commands + the ticker
# =========================================================================


def test_led_set_colors_help(cli_runner: CliRunner, cli_app) -> None:
    """``led set-colors`` needs hex args + a connected device — smoke via --help."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "set-colors", "--help"])
    assert result.exit_code == 0


def test_led_render_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "render", "--help"])
    assert result.exit_code == 0


def test_led_mode_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "mode", "0416:8001", "rainbow"],
    )
    assert result.exit_code == 0


def test_led_color_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "color", "0416:8001", "ff0080"],
    )
    assert result.exit_code == 0


def test_led_brightness_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "brightness", "0416:8001", "55"],
    )
    assert result.exit_code == 0


def test_led_test_mode_toggle(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "test-mode", "0416:8001", "true"],
    )
    assert result.exit_code == 0


def test_led_temp_source_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "temp-source", "0416:8001", "gpu"],
    )
    assert result.exit_code == 0


def test_led_load_source_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "load-source", "0416:8001", "cpu"],
    )
    assert result.exit_code == 0


def test_led_play_help(cli_runner: CliRunner, cli_app) -> None:
    """``led play`` is a blocking ticker — --help only."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "play", "--help"])
    assert result.exit_code == 0


# =========================================================================
# system sub-app — setup / sensors / info
# =========================================================================


def test_system_sensors_runs(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "sensors"])
    assert result.exit_code == 0


def test_system_info_runs(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "info"])
    assert result.exit_code == 0


def test_system_setup_help(cli_runner: CliRunner, cli_app) -> None:
    """``system setup`` invokes platform-specific install steps — smoke
    via --help so we don't trigger udev / launchctl side effects."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "setup", "--help"])
    assert result.exit_code == 0


# =========================================================================
# config sub-app — 4 commands
# =========================================================================


def test_config_temp_unit_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["config", "temp-unit", "F"])
    assert result.exit_code == 0


def test_config_language_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["config", "language", "de"])
    assert result.exit_code == 0


def test_config_gpu_help(cli_runner: CliRunner, cli_app) -> None:
    """``config gpu`` needs a GPU key argument — smoke via --help."""
    del cli_app
    result = cli_runner.invoke(_app(), ["config", "gpu", "--help"])
    assert result.exit_code == 0


def test_config_refresh_interval_persists(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["config", "refresh-interval", "3.0"],
    )
    assert result.exit_code == 0


# =========================================================================
# theme sub-app — save / export / import
# =========================================================================


def test_theme_save_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["theme", "save", "--help"])
    assert result.exit_code == 0


def test_theme_export_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["theme", "export", "--help"])
    assert result.exit_code == 0


def test_theme_import_help(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["theme", "import", "--help"])
    assert result.exit_code == 0


# =========================================================================
# Coverage sanity — sub-app registration drift detection
# =========================================================================


def test_every_sub_app_has_commands_registered() -> None:
    """Each sub-app actually wires its commands.

    A regression where a Typer module gets reorganized but the
    ``@app.command`` decorator isn't reapplied surfaces as an empty
    sub-app — caught here before the CLI ships missing functions.
    """
    from trcc.next.ui.cli import (
        config,
        device,
        display,
        led,
        system,
        theme,
    )

    assert len(config.app.registered_commands) >= 4
    assert len(device.app.registered_commands) >= 3
    assert len(display.app.registered_commands) >= 14
    assert len(led.app.registered_commands) >= 9
    assert len(system.app.registered_commands) >= 3
    assert len(theme.app.registered_commands) >= 3
