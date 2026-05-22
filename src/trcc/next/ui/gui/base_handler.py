"""BaseHandler — shared interface for per-device GUI handlers.

Phase 5 will collapse the legacy ``Device`` reference into a string
``_device_key`` (vid:pid) since next/'s handlers dispatch Commands
rather than calling device methods directly.  For now this base just
defines the lifecycle hooks the window calls (``cleanup``, ``deactivate``,
``update_metrics``, ``handle_frame``, ``rebuild_preview``) so the window
can talk to handlers via a stable interface.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class BaseHandler:
    """Shared handler base — minimal ``view_name`` + lifecycle stubs.

    Concrete handlers (``LCDHandler`` / ``LEDHandler``) take additional
    constructor args + override the lifecycle methods they care about.
    The window only calls into this base interface so any handler that
    keeps it satisfied can be swapped in.
    """

    def __init__(self, device: Any, view: str) -> None:
        # ``device`` is the next/ ``Device`` (HidLcd / ScsiLcd / Led / …)
        # for now; Phase 5 swaps it for a key string so handlers can
        # dispatch through the App without holding device refs.
        self._device = device
        self._view = view

    @property
    def view_name(self) -> str:
        return self._view

    @property
    def device(self) -> Any:
        """The handler's device.  Typed as ``Any`` while Phase 5 lands."""
        return self._device

    # ── Lifecycle ─────────────────────────────────────────────────────

    def deactivate(self) -> None:
        """Pause this handler — called when the window switches devices."""

    def cleanup(self) -> None:
        """Release device resources on shutdown.  Override in subclass."""

    # ── Tick + push ───────────────────────────────────────────────────

    def update_metrics(self, metrics: Any) -> None:
        """Push the latest metrics snapshot to handler-local widgets."""
        del metrics

    def handle_frame(self, image: Any) -> None:
        """Show a rendered frame in the preview.  Override per device kind."""
        del image

    def rebuild_preview(self) -> None:
        """Re-render the preview from current device state.

        Called by the window's ``FrameSent`` slot since next/'s frame
        event doesn't carry the rendered image.  LCDHandler overrides
        with the actual ``app.display.build_preview_surface`` pipeline;
        LEDHandler can ignore (its preview is segment colors, not a
        bitmap).
        """
