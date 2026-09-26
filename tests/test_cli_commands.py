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

import json

import pytest
from typer.testing import CliRunner


def _json_from_output(output: str) -> dict:
    """Parse the JSON object from CLI output.

    Real ``--json`` runs emit clean JSON on stdout (logs go to stderr), but
    CliRunner mixes the INFO log lines in — the JSON object is the trailing
    ``{...}`` block, so slice from the first brace.
    """
    return json.loads(output[output.index("{"):])


def _app():
    """Import the top-level Typer app inside each test so the
    ``cli_app`` fixture's platform override is in place first."""
    from trcc.ui.cli.main import app
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


def test_gui_resume_starts_hidden(cli_runner: CliRunner, cli_app, monkeypatch) -> None:
    """``gui --resume`` launches the GUI hidden in the tray (#201/#195).

    Patches ``launch`` so no Qt window is built — we only assert the flag
    threads through to ``start_hidden``.
    """
    del cli_app
    captured: dict[str, object] = {}

    def _fake_launch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    import trcc.ui.gui as gui_pkg
    monkeypatch.setattr(gui_pkg, "launch", _fake_launch)

    result = cli_runner.invoke(_app(), ["gui", "--resume"])
    assert result.exit_code == 0
    assert captured["start_hidden"] is True


def test_gui_default_shows_window(cli_runner: CliRunner, cli_app, monkeypatch) -> None:
    """Bare ``gui`` shows the window (``start_hidden`` False) (#201)."""
    del cli_app
    captured: dict[str, object] = {}

    def _fake_launch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    import trcc.ui.gui as gui_pkg
    monkeypatch.setattr(gui_pkg, "launch", _fake_launch)

    result = cli_runner.invoke(_app(), ["gui"])
    assert result.exit_code == 0
    assert captured["start_hidden"] is False


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


def test_device_list_never_passes_a_catalog_guess_off_as_a_measurement(
    cli_runner: CliRunner, cli_app, monkeypatch,
) -> None:
    """``detect`` must not print a catalog default as if it were measured.

    ``DiscoverDevices`` never handshakes, so every resolution it reports comes
    from the static registry.  One USB id covers panels from 240x320 to
    1280x480, and #268's reporter compared this line against
    ``trcc system hid-debug`` -- which DOES handshake -- and reasonably
    concluded detection was broken.  It was the label, not the detection.

    Both branches in one invocation: ``0402:3922`` declares (320,320), and the
    LED controller ``0416:8001`` declares (0,0), which is how the registry
    already spells "not declared".  Faked at the USB boundary only -- the real
    CLI, the real Command and the real registry all run.
    """
    from trcc.core.models import DeviceInfo

    monkeypatch.setattr(
        cli_app.platform, "scan_devices",
        lambda: [DeviceInfo(vid=0x0402, pid=0x3922),
                 DeviceInfo(vid=0x0416, pid=0x8001)],
    )

    result = cli_runner.invoke(_app(), ["device", "list"])

    assert result.exit_code == 0
    assert "2 device(s) found" in result.stdout
    # The declared one is LABELLED, never bare digits.
    assert "320×320 (catalog)" in result.stdout
    assert "resolution=320×320)" not in result.stdout, (
        "a catalog default is printed as if the panel had been asked"
    )
    # The undeclared one says so instead of inventing a size.
    assert "unknown" in result.stdout
    assert "0×0" not in result.stdout
    # And the reader is told where the real number comes from.
    assert "trcc device connect" in result.stdout


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


