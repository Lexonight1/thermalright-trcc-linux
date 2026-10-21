"""What the Audio Visualizer tells every UI."""
from __future__ import annotations

from dataclasses import dataclass

from trcc.core.events import Event


@dataclass(frozen=True, slots=True)
class VisualHearingChanged(Event):
    """A panel's audio visual started -- or stopped -- hearing anything.
    Published on the change only.  Silence is how every OS reports blocked
    sound (a denied permission, the wrong output, nothing playing), never
    an error, so this is how a UI can tell the user why the visual is
    still."""
    key: str
    hearing: bool
