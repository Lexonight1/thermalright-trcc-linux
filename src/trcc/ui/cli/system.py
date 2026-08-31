"""CLI `system` group — setup, sensors, diagnostics."""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from ...core.commands import (
    CheckForUpdate,
    ControlCenterSnapshot,
    DeviceState,
    DisableAutostart,
    EnableAutostart,
    EnsureDataDownload,
    GenerateDebugReport,
    GetAutostartStatus,
    GetFirstRunStatus,
    GetPaths,
    GetPlatformInfo,
    ListDisks,
    ListFans,
    ListFonts,
    ListGpus,
    ListLanguages,
    ListSensors,
    MarkFirstRunDone,
    ReadSensors,
    RefreshAutostart,
    RunDoctor,
    RunHealthCheck,
    RunSetup,
    RunUpgrade,
    SetHddEnabled,
)
from ._ctx import emit_json, get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="System-level operations (setup, sensors, info).",
                  no_args_is_help=True)


@app.command("setup")
def setup(
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Non-interactive (assume yes to prompts)"),
) -> None:
    """Run the OS-specific setup (udev rules on Linux, WinUSB guide on Windows)."""
    log.info("cli system setup: yes=%s", yes)
    result = get_app().dispatch(RunSetup(interactive=not yes))
    typer.echo(result.message)
    for warning in result.warnings:
        typer.echo(f"  warning: {warning}", err=True)
    raise typer.Exit(code=result.exit_code)


@app.command("sensors")
def sensors() -> None:
    """Print current sensor readings."""
    log.info("cli system sensors")
    result = get_app().dispatch(ReadSensors())
    if not result.readings:
        typer.echo("No sensor readings available.")
        return
    for reading in result.readings:
        typer.echo(
            f"  {reading.sensor_id}  {reading.value:.2f} {reading.unit}"
            f"  ({reading.category})"
        )


@app.command("list-gpus")
def list_gpus() -> None:
    """List GPUs exposed by the sensors aggregator."""
    log.info("cli system list-gpus")
    result = get_app().dispatch(ListGpus())
    typer.echo(result.message)
    for g in result.gpus:
        discrete = "discrete" if g.is_discrete else "integrated"
        typer.echo(f"  {g.key:20} {g.name}  ({discrete})")


@app.command("list-fans")
def list_fans() -> None:
    """List fans the sensors aggregator exposes, with live readings.

    Read-only diagnostic (#145/#207) — snapshot() maps fans to theme slots
    automatically (GPU slot follows the picked GPU); this shows what the box
    exposes.
    """
    log.info("cli system list-fans")
    result = get_app().dispatch(ListFans())
    typer.echo(result.message)
    for f in result.fans:
        rpm = f"{f.rpm} rpm" if f.rpm is not None else "-- rpm"
        pct = f", {f.percent:.0f}%" if f.percent is not None else ""
        typer.echo(f"  {f.key:24} {f.name}  ({rpm}{pct})")


@app.command("list-fonts")
def list_fonts() -> None:
    """List font families Qt can see."""
    log.info("cli system list-fonts")
    result = get_app().dispatch(ListFonts())
    typer.echo(result.message)
    for name in result.fonts:
        typer.echo(f"  {name}")


@app.command("list-disks")
def list_disks() -> None:
    """List disk partitions (for use with `led disk-index`)."""
    log.info("cli system list-disks")
    result = get_app().dispatch(ListDisks())
    typer.echo(result.message)
    for d in result.disks:
        typer.echo(f"  [{d.index}] {d.device:20} -> {d.mountpoint}")


@app.command("list-sensors")
def list_sensors() -> None:
    """Print every sensor the platform enumerates — descriptors only.

    Read-only enumeration: no polling, no values.  Pair with
    ``system sensors`` (or ``system info --metric <prefix>``) when you
    want the current readings instead.
    """
    log.info("cli system list-sensors")
    result = get_app().dispatch(ListSensors())
    typer.echo(result.message)
    for s in result.sensors:
        unit = f" [{s.unit}]" if s.unit else ""
        label = f"  {s.label}" if s.label else ""
        typer.echo(f"  {s.sensor_id:32} {s.category:14}{unit}{label}")


