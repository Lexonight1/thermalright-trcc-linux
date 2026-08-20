"""Domain models — frozen dataclasses + enums.  No logic, no I/O."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

# =========================================================================
# Wire protocols and device kinds
# =========================================================================


class Wire(str, Enum):
    """The wire protocol a device speaks over USB."""
    SCSI = "scsi"
    HID = "hid"
    BULK = "bulk"
    LY = "ly"
    LED = "led"


# Whether a panel's firmware latches a frame is a property of the PANEL, so it
# is declared per device on ``ProductInfo.volatile_frames`` — not here.
#
# It lived here as a ``VOLATILE_FRAME_WIRES`` set keyed on ``Wire`` until
# 2026-08-19.  That read as though the wire caused the behaviour, and it made
# the wire load-bearing for something it does not describe: two devices sharing
# a protocol could not share a class if their firmware differed, and moving a
# device between wires silently changed whether we kept its screen alive.
# ``Device.needs_keepalive`` already OR-ed a per-firmware quirk alongside it,
# so half the fact was per-device already.


class Kind(str, Enum):
    """High-level device kind visible to UIs."""
    LCD = "lcd"
    LED = "led"


class Capability(str, Enum):
    """What a device can do — the data universal Commands gate on instead
    of ``isinstance(device, SpecificClass)``.

    A device's capability set is derived from its ``Kind`` (see
    ``CAPABILITIES_BY_KIND``).  Commands check membership, so the same
    ``SetBrightness`` / ``SetColor`` / etc. work on any device that
    declares the capability — and adding a new device means adding a
    registry row with the right ``Kind``, not new Command code.
    """
    FRAME_RENDER = "frame_render"   # composite + send a full image frame
    COLOR_FILL = "color_fill"       # set a solid color (LCD test / LED strip)
    BRIGHTNESS = "brightness"       # adjust output brightness
    OVERLAY = "overlay"             # render sensor/clock overlay elements
    MASK = "mask"                   # apply a foreground mask
    ORIENTATION = "orientation"     # rotate the displayed image
    EFFECTS = "effects"             # animated LED modes (breathe, rainbow…)
    LED_ZONES = "led_zones"         # per-zone LED color / brightness


_LCD_CAPABILITIES = frozenset({
    Capability.FRAME_RENDER,
    Capability.COLOR_FILL,
    Capability.BRIGHTNESS,
    Capability.OVERLAY,
    Capability.MASK,
    Capability.ORIENTATION,
})

_LED_CAPABILITIES = frozenset({
    Capability.COLOR_FILL,
    Capability.BRIGHTNESS,
    Capability.EFFECTS,
    Capability.LED_ZONES,
})

CAPABILITIES_BY_KIND: dict[Kind, frozenset[Capability]] = {
    Kind.LCD: _LCD_CAPABILITIES,
    Kind.LED: _LED_CAPABILITIES,
}


Orientation = Literal[0, 90, 180, 270]
TempUnit = Literal["C", "F"]


# Data-refresh-rate bounds — the GUI's "data refresh rate" control runs
# 1–100 s.  The refresh interval governs how often metric data is polled +
# updated; the render reads the cached snapshot between polls.  One source
# of truth for the floor/ceiling shared by Settings, MetricsLoop, and the
# sensor poll thread, so the poll can never run faster than the GUI minimum.
MIN_REFRESH_INTERVAL_S: float = 1.0
MAX_REFRESH_INTERVAL_S: float = 100.0


def parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse a ``"320x320"`` resolution string into ``(width, height)``.

    Case-insensitive on the ``x`` separator.  Raises ``ValueError`` on
    malformed input — callers translate to their own error shape (the API
    to a 400, the GUI to a status message).
    """
    try:
        w_str, h_str = resolution.lower().split("x", 1)
        return int(w_str), int(h_str)
    except (ValueError, AttributeError) as e:
        raise ValueError(
            f"bad resolution {resolution!r} — expected WxH (e.g. 320x320)",
        ) from e


def oriented_resolution(
    native: tuple[int, int], orientation: int,
) -> tuple[int, int]:
    """The native resolution adjusted for a user orientation.

    Portrait orientations (90 / 270) swap width and height; landscape ones
    (0 / 180) leave them as-is.  This is the SINGLE source of the swap rule the
    whole app keys orientation on — render canvas geometry, the per-orientation
    cloud/theme/mask catalog dirs (``Web\\854480`` ↔ ``Web\\480854``, the C#
    ``GetWebBackgroundImageDirectory`` direction split), and the preview bezel.
    """
    w, h = native
    return (h, w) if orientation in (90, 270) else (w, h)


class FitMode(str, Enum):
    """How a background image/video fits the device canvas.

    WIDTH   — scale to fill width, letterbox top/bottom  (C# buttonTPJCW)
    HEIGHT  — scale to fill height, pillarbox left/right (C# buttonTPJCH)
    STRETCH — fill both axes, distorting aspect
    """
    WIDTH = "width"
    HEIGHT = "height"
    STRETCH = "stretch"


# =========================================================================
# Split-mode overlay (Dynamic Island / 灵动岛) for 1600x720 widescreen
# =========================================================================
# Mirrors legacy ``core/models/protocol.py:SPLIT_OVERLAY_MAP`` exactly —
# (style, rotation_degrees) → asset filename under
# ``src/trcc/ui/gui/assets/``.  C# UCScreenImage.cs cycles
# ``buttonLDD myLddVal`` 1→2→3→1 and picks the asset by (style,
# directionB).  Only fires when the LCD's resolution is the widescreen
# 1600x720 — DisplayService gates on that.

SPLIT_OVERLAY_MAP: dict[tuple[int, int], str] = {
    # Style A (myLddVal=1)
    (1, 0):   "split_overlay_a.png",
    (1, 90):  "split_overlay_a_90.png",
    (1, 180): "split_overlay_a_180.png",
    (1, 270): "split_overlay_a_270.png",
    # Style B (myLddVal=2, the default Levita ships with)
    (2, 0):   "split_overlay_b.png",
    (2, 90):  "split_overlay_b_90.png",
    (2, 180): "split_overlay_b_180.png",
    (2, 270): "split_overlay_b_270.png",
    # Style C (myLddVal=3)
    (3, 0):   "split_overlay_c.png",
    (3, 90):  "split_overlay_c_90.png",
    (3, 180): "split_overlay_c_180.png",
    (3, 270): "split_overlay_c_270.png",
}


