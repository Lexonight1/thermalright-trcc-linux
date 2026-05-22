"""DeviceFactory — abstract factory for the concrete ``Device`` class.

Third leg of the three-factory chain (after ``PlatformFactory`` and
``ProtocolFactory``). Same idiom: ABC + ``@DeviceFactory.register(kind)``
self-registering subclasses + single ``DeviceFactory.for_info(info, builder)``
chokepoint that dispatches by *device kind* (``'lcd'`` vs ``'led'``).

Why the third factory: every chain step is now factory-routed.

    PlatformFactory.current()              ← OS dispatch
        ↓
    ProtocolFactory.for_info(info)         ← protocol dispatch (by name)
        ↓
    DeviceFactory.for_info(info, builder)  ← device dispatch (by kind)
        ↓
    Device(protocol=…, …)                  ← fully wired

OCP wins: new Device kind = one new decorated subclass. Zero touchpoints
in ``ControllerBuilder``, ``Trcc``, UI, or any caller that holds a Device.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from .base import Device

if TYPE_CHECKING:
    from ..builder import ControllerBuilder
    from ..models import DeviceInfo


class DeviceFactory(ABC):
    """Abstract factory for one Device kind. Subclasses register via ``@register``.

    The builder is injected at construction so each ``make()`` has access
    to the shared deps (renderer, events, data_dir, device_svc, config
    callables) without forcing every Device kind to take the same 11-arg
    constructor explicitly.
    """

    _registry: ClassVar[dict[str, type[DeviceFactory]]] = {}

    @classmethod
    def register(cls, kind: str):
        """Mark a factory subclass for a device kind (``'lcd'`` or ``'led'``)."""
        def deco(sub: type[DeviceFactory]) -> type[DeviceFactory]:
            cls._registry[kind] = sub
            return sub
        return deco

    @classmethod
    def for_info(
        cls,
        info: DeviceInfo | None,
        builder: ControllerBuilder,
    ) -> Device:
        """Build the right ``Device`` from a ``DeviceInfo`` (dispatch by kind).

        ``info=None`` (API standalone — no device detected yet) defaults to
        the LCD factory; the resulting Device discovers via its later
        ``connect(detected)`` call.
        """
        from ..models import PROTOCOL_TRAITS
        if info is None:
            kind = 'lcd'
        else:
            traits = PROTOCOL_TRAITS.get(info.protocol, PROTOCOL_TRAITS['scsi'])
            kind = 'led' if traits.is_led else 'lcd'
        return cls._registry[kind](builder).make(info)

    def __init__(self, builder: ControllerBuilder) -> None:
        self._builder = builder

    @abstractmethod
    def make(self, info: DeviceInfo | None) -> Device:
        """Construct the Device — protocol DI'd by name, services wired in."""


@DeviceFactory.register('led')
class LEDDeviceFactory(DeviceFactory):
    """Build ``LEDDevice`` with its services and the DI'd ``LedProtocol``."""

    def make(self, info: DeviceInfo | None) -> Device:
        from ...adapters.device.factory import DeviceProtocolFactory
        from ...services import LEDService
        from ...services.led_config import LEDConfigService
        from .led import LEDDevice

        protocol = DeviceProtocolFactory.get_protocol(info) if info is not None else None
        return LEDDevice(
            protocol=protocol,
            device_svc=self._builder._build_device_svc(),
            led_svc_factory=LEDService,
            led_config=LEDConfigService(**self._builder._build_config_callables()),
        )


@DeviceFactory.register('lcd')
class LCDDeviceFactory(DeviceFactory):
    """Build ``LCDDevice`` with its services and the DI'd protocol.

    Also the fallback when ``info is None`` (API standalone) — produces an
    LCDDevice with ``protocol=None`` that will discover via ``connect()``.
    """

    def make(self, info: DeviceInfo | None) -> Device:
        from ...adapters.device.factory import DeviceProtocolFactory
        from ...conf import Settings
        from ...services.image import ImageService
        from ...services.lcd_config import LCDConfigService
        from ...services.theme import theme_info_from_directory
        from .lcd import LCDDevice

        protocol = DeviceProtocolFactory.get_protocol(info) if info is not None else None
        b = self._builder
        device_svc = b._build_device_svc()
        cfg = b._build_config_callables()
        build_fn = b._make_build_services_fn()

        renderer = b._renderer
        if renderer is None:
            raise RuntimeError(
                "ControllerBuilder: renderer not set. "
                "Dispatch InitPlatformCommand with renderer_factory before building devices.")
        ImageService.set_renderer(renderer)
        lcd_config = LCDConfigService(
            **cfg,
            apply_format_prefs_fn=Settings.apply_format_prefs,
        )
        result = build_fn(device_svc, renderer)
        device = LCDDevice(
            protocol=protocol,
            device_svc=device_svc,
            display_svc=result['display_svc'],
            theme_svc=result['theme_svc'],
            renderer=renderer,
            dc_config_cls=result['dc_config_cls'],
            load_config_json_fn=result['load_config_json_fn'],
            theme_info_from_dir_fn=theme_info_from_directory,
            lcd_config=lcd_config,
            build_services_fn=build_fn,
            events=b._events,
        )
        if b._data_dir:
            device.initialize(b._data_dir)
        return device


__all__ = ['DeviceFactory', 'LCDDeviceFactory', 'LEDDeviceFactory']
