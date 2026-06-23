"""LcdPresentationModel — Qt-free coordination state for one LCD device View.

The handler (``ui/gui/lcd_handler.py``) is a thin Qt View: it owns the
``QTimer``s, the widget pokes, and ``app.dispatch(...)``.  The per-device display
state and the decisions it coordinates are pure — this model holds them with
**zero Qt, zero App handle, zero widgets**, so they unit-test without a
``QApplication`` and a different presentation (TUI / web) could bind the same
logic.

Increment 5 of the PM refactor lifts the handler's state + decisions here one
slice at a time.  B1 establishes the model and relocates the ``DeviceState``
cache; later slices add the geometry, activation, restore and render decisions.
Its purity (Qt-free, App-free, adapter-free) is machine-enforced by
``tests/test_architecture_boundaries.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DeviceState:
    """Per-device cache of derived display state — a Qt-free value object.

    Populated / refreshed on ``apply_device_config`` and event-driven callbacks
    (orientation, theme-load, overlay-toggle).  Read locally per frame so the
    hot path doesn't pay per-call dispatch overhead.
    """

    canvas_size: tuple[int, int] = (0, 0)        # pre-rotation (w, h)
    lcd_size: tuple[int, int] = (0, 0)           # post-rotation (w, h)
    is_rotated: bool = False                     # 90° / 270° → True
    overlay_enabled: bool = False
    current_theme_path: Path | None = None
    last_metrics: Any = None                     # cached for video-overlay updates


class LcdPresentationModel:
    """Qt-free coordination model for one LCD device View.

    Holds no Qt, no ``App`` handle and no widgets: the View feeds it primitives
    and reads back its state / decisions.  B1 holds only :class:`DeviceState`;
    subsequent slices grow the activation flags and the geometry / restore /
    render decisions onto it.
    """

    def __init__(self, device_key: str) -> None:
        self.device_key = device_key
        self.state = DeviceState()
