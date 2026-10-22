"""RGB Lighting's ``trcc system`` commands -- the names they always had
(``follow``, ``ram-lighting``, ``rgb``, ``ram-effect``, ``aura-effect``,
``linked-color``, ``aura-leds``, ``aura-blink``).

Added for every installed module, on or off; while it is off each answers
that it is off and where to turn it on.
"""
from __future__ import annotations

import logging

import typer

from trcc.core.models import RamAccessState
from trcc.modules.rgb.core.commands import (
    BlinkAuraHeader,
    RamLighting,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetAuraEffect,
    SetAuraLeds,
    SetLinkedColor,
    SetRamEffect,
    SetRamLighting,
    SetRgbFollow,
)
from trcc.modules.rgb.core.models import (
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    LinkedEffect,
    RamEffect,
    RgbFollowMode,
)
from trcc.modules.rgb.core.results import RgbLightsResult
from trcc.ui.cli._ctx import get_app

log = logging.getLogger(__name__)

GROUP = "system"
COMMANDS = typer.Typer()


@COMMANDS.command("follow")
def follow(
    mode: str = typer.Argument(
        "status", help="'off', 'openrgb', 'ram', 'board' or 'status'"),
    host: str = typer.Option(
        "", "--host", help="OpenRGB's SDK server (default: the saved one, "
                           "127.0.0.1 at first)."),
    port: int = typer.Option(
        0, "--port", help="Its port (default: the saved one, 6742 at first)."),
    source: str | None = typer.Option(
        None, "--source", help="The device that leads: an LCD's key "
        "(e.g. 0402:3922) to follow its picture, or '' for the first LED "
        "cooler.  Default: the saved one."),
    mapping: FollowMapping | None = typer.Option(
        None, "--mapping", help="An LCD's picture on the followers: 'halves' "
        "(left to the first, right to the second) or 'single' (one colour "
        "each).  Default: the saved one."),
    colors: FollowColors | None = typer.Option(
        None, "--colors", help="How a region of an LCD's picture becomes a "
        "light's colour: 'vivid' (its brightest pixels -- small bright "
        "details show) or 'smooth' (its average -- steadier).  Default: the "
        "saved one, vivid at first."),
    target: list[str] | None = typer.Option(
        None, "--target", help="A light that follows (a stick's "
        "'i2c-3/0x19', an OpenRGB device's name); repeat for more.  "
        "'--target all' for every one.  Default: the saved ones."),
) -> None:
    """Make other RGB follow the LED cooler's colours -- or an LCD's picture (#160).

    'openrgb': TRCC connects to OpenRGB's SDK server -- start it in OpenRGB's
    SDK Server tab -- and sends the cooler's colours to every OpenRGB device,
    in OpenRGB's direct mode.  'ram': TRCC sends them to Corsair RGB memory
    itself, with no OpenRGB -- close OpenRGB first, so the two never drive the
    same sticks.  'off' stops.
    """
    log.info("cli system follow: mode=%s host=%r port=%s source=%r "
             "mapping=%s colors=%s target=%s", mode, host, port, source,
             mapping, colors, target)
    choice = mode.lower()
    modes = [m.value for m in RgbFollowMode]
    if choice != "status" and choice not in modes:
        raise typer.BadParameter(
            f"mode must be one of {', '.join(modes)} or 'status', "
            f"got {mode!r}")
    result = get_app().dispatch(
        RgbFollow() if choice == "status" else
        SetRgbFollow(mode=RgbFollowMode(choice), host=host, port=port,
                     source=source, mapping=mapping, colors=colors,
                     targets=(None if target is None else
                              () if target == ["all"] else tuple(target))))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    typer.echo(f"  following: {result.mode.value} "
               f"(OpenRGB at {result.host}:{result.port})")
    typer.echo(f"  source   : {result.source or 'the first LED cooler'}"
               + (f" ({result.mapping.value}, {result.colors.value})"
                  if result.source else ""))
    typer.echo(f"  lights   : {', '.join(result.targets) or 'all'}")
    if result.lead:
        typer.echo(f"  cooler   : {result.lead}")
    if result.devices:
        typer.echo(f"  devices  : {', '.join(result.devices)}")
    if result.error:
        typer.echo(f"  problem  : {result.error}")


