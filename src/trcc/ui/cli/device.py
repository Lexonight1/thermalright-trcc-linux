"""CLI `device` group — discover / connect / disconnect."""
from __future__ import annotations

import logging

import typer

from ...core.commands import (
    ConnectDevice,
    DisconnectDevice,
    DiscoverDevices,
    ResetDevice,
    SetActiveDevice,
)
from ._ctx import get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="Discover and connect to TRCC devices.",
                  no_args_is_help=True)


@app.command("list")
def list_devices() -> None:
    """List devices currently attached to the host."""
    log.info("cli device list")
    result = get_app().dispatch(DiscoverDevices())
    if not result.products:
        typer.echo("No supported devices found.")
        raise typer.Exit(code=1)
    typer.echo(f"{len(result.products)} device(s) found:")
    for product in result.products:
        typer.echo(
            f"  {product.key}  {product.vendor} {product.product}  "
            f"(wire={product.wire.value}, "
            f"resolution={product.native_resolution[0]}×{product.native_resolution[1]})"
        )


@app.command("connect")
def connect(key: str = typer.Argument(..., help="Device key, e.g. 0402:3922")) -> None:
    """Open USB transport and perform the wire-protocol handshake."""
    log.info("cli device connect: key=%s", key)
    result = get_app().dispatch(ConnectDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        for hint in result.hints:
            typer.echo(f"  hint: {hint}")
        raise typer.Exit(code=1)
    if result.handshake:
        h = result.handshake
        typer.echo(f"  resolution: {h.resolution[0]}×{h.resolution[1]}")
        typer.echo(f"  model_id:   {h.model_id}")
        if h.serial:
            typer.echo(f"  serial:     {h.serial}")


@app.command("disconnect")
def disconnect(key: str = typer.Argument(...)) -> None:
    """Close the transport and drop the device."""
    log.info("cli device disconnect: key=%s", key)
    result = get_app().dispatch(DisconnectDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("select")
def select(
    ordinal: int = typer.Argument(
        ...,
        help="1-based ordinal of the attached device to mark active "
             "(matches `device list` output)",
    ),
) -> None:
    """Persist the active-device selection by ordinal.

    Multi-device hosts (e.g. two LCDs + one LED controller) need a way
    to point CLI commands at "the one I'm steering today".  Resolves
    the ordinal against ``device list`` and stores the resulting key
    in ``AppSettings.active_device``.
    """
    log.info("cli device select: ordinal=%s", ordinal)
    app_obj = get_app()
    listing = app_obj.dispatch(DiscoverDevices())
    if not listing.products:
        typer.echo("No supported devices found.")
        raise typer.Exit(code=1)
    if ordinal < 1 or ordinal > len(listing.products):
        typer.echo(
            f"ordinal {ordinal} out of range (1-{len(listing.products)} "
            f"attached)",
        )
        raise typer.Exit(code=1)
    key = listing.products[ordinal - 1].key
    result = app_obj.dispatch(SetActiveDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("reset")
def reset(key: str = typer.Argument(...)) -> None:
    """Disconnect + clear cached state for a device.

    Use this when the LCD seems stuck — drops any cached frame, theme,
    and runtime counters.  Re-running `connect` after this starts
    completely fresh.
    """
    log.info("cli device reset: key=%s", key)
    result = get_app().dispatch(ResetDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
