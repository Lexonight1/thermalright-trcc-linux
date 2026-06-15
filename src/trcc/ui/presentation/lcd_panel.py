"""LCD panel composition model — what an LCD device's preview panel shows.

Toolkit-free (no Qt): the single source of "given the resolution the handshake
resolves to, how does the LCD preview render" — the preview frame image, where
the LCD area sits inside the 500x500 preview container, and whether the panel is
a **widescreen** ("bilibili screen", C# ``isBiliPingmu``) panel vs a standard
square/portrait one.  Both graphical front-ends (``ui/gui``, the in-progress
``ui/qtgui`` rebuild) render their preview from this one contract instead of each
carrying their own copy of the resolution→offset table.

The LCD form is *one* form for every LCD device (C# ``FormCZTVInit``); the
per-device variation is driven entirely by the handshake-resolved resolution.
The ``widescreen`` flag is the C#'s, extracted from ``FormCZTVInit`` by
``dev/tools/audit_csharp.py`` (the ``PANELS — LCD composition`` dimension);
``tests/test_lcd_panel_model.py`` cross-checks it against the audit parser
whenever the decompile is present, so a vendor change surfaces as a failing
guard rather than silent drift.

The preview offsets are normalised to the shared 500x500 preview container both
front-ends use, so they are plain data (tuples + frame-image names), not a
toolkit concern.

Unit-testable with plain ``pytest`` — there is no toolkit here.
"""
from __future__ import annotations

from dataclasses import dataclass

# Preview placement per resolution: (left, top, width, height, frame_image).
# Native 1:1 for square / small panels; scaled to fit the 500x500 container for
# the larger widescreen ones.  Mirrors C# UCScreenImage.SetMyUCScreenImage().
_PREVIEW_OFFSETS: dict[tuple[int, int], tuple[int, int, int, int, str]] = {
    # Square / small — 1:1 native size
    (240, 240): (130, 130, 240, 240, 'preview_240x240.png'),
    (320, 320): (90, 90, 320, 320, 'preview_320x320.png'),
    (360, 360): (70, 70, 360, 360, 'preview_360x360_round.png'),
    (480, 480): (10, 10, 480, 480, 'preview_480x480.png'),
    # Rectangular — 1:1 native size
    (240, 320): (130, 90, 240, 320, 'preview_240x320.png'),
    (320, 240): (90, 130, 320, 240, 'preview_320x240.png'),
    (240, 400): (130, 50, 240, 400, 'preview_240x400.png'),
    (400, 240): (50, 130, 400, 240, 'preview_400x240.png'),
    (180, 480): (160, 10, 180, 480, 'preview_180x480.png'),
    (480, 180): (10, 160, 480, 180, 'preview_480x180.png'),
    (270, 480): (115, 10, 270, 480, 'preview_270x480.png'),
    (480, 270): (10, 115, 480, 270, 'preview_480x270.png'),
    # Widescreen — scaled to fit 500x500 (isBiliPingmu=true)
    (640, 480): (90, 130, 320, 240, 'preview_320x240.png'),
    (480, 640): (130, 90, 240, 320, 'preview_240x320.png'),
    (640, 172): (90, 207, 320, 86, 'preview_320x86.png'),
    (172, 640): (207, 90, 86, 320, 'preview_86x320.png'),
    (800, 480): (50, 130, 400, 240, 'preview_400x240.png'),
    (480, 800): (130, 50, 240, 400, 'preview_240x400.png'),
    (854, 480): (36, 130, 427, 240, 'preview_427x240.png'),
    (480, 854): (130, 36, 240, 427, 'preview_240x427.png'),
    (960, 540): (10, 115, 480, 270, 'preview_480x270.png'),
    (540, 960): (115, 10, 270, 480, 'preview_270x480.png'),
    (960, 320): (10, 170, 480, 160, 'preview_480x160.png'),
    (320, 960): (170, 10, 160, 480, 'preview_160x480.png'),
    (1280, 480): (10, 160, 480, 180, 'preview_480x180.png'),
    (480, 1280): (160, 10, 180, 480, 'preview_180x480.png'),
    (1600, 720): (50, 160, 400, 180, 'preview_400x180.png'),
    (720, 1600): (160, 50, 180, 400, 'preview_180x400.png'),
    (1920, 440): (10, 195, 480, 110, 'preview_480x110.png'),
    (440, 1920): (195, 10, 110, 480, 'preview_110x480.png'),
    (1920, 462): (10, 192, 480, 116, 'preview_480x116.png'),
    (462, 1920): (192, 10, 116, 480, 'preview_116x480.png'),
}

_DEFAULT_OFFSET: tuple[int, int, int, int, str] = (90, 90, 320, 320, 'preview_320x320.png')

# Widescreen ("bilibili screen", C# isBiliPingmu) resolutions, landscape-canonical.
# Portrait/rotated forms (e.g. 480x854) are matched by also testing the swap.
# Source of truth: dev/tools/audit_csharp._lcd_panel_composition (FormCZTVInit).
_WIDESCREEN: frozenset[tuple[int, int]] = frozenset({
    (800, 480), (854, 480), (960, 320), (960, 540),
    (1280, 480), (1600, 720), (1920, 440), (1920, 462),
})


@dataclass(frozen=True, slots=True)
class LcdPanelModel:
    """What an LCD preview panel shows, for one resolution.

    ``widescreen``: the C# ``isBiliPingmu`` panel kind (proportional preview);
    standard square/portrait panels are ``False``.
    ``offset_info``: the 5-tuple ``(left, top, width, height, frame_image)``
    placing the LCD area inside the shared 500x500 preview container.
    """
    resolution: tuple[int, int]
    widescreen: bool
    offset_info: tuple[int, int, int, int, str]


def lcd_panel_for(resolution: tuple[int, int]) -> LcdPanelModel:
    """Map a handshake-resolved ``(width, height)`` to its panel composition.

    Unknown resolutions fall back to the 320x320 preview, matching the View's
    historical default.
    """
    w, h = resolution
    return LcdPanelModel(
        resolution=(w, h),
        widescreen=(w, h) in _WIDESCREEN or (h, w) in _WIDESCREEN,
        offset_info=_PREVIEW_OFFSETS.get((w, h), _DEFAULT_OFFSET),
    )
