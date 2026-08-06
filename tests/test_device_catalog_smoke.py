"""Every cooler in the catalog handshakes + resolves geometry — one test each.

The registry's ``VariantOverride`` table is the real device catalog: one USB
vid:pid fronts many coolers told apart by the handshake PM/SUB return bytes.
This parametrizes over EVERY distinct cooler variant (plus the registry devices
that carry no variant table) and drives the REAL ``ConnectDevice`` path with a
faithfully-scripted handshake (``MockPlatform``), so every device the app
claims to support is proven on every CI run — no guessing whether a reporter's
panel works.

Wire transport lifecycle (send/close/resume) is covered per vid:pid by
``dev/smoke_device_matrix.py``; this is the per-variant GEOMETRY layer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Wire
from trcc.core.registry import ALL_DEVICES
from trcc.core.variants import _VARIANT_REGISTRY

# The dev mock fleet sizes its fake panels without USB; this file owns the
# real connect path, so it is where the two are held to the same answer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
from _mock_bootstrap import NO_PANEL, variant_resolution


def _catalog() -> list:
    """Every distinct cooler: variant (vid,pid,pm,sub) + non-variant devices.

    Shared variant tables (the bulk table aliased across 4 vid:pids) are emitted
    once, under their first vid:pid; registry devices with no variant table
    (HID Type 3, LY) are tested once at their default handshake.
    """
    params: list = []
    seen_tables: set[int] = set()
    covered: set[tuple[int, int]] = set()

    for (vid, pid), table in _VARIANT_REGISTRY.items():
        if id(table) in seen_tables:
            covered.add((vid, pid))
            continue
        seen_tables.add(id(table))
        covered.add((vid, pid))
        product = ALL_DEVICES[(vid, pid)]
        for pm in sorted(table):
            for sub, override in table[pm].items():
                params.append(pytest.param(
                    vid, pid, pm, sub, product.wire, override.button_image,
                    id=f"{override.button_image}|{vid:04x}:{pid:04x}|"
                       f"pm{pm}sub{sub if sub is not None else '-'}",
                ))

    for (vid, pid), product in ALL_DEVICES.items():
        if (vid, pid) in covered:
            continue
        params.append(pytest.param(
            vid, pid, None, None, product.wire, product.product,
            id=f"{product.product}|{vid:04x}:{pid:04x}|default",
        ))
    return params


_CATALOG = _catalog()


@pytest.mark.parametrize("vid,pid,pm,sub,wire,model", _CATALOG)
def test_device_variant_handshakes_and_resolves_geometry(
    vid: int, pid: int, pm: int | None, sub: int | None, wire: Wire,
    model: str, tmp_path: Path,
) -> None:
    """Every cooler handshakes via the real ConnectDevice path + yields a canvas.

    Asserts the device CONNECTS (the wire parses the scripted return bytes for
    this PM/SUB without failing) and — for LCD wires — resolves a valid, non-zero
    canvas.  Exact dimensions are still not hardcoded here: the device's own
    resolution logic owns them, and restating it in the oracle would prove
    nothing.

    What IS asserted is that the dev mock fleet agrees with the answer connect()
    just produced.  ``dev/_mock_bootstrap`` has to size panels with no USB, so it
    resolves geometry a second way — and it drifted: it asked
    ``get_profile(pm_to_fbl(pm, sub), pm)`` for every wire, while bulk actually
    ships ``bulk_profile``, which diverts an unknown PM to the 480x480 base
    rather than echoing it into ``get_profile`` as a bogus FBL.  So GRAND VISION,
    CORE VISION, HYPER VISION and PA120 mocked at 320x320 while the app drove
    them at 480x480 — silently, in the harness we lean on to verify geometry.
    Comparing two implementations is not re-deriving one.
    """
    spec: dict = {"vid": f"{vid:04x}", "pid": f"{pid:04x}"}
    if pm is not None:
        spec["pm"] = pm
    if sub is not None:
        spec["sub"] = sub

    app = App(MockPlatform([spec], tmp_path), send_scheduler=SyncSendScheduler())
    try:
        key = f"{vid:04x}:{pid:04x}"
        result = app.dispatch(ConnectDevice(key=key))
        assert result.ok, f"{model}: handshake failed — {result.message}"

        device = app.devices.get(key)
        assert device is not None and device.is_connected, f"{model}: not connected"

        # LED has no canvas (segment display); LCD wires must resolve geometry.
        if wire is not Wire.LED:
            assert device.profile is not None, f"{model}: no profile after handshake"
            w, h = device.profile.resolution
            assert w > 0 and h > 0, f"{model}: invalid canvas {(w, h)}"

        # Registry devices carrying no variant table are tested at their default
        # handshake and have no catalog row to compare against.
        if pm is None:
            return
        mocked = variant_resolution(wire, pm, sub if sub is not None else 0)
        real = NO_PANEL if wire is Wire.LED else device.profile.resolution
        assert mocked == real, (
            f"{model}: dev mock fleet sizes this cooler {mocked} but connect() "
            f"resolved {real} — dev/_mock_bootstrap.variant_resolution has "
            f"drifted from the {wire.name} adapter"
        )
    finally:
        app.close()


def test_the_mock_fleet_gate_has_teeth() -> None:
    """The wire dispatch above must be load-bearing, not decoration.

    If ``variant_resolution`` ever collapses back to one ``get_profile`` call
    for every wire, the assertion in the connect test must fail rather than
    keep passing.  Bulk PM 1 is the witness: the shipping ``bulk_profile``
    answers 480x480, the naive lookup answers 320x320.
    """
    from trcc.core.protocol import get_profile, pm_to_fbl

    naive = get_profile(pm_to_fbl(1, 0), 1).resolution
    assert variant_resolution(Wire.BULK, 1, 0) != naive, (
        "variant_resolution now agrees with the naive per-wire-agnostic lookup "
        "— the bulk divergence it exists to carry has been lost"
    )
    assert variant_resolution(Wire.LED, 1, 0) == NO_PANEL, (
        "LED coolers drive a segment display and must report NO_PANEL, not a "
        "resolution invented by the FBL tables"
    )


def test_catalog_is_non_trivial() -> None:
    """Guard: the catalog actually enumerated the fleet (not silently empty)."""
    assert len(_CATALOG) >= 100, f"only {len(_CATALOG)} variants enumerated"


def test_every_variant_button_image_has_an_asset() -> None:
    """Every ``button_image`` in the registry must resolve to a bundled .png.

    Without this, a C# sync that adds a device (e.g. 2.1.6's LC10/LC13/LC15/
    LF014/LD11/RX1) silently ships a model whose sidebar button falls back to
    the generic image — invisible until a reporter notices.  Assets carry both
    a spaced and an underscored name historically, so accept either form.
    """
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR

    names = {
        ov.button_image
        for table in _VARIANT_REGISTRY.values()
        for sub_map in table.values()
        for ov in sub_map.values()
        if ov.button_image
    }
    missing = sorted(
        n for n in names
        if not (_PKG_ASSETS_DIR / f"{n}.png").exists()
        and not (_PKG_ASSETS_DIR / f"{n.replace(' ', '_')}.png").exists()
    )
    assert not missing, f"button_image(s) with no bundled asset .png: {missing}"


# ── orientation availability ──────────────────────────────────────────

def test_every_lcd_panel_offers_all_four_orientations() -> None:
    """A panel the user cannot rotate is a panel with a registry typo.

    `SetOrientation` gates on `ProductInfo.orientations`, so a missing angle is
    rejected before the render is ever reached — the user sees "vertical does
    not work" on a build whose rotation math is correct and C#-verified.

    That is exactly what happened to the Trofeo Vision 9.16 (0416:5408/5409,
    1920x462): the cutover declared `(0, 180)`, while the C# has an explicit
    `is1920x462` directionB switch covering 0/90/180/270 (FormCZTV.cs:2690) and
    our own encode table already matched it at every angle. One wrong tuple, and
    the reporter was stuck in landscape (#207).

    Every LCD in the catalog rotates in the official app. If a genuinely
    fixed-orientation panel ever ships, add it here WITH the C# evidence rather
    than quietly narrowing the tuple.
    """
    from trcc.core.models import Kind
    from trcc.core.registry import ALL_DEVICES

    offenders = {
        f"{vid:04x}:{pid:04x} ({p.product})": p.orientations
        for (vid, pid), p in ALL_DEVICES.items()
        if p.kind is Kind.LCD and set(p.orientations) != {0, 90, 180, 270}
    }
    assert not offenders, (
        "LCD panels that cannot be rotated by the user — SetOrientation will "
        f"reject the missing angles before rendering: {offenders}"
    )
