"""Domain models for the decompile-miner — pure dataclasses, zero I/O.

These are the *facts* mined out of a decompiled binary: the data tables the
Windows app hides inside code (a ``switch`` is a lookup table; a byte-array
literal is a wire command).  They carry a ``source`` provenance string (the
decompiled line/guard they came from) so every extracted value is auditable
back to the binary — no unexplained magic numbers land in the clean app.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RotationTable:
    """One panel's orientation → wire-rotation mapping (a C# ``directionB``
    switch).  ``guard`` is the condition that selects this panel in the
    decompile (e.g. ``is854x480``, ``w == h && pm == 6``); ``angles`` maps the
    display orientation to the degrees the app rotates the image before encode.
    """

    guard: str
    angles: dict[int, int]                 # orientation → rotate degrees
    baseline: int | None = None            # angle at orientation 0 (the mount offset)

    def as_row(self) -> dict[str, object]:
        return {"guard": self.guard, "baseline": self.baseline, "angles": self.angles}


@dataclass(frozen=True)
class WireCommand:
    """A byte-array literal found next to a device send call — a candidate
    handshake / control command (e.g. the sleep/clear bytes #143 needs)."""

    name: str
    data: tuple[int, ...]
    source: str                            # the decompiled context it came from


@dataclass
class Extraction:
    """Everything mined from one binary — the tool's output."""

    artifact: str
    rotation_tables: list[RotationTable] = field(default_factory=list)
    wire_commands: list[WireCommand] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.artifact}: {len(self.rotation_tables)} rotation table(s), "
                f"{len(self.wire_commands)} wire command(s)")
