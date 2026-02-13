"""Backward-compat — merged into device_led_hr10.py."""
from .device_led_hr10 import (  # noqa: F401
    CHAR_SEGMENTS,
    DIGIT_LEDS,
    IND_DEG,
    IND_MBS,
    IND_PCT,
    LED_COUNT,
    WIRE_ORDER,
    Hr10Display,
    apply_animation_colors,
    get_digit_mask,
    render_display,
    render_metric,
)