@COMMANDS.command("ram-lighting")
def ram_lighting(
    action: str = typer.Argument(
        "status", help="'enable', 'disable' or 'status'"),
) -> None:
    """Let TRCC reach RGB memory's lighting -- opt-in, asks for a password.

    RGB memory's lighting chips sit on the motherboard's SMBus, beside the
    chips that hold the memory's own settings.  Linux can only open the whole
    bus, so enabling lets programs you run reach all of it, not just TRCC.
    Only the person logged in at this computer gets access, and only that
    bus.  'disable' removes it completely.  It lasts across reboots.  Your
    desktop asks for the password; the answer comes when you type it or close
    the prompt.
    """
    log.info("cli system ram-lighting: %s", action)
    choice = action.lower()
    if choice not in ("enable", "disable", "status"):
        raise typer.BadParameter(
            f"action must be 'enable', 'disable' or 'status', got {action!r}")
    result = get_app().dispatch(
        RamLighting() if choice == "status" else
        SetRamLighting(enabled=choice == "enable"))
    typer.echo(result.message)
    if not result.ok:              # an error is its message: nothing beside it
        raise typer.Exit(code=1)
    typer.echo(f"  state: {result.state.value}")
    if result.command and result.state not in (RamAccessState.ON,
                                               RamAccessState.ELSEWHERE):
        typer.echo(f"  to enable: {result.command}")


def _echo_lights(result: RgbLightsResult) -> None:
    """The lights, one per line, with what TRCC last saved on each stick --
    or, for an error, its message alone: a refused or failed Command's other
    fields are defaults, not facts, as ``follow`` already treats them."""
    log.debug("_echo_lights: %d ok=%s", len(result.lights), result.ok)
    if result.message:
        typer.echo(result.message)
    if not result.ok:
        return
    if any(light.remembered for light in result.lights):
        typer.echo("  (found at the last search -- `trcc system rgb --scan` "
                   "to check again)")
    elif not result.scanned:
        typer.echo("  (not searched yet -- `trcc system rgb --scan`)")
    for light in result.lights:
        effect = ""
        if isinstance(light.effect, LinkedEffect):
            effect = (f"  effect: {light.effect.metric}-linked "
                      f"({light.effect.source}) (last set by TRCC)")
        elif light.effect is not None:
            effect = f"  effect: {light.effect.effect.value} (last set by TRCC)"
        typer.echo(f"  {light.kind.value:<7} {light.ref:<24} {light.name} "
                   f"({light.led_count} LEDs){effect}")
    if result.openrgb_error:
        typer.echo(f"  OpenRGB: {result.openrgb_error}")
    typer.echo(f"  RAM access: {result.ram_access.value}")


@COMMANDS.command("rgb")
def rgb(
    scan: bool = typer.Option(
        False, "--scan", help="Look for the lights first: RGB memory on the "
        "SMBus, and OpenRGB's devices."),
    host: str = typer.Option(
        "", "--host", help="With --scan: look for OpenRGB here, and save it "
                           "(default: the saved one, 127.0.0.1 at first)."),
    port: int = typer.Option(
        0, "--port", help="With --scan: OpenRGB's port (default: the saved "
                          "one, 6742 at first)."),
) -> None:
    """The RGB lights TRCC can drive or hand colours to."""
    log.info("cli system rgb: scan=%s host=%r port=%s", scan, host, port)
    if (host or port) and not scan:
        raise typer.BadParameter("--host and --port go with --scan")
    result = get_app().dispatch(ScanRgbLights(host=host, port=port) if scan
                                else RgbLights())
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)


def _parse_rgb(text: str) -> tuple[int, int, int]:
    """``255,0,0`` -> (255, 0, 0)."""
    log.debug("_parse_rgb: %r", text)
    parts = text.split(",")
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        raise typer.BadParameter(f"a colour is R,G,B -- got {text!r}")
    r, g, b = (int(p) for p in parts)
    return r, g, b


@COMMANDS.command("ram-effect")
def ram_effect(
    effect: RamEffect = typer.Argument(..., help="The effect to save on the RAM."),
    stick: list[str] | None = typer.Option(
        None, "--stick", help="A stick by ref (see `trcc system rgb`); "
        "repeat for more.  Default: every stick."),
    speed: EffectSpeed = typer.Option(EffectSpeed.MEDIUM, "--speed"),
    direction: EffectDirection | None = typer.Option(None, "--direction"),
    color: list[str] | None = typer.Option(
        None, "--color", help="R,G,B; repeat for a second colour."),
    random_colors: bool = typer.Option(
        False, "--random", help="Let the stick pick the colours."),
    brightness: int = typer.Option(255, "--brightness", min=0, max=255),
) -> None:
    """Save one of the memory's own effects on the sticks.

    The stick keeps it after TRCC closes and through a reboot.  If the RAM
    was following a device, following turns off first.
    """
    log.info("cli system ram-effect: %s sticks=%s", effect.value, stick)
    result = get_app().dispatch(SetRamEffect(
        effect=effect, refs=tuple(stick or ()), speed=speed,
        direction=direction,
        colors=tuple(_parse_rgb(c) for c in color or ()),
        random_colors=random_colors, brightness=brightness))
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)


