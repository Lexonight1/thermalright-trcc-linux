"""Hex-color parsing primitive shared across UIs and the DC codec.

Why centralised: the project had four parsers — ``cli/led.py:38``,
``cli/display.py:79``, ``qtgui/.../configuration_panel.py:261``, and
``services/_dc.py:705`` — each doing the same ``lstrip("#") + slice
+ int(_, 16)`` work with subtly different return shapes and failure
modes (one raised ``typer.BadParameter``, one returned ``None``, one
returned ``(0, 0, 0)``, one returned ``(255, 255, 255, 255)``).
Centralising the parsing primitive lets each caller keep its own
preferred wrapper around the same well-tested core.

See ``memory/project_hexagonal_solid_dry_plan`` §1.

Pure-stdlib, no project imports.
"""

from __future__ import annotations


def parse_hex(value: str) -> tuple[int, int, int, int]:
    """Parse ``#RRGGBB`` or ``#RRGGBBAA`` to ``(r, g, b, a)``.

    Strips a leading ``#`` if present and tolerates whitespace.  For a
    6-character form, alpha defaults to 255 (opaque).  Raises
    ``ValueError`` on any malformed input — callers translate that to
    their domain's preferred failure UX (``typer.BadParameter``,
    silent ``None``, ``(0, 0, 0)`` fallback, etc.).
    """
    s = value.strip().lstrip("#")
    if len(s) == 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
    if len(s) == 8:
        return (int(s[0:2], 16), int(s[2:4], 16),
                int(s[4:6], 16), int(s[6:8], 16))
    raise ValueError(
        f"hex color must be #RRGGBB or #RRGGBBAA, got {value!r}"
    )
