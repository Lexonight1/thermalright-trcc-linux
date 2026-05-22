"""
TRCC Linux — Command Line Interface.

Entry points for the trcc-linux package (Typer CLI).
Split into submodules by command group:
  _device.py   — detection, selection, probing
  _display.py  — LCD frame operations (test, send, color, video, resume,
                  brightness, rotation, screencast, mask, overlay)
  _theme.py    — theme listing, loading, save, export, import
  _led.py      — LED color, mode, brightness, off, sensor source
  _diag.py     — HID/LED diagnostics
  _system.py   — setup, install, admin, info, download
"""

import functools
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

import click.exceptions
import typer

log = logging.getLogger(__name__)

# =========================================================================
# CLI error handler decorator (imported by submodules via circular import)
# =========================================================================

_qt_app: "QApplication | None" = None  # kept alive to prevent PySide6 teardown segfault
_system_svc = None  # lazy SystemService singleton for CLI commands that need metrics


def _make_cli_renderer():
    """Create a QtRenderer for CLI use (offscreen, no display needed).

    Called once by InitPlatformCommand via renderer_factory. Stores the
    QApplication in _qt_app so PySide6 teardown doesn't segfault on exit.
    """
    global _qt_app
    import os
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        log.debug("Creating QApplication for CLI renderer (offscreen)")
        _qt_app = QApplication([])
    from trcc.legacy.adapters.render.qt import QtRenderer
    log.debug("CLI renderer initialised: QtRenderer (offscreen)")
    return QtRenderer()


def _ensure_system(builder=None) -> None:
    """Initialize and start SystemService for CLI commands that need sensor metrics.

    Lazy — only called by commands that use metrics (overlay, LED mode, etc.).
    Resolves internally via `_boot.trcc()` when no builder is passed; the
    `builder` parameter is kept for legacy callers (any object with
    ``.build_system()`` works) and will be removed once they migrate.
    """
    global _system_svc
    if _system_svc is None:
        from trcc.legacy.services.system import set_instance
        if builder is None:
            from trcc.legacy._boot import trcc as _trcc
            from trcc.legacy.core.builder import ControllerBuilder
            builder = ControllerBuilder(_trcc().os)
        svc = builder.build_system()
        set_instance(svc)
        _system_svc = svc


def _cli_handler(func):
    """Decorator: logs entry/exit, catches Exception, prints error, returns 1."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            log.info("CLI %s %s %s", func.__name__,
                     ' '.join(str(a) for a in args),
                     ' '.join(f'{k}={v}' for k, v in kwargs.items()))
            result = func(*args, **kwargs)
            log.info("CLI %s → %s", func.__name__, result)
            return result
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return 0
        except Exception as e:
            log.exception("CLI %s raised", func.__name__)
            print(f"Error: {e}")
            return 1
    return wrapper



# =========================================================================
# Import submodules (must be AFTER _cli_handler definition)
# =========================================================================

from trcc.legacy.ui.cli import (  # noqa: E402
    _control_center,
    _device,
    _diag,
    _display,
    _i18n,
    _led,
    _status,
    _system,
    _theme,
)

# =========================================================================
# Typer app
# =========================================================================

app = typer.Typer(
    help="Thermalright LCD Control Center for Linux",
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["--help", "-h"]},
)

_verbose = 0


def _version_callback(value: bool) -> None:
    if value:
        from trcc.__version__ import __version__
        typer.echo(f"trcc {__version__}")
        raise typer.Exit()


def _ensure_file_logging() -> None:
    """Set up logging via TrccLoggingConfigurator (WARNING console, DEBUG file)."""
    from trcc.legacy.adapters.infra.diagnostics import StandardLoggingConfigurator
    StandardLoggingConfigurator().configure(verbosity=0)


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    verbose: Annotated[int, typer.Option(
        "--verbose", "-v", count=True,
        help="Increase verbosity (-v, -vv, -vvv)",
    )] = 0,
    testing_hid: Annotated[bool, typer.Option(
        "--testing-hid", hidden=True,
        help="No-op (HID devices are now auto-detected)",
    )] = False,
    version: Annotated[bool | None, typer.Option(
        "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit",
    )] = None,
) -> None:
    global _verbose
    _verbose = verbose
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# =========================================================================
# GUI launcher
# =========================================================================

def gui(verbose=0, decorated=False, start_hidden=False):
    """Launch the GUI application.

    Args:
        verbose: Logging verbosity (0=warning, 1=info, 2=debug).
        decorated: Use decorated window with titlebar.
        start_hidden: Start minimized to system tray (used by --last-one autostart).
    """
    from trcc.legacy.adapters.infra.diagnostics import StandardLoggingConfigurator
    StandardLoggingConfigurator().configure(verbosity=verbose)


    try:
        # Clear offscreen platform set by _ensure_qt() for CLI commands —
        # the GUI needs the real windowed platform.
        import os
        os.environ.pop('QT_QPA_PLATFORM', None)

        from trcc.legacy.ui.gui import launch
        print("[TRCC] Starting LCD Control Center...")
        return launch(decorated=decorated, start_hidden=start_hidden)
    except KeyboardInterrupt:
        return 0
    except ImportError as e:
        print(f"Error: PySide6 not available: {e}")
        print("Install with: pip install PySide6")
        return 1
    except Exception as e:
        print(f"Error launching GUI: {e}")
        import traceback
        traceback.print_exc()
        return 1


# =========================================================================
# Typer command functions (thin wrappers → submodule functions)
# =========================================================================

@app.command("detect", rich_help_panel="Device")
def _cmd_detect(
    all_devices: Annotated[bool, typer.Option(
        "--all", "-a", help="Show all devices",
    )] = False,
) -> int:
    """Detect LCD device."""
    return _device.detect(show_all=all_devices)


@app.command("status", rich_help_panel="Device")
def _cmd_status(
    json_output: Annotated[bool, typer.Option(
        "--json", help="Emit JSON instead of human text",
    )] = False,
) -> int:
    """Show unified state: app + every LCD + every LED (via Trcc)."""
    return _status.status(json_output=json_output)


@app.command("lcd-snapshot", rich_help_panel="LCD Display")
def _cmd_lcd_snapshot(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    json_output: Annotated[bool, typer.Option(
        "--json", help="Emit JSON",
    )] = False,
) -> int:
    """Show a single LCD's current state."""
    return _status.lcd_snapshot(lcd=lcd, json_output=json_output)


