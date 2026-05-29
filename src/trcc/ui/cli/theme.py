"""CLI ``theme`` group — save / export / import themes."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import (
    AddOverlayElement,
    DeleteTheme,
    ExportConfig,
    ExportDcTheme,
    ExportOverlay,
    ExportTheme,
    ImportConfig,
    ImportTheme,
    ListCloudThemes,
    ListThemes,
    LoadCloudTheme,
    LoadImage,
    SaveTheme,
    UploadCustomMask,
)
from ._ctx import get_app

_METRIC_USAGE = (
    "metric spec: 'metric_key:x,y[:color[:size]]' — e.g. "
    "'cpu:temp:160,90:#ff8800:24'.  color defaults to '#ffffff', "
    "size defaults to 16."
)


def _parse_metric_spec(spec: str) -> dict[str, object]:
    """Parse a ``--metric`` arg into kwargs for AddOverlayElement."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise typer.BadParameter(
            f"Invalid metric spec {spec!r}; {_METRIC_USAGE}",
        )
    metric_key, coords, *rest = parts
    try:
        x_str, y_str = coords.split(",")
        x, y = int(x_str), int(y_str)
    except (ValueError, IndexError) as e:
        raise typer.BadParameter(
            f"Invalid coords in {spec!r}; expected 'x,y' got {coords!r}",
        ) from e
    color = rest[0] if rest else "#ffffff"
    if color and not color.startswith("#"):
        color = f"#{color}"
    try:
        size = int(rest[1]) if len(rest) >= 2 else 16
    except ValueError as e:
        raise typer.BadParameter(
            f"Invalid size in {spec!r}; expected int got {rest[1]!r}",
        ) from e
    return {
        "metric": metric_key,
        "x": x, "y": y,
        "color": color,
        "size": size,
    }

app = typer.Typer(
    help="Save / export / import themes.",
    no_args_is_help=True,
)


@app.command("save")
def save(
    key: str = typer.Argument(..., help="Device key whose active theme to save"),
    name: str = typer.Argument(
        ..., help="New theme name (directory under user_content_dir)",
    ),
) -> None:
    """Duplicate the device's active theme directory under a new name."""
    result = get_app().dispatch(SaveTheme(key=key, name=name))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("create")