# =========================================================================
# Image-crop pan sensitivity
# =========================================================================
# How far one drag pixel in a crop editor moves the source image, per target
# canvas resolution — the editor previews at a fixed size, so a bigger canvas
# needs a bigger step to stay responsive.  Shared by both crop editors
# (``ui/gui/uc_image_cut.py``, ``ui/qtgui/image_crop.py``); a resolution the
# table doesn't name pans 1:1.

PAN_MULTIPLIERS: dict[tuple[int, int], int] = {
    (240, 240): 1, (320, 320): 1, (360, 360): 1,
    (480, 480): 2, (640, 480): 2, (800, 480): 3,
    (854, 480): 3, (960, 540): 3, (1280, 480): 4,
    (1600, 720): 4, (1920, 462): 4,
}


# =========================================================================
# LED styles / segment displays (categorical, enumerable)
# =========================================================================


class LedStyle(str, Enum):
    """LED strip layout style (affects color remap + segment mask).

    Names mirror legacy ``LED_STYLES`` model_name field. Order matches
    legacy style_id (1..12) so the PM-byte → style map in
    ``core/led_protocol.py`` can express its lookups symbolically.
    """
    AX120 = "ax120"     # legacy style_id 1
    PA120 = "pa120"     # legacy style_id 2
    AK120 = "ak120"     # legacy style_id 3
    LC1 = "lc1"         # legacy style_id 4
    LF8 = "lf8"         # legacy style_id 5
    LF12 = "lf12"       # legacy style_id 6
    LF10 = "lf10"       # legacy style_id 7
    CZ1 = "cz1"         # legacy style_id 8
    LC2 = "lc2"         # legacy style_id 9
    LF11 = "lf11"       # legacy style_id 10
    LF15 = "lf15"       # legacy style_id 11
    LF13 = "lf13"       # legacy style_id 12
    MAGIC_QUBE = "magic_qube"  # Linux-added: Thermalright Magic Qube (no legacy style_id)


# =========================================================================
# Product registry entry — immutable hardware description
# =========================================================================


@dataclass(frozen=True, slots=True)
class UsbPowerState:
    """A device's USB runtime-power state — an OS fact, read, never set here.

    Exists because nothing in TRCC has ever known whether a panel was
    SUSPENDED at the moment it failed to answer.  #150: a headless box stops
    streaming, the kernel autosuspends the panel ~10s later, and the next
    connect times out with ``[Errno 110]`` — indistinguishable, in every log we
    have, from a broken cable or a dead device.

    ``control`` is the policy ("auto" = the kernel may suspend it, "on" =
    never); ``runtime_status`` is the live truth ("active" / "suspended").
    ``supports_remote_wakeup`` is why two identical-looking panels behave
    differently: a device that cannot wake the host is not autosuspended at
    all, which is why the SCSI dev panel (bmAttributes 0x80) can never
    reproduce the HID reporter's failure.
    """
    control: str                       # "auto" | "on" | "" if unreadable
    runtime_status: str                # "active" | "suspended" | ""
    autosuspend_delay_ms: int = 0
    suspended_time_ms: int = 0
    supports_remote_wakeup: bool = False

    @property
    def suspended(self) -> bool:
        return self.runtime_status == "suspended"

    @property
    def may_autosuspend(self) -> bool:
        """The kernel is *allowed* to suspend it AND the device can wake."""
        return self.control == "auto" and self.supports_remote_wakeup


