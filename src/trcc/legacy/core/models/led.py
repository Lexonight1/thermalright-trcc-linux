"""LED models — modes, states, device styles, PM registry, remap tables."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from .protocol import HandshakeResult

# =============================================================================
# LED Model (FormLED equivalent)
# =============================================================================

class LEDMode(Enum):
    """LED effect modes from FormLED.cs timer functions."""
    STATIC = 0       # DSCL_Timer: solid color
    BREATHING = 1    # DSHX_Timer: fade in/out, period=66 ticks
    COLORFUL = 2     # QCJB_Timer: 6-phase gradient, period=168 ticks
    RAINBOW = 3      # CHMS_Timer: 768-entry table shift
    TEMP_LINKED = 4  # WDLD_Timer: color from CPU/GPU temperature
    LOAD_LINKED = 5  # FZLD_Timer: color from CPU/GPU load %


@dataclass(slots=True)
class LEDZoneState:
    """Per-zone state for multi-zone LED devices.

    Multi-zone devices (styles 2,3,5,6,7,8,11) have 2-4 independent zones,
    each with its own mode, color, brightness, and on/off state.
    From FormLED.cs: myLedMode1-4, rgbR1_1-4, myBrightness1-4, myOnOff1-4.
    """
    mode: LEDMode = LEDMode.STATIC
    color: tuple[int, int, int] = (255, 0, 0)
    brightness: int = 65   # 0-100 (C# default: 65)
    on: bool = True


@dataclass(slots=True)
class LEDState:
    """Complete LED device state matching FormLED.cs globals.

    This is the serializable state that gets persisted and restored.
    Animation counters are transient (not saved).
    """
    # Device configuration (from handshake pm → LedDeviceStyle)
    style: int = 1              # nowLedStyle
    led_count: int = 30         # from LedDeviceStyle.led_count
    segment_count: int = 10     # from LedDeviceStyle.segment_count
    zone_count: int = 1         # from LedDeviceStyle.zone_count

    # Global state
    mode: LEDMode = LEDMode.STATIC    # myLedMode
    color: tuple[int, int, int] = (255, 0, 0)  # rgbR1, rgbG1, rgbB1
    brightness: int = 65        # myBrightness (0-100, C# default: 65)
    global_on: bool = True      # myOnOff

    # Per-segment on/off (ucScreenLED1.isOn[] per logical segment)
    segment_on: list[bool] = field(default_factory=list)

    # Multi-zone states (styles with zone_count > 1)
    zones: list[LEDZoneState] = field(default_factory=list)

    # Animation counters (transient, not persisted)
    rgb_timer: int = 0          # rgbTimer for breathing/gradient/rainbow
    test_mode: bool = False     # C# checkBox1 — diagnostic color cycle
    test_timer: int = 0         # testTimer — tick counter for test mode
    test_color: int = 0         # testCount — 0=white, 1=red, 2=green, 3=blue

    # Sensor linkage (for TEMP_LINKED and LOAD_LINKED modes)
    temp_source: str = "cpu"    # "cpu" or "gpu"
    load_source: str = "cpu"    # "cpu" or "gpu"

    # LC1 (style 4) sub-style: 0=memory, 1=hard disk
    sub_style: int = 0              # nowLedStyleSub
    ring_count: int = 0             # decoration ring LEDs (e.g. LF25 = 77)
    memory_ratio: int = 2           # DDR multiplier (1, 2, or 4) — C# default: 2

    # LF11 (style 10) disk selector (C# hardDiskCount, 0-based)
    disk_index: int = 0

    # Segment carousel interval (ticks per phase, ~30ms per tick → 100 = ~3s)
    carousel_interval: int = 100

    # Zone sync (C# isLunBo) — one checkbox, behavior depends on style:
    #   Styles 2/7: "Select all" — sync all zones to same mode/color/brightness
    #   Other styles: "Circulate" — timer-rotate through enabled zones
    selected_zone: int = 0               # Currently selected zone (UI)
    zone_sync: bool = False              # isLunBo: checkbox state
    zone_sync_zones: list[bool] = field(default_factory=list)  # LunBo1-4
    zone_sync_current: int = 0           # nowLunbo: current active zone
    zone_sync_ticks: int = 0             # ValCount: tick counter
    zone_sync_interval: int = 13         # round(2s * 1000 / 150ms tick)

    # LC2 clock settings (style 9)
    is_timer_24h: bool = True
    is_week_sunday: bool = False

    def __post_init__(self):
        if not self.segment_on:
            self.segment_on = [True] * self.segment_count
        if not self.zones and self.zone_count > 1:
            self.zones = [LEDZoneState() for _ in range(self.zone_count)]
        if not self.zone_sync_zones and self.zone_count > 1:
            self.zone_sync_zones = [True] + [False] * (self.zone_count - 1)


# =============================================================================
# LED Device Styles (from FormLED.cs FormLEDInit, lines 1598-1750)
# =============================================================================

@dataclass(frozen=True, slots=True)
class LedDeviceStyle:
    """LED device configuration derived from FormLEDInit pm→nowLedStyle.

    Attributes:
        style_id: Internal style number (nowLedStyle in Windows).
        led_count: Total addressable LEDs (LedCountValN).
        segment_count: Logical segments (LedCountValNs).
        zone_count: Number of independent zones (1=single, 2-4=multi).
        model_name: Human-readable model name.
        preview_image: Device background asset name.
        background_base: Localized background base.
    """
    style_id: int
    led_count: int
    segment_count: int
    zone_count: int = 1
    model_name: str = ""
    preview_image: str = ""
    background_base: str = "led_bg_segment"

    @property
    def zone_assets(self) -> list[tuple[str, str]]:
        """Zone button asset pairs for this style."""
        return _ZONE_STYLE_ASSETS.get(self.style_id, [])


class _LedStylesRegistry:
    """LED style registry with Pythonic access — never returns None.

    Usage::

        style = LED_STYLES[6]           # __getitem__, defaults to style 1
        if 6 in LED_STYLES: ...         # __contains__
        for sid, style in LED_STYLES:   # __iter__
            ...
        sid = LED_STYLES.by_name("LF12")  # reverse lookup
    """

    _DEFAULT = 1

    _REGISTRY: dict[int, LedDeviceStyle] = {
        1:  LedDeviceStyle(1, 30, 10, 4, "AX120_DIGITAL", "led_preview_ax120", "led_bg_segment"),
        2:  LedDeviceStyle(2, 84, 18, 4, "PA120_DIGITAL", "led_preview_pa120", "led_bg_segment_4zone"),
        3:  LedDeviceStyle(3, 64, 10, 2, "AK120_DIGITAL", "led_preview_ak120", "led_bg_segment"),
        4:  LedDeviceStyle(4, 31, 14, 3, "LC1", "led_preview_lc1", "led_bg_lc1"),
        5:  LedDeviceStyle(5, 93, 23, 2, "LF8", "led_preview_lf8", "led_bg_lf8"),
        6:  LedDeviceStyle(6, 124, 72, 2, "LF12", "led_preview_lf12", "led_bg_lf12"),
        7:  LedDeviceStyle(7, 116, 12, 3, "LF10", "led_preview_lf10", "led_bg_lf10"),
        8:  LedDeviceStyle(8, 18, 13, 4, "CZ1", "led_preview_cz1", "led_bg_cz1"),
        9:  LedDeviceStyle(9, 61, 31, 0, "LC2", "led_preview_lc2", "led_bg_lc2"),
        10: LedDeviceStyle(10, 38, 17, 4, "LF11", "led_preview_lf11", "led_bg_lf11"),
        11: LedDeviceStyle(11, 93, 72, 2, "LF15", "led_preview_lf15", "led_bg_lf15"),
        12: LedDeviceStyle(12, 62, 62, 0, "LF13", "led_preview_lf13", "led_bg_lf13"),
    }

    def __getitem__(self, style_id: int) -> LedDeviceStyle:
        return self._REGISTRY.get(style_id, self._REGISTRY[self._DEFAULT])

    def __len__(self) -> int:
        return len(self._REGISTRY)

    def __contains__(self, style_id: object) -> bool:
        return style_id in self._REGISTRY

    def __iter__(self):
        return iter(self._REGISTRY.items())

    def items(self):
        return self._REGISTRY.items()

    def keys(self):
        return self._REGISTRY.keys()

    def values(self):
        return self._REGISTRY.values()

    def get(self, style_id: int) -> LedDeviceStyle | None:
        """Explicit get for callers that need to distinguish unknown styles."""
        return self._REGISTRY.get(style_id)

    def by_name(self, model_name: str) -> int:
        """Resolve style_id from model name. Returns default (AX120) if unknown."""
        for sid, s in self._REGISTRY.items():
            if s.model_name == model_name:
                return sid
        return self._DEFAULT


LED_STYLES = _LedStylesRegistry()

# Style IDs that support "select all zones" — sync every zone to same
# mode/color/brightness in one operation (PA120=2, LF10=7).
LED_SELECT_ALL_STYLES: frozenset[int] = frozenset({2, 7})


@dataclass(slots=True)
class LedHandshakeInfo(HandshakeResult):
    """LED-specific handshake info (extends HandshakeResult).

    ``model_name`` is inherited from ``HandshakeResult`` — every protocol
    that produces it (Bulk parses ASCII bytes; LED resolves via PM registry)
    writes the same field.
    """
    pm: int = 0
    sub_type: int = 0
    style: LedDeviceStyle | None = None
    style_sub: int = 0  # C# nowLedStyleSub — wire remap variant


# =============================================================================
# PM Registry — PM byte → (style, model, button image)
# =============================================================================

class PmEntry(NamedTuple):
    """PM registry entry mapping a firmware PM byte to device metadata."""
    style_id: int
    model_name: str
    button_image: str
    preview_image: str = ""  # PM-specific preview; empty = use style default
    style_sub: int = 0       # C# nowLedStyleSub — variant within same style

    def __str__(self) -> str:
        return self.model_name


class _PmRegistryType:
    """PM byte → device metadata registry with Pythonic access.

    Maps firmware PM bytes (from HID handshake) to device style, model name,
    and button image.  Handles sub-type overrides and PA120 variant range
    (PMs 17-31).

    Usage::

        entry = PmRegistry[pm, sub]       # __getitem__
        if (pm, sub) in PmRegistry: ...   # __contains__
        for pm_val, entry in PmRegistry:  # __iter__
            ...
    """

    # (pm, sub_type) → PmEntry override for devices that share a PM byte.
    _OVERRIDES: dict[tuple[int, int], PmEntry] = {}

    # PM → PmEntry base registry (built once at class load time).
    _REGISTRY: dict[int, PmEntry] = {
        1:   PmEntry(1, "FROZEN_HORIZON_PRO", "A1FROZEN HORIZON PRO", "led_preview_frozen_horizon_pro"),
        2:   PmEntry(1, "FROZEN_MAGIC_PRO", "A1FROZEN MAGIC PRO", "led_preview_frozen_magic_pro"),
        3:   PmEntry(1, "AX120_DIGITAL", "A1AX120 DIGITAL"),
        16:  PmEntry(2, "PA120_DIGITAL", "A1PA120 DIGITAL"),
        23:  PmEntry(2, "RK120_DIGITAL", "A1RK120 DIGITAL"),
        32:  PmEntry(3, "AK120_DIGITAL", "A1AK120 Digital"),
        48:  PmEntry(5, "LF8", "A1LF8"),
        49:  PmEntry(5, "LF10", "A1LF10"),
        80:  PmEntry(6, "LF12", "A1LF12"),
        96:  PmEntry(7, "LF10", "A1LF10"),
        112: PmEntry(9, "LC2", "A1LC2"),
        128: PmEntry(4, "LC1", "A1LC1"),
        129: PmEntry(10, "LF11", "A1LF11", style_sub=1),
        144: PmEntry(11, "LF15", "A1LF15"),
        160: PmEntry(12, "LF13", "A1LF13"),
        176: PmEntry(5, "LF25", "A1LF25", style_sub=1),
        208: PmEntry(8, "CZ1", "A1CZ1"),
        # PA120 variants (PMs 17-22, 24-31) all map to style 2.
        **{pm: PmEntry(2, "PA120_DIGITAL", "A1PA120 DIGITAL")
           for pm in range(17, 32) if pm not in (23,)},
    }

    # PM → style_id convenience mapping (used by cli.py, debug_report.py).
    PM_TO_STYLE: dict[int, int] = {pm: e.style_id for pm, e in _REGISTRY.items()}

    def __getitem__(self, key: tuple[int, int]) -> PmEntry | None:
        pm, sub = key
        return self._OVERRIDES.get((pm, sub)) or self._REGISTRY.get(pm)

    def __contains__(self, key: object) -> bool:
        match key:
            case (int() as pm, int() as sub):
                return (pm, sub) in self._OVERRIDES or pm in self._REGISTRY
            case int() as pm:
                return pm in self._REGISTRY
            case _:
                return False

    def __iter__(self):
        return iter(self._REGISTRY.items())

    def resolve(self, pm: int, sub_type: int = 0) -> PmEntry | None:
        """Resolve PM + SUB to a PmEntry, checking overrides first."""
        return self[pm, sub_type]

    def get_model_name(self, pm: int, sub_type: int = 0) -> str:
        """Get human-readable model name for a PM + SUB byte combo."""
        entry = self[pm, sub_type]
        return str(entry) if entry else f"Unknown (pm={pm})"

    def get_style(self, pm: int, sub_type: int = 0) -> LedDeviceStyle:
        """Get LED device style from firmware PM byte."""
        entry = self[pm, sub_type]
        return LED_STYLES[entry.style_id if entry else 1]

    def get_preview_image(self, pm: int, sub_type: int = 0) -> str:
        """Get device preview image name, PM-specific or style default."""
        entry = self[pm, sub_type]
        if entry and entry.preview_image:
            return entry.preview_image
        style = self.get_style(pm, sub_type)
        return style.preview_image


PmRegistry = _PmRegistryType()


# Preset colors from FormLED.cs ucColor1_ChangeColor handlers
PRESET_COLORS: list[tuple[int, int, int]] = [
    (255, 0, 42),     # C1: Red-pink
    (255, 110, 0),    # C2: Orange
    (255, 255, 0),    # C3: Yellow
    (0, 255, 0),      # C4: Green
    (0, 255, 255),    # C5: Cyan
    (0, 91, 255),     # C6: Blue
    (214, 0, 255),    # C7: Purple
    (255, 255, 255),  # C8: White
]


# =============================================================================
# LED Index Remapping Tables (from FormLED.cs SendHidVal)
# =============================================================================

_REMAP_STYLE_2: tuple[int, ...] = (
    1, 0, 15, 10, 11, 16, 14, 13, 12,
    22, 17, 18, 23, 21, 20, 19,
    29, 24, 25, 30, 28, 27, 26,
    4, 5, 81, 80,
    36, 31, 32, 37, 35, 34, 33,
    43, 38, 39, 44, 42, 41, 40,
    6, 9,
    75, 76, 77, 79, 74, 73, 78,
    68, 69, 70, 72, 67, 66, 71,
    82, 83, 7, 8,
    61, 62, 63, 65, 60, 59, 64,
    54, 55, 56, 58, 53, 52, 57,
    47, 48, 49, 51, 46, 45, 50,
    2, 3,
)

_REMAP_STYLE_3: tuple[int, ...] = (
    1, 22, 23, 24, 26, 21, 20, 25,
    14, 0, 13, 18, 19, 15, 16, 17,
    7, 6, 11, 12, 8, 9, 10,
    32, 27, 28, 33, 31, 30, 29,
    39, 34, 35, 40, 38, 37, 36,
    46, 41, 42, 47, 45, 44, 43,
    2, 3, 4,
    57, 58, 59, 61, 56, 55, 60,
    50, 5, 51, 52, 54, 49, 48, 53,
    62, 63,
)

_REMAP_STYLE_4: tuple[int, ...] = (
    2, 1, 26, 27, 28, 30, 25, 24, 0,
    29, 19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_5: tuple[int, ...] = (
    6, 86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    11, 10, 9, 13, 12, 7, 0, 8,
    18, 17, 16, 20, 19, 14, 1, 15,
    25, 24, 23, 27, 26, 21, 22,
    3, 2,
    32, 31, 30, 34, 33, 28, 29,
    39, 38, 37, 41, 40, 35, 36,
    46, 45, 44, 48, 47, 42, 43,
    4, 92,
)

_REMAP_STYLE_5_SUB1: tuple[int, ...] = (
    4,
    43, 42, 47, 48, 44, 45, 46,
    36, 35, 40, 41, 37, 38, 39,
    29, 28, 33, 34, 30, 31, 32,
    3, 2,
    22, 21, 26, 27, 23, 24, 25,
    15, 1, 14, 19, 20, 16, 17, 18,
    8, 0, 7, 12, 13, 9, 10, 11,
    54, 49, 50, 55, 53, 52, 51,
    61, 56, 57, 62, 60, 59, 58,
    68, 63, 64, 69, 67, 66, 65,
    75, 70, 71, 76, 74, 73, 72,
    5, 92, 91,
    82, 77, 78, 83, 81, 80, 79,
    89, 84, 85, 90, 88, 87, 86,
    6,
    96, 95, 94, 93,
    169, 168, 167, 166, 165, 164, 163, 162, 161, 160,
    159, 158, 157, 156, 155, 154, 153, 152, 151, 150,
    149, 148, 147, 146, 145, 144, 143, 142, 141, 140,
    139, 138, 137, 136, 135, 134, 133, 132, 131, 130,
    129, 128, 127, 126, 125, 124, 123, 122, 121, 120,
    119, 118, 117, 116, 115, 114, 113, 112, 111, 110,
    109, 108, 107, 106, 105, 104, 103, 102, 101, 100,
    99, 98, 97,
)

_REMAP_STYLE_6: tuple[int, ...] = (
    119, 120, 121, 122, 123,
    122, 121, 120, 119,
    6, 6,
    86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    92, 91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
    4,
    44, 45, 46, 48, 43, 42, 47,
    37, 38, 39, 41, 36, 35, 40,
    30, 31, 32, 34, 29, 28, 33,
    2, 3,
    23, 24, 25, 27, 22, 21, 1, 26,
    16, 17, 18, 20, 15, 14, 19,
    9, 10, 11, 13, 8, 7, 12,
    0,
    93, 93, 94, 94, 95, 95, 96, 96, 97, 97, 98, 98,
    99, 99, 100, 100, 101, 101, 102, 102, 103, 103, 104, 104,
)

_REMAP_STYLE_7: tuple[int, ...] = (
    115, 114, 113, 112, 111, 110,
    110, 111, 112, 113, 114, 115,
    103, 102, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85, 84,
    104, 105, 106, 107, 108, 109,
    0, 0,
    17, 6, 7, 7, 8, 9, 10, 18, 18, 16, 15, 14, 13, 13, 12, 11,
    28, 27, 26, 26, 25, 24, 23, 31, 31, 29, 30, 19, 20, 20, 21, 22,
    43, 32, 33, 33, 34, 35, 36, 44, 44, 42, 41, 40, 39, 39, 38, 37,
    2, 2, 1, 1, 3, 3,
    56, 45, 46, 46, 47, 48, 49, 57, 57, 55, 54, 53, 52, 52, 51, 50,
    67, 66, 65, 65, 64, 63, 62, 70, 70, 68, 69, 58, 59, 59, 60, 61,
    82, 71, 72, 72, 73, 74, 75, 83, 83, 81, 80, 79, 78, 78, 77, 76,
    5, 5, 4, 4,
)

_REMAP_STYLE_9: tuple[int, ...] = (
    60, 59, 58, 57, 56, 55, 54,
    53, 52,
    36, 31, 32, 37, 35, 34, 33,
    2, 2, 2,
    43, 38, 39, 44, 42, 41, 40,
    50, 45, 46, 51, 49, 48, 47,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    0, 1,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_10: tuple[int, ...] = (
    2, 1,
    33, 34, 35, 37, 32, 31, 0, 36,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

LED_REMAP_TABLES: dict[int, tuple[int, ...]] = {
    2: _REMAP_STYLE_2,
    3: _REMAP_STYLE_3,
    4: _REMAP_STYLE_4,
    5: _REMAP_STYLE_5,
    6: _REMAP_STYLE_6,
    7: _REMAP_STYLE_7,
    9: _REMAP_STYLE_9,
    10: _REMAP_STYLE_10,
}

LED_REMAP_SUB_TABLES: dict[tuple[int, int], tuple[int, ...]] = {
    (5, 1): _REMAP_STYLE_5_SUB1,
}

LED_DEFAULT_OFF: dict[int, frozenset[int]] = {
    1: frozenset({4, 5, 7, 8}),
    2: frozenset({7}),
    3: frozenset({3, 5}),
    4: frozenset({1, 2, 25, 26, 30}),
    5: frozenset({1, 3}),
    6: frozenset({1, 3}),
    7: frozenset({2, 5}),
    8: frozenset({1, 2, 3}),
    10: frozenset({1, 2, 32, 33, 37}),
    11: frozenset({1, 3}),
}


def remap_led_colors(
    colors: list[tuple[int, int, int]],
    style_id: int,
    style_sub: int = 0,
) -> list[tuple[int, int, int]]:
    """Remap LED colors from logical to physical wire order."""
    table = LED_REMAP_SUB_TABLES.get((style_id, style_sub))
    if table is None:
        table = LED_REMAP_TABLES.get(style_id)
    if table is None:
        return colors
    black = (0, 0, 0)
    return [colors[idx] if idx < len(colors) else black for idx in table]


# =============================================================================
# LED asset mappings
# =============================================================================

LED_PRESET_ASSETS: list[str] = [
    'led_preset_red', 'led_preset_orange', 'led_preset_yellow',
    'led_preset_green', 'led_preset_cyan', 'led_preset_blue',
    'led_preset_purple', 'led_preset_white',
]

LED_MODE_LABELS: list[str] = [
    "Solid", "Breathe", "Color Cycle", "Rainbow", "Temp Linked", "Load Linked",
]

_ZONE_ASSETS_BTN14 = [
    ('led_zone_mode_1', 'led_zone_mode_1_active'),
    ('led_zone_mode_2', 'led_zone_mode_2_active'),
    ('led_zone_mode_3', 'led_zone_mode_3_active'),
    ('led_zone_mode_4', 'led_zone_mode_4_active'),
]
_ZONE_ASSETS_BTN56 = [
    ('led_zone_mode_5', 'led_zone_mode_5_active'),
    ('led_zone_mode_6', 'led_zone_mode_6_active'),
]
_ZONE_ASSETS_BTNN = [
    ('led_zone_btn_1', 'led_zone_btn_1_active'),
    ('led_zone_btn_2', 'led_zone_btn_2_active'),
    ('led_zone_btn_3', 'led_zone_btn_3_active'),
    ('led_zone_btn_4', 'led_zone_btn_4_active'),
]
_ZONE_STYLE_ASSETS: dict[int, list[tuple[str, str]]] = {
    1: _ZONE_ASSETS_BTN14, 2: _ZONE_ASSETS_BTN14,
    3: _ZONE_ASSETS_BTN56, 5: _ZONE_ASSETS_BTN56,
    6: _ZONE_ASSETS_BTN56, 11: _ZONE_ASSETS_BTN56,
    4: _ZONE_ASSETS_BTNN, 7: _ZONE_ASSETS_BTNN,
    8: _ZONE_ASSETS_BTNN, 10: _ZONE_ASSETS_BTNN,
}


__all__ = [
    'LED_DEFAULT_OFF',
    'LED_MODE_LABELS',
    'LED_PRESET_ASSETS',
    'LED_REMAP_SUB_TABLES',
    'LED_REMAP_TABLES',
    'LED_SELECT_ALL_STYLES',
    'LED_STYLES',
    'PRESET_COLORS',
    'LEDMode',
    'LEDState',
    'LEDZoneState',
    'LedDeviceStyle',
    'LedHandshakeInfo',
    'PmEntry',
    'PmRegistry',
    'remap_led_colors',
]
