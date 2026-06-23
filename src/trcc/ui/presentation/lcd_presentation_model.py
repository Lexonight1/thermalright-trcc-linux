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
from typing import TYPE_CHECKING, Any

from .preview_geometry import (
    CanvasComposer,
    composed_preview_size,
    rotated_lcd_size,
)

if TYPE_CHECKING:
    from ...core.models import ProductInfo, Theme
    from ...core.protocol import DeviceProfile

# Default brightness % before the user picks one (legacy default).
_DEFAULT_BRIGHTNESS = 100

# Panel resolutions that get the multi-zone "Dynamic Island" split editor
# (legacy SPLIT_MODE_RESOLUTIONS) instead of the brightness-cycle button.
SPLIT_MODE_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (480, 1280), (1280, 480),
    (440, 1920), (1920, 440),
    (462, 1920), (1920, 462),
})


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

        # ── Activation / view-lifecycle flags (B3) ──
        # ui_active: multi-display windows share one preview widget set; only
        # the active handler may write to them — this is the gate.
        self.ui_active = False
        # configured: True once the first connect LOADED the persisted theme
        # (distinguishes first activation from a read-only re-select).
        self.configured = False
        # Per-device display state mirrored from DeviceSettings on restore.
        self.background_active = False
        self.brightness_level = _DEFAULT_BRIGHTNESS
        self.split_mode = 0
        self.ldd_is_split = False

    # ── Geometry (B2) ──────────────────────────────────────────────────

    def set_canvas(self, width: int, height: int) -> None:
        """Cache the device's pre-rotation canvas (connect / refresh).

        Seeds ``lcd_size`` to the same value; a later :meth:`apply_rotation`
        swaps it for portrait orientations.
        """
        self.state.canvas_size = (width, height)
        self.state.lcd_size = (width, height)

    def apply_rotation(self, degrees: int) -> None:
        """Update ``is_rotated`` + post-rotation ``lcd_size`` for an orientation.

        The catalogs + preview key off these; dispatching SetOrientation alone
        rotates the DEVICE, not the GUI's cached geometry.
        """
        self.state.is_rotated, self.state.lcd_size = rotated_lcd_size(
            self.state.canvas_size, degrees,
        )

    # ── Video math (B5) ────────────────────────────────────────────────
    # Pure per-tick arithmetic lifted off the handler's video path; the
    # QTimer + MediaService playback stay in the View, which feeds primitives.

    @staticmethod
    def video_interval_ms(fps: float | None) -> int:
        """ms-per-frame for a playback fps (None / 0 → 30 fps → 33 ms)."""
        return max(1, int(1000 / (fps or 30)))

    @staticmethod
    def seek_frame(percent: float, total: int) -> int:
        """Clamp a 0..1 seek fraction to a valid frame index in ``total``."""
        return max(0, min(total - 1, int(percent * total)))

    @staticmethod
    def progress_fraction(cursor: int, total: int) -> float:
        """Playback progress as 0..1 (0.0 when there are no frames)."""
        return (cursor / total) if total else 0.0

    def apply_split_mode(
        self, persisted_mode: int, lcd_size: tuple[int, int],
    ) -> int:
        """Resolve persisted split mode for a geometry; return the dispatch mode.

        Sets ``split_mode`` (default 2 when unset) and ``ldd_is_split`` (whether
        this panel's resolution supports the split editor), and returns the mode
        to send the device: the chosen mode on a split-capable panel, else 0.
        """
        self.split_mode = persisted_mode or 2
        self.ldd_is_split = lcd_size in SPLIT_MODE_RESOLUTIONS
        return self.split_mode if self.ldd_is_split else 0

    def preview_size(
        self,
        display: CanvasComposer,
        *,
        info: ProductInfo | None,
        theme: Theme | None,
        profile: DeviceProfile | None,
        orientation: int,
    ) -> tuple[int, int]:
        """Preview bezel/label dims for the active theme (#136).

        Composed canvas when a device + theme are present, else the cached
        canvas swapped for the user orientation.  The View supplies the
        device/theme primitives; the model owns the cached ``canvas_size``.
        """
        return composed_preview_size(
            display, info=info, theme=theme, profile=profile,
            orientation=orientation, canvas_size=self.state.canvas_size,
        )