@COMMANDS.command("aura-effect")
def aura_effect(
    effect: RamEffect = typer.Argument(..., help="The effect to run."),
    light: list[str] | None = typer.Option(
        None, "--light", help="A motherboard light by ref -- aura/argb1, "
        "aura/onboard (see `trcc system rgb`); repeat for more.  Default: "
        "every one."),
    speed: EffectSpeed = typer.Option(EffectSpeed.MEDIUM, "--speed"),
    direction: EffectDirection | None = typer.Option(None, "--direction"),
    color: list[str] | None = typer.Option(
        None, "--color", help="R,G,B; repeat for a second colour."),
    random_colors: bool = typer.Option(
        False, "--random", help="Let the effect pick the colours."),
    brightness: int = typer.Option(255, "--brightness", min=0, max=255),
) -> None:
    """Run a built-in effect on the motherboard's lights -- an ARGB header's
    fan or strip, or the board's own LEDs.

    TRCC draws it while TRCC runs; nothing is saved on the board, which
    shows its own effect before TRCC starts.  A header needs its LED count
    first (`trcc system aura-leds`).
    """
    log.info("cli system aura-effect: %s lights=%s", effect.value, light)
    result = get_app().dispatch(SetAuraEffect(
        effect=effect, refs=tuple(light or ()), speed=speed,
        direction=direction,
        colors=tuple(_parse_rgb(c) for c in color or ()),
        random_colors=random_colors, brightness=brightness))
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)


@COMMANDS.command("linked-color")
def linked_color(
    metric: str = typer.Argument(..., help="temp or load."),
    light: list[str] = typer.Option(
        ..., "--light", help="A light by ref -- RAM sticks (i2c-3/0x19) or "
        "motherboard lights (aura/argb1), not both (see `trcc system rgb`); "
        "repeat for more."),
    sensor: str = typer.Option("cpu", "--sensor", help="cpu or gpu."),
) -> None:
    """Colour RAM sticks or the motherboard's lights by the CPU's or GPU's
    temperature or load -- the LED cooler's Temperature-linked and
    Load-linked modes.

    Green when fine, then yellow, orange and red as it gets busy or hot; one
    colour per light, written only when the reading changes it.  Live while
    TRCC runs: a stick keeps its last colour when TRCC stops.
    """
    log.info("cli system linked-color: %s %s lights=%s", metric, sensor, light)
    if metric not in ("temp", "load"):
        raise typer.BadParameter(f"temp or load -- got {metric!r}")
    if sensor not in ("cpu", "gpu"):
        raise typer.BadParameter(f"cpu or gpu -- got {sensor!r}")
    result = get_app().dispatch(SetLinkedColor(
        metric="temp" if metric == "temp" else "load", refs=tuple(light),
        source="cpu" if sensor == "cpu" else "gpu"))
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)


@COMMANDS.command("aura-leds")
def aura_leds(
    header: int = typer.Argument(..., min=1, help="The ARGB header, from 1 "
                                 "(see `trcc system rgb`)."),
    leds: int = typer.Argument(..., min=0, help="How many LEDs it drives."),
) -> None:
    """Say how many LEDs an ASUS Aura ARGB header drives.

    The board cannot tell: the header's data line runs one way.  The board
    is read again after, so the new count is listed.
    """
    log.info("cli system aura-leds: header %d -> %d", header, leds)
    result = get_app().dispatch(SetAuraLeds(header=header, leds=leds))
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)


@COMMANDS.command("aura-blink")
def aura_blink(
    header: int = typer.Argument(..., min=1, help="The ARGB header, from 1 "
                                 "(see `trcc system rgb`)."),
) -> None:
    """Blink an ASUS Aura ARGB header for two seconds, to see which it is.

    Nothing is saved: the header goes back to its own effect after.
    """
    log.info("cli system aura-blink: header %d", header)
    result = get_app().dispatch(BlinkAuraHeader(header=header))
    _echo_lights(result)
    if not result.ok:
        raise typer.Exit(code=1)
