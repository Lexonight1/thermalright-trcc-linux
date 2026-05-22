# Re-export stub — all diagnostic code lives in diagnostics.py
from trcc.legacy.adapters.infra.diagnostics import (
    _debug_hid_lcd_interactive as _hid_debug_lcd,
)
from trcc.legacy.adapters.infra.diagnostics import (
    _debug_hid_led_interactive as _hid_debug_led,
)
from trcc.legacy.adapters.infra.diagnostics import (
    _hex_dump,
    device_debug,
    led_debug_interactive,
)

__all__ = [
    "_hex_dump", "_hid_debug_lcd", "_hid_debug_led",
    "device_debug", "led_debug_interactive",
]
