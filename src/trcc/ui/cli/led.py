"""CLI `led` group — set LED colors on RGB LED controllers."""
from __future__ import annotations

import logging

import typer

from ...core._colors import parse_hex
from ...core.commands import (
    EnableLedTestMode,
    InitializeLed,
    LedSnapshot,
    ListLedModes,
    ListLedStyles,
    RenderLed,
    SelectZone,
    SetClockFormat,
    SetDiskIndex,
    SetLedBrightness,
    SetLedColor,
    SetLedColors,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
    SetLedZoneBrightness,
    SetLedZoneColor,
    SetLedZoneMode,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
    ToggleSegment,
)
from ...core.led_models import LEDMode
from ._ctx import get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="RGB LED control.", no_args_is_help=True)


def _parse_hex_color(raw: str) -> tuple[int, int, int]:
    """Parse '#rrggbb' or 'rrggbb' → (r, g, b); typer.BadParameter on miss."""
    try:
        r, g, b, _a = parse_hex(raw)
    except ValueError as e:
        raise typer.BadParameter(f"Invalid hex color: {raw!r}") from e
    return (r, g, b)


def _parse_mode(raw: str) -> LEDMode:
    """Parse a mode name (case-insensitive) into a LEDMode."""
    key = raw.upper()
    try:
        return LEDMode[key]
    except KeyError as e:
        names = ", ".join(m.name.lower() for m in LEDMode)
        raise typer.BadParameter(
            f"Unknown LED mode {raw!r}; expected one of: {names}"
        ) from e


