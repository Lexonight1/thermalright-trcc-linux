"""ANSI terminal preview helpers — pure formatting, no I/O.

Used by ``trcc display test-lcd``, ``trcc led test-led`` and the
``trcc system doctor`` ASCII preview to draw rendered frames + LED
zone colors directly in the terminal.

Both functions emit ANSI true-color (24-bit) escape sequences — the
output is meant for modern terminals (any released after ~2018).
Older terminals fall back to literal escape bytes; the renderer can't
detect that here.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Half-block character — one cell encodes two rows of pixels by colouring
# the top half (foreground) and bottom half (background) separately.
_HALF_BLOCK = "▀"
_ANSI_RESET = "\033[0m"


def zones_to_ansi(colors: list[tuple[int, int, int]]) -> str:
    """Render LED zone colors as ANSI true-color blocks.

    Each ``(r, g, b)`` becomes a two-cell coloured square via the
    background-colour escape.  Concatenated horizontally so a six-
    zone strip prints as ``[][][][][][]``.  Caller is responsible
    for a trailing newline if one's wanted.
    """
    log.debug("zones_to_ansi: zones=%d", len(colors))
    if not colors:
        return ""
    parts = [
        f"\033[48;2;{r};{g};{b}m  {_ANSI_RESET}" for r, g, b in colors
    ]
    return "".join(parts)


def image_to_ansi(pixels: list[list[tuple[int, int, int]]]) -> str:
    """Render a sampled RGB grid as ANSI true-color block art.

    Takes the grid ``BuildPreview(sample_cols=…)`` returns — row-major
    ``pixels[y][x] -> (r, g, b)``, already sized to the surface's aspect
    ratio — and collapses each pair of rows into one half-block
    character, so the output is ``rows // 2`` terminal lines.

    Pure formatting: the sampling lives behind the Command (the renderer
    is the only thing that can read a surface, and a UI must not hold
    one).  An empty grid renders as an empty string.
    """
    rows = len(pixels)
    cols = len(pixels[0]) if rows else 0
    log.debug("image_to_ansi: grid=%dx%d", cols, rows)
    if not cols:
        return ""

    lines: list[str] = []
    for y in range(0, rows, 2):
        parts: list[str] = []
        top_row = pixels[y]
        bottom_row = pixels[y + 1] if y + 1 < rows else [(0, 0, 0)] * cols
        for x in range(cols):
            tr, tg, tb = top_row[x]
            br, bg, bb = bottom_row[x]
            parts.append(
                f"\033[38;2;{tr};{tg};{tb}m"
                f"\033[48;2;{br};{bg};{bb}m{_HALF_BLOCK}",
            )
        lines.append("".join(parts) + _ANSI_RESET)
    return "\n".join(lines)
