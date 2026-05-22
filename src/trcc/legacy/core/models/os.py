"""OS models — normalized data types returned by Platform."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UsbDeviceEntry:
    """Normalized USB device from OS enumeration.

    The OS discovers what's on the bus and returns these.
    Discovery matches VID/PID against the device registry.
    """
    vid: int
    pid: int
    path: str
    serial: str = ""


@dataclass(frozen=True, slots=True)
class HardwareComponent:
    """Normalized hardware component from OS discovery.

    The OS finds what hardware exists and returns these.
    Sensor service uses sensor_id to read values via get_metrics().
    """
    category: str               # "cpu", "gpu", "memory", "disk", "fan", "network"
    name: str                   # "Intel i7-13700K", "Samsung 990 Pro 2TB"
    sensor_id: str              # unique ID for get_metrics() readings
    attributes: dict = field(default_factory=dict)  # category-specific static data


__all__ = ['HardwareComponent', 'UsbDeviceEntry']