@app.command("set-colors")
def set_colors(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    colors: list[str] = typer.Argument(..., help="Hex colors (#rrggbb), one per LED"),
    brightness: int = typer.Option(100, "--brightness", "-b",
                                   help="Global brightness 0–100"),
    off: bool = typer.Option(False, "--off",
                             help="Force all LEDs off (overrides colors)"),
) -> None:
    """Push a full LED color update."""
    log.info(
        "cli led set-colors: key=%s colors=%s brightness=%s off=%s",
        key, colors, brightness, off,
    )
    parsed = [_parse_hex_color(c) for c in colors]
    result = get_app().dispatch(SetLedColors(
        key=key, colors=parsed,
        global_on=not off, brightness=brightness,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("render")
def render(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    color: str = typer.Option(None, "--color", "-c",
                              help="Override hex color (#rrggbb); omit to use the saved color"),
    phase: int = typer.Option(0, "--phase", "-p",
                              help="Rotation phase for multi-phase displays"),
) -> None:
    """Render one LED frame from current settings + sensors and send.

    Reads the device's saved mode / color / brightness from Settings,
    advances the engine's phase counters on ``app.led_runtime``, and
    sends one tick.  Pass ``--color`` to override the saved color
    (treated as STATIC at full brightness — diagnostic shape).
    """
    log.info("cli led render: key=%s color=%s phase=%s", key, color, phase)
    override = _parse_hex_color(color) if color else None
    result = get_app().dispatch(RenderLed(
        key=key, color=override, phase=phase,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("mode")
def set_mode(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    mode: str = typer.Argument(
        ..., help="One of: static, breathing, colorful, rainbow, temp_linked, load_linked",
    ),
) -> None:
    """Set the LED animation mode (persists)."""
    log.info("cli led mode: key=%s mode=%s", key, mode)
    result = get_app().dispatch(SetLedMode(key=key, mode=_parse_mode(mode)))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("color")
def set_color(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    color: str = typer.Argument(..., help="Hex color (#rrggbb)"),
) -> None:
    """Set the LED color used by STATIC / BREATHING / COLORFUL modes."""
    log.info("cli led color: key=%s color=%s", key, color)
    parsed = _parse_hex_color(color)
    result = get_app().dispatch(SetLedColor(key=key, color=parsed))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("brightness")
def set_brightness(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    percent: int = typer.Argument(..., min=0, max=100, help="Brightness 0-100"),
) -> None:
    """Set the global LED brightness (persists)."""
    log.info("cli led brightness: key=%s percent=%s", key, percent)
    result = get_app().dispatch(SetLedBrightness(key=key, percent=percent))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("test-mode")
def test_mode(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    on: bool = typer.Argument(..., help="Enable (true) or disable (false)"),
) -> None:
    """Toggle the 4-color diagnostic test cycle."""
    log.info("cli led test-mode: key=%s on=%s", key, on)
    result = get_app().dispatch(EnableLedTestMode(key=key, enabled=on))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("temp-source")
def temp_source(
    key: str = typer.Argument(..., help="LED device key"),
    source: str = typer.Argument(..., help="'cpu' or 'gpu'"),
) -> None:
    """Pick the sensor source for TEMP_LINKED mode."""
    log.info("cli led temp-source: key=%s source=%s", key, source)
    result = get_app().dispatch(SetLedTempSource(key=key, source=source))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("load-source")
def load_source(
    key: str = typer.Argument(..., help="LED device key"),
    source: str = typer.Argument(..., help="'cpu' or 'gpu'"),
) -> None:
    """Pick the sensor source for LOAD_LINKED mode."""
    log.info("cli led load-source: key=%s source=%s", key, source)
    result = get_app().dispatch(SetLedLoadSource(key=key, source=source))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("toggle")
def toggle(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    state: str = typer.Argument(
        ..., help="'on' or 'off' (or use --zone N to target one zone)",
    ),
    zone: int | None = typer.Option(
        None, "--zone", "-z",
        help="Toggle a single zone (omit for global toggle)",
    ),
) -> None:
    """Turn the LED device (or one zone) on/off."""
    log.info("cli led toggle: key=%s state=%s zone=%s", key, state, zone)
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    on = state.lower() == "on"
    result = get_app().dispatch(ToggleLed(key=key, on=on, zone=zone))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("zone-color")
def zone_color(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    zone: int = typer.Argument(..., help="Zone index (0-based)"),
    color: str = typer.Argument(..., help="Hex color (#rrggbb)"),
) -> None:
    """Set one zone's persistent color."""
    log.info("cli led zone-color: key=%s zone=%s color=%s", key, zone, color)
    parsed = _parse_hex_color(color)
    result = get_app().dispatch(
        SetLedZoneColor(key=key, zone=zone, color=parsed),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("zone-mode")
def zone_mode(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    zone: int = typer.Argument(..., help="Zone index (0-based)"),
    mode: str = typer.Argument(
        ..., help="One of: static, breathing, colorful, rainbow, temp_linked, load_linked",
    ),
) -> None:
    """Set one zone's persistent animation mode."""
    log.info("cli led zone-mode: key=%s zone=%s mode=%s", key, zone, mode)
    result = get_app().dispatch(
        SetLedZoneMode(key=key, zone=zone, mode=_parse_mode(mode)),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("zone-brightness")
def zone_brightness(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    zone: int = typer.Argument(..., help="Zone index (0-based)"),
    percent: int = typer.Argument(..., min=0, max=100, help="Brightness 0-100"),
) -> None:
    """Set one zone's persistent brightness."""
    log.info(
        "cli led zone-brightness: key=%s zone=%s percent=%s",
        key, zone, percent,
    )
    result = get_app().dispatch(
        SetLedZoneBrightness(key=key, zone=zone, percent=percent),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("zone-sync")
def zone_sync(
    key: str = typer.Argument(..., help="LED device key"),
    state: str = typer.Argument(..., help="'on' or 'off'"),
    interval: int | None = typer.Option(
        None, "--interval", "-i", min=1,
        help="Set ticks-per-rotation alongside the toggle",
    ),
) -> None:
    """Toggle the zone-sync carousel (optionally set the interval)."""
    log.info(
        "cli led zone-sync: key=%s state=%s interval=%s",
        key, state, interval,
    )
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    app_obj = get_app()
    result = app_obj.dispatch(
        SetLedZoneSync(key=key, enabled=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    if interval is not None:
        ir = app_obj.dispatch(SetLedZoneSyncInterval(key=key, ticks=interval))
        typer.echo(ir.message)
        if not ir.ok:
            raise typer.Exit(code=1)


@app.command("select-zone")
def select_zone(
    key: str = typer.Argument(..., help="LED device key"),
    zone: int = typer.Argument(..., help="Zone index to select"),
) -> None:
    """Set the currently-selected zone (UI state)."""
    log.info("cli led select-zone: key=%s zone=%s", key, zone)
    result = get_app().dispatch(SelectZone(key=key, zone=zone))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("toggle-segment")
def toggle_segment(
    key: str = typer.Argument(..., help="LED device key"),
    index: int = typer.Argument(..., help="Segment index"),
    state: str = typer.Argument(..., help="'on' or 'off'"),
) -> None:
    """Flip one segment on/off (segment-display devices)."""
    log.info(
        "cli led toggle-segment: key=%s index=%s state=%s",
        key, index, state,
    )
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        ToggleSegment(key=key, index=index, on=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("list-styles")
def list_styles() -> None:
    """List every LED style registered in the PM byte registry."""
    log.info("cli led list-styles")
    result = get_app().dispatch(ListLedStyles())
    typer.echo(result.message)
    for s in result.styles:
        sub = f" sub={s.style_sub}" if s.style_sub else ""
        typer.echo(f"  PM {s.pm_byte:3d}: {s.style:6} {s.model_name}{sub}")


@app.command("list-modes")
def list_modes() -> None:
    """List every animation mode (STATIC, BREATHING, RAINBOW, …)."""
    log.info("cli led list-modes")
    result = get_app().dispatch(ListLedModes())
    typer.echo(result.message)
    for m in result.modes:
        typer.echo(f"  {m}")


@app.command("clock-format")
def clock_format(
    key: str = typer.Argument(..., help="LED device key"),
    fmt: str = typer.Argument(..., help="'12h' or '24h'"),
) -> None:
    """Set the 12h/24h clock display for LC2-style segment devices."""
    log.info("cli led clock-format: key=%s fmt=%s", key, fmt)
    fmt_lower = fmt.lower()
    if fmt_lower not in ("12h", "24h"):
        raise typer.BadParameter(f"fmt must be '12h' or '24h', got {fmt!r}")
    result = get_app().dispatch(
        SetClockFormat(key=key, is_24h=fmt_lower == "24h"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("week-start")
def week_start(
    key: str = typer.Argument(..., help="LED device key"),
    day: str = typer.Argument(..., help="'sunday' or 'monday'"),
) -> None:
    """Pick the week-start day on devices that show a day-of-week display."""
    log.info("cli led week-start: key=%s day=%s", key, day)
    day_lower = day.lower()
    if day_lower not in ("sunday", "monday"):
        raise typer.BadParameter(
            f"day must be 'sunday' or 'monday', got {day!r}",
        )
    result = get_app().dispatch(
        SetWeekStart(key=key, sunday_first=day_lower == "sunday"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("memory-ratio")
def memory_ratio(
    key: str = typer.Argument(..., help="LED device key"),
    mode: str = typer.Argument(..., help="'ratio' (percentage) or 'absolute' (GB)"),
) -> None:
    """Choose how memory usage is shown on the LED gauge."""
    log.info("cli led memory-ratio: key=%s mode=%s", key, mode)
    mode_lower = mode.lower()
    if mode_lower not in ("ratio", "absolute", "abs", "percent", "pct", "gb"):
        raise typer.BadParameter(
            f"mode must be 'ratio' or 'absolute', got {mode!r}",
        )
    ratio_mode = mode_lower in ("ratio", "percent", "pct")
    result = get_app().dispatch(
        SetMemoryRatio(key=key, ratio_mode=ratio_mode),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("disk-index")
def disk_index(
    key: str = typer.Argument(..., help="LED device key"),
    index: int = typer.Argument(..., help="Disk index (0-based)"),
) -> None:
    """Pick which disk's read/write stats to surface."""
    log.info("cli led disk-index: key=%s index=%s", key, index)
    result = get_app().dispatch(SetDiskIndex(key=key, index=index))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("initialize")
def initialize(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
) -> None:
    """Connect + render one initial frame in a single dispatch.

    Convenience for boot scripts — equivalent to ``device connect``
    followed by ``led render``, but in one Command so the caller only
    inspects one Result.  Use this on app start; use the individual
    commands for finer control.
    """
    log.info("cli led initialize: key=%s", key)
    result = get_app().dispatch(InitializeLed(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("test-led")
def test_led(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
) -> None:
    """Print an ANSI true-color preview of the LED zones in the terminal.

    Reads the current zone color list from :class:`LedSnapshot` and
    paints each zone as a coloured square — handy for visualising
    multi-zone strips during headless debugging.
    """
    log.info("cli led test-led: key=%s", key)
    from ...services._ansi import zones_to_ansi

    result = get_app().dispatch(LedSnapshot(key=key))
    if not result.ok:
        typer.echo(result.message, err=True)
        raise typer.Exit(code=1)
    # LedSnapshotResult has `color: tuple[int, int, int]` for the
    # global LED + (optional) per-zone breakdown.  zones_to_ansi
    # accepts a list; build it from whatever per-zone state exists.
    zone_colors = getattr(result, "zone_colors", None) or [result.color]
    typer.echo(zones_to_ansi(zone_colors))
    typer.echo(f"  ({len(zone_colors)} zone(s), mode={result.mode}, "
               f"brightness={result.brightness}%)")


@app.command("snapshot")
def snapshot(
    key: str = typer.Argument(..., help="LED device key"),
) -> None:
    """Print the persisted LED state for a device."""
    log.info("cli led snapshot: key=%s", key)
    result = get_app().dispatch(LedSnapshot(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    typer.echo(f"  mode             {result.mode}")
    typer.echo(f"  color            #{result.color[0]:02x}{result.color[1]:02x}{result.color[2]:02x}")
    typer.echo(f"  brightness       {result.brightness}%")
    typer.echo(f"  global_on        {result.global_on}")
    typer.echo(f"  test_mode        {result.test_mode}")
    typer.echo(f"  temp_source      {result.temp_source}")
    typer.echo(f"  load_source      {result.load_source}")
    typer.echo(f"  zone_sync        {result.zone_sync} (interval {result.zone_sync_interval_ticks} ticks)")
    typer.echo(f"  selected_zone    {result.selected_zone}")
    typer.echo(f"  zone_count       {result.zone_count}")
    typer.echo(f"  segment_count    {result.segment_count}")


@app.command("play")
def play(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    interval: float = typer.Option(
        None, "--interval", "-i",
        help="Tick interval in seconds (default: AppSettings.refresh_interval_s)",
    ),
) -> None:
    """Run the LED render ticker until Ctrl-C.

    Mirrors ``display play`` — dispatches ``RenderLed`` every tick so
    BREATHING / COLORFUL / RAINBOW animations advance.  Stops cleanly
    on SIGINT.
    """
    log.info("cli led play: key=%s interval=%s", key, interval)
    import time

    app_obj = get_app()
    tick_s = interval if interval is not None else app_obj.settings.app.refresh_interval_s
    tick_s = max(0.05, tick_s)

    typer.echo(f"Animating LED on {key} at {tick_s:.2f}s intervals (Ctrl-C to stop)…")
    try:
        while True:
            result = app_obj.dispatch(RenderLed(key=key))
            if not result.ok:
                typer.echo(f"  tick failed: {result.message}", err=True)
                raise typer.Exit(code=1)
            time.sleep(tick_s)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
