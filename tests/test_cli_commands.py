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


def test_led_disk_index(cli_runner: CliRunner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "disk-index", "0416:8001", "2"],
    )
    assert result.exit_code == 0
    assert "2" in result.output


def test_led_disk_index_negative_rejected(
    cli_runner: CliRunner, cli_app,
) -> None:
    del cli_app
    result = cli_runner.invoke(
        _app(), ["led", "disk-index", "0416:8001", "-1"],
    )
    assert result.exit_code != 0


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

def test_display_play_advances_an_active_video(
    cli_runner: CliRunner, cli_app, monkeypatch,
) -> None:
    """`play` now ANIMATES a video override headlessly (#239).

    #150 shipped a nag ("`play` redraws a video as a still every tick — use
    play-video") because outside the GUI nothing advanced the playback cursor,
    so `display play` sent frame 0 forever.  The real fix is to advance it: the
    play loop calls ``playback.advance()`` each tick when a playback is active
    and paces at the video's fps, so the video actually plays.  This asserts the
    cursor advances and the frame is surfaced, and that the obsolete nag is gone.

    Drives the real command body: `play` loops forever, so time.sleep is the
    seam -- raise KeyboardInterrupt there and the handler exits after one tick.
    """
    from trcc.core.results import RenderResult
    from trcc.ui.cli import display as cli_display

    class _FakePlayback:
        frames = [b"x"] * 30
        paused = False
        cursor = 3
        frame_count = 30
        fps = 15

        def __init__(self) -> None:
            self.advanced = 0

        def advance(self) -> None:
            self.advanced += 1

    fake = _FakePlayback()
    monkeypatch.setattr(cli_app.media, "playback", lambda key: fake)

    def _one_tick(_s):
        raise KeyboardInterrupt

    import time as _time
    monkeypatch.setattr(_time, "sleep", _one_tick)   # `play` does `import time` inside
    monkeypatch.setattr(
        cli_app, "dispatch",
        lambda cmd: RenderResult(ok=True, key="0416:5302", message="ok",
                               bytes_sent=153600, theme_name="Misfiled"),
    )

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
    from trcc.core.results import RenderResult
    from trcc.ui.cli import display as cli_display

    def _one_tick(_s):
        raise KeyboardInterrupt

    import time as _time
    monkeypatch.setattr(_time, "sleep", _one_tick)
    monkeypatch.setattr(
        cli_app, "dispatch",
        lambda cmd: RenderResult(ok=True, key="0416:5302", message="ok",
                               bytes_sent=153600, theme_name="Theme1"),
    )

    result = cli_runner.invoke(cli_display.app, ["play", "0416:5302"])

    out = result.stdout + (result.stderr or "")
    assert "is a video" not in out
    assert "play-video" not in out
