"""LED CLI commands — thin wrappers over Device.

Presentation-only: builder injected by _cmd_* boundary functions, call method, print result.
"""
from __future__ import annotations

import logging
from typing import Any

from trcc.cli import _cli_handler
from trcc.core.models import parse_hex_color as _parse_hex

log = logging.getLogger(__name__)

# =========================================================================
# CLI presentation helpers
# =========================================================================

def _connect_or_fail() -> int:
    """Connect device via discover(). Returns exit code (0 = success)."""
    from trcc.cli._connect import connect_device
    return connect_device()


def _print_result(result: dict, *, preview: bool = False) -> int:
    """Print result message + optional ANSI preview. Returns exit code."""
    from trcc.cli._connect import print_result
    return print_result(result, preview=preview)


def _led_call(method_name: str, preview: bool = False, **kwargs) -> int:
    """Connect LED, call a method on the device, print result."""
    log.debug("LED call method=%s kwargs=%s", method_name, kwargs)
    if (rc := _connect_or_fail()):
        return rc
    from trcc.core.app import TrccApp
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = getattr(led, method_name)(**kwargs)
    return _print_result(result, preview=preview)


# =========================================================================
# CLI functions (thin wrappers — print + exit code)
# =========================================================================

@_cli_handler
def set_color(builder, hex_color, *, preview=False):
    """Set LED static color."""
    from trcc.core.app import TrccApp
    log.debug("set_color hex=%s", hex_color)
    if not (rgb := _parse_hex(hex_color)):
        print("Error: Invalid hex color. Use format: ff0000")
        return 1
    if (rc := _connect_or_fail()):
        return rc
    r, g, b = rgb
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = led.set_color(r, g, b)
    return _print_result(result, preview=preview)


@_cli_handler
def set_mode(builder, mode_name, *, preview=False):
    """Set LED effect mode."""
    log.debug("set_mode mode=%s", mode_name)
    if (rc := _connect_or_fail()):
        return rc
    from trcc.core.app import TrccApp
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = led.set_mode(mode_name)
    if not result["success"]:
        print(f"Error: {result['error']}")
        if result.get("available"):
            print(f"Available: {', '.join(result['available'])}")
        return 1

    if result["animated"]:
        import threading

        from trcc.cli import _ensure_system
        from trcc.core.app import AppEvent, AppObserver

        _ensure_system(builder)
        app = TrccApp.get()
        app.start_metrics_loop()
        print(f"LED mode: {mode_name} (running, Ctrl+C to stop)")
        done = threading.Event()

        class _CliLedObserver(AppObserver):
            def on_app_event(self, event: AppEvent, data: Any) -> None:
                if not preview or event != AppEvent.FRAME_RENDERED:
                    return
                colors = data.get('image', {}).get('colors')
                if colors and data.get('path') == led.device_path:
                    from trcc.services import LEDService
                    print(LEDService.zones_to_ansi(colors), end='\r', flush=True)

        observer = _CliLedObserver()
        app.register(observer)
        try:
            done.wait()  # blocks until Ctrl+C
        except KeyboardInterrupt:
            pass
        app.unregister(observer)
        app.stop_metrics_loop()
        print("\nStopped.")
    else:
        print(result["message"])
        if preview and result.get("colors"):
            from trcc.services import LEDService
            print(LEDService.zones_to_ansi(result["colors"]))

    return 0


@_cli_handler
def set_led_brightness(builder, level, *, preview=False):
    """Set LED brightness (0-100)."""
    from trcc.core.app import TrccApp
    if (rc := _connect_or_fail()):
        return rc
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = led.set_brightness(level)
    return _print_result(result, preview=preview)


@_cli_handler
def led_off(builder):
    """Turn LEDs off."""
    return _led_call('toggle_global', on=False)


@_cli_handler
def set_sensor_source(builder, source):
    """Set CPU/GPU sensor source for temp/load linked LED modes."""
    from trcc.core.app import TrccApp
    if (rc := _connect_or_fail()):
        return rc
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = led.set_sensor_source(source)
    return _print_result(result)


@_cli_handler
def set_zone_color(builder, zone: int, hex_color: str, *, preview: bool = False):
    """Set color for a specific LED zone."""
    from trcc.core.app import TrccApp
    if not (rgb := _parse_hex(hex_color)):
        print("Error: Invalid hex color. Use format: ff0000")
        return 1
    if (rc := _connect_or_fail()):
        return rc
    r, g, b = rgb
    app = TrccApp.get()
    led = app.led_device
    if led is None:
        print("Error: No LED device found.")
        return 1
    result = led.set_zone_color(zone, r, g, b)
    return _print_result(result, preview=preview)


@_cli_handler
def set_zone_mode(builder, zone: int, mode_name: str, *, preview: bool = False):
    """Set effect mode for a specific LED zone."""
    return _led_call('set_zone_mode', preview=preview, zone=zone, mode=mode_name)


@_cli_handler
def set_zone_brightness(builder, zone: int, level: int, *, preview: bool = False):
    """Set brightness for a specific LED zone (0-100)."""
    return _led_call('set_zone_brightness', preview=preview, zone=zone, level=level)


@_cli_handler
def toggle_zone(builder, zone: int, on: bool):
    """Toggle a specific LED zone on/off."""
    return _led_call('toggle_zone', zone=zone, on=on)


@_cli_handler
def set_zone_sync(builder, enabled: bool, *, interval: int | None = None):
    """Enable/disable zone sync (circulate or select-all depending on style)."""
    return _led_call('set_zone_sync', enabled=enabled, interval=interval)


@_cli_handler
def toggle_segment(builder, index: int, on: bool):
    """Toggle a specific LED segment on/off."""
    return _led_call('toggle_segment', index=index, on=on)


@_cli_handler
def set_clock_format(builder, is_24h: bool):
    """Set LED segment display clock format (12h/24h)."""
    return _led_call('set_clock_format', is_24h=is_24h)


@_cli_handler
def set_temp_unit(builder, unit: int | str):
    """Set LED segment display temperature unit (0=Celsius, 1=Fahrenheit)."""
    if isinstance(unit, str):
        unit = 1 if unit.upper() == "F" else 0
    return _led_call('set_temp_unit', unit=unit)


# =========================================================================
# Developer test commands (no device needed)
# =========================================================================

def test_led(builder, *, mode: str | None = None, segments: int = 64,
             duration: int = 0):
    """Test LED ANSI preview with real system metrics. No device needed."""
    import time

    from trcc.cli import _ensure_system
    from trcc.core.models import LEDMode, LEDState
    from trcc.services.led import LEDService
    from trcc.services.system import get_instance

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
    metrics = sys_svc.all_metrics

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


def test_lcd(builder, *, cols: int = 60):
    """Test LCD ANSI preview with real system metrics. No device needed."""
    from trcc.cli import _ensure_system
    from trcc.services.image import ImageService
    from trcc.services.system import get_instance

    _ensure_system(builder)
    sys_svc = get_instance()
    metrics = sys_svc.all_metrics

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
