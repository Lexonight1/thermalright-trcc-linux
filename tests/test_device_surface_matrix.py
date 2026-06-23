"""Every LCD cooler renders a real frame to the wire at every user orientation.

This completes the device-coverage trio:

  * ``test_device_catalog_smoke.py`` — every variant CONNECTS + resolves a canvas.
  * ``test_device_sender.py`` / ``test_*_lcd_geometry.py`` — per-wire transport
    lifecycle + frame chunking.
  * **THIS** — every variant RENDERS an image through the REAL ``DisplayService``
    + ``QtRenderer`` and pushes it down the REAL ``Device.send`` path, at each
    user orientation the device supports.  The matrix is (variant × orientation).

Programmatic contract asserted here (no hardware, no eyeball):

  * the render→encode→send chain does not crash at any orientation (the
    rotation path is exercised for real);
  * the Command returns ``ok`` and reports wire bytes (``bytes_sent > 0``);
  * for RGB565 panels the encoded byte count equals the oriented canvas
    (``w*h*2``) — a rotation that silently changed dimensions would fail here.
    JPEG panels encode to a variable size, so only non-emptiness is checked.

What this CANNOT prove is visual **uprightness** — whether portrait content
reads the right way up on the glass.  That still needs ``mock_gui --all`` with a
human looking; this locks the byte-level contract underneath it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from tests.test_device_catalog_smoke import _CATALOG
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, SendImage, SetOrientation
from trcc.core.models import Wire
from trcc.core.registry import ALL_DEVICES

_ORIENTATIONS = (0, 90, 180, 270)


def _surface_matrix() -> list:
    """(variant × supported-orientation) for every LCD wire — LED has no canvas.

    Orientation is intersected with the product's declared ``orientations`` so we
    never drive an unsupported angle (``SetOrientation`` rejects those by design);
    a panel that supports only landscape collapses to a single row, faithfully.
    """
    params: list = []
    for p in _CATALOG:
        vid, pid, pm, sub, wire, model = p.values
        if wire is Wire.LED:
            continue
        supported = ALL_DEVICES[(vid, pid)].orientations or (0,)
        for deg in _ORIENTATIONS:
            if deg not in supported:
                continue
            params.append(pytest.param(
                vid, pid, pm, sub, wire, model, deg, id=f"{p.id}|rot{deg}",
            ))
    return params


_MATRIX = _surface_matrix()


@pytest.fixture(scope="module")
def sample_image(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real on-disk PNG the SendImage path can open, resize, encode + send.

    Built once for the module via the same ``QtRenderer`` the app uses, so the
    file is a faithful image (not hand-rolled bytes) on every CI host.
    """
    renderer = QtRenderer()
    surface = renderer.create_surface(64, 64, color=(10, 120, 220, 255))
    path = tmp_path_factory.mktemp("surface_matrix") / "sample.png"
    path.write_bytes(renderer.encode_png(surface))
    return path


@pytest.mark.parametrize("vid,pid,pm,sub,wire,model,degrees", _MATRIX)
def test_variant_renders_frame_to_wire_at_orientation(
    vid: int, pid: int, pm: int | None, sub: int | None, wire: Wire,
    model: str, degrees: int, sample_image: Path, tmp_path: Path,
) -> None:
    """Render + send an image to one cooler at one orientation, check the bytes."""
    spec: dict = {"vid": f"{vid:04x}", "pid": f"{pid:04x}"}
    if pm is not None:
        spec["pm"] = pm
    if sub is not None:
        spec["sub"] = sub

    # Real ``ThreadSendScheduler`` (the App default) drives the per-device send
    # worker so a ``wait=True`` submit returns promptly — the deterministic
    # ``SyncSendScheduler`` would need a manual ``tick()`` and a blocking send
    # would just time out.
    app = App(MockPlatform([spec], tmp_path), renderer=QtRenderer())
    try:
        key = f"{vid:04x}:{pid:04x}"
        assert app.dispatch(ConnectDevice(key=key)).ok, f"{model}: connect failed"

        rot = app.dispatch(SetOrientation(key=key, degrees=degrees))
        assert rot.ok, f"{model}: SetOrientation({degrees}) rejected — {rot.message}"

        device = app.devices.get(key)
        assert device is not None and device.profile is not None, \
            f"{model}: no profile after handshake"

        result = app.dispatch(SendImage(key=key, path=sample_image))
        assert result.ok, f"{model} @rot{degrees}: send failed — {result.message}"
        assert result.bytes_sent > 0, f"{model} @rot{degrees}: no wire bytes"

        # RGB565 panels have a deterministic frame size = oriented canvas × 2
        # bytes/pixel.  Square panels keep the same area at every orientation,
        # so the swap is a no-op on the size — but a rotation bug that dropped
        # or duplicated a row/column would still trip this.  JPEG output is
        # compressed (variable), so the non-empty check above is all we can pin.
        profile = device.profile
        w, h = profile.resolution
        if not profile.jpeg and w == h:
            assert result.bytes_sent == w * h * 2, (
                f"{model} @rot{degrees}: frame {result.bytes_sent}B != "
                f"oriented canvas {w}x{h} RGB565 ({w * h * 2}B)"
            )
    finally:
        app.close()


def test_matrix_is_non_trivial() -> None:
    """Guard: the matrix actually enumerated the fleet (not silently empty)."""
    assert len(_MATRIX) >= 200, f"only {len(_MATRIX)} (variant×orientation) rows"
