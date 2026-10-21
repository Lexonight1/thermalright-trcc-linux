"""``trcc hello`` -- what Hello adds to the CLI.

A module's ``ui/cli/`` exports two fixed names: ``GROUP``, the ``trcc``
group its commands join (``""`` for the top level), and ``COMMANDS``, a Typer
app holding them.  They are added for every installed module, on or off; an
off module's command still runs, and the App says it is off.
"""
from __future__ import annotations

import logging

import typer

from trcc.ui.cli._ctx import get_app

from ...core import Greet

log = logging.getLogger(__name__)

GROUP = ""
COMMANDS = typer.Typer()


@COMMANDS.command("hello")
def hello(name: str = typer.Argument("world", help="Who to greet.")) -> None:
    """Say hello -- the module skeleton's command."""
    log.info("cli hello: %s", name)
    result = get_app().dispatch(Greet(name=name))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
