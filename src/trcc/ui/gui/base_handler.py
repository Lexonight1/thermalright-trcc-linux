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
        log.info("BaseHandler.__init__: view=%r device=%s",
                 view, type(device).__name__)

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
        log.info("BaseHandler.deactivate: view=%r (no-op base)", self._view)

    def cleanup(self) -> None:
        """Release device resources on shutdown.  Override in subclass."""
        log.info("BaseHandler.cleanup: view=%r (no-op base)", self._view)

    # ── Tick + push ───────────────────────────────────────────────────

    def update_metrics(self, metrics: Any) -> None:
        """Push the latest metrics snapshot to handler-local widgets."""
        # Per-tick; DEBUG.  Subclasses override — base no-op log marks
        # the rare case a handler didn't bother to override at all.
        log.debug("BaseHandler.update_metrics: view=%r dropped (base no-op)",
                  self._view)
        del metrics

    def handle_frame(self, image: Any) -> None:
        """Show a rendered frame in the preview.  Override per device kind."""
        log.debug("BaseHandler.handle_frame: view=%r dropped (base no-op)",
                  self._view)
        del image

    def rebuild_preview(self) -> None:
        """Re-render the preview from current device state.

        Called by the window's ``FrameSent`` slot since next/'s frame
        event doesn't carry the rendered image.  LCDHandler overrides
        with the actual ``app.display.build_preview_surface`` pipeline;
        LEDHandler can ignore (its preview is segment colors, not a
        bitmap).
        """
        log.debug("BaseHandler.rebuild_preview: view=%r dropped (base no-op)",
                  self._view)

    # ── Video bus_bridge observers ───────────────────────────────────
    # LCDHandler overrides to drive its Qt animation timer; LEDHandler
    # ignores (LEDs don't animate per-frame from a video).  Defined on
    # the base so ``trcc_app.py`` can route ``VideoStarted`` /
    # ``VideoStopped`` events without a ``hasattr`` dance — every
    # handler subclass satisfies the same interface.

    def on_video_started(self, event: Any) -> None:
        """Domain event ``VideoStarted`` for this handler's device."""
        log.debug("BaseHandler.on_video_started: view=%r dropped (base no-op)",
                  self._view)
        del event

    def on_video_stopped(self, event: Any) -> None:
        """Domain event ``VideoStopped`` for this handler's device."""
        log.debug("BaseHandler.on_video_stopped: view=%r dropped (base no-op)",
                  self._view)
        del event
