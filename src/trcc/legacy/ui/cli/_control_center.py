"""Control Center CLI commands — app-level settings + updates.

Every command goes through Trcc.control_center, same surface GUI/API use.
These commands don't need devices (autostart, language, etc.) so they
build Trcc without device discovery where possible.
"""
from __future__ import annotations

from dataclasses import asdict

import typer

from trcc.legacy._boot import trcc


def _emit(result) -> int:
    if result.exit_code == 0:
        typer.echo(result.format())
    else:
        typer.echo(result.format(), err=True)
    return result.exit_code


# =========================================================================
# Settings
# =========================================================================

def set_temp_unit(unit: str) -> int:
    """Set app-wide temperature unit ('C' or 'F')."""
    return _emit(trcc().control_center.set_temp_unit(unit))


def set_language(lang: str) -> int:
    """Set app language (ISO 639-1 code, e.g. 'en', 'de', 'zh')."""
    return _emit(trcc().control_center.set_language(lang))


def set_autostart(enabled: bool) -> int:
    """Enable or disable autostart on login."""
    return _emit(trcc().control_center.set_autostart(enabled))


def set_hdd_enabled(enabled: bool) -> int:
    """Enable or disable HDD metrics collection."""
    return _emit(trcc().control_center.set_hdd_enabled(enabled))


def set_refresh_interval(seconds: int) -> int:
    """Set metrics refresh interval (1–100 seconds)."""
    return _emit(trcc().control_center.set_metrics_refresh(seconds))


def set_gpu_device(gpu_key: str) -> int:
    """Set the GPU device key used for metrics."""
    return _emit(trcc().control_center.set_gpu_device(gpu_key))


# =========================================================================
# Updates
# =========================================================================

def check_update() -> int:
    """Check GitHub for a newer release. Exits 0 on success (updated or not)."""
    result = trcc().control_center.check_for_update()
    if result.exit_code != 0:
        typer.echo(result.format(), err=True)
        return result.exit_code
    if result.update_available:
        typer.echo(
            f"Update available: {result.current_version} → {result.latest_version}",
        )
        assets = result.assets
        if assets:
            typer.echo("Download URLs:")
            for mgr, url in assets.items():
                typer.echo(f"  {mgr}: {url}")
    else:
        typer.echo(f"Up to date ({result.current_version})")
    return 0


def run_update() -> int:
    """Download + install the newest release via the detected package method."""
    return _emit(trcc().control_center.run_upgrade())


# =========================================================================
# Snapshots / listing
# =========================================================================

def app_snapshot(json_output: bool = False) -> int:
    """Print the Control Center state."""
    snap = trcc().control_center.snapshot()
    if json_output:
        import json as _json
        typer.echo(_json.dumps(asdict(snap), indent=2, default=str))
        return 0
    typer.echo(f'version:          {snap.version}')
    typer.echo(f'install method:   {snap.install_method} (distro: {snap.distro})')
    typer.echo(f'autostart:        {"on" if snap.autostart else "off"}')
    typer.echo(f'temp unit:        °{snap.temp_unit}')
    typer.echo(f'language:         {snap.language}')
    typer.echo(f'hdd metrics:      {"on" if snap.hdd_enabled else "off"}')
    typer.echo(f'refresh interval: {snap.refresh_interval}s')
    if snap.gpu_device:
        typer.echo(f'gpu device:       {snap.gpu_device}')
    if snap.gpu_list:
        typer.echo('available gpus:')
        for key, name in snap.gpu_list:
            marker = ' *' if key == snap.gpu_device else ''
            typer.echo(f'  {key} — {name}{marker}')
    return 0


def list_gpus() -> int:
    """List available GPUs."""
    gpus = trcc().control_center.list_gpus()
    if not gpus:
        typer.echo('No GPUs detected.')
        return 0
    for key, name in gpus:
        typer.echo(f'{key}\t{name}')
    return 0


def list_sensors() -> int:
    """List discovered hardware sensors."""
    sensors = trcc().control_center.list_sensors()
    if not sensors:
        typer.echo('No sensors discovered.')
        return 0
    for s in sensors:
        typer.echo(f'{s.id}\t{s.name} ({s.category}, {s.unit})')
    return 0
