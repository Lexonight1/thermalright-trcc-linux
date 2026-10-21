"""The Audio Visualizer's ``trcc display`` commands -- the names they always
had (``visuals``, ``visual``, ``visual-set``, ``visual-picture``,
``visual-tile``, ``visual-center``).

Added for every installed module, on or off; while it is off each answers
that it is off and where to turn it on.  ``trcc display visual KEY`` (no
visual) stops it through the App's own ``ClearModuleSource``, so it works
with the module off too.
"""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from trcc.core.commands import ClearModuleSource
from trcc.ui.cli._ctx import dispatch_echo, get_app

from ...core import (
    ListVisuals,
    PlayVisual,
    SetVisualCenter,
    SetVisualPicture,
    SetVisualSettings,
    VisualTile,
)

log = logging.getLogger(__name__)

GROUP = "display"
COMMANDS = typer.Typer()


@COMMANDS.command("visuals")
def visuals(
    key: str = typer.Argument("", help="A device key: also show what it plays."),
) -> None:
    """List the audio visuals this computer can draw, and their presets."""
    log.info("cli display visuals: key=%r", key)
    result = get_app().dispatch(ListVisuals(key=key))
    for info in result.visuals:
        sliders = ", ".join(f"{a.name} {a.minimum:g}-{a.maximum:g}"
                            for a in info.adjustments)
        typer.echo(f"  {info.kind:<12} {info.title}  ({sliders})")
        for name in info.presets:
            typer.echo(f"    preset: {name}")
    typer.echo(f"  centre picture: {result.center or 'none'}")
    if key:
        sliders = ", ".join(f"{n} {v:g}" for n, v in result.sliders.items())
        typer.echo(f"  {key} plays: {result.playing or 'no audio visual'}"
                   + (f" {result.preset}" if result.preset else "")
                   + (f" ({sliders})" if sliders else "")
                   + (" -- hearing nothing" if result.playing
                      and not result.hearing else ""))


def _visual_settings(pairs: list[str]) -> dict[str, float]:
    """``NAME=VALUE`` pairs as slider values."""
    log.debug("_visual_settings: %s", pairs)
    values: dict[str, float] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        try:
            values[name.strip()] = float(value)
        except ValueError:
            raise typer.BadParameter(f"a setting is NAME=VALUE -- got {pair!r}") from None
        if not sep or not name.strip():
            raise typer.BadParameter(f"a setting is NAME=VALUE -- got {pair!r}")
    return values


@COMMANDS.command("visual")
def visual(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    name: str = typer.Argument(
        "", help="The audio visual (see `trcc display visuals`); '' stops it."),
    preset: str = typer.Option(
        "", "--preset", help="A preset file, for a visual that draws presets."),
    setting: list[str] | None = typer.Option(
        None, "--set", help="NAME=VALUE for one slider; repeat for more."),
) -> None:
    """Draw the panel's background from what is playing, with an audio
    visual -- or stop it."""
    log.info("cli display visual: key=%s name=%r preset=%r", key, name, preset)
    command = (PlayVisual(key=key, visual=name, preset=preset,
                          settings=_visual_settings(setting or []))
               if name else ClearModuleSource(key=key, module="visualizer"))
    dispatch_echo(command)


@COMMANDS.command("visual-set")
def visual_set(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    setting: list[str] = typer.Option(
        ..., "--set", help="NAME=VALUE for one slider; repeat for more."),
) -> None:
    """Move the playing audio visual's sliders -- live, it keeps playing."""
    log.info("cli display visual-set: key=%s %s", key, setting)
    dispatch_echo(SetVisualSettings(key=key, settings=_visual_settings(setting)))


@COMMANDS.command("visual-picture")
def visual_picture(
    shown: str = typer.Argument("", help="on or off -- '' leaves it as it is."),
    fire: float | None = typer.Option(
        None, "--fire", help="How hot the picture burns, 0 to 1."),
) -> None:
    """Switch the centre picture on or off (it is kept while off) and set
    its fire."""
    log.info("cli display visual-picture: %r fire=%s", shown, fire)
    if shown not in ("", "on", "off"):
        raise typer.BadParameter(f"on or off -- got {shown!r}")
    dispatch_echo(SetVisualPicture(shown=None if not shown else shown == "on",
                                   fire=fire))


@COMMANDS.command("visual-tile")
def visual_tile(
    name: str = typer.Argument(..., help="The audio visual (see `trcc display visuals`)."),
    output: Path = typer.Option(..., "--output", "-o", help="Where to write the PNG."),
    preset: str = typer.Option("", "--preset", help="A preset, for a visual that draws presets."),
    size: int = typer.Option(120, "--size", help="Pixels a side."),
) -> None:
    """Save a visual's gallery tile: what it draws for noise."""
    log.info("cli display visual-tile: %s %r size=%d -> %s", name, preset,
             size, output)
    result = get_app().dispatch(VisualTile(visual=name, preset=preset, size=size))
    if not result.ok:
        typer.echo(result.message)
        raise typer.Exit(code=1)
    output.write_bytes(result.image)
    typer.echo(f"Saved {output} ({len(result.image)} bytes)")


@COMMANDS.command("visual-center")
def visual_center(
    path: str = typer.Argument(
        "", help="Any picture for the middle of a visual; '' for none."),
) -> None:
    """The picture in the middle of the audio visuals that show one."""
    log.info("cli display visual-center: %r", path)
    dispatch_echo(SetVisualCenter(path=path))
