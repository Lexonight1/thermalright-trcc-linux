"""LED Protocol adapter — DeviceProtocol ABC conformance for RGB LED devices.

Unlike LCD protocols (which send frames), LedProtocol sends LED color
arrays + brightness/on-state via HID 64-byte reports. Delegates raw I/O
to `led.py::LedHidSender`.
"""
from __future__ import annotations

import logging

from trcc.legacy.core.models import HandshakeResult, UsbAddress

from .factory import ProtocolInfo, UsbProtocol

log = logging.getLogger(__name__)


class LedProtocol(UsbProtocol):
    """LED device communication via HID 64-byte reports (FormLED equivalent).

    Sends LED color arrays for RGB LED effects. Uses the same
    UsbTransport as HidProtocol (lazy open via Platform factory).
    """

    def __init__(
        self, vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__(vid, pid, usb_address=usb_address)
        self._sender = None

    def send_data(
        self,
        led_colors: list[tuple[int, int, int]],
        is_on: list[bool] | None = None,
        global_on: bool = True,
        brightness: int = 100,
    ) -> bool:
        """Send LED color data to the device."""
        def _do_send() -> bool:
            self._ensure_transport()
            assert self._transport is not None

            if self._sender is None:
                from .led import LedHidSender
                self._sender = LedHidSender(self._transport)

            from .led import LedPacketBuilder, remap_led_colors

            hr = self._handshake_result
            style = getattr(hr, 'style', None) if hr else None
            style_sub = getattr(hr, 'style_sub', 0) if hr else 0
            remapped = remap_led_colors(
                led_colors, style.style_id, style_sub,
            ) if style else led_colors

            packet = LedPacketBuilder.build_led_packet(
                remapped, is_on, global_on, brightness
            )

            try:
                return self._sender.send_data(packet)
            except (OSError, ValueError) as e:
                # USB I/O failure (USBError extends OSError) — reconnect + retry once.
                log.warning("LED send failed (%s) — reconnecting and retrying", e)
                self.close()
                self._handshake_result = None
                self.handshake()
                return self._sender.send_data(packet)

        return self._guarded_send("LED", _do_send)

    def _do_handshake(self) -> HandshakeResult | None:
        """LED handshake — cached after first call (firmware ignores re-handshakes)."""
        if self._handshake_result is not None:
            return self._handshake_result

        self._ensure_transport()
        assert self._transport is not None

        if self._sender is None:
            from .led import LedHidSender
            self._sender = LedHidSender(self._transport)

        result = self._sender.handshake()
        self._notify_state_changed("handshake_complete", True)
        return result

    def close(self) -> None:
        self._close_transport()
        self._sender = None

    def get_info(self) -> ProtocolInfo:
        return self._build_usb_protocol_info(
            "led", 1, "LED (HID 64-byte)", "RGB LED Controller",
            self._transport is not None and getattr(self._transport, 'is_open', False),
        )

    @property
    def protocol_name(self) -> str:
        return "led"

    @property
    def is_led(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"LedProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"
