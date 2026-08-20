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
from trcc.core.protocol import FBL_PROFILES
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
    profile = FBL_PROFILES[fbl]
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
