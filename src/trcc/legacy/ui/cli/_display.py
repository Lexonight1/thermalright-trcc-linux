"""LCD display CLI commands.

Simple command-and-exit wrappers call the universal Trcc command layer via
`_boot.trcc()`. Long-running / streaming commands (test loop, play_video,
screencast, render_overlay) still go through the legacy Device path — they
get migrated in phase 8 once EventBus streams frames.
"""
from __future__ import annotations

import logging
import os

import typer

from trcc.legacy._boot import trcc
from trcc.legacy.core.models import HardwareMetrics
from trcc.legacy.core.models import parse_hex_color as _parse_hex
from trcc.legacy.ui.cli import _cli_handler

log = logging.getLogger(__name__)


def _current_metrics() -> HardwareMetrics:
    """Supply fresh metrics to overlay loops — named callable, not a lambda."""
    return trcc().os.metrics


def _emit(result) -> int:
    """Print the Result's one-line format + return its exit code."""
    if result.exit_code == 0:
        typer.echo(result.format())
    else:
        typer.echo(result.format(), err=True)
    return result.exit_code


# =========================================================================
# Legacy helpers — still used by long-running commands below.
# Migrate when those move to Trcc + EventBus in phase 8.
# =========================================================================

def _connect_or_fail(device: str | None = None) -> int:
    """Connect device via discover(). Returns exit code (0 = success)."""
    from trcc.legacy.ui.cli._connect import connect_device
    return connect_device(device)


def _print_result(result: dict, *, preview: bool = False) -> int:
    """Print result message + optional ANSI preview. Returns exit code."""
    from trcc.legacy.ui.cli._connect import print_result
    return print_result(result, preview=preview)


# =========================================================================
# CLI functions — blocking loops (terminal-only)
# =========================================================================

def test(device=None, loop=False, preview=False):
    """Test display with color cycle."""
    try:
        import time

        from trcc.legacy._boot import trcc as _trcc
        from trcc.legacy.services import ImageService

        log.debug("test display device=%s loop=%s", device, loop)
        if (rc := _connect_or_fail(device)):
            return rc

        lcd = _trcc().lcd_device
        assert lcd is not None
        if lcd.device_path and 'led' in lcd.device_path:
            print("LED controller with segment display — use 'trcc led-*' "
                  "commands (led-brightness, led-color, led-mode, ...).")
            return 0
        w, h = lcd.lcd_size

        colors = [
            ((255, 0, 0), "Red"),
            ((0, 255, 0), "Green"),
            ((0, 0, 255), "Blue"),
            ((255, 255, 0), "Yellow"),
            ((255, 0, 255), "Magenta"),
            ((0, 255, 255), "Cyan"),
            ((255, 255, 255), "White"),
        ]

        print(f"Testing display on {lcd.device_path}...")

        while True:
            for (r, g, b), name in colors:
                print(f"  Displaying: {name}")
                lcd.send_color(r, g, b)
                if preview:
                    img = ImageService.solid_color(r, g, b, w, h)
                    print(ImageService.to_ansi(img))
                time.sleep(1)

            if not loop:
                break

        print("Test complete!")
        return 0
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        return 0
    except Exception as e:
        print(f"Error testing display: {e}")
        return 1


def play_video(builder=None, video_path=None, *, device=None, loop=True, duration=0,
               preview=False, metrics=None, mask=None,
               font_size=14, color='ffffff', font='Microsoft YaHei',
               font_style='regular', temp_unit=0, time_format=0,
               date_format=0, lcd: int = 0):
    """Play video/GIF/ZT on LCD device with optional overlay (via Trcc)."""
    try:
        from pathlib import Path

        from trcc.legacy.core.models import build_overlay_config

        log.debug("play_video path=%s lcd=%d loop=%s", video_path, lcd, loop)
        if not video_path or not os.path.exists(video_path):
            print(f"Error: File not found: {video_path}")
            return 1

        # Build overlay config from --metric specs
        overlay_config = None
        if metrics:
            try:
                overlay_config = build_overlay_config(
                    metrics,
                    default_color=color,
                    default_font_size=font_size,
                    default_font=font,
                    default_style=font_style,
                    temp_unit=temp_unit,
                    time_format=time_format,
                    date_format=date_format,
                )
            except ValueError as e:
                print(f"Error: {e}")
                return 1

        # Metrics supplier for live overlay updates
        metrics_fn = None
        if overlay_config:
            try:
                from trcc.legacy.core.builder import ControllerBuilder
                from trcc.legacy.ui.cli import _ensure_system
                _ensure_system(ControllerBuilder(trcc().os))
            except Exception as e:
                # Best-effort metrics warmup; play continues with stale defaults.
                log.debug("_ensure_system warmup failed for play: %s", e)
            metrics_fn = _current_metrics

        app = trcc()
        snap = app.lcd.snapshot(lcd)
        w, h = snap.resolution

        print(f"Playing {video_path} on LCD {lcd} [{w}x{h}]")
        if overlay_config:
            print(f"  Overlay: {len(overlay_config)} elements")
        if mask:
            print(f"  Mask: {mask}")
        if loop:
            print("Press Ctrl+C to stop.")

        if preview:
            print('\033[2J', end='', flush=True)

        # Subscribe to EventBus for ANSI preview + progress printing.
        from trcc.legacy.services import ImageService

        def _on_frame(_idx, frame):
            img = frame.native if frame else None
            if preview and img is not None:
                print(ImageService.to_ansi_cursor_home(img), flush=True)

        def _on_progress(_idx, pct, cur, total_t):
            if not preview:
                print(f"\r  {cur} / {total_t} ({pct:.0f}%)",
                      end="", flush=True)

        sub_frame = app.events.subscribe('frame', _on_frame)
        sub_prog = app.events.subscribe('progress', _on_progress)
        try:
            result = app.lcd.play_video_loop(
                lcd, Path(video_path),
                overlay_config=overlay_config,
                mask_path=Path(mask) if mask else None,
                metrics_fn=metrics_fn,
                loop=loop,
                duration=duration,
            )
        finally:
            app.events.unsubscribe(sub_frame)
            app.events.unsubscribe(sub_prog)

        if result.success:
            print(f"\n{result.message}.")
            return 0
        print(f"Error: {result.error or 'Unknown error'}")
        return 1

    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as e:
        print(f"Error playing video: {e}")
        return 1


