"""``trcc system modules`` -- list the modules, switch one on or off, and get
more from the signed index.

The CLI's Modules page, under ``system`` as the gui keeps it in the Control
Center and the API at ``/system/modules``: the same ``ListModules`` /
``SetModuleEnabled`` / ``BrowseModules`` / ``InstallModule`` / ``RemoveModule``
every other UI sends.  The App does the installing; this asks and prints.
"""
from __future__ import annotations

import logging

import typer

from ...core.commands import (
    BrowseModules,
    InstallModule,
    ListModules,
    RemoveModule,
    SetModuleEnabled,
)
from ...core.models import AvailableModule
from ..presentation.module_catalog import consent, removal_question
from ._ctx import get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="Modules: list them, switch one on or off, get more.",
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


def _listed() -> tuple[AvailableModule, ...]:
    """What the index lists -- or the command fails, saying why."""
    log.info("cli module: reading the index")
    result = get_app().dispatch(BrowseModules())
    if not result.ok:
        typer.echo(result.message)
        raise typer.Exit(code=1)
    return result.modules


@app.command("available")
def available() -> None:
    """Every module you can install, and the ones you have."""
    log.info("cli system modules available")
    for m in _listed():
        typer.echo(f"  [{m.status:<9}] {m.key:<16} {m.version:<8} {m.title} -- {m.summary}")
        if m.status == "update":
            typer.echo(f"        installed: {m.installed_version}")
        if m.reason:
            typer.echo(f"        taken back: {m.reason}")


@app.command("install")
def install(key: str = typer.Argument(..., help="The module's key"),
            yes: bool = typer.Option(False, "--yes", "-y",
                                     help="Accept what it asks for without asking")) -> None:
    """Install a module from the list, or update it -- after saying what it may do."""
    log.info("cli system modules install: %s yes=%s", key, yes)
    module = next((m for m in _listed() if m.key == key), None)
    if module is None:
        typer.echo(f"no module {key!r} in the list -- see: trcc system modules available")
        raise typer.Exit(code=1)
    question, lines = consent(module, str)
    typer.echo("\n".join(lines))
    if not yes and not typer.confirm(question):
        log.info("cli system modules install: %s declined", key)
        raise typer.Exit(code=1)
    result = get_app().dispatch(InstallModule(key=key, capabilities=module.capabilities,
                                              network_hosts=module.network_hosts))
    typer.echo(result.message)
    if result.note:
        typer.echo(result.note)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("remove")
def remove(key: str = typer.Argument(..., help="The module's key"),
           yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask first")) -> None:
    """Remove a module you installed.  Themes made with it stay."""
    log.info("cli system modules remove: %s yes=%s", key, yes)
    if not yes and not typer.confirm(removal_question(key, str)):
        log.info("cli system modules remove: %s declined", key)
        raise typer.Exit(code=1)
    result = get_app().dispatch(RemoveModule(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
