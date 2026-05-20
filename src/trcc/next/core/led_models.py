"""LED domain models — mode enum, per-device state, preset palette.

Data only.  No I/O, no protocol bytes, no Qt.  Lives in ``core`` so the
effects engine and the LedDeviceSettings persistence layer share one
source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

# =========================================================================
# LEDMode — the six color-cycling effects
# =========================================================================


class LEDMode(IntEnum):
    """Per-segment color computation modes (matches FormLED.cs timers)."""
    STATIC = 0       # solid color (DSCL_Timer)
    BREATHING = 1    # pulse brightness, 66-tick period (DSHX_Timer)
    COLORFUL = 2     # 6-phase gradient cycle per segment (QCJB_Timer)
    RAINBOW = 3      # 768-entry table shift across segments (CHMS_Timer)
    TEMP_LINKED = 4  # color from CPU/GPU temp gradient (WDLD_Timer)
    LOAD_LINKED = 5  # color from CPU/GPU load gradient (FZLD_Timer)


# =========================================================================
# Per-zone state — multi-zone LED devices (PA120 / LF8 / …)
# =========================================================================


@dataclass(slots=True)
class LedZoneSettings:
    """One zone's persisted preferences.

    Multi-zone devices (PA120, LF8, AK120, LF10, LF12, LF15, CZ1, LF11)
    let each zone run an independent mode + color + brightness.
    """
    mode: LEDMode = LEDMode.STATIC
    color: tuple[int, int, int] = (255, 0, 0)
    brightness: int = 65          # 0-100
    on: bool = True


# =========================================================================
# Per-device LED settings — persisted to config.json under "led_devices"
# =========================================================================


SensorLink = Literal["cpu", "gpu"]


@dataclass
class LedDeviceSettings:
    """Persisted LED-device preferences (parallel to ``DeviceSettings``).

    Mutable (not frozen) so ``Settings.set_led_*`` can update fields in
    place, then atomic-save.  Loaded back from JSON via
    ``_led_settings_from_dict``.
    """
    # Global state (used by single-zone devices + as zone-sync defaults)
    mode: LEDMode = LEDMode.STATIC
    color: tuple[int, int, int] = (255, 0, 0)
    brightness: int = 65
    global_on: bool = True

    # Multi-zone state (only populated for zone_count > 1)
    zones: list[LedZoneSettings] = field(default_factory=list)

    # Zone-sync carousel (rotate the lit zone every N ticks)
    zone_sync: bool = False
    zone_sync_zones: list[bool] = field(default_factory=list)
    zone_sync_interval_ticks: int = 13     # ≈ 2 s at default ticker cadence
    selected_zone: int = 0

    # Test mode — cycle through 4 reference colors
    test_mode: bool = False

    # Sensor linkage (TEMP_LINKED / LOAD_LINKED choose source per global / zone)
    temp_source: SensorLink = "cpu"
    load_source: SensorLink = "cpu"

    # Per-segment on/off — driven by the segment display (gauges) when
    # the device has one; empty list = always-on.
    segment_on: list[bool] = field(default_factory=list)

    # LC2 clock display options (style 9)
    clock_24h: bool = True
    week_sunday: bool = False


# =========================================================================
# Transient runtime counters — NOT persisted
# =========================================================================


@dataclass(slots=True)
class LedRuntimeState:
    """Tick counters that don't survive a process restart.

    Lives on the App as ``app.led_runtime[key]`` so the effects engine
    can advance its phase between ``RenderLed`` dispatches.
    """
    rgb_timer: int = 0           # shared by BREATHING / COLORFUL / RAINBOW
    test_timer: int = 0          # ticks until next test-color rotation
    test_color: int = 0          # 0=white 1=red 2=green 3=blue
    zone_sync_ticks: int = 0     # ticks since last zone rotation
    zone_sync_current: int = 0   # currently active zone in the carousel
    last_temp_color: tuple[int, int, int] = (0, 0, 0)
    last_load_color: tuple[int, int, int] = (0, 0, 0)


# =========================================================================
# Preset palette — picker buttons in legacy FormLED ucColor1
# =========================================================================


PRESET_COLORS: tuple[tuple[int, int, int], ...] = (
    (255,   0,  42),  # C1 — red-pink
    (255, 110,   0),  # C2 — orange
    (255, 255,   0),  # C3 — yellow
    (  0, 255,   0),  # C4 — green
    (  0, 255, 255),  # C5 — cyan
    (  0,  91, 255),  # C6 — blue
    (214,   0, 255),  # C7 — purple
    (255, 255, 255),  # C8 — white
)
