"""LED domain models — mode enum, per-device state, preset palette.

Data only.  No I/O, no protocol bytes, no Qt.  Lives in ``core`` so the
effects engine and the LedDeviceSettings persistence layer share one
source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from .models import LedStyle

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

    # DDR memory multiplier (1 / 2 / 4) — scales the memory value shown on
    # the LED gauge (C# memoryRatio).  The segment renderer multiplies the
    # raw reading by this (``value *= memory_ratio``); the GUI exposes it
    # as a DDR selector.  Default 2 (DDR effective rate).
    memory_ratio: int = 2


# =========================================================================
# Transient runtime counters — NOT persisted
# =========================================================================


@dataclass(slots=True)
class LedRuntimeState:
    """Tick counters that don't survive a process restart.

    Lives on the App as ``app.led_runtime[key]`` so the effects engine
    can advance its phase between ``RenderLed`` dispatches.
    """
    # One phase per animated effect, NOT one shared counter.
    #
    # The C# keeps three (`rgbTimer` strip, `rgbTimer1` ring breathe,
    # `rgbTimer2` ring rainbow) and advances each ONCE per tick in
    # `MyTimer_Event` (FormLED.cs:4136), because a style-2/7 device has FOUR
    # independent per-zone modes (`myLedMode1..4`, :889-895) all reading the
    # same precomputed planes.  One counter shared across zones cannot express
    # that: with N animated zones it advanced N times per tick (animation ran
    # N-times fast), zones sharing a mode came out one step apart, and zones in
    # DIFFERENT modes corrupted each other outright -- a `% 66` breathe write
    # read back as a `% 768` rainbow phase.  11 of our 13 styles are
    # multi-zone, so that was not a corner case.
    #
    # Advancing is `LEDEffectEngine.advance_phases`, which owns the periods.
    breathe_phase: int = 0       # 0 .. _BREATHING_PERIOD
    colorful_phase: int = 0      # 0 .. _COLORFUL_PERIOD
    rainbow_phase: int = 0       # 0 .. len(RGBTable), stepped by _RAINBOW_STEP
    test_timer: int = 0          # ticks until next test-color rotation
    test_color: int = 0          # 0=white 1=red 2=green 3=blue
    zone_sync_ticks: int = 0     # ticks since last zone rotation
    zone_sync_current: int = 0   # currently active zone in the carousel
    last_temp_color: tuple[int, int, int] = (0, 0, 0)
    last_load_color: tuple[int, int, int] = (0, 0, 0)


# =========================================================================
# LedPayload — the send() shape
# =========================================================================
#
# The structured payload ``Led.send()`` consumes.  A pure DTO (no I/O,
# no protocol bytes), so it lives in core: Commands construct it, the
# LED adapter consumes it, and neither side imports the other.


@dataclass(frozen=True, slots=True)
class LedPayload:
    """Structured payload for ``Led.send()``.

    colors:     per-LED RGB tuples (0-255), already brightness-baked by
                the service layer (``apply_brightness``).  The wire is pure
                transport — it applies only the FormLED 0.4x hardware scale
                + the on/off mask, never brightness.
    is_on:      per-LED boolean mask; ``None`` means all on.
    global_on:  master switch; ``False`` turns every LED off.
    """
    colors: list[tuple[int, int, int]]
    is_on: list[bool] | None = None
    global_on: bool = True


# =========================================================================
# Style spec — per-LED-style layout metadata
# =========================================================================
#
# Counts + asset basenames so the legacy-style ``UCLedControl`` panel and
# ``UCScreenLED`` preview can lay themselves out without consulting a
# device instance.  Mirrors legacy ``LedDeviceStyle`` fields except the
# style key is the :class:`LedStyle` enum instead of an integer id.


@dataclass(frozen=True, slots=True)
class LedStyleSpec:
    """LED-strip layout metadata + asset basenames for one style.

    Used by the legacy-style RGB-control panel:
      * ``led_count`` / ``segment_count`` — sizing the preview + masks
      * ``zone_count`` — how many zone buttons to show (1 = single-zone)
      * ``model_name`` — title-bar string ("AX120_DIGITAL")
      * ``preview_image`` — device-image asset for the preview pane
      * ``background_base`` — localised panel background asset
      * ``zone_assets`` — per-zone (normal, active) asset basenames;
        legacy GUI builds the zone buttons from this tuple.  Empty
        means "use the panel's flat fallback styling".
    """
    led_count: int
    segment_count: int
    zone_count: int
    model_name: str
    preview_image: str
    background_base: str = "led_bg_segment"
    zone_assets: tuple[tuple[str, str], ...] = ()


# Legacy integer style_id (1..12) for each LedStyle — kept because the
# segment-position arrays inside :mod:`next.ui.gui.uc_screen_led` are
# byte-for-byte ports of the legacy ``UCScreenLED.cs`` data, which was
# keyed by these ints.  The widget consumes the int; everything else
# uses :class:`LedStyle`.
LEGACY_STYLE_ID: dict[LedStyle, int] = {
    LedStyle.AX120: 1,
    LedStyle.PA120: 2,
    LedStyle.AK120: 3,
    LedStyle.LC1:   4,
    LedStyle.LF8:   5,
    LedStyle.LF12:  6,
    LedStyle.LF10:  7,
    LedStyle.CZ1:   8,
    LedStyle.LC2:   9,
    LedStyle.LF11:  10,
    LedStyle.LF15:  11,
    LedStyle.LF13:  12,
    # Synthetic id for the Linux-added Magic Qube (no legacy C# style_id);
    # continues the sequence so every LED_STYLES entry has an id.  It shows
    # plain sensor gauges (led_panel_for treats any non-LC1/LF11/LC2 id so).
    LedStyle.MAGIC_QUBE: 13,
}

# Inverse lookup — legacy int → :class:`LedStyle` enum.  Used by GUI
# widgets that still index data by the legacy int and need to recover
# the enum to look up the matching :class:`LedStyleSpec`.
STYLE_BY_LEGACY_ID: dict[int, LedStyle] = {
    v: k for k, v in LEGACY_STYLE_ID.items()
}


# Zone-button image pairs (normal, active) per C# button group.  The cutover
# dropped this mapping — legacy held it in a computed ``zone_assets`` property
# (``_ZONE_STYLE_ASSETS``); here it lives as data on the spec.  Without it
# ``uc_led_control._apply_zone_images`` early-returns and the zone buttons
# render blank ("buttons don't show below it").  Names verified on disk.
_ZONE_BTN_MODE14: tuple[tuple[str, str], ...] = (
    ("led_zone_mode_1", "led_zone_mode_1_active"),
    ("led_zone_mode_2", "led_zone_mode_2_active"),
    ("led_zone_mode_3", "led_zone_mode_3_active"),
    ("led_zone_mode_4", "led_zone_mode_4_active"),
)
_ZONE_BTN_MODE56: tuple[tuple[str, str], ...] = (
    ("led_zone_mode_5", "led_zone_mode_5_active"),
    ("led_zone_mode_6", "led_zone_mode_6_active"),
)
_ZONE_BTN_N: tuple[tuple[str, str], ...] = (
    ("led_zone_btn_1", "led_zone_btn_1_active"),
    ("led_zone_btn_2", "led_zone_btn_2_active"),
    ("led_zone_btn_3", "led_zone_btn_3_active"),
    ("led_zone_btn_4", "led_zone_btn_4_active"),
)


LED_STYLES: dict[LedStyle, LedStyleSpec] = {
    LedStyle.AX120: LedStyleSpec(30,  10, 4, "AX120_DIGITAL",
                                 "led_preview_ax120", "led_bg_segment",
                                 zone_assets=_ZONE_BTN_MODE14),
    LedStyle.PA120: LedStyleSpec(84,  18, 4, "PA120_DIGITAL",
                                 "led_preview_pa120", "led_bg_segment_4zone",
                                 zone_assets=_ZONE_BTN_MODE14),
    LedStyle.AK120: LedStyleSpec(64,  10, 2, "AK120_DIGITAL",
                                 "led_preview_ak120", "led_bg_segment",
                                 zone_assets=_ZONE_BTN_MODE56),
    LedStyle.LC1:   LedStyleSpec(31,  14, 3, "LC1",
                                 "led_preview_lc1",   "led_bg_lc1",
                                 zone_assets=_ZONE_BTN_N),
    LedStyle.LF8:   LedStyleSpec(93,  23, 2, "LF8",
                                 "led_preview_lf8",   "led_bg_lf8",
                                 zone_assets=_ZONE_BTN_MODE56),
    LedStyle.LF12:  LedStyleSpec(124, 72, 2, "LF12",
                                 "led_preview_lf12",  "led_bg_lf12",
                                 zone_assets=_ZONE_BTN_MODE56),
    LedStyle.LF10:  LedStyleSpec(116, 12, 3, "LF10",
                                 "led_preview_lf10",  "led_bg_lf10",
                                 zone_assets=_ZONE_BTN_N),
    LedStyle.CZ1:   LedStyleSpec(18,  13, 4, "CZ1",
                                 "led_preview_cz1",   "led_bg_cz1",
                                 zone_assets=_ZONE_BTN_N),
    LedStyle.LC2:   LedStyleSpec(61,  31, 0, "LC2",
                                 "led_preview_lc2",   "led_bg_lc2"),
    LedStyle.LF11:  LedStyleSpec(38,  17, 4, "LF11",
                                 "led_preview_lf11",  "led_bg_lf11",
                                 zone_assets=_ZONE_BTN_N),
    LedStyle.LF15:  LedStyleSpec(93,  72, 2, "LF15",
                                 "led_preview_lf15",  "led_bg_lf15",
                                 zone_assets=_ZONE_BTN_MODE56),
    LedStyle.LF13:  LedStyleSpec(62,  62, 0, "LF13",
                                 "led_preview_lf13",  "led_bg_lf13"),
    # Magic Qube — 65 LEDs: two 7-seg digits (3 LEDs/segment, LED 0-41) + 4
    # corner metric indicators (42-49) + a 15-LED contour border (50-64).
    # Same 2-digit / 4-phase design as CZ1.  Reuses the generic segment assets
    # until dedicated art is added.
    LedStyle.MAGIC_QUBE: LedStyleSpec(65, 14, 4, "MAGIC_QUBE",
                                      "led_preview_cz1", "led_bg_segment",
                                      zone_assets=_ZONE_BTN_N),
}


# Per-style zone-button asset pairs (normal, active).  Styles map onto
# three asset families (button1-4 / button5-6 / buttonN1-4) — see legacy
# ``_ZONE_ASSETS_BTN14`` etc.
_ZONE_ASSETS_BTN14: tuple[tuple[str, str], ...] = (
    ("led_zone_mode_1", "led_zone_mode_1_active"),
    ("led_zone_mode_2", "led_zone_mode_2_active"),
    ("led_zone_mode_3", "led_zone_mode_3_active"),
    ("led_zone_mode_4", "led_zone_mode_4_active"),
)
_ZONE_ASSETS_BTN56: tuple[tuple[str, str], ...] = (
    ("led_zone_mode_5", "led_zone_mode_5_active"),
    ("led_zone_mode_6", "led_zone_mode_6_active"),
)
_ZONE_ASSETS_BTNN: tuple[tuple[str, str], ...] = (
    ("led_zone_btn_1", "led_zone_btn_1_active"),
    ("led_zone_btn_2", "led_zone_btn_2_active"),
    ("led_zone_btn_3", "led_zone_btn_3_active"),
    ("led_zone_btn_4", "led_zone_btn_4_active"),
)

ZONE_STYLE_ASSETS: dict[LedStyle, tuple[tuple[str, str], ...]] = {
    LedStyle.AX120: _ZONE_ASSETS_BTN14,
    LedStyle.PA120: _ZONE_ASSETS_BTN14,
    LedStyle.AK120: _ZONE_ASSETS_BTN56,
    LedStyle.LF8:   _ZONE_ASSETS_BTN56,
    LedStyle.LF12:  _ZONE_ASSETS_BTN56,
    LedStyle.LF15:  _ZONE_ASSETS_BTN56,
    LedStyle.LC1:   _ZONE_ASSETS_BTNN,
    LedStyle.LF10:  _ZONE_ASSETS_BTNN,
    LedStyle.CZ1:   _ZONE_ASSETS_BTNN,
    LedStyle.LF11:  _ZONE_ASSETS_BTNN,
    LedStyle.MAGIC_QUBE: _ZONE_ASSETS_BTNN,
}


# =========================================================================
# Legacy GUI-only LED tables (consumed by uc_led_control + uc_screen_led)
# =========================================================================

# Style IDs that support "select all zones" — sync every zone to the
# same mode/color/brightness in one operation.  PA120 (style 2) and
# LF10 (style 7).
LED_SELECT_ALL_STYLES: frozenset[int] = frozenset({2, 7})

# Preset color buttons in the LED panel (legacy C# FormLED ucColor1
# preset bar).  Eight fixed positions; the panel renders these as
# square swatches users click to apply.
PRESET_COLORS: list[tuple[int, int, int]] = [
    (255, 0, 42),     # C1 — red/pink
    (255, 110, 0),    # C2 — orange
    (255, 255, 0),    # C3 — yellow
    (0, 255, 0),      # C4 — green
    (0, 255, 255),    # C5 — cyan
    (0, 91, 255),     # C6 — blue
    (214, 0, 255),    # C7 — purple
    (255, 255, 255),  # C8 — white
]

# Asset basenames for the eight preset buttons (legacy resx names).
LED_PRESET_ASSETS: list[str] = [
    'led_preset_red', 'led_preset_orange', 'led_preset_yellow',
    'led_preset_green', 'led_preset_cyan', 'led_preset_blue',
    'led_preset_purple', 'led_preset_white',
]

# Six animation-mode buttons; the LED panel renders these as a row.
LED_MODE_LABELS: list[str] = [
    "Solid", "Breathe", "Color Cycle", "Rainbow",
    "Temp Linked", "Load Linked",
]

# Segments that are OFF by default per legacy style.  Keys are the
# int style id (1-12, see LEGACY_STYLE_ID); values are 0-based segment
# indices the panel should render dim until the user clicks them on.
LED_DEFAULT_OFF: dict[int, frozenset[int]] = {
    1:  frozenset({4, 5, 7, 8}),
    2:  frozenset({7}),
    3:  frozenset({3, 5}),
    4:  frozenset({1, 2, 25, 26, 30}),
    5:  frozenset({1, 3}),
    6:  frozenset({1, 3}),
    7:  frozenset({2, 5}),
    8:  frozenset({1, 2, 3}),
    10: frozenset({1, 2, 32, 33, 37}),
    11: frozenset({1, 3}),
}


# =========================================================================
# PmRegistry — legacy PM-byte → display-model lookup
# =========================================================================
# next/'s ProductInfo carries the resolved model name directly; the
# legacy GUI's UCLedControl reads ``PmRegistry._REGISTRY`` to swap
# preview images at handshake time.  We expose a minimal compatible
# shape (a class with a ``_REGISTRY`` dict) so the widget keeps working
# without rewiring.  Empty by default — populated lazily if/when next/'s
# registry exposes PM mappings.


@dataclass(frozen=True, slots=True)
class _PmRegistryEntry:
    """One PM-byte → model entry (legacy GUI shape)."""
    model_name: str = ""
    preview_image: str = ""


class _PmRegistryShim:
    """Legacy-compatible registry stub.

    next/ resolves model identity via ``ProductInfo`` at handshake.  This
    shim exists only so legacy widgets that iterate ``PmRegistry._REGISTRY``
    keep working with the existing iteration shape.
    """

    _REGISTRY: dict[int, _PmRegistryEntry] = {}


PmRegistry = _PmRegistryShim()
