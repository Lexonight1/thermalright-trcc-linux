"""CLI ``theme`` group — save / export / import themes."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import (
    DeleteTheme,
    ExportDcTheme,
    ExportTheme,
    ImportTheme,
    ListCloudThemes,
    ListThemes,
    LoadCloudTheme,
    SaveTheme,
)
from ._ctx import get_app

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