def test_display_media_player_clear(cli_runner: CliRunner, cli_app) -> None:
    """`display media-player <key> ''` clears the source — the media-player
    toggle unified into the CLI (empty URI needs no connected device)."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "media-player", "0402:3922", ""],
    )
    assert result.exit_code == 0


def test_display_color_auto_connects_then_sends(cli_runner: CliRunner, cli_app) -> None:
    """``display color`` self-connects before sending (#150).

    Each non-daemon CLI invocation is a fresh App with no attached devices,
    so the command dispatches ConnectDevice first — a known, present device
    therefore sends successfully without a separate ``device connect`` step.
    """
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "color", "0402:3922", "ff0000"],
    )
    assert result.exit_code == 0


def test_display_load_image_auto_connects_then_sends(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """``display load-image`` self-connects before rendering (#150).

    LoadImage → LoadTheme renders on the wire; a fresh CLI process holds no
    attached device, so without the ensure_connected the command failed
    "not connected".  A known device now loads the image successfully.
    """
    del cli_app
    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")  # header only — _CliRenderer ignores content
    result = cli_runner.invoke(
        _app(), ["display", "load-image", "0402:3922", str(image)],
    )
    assert result.exit_code == 0


def test_theme_cloud_load_attaches_before_load(
    cli_runner: CliRunner, cli_app, monkeypatch,
) -> None:
    """``theme cloud-load`` dispatches EnsureConnected before the wire load (#150).

    The reporter's "Not attached: 0402:3922" was cloud-load skipping the
    attach that display commands already do.  We spy the App's dispatch to
    assert the attach lands first, short-circuiting the actual network
    download at the LoadCloudTheme boundary.
    """
    from trcc.core.commands import LoadCloudTheme
    from trcc.core.commands.theme import CloudThemeLoadResult

    seen: list[str] = []
    real_dispatch = cli_app.dispatch

    def spy(cmd):
        seen.append(type(cmd).__name__)
        if isinstance(cmd, LoadCloudTheme):
            return CloudThemeLoadResult(
                ok=True, key=cmd.key, theme_id=cmd.theme_id,
                theme_path="", message="ok",
            )
        return real_dispatch(cmd)

    monkeypatch.setattr(cli_app, "dispatch", spy)
    cli_runner.invoke(_app(), ["theme", "cloud-load", "0402:3922", "a001"])

    assert "EnsureConnected" in seen
    assert seen.index("EnsureConnected") < seen.index("LoadCloudTheme")


def test_display_color_unknown_device_fails(cli_runner: CliRunner, cli_app) -> None:
    """An unknown VID:PID can't connect, so ``display color`` exits non-zero
    with the connect failure surfaced (not a silent send)."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "color", "dead:beef", "ff0000"],
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


def test_led_color_on_an_lcd_key_fails_loudly(
    cli_runner: CliRunner, cli_app,
) -> None:
    """The reporter's own command (#252), end to end through the real edge.

    ``trcc led color <lcd-key> <hex>`` used to print "LED color set to
    #ffffff" and exit 0 on a device with no LED hardware.  The gate lives in
    the Command, so this asserts the whole chain: Command refuses ->
    ``dispatch_echo`` sees ok=False -> non-zero exit for scripts.
    """
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "color", "0402:3922", "ffffff"],
    )
    assert result.exit_code == 1
    assert "is not an LED device" in result.stdout
    assert "LED color set" not in result.stdout


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


def test_led_toggle_global_off(cli_runner: CliRunner, cli_app) -> None:
    """``led toggle <key> off`` flips global_on."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "toggle", "0416:8001", "off"])
    assert result.exit_code == 0
    assert "off" in result.output


def test_led_toggle_invalid_state_rejected(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``led toggle`` rejects anything other than 'on' or 'off'."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "toggle", "0416:8001", "maybe"])
    assert result.exit_code != 0


def test_led_zone_sync_enable(cli_runner: CliRunner, cli_app) -> None:
    """``led zone-sync <key> on`` enables zone-sync."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "zone-sync", "0416:8001", "on"])
    assert result.exit_code == 0
    assert "enabled" in result.output


def test_led_zone_sync_with_interval(cli_runner: CliRunner, cli_app) -> None:
    """``--interval`` sets both flag + tick count."""
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["led", "zone-sync", "0416:8001", "on", "--interval", "20"],
    )
    assert result.exit_code == 0
    assert "20 tick" in result.output


def test_led_select_zone(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "select-zone", "0416:8001", "2"],
    )
    assert result.exit_code == 0
    assert "Selected zone 2" in result.output


def test_led_toggle_segment(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "toggle-segment", "0416:8001", "5", "off"],
    )
    assert result.exit_code == 0
    assert "Segment 5" in result.output
    assert "off" in result.output


def test_led_list_styles(cli_runner: CliRunner, cli_app) -> None:
    """``led list-styles`` emits the PM registry."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "list-styles"])
    assert result.exit_code == 0
    # PM 1 = AX120 entry in the registry
    assert "ax120" in result.output.lower()
    # Capability columns restored (segments + zones per style).
    assert "segments=" in result.output
    assert "zones=4" in result.output   # PA120 is the 4-zone style


def test_led_list_modes(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "list-modes"])
    assert result.exit_code == 0
    assert "STATIC" in result.output
    assert "RAINBOW" in result.output


def test_led_snapshot(cli_runner: CliRunner, cli_app) -> None:
    """``led snapshot <key>`` prints persisted LED state."""
    del cli_app
    result = cli_runner.invoke(_app(), ["led", "snapshot", "0416:8001"])
    assert result.exit_code == 0
    assert "mode" in result.output
    assert "brightness" in result.output


def test_display_snapshot(cli_runner: CliRunner, cli_app) -> None:
    """``display snapshot <key>`` prints persisted LCD state."""
    del cli_app
    result = cli_runner.invoke(_app(), ["display", "snapshot", "0402:3922"])
    assert result.exit_code == 0
    assert "orientation" in result.output


def test_display_restore_theme_no_persisted(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``display restore-theme`` with nothing persisted exits non-zero."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "restore-theme", "0402:3922"],
    )
    assert result.exit_code != 0
    assert "No persisted theme" in result.output


def test_system_snapshot(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "snapshot"])
    assert result.exit_code == 0
    assert "language" in result.output
    assert "refresh_interval" in result.output


def test_led_snapshot_json(cli_runner: CliRunner, cli_app) -> None:
    """``led snapshot <key> --json`` emits parseable JSON for scripts."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "snapshot", "0416:8001", "--json"],
    )
    assert result.exit_code == 0
    data = _json_from_output(result.output)
    assert data["ok"] is True
    assert data["key"] == "0416:8001"
    assert "mode" in data
    assert "brightness" in data


def test_display_snapshot_json(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "snapshot", "0402:3922", "--json"],
    )
    assert result.exit_code == 0
    data = _json_from_output(result.output)
    assert data["ok"] is True
    assert "orientation" in data
    assert "brightness" in data


def test_system_snapshot_json(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "snapshot", "--json"])
    assert result.exit_code == 0
    data = _json_from_output(result.output)
    assert data["ok"] is True
    assert "language" in data
    assert "refresh_interval_s" in data


def test_system_list_gpus(cli_runner: CliRunner, cli_app) -> None:
    """``system list-gpus`` runs (output depends on platform fake)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "list-gpus"])
    assert result.exit_code == 0


def test_system_list_fans(cli_runner: CliRunner, cli_app) -> None:
    """``system list-fans`` runs (output depends on platform fake)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "list-fans"])
    assert result.exit_code == 0


