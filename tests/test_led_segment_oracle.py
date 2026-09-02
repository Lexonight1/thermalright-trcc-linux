"""Our LED element tables must match the C#'s — BOTH of them, separately.

``UCScreenLED.cs`` declares **two** per-product arrays, and they are not always
the same size:

    ledPosition{n} = new int[N, 4]    the rectangles the preview DRAWS
    isOn{n}        = new bool[M]      the flags the mask CARRIES

For nine styles N == M.  For **lf12 (93/124)** and **lf10 (104/116)** the vendor
declares more mask flags than drawn rectangles, and we port both numbers
faithfully — ``STYLE_POSITIONS`` from ``ledPosition``, ``SegmentDisplay
.mask_size`` from ``isOn``.

This file exists because that asymmetry looks exactly like a bug.  On
2026-09-02 it was read as one: the two tables were diffed against each other,
found to disagree for four styles, and written up as a defect needing a per-
style C# investigation.  They are not two answers to one question — they are
faithful ports of two different arrays.  A gate that pins each of ours to ITS
OWN C# array turns that afternoon into a one-line failure, and stops the next
reader "fixing" a number that was right.

Reads the decompile directly rather than transcribing the sizes here: a
transcription is a second copy, and this suite has already learned where those
end up (see ``test_csharp_oracle_parity``'s header on the rotation constants).
``test_oracle_version`` guarantees a decompile that IS present is 2.1.6.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from trcc.core.led_models import LED_STYLES, LEGACY_STYLE_ID
from trcc.core.models import LedStyle
from trcc.services.led_segment import get_display
from trcc.ui.gui.uc_screen_led import STYLE_POSITIONS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from core.csharp import (  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
)

_SOURCE = DECOMPILE_ROOT / "TRCC.DCUserControl" / "UCScreenLED.cs"

_ABSENT = pytest.mark.skipif(
    not _SOURCE.exists(),
    reason=f"no decompile at {_SOURCE} — extract with `ilspycmd -p`, "
           f"or set TRCC_DECOMPILE",
)

#: Legacy style id → the suffix the C# gives that product's arrays.  Ten are
#: numbered; the last two are named.
_CSHARP_SUFFIX: dict[int, str] = {
    1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "LF15", 12: "LF13",
}

#: A floor, not a target — 12 today.  Guards the DENOMINATOR: a regex that stops
#: matching makes every style vacuously correct.
_MIN_ARRAYS = 10


def _csharp_arrays() -> tuple[dict[str, int], dict[str, int]]:
    """``(ledPosition sizes, isOn sizes)`` keyed by the C# suffix."""
    src = _SOURCE.read_text(encoding="utf-8", errors="replace")
    pos = {m.group(1): int(m.group(2)) for m in
           re.finditer(r"ledPosition(\w+) = new int\[(\d+), 4\]", src)}
    on = {m.group(1): int(m.group(2)) for m in
          re.finditer(r"isOn(\w+) = new bool\[(\d+)\]", src)}
    assert len(pos) >= _MIN_ARRAYS and len(on) >= _MIN_ARRAYS, (
        f"parsed only {len(pos)} ledPosition / {len(on)} isOn arrays from "
        f"{_SOURCE.name} — under the floor of {_MIN_ARRAYS}.  Every style "
        f"would pass vacuously; the declarations were reformatted or the "
        f"regex is wrong."
    )
    return pos, on


#: Styles that deliberately do NOT match, each with the reason.  Anything else
#: failing is real drift.
_RECORDED: dict[LedStyle, str] = {
    LedStyle.LF13: (
        "scoped: the C# gives LF13 a single full-panel element "
        "(ledPositionLF13 = {{0, 0, 460, 460}}, isOnLF13 = bool[1]) — it is a "
        "whole-screen device, not a segment display.  No SegmentDisplay is "
        "registered for it, so mask_size is 0, which is what LedStyleEntry's "
        "own comment defines as 'non-segment style'.  STYLE_POSITIONS still "
        "carries the one rectangle and IS checked below"
    ),
    LedStyle.MAGIC_QUBE: (
        "scoped: not in UCScreenLED.cs at all.  Per AUDIT_LED_SEGMENT it is "
        "FormKVMALED6, 'a separate ARGB 6/10-channel lighting controller, NOT "
        "a segment display', so neither of our numbers is measurable here"
    ),
}


@_ABSENT
@pytest.mark.parametrize("style", sorted(LED_STYLES, key=lambda s: s.value))
def test_drawn_rectangles_match_the_csharp_ledPosition(style: LedStyle) -> None:
    """``STYLE_POSITIONS`` is sized by the C#'s ``ledPosition`` array."""
    sid = LEGACY_STYLE_ID.get(style)
    suffix = _CSHARP_SUFFIX.get(sid) if sid is not None else None
    if suffix is None:
        assert style in _RECORDED, (
            f"{style.value} has no C# array and no recorded reason"
        )
        return
    pos, _ = _csharp_arrays()
    assert len(STYLE_POSITIONS.get(sid, ())) == pos[suffix], (
        f"{style.value} (id {sid}): the preview draws "
        f"{len(STYLE_POSITIONS.get(sid, ()))} rectangle(s), the C# declares "
        f"ledPosition{suffix} = new int[{pos[suffix]}, 4]"
    )


@_ABSENT
@pytest.mark.parametrize("style", sorted(LED_STYLES, key=lambda s: s.value))
def test_mask_size_matches_the_csharp_isOn(style: LedStyle) -> None:
    """``SegmentDisplay.mask_size`` is sized by the C#'s ``isOn`` array.

    A DIFFERENT array from the one above — that is the whole point of the file.
    """
    sid = LEGACY_STYLE_ID.get(style)
    suffix = _CSHARP_SUFFIX.get(sid) if sid is not None else None
    if suffix is None or style in _RECORDED:
        assert style in _RECORDED, (
            f"{style.value} has no C# array and no recorded reason"
        )
        return
    _, on = _csharp_arrays()
    display = get_display(style)
    mask = display.mask_size if display is not None else 0
    assert mask == on[suffix], (
        f"{style.value} (id {sid}): our mask carries {mask} flag(s), the C# "
        f"declares isOn{suffix} = new bool[{on[suffix]}]"
    )


@_ABSENT
def test_the_two_arrays_really_do_differ_for_lf12_and_lf10() -> None:
    """The premise of this file, asserted rather than remembered.

    If the C# ever makes them equal everywhere, the "two different quantities"
    reasoning above stops being load-bearing and this file should be re-read
    rather than trusted.
    """
    pos, on = _csharp_arrays()
    asymmetric = {k for k in pos if k in on and pos[k] != on[k]}
    assert asymmetric == {"6", "7"}, (
        f"the C#'s asymmetric products changed: expected lf12 (6) and lf10 "
        f"(7), found {sorted(asymmetric)}"
    )


@_ABSENT
def test_every_recorded_exception_is_still_needed() -> None:
    """A reason nobody re-reads is a decision that expires silently."""
    pos, on = _csharp_arrays()
    for style, reason in _RECORDED.items():
        assert reason.startswith("scoped:"), f"{style.value}: untagged reason"
        sid = LEGACY_STYLE_ID.get(style)
        suffix = _CSHARP_SUFFIX.get(sid) if sid is not None else None
        if suffix is None:
            continue
        display = get_display(style)
        mask = display.mask_size if display is not None else 0
        assert mask != on[suffix], (
            f"{style.value} now MATCHES the C# ({mask} == {on[suffix]}) — "
            f"delete its entry from _RECORDED so the gate covers it"
        )
