"""CLI ``theme`` group — save / export / import themes."""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from ...core.commands import (
    CreateTheme,
    DeleteTheme,
    DeviceCanvas,
    DownloadCloudTheme,
    ExportCurrentTheme,
    ExportDcTheme,
    ExportOverlay,
    ExportTheme,
    ImportTheme,
    ListCloudThemes,
    ListThemes,
    ListWebThemes,
    LoadCloudTheme,
    SaveTheme,
)
from ...core.commands.theme import METRIC_SPEC_USAGE
from ...core.models import MEDIA, MediaKind
from ._ctx import DeviceKey, get_app, resolution_for

log = logging.getLogger(__name__)

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
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Replace an existing theme of that name.  The save stages and "
             "swaps, so a failed overwrite leaves the previous theme intact.",
    ),
) -> None:
    """Duplicate the device's active theme directory under a new name."""
    log.info("cli theme save: key=%s name=%s overwrite=%s",
             key, name, overwrite)
    result = get_app().dispatch(
        SaveTheme(key=key, name=name, overwrite=overwrite),
    )
    typer.echo(result.message)
    if not result.ok:
        # A name collision is the one refusal the user can act on, so say
        # how -- the GUIs raise a confirm dialog here and the CLI cannot.
        if result.target_exists:
            typer.echo("Re-run with --overwrite to replace it.", err=True)
        raise typer.Exit(code=1)


_MEDIA_HELP = (f"picture ({', '.join(sorted(MEDIA.exts(MediaKind.IMAGE)))}) or "
               f"video ({', '.join(sorted(MEDIA.exts(MediaKind.ANIMATED)))})")


@app.command("create")
def create(
    key: DeviceKey,
    name: str = typer.Argument(..., help="Theme name to save as"),
    background: Path = typer.Option(
        ..., "--bg", "-b", help=f"Background {_MEDIA_HELP}",
    ),
    mask: Path | None = typer.Option(
        None, "--mask",
        help="Optional mask picture; also kept in the mask library as "
             "custom_<file name>",
    ),
    overlay: Path | None = typer.Option(
        None, "--overlay",
        help='Optional overlay file, {"elements": [...]} -- before any --metric',
    ),
    metric: list[str] = typer.Option(
        [], "--metric", "-m",
        help=f"Overlay {METRIC_SPEC_USAGE}  Repeatable.",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace an existing theme of that name.",
    ),
) -> None:
    """One-shot theme builder: background + optional mask + overlay -> save.

    Mirrors legacy ``trcc theme --save``.  Dispatches :class:`CreateTheme`,
    the Command the API's create-theme dispatches too, which reads every
    input before the panel changes: bad input changes nothing.
    """
    log.info(
        "cli theme create: key=%s name=%s background=%s mask=%s overlay=%s "
        "metric=%s overwrite=%s",
        key, name, background, mask, overlay, metric, overwrite,
    )
    result = get_app().dispatch(CreateTheme(
        key=key, background=background, mask=mask, overlay=overlay,
        metrics=tuple(metric), name=name, overwrite=overwrite,
    ))
    if result.ok:
        typer.echo(result.message)
        return
    typer.echo(result.message, err=True)
    if result.target_exists:
        typer.echo("Re-run with --overwrite to replace it.", err=True)
    raise typer.Exit(code=1)