# --- Tier 2 Commands ---------------------------------------------------------


def test_led_clock_format_24h(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "clock-format", "0416:8001", "24h"],
    )
    assert result.exit_code == 0
    assert "24h" in result.output


def test_led_clock_format_rejects_garbage(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "clock-format", "0416:8001", "nope"],
    )
    assert result.exit_code != 0


def test_led_week_start_sunday(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "week-start", "0416:8001", "sunday"],
    )
    assert result.exit_code == 0
    assert "Sunday" in result.output


def test_led_memory_ratio_ddr(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "memory-ratio", "0416:8001", "4"],
    )
    assert result.exit_code == 0
    assert "×4" in result.output


def test_led_memory_ratio_rejects_invalid(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "memory-ratio", "0416:8001", "3"],
    )
    assert result.exit_code != 0


def test_led_disk_index_is_retired(cli_runner: CliRunner, cli_app) -> None:
    """``led disk-index`` is gone; ``config disk`` is the working replacement.

    It wrote a per-LED-device INDEX that nothing behavioural ever read — the
    index addressed a psutil partition list while ``disk_temp`` came from the
    thermal list.  Asserted rather than merely deleted so the command cannot
    quietly return.
    """
    del cli_app
    assert cli_runner.invoke(
        _app(), ["led", "disk-index", "0416:8001", "2"],
    ).exit_code != 0
    assert cli_runner.invoke(
        _app(), ["config", "disk", "--help"],
    ).exit_code == 0


def test_system_hdd_enabled_on(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "hdd-enabled", "on"])
    assert result.exit_code == 0
    assert "enabled" in result.output


def test_display_background_mode_color(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "background-mode", "0402:3922", "color"],
    )
    assert result.exit_code == 0
    assert "color" in result.output


def test_display_background_mode_rejects_garbage(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "background-mode", "0402:3922", "bogus"],
    )
    assert result.exit_code != 0


def test_display_overlay_background(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "overlay-background", "0402:3922", "112233"],
    )
    assert result.exit_code == 0
    assert "112233" in result.output.lower()


# --- Tier 3 -----------------------------------------------------------------


def test_display_pause_no_playback(cli_runner: CliRunner, cli_app) -> None:
    """``pause-video`` with no active playback exits non-zero (clean error)."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "pause-video", "0402:3922", "on"],
    )
    assert result.exit_code != 0
    assert "No active video" in result.output


def test_display_seek_negative_rejected(
    cli_runner: CliRunner, cli_app,
) -> None:
    """Negative frame fails — even before playback exists the Command
    surfaces a structured error rather than crashing."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "seek-video", "0402:3922", "-1"],
    )
    assert result.exit_code != 0


def test_display_loop_no_playback(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "loop-video", "0402:3922", "off"],
    )
    assert result.exit_code != 0


def test_display_list_masks_empty(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """``list-masks --dir`` with an empty dir exits 0 + empty count."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "list-masks", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "0 mask" in result.output


def test_display_upload_mask(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """``upload-mask`` stages the source under
    ``user_mask_dir(w,h)/custom_<stem>/01.png`` + applies it.  Matches
    legacy's cloud-mask shape so legacy + next/ user masks coexist."""
    del cli_app
    src = tmp_path / "myhole.png"
    # Write a tiny PNG header so the file is non-empty + has the right ext.
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = cli_runner.invoke(
        _app(), ["display", "upload-mask", "0402:3922", str(src)],
    )
    assert result.exit_code == 0
    assert "custom_myhole" in result.output


def test_theme_delete_unknown(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """Deleting a non-existent theme exits non-zero."""
    del cli_app
    bogus = tmp_path / "definitely-not-a-theme-x9z"
    result = cli_runner.invoke(
        _app(), ["theme", "delete", str(bogus)],
    )
    assert result.exit_code != 0


def test_theme_delete_round_trip(
    cli_runner: CliRunner, cli_app, tmp_path, monkeypatch,
) -> None:
    """Create a theme via fs (at the per-resolution path the writers
    would have used), delete via CLI."""
    del cli_app
    from trcc.ui.cli import _ctx
    paths = _ctx.get_app().platform.paths()
    # Mirror what SaveTheme writes: per-resolution sub-tree under
    # user_content_dir.  CLI ``theme delete`` takes an absolute path
    # to match legacy's ``delete_theme(lcd, path)`` shape.
    user_theme = paths.user_theme_dir(320, 320)
    user_theme.mkdir(parents=True, exist_ok=True)
    target = user_theme / "Disposable"
    target.mkdir(exist_ok=True)
    (target / "trcc.json").write_text(
        '{"width": 320, "height": 320, "elements": []}',
    )
    result = cli_runner.invoke(_app(), ["theme", "delete", str(target)])
    assert result.exit_code == 0
    assert not target.exists()


def test_system_list_fonts(cli_runner: CliRunner, cli_app) -> None:
    """``system list-fonts`` exits 0 — output depends on Qt availability."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "list-fonts"])
    assert result.exit_code == 0


def test_system_list_disks(cli_runner: CliRunner, cli_app) -> None:
    """``system list-disks`` exits 0."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "list-disks"])
    assert result.exit_code == 0


# --- Tier 4 — overlay element CRUD ------------------------------------------


def test_overlay_add_text_element(cli_runner: CliRunner, cli_app) -> None:
    """``overlay-add`` round-trips a text element."""
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-add", "0402:3922", "text",
         "--x", "10", "--y", "20", "--text", "hello"],
    )
    assert result.exit_code == 0
    assert "Added overlay element" in result.output
    assert "id: el_" in result.output


