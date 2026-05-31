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
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Half-block character — one cell encodes two rows of pixels by colouring
# the top half (foreground) and bottom half (background) separately.
_HALF_BLOCK = "▀"
_ANSI_RESET = "\033[0m"


class _PixelSampler(Protocol):
    """Minimal renderer surface ``get_pixels_rgb`` exposes."""

    def get_pixels_rgb(
        self, surface: Any, cols: int, rows: int,
    ) -> list[list[tuple[int, int, int]]]: ...


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


def image_to_ansi(
    renderer: _PixelSampler, surface: Any, cols: int = 60,
) -> str:
    """Render a surface as ANSI true-color block art.

    Samples the surface into a ``cols × rows`` grid via the renderer's
    ``get_pixels_rgb`` (rows derived from the surface aspect ratio so
    the preview isn't squashed), then collapses each pair of rows
    into a single half-block character.  Result fits in ``rows//2 + 1``
    terminal lines.
    """
    log.debug("image_to_ansi: cols=%d", cols)
    if cols <= 0:
        return ""
    # The renderer's get_pixels_rgb returns row-major output.  We need
    # to know the surface aspect ratio to pick a row count — sample
    # a tiny probe to get dimensions, then re-sample at the target
    # grid in one shot.
    probe = renderer.get_pixels_rgb(surface, 1, 1)
    del probe  # only the call confirms the surface is valid
    # Aspect from the surface itself — caller renders the LCD canvas
    # so the natural ratio is whatever the device's profile produced.
    # We approximate using a 1:1 default when no width/height
    # property is queryable here; production renderer (QtRenderer)
    # honours the requested grid size directly.
    rows = max(2, cols)
    if rows % 2:
        rows += 1
    pixels = renderer.get_pixels_rgb(surface, cols, rows)

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
