"""LED CLI commands — thin wrappers over Trcc.led.

Every command builds a Trcc via `_boot.trcc()`, calls the corresponding
command method, prints `result.format()`, and returns `result.exit_code`.
Same surface GUI and API use — see doc/TRCC_CONTRACT.md.
"""
from __future__ import annotations

import logging

import typer

from trcc._boot import trcc
from trcc.core.models import parse_hex_color

log = logging.getLogger(__name__)


def _emit(result) -> int:
    """Print the result's one-line format and return its exit code."""
    if result.exit_code == 0:
        typer.echo(result.format())
    else:
        typer.echo(result.format(), err=True)
    return result.exit_code


# =========================================================================
# Color / mode / brightness (global)
# =========================================================================

def set_color(hex_color: str, *, led: int = 0, preview: bool = False) -> int:
    """Set LED static color from a hex string like 'ff0000'."""
    if not (rgb := parse_hex_color(hex_color)):
        typer.echo("Error: Invalid hex color. Use format: ff0000", err=True)
        return 1
    r, g, b = rgb
    result = trcc().led.set_color(led, r, g, b)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def set_mode(mode_name: str, *, led: int = 0, preview: bool = False) -> int:
    """Set LED effect mode (static, breathing, colorful, rainbow, …)."""
    result = trcc().led.set_mode(led, mode_name)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def set_led_brightness(level: int, *, led: int = 0, preview: bool = False) -> int:
    """Set LED brightness (0-100)."""
    result = trcc().led.set_brightness(led, level)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def led_off(*, led: int = 0) -> int:
    """Turn LEDs off."""
    return _emit(trcc().led.toggle(led, False))


def set_sensor_source(source: str, *, led: int = 0) -> int:
    """Set CPU/GPU sensor source for temp/load linked LED modes."""
    return _emit(trcc().led.set_sensor_source(led, source))


# =========================================================================
# Zones
# =========================================================================

def set_zone_color(zone: int, hex_color: str,
                   *, led: int = 0, preview: bool = False) -> int:
    """Set color for a specific LED zone."""
    if not (rgb := parse_hex_color(hex_color)):
        typer.echo("Error: Invalid hex color. Use format: ff0000", err=True)
        return 1
    r, g, b = rgb
    result = trcc().led.set_color(led, r, g, b, zone=zone)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def set_zone_mode(zone: int, mode_name: str,
                  *, led: int = 0, preview: bool = False) -> int:
    """Set effect mode for a specific LED zone."""
    result = trcc().led.set_mode(led, mode_name, zone=zone)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def set_zone_brightness(zone: int, level: int,
                        *, led: int = 0, preview: bool = False) -> int:
    """Set brightness for a specific LED zone (0-100)."""
    result = trcc().led.set_brightness(led, level, zone=zone)
    if preview and result.display_colors:
        from trcc.services import LEDService
        typer.echo(LEDService.zones_to_ansi(result.display_colors))
    return _emit(result)


def toggle_zone(zone: int, on: bool, *, led: int = 0) -> int:
    """Toggle a specific LED zone on/off."""
    return _emit(trcc().led.toggle(led, on, zone=zone))


def set_zone_sync(enabled: bool, *, led: int = 0,
                  interval: int | None = None) -> int:
    """Enable/disable zone sync (circulate or select-all depending on style)."""
    return _emit(trcc().led.set_zone_sync(led, enabled, interval_s=interval))


# =========================================================================
# Segments + display modes
# =========================================================================

def toggle_segment(index: int, on: bool, *, led: int = 0) -> int:
    """Toggle a specific LED segment on/off."""
    return _emit(trcc().led.toggle_segment(led, index, on))


def set_clock_format(is_24h: bool, *, led: int = 0) -> int:
    """Set LED segment display clock format (12h/24h)."""
    return _emit(trcc().led.set_clock_format(led, is_24h))


def set_temp_unit(unit) -> int:
    """Set app-wide temperature unit (affects LED segments + LCD overlay)."""
    if isinstance(unit, int):
        unit_str = 'F' if unit else 'C'
    else:
        unit_str = str(unit).upper()
    return _emit(trcc().control_center.set_temp_unit(unit_str))


def set_week_start(sunday: bool, *, led: int = 0) -> int:
    """Set week-start day for LED segment displays (LC2-family)."""
    return _emit(trcc().led.set_week_start(led, sunday))