def test_overlay_add_hide_unit_sets_show_unit_false(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``overlay-add --hide-unit`` creates the element with show_unit=False."""
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-add", "0402:3922", "metric",
         "--metric", "cpu:temp", "--hide-unit"],
    )
    assert result.exit_code == 0
    elements = cli_app.settings.for_device("0402:3922").user_overlay_elements
    assert len(elements) == 1
    assert elements[0].show_unit is False


def test_overlay_add_rejects_bad_type(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-add", "0402:3922", "bogus"],
    )
    assert result.exit_code != 0


def test_overlay_update_unknown_id(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-update", "0402:3922", "el_nope",
         "--x", "5"],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_overlay_delete_unknown_id(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-delete", "0402:3922", "el_nope"],
    )
    assert result.exit_code != 0


def test_overlay_round_trip_add_update_delete(
    cli_runner: CliRunner, cli_app,
) -> None:
    """Full add → update → delete cycle on one element."""
    del cli_app
    add = cli_runner.invoke(
        _app(),
        ["display", "overlay-add", "0402:3922", "text",
         "--x", "0", "--y", "0", "--text", "first", "--id", "el_test"],
    )
    assert add.exit_code == 0, add.output
    upd = cli_runner.invoke(
        _app(),
        ["display", "overlay-update", "0402:3922", "el_test",
         "--text", "second"],
    )
    assert upd.exit_code == 0, upd.output
    delete = cli_runner.invoke(
        _app(),
        ["display", "overlay-delete", "0402:3922", "el_test"],
    )
    assert delete.exit_code == 0, delete.output


def test_overlay_flash_unknown_id(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(),
        ["display", "overlay-flash", "0402:3922", "el_nope"],
    )
    assert result.exit_code != 0


# --- Tier 5 — cloud themes (no network — list is static; load mocked elsewhere) ---


def test_theme_cloud_list_all(cli_runner: CliRunner, cli_app) -> None:
    """``theme cloud-list`` emits every category and theme id (offline)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["theme", "cloud-list"])
    assert result.exit_code == 0
    # First category prefix from the static table
    assert "Gallery" in result.output
    assert "a001" in result.output


def test_theme_cloud_list_filter_category(
    cli_runner: CliRunner, cli_app,
) -> None:
    """Filtering by category shows only that prefix."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["theme", "cloud-list", "--category", "y"],
    )
    assert result.exit_code == 0
    assert "y001" in result.output


def test_theme_cloud_list_rejects_unknown_category(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["theme", "cloud-list", "--category", "zzz"],
    )
    assert result.exit_code != 0


# --- Diagnostics ------------------------------------------------------------


def test_system_doctor_runs(cli_runner: CliRunner, cli_app) -> None:
    """``system doctor`` exits 0 or 1 (FakePlatform → no FAIL expected)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "doctor"])
    assert result.exit_code in (0, 1)
    assert "checks total" in result.output


def test_system_health_lists_checks(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "health"])
    assert result.exit_code == 0
    assert "python-version" in result.output


def test_system_debug_report_to_stdout(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "debug-report"])
    assert result.exit_code == 0
    assert "## Platform" in result.output


def test_system_debug_report_to_file(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    del cli_app
    out = tmp_path / "bundle.txt"
    result = cli_runner.invoke(
        _app(),
        ["system", "debug-report", "--output", str(out), "--log-lines", "5"],
    )
    assert result.exit_code == 0
    assert out.is_file()


# --- Final backend batch ----------------------------------------------------


def test_system_upgrade_refuses_without_yes(
    cli_runner: CliRunner, cli_app,
) -> None:
    """Upgrade is a sudo subprocess — refuses to run without --yes."""
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "upgrade"])
    assert result.exit_code == 2
    assert "Refusing" in result.output


def test_system_upgrade_dry_run_emits_command(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), ["system", "upgrade", "--dry-run"])
    # Dry-run never sudos — exits 0 on systems with a detected pm,
    # non-zero only if no pm detected.  Just check it runs.
    assert result.exit_code in (0, 1)


def test_display_slideshow_off(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["display", "slideshow", "0402:3922", "off"],
    )
    assert result.exit_code == 0
    assert "Slideshow off" in result.output


