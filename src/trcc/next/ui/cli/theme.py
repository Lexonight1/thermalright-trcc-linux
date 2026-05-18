"""CLI ``theme`` group — save / export / import themes."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import ExportTheme, ImportTheme, SaveTheme
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
    theme_name: str = typer.Argument(
        ..., help="Theme name (directory under user_content_dir)",
    ),
    archive_path: Path = typer.Argument(
        ..., help="Destination archive path (e.g. theme.tr)",
    ),
) -> None:
    """Zip a theme into an archive file."""
    result = get_app().dispatch(
        ExportTheme(theme_name=theme_name, archive_path=archive_path),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("import")
def import_(
    archive_path: Path = typer.Argument(
        ..., help="Archive to unpack",
        exists=True, file_okay=True, dir_okay=False,
    ),
    name: str = typer.Argument(
        "", help="Theme name (defaults to archive filename stem)",
    ),
) -> None:
    """Unpack a theme archive into user_content_dir."""
    result = get_app().dispatch(
        ImportTheme(archive_path=archive_path, name=name),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
