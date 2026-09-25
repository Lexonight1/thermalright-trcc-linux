"""CLI `device` group — discover / connect / disconnect."""
from __future__ import annotations

import logging

import typer

from ...core.commands import (
    ConnectDevice,
    DeviceCanvas,
    DeviceConnectionIssues,
    DeviceState,
    DisconnectDevice,
    DiscoverDevices,
    ResetDevice,
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
    for key, product in result.units():
        # ``DiscoverDevices`` never handshakes (``test_it_does_not_scan_the_bus``
        # pins that), so this figure is the CATALOG's, not the panel's.  One USB
        # id covers panels from 240x320 to 1280x480, and printing bare digits
        # made a guess indistinguishable from a measurement: #268's reporter
        # compared this line against ``trcc system hid-debug`` and correctly
        # concluded one of them was lying.  (0,0) is how the registry already
        # spells "not declared" -- the LED controller row uses it.
        w, h = product.native_resolution
        size = f"{w}×{h} (catalog)" if (w, h) != (0, 0) else "unknown"
        typer.echo(
            f"  {key}  {product.vendor} {product.product}  "
            f"(wire={product.wire.value}, resolution={size})"
        )
    typer.echo(
        "Resolutions above come from the catalog, not the panel.  "
        "Run 'trcc device connect <key>' to read the panel's own."
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


@app.command("issues")
def issues() -> None:
    """Show devices that failed to connect, and why.

    A connect can fail before anything is watching the bus, so the failure
    is pulled with a query rather than only published — the same one the
    GUIs and ``GET /devices/issues`` use.
    """
    log.info("cli device issues")
    result = get_app().dispatch(DeviceConnectionIssues())
    if not result.issues:
        typer.echo("No connection issues.")
        return
    typer.echo(f"{len(result.issues)} device(s) failed to connect:")
    for issue in result.issues:
        typer.echo(f"  {issue.key}  {issue.message}")
        for hint in issue.hints:
            typer.echo(f"    hint: {hint}")
    raise typer.Exit(code=1)


@app.command("state")
def state(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Show what a device IS — identity, connection, handshake geometry.

    ``native_resolution`` is what the product registry claims; ``resolution``
    is what the panel answered at handshake.  When they differ, the handshake
    wins and the difference is usually the thing worth reporting.
    """
    log.info("cli device state: key=%s", key)
    result = get_app().dispatch(DeviceState(key=key))
    if not result.ok:
        typer.echo(result.message)
        raise typer.Exit(code=1)
    for field in (
        "vendor", "product", "wire", "kind", "model", "native_resolution",
        "connected", "is_led", "resolution", "pm_byte", "sub_byte", "fbl",
        "jpeg", "rotate", "widescreen", "led_style",
    ):
        value = getattr(result, field)
        # None means "not handshaken yet" — distinct from 0 / False.  Say so
        # rather than printing a value the device never gave us.
        typer.echo(f"{field:20} {'(not handshaken)' if value is None else value}")


@app.command("canvas")
def canvas(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """The panel's NATIVE pixels — the size to AUTHOR an asset for.

    Not the size to DRAW a preview at: that folds the user orientation and
    the active theme's composition, while a theme, a mask or a Theme.zt is
    authored at the panel's own pixels and the firmware mounts it.

    ``source`` says which answer this is — the handshake the panel gave, the
    scan, or the product registry — because when a panel comes out the wrong
    shape, WHICH source won is the diagnostic.
    """
    log.info("cli device canvas: key=%s", key)
    result = get_app().dispatch(DeviceCanvas(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    typer.echo(f"{'width':20} {result.width}")
    typer.echo(f"{'height':20} {result.height}")
    typer.echo(f"{'source':20} {result.source}")


@app.command("disconnect")
def disconnect(key: str = typer.Argument(...)) -> None:
    """Close the transport and drop the device."""
    log.info("cli device disconnect: key=%s", key)
    result = get_app().dispatch(DisconnectDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("reset")
def reset(key: str = typer.Argument(...)) -> None:
    """Power-cycle a device: disconnect, reconnect, restore its display.

    Use this when the LCD seems stuck.  The connection is torn down and
    rebuilt and the persisted theme is put back, so the panel ends up
    showing what it showed before — no second `connect` needed.

    Not the same as blanking the panel to a known colour; that is
    `trcc display reset`, which leaves the device connected.
    """
    log.info("cli device reset: key=%s", key)
    result = get_app().dispatch(ResetDevice(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
