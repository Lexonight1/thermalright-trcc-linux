"""``trcc system modules`` -- list the modules, switch one on or off.

The CLI's Modules page, under ``system`` as the gui keeps it in the Control
Center and the API at ``/system/modules``: the same ``ListModules`` /
``SetModuleEnabled`` every other UI sends.
"""
from __future__ import annotations

import logging

import typer

from ...core.commands import ListModules, SetModuleEnabled
from ._ctx import get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="Modules: list them, switch one on or off.",
                  no_args_is_help=True)


@app.command("list")
def list_modules() -> None:
    """Every module, on or off, and why one cannot run here."""
    log.info("cli module list")
    for m in get_app().dispatch(ListModules()).modules:
        state = "on " if m.enabled else "off"
        typer.echo(f"  [{state}] {m.key:<16} {m.title} -- {m.summary}")
        if m.problem:
            typer.echo(f"        cannot run here: {m.problem}")


def _switch(key: str, enabled: bool) -> None:
    """Switch *key*, print what happened, fail the command on a refusal."""
    log.info("cli module %s: %s", "on" if enabled else "off", key)
    result = get_app().dispatch(SetModuleEnabled(key=key, enabled=enabled))
    typer.echo(result.message)
    if result.note:
        typer.echo(result.note)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("on")
def module_on(key: str = typer.Argument(..., help="The module's key")) -> None:
    """Switch a module on."""
    log.info("cli system modules on: %s", key)
    _switch(key, True)


@app.command("off")
def module_off(key: str = typer.Argument(..., help="The module's key")) -> None:
    """Switch a module off."""
    log.info("cli system modules off: %s", key)
    _switch(key, False)