@app.command("list-languages")
def list_languages() -> None:
    """List every UI language the i18n table supports."""
    log.info("cli system list-languages")
    result = get_app().dispatch(ListLanguages())
    typer.echo(result.message)
    for lang in result.languages:
        typer.echo(
            f"  {lang.code:6} {lang.name:25} "
            f"{lang.translated_keys} translated",
        )


@app.command("lang")
def lang() -> None:
    """Print the currently-active UI language code.

    Read-only — for "what language is TRCC in right now?" without
    digging through ``snapshot``.  Use ``set-language`` to change it.
    """
    log.info("cli system lang")
    # Via the bus, not .settings — AppProxy exposes dispatch() only (#249).
    typer.echo(get_app().dispatch(ControlCenterSnapshot()).language)


@app.command("doctor")
def doctor() -> None:
    """Run health checks — exits 1 on any FAIL.

    The reporter-friendly summary tells you what's wrong + how to fix
    it.  For a copy-paste GitHub-issue dump, use `system debug-report`
    instead.
    """
    log.info("cli system doctor")
    result = get_app().dispatch(RunDoctor())
    typer.echo(result.rendered)
    raise typer.Exit(code=result.exit_code)


@app.command("debug-report")
def debug_report(
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help=(
            "Write the report to this path instead of stdout.  "
            "Recommended when filing a GitHub issue — attach the file."
        ),
    ),
    log_lines: int = typer.Option(
        1000, "--log-lines",
        help="How many trailing log lines to include (default 1000).",
    ),
) -> None:
    """Generate a debug report bundle for GitHub issues."""
    log.info(
        "cli system debug-report: output=%s log_lines=%s", output, log_lines,
    )
    result = get_app().dispatch(GenerateDebugReport(
        output_path=output, log_tail_lines=log_lines,
    ))
    if output is None:
        typer.echo(result.rendered_text)
    else:
        typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("check-update")
def check_update() -> None:
    """Ask GitHub Releases whether a newer version is available."""
    log.info("cli system check-update")
    r = get_app().dispatch(CheckForUpdate())
    typer.echo(r.message)
    if r.release_url:
        typer.echo(f"  Release page: {r.release_url}")
    if not r.ok:
        raise typer.Exit(code=1)


@app.command("upgrade")
def upgrade(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip confirmation and run the upgrade subprocess.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the command that would run, don't execute it.",
    ),
) -> None:
    """Upgrade trcc-linux via the detected package manager."""
    log.info("cli system upgrade: yes=%s dry_run=%s", yes, dry_run)
    if not yes and not dry_run:
        typer.echo("Refusing to run upgrade without --yes (sudo subprocess).")
        typer.echo("Re-run with --dry-run to see the command, or --yes to confirm.")
        raise typer.Exit(code=2)
    r = get_app().dispatch(RunUpgrade(dry_run=dry_run))
    typer.echo(r.message)
    if r.stdout:
        typer.echo(r.stdout)
    if r.stderr:
        typer.echo(r.stderr, err=True)
    if not r.ok:
        raise typer.Exit(code=r.exit_code or 1)


@app.command("first-run-status")
def first_run_status() -> None:
    """Show whether trcc has been set up on this machine yet."""
    log.info("cli system first-run-status")
    r = get_app().dispatch(GetFirstRunStatus())
    typer.echo(r.message)
    typer.echo(f"  marker: {r.marker_path}")


@app.command("mark-setup-done")
def mark_setup_done() -> None:
    """Tell trcc the welcome flow has been completed."""
    log.info("cli system mark-setup-done")
    r = get_app().dispatch(MarkFirstRunDone())
    typer.echo(r.message)


@app.command("health")
def health() -> None:
    """Quick read-only health report — same checks as `doctor`, no exit code."""
    log.info("cli system health")
    result = get_app().dispatch(RunHealthCheck())
    typer.echo(result.message)
    for c in result.checks:
        typer.echo(f"  [{c.severity:4}] {c.name:22}  {c.message}")


