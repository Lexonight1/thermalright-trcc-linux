"""Device adapters — transport + concrete Device subclasses, dispatched
by DeviceFactory.

The factory IS the OCP chokepoint for device support: a new wire
protocol adds one class under ``adapters/device/`` with
``@DeviceFactory.register(Wire.X)``.  Importing this package fires the
side-effect imports of every device module, which fire their
``@register`` decorators, which populate the registry.

``core/ports.Device`` stays a pure ABC; the ``Wire → class`` dispatch
table lives here in the adapter layer — not in ``app.py`` (where it
was an ad-hoc dict), not in core (which must never name a concrete
adapter class).

See ``memory/project_hexagonal_solid_dry_plan`` §2.  Note: the cutover
unified legacy's separate Protocol + Device layers into one ``Device``
ABC, so this is the only device-construction factory — there is no
separate ProtocolFactory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from ...core.errors import DeviceNotFoundError
from ...core.models import Wire

if TYPE_CHECKING:
    from ...core.ports import Device

log = logging.getLogger(__name__)


class DeviceFactory:
    """Registry + dispatch chokepoint for wire-specific ``Device`` subclasses.

    Subclasses self-register via ``@DeviceFactory.register(Wire.SCSI)``
    (etc.); ``for_wire`` returns the class for a given wire so the
    composition root can construct it with the right transport.
    """

    _REGISTRY: ClassVar[dict[Wire, type[Device]]] = {}

    @classmethod
    def register(cls, wire: Wire) -> Callable[[type[Device]], type[Device]]:
        """Decorator: register *device_cls* under *wire*."""
        def deco(device_cls: type[Device]) -> type[Device]:
            cls._REGISTRY[wire] = device_cls
            log.debug("DeviceFactory: registered %s for %s",
                      device_cls.__name__, wire.value)
            return device_cls
        return deco

    @classmethod
    def for_wire(cls, wire: Wire) -> type[Device]:
        """Return the ``Device`` subclass for *wire*, or raise.

        Raises ``DeviceNotFoundError`` for an unregistered wire — the
        same error the old ``app._DEVICE_CLASSES.get`` guard raised,
        single-sourced here at the chokepoint.
        """
        device_cls = cls._REGISTRY.get(wire)
        if device_cls is None:
            raise DeviceNotFoundError(
                f"No Device implementation for wire={wire.value!r}"
            )
        return device_cls


# Side-effect imports: load each device module so its @register decorator
# fires and populates the registry above.  Anything that imports
# ``trcc.adapters.device`` triggers all five.
from . import bulk_lcd as _bulk_lcd  # noqa: E402, F401
from . import hid_lcd as _hid_lcd  # noqa: E402, F401
from . import led as _led  # noqa: E402, F401
from . import ly_lcd as _ly_lcd  # noqa: E402, F401
from . import scsi_lcd as _scsi_lcd  # noqa: E402, F401
