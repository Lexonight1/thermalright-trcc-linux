"""A cooler introduces itself by its own name, not its chipset's (#272).

``ProductInfo.product`` is one string per (VID, PID), but a single USB id
covers dozens of coolers — so every ``87ad:70db`` panel called itself
"GrandVision 360 AIO" whatever it actually was.  The handshake already
resolves the right variant per (PM, SUB); it just never carried the name.
"""
from __future__ import annotations

from trcc.core.variants import get_variant_override


def test_a_confirmed_variant_carries_its_marketed_name() -> None:
    """PM=4/SUB=5 is a Peerless Vision 360 — Ziusz confirmed it on hardware.

    MUTATION CHECK: swap ``_named`` back for ``_v`` on that row and this
    fails with '' — the cooler goes back to inheriting the registry's name.
    """
    override = get_variant_override(0x87AD, 0x70DB, 4, 5)

    assert override is not None
    assert override.display_name == "Peerless Vision 360"
    assert override.button_image == "A1LM19SE"   # asset id unchanged


def test_the_other_confirmed_variant_too() -> None:
    """PM=4/SUB=1 is a Hyper Vision 360 — Seryogaberkut, #274."""
    override = get_variant_override(0x87AD, 0x70DB, 4, 1)

    assert override is not None
    assert override.display_name == "Hyper Vision 360"


def test_an_unconfirmed_variant_claims_no_name() -> None:
    """We only assert a name a reporter has read off the cooler in their hand.

    Deriving one from ``button_image`` would trade a wrong name for an opaque
    one: some of those ids read as products (``A1GRAND VISION``), but others
    are internal codes (``A1LM16SE``) no owner would recognise.
    """
    override = get_variant_override(0x87AD, 0x70DB, 4, 3)

    assert override is not None
    assert override.button_image == "A1LM16SE"
    assert override.display_name == ""


def test_connect_renames_the_product_to_the_confirmed_variant(tmp_path) -> None:
    """End to end: the name the user sees comes from the handshake.

    MUTATION CHECK: drop the ``display_name`` arm from ConnectDevice's patch
    and this fails — the device reports "GrandVision 360 AIO".
    """
    from pathlib import Path

    from trcc.app import App
    from trcc.core.commands import ConnectDevice

    from .mock_platform import MockPlatform

    app = App(platform=MockPlatform(
        [{"type": "lcd", "name": "Peerless Vision 360", "vid": "87ad",
          "pid": "70db", "pm": 4, "sub": 5, "resolution": "480x480"}],
        Path(tmp_path),
    ))

    result = app.dispatch(ConnectDevice(key="87ad:70db"))

    assert result.ok, result.message
    assert app.devices["87ad:70db"].info.product == "Peerless Vision 360"


# ── #289 / #176 / #272: the name reaches every place a user reads it ────────


def _app_with(tmp_path, **spec):
    from pathlib import Path

    from trcc.app import App

    from .mock_platform import MockPlatform

    return App(platform=MockPlatform([{"type": "lcd", **spec}], Path(tmp_path)))


def test_an_ly_panel_is_looked_up_in_the_csharp_257_table() -> None:
    """LY panels are device mode 2 in the C#: RGB_ADD_Device(pm, sub) ->
    ADDUserButton(257, ...), the same PM table as bulk.  They were left out,
    so no LY panel had a per-model button or name (#289)."""
    override = get_variant_override(0x0416, 0x5408, 69, 2)
    assert override is not None
    assert (override.button_image, override.display_name) == (
        "A1LD9", "Trofeo Vision 11.3 LCD")


def test_connecting_the_113_inch_panel_names_it(tmp_path) -> None:
    from trcc.core.commands import ConnectDevice

    app = _app_with(tmp_path, vid="0416", pid="5408", pm=69, sub=2)
    assert app.dispatch(ConnectDevice(key="0416:5408")).ok
    assert app.devices["0416:5408"].info.product == "Trofeo Vision 11.3 LCD"


def test_connecting_a_mjolnir_vision_names_it(tmp_path) -> None:
    from trcc.core.commands import ConnectDevice

    app = _app_with(tmp_path, vid="87ad", pid="70db", pm=5, sub=1)
    assert app.dispatch(ConnectDevice(key="87ad:70db")).ok
    assert app.devices["87ad:70db"].info.product == "Mjolnir Vision"


def test_a_frozen_warframe_se_says_so(tmp_path) -> None:
    """PM=58/SUB=0 called itself "USBDISPLAY" -- the registry's name for
    0416:5302.  Three owners read it off the cooler: #295 (a Frozen Warframe
    SE 360 ARGB), #228 and #150.  SUB=1 (A1LM26) has no owner yet."""
    from trcc.core.commands import ConnectDevice

    se = get_variant_override(0x0416, 0x5302, 58, 0)
    other = get_variant_override(0x0416, 0x5302, 58, 1)
    assert se is not None and other is not None
    assert (se.button_image, se.display_name) == (
        "A1FROZEN WARFRAME SE", "Frozen Warframe SE")
    assert (other.button_image, other.display_name) == ("A1LM26", "")

    app = _app_with(tmp_path, vid="0416", pid="5302", pm=58, sub=0)
    assert app.dispatch(ConnectDevice(key="0416:5302")).ok
    assert app.devices["0416:5302"].info.product == "Frozen Warframe SE"


def test_connect_says_which_cooler_it_found(tmp_path, cli_runner) -> None:
    """`device list` promises that connect "names yours"; connect printed only
    a resolution tuple, and the Result carried no name for any UI (#272)."""
    from trcc.core.commands import ConnectDevice
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    app = _app_with(tmp_path, vid="87ad", pid="70db", pm=5, sub=1)
    result = app.dispatch(ConnectDevice(key="87ad:70db"))
    assert (result.product, result.catalog_product) == (
        "Mjolnir Vision", "GrandVision 360 AIO")
    assert result.handshake is not None
    w, h = result.handshake.resolution
    assert result.message == f"Connected: Mjolnir Vision {w}x{h}"

    _ctx.set_platform(MockPlatform(
        [{"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 5, "sub": 1}],
        tmp_path / "cli"))
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    try:
        out = cli_runner.invoke(cli, ["device", "connect", "87ad:70db"]).output
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None
    assert "  device:     Mjolnir Vision  (catalog: GrandVision 360 AIO)" in out, out


def test_the_report_names_the_cooler_its_own_probe_identified(tmp_path) -> None:
    """It printed "GrandVision 360 AIO" directly above ``PM=4 SUB=5`` (#272)."""
    from trcc.adapters.diagnostics.debug_report import _collect_devices

    app = _app_with(tmp_path, vid="87ad", pid="70db", pm=4, sub=5)
    rows, _ = _collect_devices(app.platform)
    assert rows[0]["product"] == "Peerless Vision 360 (catalog: GrandVision 360 AIO)"


def test_device_list_says_which_cooler_it_cannot_know(tmp_path, cli_runner) -> None:
    """No handshake, so no name -- say so for a shared USB id (#176)."""
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    _ctx.set_platform(MockPlatform([
        {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 5, "sub": 1},
        {"type": "lcd", "vid": "0416", "pid": "5406", "pm": 32},
    ], tmp_path))
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    try:
        out = cli_runner.invoke(cli, ["device", "list"]).output
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None
    assert "'trcc device connect 87ad:70db' names yours" in out, out
    assert "trcc device connect 0416:5406' names yours" not in out