@app.command("hdd-enabled")
def hdd_enabled(
    state: str = typer.Argument(..., help="'on' or 'off'"),
) -> None:
    """Toggle inclusion of HDD metrics in sensor broadcasts."""
    log.info("cli system hdd-enabled: state=%s", state)
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        SetHddEnabled(enabled=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("snapshot")
def snapshot(
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON instead of human text.",
    ),
) -> None:
    """Print the AppSettings snapshot (language, GPU, refresh interval)."""
    log.info("cli system snapshot: json_output=%s", json_output)
    r = get_app().dispatch(ControlCenterSnapshot())
    if json_output:
        emit_json(r)
        raise typer.Exit(code=0 if r.ok else 1)
    typer.echo(r.message)
    typer.echo(f"  language          {r.language}")
    typer.echo(f"  temp_unit         {r.temp_unit}")
    typer.echo(f"  active_gpu        {r.active_gpu}")
    typer.echo(f"  refresh_interval  {r.refresh_interval_s}s")


def _show_platform_info() -> None:
    """Shared body for ``info``-platform / ``platform-info`` commands."""
    r = get_app().dispatch(GetPlatformInfo())
    typer.echo(f"Distro:   {r.distro_name}")
    typer.echo(f"Install:  {r.install_method}")
    typer.echo(f"Config:   {r.config_dir}")
    typer.echo(f"Data:     {r.data_dir}")
    typer.echo(f"Logs:     {r.log_file}")
    if r.permission_warnings:
        typer.echo("\nWarnings:")
        for w in r.permission_warnings:
            typer.echo(f"  {w}")
    else:
        typer.echo("\nPermissions: OK")


@app.command("platform-info")
def platform_info() -> None:
    """Show platform info (distro, install method, config dir, permissions)."""
    log.info("cli system platform-info")
    _show_platform_info()


@app.command("info")
def info(
    metric: str | None = typer.Option(
        None, "--metric", "-m",
        help=(
            "Filter readings whose ``sensor_id`` startswith this prefix "
            "(e.g. cpu, gpu, mem, disk, net, fan, time)."
        ),
    ),
) -> None:
    """Show current sensor metrics (CPU/GPU/fan/disk/net readings).

    Mirrors legacy ``trcc info`` — dispatches :class:`ReadSensors` and
    prints each reading.  Use ``--metric <prefix>`` to narrow the
    output; pass no args for everything.  For paths / install info /
    permissions, see :command:`trcc system platform-info`.
    """
    log.info("cli system info: metric=%s", metric)
    result = get_app().dispatch(ReadSensors())
    typer.echo(result.message)
    needle = metric.lower() if metric else None
    for reading in result.readings:
        if needle and not reading.sensor_id.lower().startswith(needle):
            continue
        unit = f" {reading.unit}" if reading.unit else ""
        label = f"  {reading.label}" if reading.label else ""
        typer.echo(f"  {reading.sensor_id:32} {reading.value:>10.2f}{unit}{label}")


