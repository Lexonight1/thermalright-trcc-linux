"""CLI `device` group — discover / connect / disconnect."""
from __future__ import annotations

import typer

from ...core.commands import ConnectDevice, DisconnectDevice, DiscoverDevices
from ._ctx import get_app

app = typer.Typer(help="Discover and connect to TRCC devices.",
                  no_args_is_help=True)


@app.command("list")
def list_devices() -> None:
    """List devices currently attached to the host."""
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
    result = get_app().dispatch(ConnectDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
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
    result = get_app().dispatch(DisconnectDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
