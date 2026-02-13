"""Backward-compat — merged into device_led_hr10.py."""
from .device_led_hr10 import (  # noqa: F401
    SSD_PROFILES,
    breathe_brightness,
    celsius_to_f,
    find_nvme_hwmon,
    read_temp_celsius,
    select_profile,
    temp_to_color,
)
from .device_led_hr10 import (
    run_hr10_daemon as run_daemon,  # noqa: F401
)
