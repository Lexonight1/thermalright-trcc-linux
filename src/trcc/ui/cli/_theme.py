"""Theme discovery and loading commands."""
from __future__ import annotations

import logging

import typer

from trcc._boot import trcc
from trcc.ui.cli import _cli_handler

log = logging.getLogger(__name__)


def _get_device_cfg() -> dict | None:
    """Read the last active device's config. Returns None if no device configured."""
    from trcc.conf import Settings
    key = str(Settings.get_last_device())
    cfg = Settings.get_device_config(key)
    if not cfg or not cfg.get('w'):
        return None
    return cfg


def list_themes(*, lcd: int = 0, source: str = 'all') -> int:
    """List themes for the LCD's current resolution."""
    app = trcc()
    themes = app.lcd.list_themes(lcd, source=source)
    snap = app.lcd.snapshot(lcd)
    w, h = snap.resolution
    label = {
        'local': 'Local (default)',
        'user':  'User-saved',
        'cloud': 'Cloud',
        'all':   'All',
    }.get(source, source.title())
    if not themes:
        typer.echo(f'{label} themes ({w}x{h}): 0')
        return 0
    typer.echo(f'{label} themes ({w}x{h}): {len(themes)}')
    for t in themes:
        kind = 'video' if t.is_animated else 'static'
        user = ' [user]' if t.name.startswith(('Custom_', 'User')) else ''
        typer.echo(f'  {t.name} ({kind}){user}')
    return 0


def list_backgrounds(category=None, *, lcd: int = 0) -> int:
    """List cloud backgrounds for the LCD's current resolution."""
    app = trcc()
    themes = app.lcd.list_themes(lcd, source='cloud')
    if category and category != 'all':
        themes = [t for t in themes if t.category == category]
    snap = app.lcd.snapshot(lcd)
    w, h = snap.resolution
    if not themes:
        typer.echo(f'No cloud backgrounds for {w}x{h}.')
        return 0
    typer.echo(f'Cloud backgrounds ({w}x{h}): {len(themes)}')
    for t in themes:
        cat = f' [{t.category}]' if t.category else ''
        typer.echo(f'  {t.name}{cat}')
    return 0


def list_masks(*, lcd: int = 0, source: str = 'all') -> int:
    """List available masks for the LCD's current resolution."""
    app = trcc()
    masks = app.lcd.list_masks(lcd, source=source)
    snap = app.lcd.snapshot(lcd)
    w, h = snap.resolution
    if not masks:
        typer.echo(f'No masks for {w}x{h}.')
        return 0
    typer.echo(f'Masks ({w}x{h}): {len(masks)}')
    for m in masks:
        tag = ' [custom]' if m.is_custom else ''
        typer.echo(f'  {m.name}{tag}')
    return 0


@_cli_handler
def load_theme(builder, name, *, device=None, preview=False):
    """Load a theme by name and send to LCD."""
    from trcc._boot import trcc as _trcc
    from trcc.services import ImageService
    from trcc.ui.cli._display import _connect_or_fail

    log.debug("load_theme name=%s device=%s", name, device)
    if (rc := _connect_or_fail(device)):
        return rc

    if (lcd := _trcc().lcd_device) is None:
        print("Error: No LCD device connected.")
        return 1
    result = lcd.load_theme_by_name(name)

    if not result.get("success"):
        print(f"Error: {result.get('error', 'Unknown error')}")
        return 1

    if result.get("is_animated") and result.get("theme_path"):
        from pathlib import Path

        theme_dir = Path(str(result["theme_path"]))
        video_path = None
        for ext in ('.zt', '.mp4', '.gif', '.avi', '.mkv', '.webm'):
            for stem in ('Theme', 'theme'):
                candidate = theme_dir / f"{stem}{ext}"
                if candidate.exists():
                    video_path = str(candidate)
                    break
            if video_path:
                break
        if not video_path:
            print(f"Error: No video file found in {theme_dir}")
            return 1

        overlay_config = result.get("overlay_config")

        # Wire metrics if overlay is configured
        metrics_fn = None
        if overlay_config:
            import trcc.ui.cli as _cli_mod
            _cli_mod._ensure_system(builder)
            from trcc._boot import trcc as _trcc
            metrics_fn = lambda: _trcc().os.metrics  # noqa: E731

        # Wire mask from theme dir
        mask_path = None
        mask_file = theme_dir / '01.png'
        if mask_file.exists():
            mask_path = str(mask_file)

        print(f"Playing '{name}' → {lcd.device_path}")
        if overlay_config:
            print(f"  Overlay: {len(overlay_config)} elements")
        print("Press Ctrl+C to stop.")

        if preview:
            print('\033[2J', end='', flush=True)

        def _on_frame(img):
            lcd.send(img)
            if preview:
                print(ImageService.to_ansi_cursor_home(img), flush=True)

        def _on_progress(pct, cur, total_t):
            if not preview:
                print(f"\r  {cur} / {total_t} ({pct:.0f}%)",
                      end="", flush=True)

        try:
            loop_result = lcd.play_video_loop(
                video_path, loop=True,
                overlay_config=overlay_config,
                mask_path=mask_path,
                metrics_fn=metrics_fn,
                on_frame=_on_frame,
                on_progress=_on_progress)
            print(f"\n{loop_result.get('message', 'Done')}.")
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        img = result.get("image")
        if img:
            print(f"Loaded '{name}' → {lcd.device_path}")
            if preview:
                print(ImageService.to_ansi(img))
            print("Press Ctrl+C to stop.")
            try:
                import trcc.ui.cli as _cli_mod
                _cli_mod._ensure_system(builder)
                from trcc._boot import trcc as _trcc
                metrics_fn = lambda: _trcc().os.metrics  # noqa: E731
                def _preview_frame(img):
                    print(ImageService.to_ansi_cursor_home(img), flush=True)

                if preview:
                    print('\033[2J', end='', flush=True)
                lcd.keep_alive_loop(
                    metrics_fn=metrics_fn,
                    on_frame=_preview_frame if preview else None)
            except KeyboardInterrupt:
                print("\nStopped.")
        else:
            print(f"Theme '{name}' has no background image.")
            return 1

    return 0