def set_memory_ratio(ratio: int, *, led: int = 0) -> int:
    """Set DDR memory multiplier (1, 2, or 4) for LC1 memory mode."""
    return _emit(trcc().led.set_memory_ratio(led, ratio))


def set_disk_index(index: int, *, led: int = 0) -> int:
    """Select which physical disk the LF11 segment display tracks."""
    return _emit(trcc().led.set_disk_index(led, index))


def set_test_mode(enabled: bool, *, led: int = 0) -> int:
    """Toggle LED test mode (cycles through white/red/green/blue)."""
    return _emit(trcc().led.set_test_mode(led, enabled))


def select_zone(zone: int, *, led: int = 0) -> int:
    """Set which zone is 'active' for subsequent per-zone commands."""
    return _emit(trcc().led.select_zone(led, zone))


# =========================================================================
# Developer test commands (no device needed) — unchanged
# =========================================================================

def test_led(builder=None, *, mode: str | None = None, segments: int = 64,
             duration: int = 0):
    """Test LED ANSI preview with real system metrics. No device needed."""
    import time

    from trcc.core.models import LEDMode, LEDState
    from trcc.services.led import LEDService
    from trcc.services.system import get_instance
    from trcc.ui.cli import _ensure_system

    modes = {
        'static': LEDMode.STATIC,
        'breathing': LEDMode.BREATHING,
        'colorful': LEDMode.COLORFUL,
        'rainbow': LEDMode.RAINBOW,
    }

    if mode:
        key = mode.lower()
        if key not in modes:
            print(f"Unknown mode '{mode}'. Choose: {', '.join(modes)}")
            return 1
        run_modes = {key: modes[key]}
    else:
        run_modes = modes

    _ensure_system(builder)
    sys_svc = get_instance()
    from trcc._boot import trcc as _trcc
    metrics = _trcc().os.metrics

    print(f"LED ANSI Preview ({segments} segments)")
    print(f"CPU: {metrics.cpu_temp:.0f}°C {metrics.cpu_percent:.0f}%  "
          f"GPU: {metrics.gpu_temp:.0f}°C {metrics.gpu_usage:.0f}%  "
          f"MEM: {metrics.mem_percent:.0f}%")
    print("─" * 60)

    try:
        start = time.monotonic()
        for mode_name, led_mode in run_modes.items():
            state = LEDState()
            state.mode = led_mode
            state.color = (255, 0, 0) if led_mode == LEDMode.STATIC else (0, 255, 255)
            state.segment_count = segments
            state.global_on = True
            state.brightness = 100
            svc = LEDService(state=state)
            svc.update_metrics(metrics)

            animated = led_mode in (LEDMode.BREATHING, LEDMode.COLORFUL,
                                    LEDMode.RAINBOW)
            if animated:
                print(f"\n  {mode_name} (animating, Ctrl+C to skip)")
                ticks = 60 if duration == 0 else duration * 20
                for _ in range(ticks):
                    colors = svc.tick()
                    line = LEDService.zones_to_ansi(colors[:20])
                    print(f'  {line}', end='\r', flush=True)
                    if duration and (time.monotonic() - start) >= duration:
                        break
                    time.sleep(0.05)
                print()
            else:
                colors = svc.tick()
                line = LEDService.zones_to_ansi(colors[:20])
                print(f"  {mode_name:12s} {line}")

        sys_svc.stop_polling()
        print("\nDone.")
        return 0
    except KeyboardInterrupt:
        sys_svc.stop_polling()
        print("\nStopped.")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def test_lcd(builder=None, *, cols: int = 60):
    """Test LCD ANSI preview with real system metrics. No device needed."""
    from trcc.services.image import ImageService
    from trcc.services.system import get_instance
    from trcc.ui.cli import _ensure_system

    _ensure_system(builder)
    sys_svc = get_instance()
    from trcc._boot import trcc as _trcc
    metrics = _trcc().os.metrics

    print(f"LCD ANSI Preview ({cols} cols)")
    print("─" * 60)

    print("\n  All metrics:")
    print(ImageService.metrics_to_ansi(metrics, cols=cols))

    for group in ('cpu', 'gpu', 'mem', 'disk', 'net', 'fan', 'time'):
        print(f"\n  {group.upper()}:")
        print(ImageService.metrics_to_ansi(metrics, cols=cols, group=group))

    sys_svc.stop_polling()
    print("\nDone.")
    return 0
