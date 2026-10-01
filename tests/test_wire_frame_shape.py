"""Every single-image wire frame must be the SHAPE its header declares.

``SendColor`` / ``SleepDevice`` / ``SendImage`` / the screencast loop each build
one surface and ship it to the panel.  The header they ship it under declares
the device's native resolution, and the firmware uses that to interpret the
buffer -- so a frame of a different shape is painted only where the two
overlap.  That is #262, and it is why the shutdown blank left part of the glass
still lit.

``build_solid_color_frame`` carried the pre-``wire_angle`` model that
``8cc1520e`` removed from the other two single-image producers::

    if resolved.rotate:
        surface = self._r.rotate(surface, 90)

a blanket 90 that transposes the buffer on all 10 ``rotate=True`` profiles.  It
was left in place deliberately -- ``_apply_post_processing`` said orientation on
a uniform fill "is a no-op and the extra rotate calls would burn cycles".  True
of the COLOUR, false of the DIMENSIONS, and dimensions are what the header
declares.

THE INVARIANT, and why it is expressed this way: the surface handed to the
encoder by a single-image producer must equal the one handed to it by
``build_frame`` for the same panel at the same angle.  ``build_frame`` is the
path measured correct on all 10 panels at all 4 angles (``8cc1520e``: "before
8 mismatches, after ALL MATCH"), so asserting agreement with it makes this gate
transitively oracle-anchored -- instead of restating per-family header shapes
here, where they would rot the moment the table moved.

WHY THE WHOLE FAMILY, not just the solid-colour path.  The three producers are
one family by construction: each resolves a canvas from ``plan_orientation``,
produces a surface, and hands it to the SAME tail --
``_apply_post_processing`` -> ``_orient_for_wire`` -> ``_encode_for_wire``.  The
``_orient_for_wire`` correction that fixes the solid path fixes all three, so
gating one of three would leave two members of the family free to regress.  And
the family is DERIVED, not listed: ``test_the_family_is_exactly_the_tail_callers``
reads the callers of ``_orient_for_wire`` out of the source, so a fourth
producer joining the family fails this file until it is covered here.  A
hand-written list of three method names would have been one more restatement of
a rule the code already knows.

It is asserted at the SURFACE, before encode, on purpose.  6 of the 10
``rotate=True`` profiles are RGB565, and 480x854 and 854x480 are the same
BYTE COUNT -- a gate reading the encoded payload would pass while guarding
nothing on more than half the panels it claims to cover.

MUTATION CHECK -- restore the blanket rotate in
``DisplayService.build_solid_color_frame``, i.e. compose on the NATIVE
resolution and replace the tail's orientation step with::

    if resolved.rotate:
        surface = self._r.rotate(surface, 90)

MEASURED 2026-08-19, re-run before landing: **28 failures** on the
``build_solid_color_frame`` parameters.  At **0 and 180**, every one of the 10
``rotate=True`` profiles -- 50, 51, 52, 53, 58, 64, 114, 128, 192, 224.  At
**90 and 270**, the 4 widescreen JPEG panels -- 114, 128, 192, 224.

The first measurement, 2026-08-17, was **20** -- 0 and 180 only -- on the
reasoning that at 90/270 a transposed canvas and a blanket 90 from the native
one land on the same shape, so no shape test could separate them.  That held
while wire rotation was TWO models.  It stopped holding when ``ENCODE_ROTATIONS``
became the single authority, because the table's angle for a widescreen panel at
90/270 is no longer a blanket 90.  The gate got STRONGER: 8 panel-angle pairs it
could not previously see.  The 6 non-widescreen profiles still pass at 90/270,
for the original reason.

``test_the_family_is_exactly_the_tail_callers`` fires on that mutation too --
29 failures in total -- because the mutated method stops calling the tail and so
leaves the family.  That is the completeness gate working, not a second defect.

If nothing fails, this file is guarding nothing.

MIND THE ANCHOR.  The tail is SHARED, so a search-and-replace across those three
lines mutates all three producers at once and you are measuring something other
than the mutation described above.  To reproduce the numbers, change
``build_solid_color_frame`` alone -- its ``create_surface`` call carries the
colour tuple and is unique to it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from trcc.core.models import RawFrame
from trcc.core.protocol import FBL_PROFILES, get_profile
from trcc.services.display import DisplayService

from .test_display_rotation import RecordingRenderer
from .test_preview_is_not_rotated import ANGLES, _display, _info, _theme

#: The one argument each producer takes beyond ``info`` / ``profile``.
#: Values are inert: the fake renderer's ``open_image`` ignores the path and
#: ``from_raw_rgb24`` reads only the frame's dimensions, so neither producer
#: needs a real file -- what is under test is the SHAPE the tail emits, and
#: every producer resizes its source onto the canvas before reaching it.
_SOURCES: dict[str, dict[str, Any]] = {
    "build_solid_color_frame": {"color": (0, 0, 0)},
    "build_image_frame": {"path": Path("ignored-by-the-fake-renderer.png")},
    "build_screencast_frame": {"frame": RawFrame(b"", 64, 64)},
}


def _tail_callers() -> set[str]:
    """Every ``DisplayService`` method that ends in the shared wire tail.

    Read out of the source rather than listed, so the family cannot grow a
    member in silence.  ``_orient_for_wire`` is the marker because it is the
    step that decides the emitted shape -- the one this file gates.
    """
    tree = ast.parse(Path(inspect.getfile(DisplayService)).read_text("utf-8"))
    service = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "DisplayService"
    )
    return {
        method.name
        for method in service.body
        if isinstance(method, ast.FunctionDef)
        for call in ast.walk(method)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_orient_for_wire"
    }


def _encoded_surface_size(renderer: RecordingRenderer) -> tuple[int, int]:
    """Size of the surface the wire encoder was actually handed."""
    for name, args in reversed(renderer.calls):
        if name in ("encode_rgb565", "encode_jpeg"):
            surface: Any = args[0]
            return (surface.w, surface.h)
    raise AssertionError(
        "no encode call recorded — the build never reached the wire encoder"
    )


def test_the_family_is_exactly_the_tail_callers() -> None:
    """The gate below must cover every producer that uses the shared tail.

    This is the part that survives a future change: add a fourth single-image
    producer and this fails, naming it, instead of the shape gate quietly
    covering three of four.
    """
    assert _tail_callers() == set(_SOURCES), (
        f"the single-image family is {sorted(_tail_callers())} but this file "
        f"gates {sorted(_SOURCES)} — a producer that calls _orient_for_wire is "
        "ungated, so it can emit a shape its header does not declare (#262)."
    )


@pytest.mark.parametrize("builder", sorted(_SOURCES))
@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("orientation", ANGLES)
def test_wire_frame_matches_the_rendered_frame_shape(
    builder: str, fbl: int, orientation: int, tmp_home: Path,
) -> None:
    # ``get_profile``, never the raw ``FBL_PROFILES`` row: the row carries no
    # encode base (0 on every one), and every device's profile comes through
    # ``get_profile``, which sets it -- 90 on the base-90 family.  Measured on
    # the raw rows, this gate passed while cast, image and solid colour sent
    # a TRANSPOSED buffer at 90/270 on FBL 50-53/58/60/64 on real devices:
    # 42 of 240 frames (2026-09-30).
    profile = get_profile(fbl)
    info = _info(fbl, profile.resolution)

    renderer = RecordingRenderer()
    display = _display(renderer, tmp_home)
    display._settings.set_orientation(info.key, orientation)

    renderer.calls.clear()
    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)
    rendered = _encoded_surface_size(renderer)

    renderer.calls.clear()
    getattr(display, builder)(info=info, profile=profile, **_SOURCES[builder])
    single = _encoded_surface_size(renderer)

    assert single == rendered, (
        f"{builder} fbl={fbl} {profile.resolution} rotate={profile.rotate} @ "
        f"{orientation}deg: the frame is {single[0]}x{single[1]} but a "
        f"rendered frame is {rendered[0]}x{rendered[1]}.  Both ship under the "
        f"same header, so the panel paints only the overlap (#262) — the "
        f"shutdown blank leaves part of the glass lit."
    )


# ── the screencast composites mask + metrics, and its geometry did NOT move ──
#
# Reported 2026-09-14: "it does capture the screen but it blinks the metrics
# mask".  Two defects compounded.  ``build_screencast_frame`` skipped the
# theme/overlay pipeline entirely -- its own docstring said the layering was
# still to come -- so a screencast showed the bare desktop; and nothing gated
# ``_DeviceRenderObserver``, so every ``SensorsUpdated`` dispatched a full
# theme render to the same panel.  At ~7 fps capture (``SCREENCAST_TICK_S``)
# against a 2 s sensor tick the panel showed ~13 bare frames then one frame of
# mask+metrics, forever.
#
# The C# has one producer and composites every frame: ``CopyFromScreen`` writes
# the region into ``bitmapBGK`` (FormCZTV.cs:3550) -- the same slot a GIF
# (:3007) and the video player (:3057) write -- and ``GenerateImage`` draws
# background -> mask -> a ``DrawString`` per metric.

def _screencast_bytes(display, info, profile, theme, renderer):
    renderer.calls.clear()
    return display.build_screencast_frame(
        info=info, frame=RawFrame(b"", 64, 64), theme=theme, profile=profile,
    )


def _geometry_ops(renderer) -> list[tuple]:
    """The resize/rotate calls, by their NUMBERS only.

    The recorded args carry the surface object too, and that is a fresh
    instance per build -- comparing it compares identity, not geometry.
    """
    return [
        (name, *[a for a in args if isinstance(a, (int, float))])
        for name, args in renderer.calls
        if name in ("resize", "rotate")
    ]


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("angle", ANGLES)
def test_screencast_geometry_is_unchanged_by_the_overlay(
    fbl: int, angle: int, tmp_path: Path,
) -> None:
    """Adding the layers must not move the canvas or the rotation.

    The composite is inserted BEFORE ``_apply_post_processing`` /
    ``_orient_for_wire``, so the tail sees the same surface shape it always
    did.  Asserted over every panel the catalog can produce, at every angle,
    because the divergence this protects is panel-specific: the oracle
    composes screencasts on the ORIENTED canvas (``GIFSize`` transposes at
    90/270 for non-square panels) where we compose on the native one, and
    moving to that is a separate, coupled change -- it needs the region
    picker's aspect lock to transpose too, which it does not yet do.

    MUTATION CHECK: see the ordering test below -- a call-count comparison
    alone does NOT catch a mis-ordered composite, which is why that test
    exists separately.
    """
    profile = get_profile(fbl, 0)
    info = _info(fbl, profile.resolution)
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_path)
    display._settings.set_orientation(info.key, angle)

    _screencast_bytes(display, info, profile, None, renderer)
    without = _geometry_ops(renderer)

    _screencast_bytes(display, info, profile, _theme(), renderer)
    with_theme = _geometry_ops(renderer)

    assert without == with_theme, (
        f"fbl={fbl} {profile.resolution} @{angle}: compositing the mask and "
        f"metrics changed the geometry of the screencast frame"
    )


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
def test_screencast_composites_the_mask_and_the_metrics(
    fbl: int, tmp_path: Path,
) -> None:
    """The layers are actually drawn -- the defect was that they were not.

    Checks the COMPOSITE calls, not the bytes: a fake renderer's bytes are
    inert, but which layers reached ``composite`` is exactly what was missing.
    """
    profile = get_profile(fbl, 0)
    info = _info(fbl, profile.resolution)
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_path)

    _screencast_bytes(display, info, profile, None, renderer)
    bare = sum(1 for n, _ in renderer.calls if n == "composite")

    _screencast_bytes(display, info, profile, _theme(), renderer)
    composed = sum(1 for n, _ in renderer.calls if n == "composite")

    assert composed > bare, (
        f"fbl={fbl}: a screencast with an active theme composited no extra "
        f"layer -- the mask and metric elements are missing from the frame"
    )


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("angle", ANGLES)
def test_screencast_composites_before_it_rotates(
    fbl: int, angle: int, tmp_path: Path,
) -> None:
    """Mask and metrics go on BEFORE the wire rotation, never after.

    The layers are composed at the un-rotated canvas size, so compositing them
    after ``_orient_for_wire`` would paint an upright overlay onto a rotated
    background -- metrics lying sideways across the picture, at the wrong
    size on any panel whose rotation transposes the axes.

    It also matches the C#, where rotation happens ONLY in the encoders
    (``ImageTo565`` / ``ImageToJpg``) and ``GenerateImage`` composes upright.

    MUTATION CHECK: move the composite block below ``_orient_for_wire`` in
    ``build_screencast_frame`` and every rotating (fbl, angle) pair fails.
    A call-COUNT gate cannot see this -- compositing adds no resize or
    rotate -- so the order is asserted directly.
    """
    profile = get_profile(fbl, 0)
    info = _info(fbl, profile.resolution)
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_path)
    display._settings.set_orientation(info.key, angle)

    _screencast_bytes(display, info, profile, _theme(), renderer)
    names = [n for n, _ in renderer.calls]
    if "rotate" not in names:
        pytest.skip(f"fbl={fbl} @{angle} does not rotate — nothing to order")

    assert "composite" in names, "the overlay never reached the frame"
    assert names.index("composite") < names.index("rotate"), (
        f"fbl={fbl} {profile.resolution} @{angle}: the overlay is composited "
        f"AFTER the wire rotation, so it paints upright onto a rotated "
        f"background — call order was {names}"
    )
