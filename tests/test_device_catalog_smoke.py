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

from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Wire
from trcc.core.registry import ALL_DEVICES
from trcc.core.variants import _VARIANT_REGISTRY


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
    canvas.  Exact dimensions are the device's own resolution logic (verified at
    runtime by ``mock_gui --all``); re-deriving them here would just re-implement
    that logic in the oracle.
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
    finally:
        app.close()


def test_catalog_is_non_trivial() -> None:
    """Guard: the catalog actually enumerated the fleet (not silently empty)."""
    assert len(_CATALOG) >= 100, f"only {len(_CATALOG)} variants enumerated"
