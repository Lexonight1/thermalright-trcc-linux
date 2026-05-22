"""BaseHandler — shared interface for device handlers.

Holds a ``Device`` (the ABC) and routes the mutual lifecycle methods
through it. LCD and LED handlers inherit from this; they keep a typed
reference to their concrete subclass for the type-specific calls
(``send_image``, ``update_color``, zone-*, etc.) that aren't on the ABC.

This is the UI side of the chain:
    ``UI handler → device.{connect, cleanup, update_metrics, tick} → protocol``
"""
from __future__ import annotations

import logging
from typing import Any

from ...core.device import Device
from ...core.models import DeviceInfo

log = logging.getLogger(__name__)


class BaseHandler:
    """Shared handler interface — holds a ``Device``, routes mutual methods."""

    def __init__(self, device: Device, view: str) -> None:
        self._device = device
        self._view = view

    @property
    def view_name(self) -> str:
        return self._view

    @property
    def device(self) -> Device:
        """The handler's device, typed as the ABC for layer-uniform consumers."""
        return self._device

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._device.device_info if self._device else None

    def deactivate(self) -> None:
        """Pause this handler — called when switching away from device."""

    def cleanup(self) -> None:
        """Release device resources — delegates to ``Device.cleanup`` (mutual ABC method)."""
        if self._device:
            self._device.cleanup()

    def update_metrics(self, metrics: Any) -> None:
        """Push sensor metrics through to the device.

        Default impl forwards to the device's mutual ABC method. Subclasses
        may override to add UI-side concerns (preview repaint, etc.).
        """
        if self._device:
            self._device.update_metrics(metrics)

    def handle_frame(self, image: Any) -> None:
        """Receive a rendered frame from the background tick loop.

        Override in subclass — LCD shows preview, LED updates color display.
        """
