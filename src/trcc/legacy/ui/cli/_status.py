"""`trcc status` — unified overview via the Trcc command layer.

Also provides per-device snapshots (`trcc lcd-snapshot`, `trcc led-snapshot`)
and LED style listing (`trcc led-styles`). All use the same Trcc that GUI
and API speak.
"""
from __future__ import annotations

from dataclasses import asdict

import typer

from trcc.legacy._boot import trcc as _boot_trcc


def _echo_json(payload) -> int:
    import json as _json
    typer.echo(_json.dumps(payload, indent=2, default=str))
    return 0


def lcd_snapshot(lcd: int = 0, json_output: bool = False) -> int:
    """Print an individual LCD snapshot."""
    app = _boot_trcc()
    snap = app.lcd.snapshot(lcd)
    if json_output:
        return _echo_json(asdict(snap))
    typer.echo(f'─ LCD {lcd} ───────────────────────────────────')
    typer.echo(f'  connected:     {snap.connected}')
    typer.echo(f'  resolution:    {snap.resolution[0]}x{snap.resolution[1]}')
    typer.echo(f'  brightness:    {snap.brightness}%')
    typer.echo(f'  rotation:      {snap.rotation}°')
    typer.echo(f'  split mode:    {snap.split_mode}')
    typer.echo(f'  fit mode:      {snap.fit_mode or "—"}')
    typer.echo(f'  overlay:       {"enabled" if snap.overlay_enabled else "disabled"}')
    typer.echo(f'  playing video: {snap.playing}')
    if snap.current_theme:
        typer.echo(f'  theme:         {snap.current_theme}')
    return 0


def led_snapshot(led: int = 0, json_output: bool = False) -> int:
    """Print an individual LED snapshot."""
    app = _boot_trcc()
    snap = app.led.snapshot(led)
    if json_output:
        return _echo_json(asdict(snap))
    typer.echo(f'─ LED {led} ───────────────────────────────────')
    typer.echo(f'  connected:     {snap.connected}')
    typer.echo(f'  style id:      {snap.style_id}')
    typer.echo(f'  mode:          {snap.mode}')
    typer.echo(f'  color:         rgb{snap.color}')
    typer.echo(f'  brightness:    {snap.brightness}%')
    typer.echo(f'  global on:     {snap.global_on}')
    if snap.zones:
        typer.echo(f'  zones:         {len(snap.zones)} (sync={snap.zone_sync})')
        typer.echo(f'  selected zone: {snap.selected_zone}')
    typer.echo(f'  clock:         {"24h" if snap.clock_24h else "12h"}')
    typer.echo(f'  week starts:   {"Sunday" if snap.week_sunday else "Monday"}')
    typer.echo(f'  memory ratio:  {snap.memory_ratio}x')
    typer.echo(f'  disk index:    {snap.disk_index}')
    typer.echo(f'  test mode:     {snap.test_mode}')
    return 0


def led_styles(json_output: bool = False) -> int:
    """List every supported LED device style and its capabilities."""
    styles = _boot_trcc().led.list_styles()
    if json_output:
        return _echo_json([asdict(s) for s in styles])
    typer.echo(f'LED styles: {len(styles)}')
    for s in styles:
        typer.echo(
            f'  [{s.style_id:2d}] {s.name:18s} '
            f'segments={s.segment_count:3d} zones={s.zone_count} '
            f'modes={",".join(s.supported_modes)}',
        )
    return 0


def status(json_output: bool = False) -> int:
    """Print a full overview: app state + every connected device."""
    trcc = _boot_trcc()
    app_snap = trcc.control_center.snapshot()
    discovery = trcc.discover()
    lcd_snaps = [trcc.lcd.snapshot(i) for i in range(len(discovery.lcd_devices))]
    led_snaps = [trcc.led.snapshot(i) for i in range(len(discovery.led_devices))]

    if json_output:
        return _echo_json({
            'app': asdict(app_snap),
            'lcd_devices': [asdict(s) for s in lcd_snaps],
            'led_devices': [asdict(s) for s in led_snaps],
        })

    # Human-readable output.
    typer.echo('─ App ─────────────────────────────────────────')
    typer.echo(f'  version:          {app_snap.version}')
    typer.echo(f'  install method:   {app_snap.install_method}  (distro: {app_snap.distro})')
    typer.echo(f'  autostart:        {"on" if app_snap.autostart else "off"}')
    typer.echo(f'  temp unit:        °{app_snap.temp_unit}')
    typer.echo(f'  language:         {app_snap.language}')
    typer.echo(f'  hdd metrics:      {"on" if app_snap.hdd_enabled else "off"}')
    typer.echo(f'  refresh interval: {app_snap.refresh_interval}s')
    if app_snap.gpu_device:
        typer.echo(f'  gpu:              {app_snap.gpu_device}')

    if not lcd_snaps and not led_snaps:
        typer.echo()
        typer.echo('No devices connected.')
        return 0

    for i, s in enumerate(lcd_snaps):
        typer.echo('')
        typer.echo(f'─ LCD {i} ───────────────────────────────────────')
        typer.echo(f'  connected:     {s.connected}')
        typer.echo(f'  resolution:    {s.resolution[0]}x{s.resolution[1]}')
        typer.echo(f'  brightness:    {s.brightness}%')
        typer.echo(f'  rotation:      {s.rotation}°')
        typer.echo(f'  split mode:    {s.split_mode}')
        typer.echo(f'  overlay:       {"enabled" if s.overlay_enabled else "disabled"}')
        typer.echo(f'  playing video: {s.playing}')
        if s.current_theme:
            typer.echo(f'  theme:         {s.current_theme}')

    for i, s in enumerate(led_snaps):
        typer.echo('')
        typer.echo(f'─ LED {i} ───────────────────────────────────────')
        typer.echo(f'  connected:   {s.connected}')
        typer.echo(f'  style id:    {s.style_id}')
        typer.echo(f'  mode:        {s.mode}')
        typer.echo(f'  color:       rgb{s.color}')
        typer.echo(f'  brightness:  {s.brightness}%')
        typer.echo(f'  global on:   {s.global_on}')
        if s.zones:
            typer.echo(f'  zones:       {len(s.zones)}')
            typer.echo(f'  zone sync:   {s.zone_sync}')
        typer.echo(f'  test mode:   {s.test_mode}')

    return 0