@app.command("load-theme", rich_help_panel="Themes")
def _cmd_load_theme(
    path: Annotated[str, typer.Argument(help="Theme directory path")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Load a theme by directory path."""
    return _display.load_theme_by_path(path, lcd=lcd)


@app.command("save-theme", rich_help_panel="Themes")
def _cmd_save_theme(
    name: Annotated[str, typer.Argument(help="Theme name to save as")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Save the LCD's current state as a named theme."""
    return _display.save_theme_by_name(name, lcd=lcd)


@app.command("restore-theme", rich_help_panel="Themes")
def _cmd_restore_theme(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Restore the last theme applied to this LCD."""
    return _display.restore_last_theme(lcd=lcd)


@app.command("overlay-toggle", rich_help_panel="LCD Display")
def _cmd_overlay_toggle(
    enabled: Annotated[bool, typer.Argument(help="true=on, false=off")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Enable or disable the overlay."""
    return _display.enable_overlay(enabled, lcd=lcd)


@app.command("fit-mode", rich_help_panel="LCD Display")
def _cmd_fit_mode(
    mode: Annotated[str, typer.Argument(help="'width' or 'height'")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Set video fit mode."""
    return _display.set_fit_mode(mode, lcd=lcd)


@app.command("mask-position", rich_help_panel="LCD Display")
def _cmd_mask_position(
    x: Annotated[int, typer.Argument(help="Mask X coordinate")],
    y: Annotated[int, typer.Argument(help="Mask Y coordinate")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Move the active mask on the LCD."""
    return _display.set_mask_position(x, y, lcd=lcd)


@app.command("mask-visible", rich_help_panel="LCD Display")
def _cmd_mask_visible(
    visible: Annotated[bool, typer.Argument(help="true=show, false=hide")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Show or hide the active mask without unloading it."""
    return _display.set_mask_visible(visible, lcd=lcd)


@app.command("render", rich_help_panel="LCD Display")
def _cmd_render(
    no_send: Annotated[bool, typer.Option(
        "--no-send", help="Render only (no send to device)",
    )] = False,
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Force a render (useful for debugging overlay state)."""
    return _display.render_and_send(send=not no_send, lcd=lcd)


@app.command("export-config", rich_help_panel="Themes")
def _cmd_export_config(
    path: Annotated[str, typer.Argument(help="Destination .tr archive path")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Export the current theme config to a .tr archive (shareable)."""
    return _display.export_config(path, lcd=lcd)


@app.command("import-config", rich_help_panel="Themes")
def _cmd_import_config(
    path: Annotated[str, typer.Argument(help="Source .tr archive path")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Import a .tr archive and load its theme."""
    return _display.import_config(path, lcd=lcd)


@app.command("delete-theme", rich_help_panel="Themes")
def _cmd_delete_theme(
    path: Annotated[str, typer.Argument(help="Theme directory or file to delete")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Delete a theme directory or file."""
    return _display.delete_theme(path, lcd=lcd)


@app.command("slideshow-config", rich_help_panel="Themes")
def _cmd_slideshow_config(
    interval: Annotated[int, typer.Argument(help="Cycle interval in seconds")],
    themes: Annotated[list[str], typer.Argument(help="Theme names to cycle through")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Configure the theme slideshow (cycle interval + theme set)."""
    return _display.configure_slideshow(themes, interval, lcd=lcd)


@app.command("slideshow", rich_help_panel="Themes")
def _cmd_slideshow_toggle(
    enabled: Annotated[bool, typer.Argument(help="true=on, false=off")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Turn the theme slideshow on or off."""
    return _display.set_slideshow(enabled, lcd=lcd)


@app.command("led-snapshot", rich_help_panel="LED")
def _cmd_led_snapshot(
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    json_output: Annotated[bool, typer.Option(
        "--json", help="Emit JSON",
    )] = False,
) -> int:
    """Show a single LED's current state."""
    return _status.led_snapshot(led=led, json_output=json_output)


@app.command("led-styles", rich_help_panel="LED")
def _cmd_led_styles(
    json_output: Annotated[bool, typer.Option(
        "--json", help="Emit JSON",
    )] = False,
) -> int:
    """List every supported LED device style + capabilities."""
    return _status.led_styles(json_output=json_output)


# =========================================================================
# Control Center commands — app-level settings + updates
# =========================================================================

@app.command("app-snapshot", rich_help_panel="Control Center")
def _cmd_app_snapshot(
    json_output: Annotated[bool, typer.Option(
        "--json", help="Emit JSON instead of human text",
    )] = False,
) -> int:
    """Show app-level Control Center state."""
    return _control_center.app_snapshot(json_output=json_output)


@app.command("temp-unit", rich_help_panel="Control Center")
def _cmd_temp_unit(
    unit: Annotated[str, typer.Argument(help="C or F")],
) -> int:
    """Set app-wide temperature unit (°C or °F)."""
    return _control_center.set_temp_unit(unit)


@app.command("language", rich_help_panel="Control Center")
def _cmd_language(
    lang: Annotated[str, typer.Argument(help="ISO code: en, de, zh, fr, …")],
) -> int:
    """Set app language."""
    return _control_center.set_language(lang)


@app.command("autostart", rich_help_panel="Control Center")
def _cmd_autostart(
    enabled: Annotated[bool, typer.Argument(help="true to enable, false to disable")],
) -> int:
    """Enable or disable autostart on login."""
    return _control_center.set_autostart(enabled)


@app.command("hdd", rich_help_panel="Control Center")
def _cmd_hdd(
    enabled: Annotated[bool, typer.Argument(help="true to enable, false to disable")],
) -> int:
    """Enable or disable HDD metrics collection."""
    return _control_center.set_hdd_enabled(enabled)


@app.command("refresh", rich_help_panel="Control Center")
def _cmd_refresh(
    seconds: Annotated[int, typer.Argument(help="Interval 1-100s")],
) -> int:
    """Set the metrics refresh interval."""
    return _control_center.set_refresh_interval(seconds)


@app.command("gpu", rich_help_panel="Control Center")
def _cmd_gpu(
    gpu_key: Annotated[str, typer.Argument(help="GPU device key (from 'trcc gpus')")],
) -> int:
    """Set which GPU's metrics to display."""
    return _control_center.set_gpu_device(gpu_key)


@app.command("gpus", rich_help_panel="Control Center")
def _cmd_gpus() -> int:
    """List available GPUs."""
    return _control_center.list_gpus()


@app.command("sensors", rich_help_panel="Control Center")
def _cmd_sensors() -> int:
    """List discovered hardware sensors."""
    return _control_center.list_sensors()


@app.command("update-check", rich_help_panel="Control Center")
def _cmd_update_check() -> int:
    """Check GitHub for a newer TRCC release."""
    return _control_center.check_update()


@app.command("update", rich_help_panel="Control Center")
def _cmd_update() -> int:
    """Install the latest release via the detected package manager."""
    return _control_center.run_update()


@app.command("select", rich_help_panel="Device")
def _cmd_select(
    number: Annotated[int, typer.Argument(help="Device number from 'trcc detect --all'")],
) -> int:
    """Select device to control."""
    return _device.select(number)


@app.command("test", rich_help_panel="LCD Display")
def _cmd_test(
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path (e.g., /dev/sg0)",
    )] = None,
    loop: Annotated[bool, typer.Option(
        "--loop", "-l", help="Loop colors continuously",
    )] = False,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Test display with color cycle."""
    return _display.test(device=device, loop=loop, preview=preview)


@app.command("send", rich_help_panel="LCD Display")
def _cmd_send(
    image: Annotated[str, typer.Argument(help="Image file to send")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Send image to LCD."""
    return _display.send_image(image, lcd=lcd, preview=preview)


@app.command("color", rich_help_panel="LCD Display")
def _cmd_color(
    hex_color: Annotated[str, typer.Argument(
        metavar="HEX", help="Hex color code (e.g., ff0000 for red)",
    )],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Display solid color."""
    return _display.send_color(hex_color, lcd=lcd, preview=preview)


@app.command("video", rich_help_panel="LCD Display")
def _cmd_video(
    path: Annotated[str, typer.Argument(help="Video/GIF/ZT file to play")],
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    no_loop: Annotated[bool, typer.Option(
        "--no-loop", "-n", help="Play once without looping",
    )] = False,
    duration: Annotated[int, typer.Option(
        "--duration", "-t", help="Stop after N seconds (0=unlimited)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Play video/GIF on LCD. For overlays, use 'trcc theme' instead."""
    return _display.play_video(
        video_path=path, device=device, loop=not no_loop,
        duration=duration, preview=preview)


@app.command("theme", rich_help_panel="LCD Display")
def _cmd_theme(
    background: Annotated[str, typer.Option(
        "--background", "-b", help="Background image/video/GIF",
    )],
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    no_loop: Annotated[bool, typer.Option(
        "--no-loop", "-n", help="Play once without looping",
    )] = False,
    duration: Annotated[int, typer.Option(
        "--duration", "-t", help="Stop after N seconds (0=unlimited)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
    metric: Annotated[list[str] | None, typer.Option(
        "--metric", "-m",
        help="Overlay metric: key:x,y[:color[:size]] (repeatable)",
    )] = None,
    mask: Annotated[str | None, typer.Option(
        "--mask", help="Mask PNG file or directory",
    )] = None,
    font: Annotated[str, typer.Option(
        "--font", help="Font family name",
    )] = "Microsoft YaHei",
    font_style: Annotated[str, typer.Option(
        "--font-style", help="Font style: regular or bold",
    )] = "regular",
    font_size: Annotated[int, typer.Option(
        "--font-size", help="Font size in pixels",
    )] = 14,
    color: Annotated[str, typer.Option(
        "--color", "-c", help="Hex color for overlay text",
    )] = "ffffff",
    temp_unit: Annotated[int, typer.Option(
        "--temp-unit", help="Temperature unit: 0=Celsius, 1=Fahrenheit",
    )] = 0,
    time_format: Annotated[int, typer.Option(
        "--time-format", help="Time format: 0=24h HH:MM, 1=12h hh:MM",
    )] = 0,
    date_format: Annotated[int, typer.Option(
        "--date-format", help="Date format: 0=yyyy/MM/dd, 2=dd/MM/yyyy",
    )] = 0,
    save: Annotated[str | None, typer.Option(
        "--save", "-s", help="Save as named theme (e.g. --save MyTheme)",
    )] = None,
) -> int:
    """Play background with mask + metrics overlay. Use --save to persist."""
    if save:
        return _theme.save_theme(
            save, device=device, background=background,
            metrics=metric, mask=mask, font_size=font_size, color=color,
            font=font, font_style=font_style, temp_unit=temp_unit,
            time_format=time_format, date_format=date_format)
    return _display.play_video(
        video_path=background, device=device, loop=not no_loop, duration=duration,
        preview=preview, metrics=metric, mask=mask,
        font_size=font_size, color=color, font=font,
        font_style=font_style, temp_unit=temp_unit,
        time_format=time_format, date_format=date_format)


@app.command("brightness", rich_help_panel="LCD Display")
def _cmd_brightness(
    level: Annotated[int, typer.Argument(help="Brightness level: 1=25%, 2=50%, 3=100%")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Set display brightness."""
    return _display.set_brightness(level, lcd=lcd)


@app.command("rotation", rich_help_panel="LCD Display")
def _cmd_rotation(
    degrees: Annotated[int, typer.Argument(help="Rotation: 0, 90, 180, or 270")],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Set display rotation."""
    return _display.set_rotation(degrees, lcd=lcd)


@app.command("screencast", rich_help_panel="LCD Display")
def _cmd_screencast(
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    x: Annotated[int, typer.Option(help="Capture region X offset")] = 0,
    y: Annotated[int, typer.Option(help="Capture region Y offset")] = 0,
    w: Annotated[int, typer.Option(help="Capture region width (0=full)")] = 0,
    h: Annotated[int, typer.Option(help="Capture region height (0=full)")] = 0,
    fps: Annotated[int, typer.Option(help="Target frames per second")] = 10,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Stream screen region to LCD."""
    return _display.screencast(device=device,
                               x=x, y=y, w=w, h=h, fps=fps, preview=preview)


@app.command("mask", rich_help_panel="LCD Display")
def _cmd_mask(
    path: Annotated[str | None, typer.Argument(
        help="Mask PNG file or theme directory",
    )] = None,
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    clear: Annotated[bool, typer.Option(
        "--clear", "-c", help="Clear mask (send solid black)",
    )] = False,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Load mask overlay and send to LCD."""
    if clear:
        return _display.send_color("#000000", preview=preview)
    if not path:
        typer.echo("Error: Provide a mask path or use --clear")
        raise typer.Exit(1)
    return _display.load_mask(path, preview=preview)


@app.command("overlay", rich_help_panel="LCD Display")
def _cmd_overlay(
    dc_path: Annotated[str, typer.Argument(help="DC config or theme directory path")],
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    send: Annotated[bool, typer.Option(
        "--send", "-s", help="Send rendered result to LCD",
    )] = False,
    output: Annotated[str | None, typer.Option(
        "--output", "-o", help="Save rendered image to file",
    )] = None,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Render overlay from DC config."""
    return _display.render_overlay(
        None, dc_path,
        device=device, send=send, output=output, preview=preview)


@app.command("theme-list", rich_help_panel="Themes")
def _cmd_theme_list(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    source: Annotated[str, typer.Option(
        "--source", help="Filter: all (default), local, user, cloud",
    )] = "all",
) -> int:
    """List themes for the LCD's current resolution."""
    return _theme.list_themes(lcd=lcd, source=source)


@app.command("mask-list", rich_help_panel="Themes")
def _cmd_mask_list(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    source: Annotated[str, typer.Option(
        "--source", help="Filter: all (default), builtin, custom",
    )] = "all",
) -> int:
    """List cloud masks."""
    return _theme.list_masks(lcd=lcd, source=source)


@app.command("background-list", rich_help_panel="Themes")
def _cmd_background_list(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    category: Annotated[str | None, typer.Option(
        "--category", help="Filter by category (a=Gallery, b=Tech, c=HUD, etc.)",
    )] = None,
) -> int:
    """List cloud backgrounds."""
    return _theme.list_backgrounds(category=category, lcd=lcd)


@app.command("theme-load", rich_help_panel="Themes")
def _cmd_theme_load(
    name: Annotated[str, typer.Argument(help="Theme name (from theme-list)")],
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Load a theme and send to LCD."""
    return _theme.load_theme(None, name, device=device, preview=preview)


@app.command("led-color", rich_help_panel="LED")
def _cmd_led_color(
    hex_color: Annotated[str, typer.Argument(
        metavar="HEX", help="Hex color (e.g., ff0000 for red)",
    )],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set LED static color."""
    return _led.set_color(hex_color, led=led, preview=preview)


@app.command("led-mode", rich_help_panel="LED")
def _cmd_led_mode(
    mode: Annotated[str, typer.Argument(
        help="Effect: static, breathing, colorful, rainbow",
    )],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set LED effect mode."""
    return _led.set_mode(mode, led=led, preview=preview)


@app.command("led-brightness", rich_help_panel="LED")
def _cmd_led_brightness(
    level: Annotated[int, typer.Argument(help="Brightness 0-100")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set LED brightness."""
    return _led.set_led_brightness(level, led=led, preview=preview)


@app.command("led-off", rich_help_panel="LED")
def _cmd_led_off(
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Turn LEDs off."""
    return _led.led_off(led=led)


@app.command("led-sensor", rich_help_panel="LED")
def _cmd_led_sensor(
    source: Annotated[str, typer.Argument(
        help="Sensor source: cpu or gpu",
    )],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Set LED sensor source for temp/load linked modes."""
    return _led.set_sensor_source(source, led=led)


@app.command("led-zone-color", rich_help_panel="LED")
def _cmd_led_zone_color(
    zone: Annotated[int, typer.Argument(help="Zone index (0-based)")],
    hex_color: Annotated[str, typer.Argument(
        metavar="HEX", help="Hex color (e.g., ff0000)",
    )],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set color for a specific LED zone."""
    return _led.set_zone_color(zone, hex_color, led=led, preview=preview)


@app.command("led-zone-mode", rich_help_panel="LED")
def _cmd_led_zone_mode(
    zone: Annotated[int, typer.Argument(help="Zone index (0-based)")],
    mode: Annotated[str, typer.Argument(
        help="Effect: static, breathing, colorful, rainbow",
    )],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set effect mode for a specific LED zone."""
    return _led.set_zone_mode(zone, mode, led=led, preview=preview)


@app.command("led-zone-brightness", rich_help_panel="LED")
def _cmd_led_zone_brightness(
    zone: Annotated[int, typer.Argument(help="Zone index (0-based)")],
    level: Annotated[int, typer.Argument(help="Brightness 0-100")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Set brightness for a specific LED zone."""
    return _led.set_zone_brightness(zone, level, led=led, preview=preview)


@app.command("led-zone-toggle", rich_help_panel="LED")
def _cmd_led_zone_toggle(
    zone: Annotated[int, typer.Argument(help="Zone index (0-based)")],
    on: Annotated[bool, typer.Argument(help="true/false")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Toggle a specific LED zone on/off."""
    return _led.toggle_zone(zone, on, led=led)


@app.command("led-zone-sync", rich_help_panel="LED")
def _cmd_led_zone_sync(
    enabled: Annotated[bool, typer.Argument(help="true/false")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
    interval: Annotated[int | None, typer.Option(
        "--interval", "-i", help="Sync interval in seconds",
    )] = None,
) -> int:
    """Enable/disable LED zone sync (circulate/select-all)."""
    return _led.set_zone_sync(enabled, led=led, interval=interval)


@app.command("led-segment", rich_help_panel="LED")
def _cmd_led_segment(
    index: Annotated[int, typer.Argument(help="Segment index (0-based)")],
    on: Annotated[bool, typer.Argument(help="true/false")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Toggle a specific LED segment on/off."""
    return _led.toggle_segment(index, on, led=led)


@app.command("led-clock", rich_help_panel="LED")
def _cmd_led_clock(
    is_24h: Annotated[bool, typer.Argument(help="true=24h, false=12h")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Set LED segment display clock format."""
    return _led.set_clock_format(is_24h, led=led)


@app.command("led-temp-unit", rich_help_panel="LED")
def _cmd_led_temp_unit(
    unit: Annotated[str, typer.Argument(help="C or F")],
) -> int:
    """Set app-wide temperature unit (affects LED segments + LCD overlay)."""
    return _led.set_temp_unit(unit)


@app.command("led-week-start", rich_help_panel="LED")
def _cmd_led_week_start(
    sunday: Annotated[bool, typer.Argument(help="true=Sunday, false=Monday")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Set week-start day for LED segment displays."""
    return _led.set_week_start(sunday, led=led)


@app.command("led-memory-ratio", rich_help_panel="LED")
def _cmd_led_memory_ratio(
    ratio: Annotated[int, typer.Argument(help="DDR multiplier: 1, 2, or 4")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Set DDR memory multiplier for LC1 memory mode."""
    return _led.set_memory_ratio(ratio, led=led)


@app.command("led-disk-index", rich_help_panel="LED")
def _cmd_led_disk_index(
    index: Annotated[int, typer.Argument(help="Disk index (0-based)")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Select which disk the LF11 segment display tracks."""
    return _led.set_disk_index(index, led=led)


@app.command("led-test-mode", rich_help_panel="LED")
def _cmd_led_test_mode(
    enabled: Annotated[bool, typer.Argument(help="true to enable, false to disable")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Cycle LEDs through white/red/green/blue for hardware verification."""
    return _led.set_test_mode(enabled, led=led)


@app.command("led-select-zone", rich_help_panel="LED")
def _cmd_led_select_zone(
    zone: Annotated[int, typer.Argument(help="Zone index (0-based)")],
    led: Annotated[int, typer.Option(
        "--led", help="LED device index (default 0)",
    )] = 0,
) -> int:
    """Set which zone is 'active' for subsequent per-zone commands."""
    return _led.select_zone(zone, led=led)


@app.command("gpu-list", rich_help_panel="System")
@_cli_handler
def _cmd_gpu_list() -> int:
    """List available GPUs."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.builder import ControllerBuilder
    svc = ControllerBuilder(_trcc().os).build_system()
    gpu_list = svc.enumerator.get_gpu_list()
    if not gpu_list:
        print("No GPUs detected.")
        return 0
    from trcc.legacy._boot import trcc as _trcc
    current = _trcc().settings.gpu_device
    for gpu_key, name in gpu_list:
        marker = " *" if gpu_key == current else ""
        print(f"  {gpu_key}: {name}{marker}")
    return 0


@app.command("gpu-set", rich_help_panel="System")
@_cli_handler
def _cmd_gpu_set(
    gpu_key: Annotated[str, typer.Argument(
        help="GPU key from gpu-list (e.g., nvidia:0, amd:card0)",
    )],
) -> int:
    """Set the active GPU for metrics."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.builder import ControllerBuilder
    svc = ControllerBuilder(_trcc().os).build_system()
    valid_keys = [k for k, _ in svc.enumerator.get_gpu_list()]
    if gpu_key not in valid_keys:
        print(f"Unknown GPU '{gpu_key}'. Available: {', '.join(valid_keys)}")
        return 1
    # Single call — ``Trcc.set_gpu_device`` (via the control_center
    # facade) propagates ``settings.set_gpu_device`` AND
    # ``enumerator.set_preferred_gpu``. The legacy two-line hand-roll
    # was the same bug class as temp_unit: easy to forget the second
    # line on a new caller.
    _trcc().control_center.set_gpu_device(gpu_key)
    print(f"GPU set to: {gpu_key}")
    return 0


@app.command("lang", rich_help_panel="System")
def _cmd_lang() -> int:
    """Show current language."""
    return _i18n.get_language()


@app.command("lang-set", rich_help_panel="System")
def _cmd_lang_set(
    code: Annotated[str, typer.Argument(
        help="ISO 639-1 language code (e.g., en, de, ja, zh)",
    )],
) -> int:
    """Set the application language."""
    return _i18n.set_language(code)


@app.command("lang-list", rich_help_panel="System")
def _cmd_lang_list() -> int:
    """List all available languages."""
    return _i18n.get_languages()


@app.command("split", rich_help_panel="LCD Display")
def _cmd_split(
    mode: Annotated[int, typer.Argument(
        help="Split mode: 0=off, 1-3=Dynamic Island style",
    )],
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
) -> int:
    """Set split mode (Dynamic Island) for widescreen displays."""
    return _display.set_split_mode(mode, lcd=lcd)


@app.command("test-led", rich_help_panel="Diagnostics")
def _cmd_test_led(
    mode: Annotated[str | None, typer.Argument(
        help="LED mode: static, breathing, colorful, rainbow (omit for all)",
    )] = None,
    segments: Annotated[int, typer.Option(
        "--segments", "-s", help="Number of LED segments to simulate",
    )] = 64,
    duration: Annotated[int, typer.Option(
        "--duration", "-t", help="Animation duration in seconds (0=default)",
    )] = 0,
) -> int:
    """Test LED ANSI preview with real metrics. No device needed."""
    return _led.test_led(mode=mode, segments=segments, duration=duration)


@app.command("test-lcd", rich_help_panel="Diagnostics")
def _cmd_test_lcd(
    cols: Annotated[int, typer.Option(
        "--cols", "-c", help="Terminal width in columns",
    )] = 60,
) -> int:
    """Test LCD ANSI preview with real metrics. No device needed."""
    return _led.test_lcd(cols=cols)


@app.command("theme-save", deprecated=True, rich_help_panel="Themes")
def _cmd_theme_save(
    name: Annotated[str, typer.Argument(help="Theme name")],
    background: Annotated[str | None, typer.Option(
        "--background", "-b", help="Background image/video (auto-detects format)",
    )] = None,
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
    metric: Annotated[list[str] | None, typer.Option(
        "--metric", "-m",
        help="Overlay metric: key:x,y[:color[:size]] (repeatable)",
    )] = None,
    mask: Annotated[str | None, typer.Option(
        "--mask", help="Mask PNG file or directory",
    )] = None,
    font: Annotated[str, typer.Option(
        "--font", help="Font family name",
    )] = "Microsoft YaHei",
    font_style: Annotated[str, typer.Option(
        "--font-style", help="Font style: regular or bold",
    )] = "regular",
    font_size: Annotated[int, typer.Option(
        "--font-size", help="Font size in pixels",
    )] = 14,
    color: Annotated[str, typer.Option(
        "--color", "-c", help="Hex color for overlay text",
    )] = "ffffff",
    temp_unit: Annotated[int, typer.Option(
        "--temp-unit", help="Temperature unit: 0=Celsius, 1=Fahrenheit",
    )] = 0,
    time_format: Annotated[int, typer.Option(
        "--time-format", help="Time format: 0=24h HH:MM, 1=12h hh:MM",
    )] = 0,
    date_format: Annotated[int, typer.Option(
        "--date-format", help="Date format: 0=yyyy/MM/dd, 2=dd/MM/yyyy",
    )] = 0,
) -> int:
    """(Deprecated) Alias for 'trcc theme --save NAME'."""
    return _cmd_theme(
        background=background or '', device=device,
        metric=metric, mask=mask, font=font, font_style=font_style,
        font_size=font_size, color=color, temp_unit=temp_unit,
        time_format=time_format, date_format=date_format,
        save=name)


@app.command("theme-export", rich_help_panel="Themes")
def _cmd_theme_export(
    theme_name: Annotated[str, typer.Argument(help="Theme name to export")],
    output: Annotated[str, typer.Argument(help="Output .tr file path")],
) -> int:
    """Export a theme as .tr file."""
    return _theme.export_theme(theme_name, output)


@app.command("theme-import", rich_help_panel="Themes")
def _cmd_theme_import(
    file_path: Annotated[str, typer.Argument(help="Path to .tr file")],
    device: Annotated[str | None, typer.Option(
        "--device", "-d", help="Device path",
    )] = None,
) -> int:
    """Import a theme from .tr file."""
    return _theme.import_theme(file_path, device=device)


@app.command("info", rich_help_panel="System")
def _cmd_info(
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal dashboard",
    )] = False,
    metric: Annotated[str | None, typer.Option(
        "--metric", "-m",
        help="Filter: cpu, gpu, mem, disk, net, fan, time",
    )] = None,
) -> int:
    """Show system metrics."""
    return _system.show_info(preview=preview, metric=metric)


@app.command("reset", rich_help_panel="LCD Display")
def _cmd_reset(
    lcd: Annotated[int, typer.Option(
        "--lcd", help="LCD device index (default 0)",
    )] = 0,
    preview: Annotated[bool, typer.Option(
        "--preview", "-p", help="Show ANSI terminal preview",
    )] = False,
) -> int:
    """Reset/reinitialize LCD device."""
    return _display.reset(lcd=lcd, preview=preview)



@app.command("resume", rich_help_panel="LCD Display")
def _cmd_resume() -> int:
    """Send last-used theme to each detected device (headless)."""
    return _display.resume()


@app.command("uninstall", rich_help_panel="System")
def _cmd_uninstall(
    yes: Annotated[bool, typer.Option(
        "--yes", "-y", help="Skip confirmation prompts (for non-interactive use)",
    )] = False,
) -> int:
    """Remove all TRCC config, udev rules, and autostart files."""
    return _system.uninstall(yes=yes)


@app.command("hid-debug", rich_help_panel="Diagnostics")
def _cmd_hid_debug(
    test_frame: Annotated[bool, typer.Option(
        "--test-frame", "-t", help="Send solid red test frame after handshake",
    )] = False,
) -> int:
    """HID handshake diagnostic (hex dump for bug reports)."""
    return _diag.device_debug(test_frame=test_frame)


@app.command("led-debug", rich_help_panel="Diagnostics")
def _cmd_led_debug(
    test_colors: Annotated[bool, typer.Option(
        "--test", "-t", help="Send test colors after handshake",
    )] = False,
) -> int:
    """Diagnose LED device (handshake, PM byte)."""
    return _diag.led_debug_interactive(test_colors=test_colors)


@app.command("report", rich_help_panel="Diagnostics")
def _cmd_report() -> int:
    """Generate full diagnostic report for bug reports."""
    return _system.report()


@app.command("doctor", rich_help_panel="Diagnostics")
def _cmd_doctor() -> int:
    """Check dependencies, libraries, and permissions."""
    from trcc.legacy.adapters.infra.diagnostics import run_doctor
    return run_doctor()


@app.command("perf", rich_help_panel="Diagnostics")
def _cmd_perf(
    device: Annotated[bool, typer.Option(
        "--device", "-d", help="Benchmark connected hardware (USB I/O latency, FPS)",
    )] = False,
) -> int:
    """Run CPU + memory performance benchmarks."""

    if device:
        from trcc.legacy._boot import trcc as _trcc
        from trcc.legacy.services.perf import run_device_benchmarks

        print("Running device I/O benchmarks (this takes ~10s)...")
        t = _trcc()
        report = run_device_benchmarks(
            detect_fn=t.detect,
            get_protocol=t.protocol_for,
            get_protocol_info=t.protocol_info_for,
            probe_led_fn=t.probe_led,
        )
        if not report.has_data:
            print("No devices found. Connect a device and try again.")
            return 1
    else:
        from trcc.legacy.services.perf import run_benchmarks

        print("Running performance benchmarks...")
        report = run_benchmarks()

    for line in report.format_report():
        print(line)
    return 0 if report.all_passed else 1


@app.command("setup", rich_help_panel="System")
def _cmd_setup(
    yes: Annotated[bool, typer.Option(
        "--yes", "-y", help="Accept all defaults (non-interactive)",
    )] = False,
) -> int:
    """Interactive setup wizard — check deps, install packages, configure system."""
    return _system.setup(auto_yes=yes)


@app.command("setup-udev", rich_help_panel="System")
def _cmd_setup_udev() -> int:
    """Install device access rules (udev on Linux, WinUSB guide on Windows).

    Narrower than ``trcc setup`` — skips the full interactive wizard,
    just installs / refreshes the udev rules + usb-storage quirks.
    Suggested by the doctor and factory error messages when device
    permissions are wrong.
    """
    from trcc.legacy._boot import trcc as _trcc
    return _trcc().os.install_rules()


@app.command("setup-winusb", rich_help_panel="System")
def _cmd_setup_winusb() -> int:
    """Print the Zadig-based WinUSB driver setup guide (Windows-natural name).

    Aliases ``trcc setup-udev`` — same ``Platform.install_rules()`` call,
    different name that matches what Windows users see in error messages.
    """
    from trcc.legacy._boot import trcc as _trcc
    return _trcc().os.install_rules()


@app.command("setup-selinux", rich_help_panel="System")
def _cmd_setup_selinux() -> int:
    """Install TRCC's SELinux policy module (Fedora/RHEL/CentOS only)."""
    if sys.platform != "linux":
        typer.echo("setup-selinux is Linux-only.", err=True)
        return 1
    from trcc.legacy.adapters.system.linux_platform import setup_selinux
    return setup_selinux()


@app.command("setup-polkit", rich_help_panel="System")
def _cmd_setup_polkit() -> int:
    """Install TRCC's polkit policy for password-prompted privileged ops."""
    if sys.platform != "linux":
        typer.echo("setup-polkit is Linux-only.", err=True)
        return 1
    from trcc.legacy.adapters.system.linux_platform import setup_polkit
    return setup_polkit()


@app.command("setup-gui", rich_help_panel="Interfaces")
def _cmd_setup_gui() -> None:
    """Launch the setup wizard GUI."""
    from trcc.legacy.install.gui import main
    raise SystemExit(main())


@app.command("download", rich_help_panel="Themes")
def _cmd_download(
    pack: Annotated[str | None, typer.Argument(
        help="Theme pack name (e.g., themes-320x320 or themes-480)",
    )] = None,
    show_list: Annotated[bool, typer.Option(
        "--list", "-l", help="List available packs",
    )] = False,
    force: Annotated[bool, typer.Option(
        "--force", "-f", help="Force reinstall",
    )] = False,
    show_info: Annotated[bool, typer.Option(
        "--info", "-i", help="Show pack info",
    )] = False,
) -> int:
    """Download theme packs."""
    return _system.download_themes(
        pack=pack, show_list=show_list, force=force, show_info=show_info)


@app.command("kill", rich_help_panel="Interfaces")
def _cmd_kill() -> int:
    """Stop the running TRCC daemon (no-op if none is running).

    Sends a graceful shutdown request over the IPC socket; the daemon
    acks, tears down its event loop, and exits. Other UI clients (GUI
    windows, API server) will lose their daemon connection — they can
    reconnect by re-launching, which auto-spawns a fresh daemon.
    """
    from trcc.legacy.daemon import kill_daemon
    if kill_daemon():
        typer.echo("Daemon stopped.")
        return 0
    typer.echo("Daemon did not stop within timeout.", err=True)
    return 1


@app.command("daemon", rich_help_panel="Interfaces")
def _cmd_daemon() -> int:
    """Run TRCC as a singleton background daemon.

    Owns USB and serves CLI / API / GUI clients over the IPC socket.
    Refuses to start if another daemon is already running. Exits cleanly
    on SIGTERM / SIGINT.

    Opt-in today — UIs default to in-process Trcc unless ``TRCC_DAEMON=1``
    is set or ``trcc daemon`` is started explicitly. Phase 12 flips the
    default after the soak period in the v10 cutover plan.
    """
    from trcc.legacy.daemon import run_daemon
    return run_daemon(verbosity=_verbose)


@app.command("gui", rich_help_panel="Interfaces")
def _cmd_gui(
    decorated: Annotated[bool, typer.Option(
        "--decorated", "-d",
        help="Use decorated window (normal window with titlebar, can minimize)",
    )] = False,
    resume: Annotated[bool, typer.Option(
        "--resume",
        help="Start hidden in system tray and restore last-used theme (autostart)",
    )] = False,
) -> int:
    """Launch graphical interface."""
    return gui(verbose=_verbose, decorated=decorated, start_hidden=resume)


@app.command("shell", rich_help_panel="Interfaces")
def _cmd_shell() -> int:
    """Open interactive TRCC shell — type commands without the 'trcc' prefix."""
    import shlex

    import click
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory

    from trcc.legacy.core.paths import USER_DATA_DIR

    # Build tab-completer from all registered commands
    click_app = typer.main.get_command(app)
    commands = sorted(click_app.commands.keys())  # type: ignore[union-attr]
    completer = WordCompleter(commands, sentence=True)

    history_file = Path(USER_DATA_DIR) / "shell_history"
    session: PromptSession = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
    )

    print("TRCC shell — type commands without 'trcc' prefix. Tab to complete. Ctrl+D to exit.")
    while True:
        try:
            text = session.prompt("trcc> ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if not text:
            continue
        if text in ("exit", "quit"):
            break

        try:
            args = shlex.split(text)
            click_app.main(args, standalone_mode=False)
        except click.exceptions.Exit:
            pass
        except click.exceptions.Abort:
            print("\nAborted.")
        except SystemExit:
            pass
        except Exception as e:
            print(f"Error: {e}")

    return 0


@app.command("api", rich_help_panel="Interfaces")
def _cmd_api() -> int:
    """List all REST API endpoints."""
    from trcc.legacy.ui.api import app as api_app

    routes = []
    for route in api_app.routes:
        methods = getattr(route, 'methods', None)
        path = getattr(route, 'path', None)
        if not methods or not path:
            continue
        summary = getattr(route, 'summary', '') or getattr(route, 'name', '') or ''
        for method in sorted(methods):
            if method == 'HEAD':
                continue
            routes.append((method, path, summary))

    routes.sort(key=lambda r: (r[1], r[0]))
    for method, path, summary in routes:
        desc = f"  {summary}" if summary else ""
        print(f"  {method:6s} {path}{desc}")
    print(f"\n  {len(routes)} endpoints. Docs at http://<host>:<port>/docs")
    return 0


@app.command("serve", rich_help_panel="Interfaces")
def _cmd_serve(
    host: Annotated[str, typer.Option(
        "--host", "-H", help="Bind address (use 0.0.0.0 for LAN)",
    )] = "127.0.0.1",
    port: Annotated[int, typer.Option(
        "--port", "-p", help="Listen port",
    )] = 9876,
    token: Annotated[str | None, typer.Option(
        "--token", "-t", help="API token for auth",
    )] = None,
    tls: Annotated[bool, typer.Option(
        "--tls", help="Enable HTTPS (auto-generates self-signed cert if needed)",
    )] = False,
    cert: Annotated[str | None, typer.Option(
        "--cert", help="Path to TLS certificate file (.pem)",
    )] = None,
    key: Annotated[str | None, typer.Option(
        "--key", help="Path to TLS private key file (.pem)",
    )] = None,
) -> int:
    """Start REST API server."""
    import secrets  # noqa: I001
    import string

    import uvicorn

    from trcc.legacy.ui.api import app as api_app, configure_app, configure_auth, set_pairing_code
    from trcc.legacy.conf import Settings

    # Token resolution: explicit --token > persistent config > auto-generate
    pairing_code: str | None = None
    if token is not None:
        # Explicit --token: save it, no pairing code needed
        Settings.save_api_token(token)
    else:
        # Use persistent token (generated on first run, reused forever)
        token = Settings.get_api_token()
        # Generate ephemeral 6-char pairing code for phone to enter
        alphabet = string.ascii_uppercase + string.digits
        pairing_code = ''.join(secrets.choice(alphabet) for _ in range(6))
        set_pairing_code(pairing_code)

    configure_auth(token)

    ssl_kwargs: dict = {}
    if cert and key:
        ssl_kwargs = {"ssl_certfile": cert, "ssl_keyfile": key}
    elif tls:
        if not (certs := _ensure_self_signed_cert()):
            return 1
        ssl_kwargs = {"ssl_certfile": certs[0], "ssl_keyfile": certs[1]}

    # Warn if token travels in plaintext over LAN
    if host != "127.0.0.1" and not ssl_kwargs:
        print("WARNING: No --tls — traffic is unencrypted on the network.")
        print("         Add --tls for HTTPS, or use --cert/--key for a custom certificate.")

    scheme = "https" if ssl_kwargs else "http"
    print(f"Serving on {scheme}://{host}:{port}")

    if pairing_code:
        print(f"\n  Pairing code:  {pairing_code}\n")
        print("  Enter this code in TRCC Remote to pair your phone.")
        print("  After pairing, the phone stays connected across restarts.\n")

    _print_serve_qr(host, port, token, bool(ssl_kwargs))
    configure_app()
    uvicorn.run(api_app, host=host, port=port, **ssl_kwargs)
    return 0


def _print_serve_qr(host: str, port: int, token: str | None, tls: bool) -> None:
    """Print a terminal QR code with connection details for remote apps.

    Presentation-only — delegates to ServerInfo (DTO) for payload and
    adapters.infra.network for LAN IP detection.
    """
    try:
        import qrcode  # pyright: ignore[reportMissingImports]
    except ImportError:
        return

    from trcc.legacy.adapters.infra.network import get_lan_ip
    from trcc.legacy.core.models import ServerInfo

    display_host = get_lan_ip() if host in ("0.0.0.0", "::") else host
    info = ServerInfo(host=display_host, port=port, token=token or "", tls=tls)

    qr = qrcode.QRCode(error_correction=qrcode.ERROR_CORRECT_L, border=1)
    qr.add_data(info.to_json())
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"Scan to connect: {display_host}:{port}")


def _ensure_self_signed_cert() -> tuple[str, str] | None:
    """Auto-generate a self-signed TLS cert in ~/.trcc/tls/ if missing."""
    import shutil
    import subprocess

    from trcc.legacy.conf import CONFIG_DIR

    tls_dir = Path(CONFIG_DIR) / 'tls'
    certfile = tls_dir / 'cert.pem'
    keyfile = tls_dir / 'key.pem'

    if certfile.is_file() and keyfile.is_file():
        return str(certfile), str(keyfile)

    if not shutil.which('openssl'):
        print("ERROR: openssl not found. Install openssl or provide --cert/--key manually.")
        return None

    tls_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', str(keyfile), '-out', str(certfile),
            '-days', '3650', '-nodes',
            '-subj', '/CN=trcc-linux/O=TRCC',
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to generate TLS certificate: {result.stderr.strip()}")
        return None

    keyfile.chmod(0o600)
    print(f"Generated self-signed TLS certificate in {tls_dir}/")
    return str(certfile), str(keyfile)


# =========================================================================
# Main entry point
# =========================================================================

def main():
    """Main CLI entry point — composition root.

    Delegates the entire DI graph to ``trcc._boot.trcc()``. The first
    ``trcc()`` call from any subcommand (or from this entry point's
    progress-subscription bootstrap) builds the cached singleton.
    """
    from trcc.legacy._boot import trcc as _boot_trcc
    from trcc.legacy.core.events import Topic

    # gui / daemon subcommands run their own composition root — they
    # need a windowed QApp (gui) or an offscreen QApp + signal handlers
    # (daemon). Bootstrapping a Trcc here would race their setup, so
    # we let those subcommands construct on demand.
    _positional = [a for a in sys.argv[1:] if not a.startswith('-')]
    _runs_own_root = _positional[:1] in (['gui'], ['daemon'])

    if not _runs_own_root:
        # CLI builds its Trcc up-front so progress events from the
        # first device-touching subcommand land on a real subscriber.
        # Defer device discovery to keep `trcc --help` instantaneous;
        # subcommands that need devices call _boot_trcc() (cached) which
        # is a no-op once we've built the singleton — they trigger
        # discover via TrccApp.discover today (Phase 9 intermediate state).
        t = _boot_trcc(verbosity=_verbose, discover_now=False)
        t.events.subscribe(
            Topic.BOOTSTRAP_PROGRESS,
            lambda msg: print(msg, flush=True),
        )

    try:
        result = app(standalone_mode=False)
        return result if isinstance(result, int) else 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except click.exceptions.UsageError as e:
        print(f"Error: {e.format_message()}")
        if hasattr(e, 'ctx') and e.ctx:
            print("Try 'trcc --help' for usage info.")
        return 2
    except Exception as e:
        print(f"Error: {e}")
        return 1


# =========================================================================
# Backward-compat function aliases (pyproject.toml entry points + tests)
# =========================================================================

# Entry points (pyproject.toml console_scripts)
detect = _device.detect
test_display = _display.test
select_device = _device.select

# Backward-compat for tests and external consumers
_probe_device = _device._probe
_format_device = _device._format
send_image = _display.send_image
send_color = _display.send_color
play_video = _display.play_video
reset_device = _display.reset
resume = _display.resume
show_info = _system.show_info
hid_debug = _diag.device_debug
led_debug = _diag.led_debug_interactive
setup = _system.setup
uninstall = _system.uninstall
report = _system.report
download_themes = _system.download_themes
_hex_dump = _diag._hex_dump
_hid_debug_lcd = _diag._hid_debug_lcd
_hid_debug_led = _diag._hid_debug_led

# Display adjustments
set_brightness = _display.set_brightness
set_rotation = _display.set_rotation
set_split_mode = _display.set_split_mode
screencast = _display.screencast
load_mask = _display.load_mask
render_overlay = _display.render_overlay

# Theme commands
list_themes = _theme.list_themes
list_masks = _theme.list_masks
list_backgrounds = _theme.list_backgrounds
load_theme = _theme.load_theme
save_theme = _theme.save_theme
export_theme = _theme.export_theme
import_theme = _theme.import_theme

# Language commands
get_languages = _i18n.get_languages
get_language = _i18n.get_language
set_language = _i18n.set_language

# LED commands
led_color = _led.set_color
led_mode = _led.set_mode
led_brightness = _led.set_led_brightness
led_off = _led.led_off
led_sensor = _led.set_sensor_source
