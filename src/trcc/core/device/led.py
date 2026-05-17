"""LEDDevice — LED controller device (RGB / zones / segments / clock).

A USB device with LED-specific concerns. Subclasses the ``Device`` ABC
shared with ``LCDDevice``; LED-specific surface (zone/color/mode/segment
updaters) lives here. The builder picks LCDDevice vs LEDDevice based on
``detected.protocol``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .._logging import tagged_logger
from ..models import LEDMode
from ..models.constants import TRACE_LEVEL
from .base import Device

if TYPE_CHECKING:
    from ..ports import DeviceProtocol

log = logging.getLogger(__name__)


class LEDDevice(Device):
    """A USB LED controller. Discovered, handshaked, DI'd to handlers.

    Builder wiring:
        device = ControllerBuilder.for_current_os().build_device(detected)
        device.connect(detected)
    """

    is_lcd = False
    is_led = True

    def __init__(
        self,
        *,
        protocol: Any = None,
        device_svc: Any = None,
        led_svc: Any = None,
        led_svc_factory: Any = None,
        led_config: Any = None,
    ) -> None:
        self._protocol = protocol  # DI'd by name via DeviceProtocolFactory
        self._device_svc = device_svc
        self._led_svc = led_svc
        self._led_svc_factory = led_svc_factory
        self._led_config = led_config
        self._info: Any = None
        self._init_status: str | None = None
        self.log: logging.Logger = log

    @property
    def protocol(self) -> DeviceProtocol | None:
        """The LED wire protocol DI'd by name at construction."""
        return self._protocol

    # ══════════════════════════════════════════════════════════════════════
    # Shared lifecycle (DeviceInfo)
    # ══════════════════════════════════════════════════════════════════════

    @property
    def connected(self) -> bool:
        if self._info is not None:
            return True
        return self._led_svc is not None

    @property
    def device_info(self) -> Any:
        if self._info is not None:
            return self._info
        if self._device_svc is not None:
            return self._device_svc.selected
        return None

    def cleanup(self) -> None:
        if self._led_svc:
            self._led_svc.cleanup()

    def update_metrics(self, metrics: Any) -> dict:
        if self._led_svc:
            self._led_svc.update_metrics(metrics)
        return {"success": True}

    def set_temp_unit(self, unit: int) -> dict:
        """Set temperature unit (0=Celsius, 1=Fahrenheit)."""
        unit_str = 'F' if unit else 'C'
        self._led_svc.set_seg_temp_unit(unit_str)
        self._led_send_and_save()
        return {"success": True,
                "message": f"Temperature unit set to {unit_str}"}

    # ══════════════════════════════════════════════════════════════════════
    # Connect
    # ══════════════════════════════════════════════════════════════════════

    def connect(self, detected: Any = None) -> dict:
        if self._led_svc:
            log.debug("LED connect: already connected (status=%s)", self._init_status)
            return {"success": True, "status": self._init_status or ""}

        if detected is not None and getattr(detected, 'implementation', '') == 'hid_led':
            from ..models import DeviceInfo
            self._info = DeviceInfo.from_detected(detected)
            log.info("LED connect: using detected %s", self._info.path)
        else:
            if self._device_svc is None:
                raise RuntimeError(
                    "LEDDevice requires a DeviceService. "
                    "Use ControllerBuilder.build_device() to wire dependencies.")
            self._device_svc.detect()
            self._info = next(
                (d for d in self._device_svc.devices
                 if d.implementation == 'hid_led'), None)
            if not self._info:
                log.warning("LED connect: no LED device found")
                return {"success": False, "error": "No LED device found"}

        self._led_svc = self._led_svc_factory(
            protocol=self._protocol,
            led_config=self._led_config,
        )
        self._init_status = self._led_svc.initialize(self._info)
        pm = getattr(self._info, 'pm_byte', 0)
        sub = getattr(self._info, 'sub_byte', 0)
        vid = int(self._info.vid) if isinstance(self._info.vid, int) else 0
        pid = int(self._info.pid) if isinstance(self._info.pid, int) else 0
        label = f'led:{getattr(self._info, "device_index", 0)} [{vid:04X}:{pid:04X} PM={pm} SUB={sub}]'
        self.log = tagged_logger(__name__, label)
        self.log.info("LED connected: %s style=%s", self._info.path, self._init_status)
        return {"success": True, "status": self._init_status or ""}

    # ══════════════════════════════════════════════════════════════════════
    # Tick
    # ══════════════════════════════════════════════════════════════════════

    def tick(self) -> dict | None:
        """Advance one LED animation frame, send to hardware, return colors."""
        if not self._led_svc:
            log.log(TRACE_LEVEL, "tick: no LED service — skipping")
            return None
        colors = self._led_svc.tick()
        display_colors = self._led_svc.apply_mask(colors)
        if self._led_svc.has_protocol:
            ok = self._led_svc.send_colors(colors)
            if not ok:
                log.log(TRACE_LEVEL, "tick: send_colors skipped (concurrent)")
        return {"colors": colors, "display_colors": display_colors}

    # ══════════════════════════════════════════════════════════════════════
    # Properties
    # ══════════════════════════════════════════════════════════════════════

    @property
    def status(self) -> str | None:
        return self._init_status

    @property
    def state(self) -> Any:
        """Current LEDState."""
        return self._led_svc.state if self._led_svc else None

    # ══════════════════════════════════════════════════════════════════════
    # Lifecycle (GUI path)
    # ══════════════════════════════════════════════════════════════════════

    def initialize_led(self, device: Any, led_style: int) -> dict:
        """Initialize for a known LED device (GUI — device already detected)."""
        if not self._led_svc:
            self._led_svc = self._led_svc_factory(
                protocol=self._protocol,
                led_config=self._led_config,
            )
        self._info = device
        self._init_status = self._led_svc.initialize(device, led_style)
        return {"success": True, "status": self._init_status or "",
                "style": led_style}

    # ══════════════════════════════════════════════════════════════════════
    # State-only mutators (GUI — timer handles send)
    # ══════════════════════════════════════════════════════════════════════

    def update_color(self, r: int, g: int, b: int) -> None:
        self._led_svc.set_color(r, g, b)

    def update_mode(self, mode: LEDMode | int) -> None:
        resolved = LEDMode(mode) if isinstance(mode, int) else mode
        self._led_svc.set_mode(resolved)

    def update_brightness(self, level: int) -> None:
        self._led_svc.set_brightness(max(0, min(100, level)))

    def update_global_on(self, on: bool) -> None:
        self._led_svc.toggle_global(on)

    def update_segment(self, index: int, on: bool) -> None:
        self._led_svc.toggle_segment(index, on)

    def update_zone_color(self, zone: int, r: int, g: int, b: int) -> None:
        self._led_svc.set_zone_color(zone, r, g, b)

    def update_zone_mode(self, zone: int, mode: LEDMode | int) -> None:
        resolved = LEDMode(mode) if isinstance(mode, int) else mode
        self._led_svc.set_zone_mode(zone, resolved)

    def update_zone_brightness(self, zone: int, level: int) -> None:
        self._led_svc.set_zone_brightness(zone, max(0, min(100, level)))

    def update_zone_on(self, zone: int, on: bool) -> None:
        self._led_svc.toggle_zone(zone, on)

    def update_zone_sync(self, enabled: bool) -> None:
        self._led_svc.set_zone_sync(enabled)

    def update_zone_sync_zone(self, zone: int, selected: bool) -> None:
        self._led_svc.set_zone_sync_zone(zone, selected)

    def update_zone_sync_interval(self, seconds: int) -> None:
        self._led_svc.set_zone_sync_interval(seconds)

    def update_clock_format(self, is_24h: bool) -> None:
        self._led_svc.set_clock_format(is_24h)

    def update_week_start(self, is_sunday: bool) -> None:
        self._led_svc.set_week_start(is_sunday)

    def update_disk_index(self, index: int) -> None:
        self._led_svc.set_disk_index(index)

    def update_memory_ratio(self, ratio: int) -> None:
        self._led_svc.set_memory_ratio(ratio)

    def update_test_mode(self, enabled: bool) -> None:
        self._led_svc.set_test_mode(enabled)

    def update_selected_zone(self, zone: int) -> None:
        self._led_svc.set_selected_zone(zone)

    # ══════════════════════════════════════════════════════════════════════
    # Command methods (CLI/API — immediate tick/send/save)
    # ══════════════════════════════════════════════════════════════════════

    def _led_apply_and_send(self) -> list:
        self._led_svc.toggle_global(True)
        colors = self._led_svc.tick()
        self._led_svc.send_colors(colors)
        self._led_svc.save_config()
        return colors

    def _led_send_and_save(self) -> None:
        self._led_svc.send_tick()
        self._led_svc.save_config()

    def _resolve_mode(self, mode: LEDMode | str | int) -> LEDMode | None:
        match mode:
            case LEDMode():
                return mode
            case int() if mode in LEDMode._value2member_map_:
                return LEDMode(mode)
            case str() if mode.upper() in LEDMode._member_map_:
                return LEDMode[mode.upper()]
            case _:
                return None

    def _validate_zone(self, zone: int) -> dict | None:
        n = len(self._led_svc.state.zones)
        if n == 0:
            return {"success": False, "error": "This LED device has no zones"}
        if zone < 0 or zone >= n:
            return {"success": False,
                    "error": f"Zone {zone} out of range (valid: 0–{n - 1})"}
        return None

    def _validate_segment(self, index: int) -> dict | None:
        n = len(self._led_svc.state.segment_on)
        if n == 0:
            return {"success": False,
                    "error": "This LED device has no segments"}
        if index < 0 or index >= n:
            return {"success": False,
                    "error": f"Segment {index} out of range (valid: 0–{n - 1})"}
        return None

    def set_color(self, r: int, g: int, b: int) -> dict:
        self._led_svc.set_mode(LEDMode.STATIC)
        self._led_svc.set_color(r, g, b)
        colors = self._led_apply_and_send()
        return {"success": True, "colors": colors,
                "message": f"LED color set to #{r:02x}{g:02x}{b:02x}"}

    def set_mode(self, mode: LEDMode | str | int) -> dict:
        resolved = self._resolve_mode(mode)
        if not resolved:
            return {"success": False, "error": f"Unknown mode '{mode}'",
                    "available": [m.name.lower() for m in LEDMode]}
        self._led_svc.set_mode(resolved)
        colors = self._led_apply_and_send()
        animated = resolved in (LEDMode.BREATHING, LEDMode.COLORFUL,
                                LEDMode.RAINBOW, LEDMode.TEMP_LINKED,
                                LEDMode.LOAD_LINKED)
        return {"success": True, "colors": colors, "animated": animated,
                "message": f"LED mode: {resolved.name.lower()}"}

    def set_brightness(self, level: int) -> dict:
        if level < 0 or level > 100:
            return {"success": False, "error": "Brightness must be 0-100"}
        self._led_svc.set_brightness(level)
        colors = self._led_apply_and_send()
        return {"success": True, "colors": colors,
                "message": f"LED brightness set to {level}%"}

    def toggle_global(self, on: bool) -> dict:
        self._led_svc.toggle_global(on)
        self._led_send_and_save()
        return {"success": True, "message": f"LEDs {'on' if on else 'off'}"}

    def set_sensor_source(self, source: str) -> dict:
        source = source.lower()
        if source not in ('cpu', 'gpu'):
            return {"success": False,
                    "error": "Source must be 'cpu' or 'gpu'"}
        self._led_svc.set_sensor_source(source)
        self._led_svc.save_config()
        return {"success": True,
                "message": f"LED sensor source set to {source.upper()}"}

    def set_zone_color(self, zone: int, r: int, g: int, b: int) -> dict:
        if err := self._validate_zone(zone):
            return err
        self._led_svc.set_zone_color(zone, r, g, b)
        colors = self._led_apply_and_send()
        return {"success": True, "colors": colors,
                "message": f"Zone {zone} color set to #{r:02x}{g:02x}{b:02x}"}

    def set_zone_mode(self, zone: int, mode: LEDMode | str | int) -> dict:
        if err := self._validate_zone(zone):
            return err
        resolved = self._resolve_mode(mode)
        if not resolved:
            return {"success": False, "error": f"Unknown mode '{mode}'"}
        self._led_svc.set_zone_mode(zone, resolved)
        colors = self._led_apply_and_send()
        return {"success": True, "colors": colors,
                "message": f"Zone {zone} mode set to {resolved.name.lower()}"}

    def set_zone_brightness(self, zone: int, level: int) -> dict:
        if err := self._validate_zone(zone):
            return err
        if level < 0 or level > 100:
            return {"success": False, "error": "Brightness must be 0-100"}
        self._led_svc.set_zone_brightness(zone, level)
        colors = self._led_apply_and_send()
        return {"success": True, "colors": colors,
                "message": f"Zone {zone} brightness set to {level}%"}

    def toggle_zone(self, zone: int, on: bool) -> dict:
        if err := self._validate_zone(zone):
            return err
        self._led_svc.toggle_zone(zone, on)
        self._led_send_and_save()
        return {"success": True,
                "message": f"Zone {zone} {'ON' if on else 'OFF'}"}

    def set_zone_sync(self, enabled: bool,
                      interval: int | None = None) -> dict:
        if interval is not None:
            self._led_svc.set_zone_sync_interval(interval)
        self._led_svc.set_zone_sync(enabled)
        self._led_send_and_save()
        return {"success": True,
                "message": f"Zone sync {'enabled' if enabled else 'disabled'}"}

    def set_zone_sync_zone(self, zone: int, selected: bool) -> dict:
        self._led_svc.set_zone_sync_zone(zone, selected)
        return {"success": True}

    def set_selected_zone(self, zone: int) -> dict:
        self._led_svc.set_selected_zone(zone)
        return {"success": True}

    def toggle_segment(self, index: int, on: bool) -> dict:
        if err := self._validate_segment(index):
            return err
        self._led_svc.toggle_segment(index, on)
        self._led_send_and_save()
        return {"success": True,
                "message": f"Segment {index} {'ON' if on else 'OFF'}"}

    def set_clock_format(self, is_24h: bool) -> dict:
        self._led_svc.set_clock_format(is_24h)
        self._led_send_and_save()
        return {"success": True,
                "message": f"Clock format set to {'24h' if is_24h else '12h'}"}

    def set_week_start(self, is_sunday: bool) -> dict:
        self._led_svc.set_week_start(is_sunday)
        self._led_send_and_save()
        return {"success": True}

    def set_disk_index(self, index: int) -> dict:
        self._led_svc.set_disk_index(index)
        return {"success": True}

    def set_memory_ratio(self, ratio: int) -> dict:
        self._led_svc.set_memory_ratio(ratio)
        return {"success": True}

    def set_test_mode(self, enabled: bool) -> dict:
        self._led_svc.set_test_mode(enabled)
        return {"success": True}

    def save_config(self) -> None:
        if self._led_svc:
            self._led_svc.save_config()

    def load_config(self) -> None:
        if self._led_svc:
            self._led_svc.load_config()