@dataclass(frozen=True, slots=True)
class PanelCutout:
    """Rectangular cutout on the panel (e.g. smartphone-display camera notch).

    Hardware fact — registry data, not user preference.  Populated on a
    ``VariantOverride`` when the panel has an obstructed region the
    render pipeline should mask.
    """
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """One row in the hardware registry.

    Everything known about a specific VID/PID combination at compile time:
    vendor/product strings, wire protocol, native resolution, supported
    rotations, LED style if applicable.

    ``button_image`` / ``panel_cutout`` carry registry-default values;
    ``ConnectDevice`` resolves a ``VariantOverride`` after handshake and
    swaps the field via ``dataclasses.replace`` when the device's
    PM/SUB fingerprint identifies a more specific variant.
    """
    vid: int
    pid: int
    vendor: str
    product: str
    wire: Wire
    kind: Kind
    device_type: int = 1
    fbl: int | None = None
    native_resolution: tuple[int, int] = (0, 0)
    orientations: tuple[int, ...] = (0,)
    led_style: LedStyle | None = None
    # True when the firmware does NOT latch a frame — it reverts to the built-in
    # Thermalright logo after ~2-3 s unless frames keep arriving, so the send
    # worker keepalive-resends the last frame.  A panel fact, declared per
    # device: SCSI and HID panels latch, LED has no screen, and the bulk / LY
    # families do not.  Read by ``Device.needs_keepalive``, which OR-s it with
    # the per-firmware ``keepalive_stream`` quirk.
    volatile_frames: bool = False
    model: str = "CZTV"                 # GUI sidebar button-image lookup
    button_image: str = "A1CZTV"        # asset base name (no .png)
    panel_cutout: PanelCutout | None = None  # set post-handshake

    @property
    def key(self) -> str:
        """Stable identifier: '0402:3922'."""
        return f"{self.vid:04x}:{self.pid:04x}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Capabilities derived from device ``kind``.

        §4 universal Commands gate on this instead of isinstance checks.
        Keyed by ``kind`` so a standard product declares nothing extra;
        an unknown kind yields the empty set (no Command applies).
        """
        return CAPABILITIES_BY_KIND.get(self.kind, frozenset())


# =========================================================================
# Runtime discovery — what the OS tells us exists right now
# =========================================================================


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Live device, produced by Platform.scan_devices().

    Matches a ProductInfo by (vid, pid); the path differs per enumeration.
    ``bcd_device`` is the USB ``bcdDevice`` (firmware revision, BCD-encoded) —
    the only pre-handshake discriminator between two firmware revisions that
    share a VID/PID, so :func:`quirks_for` keys on it.
    """
    vid: int
    pid: int
    path: str | None = None
    serial: str | None = None
    bcd_device: int = 0

    @property
    def key(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"

    @property
    def quirks(self) -> DeviceQuirks:
        """Firmware-specific behavior overrides for this exact device."""
        return quirks_for(self.vid, self.pid, self.bcd_device)


@dataclass(frozen=True, slots=True)
class DeviceQuirks:
    """Per-firmware behavior overrides for a device that diverges from its
    wire family's defaults.

    Keyed on ``(vid, pid, bcd_device)`` so ONE firmware revision opts in;
    every device without a row keeps its family default (all flags ``False``),
    so shared wire code (all Type-2 HID panels, the sibling PID, …) cannot
    regress.  Isolates firmware handling to an OCP registry row instead of
    fingerprint checks scattered across the transport / handshake / render
    seams.  (#228 — Frozen Warframe SE firmware 4.07.)
    """
    hid_reports: bool = False       # HidApiTransport (HID output reports), not bulk
    skip_init: bool = False         # do NOT send the wire's init packet (reboots panel)
    short_handshake: bool = False   # accept the firmware's short (<20-byte) reply
    portrait_native: bool = False   # device self-orients: 240×320 raster, no pre-rotate
    keepalive_stream: bool = False  # never skip an unchanged frame (panel blanks when idle)


_NO_QUIRKS = DeviceQuirks()

# (vid, pid, bcd_device) → quirks.  bcd_device is the raw USB bcdDevice (BCD).
DEVICE_QUIRKS: dict[tuple[int, int, int], DeviceQuirks] = {
    # Frozen Warframe SE, firmware bcdDevice 4.07 (#228, adamkoehler1990):
    # reverse-engineered — needs HID output reports (pyusb bulk is ignored),
    # no init packet, an 8-byte handshake reply, a portrait-native 240×320
    # raster (no pre-rotate), and a continuous frame stream (blanks when idle).
    (0x0416, 0x5302, 0x0407): DeviceQuirks(
        hid_reports=True, skip_init=True, short_handshake=True,
        portrait_native=True, keepalive_stream=True,
    ),
}


def quirks_for(vid: int, pid: int, bcd_device: int) -> DeviceQuirks:
    """Firmware quirks for an exact fingerprint, or the empty default."""
    return DEVICE_QUIRKS.get((vid, pid, bcd_device), _NO_QUIRKS)


# =========================================================================
# Handshake results
# =========================================================================


@dataclass(frozen=True, slots=True)
class HandshakeResult:
    """Result of Device.connect() — the raw device-reported state."""
    resolution: tuple[int, int]
    model_id: int
    serial: str = ""
    pm_byte: int = 0
    sub_byte: int = 0
    fbl: int | None = None
    raw_response: bytes = b""


@dataclass(frozen=True, slots=True)
class LedHandshakeResult:
    """LED handshake — style + model identifier."""
    pm: int
    sub_type: int
    style: LedStyle | None = None
    model_name: str = ""
    style_sub: int = 0
    raw_response: bytes = b""


# =========================================================================
# Frame & theme models
# =========================================================================


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Decoded video frame — RGB24 bytes.  Handed to Renderer."""
    data: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RenderContent:
    """Resolved artwork for one frame — opaque renderer surfaces.

    ``DisplayService`` resolves the active theme into these surfaces
    (``background`` = the theme background already merged with its mask and,
    for user uploads, pre-fitted to the canvas; ``overlay`` = the pre-rendered
    metric/clock overlay) and hands them to ``Renderer.build_frame``, the pure
    compose→encode Template Method.  Caching, sensors, brightness, split-mode
    and preview capture stay in ``DisplayService`` — it wraps the core.
    Surfaces are ``Any`` because the core treats them as opaque handles, exactly
    like the rest of the ``Renderer`` port; ``None`` background → solid black.
    """
    background: Any
    overlay: Any
    background_is_user: bool = False


class ThemeDir:
    """Standard TRCC theme directory layout — pure domain value object.

    Ported verbatim from legacy ``core/models/theme.py``: the names are
    Windows-TRCC convention and ``ThemeService`` MUST use these (never
    a "candidates list") so we don't render ``Theme.png`` — which is
    the panel thumbnail, not the background.

      td.bg       → 00.png        (rendered background)
      td.mask     → 01.png        (mask overlay)
      td.preview  → Theme.png     (panel thumbnail; never rendered)
      td.dc       → config1.dc    (binary overlay layout)
      td.zt       → Theme.zt      (legacy JPEG-sequence animation)
      td.json     → trcc.json     (next/-native JSON config)
      td.legacy_json → config.json (legacy JSON, read for fallback)
    """

    __slots__ = ("path",)

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @property
    def bg(self) -> Path:
        return self.path / "00.png"

    @property
    def mask(self) -> Path:
        return self.path / "01.png"

    @property
    def preview(self) -> Path:
        return self.path / "Theme.png"

    @property
    def dc(self) -> Path:
        return self.path / "config1.dc"

    @property
    def json(self) -> Path:
        return self.path / "trcc.json"

    @property
    def legacy_json(self) -> Path:
        return self.path / "config.json"

    @property
    def zt(self) -> Path:
        return self.path / "Theme.zt"

    def __truediv__(self, other: str) -> Path:
        return self.path / other

    def __str__(self) -> str:
        return str(self.path)


@dataclass(frozen=True, slots=True)
class Theme:
    """A theme loaded from disk — path + config blob."""
    path: Path
    name: str
    resolution: tuple[int, int]
    config: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebPreviewInfo:
    """One downloaded cloud-theme preview on disk (``data/web/{w}{h}/``).

    Pure domain data — the ``id`` is the file stem (``a001``), ``category``
    its first letter, ``has_video`` whether a sibling ``.mp4`` exists.
    URL construction (preview/download links) is an adapter concern and
    lives in the API layer, not here.
    """
    id: str
    category: str
    has_video: bool


# =========================================================================
# Sensor readings
# =========================================================================


@dataclass(frozen=True, slots=True)
class SensorReading:
    """One sensor's current value with metadata."""
    sensor_id: str
    category: str          # "cpu_temp", "gpu_temp", "fan", etc.
    value: float
    unit: str
    label: str = ""


# =========================================================================
# Per-device user settings (mutable, persisted)
# =========================================================================


@dataclass
class DeviceSettings:
    """User prefs for one device.  Persisted to config.json."""
    orientation: int = 0
    brightness: int = 100
    current_theme: str | None = None
    time_format: Literal["12h", "24h"] = "24h"
    date_format: str = "yyyy/MM/dd"
    temp_unit: TempUnit = "C"
    overlay_enabled: bool = True
    mask_position: tuple[int, int] | None = None
    fit_mode: FitMode = FitMode.WIDTH
    # Split-mode (Dynamic Island) style for 1600×720 widescreen panels.
    # 0 = off (no split overlay), 1/2/3 = style A/B/C.  Ignored by
    # rendering on devices whose profile.resolution isn't widescreen.
    split_mode: int = 0
    # User-supplied mask path that overrides the active theme's mask.
    # None = use the theme's bundled mask (or no mask if the theme has none).
    mask_path: str | None = None
    # Cloud-background override — a video path that replaces the
    # active theme's bundled background (00.png / Theme.mp4 / etc.).
    # Set by LoadCloudTheme, cleared by LoadTheme (picking a local
    # theme reverts to its own background).  Survives app restart.
    background_path: str | None = None
    # Active screencast region (x, y, w, h, audio) — the display source when the
    # screencast toggle is on.  ``None`` = not screencasting.  Persisted like
    # ``background_path`` so SaveTheme can bake it into a theme's ``screencast``
    # ref and a reload can resume it.  Mutually exclusive with a video/image
    # background (the toggles are).
    screencast_region: tuple[int, int, int, int, bool] | None = None
    # Active media-player source URI — the display source when the media-player
    # toggle is on.  A URI: a local path OR a URL/stream (http, https, rtsp, …).
    # ``None`` = not playing media.  Persisted like ``background_path`` so
    # SaveTheme can bake it into a theme's ``media_player`` ref and a reload can
    # resume it.  Mutually exclusive with the other display-source toggles.
    media_player_uri: str | None = None
    # Show / hide the mask overlay regardless of which mask source is active.
    mask_visible: bool = True
    # Background mode — what fills the LCD behind overlays.  Mirrors
    # legacy LCDDevice.set_background_mode.
    #   'theme' = use the active theme's background (default)
    #   'color' = solid fill from ``overlay_background``
    #   'transparent' = no background (used for screencast overlay)
    background_mode: Literal["theme", "color", "transparent"] = "theme"
    # Solid color used when ``background_mode == 'color'`` (RGB 0-255).
    overlay_background: tuple[int, int, int] = (0, 0, 0)
    # User-edited overlay elements layered on top of the theme's elements.
    # Theme-bundled elements come from ``theme.config["elements"]``; these
    # are the user's additions / overrides created via the Add/Update/Delete
    # overlay-element Commands.  Persisted in config.json under "user_overlay".
    user_overlay_elements: list[OverlayElement] = field(default_factory=list)
    # Mask-supplied overlay elements — when an external mask is applied
    # (cloud mask or user upload), its ``config1.dc`` carries its own
    # metric/clock/text layout that REPLACES the active theme's overlay
    # elements at render time.  Stored here (not on theme.config) so the
    # mask state survives a theme swap — picking a new background after
    # a mask keeps the mask layout, mirroring legacy's OverlayService
    # state being independent of the theme.  Set by ApplyMask, cleared
    # by SetMaskPath(None).
    mask_overlay_elements: list[OverlayElement] | None = None
    # Slideshow config — rotates through ``slideshow_themes`` every
    # ``slideshow_interval_s`` seconds while ``slideshow_enabled`` is true.
    # Cursor + timing live in SlideshowService (transient, not persisted).
    slideshow_enabled: bool = False
    slideshow_interval_s: float = 60.0
    slideshow_themes: list[str] = field(default_factory=list)


# =========================================================================
# Overlay elements (user-edited, layered on top of theme.config["elements"])
# =========================================================================


OverlayElementType = Literal["text", "metric", "clock"]
ClockSource = Literal["time", "weekday", "date"]

# The element field vocabulary is restated wherever an overlay element
# crosses a boundary — the domain object below, the flat Result view
# (``results.OverlayElementEntry``), the ``AddOverlayElement`` Command, and
# the API request bodies.  The DEFAULTS are single-sourced here so those
# copies cannot drift: a default that differed between the Command and the
# domain object would silently split a DC-parsed element from an API-added
# one, with nothing to catch it.
OVERLAY_DEFAULT_COLOR = "#ffffff"
OVERLAY_DEFAULT_SIZE = 16
OVERLAY_DEFAULT_FORMAT = "{value}"
OVERLAY_DEFAULT_CLOCK_SOURCE: ClockSource = "time"


@dataclass
class OverlayElement:
    """One user-edited overlay element.

    Fields beyond ``id`` / ``type`` / ``x`` / ``y`` are type-specific and
    ignored when not relevant for the active type — keeps serialization
    flat (no subclass hierarchy in the JSON form).

    Mirrors the dict shape ``OverlayService.render`` consumes so the same
    element can come from either the theme's ``config.json`` or the user's
    persisted edits.
    """
    # Unique identifier — caller supplies a string ID or one is generated
    # at add-time by ``AddOverlayElement``.  Used by Update/Delete Commands.
    id: str
    type: OverlayElementType = "text"
    x: int = 0
    y: int = 0
    color: str = OVERLAY_DEFAULT_COLOR
    size: int = OVERLAY_DEFAULT_SIZE
    bold: bool = False
    italic: bool = False
    # Font family.  Serialized as ``name`` -- the key both theme parsers
    # already write and the overlay serializer round-trips -- so a user-edited
    # element keeps the font instead of silently reverting to the theme
    # default the way it did when this field did not exist.
    font: str = ""
    # type == "text"
    text: str = ""
    # type == "metric"
    metric: str = ""
    format: str = OVERLAY_DEFAULT_FORMAT
    # type == "metric" — draw the unit glyph (°C/%/MHz/RPM) after the number,
    # or the bare number when the unit is baked into the theme art.  Mirrors
    # the Windows unit-switch (myModeSub == 1); the universal per-element unit
    # toggle flips this in every UI.
    show_unit: bool = True
    # type == "clock"
    source: ClockSource = OVERLAY_DEFAULT_CLOCK_SOURCE

    def to_dict(self) -> dict:
        """Flat dict — the shape ``OverlayService.render`` consumes."""
        out: dict = {
            "id": self.id,
            "type": self.type,
            "x": self.x, "y": self.y,
            "color": self.color, "size": self.size,
            "bold": self.bold, "italic": self.italic,
        }
        if self.font:
            out["name"] = self.font
        if self.type == "text":
            out["text"] = self.text
        elif self.type == "metric":
            out["metric"] = self.metric
            out["format"] = self.format
            out["show_unit"] = self.show_unit
        elif self.type == "clock":
            out["source"] = self.source
            # A date element carries the theme's strftime pattern (e.g.
            # "%m/%d"); render honours it.  The metric default "{value}"
            # is meaningless for a clock and ignored by the renderer.
            out["format"] = self.format
        return out

    @classmethod
    def from_dict(cls, data: dict) -> OverlayElement:
        """Rebuild from JSON-loaded dict; tolerant of missing fields.

        Elements from a DC parse / an older config carry no ``id``; mint a
        stable unique one so Update/Delete/Flash Commands can address the
        element (a bare positional index never matched the ``el_<uuid>`` /
        ``el_N`` schemes — #150/#203 click-to-highlight was fully broken).
        """
        return cls(
            id=str(data.get("id") or "") or f"el_{uuid.uuid4().hex[:8]}",
            type=data.get("type", "text"),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            color=str(data.get("color", "#ffffff")),
            size=int(data.get("size", 16)),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            text=str(data.get("text", "")),
            metric=str(data.get("metric", "")),
            format=str(data.get("format", "{value}")),
            show_unit=bool(data.get("show_unit", True)),
            source=data.get("source", "time"),
        )


# =========================================================================
# Display-mode action icons (legacy GUI asset names)
# =========================================================================

# Action name → icon PNG basename.  Used by the legacy-style display-mode
# toggle panels (Background / Mask / Video / Screencast) to find their
# action-button icons in ``next/ui/gui/assets/``.  Single source of
# truth so multiple panels don't drift on which icon goes with which
# action verb.
ACTION_ICON_IMAGES: dict[str, str] = {
    "Image":     "display_mode_icon_image.png",
    "Video":     "display_mode_icon_video.png",
    "Load":      "display_mode_icon_mask.png",
    "Upload":    "display_mode_icon_image.png",
    "VideoLoad": "display_mode_icon_livestream.png",
    "GIF":       "display_mode_icon_gif.png",
    "Network":   "display_mode_icon_network.png",
}


class OverlayMode(int, Enum):
    """Overlay element mode — matches the Windows myMode integer values.

    Used by the legacy-style overlay editor (DataTablePanel et al.) and
    persisted in legacy theme configs.  Next/'s native ``OverlayElement``
    uses ``OverlayElementType`` ("text" / "metric" / "clock") instead;
    this enum exists so legacy-style panels can keep their semantics
    while the overlay-editor port settles.
    """
    HARDWARE = 0
    TIME = 1
    WEEKDAY = 2
    DATE = 3
    CUSTOM = 4


# Date format sub-mode → asset basename for the cycling button icon.
DATE_FORMAT_IMAGES: dict[int, str] = {
    1: "display_mode_date_ymd.png",
    2: "display_mode_date_dmy.png",
    3: "display_mode_date_md.png",
    4: "display_mode_date_dm.png",
}


# Overlay mode → background icon asset for the 60×60 grid cell.
OVERLAY_MODE_IMAGES: dict[OverlayMode, str] = {
    OverlayMode.HARDWARE: "overlay_mode_hardware.png",
    OverlayMode.TIME:     "overlay_mode_time.png",
    OverlayMode.WEEKDAY:  "overlay_mode_weekday.png",
    OverlayMode.DATE:     "overlay_mode_date.png",
    OverlayMode.CUSTOM:   "overlay_mode_text.png",
}

# Selection highlight overlaid on the active grid cell.
OVERLAY_SELECT_IMAGE = "overlay_select.png"


# =========================================================================
# Hardware metric registry (legacy DC format)
# =========================================================================

# DC file (main_count, sub_count) → next/ ``HardwareMetrics`` field name.
# Single source of truth shared by the DC reader/writer, overlay editor
# grid cells, and any panel that wants to surface a labelled sensor.
HARDWARE_METRICS: dict[tuple[int, int], str] = {
    # CPU (main_count=0)
    (0, 1): "cpu_temp",
    (0, 2): "cpu_percent",
    (0, 3): "cpu_freq",
    (0, 4): "cpu_power",
    # GPU (main_count=1)
    (1, 1): "gpu_temp",
    (1, 2): "gpu_usage",
    (1, 3): "gpu_clock",
    (1, 4): "gpu_power",
    # MEM (main_count=2)
    (2, 1): "mem_percent",
    (2, 2): "mem_clock",
    (2, 3): "mem_available",
    (2, 4): "mem_temp",
    # HDD (main_count=3)
    (3, 1): "disk_read",
    (3, 2): "disk_write",
    (3, 3): "disk_activity",
    (3, 4): "disk_temp",
    # NET (main_count=4)
    (4, 1): "net_down",
    (4, 2): "net_up",
    (4, 3): "net_total_down",
    (4, 4): "net_total_up",
    # FAN (main_count=5)
    (5, 1): "fan_cpu",
    (5, 2): "fan_gpu",
    (5, 3): "fan_ssd",
    (5, 4): "fan_sys2",
}

METRIC_TO_IDS: dict[str, tuple[int, int]] = {
    v: k for k, v in HARDWARE_METRICS.items()
}


# Overlay-editor hardware category ID → display name.
CATEGORY_NAMES: dict[int, str] = {
    0: "CPU",
    1: "GPU",
    2: "MEM",
    3: "HDD",
    4: "NET",
    5: "FAN",
}


# Per-category sub-metric labels (matches the legacy uc_theme_setting grid).
SUB_METRICS: dict[int, dict[int, str]] = {
    0: {1: "Temp",  2: "Usage", 3: "Freq",     4: "Power"},
    1: {1: "Temp",  2: "Usage", 3: "Clock",    4: "Power"},
    2: {1: "Used%", 2: "Clock", 3: "Used",     4: "Free"},
    3: {1: "Read",  2: "Write", 3: "Activity", 4: "Temp"},
    4: {1: "Down",  2: "Up",    3: "Total",    4: "Ping"},
    5: {1: "RPM",   2: "PWM%",  3: "Temp",     4: "Speed"},
}


# Category → cell text colour (matches Windows TRCC palette).
CATEGORY_COLORS: dict[int, str] = {
    0: "#32C5FF",  # CPU - cyan
    1: "#44D7B6",  # GPU - teal
    2: "#6DD401",  # MEM - lime
    3: "#F7B501",  # HDD - amber
    4: "#FA6401",  # NET - orange
    5: "#E02020",  # FAN - red
}


# Time / date format tables (mirror legacy ``trcc.core.models.constants``).
TIME_FORMATS: dict[int, str] = {
    0: "%H:%M",  # 24-hour (14:58)
    1: "%I:%M",  # 12-hour with leading zero (02:58); stripped at format time
    2: "%H:%M",  # 24-hour (same as 0 in legacy)
}

DATE_FORMATS: dict[int, str] = {
    0: "%Y/%m/%d",  # 2026/01/30
    1: "%Y/%m/%d",  # (same as 0 — matches Windows)
    2: "%d/%m/%Y",
    3: "%m/%d",
    4: "%d/%m",
}


WEEKDAYS_BY_LANG: dict[str, list[str]] = {
    "en":    ["MON",   "TUE",   "WED",   "THU",   "FRI",   "SAT",   "SUN"],
    "de":    ["MO",    "DI",    "MI",    "DO",    "FR",    "SA",    "SO"],
    "fr":    ["LUN",   "MAR",   "MER",   "JEU",   "VEN",   "SAM",   "DIM"],
    "es":    ["LUN",   "MAR",   "MIÉ",   "JUE",   "VIE",   "SÁB",   "DOM"],
    "pt":    ["SEG",   "TER",   "QUA",   "QUI",   "SEX",   "SÁB",   "DOM"],
    "ru":    ["ПН",    "ВТ",    "СР",    "ЧТ",    "ПТ",    "СБ",    "ВС"],
    "ja":    ["月",    "火",    "水",    "木",    "金",    "土",    "日"],
    "ko":    ["월",    "화",    "수",    "목",    "금",    "토",    "일"],
    "zh":    ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
    "zh_TW": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
}
WEEKDAYS = WEEKDAYS_BY_LANG["en"]


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert °C to °F."""
    log.debug("celsius_to_fahrenheit: celsius=%s", celsius)
    return celsius * 9 / 5 + 32


def format_metric(
    metric: str,
    value: float,
    time_format: int = 0,
    date_format: int = 0,
    temp_unit: int = 0,
    lang: str | None = None,
) -> str:
    """Format a metric value for display (matches Windows TRCC).

    Ported from legacy ``trcc.core.models.sensor.format_metric``.
    Used by the legacy-style overlay editor in next/ to render
    preview text inside each grid cell.
    """
    log.debug("format_metric: metric=%s value=%s", metric, value)
    from datetime import datetime as _dt

    if metric == "date":
        now = _dt.now()
        fmt = DATE_FORMATS.get(date_format, DATE_FORMATS[0])
        return now.strftime(fmt)
    if metric == "time":
        now = _dt.now()
        fmt = TIME_FORMATS.get(time_format, TIME_FORMATS[0])
        result = now.strftime(fmt)
        if time_format == 1:
            result = result.lstrip("0")
        return result
    if metric == "weekday":
        weekdays = (
            WEEKDAYS_BY_LANG.get(lang or "en")
            or WEEKDAYS_BY_LANG.get((lang or "en").split("_", 1)[0])
            or WEEKDAYS
        )
        return weekdays[_dt.now().weekday()]
    if metric == "day_of_week":
        weekdays = (
            WEEKDAYS_BY_LANG.get(lang or "en")
            or WEEKDAYS_BY_LANG.get((lang or "en").split("_", 1)[0])
            or WEEKDAYS
        )
        return weekdays[int(value)]
    if metric.startswith(("time_", "date_")):
        return f"{int(value):02d}"
    if "temp" in metric:
        suffix = "°F" if temp_unit == 1 else "°C"
        return f"{value:.0f}{suffix}"
    if "percent" in metric or "usage" in metric or "activity" in metric:
        return f"{value:.0f}%"
    if "freq" in metric or "clock" in metric:
        if value >= 1000:
            return f"{value / 1000:.1f}GHz"
        return f"{value:.0f}MHz"
    if metric in ("disk_read", "disk_write"):
        return f"{value:.1f}MB/s"
    if metric in ("net_up", "net_down"):
        if value >= 1024:
            return f"{value / 1024:.1f}MB/s"
        return f"{value:.0f}KB/s"
    if metric in ("net_total_up", "net_total_down"):
        if value >= 1024:
            return f"{value / 1024:.1f}GB"
        return f"{value:.0f}MB"
    if metric.startswith("fan_"):
        return f"{value:.0f}RPM"
    if metric == "mem_available":
        if value >= 1024:
            return f"{value / 1024:.1f}GB"
        return f"{value:.0f}MB"
    return f"{value:.1f}"


# =========================================================================
# Legacy DC-format DTOs (used by the legacy-style overlay editor)
# =========================================================================


@dataclass(frozen=True, slots=True)
class FontConfig:
    """Font configuration as serialised in a DC file."""
    name: str
    size: float
    style: int      # 0=Regular, 1=Bold, 2=Italic
    unit: int       # GraphicsUnit
    charset: int
    color_argb: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ElementConfig:
    """One element's position + font as stored in a DC file."""
    x: int
    y: int
    font: FontConfig | None = None
    enabled: bool = True


@dataclass(slots=True)
class OverlayElementConfig:
    """Legacy-style overlay grid cell config.

    Used by the ported ``OverlayGridPanel`` to hold per-cell state
    (mode, sub-mode, position, font, color) while the user edits.
    Distinct from next/'s native :class:`OverlayElement` (which uses
    string types like "text" / "metric" / "clock"); the editor
    converts back and forth at the bus boundary.
    """
    mode: OverlayMode = OverlayMode.TIME
    mode_sub: int = 0
    x: int = 100
    y: int = 100
    main_count: int = 0
    sub_count: int = 1
    color: str = "#FFFFFF"
    font_name: str = "Microsoft YaHei"
    font_size: int = 36
    font_style: int = 0
    text: str = ""


# =========================================================================
# Activity-sidebar sensor catalog
# =========================================================================


# Per-category sensor definitions used by the legacy activity sidebar
# (right-rail strip of live sensor values).  ``(key_suffix, label, unit,
# metric_key)`` — metric_key matches a ``HardwareMetrics`` attribute.
SENSORS: dict[str, list[tuple[str, str, str, str]]] = {
    "cpu":     [("temp",      "TEMP",      "°C",   "cpu_temp"),
                ("usage",     "Usage",     "%",    "cpu_percent"),
                ("clock",     "Clock",     "MHz",  "cpu_freq"),
                ("power",     "Power",     "W",    "cpu_power")],
    "gpu":     [("temp",      "TEMP",      "°C",   "gpu_temp"),
                ("usage",     "Usage",     "%",    "gpu_usage"),
                ("clock",     "Clock",     "MHz",  "gpu_clock"),
                ("power",     "Power",     "W",    "gpu_power")],
    "memory":  [("temp",      "TEMP",      "°C",   "mem_temp"),
                ("usage",     "Usage",     "%",    "mem_percent"),
                ("clock",     "Clock",     "MHz",  "mem_clock"),
                ("available", "Available", "MB",   "mem_available")],
    "hdd":     [("temp",      "TEMP",      "°C",   "disk_temp"),
                ("activity",  "Activity",  "%",    "disk_activity"),
                ("read",      "Read",      "MB/s", "disk_read"),
                ("write",     "Write",     "MB/s", "disk_write")],
    "network": [("upload",    "UP rate",   "KB/s", "net_up"),
                ("download",  "DL rate",   "KB/s", "net_down"),
                ("total_up",  "Total UP",  "MB",   "net_total_up"),
                ("total_dl",  "Total DL",  "MB",   "net_total_down")],
    "fan":     [("cpu_fan",   "CPUFAN",    "RPM",  "fan_cpu"),
                ("gpu_fan",   "GPUFAN",    "RPM",  "fan_gpu"),
                ("ssd_fan",   "SSDFAN",    "RPM",  "fan_ssd"),
                ("fan2",      "FAN2",      "RPM",  "fan_sys2")],
}


# Category ID → sensor-dashboard background image asset.
CATEGORY_IMAGES: dict[int, str] = {
    0: "sysinfo_custom.png",
    1: "sysinfo_cpu.png",
    2: "sysinfo_gpu.png",
    3: "sysinfo_dram.png",
    4: "sysinfo_hdd.png",
    5: "sysinfo_net.png",
    6: "sysinfo_fan.png",
}


@dataclass(slots=True)
class SensorBinding:
    """One dashboard panel row → a SensorEnumerator sensor id."""
    label: str
    sensor_id: str
    unit: str


@dataclass(slots=True)
class PanelConfig:
    """One :class:`UCSystemInfo` dashboard panel — 4 sensor rows."""
    category_id: int
    name: str
    sensors: list[SensorBinding] = field(default_factory=list)


@dataclass(slots=True)
class MaskItem:
    """One mask entry in the GUI mask browser.

    Pure data DTO consumed by ``uc_theme_mask.py``.  Adapters fill it
    from whichever source they own (cloud catalog, user-uploaded dir).
    """
    name: str = ""
    path: str | None = None
    preview: str | None = None
    is_local: bool = True
    is_custom: bool = False


@dataclass(slots=True)
class LocalThemeItem:
    """Item in the local themes browser (UCThemeLocal)."""
    name: str = ""
    path: str = ""
    thumbnail: str = ""
    is_local: bool = True
    is_user: bool = False
    index: int = 0  # position in unfiltered list


@dataclass(slots=True)
class CloudThemeItem:
    """Item in the cloud themes browser (UCThemeWeb)."""
    name: str = ""
    id: str = ""
    video: str | None = None
    preview: str | None = None
    is_local: bool = False


# Resolution → (panel_w, panel_h) scaling table for the image_cut /
# video_cut background assets.  Native size for square + small
# rectangles; halved (or smaller) for widescreen so the panel chrome
# stays a fixed 500×702 px.
PANEL_ASSET_DIMS: dict[tuple[int, int], tuple[int, int]] = {
    (240, 240): (240, 240),
    (320, 320): (320, 320),
    (360, 360): (360, 360),
    (480, 480): (480, 480),
    (320, 240): (320, 240),   (240, 320): (240, 320),
    (640, 480): (320, 240),   (480, 640): (240, 320),
    (800, 480): (400, 240),   (480, 800): (240, 400),
    (854, 480): (427, 240),   (480, 854): (240, 427),
    (960, 540): (480, 270),   (540, 960): (270, 480),
    (960, 320): (480, 160),   (320, 960): (160, 480),
    (640, 172): (320, 86),    (172, 640): (86, 320),
    (1280, 480): (480, 180),  (480, 1280): (180, 480),
    (1600, 720): (400, 180),  (720, 1600): (180, 400),
    (1920, 462): (480, 116),  (462, 1920): (116, 480),
    (1920, 440): (480, 110),  (440, 1920): (110, 480),
}


def panel_asset_dims(w: int, h: int) -> tuple[int, int]:
    """Scaled panel dims for a device resolution.  Used by image / video cut.

    Falls back to (320, 240) landscape or (240, 320) portrait when the
    resolution isn't in the table — matches the C# else branch.
    """
    log.info("panel_asset_dims: %dx%d", w, h)
    if (dims := PANEL_ASSET_DIMS.get((w, h))):
        return dims
    return (240, 320) if h > w else (320, 240)


# ``subprocess.CREATE_NO_WINDOW`` is Windows-only.  On other OSes the
# attribute doesn't exist; we fall back to 0 so call sites can always
# pass ``creationflags=SUBPROCESS_NO_WINDOW`` without an OS check.
import subprocess as _subprocess  # noqa: E402 — keeps the public API at the top

SUBPROCESS_NO_WINDOW: int = getattr(_subprocess, 'CREATE_NO_WINDOW', 0)


# =========================================================================
# Hardware metrics DTOs — GUI widgets and LED segment displays read
# ``metrics.X`` (cpu_temp, gpu_usage, …) directly off this object.
# =========================================================================


@dataclass(frozen=True, slots=True)
class CpuMetrics:
    """One CPU package's readings — plural-ready element of HardwareMetrics.

    A dual-socket server has several of these; today's single-``cpu()``
    source yields exactly one.  Plain floats only — JSON-serialisable for
    the SensorsUpdated-over-IPC stream (no source objects).
    """
    name: str = ""
    temp: float = 0.0
    usage: float = 0.0
    freq: float = 0.0
    power: float = 0.0


@dataclass(frozen=True, slots=True)
class GpuMetrics:
    """One GPU's readings — plural-ready element of HardwareMetrics."""
    name: str = ""
    temp: float = 0.0
    usage: float = 0.0
    clock: float = 0.0
    power: float = 0.0


@dataclass(slots=True)
class HardwareMetrics:
    """Typed DTO for system sensor readings — the one metrics object every
    consumer observes.

    Built once per tick by :meth:`SensorEnumerator.snapshot` from the
    typed sensor sources (no fragile ``sensor_id``→attr string table).
    GUI gauges and LED segment displays both read ``metrics.cpu_temp``
    etc. straight off this object; the ``readings`` dict carries the full
    flat view for the system-info dashboard.

    **Plural-ready (servers/workstations).**  ``cpus``/``gpus`` carry every
    detected unit (single-element on consumer hardware today); the scalar
    device fields below COLLAPSE them by policy — ``cpu_temp`` is the
    hottest socket, ``cpu_percent`` the average — so a cooler that shows one
    "CPU" number stays correct when sources widen to plural.  Widening the
    source ABC later fills the lists without touching any consumer.
    """
    cpu_temp: float = 0.0
    cpu_percent: float = 0.0
    cpu_freq: float = 0.0
    cpu_power: float = 0.0
    gpu_temp: float = 0.0
    gpu_usage: float = 0.0
    gpu_clock: float = 0.0
    gpu_power: float = 0.0
    mem_temp: float = 0.0
    mem_percent: float = 0.0
    mem_clock: float = 0.0
    mem_available: float = 0.0
    mem_used: float = 0.0
    disk_temp: float = 0.0
    disk_activity: float = 0.0
    disk_read: float = 0.0
    disk_write: float = 0.0
    net_up: float = 0.0
    net_down: float = 0.0
    net_total_up: float = 0.0
    net_total_down: float = 0.0
    fan_cpu: float = 0.0
    fan_gpu: float = 0.0
    fan_ssd: float = 0.0
    fan_sys2: float = 0.0
    readings: dict[str, float] = field(default_factory=dict)
    # Plural sources, faithful per-unit (single-element today; the scalar
    # fields above are their collapse).  See class docstring.
    cpus: list[CpuMetrics] = field(default_factory=list)
    gpus: list[GpuMetrics] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SensorInfo:
    """Describes a single hardware sensor (legacy GUI shape)."""
    id: str
    name: str
    category: str
    unit: str
    source: str


# Cloud mask server URLs by resolution string.  Source: C# UCMask
# endpoints — same hostname / path layout legacy uses.  Per-resolution
# downloads happen lazily when the user picks a mask thumbnail.
CLOUD_MASK_URLS: dict[str, str] = {
    "240x240":   "http://www.czhorde.cc/tr/zt240240/",
    "240x320":   "http://www.czhorde.cc/tr/zt240320/",
    "320x240":   "http://www.czhorde.cc/tr/zt320240/",
    "320x320":   "http://www.czhorde.cc/tr/zt320320/",
    "360x360":   "http://www.czhorde.cc/tr/zt360360/",
    "480x480":   "http://www.czhorde.cc/tr/zt480480/",
    "480x640":   "http://www.czhorde.cc/tr/zt480640/",
    "480x800":   "http://www.czhorde.cc/tr/zt480800/",
    "480x854":   "http://www.czhorde.cc/tr/zt480854/",
    "480x1280":  "http://www.czhorde.cc/tr/zt4801280/",
    "540x960":   "http://www.czhorde.cc/tr/zt540960/",
    "640x480":   "http://www.czhorde.cc/tr/zt640480/",
    "720x1600":  "http://www.czhorde.cc/tr/zt7201600/",
    "800x480":   "http://www.czhorde.cc/tr/zt800480/",
    "854x480":   "http://www.czhorde.cc/tr/zt854480/",
    "960x540":   "http://www.czhorde.cc/tr/zt960540/",
    "1280x480":  "http://www.czhorde.cc/tr/zt1280480/",
    "1600x720":  "http://www.czhorde.cc/tr/zt1600720/",
    "1920x462":  "http://www.czhorde.cc/tr/zt1920462/",
    "462x1920":  "http://www.czhorde.cc/tr/zt4621920/",
}


def is_safe_archive_member(name: str) -> bool:
    """Reject archive members that would escape the destination (zip slip)."""
    log.debug("is_safe_archive_member: name=%r", name)
    return not (Path(name).is_absolute() or '..' in name.split('/'))


# ``category_keysuffix`` → overlay ``(main_count, sub_count)``.
# Used by the activity sidebar's click-to-add-element flow.
SENSOR_TO_OVERLAY: dict[str, tuple[int, int]] = {
    "cpu_temp": (0, 1),     "cpu_usage": (0, 2),
    "cpu_clock": (0, 3),    "cpu_power": (0, 4),
    "gpu_temp": (1, 1),     "gpu_usage": (1, 2),
    "gpu_clock": (1, 3),    "gpu_power": (1, 4),
    "memory_temp": (2, 1),  "memory_usage": (2, 2),
    "memory_clock": (2, 3), "memory_available": (2, 4),
    "hdd_temp": (3, 1),     "hdd_activity": (3, 2),
    "hdd_read": (3, 3),     "hdd_write": (3, 4),
    "network_upload": (4, 1),   "network_download": (4, 2),
    "network_total_up": (4, 3), "network_total_dl": (4, 4),
    "fan_cpu_fan": (5, 1),  "fan_gpu_fan": (5, 2),
    "fan_ssd_fan": (5, 3),  "fan_fan2": (5, 4),
}


# =========================================================================
# Cloud theme catalog DTOs (pure data the CloudCatalog port speaks)
# =========================================================================


@dataclass(frozen=True, slots=True)
class CloudCategory:
    """One entry in the cloud catalog's category table."""
    prefix: str
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class CloudThemeEntry:
    """One theme in the catalog — id + which category it lives in."""
    id: str               # e.g. "a001"
    category: str         # prefix, e.g. "a"
    category_name: str    # human label, e.g. "Gallery"


# =========================================================================
# SMBIOS memory descriptors (WMI Win32_PhysicalMemory numeric codes)
# =========================================================================

# ``Win32_PhysicalMemory.SMBIOSMemoryType`` (SMBIOS 7.18.2) → DRAM family.
# ``Win32_PhysicalMemory.FormFactor`` (SMBIOS 7.18.1) → module form factor.
# The Windows sensor adapter maps the raw WMI integers through these so the
# memory panel shows "DDR5" / "SODIMM" instead of a bare code; Linux/macOS
# read the same field names from their native probes.  Codes that aren't in
# the table resolve to ``"Unknown"`` (see ``memory_type`` / ``memory_form_factor``).

SMBIOS_MEMORY_TYPE: dict[int, str] = {
    20: "DDR",
    21: "DDR2",
    24: "DDR3",
    26: "DDR4",
    30: "LPDDR4",
    34: "DDR5",
    35: "LPDDR5",
}

MEMORY_FORM_FACTOR: dict[int, str] = {
    8: "DIMM",
    12: "SODIMM",
    13: "RIMM",
    15: "FB-DIMM",
    16: "Die",
}


def memory_type(code: int | None) -> str:
    """SMBIOS memory-type code → DRAM family name (``"Unknown"`` if unmapped)."""
    return SMBIOS_MEMORY_TYPE.get(code or 0, "Unknown")


def memory_form_factor(code: int | None) -> str:
    """SMBIOS form-factor code → module form factor (``"Unknown"`` if unmapped)."""
    return MEMORY_FORM_FACTOR.get(code or 0, "Unknown")
