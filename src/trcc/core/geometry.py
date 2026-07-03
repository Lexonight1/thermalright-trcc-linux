"""Orientation geometry — the single pure decision for compose canvas + rotation.

Restores the legacy ``DisplayService.set_rotation`` / ``has_portrait_themes``
model the cutover fragmented (#136): a display angle is two orthogonal bits —
**which orientation folder** the content came from (landscape ``theme{w}{h}`` vs
portrait ``theme{h}{w}``) and **whether it needs a 180° flip**.  Because a 180°
flip is dimension-preserving, portrait content never has to be pixel-rotated into
a landscape buffer (which is what clips); the 90° turn is a *folder switch*, not a
spin.  The only 90/270 pixel-spin left is the fallback for a rotate panel whose
portrait variant is absent on disk (a local theme saved landscape-only).

This module owns ONLY the decision — pure, Qt-free, adapter-free, so every UI
(GUI preview bezel, CLI, API) and the wire path key on the same answer.  Applying
the rotation and encoding stays in ``services/display.py``; the device-mount
encode rotation (``DeviceProfile.encode_baseline``) is a *separate* concern
applied only at wire encode, never here.

Phase A: additive + behavior-neutral.  This is a faithful extraction of
``DisplayService._compose_geometry``; nothing calls it yet.  ``tests/
test_geometry.py`` pins the truth table.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import oriented_resolution
from .protocol import DeviceProfile


@dataclass(frozen=True, slots=True)
class OrientationPlan:
    """How to compose + rotate one frame for a given device angle.

    * ``canvas`` — the compose canvas size (portrait content transposes to
      ``(h, w)``; a landscape-only fallback stays ``(w, h)`` and rotates whole).
    * ``is_portrait_content`` — the loaded artwork is authored/loaded portrait
      (from a portrait catalog), so the orientation is already baked into the
      pixels and must NOT be re-rotated.
    * ``post_rotate`` — degrees to rotate the FINISHED composite as one unit.
      Non-zero only for the fallback: a non-widescreen rotate panel at 90/270
      whose portrait variant is absent, so a landscape composite is spun into
      the portrait buffer (legacy ``has_portrait_themes=False``).  0 for every
      other case, so all non-fallback panels stay byte-identical.
    """
    canvas: tuple[int, int]
    is_portrait_content: bool
    post_rotate: int


def plan_orientation(
    profile: DeviceProfile, orientation: int, content_is_portrait: bool,
) -> OrientationPlan:
    """Compose canvas + portrait flag + whole-composite rotation for an angle.

    Faithful port of ``DisplayService._compose_geometry``.  Three cases of the
    C# oriented-output model (``SetMyUCScreenImage``):

    * **Portrait content @ 90/270** (or any widescreen rotate panel) — compose on
      the transposed portrait canvas; no further rotation ("orientation is in the
      masks").
    * **Landscape-only content @ 90/270** on a non-widescreen rotate panel — the
      portrait variant is absent on disk, so compose on the native LANDSCAPE
      canvas (bg + mask + text aligned, nothing clipped) and rotate the WHOLE
      composite by ``orientation`` into the portrait buffer.  Legacy's
      ``has_portrait_themes=False`` branch.
    * **Everything else** — 0/180, squares, non-rotate, JPEG widescreen — the
      user-orientation dimension swap, no whole-composite rotation.
    """
    w, h = profile.resolution
    rotate_panel = profile.rotate and w != h and orientation in (90, 270)
    if rotate_panel and not content_is_portrait and not profile.widescreen:
        return OrientationPlan((w, h), False, orientation)
    if rotate_panel:
        return OrientationPlan((h, w), True, 0)
    return OrientationPlan(oriented_resolution((w, h), orientation), False, 0)
