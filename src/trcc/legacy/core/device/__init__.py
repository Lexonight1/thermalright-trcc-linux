"""Device package — LCD and LED device facades.

Concrete classes live in their submodules; import them directly:

    from trcc.legacy.core.device.lcd import LCDDevice
    from trcc.legacy.core.device.led import LEDDevice

``Device`` is the shared ABC both classes implement — use it for
collections (`list[Device]`) and for parameters that accept either.
"""
from __future__ import annotations

from .base import Device
from .lcd import LCDDevice
from .led import LEDDevice

__all__ = ['Device', 'LCDDevice', 'LEDDevice']