@app.command("hid-debug")
def hid_debug(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Connect to *key* + print handshake details for a GitHub issue paste.

    Composes :class:`ConnectDevice` (returns handshake bytes + parsed
    resolution / model id / serial) and :class:`LcdSnapshot` to dump the
    persisted state.  Output is plain text — copy + paste-friendly.
    """
    log.info("cli system hid-debug: key=%s", key)
    from ...core.commands import ConnectDevice, LcdSnapshot

    app_obj = get_app()
    typer.echo(f"HID/LCD diagnostic for {key}")
    typer.echo("=" * 60)
    conn = app_obj.dispatch(ConnectDevice(key=key))
    typer.echo(f"Handshake: {conn.message}")
    if not conn.ok:
        raise typer.Exit(code=1)
    if conn.handshake:
        h = conn.handshake
        typer.echo(f"  resolution: {h.resolution[0]}x{h.resolution[1]}")
        typer.echo(f"  model_id:   {h.model_id}")
        if h.serial:
            typer.echo(f"  serial:     {h.serial}")
        if getattr(h, "sub_byte", None) is not None:
            typer.echo(f"  sub_byte:   {h.sub_byte}")

    snap = app_obj.dispatch(LcdSnapshot(key=key))
    typer.echo("")
    typer.echo("Persisted state:")
    typer.echo(f"  orientation:     {snap.orientation}")
    typer.echo(f"  brightness:      {snap.brightness}%")
    typer.echo(f"  current_theme:   {snap.current_theme}")
    typer.echo(f"  overlay_enabled: {snap.overlay_enabled}")
    typer.echo("")
    typer.echo("=" * 60)
    typer.echo("Copy the output above into a GitHub issue.")


@app.command("led-debug")
def led_debug(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    test_colors: bool = typer.Option(
        False, "--test-colors",
        help="After handshake, enable the 4-color test cycle (Ctrl-C to stop).",
    ),
) -> None:
    """LED device handshake + zone/segment dump + optional test cycle.

    Composes :class:`ConnectDevice` and :class:`LedSnapshot` for a one-
    shot diagnostic.  Pass ``--test-colors`` to cycle the device's
    test pattern so you can confirm wire-up visually.
    """
    log.info("cli system led-debug: key=%s test_colors=%s", key, test_colors)
    import signal

    from ...core.commands import ConnectDevice, EnableLedTestMode, LedSnapshot

    app_obj = get_app()
    typer.echo(f"LED diagnostic for {key}")
    typer.echo("=" * 60)
    conn = app_obj.dispatch(ConnectDevice(key=key))
    typer.echo(f"Handshake: {conn.message}")
    if not conn.ok:
        raise typer.Exit(code=1)

    snap = app_obj.dispatch(LedSnapshot(key=key))
    typer.echo("")
    typer.echo("Persisted state:")
    typer.echo(f"  mode:            {snap.mode}")
    typer.echo(
        f"  color:           "
        f"#{snap.color[0]:02x}{snap.color[1]:02x}{snap.color[2]:02x}",
    )
    typer.echo(f"  brightness:      {snap.brightness}%")
    typer.echo(f"  global_on:       {snap.global_on}")
    typer.echo(f"  zones:           {snap.zone_count}")
    typer.echo(f"  segments:        {snap.segment_count}")
    typer.echo(f"  zone_sync:       {snap.zone_sync}")

    if test_colors:
        typer.echo("")
        typer.echo("Test colors enabled — Ctrl-C to stop.")
        result = app_obj.dispatch(EnableLedTestMode(key=key, enabled=True))
        typer.echo(result.message)
        if not result.ok:
            raise typer.Exit(code=1)
        stopped = {"flag": False}

        def _handle(*_args: object) -> None:
            stopped["flag"] = True

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        while not stopped["flag"]:
            signal.pause()
        stop = app_obj.dispatch(EnableLedTestMode(key=key, enabled=False))
        typer.echo(stop.message)

    typer.echo("")
    typer.echo("=" * 60)
    typer.echo("Copy the output above into a GitHub issue.")


@app.command("download")
def download(
    width: int = typer.Argument(..., min=1, help="Display width (px), e.g. 320"),
    height: int = typer.Argument(..., min=1, help="Display height (px), e.g. 320"),
) -> None:
    """Pre-fetch the theme + cloud + mask archives for a resolution.

    DiscoverDevices runs this implicitly the first time a device of a
    given resolution attaches.  Call it directly to populate the local
    cache while you have network — handy for headless setups that'll
    later run offline.  Idempotent.
    """
    log.info("cli system download: width=%s height=%s", width, height)
    result = get_app().dispatch(
        EnsureDataDownload(width=width, height=height),
    )
    typer.echo(result.message)
    typer.echo(
        f"  themes: {'ok' if result.themes_ok else 'FAIL'}\n"
        f"  web:    {'ok' if result.web_ok else 'FAIL'}\n"
        f"  masks:  {'ok' if result.masks_ok else 'FAIL'}",
    )
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("list-endpoints")
def list_endpoints() -> None:
    """Enumerate every HTTP route the REST API exposes.

    Builds the FastAPI app (no uvicorn) and walks its router so the
    output reflects what ``trcc api`` / ``trcc serve`` would serve.
    """
    log.info("cli system list-endpoints")
    from ...ui.api.main import build_app

    api_app = build_app()
    rows: list[tuple[str, str]] = []
    for route in api_app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        for method in sorted(m for m in methods if m != "HEAD"):
            rows.append((method, path))
    rows.sort()
    for method, path in rows:
        typer.echo(f"  {method:6} {path}")
    typer.echo(f"\n{len(rows)} endpoint(s).")


# ── Autostart subcommands ────────────────────────────────────────────

autostart_app = typer.Typer(
    help="Manage auto-launch-on-login (XDG .desktop on Linux).",
    no_args_is_help=True,
)
app.add_typer(autostart_app, name="autostart")


@autostart_app.command("status")
def autostart_status() -> None:
    """Show whether auto-launch-on-login is enabled."""
    log.info("cli system autostart status")
    r = get_app().dispatch(GetAutostartStatus())
    state = "enabled" if r.enabled else "disabled"
    typer.echo(f"Autostart: {state}")
    if r.path:
        typer.echo(f"Path:      {r.path}")


@autostart_app.command("enable")
def autostart_enable() -> None:
    """Install the autostart entry (per-user, no sudo required)."""
    log.info("cli system autostart enable")
    r = get_app().dispatch(EnableAutostart())
    typer.echo(r.message)
    typer.echo(f"Path: {r.path}")
    if not r.enabled:
        raise typer.Exit(code=1)


@autostart_app.command("disable")
def autostart_disable() -> None:
    """Remove the autostart entry."""
    log.info("cli system autostart disable")
    r = get_app().dispatch(DisableAutostart())
    typer.echo(r.message)


@autostart_app.command("refresh")
def autostart_refresh() -> None:
    """Re-render an existing entry so it picks up a new launch path.

    The repair for a moved install (#201): an entry written by an older
    install keeps its old ``Exec=`` forever.  Does NOT enable autostart —
    with no entry installed it reports so and changes nothing.
    """
    log.info("cli system autostart refresh")
    r = get_app().dispatch(RefreshAutostart())
    typer.echo(r.message)
    if r.path:
        typer.echo(f"Path: {r.path}")


@app.command("paths")
def paths(
    resolution: str = typer.Option(
        "", "--resolution", "-r",
        help="WxH (e.g. 854x480) — also show the per-resolution theme/mask dirs",
    ),
    key: str = typer.Option(
        "", "--key", "-k",
        help="Device key (e.g. 0402:3922) — use that cooler's own artwork "
             "libraries, and its resolution when --resolution is omitted",
    ),
) -> None:
    """Show where this install keeps config, data, logs and user content.

    Answers "where did my theme go?" and "which log do I attach to an issue?"
    without the user guessing at ``~/.trcc`` versus ``~/.trcc-user``.

    With ``--key`` the answer is that device's: the resolution comes from its
    handshake (oriented) and the theme/cloud dirs name its per-SKU library, so
    the output points at the directories the app actually opens for it.
    """
    log.info("cli system paths: resolution=%s key=%s",
             resolution or "(none)", key or "(none)")
    size: tuple[int, int] | None = None
    if resolution:
        try:
            w, h = (int(v) for v in resolution.lower().split("x", 1))
            size = (w, h)
        except ValueError:
            log.warning("cli system paths: bad --resolution %r "
                        "(expected WxH, e.g. 854x480)", resolution)
            typer.echo(f"Bad --resolution {resolution!r}; expected WxH e.g. 854x480")
            raise typer.Exit(code=1) from None
    app_obj = get_app()
    if key:
        # Ask the bus, never ``app.devices`` — that attribute is absent on the
        # AppProxy a daemon-mode client holds (#249).  A one-shot CLI does not
        # coldplug, so without this the answer would quietly be the generic
        # one and read as if it were the device's.
        state = app_obj.dispatch(DeviceState(key=key))
        if not state.ok or not state.connected:
            typer.echo(
                f"Device {key} not connected — connect first so we can read "
                "its artwork libraries (or omit --key for the generic paths)",
                err=True,
            )
            raise typer.Exit(code=1)
    result = app_obj.dispatch(GetPaths(resolution=size, key=key))
    for field in (
        "config_dir", "data_dir", "user_content_dir", "user_data_dir",
        "log_file", "uploads_dir", "media_player_dir", "screencast_dir",
        "theme_dir", "user_theme_dir", "user_mask_dir",
        "user_background_dir", "cloud_theme_dir", "cloud_mask_dir",
    ):
        value = getattr(result, field)
        # None means "not asked for" — a resolution-scoped dir with no
        # resolution given.  Print nothing rather than an empty line that
        # would read as "this directory is unset".
        if value is not None:
            typer.echo(f"{field:22} {value}")