def test_display_configure_slideshow(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(_app(), [
        "display", "configure-slideshow", "0402:3922",
        "alpha", "beta", "--interval", "20",
    ])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_display_keepalive_no_cached_frame(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``display keepalive --count 1`` exits non-zero when no frame cached."""
    del cli_app
    result = cli_runner.invoke(_app(), [
        "display", "keepalive", "0402:3922", "--count", "1",
    ])
    assert result.exit_code != 0


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


def test_top_level_setup_alias_delegates_to_system_setup(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``trcc setup`` is a top-level alias for ``trcc system setup`` (#194) —
    it reaches the same RunSetup path (FakePlatform.setup is a 0 no-op)."""
    del cli_app
    result = cli_runner.invoke(_app(), ["setup"])
    assert result.exit_code == 0


def test_top_level_setup_alias_registered() -> None:
    """The alias is wired on the top-level app, not just the system sub-app."""
    names = {cmd.name for cmd in _app().registered_commands}
    assert "setup" in names


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


def test_theme_list_against_empty_dir(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """``theme list --dir <empty>`` exits 0 with the empty-list message."""
    del cli_app
    result = cli_runner.invoke(
        _app(), ["theme", "list", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "0 theme(s)" in result.output


def test_theme_list_finds_a_theme(
    cli_runner: CliRunner, cli_app, tmp_path,
) -> None:
    """``theme list`` lists themes whose dir contains config.json."""
    del cli_app
    theme_dir = tmp_path / "Sample"
    theme_dir.mkdir()
    (theme_dir / "trcc.json").write_text(
        '{"width": 320, "height": 320, "elements": []}',
    )
    result = cli_runner.invoke(
        _app(), ["theme", "list", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Sample" in result.output
    assert "320x320" in result.output


# =========================================================================
# Coverage sanity — sub-app registration drift detection
# =========================================================================


def test_every_sub_app_has_commands_registered() -> None:
    """Each sub-app actually wires its commands.

    A regression where a Typer module gets reorganized but the
    ``@app.command`` decorator isn't reapplied surfaces as an empty
    sub-app — caught here before the CLI ships missing functions.
    """
    from trcc.ui.cli import (
        config,
        device,
        display,
        led,
        system,
        theme,
    )

    assert len(config.app.registered_commands) >= 4
    assert len(device.app.registered_commands) >= 3
    assert len(display.app.registered_commands) >= 32
    assert len(led.app.registered_commands) >= 21
    assert len(system.app.registered_commands) >= 13
    assert len(theme.app.registered_commands) >= 8


# ── `display play` on a VIDEO theme (#150) ────────────────────────────

def _render_dispatch_stub(*, theme_name: str, playback=None):
    """Dispatch stub for the `display play` loop.

    `play` dispatches more than one Command: RestoreDeviceState once before the
    loop, TickDisplay per tick, and ControlCenterSnapshot to read the refresh
    interval (it used to read app.settings directly, which crashed under
    TRCC_DAEMON=1 — #249).  A stub that returns RenderResult for EVERYTHING
    makes the interval read blow up with AttributeError, so answer each Command
    with its own Result type.

    ``playback`` opts into TickDisplay's contract — advance the cursor, then
    report it on the Result.  The loop reads its counters and its pacing from
    that Result now rather than from ``app.media`` (an AppProxy attribute error
    under a daemon — #249), so the stub is what proves the loop no longer needs
    a MediaService handle at all.
    """
    from trcc.core.commands import ControlCenterSnapshot, TickDisplay
    from trcc.core.results import ControlCenterSnapshotResult, RenderResult

    def _dispatch(cmd):
        if isinstance(cmd, ControlCenterSnapshot):
            return ControlCenterSnapshotResult(ok=True, refresh_interval_s=2.0)
        video: dict = {}
        if isinstance(cmd, TickDisplay) and playback is not None:
            playback.advance()
            video = {
                "cursor": playback.cursor,
                "frame_count": playback.frame_count,
                "interval_ms": max(1, int(1000 / playback.fps)),
            }
        return RenderResult(ok=True, key="0416:5302", message="ok",
                            bytes_sent=153600, theme_name=theme_name, **video)

    return _dispatch


def test_display_play_advances_an_active_video(
    cli_runner: CliRunner, cli_app, monkeypatch,
) -> None:
    """`play` now ANIMATES a video override headlessly (#239).

    #150 shipped a nag ("`play` redraws a video as a still every tick — use
    play-video") because outside the GUI nothing advanced the playback cursor,
    so `display play` sent frame 0 forever.  The real fix is to advance it —
    which ``TickDisplay`` now does App-side, so the loop dispatches ONE Command
    and reads the cursor + pacing off its Result instead of reaching for
    ``app.media`` (an AttributeError under TRCC_DAEMON=1 — #249).  This asserts
    the cursor advances and the frame is surfaced, and that the nag is gone.

    Drives the real command body: `play` loops forever, so time.sleep is the
    seam -- raise KeyboardInterrupt there and the handler exits after one tick.
    """
    from trcc.ui.cli import display as cli_display

    class _FakePlayback:
        frames = [b"x"] * 30
        paused = False
        frame_count = 30
        fps = 15

        def __init__(self) -> None:
            self.advanced = 0
            self.cursor = 2

        def advance(self) -> None:
            self.advanced += 1
            self.cursor += 1

    fake = _FakePlayback()

    def _one_tick(_s):
        raise KeyboardInterrupt

    import time as _time
    monkeypatch.setattr(_time, "sleep", _one_tick)   # `play` does `import time` inside
    monkeypatch.setattr(
        cli_app, "dispatch",
        _render_dispatch_stub(theme_name="Misfiled", playback=fake),
    )
    # The loop must NOT touch MediaService any more: blow up if it does, which
    # is exactly what an AppProxy does under a daemon.
    def _no_media(self):
        raise AssertionError(
            "`display play` reached app.media — that crashes under TRCC_DAEMON=1"
        )
    monkeypatch.setattr(type(cli_app), "media", property(_no_media), raising=False)

    result = cli_runner.invoke(cli_display.app, ["play", "0416:5302"])

    out = result.stdout + (result.stderr or "")
    assert fake.advanced >= 1, "play must advance the video cursor each tick"
    assert "video frame 3/30" in out, (
        f"exit={result.exit_code} exc={result.exception!r} out={out!r}"
    )
    assert "is a video" not in out, "the obsolete frozen-nag must be gone"


def test_display_play_stays_quiet_for_a_normal_theme(
    cli_runner: CliRunner, cli_app, monkeypatch,
) -> None:
    """No nag on a still theme — the note must mean something when it appears."""
    from trcc.ui.cli import display as cli_display

    def _one_tick(_s):
        raise KeyboardInterrupt

    import time as _time
    monkeypatch.setattr(_time, "sleep", _one_tick)
    monkeypatch.setattr(
        cli_app, "dispatch",
        _render_dispatch_stub(theme_name="Theme1"),
    )

    result = cli_runner.invoke(cli_display.app, ["play", "0416:5302"])

    out = result.stdout + (result.stderr or "")
    assert "is a video" not in out
    assert "play-video" not in out


# =========================================================================
# system paths --key — a diagnostic must not answer generically by accident
# =========================================================================


def test_system_paths_key_errors_when_the_device_is_not_connected(
    cli_runner: CliRunner, cli_app,
) -> None:
    """``--key`` on an unconnected device fails loudly instead of answering.

    A one-shot CLI does not coldplug (``App.discover_and_connect``: "one-shot
    CLI scripts skip it"), so without this guard the command would print the
    GENERIC libraries under a flag that says "this device's" -- indistinguish-
    able from a correct answer, and the user pastes it into an issue.

    The check goes through ``DeviceState``, not ``app.devices``, because that
    attribute does not exist on the AppProxy a daemon-mode client holds (#249).

    MUTATION CHECK -- delete the ``if key:`` guard in ``ui/cli/system.py`` and
    this passes with exit 0 and generic paths on stdout.
    """
    result = cli_runner.invoke(_app(), ["system", "paths", "--key", "dead:beef"])

    assert result.exit_code == 1, result.output
    assert "not connected" in result.output


def test_system_paths_without_key_still_reports_generic_dirs(
    cli_runner: CliRunner, cli_app,
) -> None:
    """No ``--key`` → the resolution-only output every existing caller uses."""
    result = cli_runner.invoke(
        _app(), ["system", "paths", "--resolution", "1600x720"],
    )

    assert result.exit_code == 0, result.output
    assert "theme1600720" in result.output
    assert "config_dir" in result.output


# =========================================================================
# system autostart — WHICH ui starts with the computer
# =========================================================================
#
# All four verbs had ZERO CLI coverage.  Measured by matching full command
# paths from the live Typer app against the argv every test passes to
# ``invoke``: 144 paths, 45 invoked, 99 never — and all four of these among
# them.  The Command beneath was tested; the wrapper was not, so `--target`
# could have been added to a verb nothing would notice breaking.


def test_autostart_enable_accepts_a_target(cli_runner, cli_app) -> None:
    result = cli_runner.invoke(
        _app(), ["system", "autostart", "enable", "--target", "daemon"],
    )
    assert result.exit_code == 0, result.output
    assert "daemon" in result.output


def test_autostart_enable_defaults_to_gui(cli_runner, cli_app) -> None:
    result = cli_runner.invoke(_app(), ["system", "autostart", "enable"])
    assert result.exit_code == 0, result.output
    assert "gui" in result.output


def test_autostart_enable_rejects_an_unknown_target(cli_runner, cli_app) -> None:
    """The refusal must name the valid set — pydantic and typer both accept
    any string here, so the Command's validation is the only guard."""
    result = cli_runner.invoke(
        _app(), ["system", "autostart", "enable", "--target", "nonesuch"],
    )
    assert result.exit_code != 0
    assert "nonesuch" in result.output
    assert "daemon" in result.output, "the valid set was not named"


def test_autostart_status_reports_the_installed_target(
    cli_runner, cli_app,
) -> None:
    cli_runner.invoke(_app(), ["system", "autostart", "enable",
                               "--target", "qtgui"])
    result = cli_runner.invoke(_app(), ["system", "autostart", "status"])
    assert result.exit_code == 0, result.output
    assert "qtgui" in result.output


def test_autostart_refresh_repairs_a_stale_entry(
    cli_runner, cli_app, fake_platform,
) -> None:
    """`autostart refresh` had NO test at all, while the API route had one.

    The verb is the user-facing half of the #201 repair, so "the api can prove
    it and the cli cannot" is the asymmetry, not a missing nicety.
    ``cli_app`` is built on this same ``fake_platform``, so the entry the CLI
    writes is the one asserted on here.
    """
    mgr = fake_platform.autostart()
    cli_runner.invoke(_app(), ["system", "autostart", "enable"])
    mgr.command = "/moved/bin/trcc"

    result = cli_runner.invoke(_app(), ["system", "autostart", "refresh"])

    assert result.exit_code == 0, result.output
    assert mgr.installed_command == "/moved/bin/trcc"


def test_autostart_refresh_reports_when_there_is_nothing_to_refresh(
    cli_runner, cli_app, fake_platform,
) -> None:
    """With no entry installed it says so and installs nothing."""
    result = cli_runner.invoke(_app(), ["system", "autostart", "refresh"])

    assert result.exit_code == 0, result.output
    assert not fake_platform.autostart().is_enabled()


# ── `--metric` spec parsing (#286) ──────────────────────────────────────────


@pytest.mark.parametrize("spec,metric,x,y,color,size", [
    # The example printed in the command's own --help.  It FAILED.
    ("cpu:temp:160,90:#ff8800:24", "cpu:temp", 160, 90, "#ff8800", 24),
    # Both optional fields omitted — the case the reporter's rsplit(":", 3)
    # could not handle, which is why the boundary is found by shape instead.
    ("cpu:temp:160,90", "cpu:temp", 160, 90, "#ffffff", 16),
    ("gpu:0:temp:10,20:#fff", "gpu:0:temp", 10, 20, "#fff", 16),
    ("gpu:amd:0:temp:1,2:#abc:32", "gpu:amd:0:temp", 1, 2, "#abc", 32),
    # A key with no colon at all must keep working.
    ("cpu_temp:5,5", "cpu_temp", 5, 5, "#ffffff", 16),
])
def test_metric_spec_keeps_colons_in_the_metric_key(
    spec: str, metric: str, x: int, y: int, color: str, size: int,
) -> None:
    """A metric key is colon-namespaced, so the key/coords boundary cannot be
    found by counting colons from either end (#286).

    `split(":")` took parts[0] as the key, so `cpu:temp:160,90` parsed the key
    as `cpu` and the coords as `temp` — making --metric unusable with every
    real sensor, the documented example included.
    """
    from trcc.ui.cli.theme import _parse_metric_spec

    assert _parse_metric_spec(spec) == {
        "metric": metric, "x": x, "y": y, "color": color, "size": size,
    }


@pytest.mark.parametrize("spec", ["bogus", "cpu:temp", "cpu:temp:notcoords", ""])
def test_metric_spec_still_rejects_garbage(spec: str) -> None:
    """Locating coords by shape must not make the parser accept anything."""
    import typer

    from trcc.ui.cli.theme import _parse_metric_spec

    with pytest.raises(typer.BadParameter):
        _parse_metric_spec(spec)


# ── #228 / #267: a one-shot command on a panel that blanks when frames stop ──


def _cli_on(tmp_path, cli_runner, spec: dict, args: list[str]) -> str:
    """Run the real CLI on one mock device; return stdout + stderr."""
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    _ctx.set_platform(MockPlatform([spec], tmp_path))
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    try:
        result = cli_runner.invoke(cli, args)
        return result.output + (result.stderr if result.stderr_bytes else "")
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None


_WARFRAME_SE = {"type": "lcd", "name": "Warframe SE", "vid": "0416",
                "pid": "5302", "pm": 58, "bcd": "0407"}


def test_a_one_shot_on_a_blanking_panel_says_how_to_keep_it_lit(
    tmp_path, cli_runner,
) -> None:
    out = _cli_on(tmp_path, cli_runner, _WARFRAME_SE,
                  ["display", "color", "0416:5302", "ff0000"])
    assert "0416:5302 goes blank when frames stop" in out, out
    # The daemon, not `display keepalive`: a new keepalive process holds the
    # saved theme, not the frame this command sent (#267).
    assert "export TRCC_DAEMON=1" in out, out
    assert "display keepalive" not in out, out


def test_a_panel_that_holds_its_image_gets_no_note(tmp_path, cli_runner) -> None:
    spec = {"type": "lcd", "name": "LCD", "vid": "0402", "pid": "3922",
            "pm": 32, "fbl": 100}
    out = _cli_on(tmp_path, cli_runner, spec,
                  ["display", "color", "0402:3922", "ff0000"])
    assert "goes blank when frames stop" not in out, out


def test_the_note_never_builds_an_app_a_command_did_not(capsys) -> None:
    """``trcc --version`` must not open USB just to decide it has nothing to say."""
    from trcc.ui.cli import _ctx

    _ctx.get_app.cache_clear()
    _ctx.warn_blanking_panels()
    assert _ctx.get_app.cache_info().currsize == 0
    assert capsys.readouterr().err == ""


def test_the_note_is_silent_in_daemon_mode(monkeypatch, capsys) -> None:
    """The daemon owns the panel and keeps streaming after the CLI exits."""
    from functools import lru_cache
    from unittest.mock import MagicMock

    from trcc.proxy import AppProxy
    from trcc.ui.cli import _ctx

    proxy = MagicMock(spec=AppProxy)

    @lru_cache(maxsize=1)
    def fake_get_app():
        return proxy

    fake_get_app()                                  # "a command built it"
    monkeypatch.setattr(_ctx, "get_app", fake_get_app)
    _ctx.warn_blanking_panels()
    proxy.dispatch.assert_not_called()
    assert capsys.readouterr().err == ""


def test_display_play_leaves_the_video_to_the_daemon(monkeypatch, cli_runner) -> None:
    """Under TRCC_DAEMON=1 the daemon's VideoLoop ticks; ticking here too would
    advance every frame twice (#249)."""
    from functools import lru_cache
    from unittest.mock import MagicMock

    from trcc.proxy import AppProxy
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    proxy = MagicMock(spec=AppProxy)
    sent: list[str] = []

    def dispatch(cmd):
        sent.append(type(cmd).__name__)
        return MagicMock(ok=True, message="ok")

    proxy.dispatch.side_effect = dispatch

    @lru_cache(maxsize=1)
    def fake_get_app():
        return proxy

    monkeypatch.setattr(_ctx, "get_app", fake_get_app)
    import trcc.ui.cli.display as display_mod
    monkeypatch.setattr(display_mod, "get_app", fake_get_app)

    result = cli_runner.invoke(cli, ["display", "play", "0402:3922"])

    assert result.exit_code == 0, result.output
    assert "daemon is playing 0402:3922" in result.output
    assert "TickDisplay" not in sent


# ── #270: a play loop that loses its panel reconnects instead of exiting ────


class _ScriptedApp:
    """Answers each Command by name from a script; records what it was sent."""

    def __init__(self, script: dict) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.sent: list[str] = []

    def dispatch(self, cmd):
        from unittest.mock import MagicMock
        name = type(cmd).__name__
        self.sent.append(name)
        answers = self.script.get(name)
        answer = answers.pop(0) if answers and len(answers) > 1 else (
            answers[0] if answers else MagicMock(ok=True, message="ok"))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run_led_play(monkeypatch, cli_runner, app, ticks_before_stop: int = 1):
    """Run `trcc led play` on *app*; stop after N successful tick sleeps."""
    import time as time_mod
    from functools import lru_cache

    from trcc.ui.cli import _ctx
    from trcc.ui.cli import led as led_mod
    from trcc.ui.cli.main import app as cli

    @lru_cache(maxsize=1)
    def fake_get_app():
        return app

    monkeypatch.setattr(_ctx, "get_app", fake_get_app)
    monkeypatch.setattr(led_mod, "get_app", fake_get_app)
    monkeypatch.setattr(led_mod, "ensure_connected", lambda a, k: None)
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        if s == 0.5 and sleeps.count(0.5) >= ticks_before_stop:
            raise KeyboardInterrupt
    monkeypatch.setattr(time_mod, "sleep", sleep)
    result = cli_runner.invoke(cli, ["led", "play", "0416:8001", "-i", "0.5"])
    return result, sleeps


def test_led_play_reconnects_a_lost_panel_and_keeps_going(monkeypatch, cli_runner) -> None:
    from unittest.mock import MagicMock

    from trcc.core.errors import DeviceNotConnectedError

    app = _ScriptedApp({
        "RenderLed": [DeviceNotConnectedError("0416:8001 not connected"),
                      MagicMock(ok=True, message="ok")],
        "DeviceState": [MagicMock(ok=True, connected=False)],
        "ResetDevice": [MagicMock(ok=True, message="reset")],
    })
    result, _ = _run_led_play(monkeypatch, cli_runner, app)
    assert result.exit_code == 0, result.output
    assert "reconnected" in result.output
    assert app.sent.count("RenderLed") == 2 and "ResetDevice" in app.sent


def test_led_play_still_exits_on_a_real_error(monkeypatch, cli_runner) -> None:
    """A failure while the panel is still connected is not a lost panel."""
    from unittest.mock import MagicMock

    app = _ScriptedApp({
        # A second tick ends the loop, so a build that wrongly reconnects
        # FAILS here (exit 0) instead of spinning forever.
        "RenderLed": [MagicMock(ok=False, message="not an LED device"),
                      KeyboardInterrupt()],
        "DeviceState": [MagicMock(ok=True, connected=True)],
    })
    result, _ = _run_led_play(monkeypatch, cli_runner, app)
    assert result.exit_code == 1
    assert "ResetDevice" not in app.sent


def test_reconnect_backs_off_and_caps(monkeypatch) -> None:
    import time as time_mod
    from unittest.mock import MagicMock

    from trcc.ui.cli import _ctx

    down = MagicMock(ok=False, message="no device")
    app = _ScriptedApp({"ResetDevice": [down] * 7 + [MagicMock(ok=True)],
                        "ConnectDevice": [down]})
    sleeps: list[float] = []
    monkeypatch.setattr(time_mod, "sleep", sleeps.append)
    _ctx.reconnect_until_back(app, "0416:8001")
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_led_play_leaves_the_leds_to_the_daemon(monkeypatch, cli_runner) -> None:
    """The daemon's LedAnimationLoop animates them; a second ticker doubles
    every effect's speed (the #249 shape, for LEDs)."""
    from functools import lru_cache
    from unittest.mock import MagicMock

    from trcc.proxy import AppProxy
    from trcc.ui.cli import _ctx
    from trcc.ui.cli import led as led_mod
    from trcc.ui.cli.main import app as cli

    proxy = MagicMock(spec=AppProxy)
    proxy.dispatch.return_value = MagicMock(ok=True, message="ok")

    @lru_cache(maxsize=1)
    def fake_get_app():
        return proxy

    monkeypatch.setattr(_ctx, "get_app", fake_get_app)
    monkeypatch.setattr(led_mod, "get_app", fake_get_app)
    monkeypatch.setattr(led_mod, "ensure_connected", lambda a, k: None)
    result = cli_runner.invoke(cli, ["led", "play", "0416:8001"])
    assert result.exit_code == 0 and "daemon is animating 0416:8001" in result.output
    assert "RenderLed" not in {type(c.args[0]).__name__ for c in proxy.dispatch.call_args_list}


def test_display_play_reconnects_a_lost_panel_too(monkeypatch, cli_runner) -> None:
    import time as time_mod
    from functools import lru_cache
    from unittest.mock import MagicMock

    from trcc.ui.cli import _ctx
    from trcc.ui.cli import display as display_mod
    from trcc.ui.cli.main import app as cli

    app = _ScriptedApp({
        "TickDisplay": [MagicMock(ok=False, message="device gone"),
                        MagicMock(ok=True, frame_count=None, bytes_sent=1,
                                  theme_name="t", message="ok")],
        "DeviceState": [MagicMock(ok=True, connected=False)],
        "ResetDevice": [MagicMock(ok=True, message="reset")],
    })

    @lru_cache(maxsize=1)
    def fake_get_app():
        return app

    monkeypatch.setattr(_ctx, "get_app", fake_get_app)
    monkeypatch.setattr(display_mod, "get_app", fake_get_app)
    monkeypatch.setattr(display_mod, "ensure_connected", lambda a, k: None)
    ticks: list[float] = []

    def sleep(sec):
        ticks.append(sec)
        if sec == 0.5:
            raise KeyboardInterrupt
    monkeypatch.setattr(time_mod, "sleep", sleep)

    result = cli_runner.invoke(cli, ["display", "play", "0402:3922", "-i", "0.5"])
    assert result.exit_code == 0, result.output
    assert "reconnected" in result.output and app.sent.count("TickDisplay") == 2