@app.command("export")
def export(
    key: str = typer.Argument(
        ..., help="Device key (e.g. 0402:3922) whose resolution scopes the lookup",
    ),
    theme_name: str = typer.Argument(
        ..., help="A saved theme of the device's size, by name (a module's while it is on)",
    ),
    archive_path: Path = typer.Argument(
        ..., help="Destination archive path (e.g. theme.tr)",
    ),
) -> None:
    """Zip a theme into an archive file."""
    log.info(
        "cli theme export: key=%s theme_name=%s archive_path=%s",
        key, theme_name, archive_path,
    )
    result = get_app().dispatch(
        ExportTheme(key=key, theme_name=theme_name, archive_path=archive_path),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("export-current")
def export_current(
    key: str = typer.Argument(..., help="Device key (e.g. 0402:3922)"),
    archive_path: Path = typer.Argument(
        ..., help="Destination: .tr (the Windows app's format) or .zip",
    ),
) -> None:
    """Export what the panel shows, saved or not -- the Windows app's export."""
    log.info("cli theme export-current: key=%s archive_path=%s",
             key, archive_path)
    result = get_app().dispatch(
        ExportCurrentTheme(key=key, archive_path=archive_path),
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
    log.info(
        "cli theme import: key=%s archive_path=%s name=%s",
        key, archive_path, name,
    )
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
    log.info("cli theme list: key=%s directory=%s", key, directory)
    if directory is not None:
        result = get_app().dispatch(ListThemes(directory=directory))
    elif key:
        result = get_app().dispatch(ListThemes(resolution=resolution_for(key)))
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
    log.info("cli theme delete: path=%s", path)
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
    log.info("cli theme cloud-list: category=%s", category)
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


@app.command("cloud-downloaded")
def cloud_downloaded(
    key: DeviceKey,
    resolution: str | None = typer.Option(
        None, "--resolution", "-r", metavar="WxH",
        help="Canvas to list for (e.g. 320x320). Needed when the device "
             "isn't connected — a scan reports no canvas, only a handshake does.",
    ),
) -> None:
    """List the cloud themes actually DOWNLOADED for this device.

    ``cloud-list`` shows Thermalright's hosted catalog — what EXISTS. This shows
    what is on disk for the canvas, which is a different question and had no
    answer outside the REST API.

    The directory is REPORTED by the query rather than re-spelled from the
    resolution: a per-SKU panel reads ``1600720l`` while one whose variant
    archive has not landed falls back to the generic name.
    """
    log.info("cli theme cloud-downloaded: key=%s resolution=%s", key, resolution)
    app_obj = get_app()
    if resolution is not None:
        try:
            w_s, h_s = resolution.lower().split("x")
            canvas = (int(w_s), int(h_s))
        except ValueError:
            raise typer.BadParameter(
                f"--resolution must look like 320x320, got {resolution!r}",
            ) from None
    else:
        # The one ladder: the handshake, the scanned size, the registry.
        found = app_obj.dispatch(DeviceCanvas(key=key))
        if not found.ok:
            typer.echo(
                f"{found.message}.  Or pass --resolution WxH to list a "
                "library without the hardware.",
                err=True,
            )
            raise typer.Exit(code=1)
        canvas = (found.width, found.height)
    w, h = canvas
    result = app_obj.dispatch(ListWebThemes(width=w, height=h, key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    for e in result.entries:
        typer.echo(f"  {e.id:8}  {e.category:10}"
                   f"{'  (video)' if e.has_video else ''}")


@app.command("cloud-download")
def cloud_download(
    theme_id: str = typer.Argument(..., help="Cloud theme id, e.g. a001"),
    resolution: str = typer.Option(
        ..., "--resolution", "-r", metavar="WxH",
        help="Oriented catalog size, e.g. 320x320 or 480x854.",
    ),
) -> None:
    """Cache a cloud theme locally WITHOUT applying it to a device.

    ``cloud-load`` downloads AND applies — it persists the background and
    starts playback.  This is the download half on its own: useful for
    pre-fetching a catalog over a slow link, or warming the cache before a
    demo, without disturbing what a panel is currently showing.

    Needs no device and no connection.  Idempotent: an already-cached theme
    is not fetched again.

    ``--resolution`` is the ORIENTED catalog size the cloud library is keyed
    by (854x480 and 480x854 are different libraries), not necessarily the
    panel's native size.
    """
    log.info("cli theme cloud-download: theme_id=%s resolution=%s",
             theme_id, resolution)
    from ...core.models import parse_resolution
    try:
        w, h = parse_resolution(resolution)
    except ValueError as e:
        typer.echo(f"Bad --resolution {resolution!r}: {e}", err=True)
        raise typer.Exit(code=1) from e
    result = get_app().dispatch(
        DownloadCloudTheme(theme_id=theme_id, resolution=(w, h)),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("cloud-load")
def cloud_load(
    key: DeviceKey,
    theme_id: str = typer.Argument(..., help="Cloud theme id, e.g. a001"),
) -> None:
    """Download a cloud theme and load it on a device."""
    log.info("cli theme cloud-load: key=%s theme_id=%s", key, theme_id)
    app_obj = get_app()
    # LoadCloudTheme dispatches PlayVideo on the wire; this stateless CLI
    # process must attach the device first or it fails "Not attached".
    # Idempotent when a daemon/GUI already holds it.  (#150)
    result = app_obj.dispatch(LoadCloudTheme(key=key, theme_id=theme_id))
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
        ..., help="A saved theme of the device's size, by name (a module's while it is on)",
    ),
    output_path: Path = typer.Argument(
        ..., help="Where to write the config1.dc file",
    ),
) -> None:
    """Write a theme out as legacy ``config1.dc`` for Windows TRCC users."""
    log.info(
        "cli theme export-dc: key=%s theme_name=%s output_path=%s",
        key, theme_name, output_path,
    )
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
        ..., help="A saved theme of the device's size, by name (a module's while it is on)",
    ),
    output_path: Path = typer.Argument(
        ..., help="Where to write the overlay layout file",
    ),
) -> None:
    """Export just a theme's overlay layout (the metric grid) for sharing —
    lighter than the whole-theme zip and distinct from the DC binary."""
    log.info(
        "cli theme export-overlay: key=%s theme_name=%s output_path=%s",
        key, theme_name, output_path,
    )
    result = get_app().dispatch(ExportOverlay(
        key=key,
        theme_name=theme_name,
        output_path=output_path,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
