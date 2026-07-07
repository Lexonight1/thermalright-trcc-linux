"""C# encode-rotation oracle — a faithful, line-cited transcription of the
Windows app's wire-frame rotation, for auditing our port against.

Source: ``TRCC.decompiled.cs`` (the v2.1.x decompile).  The wire image is
rotated clockwise by ``(base - directionB) mod 360`` right before it is
encoded and sent; ``base`` is the panel's dir-0 mount offset.  Two switch
statements pick ``base``, keyed on resolution + encoder + a couple of device
special-cases — collapsed here into ONE honest table (the C# smears the same
decision across ~160 ``isFanZhuan`` / per-panel sites; this is the clean form).

* JPEG panels → ``ImageToJpg`` switch, FormCZTV.cs:65262-65330.
* RGB565 panels → ``ImageTo565`` switch, FormCZTV.cs:65578-65640.

``directionB = (combo_index - 1) * 90`` → the user orientation 0/90/180/270
(FormCZTV.cs:63884).  ``RotateImg`` uses GDI+ ``RotateTransform`` — positive =
clockwise (FormCZTV.cs:39943).  ``RotateImgHei`` / ``RotateImgBu`` are the same
geometric angle as ``RotateImg`` plus a 480×480-only edge cleanup, so they fold
to the same ``base`` here.

This module is PURE (no I/O, no framework) and intentionally lives in
``dev/decompiler`` — it is a reference oracle for the audit tool, never
shipped domain logic, so nothing in ``src/trcc`` imports it.
"""
from __future__ import annotations

# Resolutions the C# treats as square in each encoder (note: ImageToJpg's
# square test is 320²/480² only; ImageTo565 also counts 240²).
_JPG_SQUARES = frozenset({(320, 320), (480, 480)})
_565_SQUARES = frozenset({(240, 240), (320, 320), (480, 480)})
# Landscape widescreen panels whose C# base is 180 (they mount inverted).
_BASE_180 = frozenset({(1600, 720), (1920, 462)})
# Widescreen panels whose C# base is 0.
_BASE_0_WIDE = frozenset({(1280, 480), (800, 480), (854, 480), (960, 540),
                          (640, 480)})


def csharp_encode_base(resolution: tuple[int, int], *, jpeg: bool,
                       pm: int) -> int:
    """The C# dir-0 mount offset (``base``) for a panel + encoder + PM byte.

    Mirrors the branch ORDER of the C# switches exactly — square test first,
    then the PM special-cases, then per-resolution — because order decides
    ties (e.g. a 480² JPEG with PM=5 hits the square branch, not PM=5).
    """
    w, h = resolution
    if jpeg:
        # ── ImageToJpg (65262-65330) ──
        if resolution in _JPG_SQUARES:            # is320x320 || is480x480
            return 180 if pm == 6 else 0          # pm6 RotateImgHei @180 (65271)
        if pm == 5:                               # Mjolnir 320×240 out (65285)
            return 0
        if resolution in _BASE_180:               # 1600×720 / 1920×462 (65292/65306)
            return 180
        if resolution in _BASE_0_WIDE:            # 1280/800/854/960 + 640×480 (65299/65313)
            return 0
        return 90                                 # default 320×240 JPEG (65320)
    # ── ImageTo565 (65578-65640) ──
    if resolution in _565_SQUARES:                # is240/320/480 square (65592)
        return 0
    return 90                                     # default 320×240 RGB565 (65599)


def csharp_wire_rotation(resolution: tuple[int, int], *, jpeg: bool,
                         pm: int, orientation: int) -> int:
    """Clockwise degrees the C# rotates the composed frame before encode.

    ``(base - orientation) mod 360`` — the whole 0↔180 and 90↔270 distinction
    is the ±180° built into this rotation (there is no separate flip).
    """
    base = csharp_encode_base(resolution, jpeg=jpeg, pm=pm)
    return (base - orientation) % 360