def create(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    name: str = typer.Argument(..., help="Theme name to save as"),
    background: Path = typer.Option(
        ..., "--bg", "-b", help="Background image (PNG/JPG/BMP/WEBP)",
    ),
    mask: Path | None = typer.Option(
        None, "--mask",
        help="Optional mask PNG to overlay (custom_<name>/01.png)",
    ),
    metric: list[str] = typer.Option(
        [], "--metric", "-m", help=f"Overlay {_METRIC_USAGE}  Repeatable.",
    ),
) -> None:
    """One-shot theme builder: bg + optional mask + overlay metrics → save.

    Mirrors legacy ``trcc theme --save``.  Dispatches a chain of
    existing Commands: :class:`LoadImage` for the background,
    :class:`UploadCustomMask` if ``--mask`` given,
    :class:`AddOverlayElement` per ``--metric`` arg, then
    :class:`SaveTheme` to persist the result.  Stops on the first
    failure and leaves the device in whatever state was reached.
    """
    app_obj = get_app()

    bg_result = app_obj.dispatch(LoadImage(key=key, path=background))
    if not bg_result.ok:
        typer.echo(bg_result.message, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Loaded background: {background.name}")

    if mask is not None:
        m_result = app_obj.dispatch(UploadCustomMask(key=key, source=mask))
        if not m_result.ok:
            typer.echo(m_result.message, err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Applied mask: {mask.name}")

    for spec in metric:
        kwargs = _parse_metric_spec(spec)
        elem = app_obj.dispatch(AddOverlayElement(
            key=key, type="metric", **kwargs,  # type: ignore[arg-type]
        ))
        if not elem.ok:
            typer.echo(elem.message, err=True)
            raise typer.Exit(code=1)
        typer.echo(
            f"Added metric {kwargs['metric']} at "
            f"({kwargs['x']},{kwargs['y']})",
        )

    save_result = app_obj.dispatch(SaveTheme(key=key, name=name))
    typer.echo(save_result.message)
    if not save_result.ok:
        raise typer.Exit(code=1)


@app.command("export")
def export(
    key: str = typer.Argument(
        ..., help="Device key (e.g. 0402:3922) whose resolution scopes the lookup",
    ),
    theme_name: str = typer.Argument(
        ..., help="Theme name (directory under user_theme_dir(w, h))",
    ),
    archive_path: Path = typer.Argument(
        ..., help="Destination archive path (e.g. theme.tr)",
    ),
) -> None:
    """Zip a theme into an archive file."""
    result = get_app().dispatch(
        ExportTheme(key=key, theme_name=theme_name, archive_path=archive_path),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("import")
def import_(
    key: str = typer.Argument(
        ..., help="Device key (e.g. 0402:3922) whose resolution scopes the target",
    ),
    archive_path: Path = typer.Argument(
        ..., help="Archive to unpack",
        exists=True, file_okay=True, dir_okay=False,
    ),
    name: str = typer.Argument(
        "", help="Theme name (defaults to archive filename stem)",
    ),
) -> None:
    """Unpack a theme archive into the device's per-resolution theme dir."""
    result = get_app().dispatch(
        ImportTheme(key=key, archive_path=archive_path, name=name),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("list")
def list_(
    key: str | None = typer.Argument(
        None,
        help=("Device key (e.g. 0402:3922) — its resolution scopes the scan. "
              "Required unless --dir is given."),
    ),
    directory: Path | None = typer.Option(
        None, "--dir", "-d",
        help="Override: scan an explicit directory instead of the device's theme dirs.",
        exists=False, file_okay=False, dir_okay=True,
    ),
) -> None:
    """List themes for a device resolution.

    By default scans both ``data/theme{W}{H}`` (pkg + GitHub-downloaded)
    and ``user_content_dir/data/theme{W}{H}`` (legacy user-saved
    location) so installed-user themes show up alongside fresh
    downloads.
    """
    if directory is not None:
        result = get_app().dispatch(ListThemes(directory=directory))
    elif key:
        app_obj = get_app()
        device = app_obj.devices.get(key)
        if device is None or device.profile is None:
            typer.echo(
                f"Device {key} not connected — connect first so we know "
                "the target resolution",
                err=True,
            )
            raise typer.Exit(code=1)
        result = app_obj.dispatch(
            ListThemes(resolution=device.profile.resolution),
        )
    else:
        typer.echo("Provide a device key, or --dir DIRECTORY.", err=True)
        raise typer.Exit(code=1)
    typer.echo(result.message)
    for theme in result.themes:
        w, h = theme.resolution
        typer.echo(f"  {theme.name:30} {w}x{h}  {theme.path}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("delete")
def delete(
    path: Path = typer.Argument(
        ..., help="Absolute path to the theme directory to delete",
    ),
) -> None:
    """Delete a theme directory.

    Path-based to match legacy's ``delete_theme(lcd, path)`` — the
    caller already has the resolved path from ``theme list`` output.
    """
    result = get_app().dispatch(DeleteTheme(path=path))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("cloud-list")
def cloud_list(
    category: str = typer.Option(
        "all", "--category", "-c",
        help="Category prefix: 'all' / 'a' / 'b' / 'c' / 'd' / 'e' / 'y'",
    ),
) -> None:
    """List themes in Thermalright's hosted catalog."""
    result = get_app().dispatch(ListCloudThemes(category=category))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    # Print category table once when listing 'all'.
    if category == "all":
        for c in result.categories:
            typer.echo(f"  [{c.prefix}] {c.name:10}  {c.count} themes")
        typer.echo("")
    for t in result.themes:
        typer.echo(f"  {t.id:6}  {t.category_name}")


@app.command("cloud-load")
def cloud_load(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    theme_id: str = typer.Argument(..., help="Cloud theme id, e.g. a001"),
) -> None:
    """Download a cloud theme and load it on a device."""
    result = get_app().dispatch(LoadCloudTheme(key=key, theme_id=theme_id))
    typer.echo(result.message)
    if result.theme_path:
        typer.echo(f"  staged at: {result.theme_path}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("export-config")
def export_config(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    output_path: Path = typer.Argument(
        ..., help="Destination JSON path (e.g. mydevice.json)",
    ),
) -> None:
    """Snapshot one device's settings to a JSON file.

    Captures everything in ``DeviceSettings``: active theme path,
    brightness, orientation, overlay edits, mask choice, format prefs.
    Pair with :command:`trcc theme import-config` to restore on
    another host or after a wipe.
    """
    result = get_app().dispatch(
        ExportConfig(key=key, output_path=output_path),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("import-config")
def import_config(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    input_path: Path = typer.Argument(
        ..., help="Source JSON written by `trcc theme export-config`",
    ),
) -> None:
    """Restore one device's settings from an export-config JSON file."""
    result = get_app().dispatch(
        ImportConfig(key=key, input_path=input_path),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("export-dc")
def export_dc(
    key: str = typer.Argument(
        ..., help="Device key (e.g. 0402:3922) — its resolution scopes the lookup "
                  "and layers the user's overlay elements into the export",
    ),
    theme_name: str = typer.Argument(
        ..., help="Theme name (directory under user_theme_dir(w, h))",
    ),
    output_path: Path = typer.Argument(
        ..., help="Where to write the config1.dc file",
    ),
) -> None:
    """Write a theme out as legacy ``config1.dc`` for Windows TRCC users."""
    result = get_app().dispatch(ExportDcTheme(
        key=key,
        theme_name=theme_name,
        output_path=output_path,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("export-overlay")
def export_overlay(
    key: str = typer.Argument(
        ..., help="Device key (e.g. 0402:3922) — its resolution scopes the lookup",
    ),
    theme_name: str = typer.Argument(
        ..., help="Theme name (directory under user_theme_dir(w, h))",
    ),
    output_path: Path = typer.Argument(
        ..., help="Where to write the overlay layout file",
    ),
) -> None:
    """Export just a theme's overlay layout (the metric grid) for sharing —
    lighter than the whole-theme zip and distinct from the DC binary."""
    result = get_app().dispatch(ExportOverlay(
        key=key,
        theme_name=theme_name,
        output_path=output_path,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