def screencast(builder=None, *, device=None, x=0, y=0, w=0, h=0, fps=10, preview=False):
    """Stream screen region to LCD via ffmpeg. Ctrl+C to stop."""
    import subprocess

    from PySide6.QtGui import QImage

    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.services import ImageService

    log.debug("screencast device=%s region=(%d,%d,%d,%d) fps=%d", device, x, y, w, h, fps)
    if (rc := _connect_or_fail(device)):
        return rc

    app = _trcc()
    lcd = app.lcd_device
    assert lcd is not None
    lcd_w, lcd_h = lcd.lcd_size

    capture = app.os.screen_capture_params(x, y, w, h)
    if capture is None:
        print("Error: Screencast not supported on this platform.")
        return 1
    fmt, inp, region_args = capture

    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-f', fmt, '-framerate', str(fps),
        *region_args,
        '-i', inp,
        '-vf', f'scale={lcd_w}:{lcd_h}',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        'pipe:1',
    ]
    frame_size = lcd_w * lcd_h * 3

    if w and h:
        print(f"Capturing region ({x},{y}) {w}x{h} → {lcd.device_path} [{lcd_w}x{lcd_h}]")
    else:
        print(f"Capturing full screen → {lcd.device_path} [{lcd_w}x{lcd_h}]")
    print(f"Target: {fps} fps. Press Ctrl+C to stop.")

    if preview:
        print('\033[2J', end='', flush=True)

    frames = 0
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            # Detach from raw buffer immediately — lcd.send may hold a ref
            qimg = QImage(raw, lcd_w, lcd_h, lcd_w * 3,
                          QImage.Format.Format_RGB888).copy()
            lcd.send(qimg)
            frames += 1
            if preview:
                print(ImageService.to_ansi_cursor_home(qimg), flush=True)
            else:
                print(f"\r  Frames: {frames}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print("Error: ffmpeg not found. Install ffmpeg to use screencast.")
        return 1
    except Exception as e:
        print(f"\nError: {e}")
        return 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(f"\nStopped after {frames} frames.")
    return 0


# =========================================================================
# Simple commands — migrated to Trcc.
# Each is a 3-liner: call → format → exit code.
# =========================================================================

def send_image(image_path, *, lcd: int = 0, device=None, preview=False):
    """Send image to LCD."""
    from pathlib import Path
    return _emit(trcc().lcd.send_image(lcd, Path(image_path)))


def send_color(hex_color, *, lcd: int = 0, device=None, preview=False):
    """Send solid color to LCD."""
    if not (rgb := _parse_hex(hex_color)):
        typer.echo("Error: Invalid hex color. Use format: ff0000", err=True)
        return 1
    r, g, b = rgb
    return _emit(trcc().lcd.send_color(lcd, r, g, b))


def set_brightness(level, *, lcd: int = 0, device=None):
    """Set display brightness level (1=25%, 2=50%, 3=100%)."""
    return _emit(trcc().lcd.set_brightness(lcd, level))


def set_rotation(degrees, *, lcd: int = 0, device=None):
    """Set display rotation (0, 90, 180, 270)."""
    return _emit(trcc().lcd.set_rotation(lcd, degrees))


def set_split_mode(mode, *, lcd: int = 0, device=None, preview=False):
    """Set split mode (Dynamic Island) for widescreen displays."""
    return _emit(trcc().lcd.set_split_mode(lcd, mode))


def load_mask(mask_path, *, lcd: int = 0, device=None, preview=False):
    """Load mask overlay from file/directory and send composited image."""
    from pathlib import Path
    return _emit(trcc().lcd.apply_mask(lcd, Path(mask_path)))


@_cli_handler
def render_overlay(builder, dc_path, *, device=None, send=False, output=None,
                   preview=False):
    """Render overlay from DC config file."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.ui.cli import _ensure_system

    if (rc := _connect_or_fail(device)):
        return rc
    _ensure_system(builder)
    t = _trcc()
    if (lcd := t.lcd_device) is None:
        print("Error: No LCD device connected.")
        return 1
    result = lcd.render_overlay_from_dc(
        dc_path, send=send, output=output or None,
        metrics=t.os.metrics,
    )
    if not result["success"]:
        print(f"Error: {result['error']}")
        return 1
    if result["message"]:
        print(result["message"])
    if preview and result.get("image"):
        from trcc.legacy.services import ImageService
        print(ImageService.to_ansi(result["image"]))
    if not output and not send and not preview:
        opts = result.get("display_opts", {})
        for k, v in opts.items():
            print(f"  {k}: {v}")
    return 0


def reset(*, lcd: int = 0, device=None, preview=False):
    """Reset/reinitialize the LCD device."""
    return _emit(trcc().lcd.reset(lcd))


def load_theme_by_path(path, *, lcd: int = 0):
    """Load a theme from a directory path via Trcc."""
    from pathlib import Path
    return _emit(trcc().lcd.load_theme(lcd, Path(path)))


def save_theme_by_name(name, *, lcd: int = 0):
    """Save current LCD state as a named theme via Trcc."""
    return _emit(trcc().lcd.save_theme(lcd, name))


def restore_last_theme(*, lcd: int = 0):
    """Restore the last theme applied to this LCD (device reset → re-apply)."""
    return _emit(trcc().lcd.restore_last_theme(lcd))


def enable_overlay(enabled: bool, *, lcd: int = 0):
    """Enable or disable the overlay on this LCD."""
    return _emit(trcc().lcd.enable_overlay(lcd, enabled))


def set_fit_mode(mode, *, lcd: int = 0):
    """Set video fit mode ('width' or 'height')."""
    return _emit(trcc().lcd.set_fit_mode(lcd, mode))


def set_mask_position(x: int, y: int, *, lcd: int = 0):
    """Move the active mask to (x, y) on the canvas."""
    return _emit(trcc().lcd.set_mask_position(lcd, x, y))


def set_mask_visible(visible: bool, *, lcd: int = 0):
    """Show or hide the active mask without unloading it."""
    return _emit(trcc().lcd.set_mask_visible(lcd, visible))


def render_and_send(send: bool = True, *, lcd: int = 0):
    """Force a render. With --no-send, only updates the preview cache."""
    return _emit(trcc().lcd.render_and_send(lcd, send=send))


def export_config(path, *, lcd: int = 0):
    """Export the current theme/config as a .tr archive at PATH."""
    from pathlib import Path
    return _emit(trcc().lcd.export_config(lcd, Path(path)))


def import_config(path, *, lcd: int = 0):
    """Import a .tr archive from PATH and load its theme."""
    from pathlib import Path

    _settings = trcc().settings
    data_dir = Path(_settings.user_data_dir) if _settings else Path.home() / '.trcc'
    return _emit(trcc().lcd.import_config(lcd, Path(path), data_dir))


def delete_theme(path, *, lcd: int = 0):
    """Delete a theme directory or file at PATH."""
    from pathlib import Path
    return _emit(trcc().lcd.delete_theme(lcd, Path(path)))


def configure_slideshow(themes: list[str], interval_s: int, *, lcd: int = 0):
    """Configure which themes cycle and at what interval."""
    return _emit(trcc().lcd.configure_slideshow(lcd, themes, interval_s))


def set_slideshow(enabled: bool, *, lcd: int = 0):
    """Enable or disable slideshow cycling (uses last-configured themes)."""
    return _emit(trcc().lcd.set_slideshow(lcd, enabled))


@_cli_handler
def video_status():
    """Show current video playback status."""
    print("Video playback is controlled by the running 'trcc video' process.")
    print("Use Ctrl+C in the video process to stop playback.")
    print("For interactive control, use the GUI: trcc gui")
    return 0


@_cli_handler
def resume(builder=None):
    """Send last-used theme to each detected device (headless, no GUI)."""
    import time

    from trcc.legacy._boot import trcc

    t = trcc()
    for attempt in range(10):
        result = t.discover()
        if result.success and t.lcd_devices:
            break
        print(f"Waiting for device... ({attempt + 1}/10)")
        time.sleep(2)

    if not t.lcd_devices:
        print("No compatible TRCC device detected.")
        return 1

    theme = t.lcd.restore_last_theme(0)
    if not theme.success:
        print(f"Error: {theme.error or 'Unknown error'}")
        print("No themes were sent. Use the GUI to set a theme first.")
        return 1

    device = t.lcd_devices[0]
    if theme.is_animated:
        print(f"  [{device.device_path}] Restored (animated — start GUI for playback)")
    else:
        print(f"  [{device.device_path}] Sent")
    print("Resumed 1 device.")
    return 0
