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
