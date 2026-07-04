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

import logging
from dataclasses import dataclass
from pathlib import Path

from .models import Theme, oriented_resolution
from .protocol import DeviceProfile

log = logging.getLogger(__name__)


def content_is_portrait(
    theme: Theme, profile: DeviceProfile,
    mask_path: str | None, mask_visible: bool,
) -> bool:
    """True when the ACTUALLY-LOADED content is portrait-oriented.

    A SUPERSET of three signals — True whenever ANY says portrait, so it never
    contradicts the old DC-only read (no regression) yet catches what that one
    missed:

    1. **Active mask under ``web/zt{h}{w}``** — an explicitly-applied portrait
       mask makes the frame portrait even over a landscape base theme (the bug
       where a portrait mask got SPUN instead of switched).
    2. **Theme loaded from ``theme{h}{w}``** — disk truth.  Shipped portrait
       folders ship the landscape DC with ``rotation=0``, so signal 3 alone
       reports landscape for a genuinely-portrait folder; the path doesn't lie.
    3. **Theme DC ``rotation`` ∈ {90,270}** — the legacy signal.  Real cloud
       portrait themes carry it; kept so nothing that worked before breaks.

    The single source of the portrait decision — shared by ``DisplayService``
    (render) and ``SaveTheme`` (which folder to persist into), so a saved theme
    always reloads into the orientation it was composed in.  Only meaningful for
    a non-square rotate panel; squares / non-rotate never compose portrait.
    """
    w, h = profile.resolution
    if not (profile.rotate and w != h):
        return False
    portrait_mask = f"zt{h}{w}"
    if mask_visible and mask_path and portrait_mask in Path(mask_path).parts:
        log.debug("content_is_portrait: active mask under %s → portrait",
                  portrait_mask)
        return True
    if f"theme{h}{w}" in theme.path.parts:
        log.debug("content_is_portrait: theme %s under portrait dir → portrait",
                  theme.name)
        return True
    if theme.config.get("rotation", 0) in (90, 270):
        log.debug("content_is_portrait: theme %s DC rotation portrait", theme.name)
        return True
    return False


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
        # Portrait content is composed on the transposed canvas with the
        # orientation baked into the pixels — but the asset is authored for 90.
        # 270 is 90 + a 180° flip, and 180° is dimension-preserving, so rotate
        # the WHOLE composite (bg + mask + text) as one unit and everything
        # stays true to the physical rotation instead of frozen at 90.
        # Widescreen JPEG panels keep post_rotate=0: their 90/270 send-unrotated
        # behaviour is hardware-verified (#169) and must not change.
        post_rotate = 180 if orientation == 270 and not profile.widescreen else 0
        return OrientationPlan((h, w), True, post_rotate)
    return OrientationPlan(oriented_resolution((w, h), orientation), False, 0)