@_cli_handler
def save_theme(name, *, device=None, video=None, background=None,
               metrics=None, mask=None, font_size=14, color='ffffff',
               font='Microsoft YaHei', font_style='regular',
               temp_unit=0, time_format=0, date_format=0):
    """Save current display state as a custom theme."""
    from pathlib import Path

    from trcc._boot import trcc as _trcc
    from trcc.ui.cli._display import _connect_or_fail

    log.debug("save_theme name=%s device=%s background=%s", name, device, background)
    if (rc := _connect_or_fail(device)):
        return rc

    if (lcd := _trcc().lcd_device) is None:
        print("Error: No LCD device connected.")
        return 1

    # --background / --video → load into DisplayService state
    if (bg_source := background or video):
        p = Path(bg_source)
        if not p.exists():
            print(f"Error: File not found: {bg_source}")
            return 1
        result = lcd.load_image(p)
        if not result.get("success"):
            print(f"Error: {result.get('error')}")
            return 1

    # No explicit background → current display state (from load_theme etc.)
    if not lcd.current_image:
        print("No background to save. Provide --background or load a theme first.")
        return 1

    # --metric → configure overlay
    if metrics:
        from trcc.core.models import build_overlay_config
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
        lcd.set_config(overlay_config)
        lcd.enable_overlay(True)

    # --mask → load mask
    if mask:
        result = lcd.set_mask_from_path(Path(mask))
        if not result.get("success"):
            print(f"Error: {result.get('error')}")
            return 1

    # Save via service layer (always to ~/.trcc-user/)
    result = lcd.save(name)
    print(result.get("message", ""))
    if metrics:
        print(f"  Overlay: {len(overlay_config)} elements")
    if mask:
        print(f"  Mask: {mask}")
    return 0 if result.get("success") else 1


@_cli_handler
def export_theme(theme_name, output_path):
    """Export a theme as .tr file."""
    from pathlib import Path

    from trcc._boot import trcc as _trcc
    from trcc.services import ThemeService

    log.debug("export_theme name=%s output=%s", theme_name, output_path)
    if not (cfg := _get_device_cfg()):
        print("No device configured. Connect your device first.")
        return 1

    w, h = cfg['w'], cfg['h']
    theme_dir = cfg.get('theme_dir')
    if not theme_dir or not Path(theme_dir).exists():
        print(f"No themes for {w}x{h}.")
        return 1

    themes = ThemeService.discover_local_merged(
        Path(theme_dir), _trcc().settings.user_content_dir / 'data', (w, h))
    match = next((t for t in themes if t.name == theme_name), None)
    if not match:
        match = next(
            (t for t in themes if theme_name.lower() in t.name.lower()),
            None,
        )
    if not match or not match.path:
        print(f"Theme not found: {theme_name}")
        return 1

    from trcc.adapters.infra.dc_config import DcConfig
    from trcc.adapters.infra.dc_parser import load_config_json
    from trcc.adapters.infra.dc_writer import export_theme as _export_fn
    theme_svc = ThemeService(
        export_theme_fn=_export_fn,
        load_config_json_fn=load_config_json,
        dc_config_cls=DcConfig,
    )
    ok, msg = theme_svc.export_tr(match.path, Path(output_path))
    print(msg)
    return 0 if ok else 1


@_cli_handler
def import_theme(file_path, *, device=None):
    """Import a theme from .tr file."""
    from pathlib import Path

    from trcc._boot import trcc as _trcc
    from trcc.adapters.infra.dc_config import DcConfig
    from trcc.adapters.infra.dc_parser import load_config_json
    from trcc.adapters.infra.dc_writer import import_theme as _import_fn
    from trcc.services import ThemeService
    from trcc.ui.cli._display import _connect_or_fail

    log.debug("import_theme path=%s device=%s", file_path, device)
    if (rc := _connect_or_fail(device)):
        return rc

    if (lcd := _trcc().lcd_device) is None:
        print("Error: No LCD device connected.")
        return 1
    w, h = lcd.lcd_size
    data_dir = _trcc().settings.user_data_dir

    theme_svc = ThemeService(
        import_theme_fn=_import_fn,
        load_config_json_fn=load_config_json,
        dc_config_cls=DcConfig,
    )
    ok, result = theme_svc.import_tr(
        Path(file_path), data_dir, (w, h))
    if ok and not isinstance(result, str):
        print(f"Imported: {result.name}")
    else:
        print(result)
    return 0 if ok else 1
