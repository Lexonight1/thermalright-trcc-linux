"""Device — the mutual ABC for connected hardware devices.

Both LCD displays and LED controllers conform to this contract. App, UI
handlers, and services hold ``Device`` (not the concrete subclass) wherever
the chain is generic: ``Platform.get_devices() -> list[Device]``, App
holds ``list[Device]``, UI dispatch picks the typed handler.

LSP rule: methods that only one flavor implements (LCD's ``send_image``,
LED's ``update_color`` / zone-* / etc.) live on the subclass, NOT promoted
to the ABC. The ABC carries only what both subclasses actually share —
discovered by mechanical comparison of their public surfaces.

Chain: the device's *model* (the registry ``DeviceEntry``) names its
protocol by string. ``DeviceProtocolFactory`` dispatches that name to the
concrete ``Protocol`` class. The result is injected into the device via
its constructor — ``device.protocol`` is the bound contract from then on.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..models import DeviceInfo

if TYPE_CHECKING:
    from ..ports import DeviceProtocol


class Device(ABC):
    """Mutual contract for every connected device the app holds."""

    is_lcd: bool = False
    is_led: bool = False

    @property
    @abstractmethod
    def protocol(self) -> DeviceProtocol | None:
        """The wire protocol DI'd at construction (by name, via factory).

        ``None`` only when the device was built without a detected source —
        e.g. API standalone mode that discovers later. After ``connect()``
        succeeds, this is always populated.
        """

    @property
    @abstractmethod
    def connected(self) -> bool:
        """True if the device is reachable (handshake completed)."""

    @property
    @abstractmethod
    def device_info(self) -> DeviceInfo | None:
        """Discovery + handshake facts. None until ``connect()`` succeeds."""

    @abstractmethod
    def connect(self, detected: Any = None) -> dict:
        """Open the device — handshake, fill ``device_info``.

        Returns ``{"success": bool, "error": str?, ...}``. Subclasses may
        add extras; callers consume ``success`` and ``error`` uniformly.
        """

    @abstractmethod
    def cleanup(self) -> None:
        """Release services, transports, and threads owned by this device."""

    @abstractmethod
    def tick(self) -> Any:
        """Per-metrics-loop hook — render/send/advance. Optional payload."""

    @abstractmethod
    def update_metrics(self, metrics: Any) -> dict:
        """Push fresh sensor metrics into the device's overlay/state."""

    @abstractmethod
    def set_temp_unit(self, unit: int) -> dict:
        """Set temperature unit (0=Celsius, 1=Fahrenheit)."""
