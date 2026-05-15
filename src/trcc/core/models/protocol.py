"""Protocol models — handshake results, device profiles, FBL/PM mappings, traits."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

# =============================================================================
# Handshake results
# =============================================================================

@dataclass(slots=True)
class HandshakeResult:
    """Common output from any device handshake.

    Every protocol (SCSI, HID, LED, Bulk) produces at least these fields.
    Protocol-specific subclasses (HidHandshakeInfo, LedHandshakeInfo) add extras.
    """

    resolution: tuple[int, int] | None = None
    model_id: int = 0
    serial: str = ""
    pm_byte: int = 0   # Raw PM from handshake (for button image lookup)
    sub_byte: int = 0   # Raw SUB from handshake (for button image lookup)
    model_name: str = ""   # ASCII product code parsed from raw_response, e.g. "SSCRM-V3" (Bulk).
                            # Empty when the protocol carries no such bytes.
    raw_response: bytes = field(default=b"", repr=False)


@dataclass(slots=True)
class HidHandshakeInfo(HandshakeResult):
    """HID-specific handshake info (extends HandshakeResult)."""
    device_type: int = 0      # 2 or 3
    mode_byte_1: int = 0     # Type 2: resp[5] (PM), Type 3: resp[0]-1
    mode_byte_2: int = 0     # Type 2: resp[4] (SUB), Type 3: 0
    fbl: int | None = None  # FBL code resolved from PM/SUB


@dataclass(slots=True)
class LCDDeviceConfig:
    """SCSI LCD device config — resolution, pixel format, protocol constants.

    Pure data: no I/O, no business logic. Business logic lives in
    ImageService (rgb_to_bytes, byte_order) and DeviceService (detect_resolution).
    """
    name: str = "Generic LCD"
    width: int = 320
    height: int = 320
    pixel_format: str = "RGB565"
    fbl: int | None = None
    resolution_detected: bool = False
    poll_command: tuple[int, int] = (0xF5, 0xE100)
    init_command: tuple[int, int] = (0x1F5, 0xE100)
    init_per_frame: bool = False
    init_delay: float = 0.0
    frame_delay: float = 0.0

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @staticmethod
    def from_key(impl_key: str) -> LCDDeviceConfig:
        """Factory: create config from implementation key."""
        from .device import IMPL_NAMES
        name = IMPL_NAMES.get(impl_key, "Generic LCD")
        return LCDDeviceConfig(name=name)

    @staticmethod
    def list_all() -> list[dict[str, str]]:
        """List all available implementations."""
        from .device import IMPL_NAMES
        return [{"name": key, "class": dn} for key, dn in IMPL_NAMES.items()]


class PlaybackState(Enum):
    """Video playback state."""
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass(slots=True)
class VideoState:
    """
    State of video/animation playback.
    """
    state: PlaybackState = PlaybackState.STOPPED
    current_frame: int = 0
    total_frames: int = 0
    fps: float = 16.0
    loop: bool = True

    @property
    def progress(self) -> float:
        """Get playback progress (0-100)."""
        if self.total_frames <= 0:
            return 0.0
        return (self.current_frame / self.total_frames) * 100

    @property
    def current_time_str(self) -> str:
        """Get current time as MM:SS string."""
        if self.fps <= 0:
            return "00:00"
        secs = self.current_frame / self.fps
        return f"{int(secs // 60):02d}:{int(secs % 60):02d}"

    @property
    def total_time_str(self) -> str:
        """Get total time as MM:SS string."""
        if self.fps <= 0:
            return "00:00"
        secs = self.total_frames / self.fps
        return f"{int(secs // 60):02d}:{int(secs % 60):02d}"

    @property
    def frame_interval_ms(self) -> int:
        """Get frame interval in milliseconds (capped at 16 FPS).

        Windows C# uses a fixed 62ms timer (≈16 FPS) for all animated
        themes regardless of source FPS.  Higher rates waste CPU on a
        small LCD where the difference is imperceptible.
        """
        if self.fps <= 0:
            return 62
        return max(62, int(1000 / self.fps))


# =============================================================================
# Device Profile — single source of truth for all FBL-derived properties.
# Replaces FBL_TO_RESOLUTION, JPEG_MODE_FBLS, BULK_RGB565_FBLS,
# byte_order_for(), _SQUARE_NO_ROTATE.
# =============================================================================


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Everything needed to talk to a device, derived from its FBL code.

    One lookup replaces 5 scattered constants/functions.
    """
    width: int
    height: int
    jpeg: bool = False           # JPEG encoding (vs RGB565)
    big_endian: bool = False     # RGB565 byte order (> vs <)
    rotate: bool = False         # Pre-rotate 90° CW for non-square portrait panels
    # Device encode rotation (C# RotateImg in ImageToJpg).
    # Formula: angle = (base + direction * sign) % 360
    # sign = -1 if encode_invert else +1. sub_byte / pm_byte override base.
    encode_base: int = 0
    encode_invert: bool = True   # True = (base - dir), most devices
    encode_sub_bases: tuple[tuple[int, int], ...] = ()  # ((sub, base), ...)
    # PM-byte (PingMu) keyed override.  C# 480x480 + 320x320 dispatches on
    # myDevicePingMu (e.g. PM=6 → 180° hardware-mount baseline for FW360
    # Ultra).  Different from encode_sub_bases — that's mySubMode.
    encode_pm_bases: tuple[tuple[int, int], ...] = ()   # ((pm, base), ...)

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def byte_order(self) -> str:
        return '>' if self.big_endian else '<'


# fmt: off
FBL_PROFILES: dict[int, DeviceProfile] = {
    #          W      H     jpeg    BE      rotate  enc_base  enc_inv  enc_sub
    36:  DeviceProfile(240,  240),
    37:  DeviceProfile(240,  240),
    50:  DeviceProfile(320,  240,  rotate=True),
    51:  DeviceProfile(320,  240,  rotate=True),                     # HID Type 2 → SPIMode=1
    52:  DeviceProfile(320,  240,  rotate=True),                     # BA120 Vision (#100)
    53:  DeviceProfile(320,  240,  rotate=True),                     # HID Type 2 → SPIMode=1
    54:  DeviceProfile(360,  360,  jpeg=True),
    58:  DeviceProfile(320,  240,  rotate=True),
    64:  DeviceProfile(640,  480,  rotate=True),
    72:  DeviceProfile(480,  480,
                       encode_pm_bases=((6, 180),)),            # FW360 Ultra PM=6 → 180° baseline (#137)
    100: DeviceProfile(320,  320,  big_endian=True),
    101: DeviceProfile(320,  320,  big_endian=True),
    102: DeviceProfile(320,  320,  big_endian=True),
    114: DeviceProfile(1600, 720,  jpeg=True, rotate=True,
                       encode_base=180, encode_sub_bases=((3, 0),)),
    128: DeviceProfile(1280, 480,  jpeg=True, rotate=True,
                       encode_sub_bases=((2, 90),)),
    129: DeviceProfile(480,  480,
                       encode_pm_bases=((6, 180),)),            # alias for 72
    192: DeviceProfile(1920, 462,  jpeg=True, rotate=True,
                       encode_base=180, encode_sub_bases=((2, 0), (3, 0), (4, 0))),
    224: DeviceProfile(854,  480,  jpeg=True, rotate=True,
                       encode_invert=False, encode_sub_bases=((2, 180),)),
}
# fmt: on

_DEFAULT_PROFILE = DeviceProfile(320, 320, big_endian=True)


def get_encode_rotation(profile: DeviceProfile, sub_byte: int,
                        direction: int, pm_byte: int = 0) -> int:
    """Compute device encode rotation angle (C# RotateImg in ImageToJpg).

    Every C# angle table follows: angle = (base + direction * sign) % 360.
    sign is per-resolution (-1 if encode_invert, +1 otherwise).
    pm_byte overrides base via encode_pm_bases (C# myDevicePingMu dispatch);
    sub_byte overrides via encode_sub_bases (C# mySubMode dispatch).
    PM takes precedence — C# checks PingMu first for square panels.
    """
    base = profile.encode_base
    for pm, pm_base in profile.encode_pm_bases:
        if pm_byte == pm:
            base = pm_base
            break
    else:
        for sub, sub_base in profile.encode_sub_bases:
            if sub_byte == sub:
                base = sub_base
                break
    sign = -1 if profile.encode_invert else 1
    return (base + direction * sign) % 360


def get_profile(fbl: int, pm: int = 0) -> DeviceProfile:
    """Get device profile for an FBL code.

    For FBL 224/192, PM disambiguates the resolution (same encoding props).
    """
    profile = FBL_PROFILES.get(fbl, _DEFAULT_PROFILE)
    if fbl == 224:
        w, h = _FBL_224_BY_PM.get(pm, (854, 480))
        return DeviceProfile(w, h, jpeg=profile.jpeg,
                             big_endian=profile.big_endian, rotate=profile.rotate,
                             encode_base=profile.encode_base,
                             encode_invert=profile.encode_invert,
                             encode_sub_bases=profile.encode_sub_bases,
                             encode_pm_bases=profile.encode_pm_bases)
    if fbl == 192:
        w, h = _FBL_192_BY_PM.get(pm, (1920, 462))
        return DeviceProfile(w, h, jpeg=profile.jpeg,
                             big_endian=profile.big_endian, rotate=profile.rotate,
                             encode_base=profile.encode_base,
                             encode_invert=profile.encode_invert,
                             encode_sub_bases=profile.encode_sub_bases,
                             encode_pm_bases=profile.encode_pm_bases)
    return profile


# --- Backward compatibility aliases (remove after migration) ---

FBL_TO_RESOLUTION: dict[int, tuple[int, int]] = {
    fbl: p.resolution for fbl, p in FBL_PROFILES.items()
}

JPEG_MODE_FBLS: frozenset[int] = frozenset(
    fbl for fbl, p in FBL_PROFILES.items() if p.jpeg
)

BULK_RGB565_FBLS: frozenset[int] = frozenset(
    fbl for fbl, p in FBL_PROFILES.items() if not p.jpeg and p.big_endian
)

# Reverse lookup: resolution → PM/FBL (first match wins)
RESOLUTION_TO_PM: dict[tuple[int, int], int] = {
    p.resolution: fbl for fbl, p in FBL_PROFILES.items()
    if fbl not in (37, 101, 102, 224)
}

# PM byte → FBL byte for Type 2 devices where PM ≠ FBL.
# (FormCZTV.cs lines 682-821)
# For all other PM values, PM=FBL (same convention as SCSI poll bytes).
_PM_TO_FBL_OVERRIDES: dict[int, int] = {
    5:   50,    # 320x240
    7:   64,    # 640x480
    9:   224,   # 854x480
    10:  224,   # 960x540 (disambiguated in _FBL_224_BY_PM)
    11:  224,   # 854x480
    12:  224,   # 800x480 (disambiguated in _FBL_224_BY_PM)
    13:  224,   # 960x320 (disambiguated in _FBL_224_BY_PM)
    14:  64,    # 640x480
    15:  224,   # 640x172 (disambiguated in _FBL_224_BY_PM)
    16:  224,   # 960x540 (disambiguated in _FBL_224_BY_PM)
    17:  224,   # 960x320 (disambiguated in _FBL_224_BY_PM)
    32:  100,   # 320x320
    50:  50,    # 320x240 (SPI mode 2)
    63:  114,   # 1600x720
    64:  114,   # 1600x720
    65:  192,   # 1920x462
    66:  192,   # 1920x462
    68:  192,   # 1280x480 (disambiguated in _FBL_192_BY_PM)
    69:  192,   # 1920x440 (disambiguated in _FBL_192_BY_PM)
}


# FBL 224 is shared by 5 resolutions — PM byte disambiguates
_FBL_224_BY_PM: dict[int, tuple[int, int]] = {
    10: (960, 540),
    12: (800, 480),
    13: (960, 320),
    15: (640, 172),
    16: (960, 540),
    17: (960, 320),
}

# FBL 192 is shared by 3 resolutions — PM byte disambiguates
_FBL_192_BY_PM: dict[int, tuple[int, int]] = {
    68: (1280, 480),
    69: (1920, 440),
}

# PM+SUB compound keys where sub byte changes the FBL mapping
_PM_SUB_TO_FBL: dict[tuple[int, int], int] = {
    (1, 48): 114,   # 1600x720
    (1, 49): 192,   # 1920x462
}


def fbl_to_resolution(fbl: int, pm: int = 0) -> tuple[int, int]:
    """Map FBL byte to (width, height). Delegates to get_profile()."""
    return get_profile(fbl, pm).resolution


def pm_to_fbl(pm: int, sub: int = 0) -> int:
    """Map PM byte to FBL byte.

    Default: PM=FBL (same convention as SCSI poll bytes).
    Only overrides for the few PM values where PM ≠ FBL.
    Compound key (PM, SUB) checked first for sub-dependent mappings.
    """
    if (pm, sub) in _PM_SUB_TO_FBL:
        return _PM_SUB_TO_FBL[(pm, sub)]
    return _PM_TO_FBL_OVERRIDES.get(pm, pm)


# Split mode overlay (灵动岛 / Dynamic Island) for 1600x720 widescreen devices.
# C# UCScreenImage.cs: buttonLDD cycles myLddVal 1→2→3→1, overlay selected by
# (style, directionB). Key: (myLddVal, rotation_degrees) → asset filename.
SPLIT_OVERLAY_MAP: dict[tuple[int, int], str] = {
    # Style A (myLddVal=1)
    (1, 0):   'split_overlay_a.png',
    (1, 90):  'split_overlay_a_90.png',
    (1, 180): 'split_overlay_a_180.png',
    (1, 270): 'split_overlay_a_270.png',
    # Style B (myLddVal=2, default)
    (2, 0):   'split_overlay_b.png',
    (2, 90):  'split_overlay_b_90.png',
    (2, 180): 'split_overlay_b_180.png',
    (2, 270): 'split_overlay_b_270.png',
    # Style C (myLddVal=3)
    (3, 0):   'split_overlay_c.png',
    (3, 90):  'split_overlay_c_90.png',
    (3, 180): 'split_overlay_c_180.png',
    (3, 270): 'split_overlay_c_270.png',
}

# Widescreen resolutions that support split mode (灵动岛).
# C#: myDeviceMode==2 && (pm==64 || (pm==1 && pmSub==48))
SPLIT_MODE_RESOLUTIONS: set[tuple[int, int]] = {(1600, 720)}


# =============================================================================
# USB transport timeouts — calibrated per transport, NOT a typo.
# =============================================================================
# Handshake values look inconsistent at first read (1000 vs 5000) — they are
# calibrated for the latency profile of each transport stack:
#
#   Bulk + LY: raw pyusb endpoint read/write straight to the device.
#              Handshake payload is tiny (a few bytes), round-trip is ~10ms
#              typical.  1000ms is generous; longer just delays handshake
#              failures.
#
#   HID:       kernel HID layer mediates each packet (HID report parsing,
#              boot-protocol negotiation on some chipsets).  Some HID Type 2
#              firmwares take 1-2s to respond on first poll after replug.
#              5000ms gives margin without making failure cases hang.
#
# Frame-write timeout is uniform — sized for the largest possible payload
# (full 1280×480 RGB565 + JPEG headers ≈ 200KB), which dominates over
# transport differences.
HANDSHAKE_TIMEOUT_BULK_MS = 1000
HANDSHAKE_TIMEOUT_LY_MS = 1000   # by-design twin of Bulk
HANDSHAKE_TIMEOUT_HID_MS = 5000
FRAME_WRITE_TIMEOUT_MS = 5000


# =============================================================================
# Panel Asset Dims — scaled dimensions for crop/video panel backgrounds
# =============================================================================
# C# buttonSelectBackgroundImage() maps each device resolution to scaled dims
# that fit the fixed-size panel. Assets: video_cut_{pw}x{ph}, image_cut_{pw}x{ph}.
# Both landscape and portrait entries included.
PANEL_ASSET_DIMS: dict[tuple[int, int], tuple[int, int]] = {
    # Square
    (240, 240): (240, 240),
    (320, 320): (320, 320),
    (360, 360): (360, 360),
    (480, 480): (480, 480),
    # Rectangular — native size
    (320, 240): (320, 240),   (240, 320): (240, 320),
    # Rectangular — ÷2
    (640, 480): (320, 240),   (480, 640): (240, 320),
    (800, 480): (400, 240),   (480, 800): (240, 400),
    (854, 480): (427, 240),   (480, 854): (240, 427),
    (960, 540): (480, 270),   (540, 960): (270, 480),
    (960, 320): (480, 160),   (320, 960): (160, 480),
    (640, 172): (320, 86),    (172, 640): (86, 320),
    # Widescreen — ÷2.67
    (1280, 480): (480, 180),  (480, 1280): (180, 480),
    # Widescreen — ÷4
    (1600, 720): (400, 180),  (720, 1600): (180, 400),
    (1920, 462): (480, 116),  (462, 1920): (116, 480),
    (1920, 440): (480, 110),  (440, 1920): (110, 480),
}


def panel_asset_dims(w: int, h: int) -> tuple[int, int]:
    """Look up scaled panel dims for a device resolution.

    Falls back to (320, 240) landscape or (240, 320) portrait,
    matching the C# else branch in buttonSelectBackgroundImage().
    """
    if (dims := PANEL_ASSET_DIMS.get((w, h))):
        return dims
    return (240, 320) if h > w else (320, 240)


# =============================================================================
# Protocol / Device Type Display Names
# =============================================================================

PROTOCOL_NAMES: dict[str, str] = {
    "scsi": "SCSI (sg_raw)",
    "hid": "HID (USB bulk)",
    "led": "LED (HID 64-byte)",
    "bulk": "USB Bulk (USBLCDNew)",
    "ly": "USB Bulk LY (Trofeo Vision)",
}

DEVICE_TYPE_NAMES: dict[int, str] = {
    1: "SCSI RGB565",
    2: "HID Type 2 (H)",
    3: "HID Type 3 (ALi)",
    4: "Raw USB Bulk LCD",
    5: "USB Bulk LY (Trofeo Vision)",
}

LED_DEVICE_TYPE_NAME: str = "RGB LED Controller"


# =============================================================================
# Protocol Traits — data-driven per-protocol behavioral properties
# =============================================================================


@dataclass(frozen=True, slots=True)
class ProtocolTraits:
    """Per-protocol behavioral data — eliminates scattered string checks.

    Single source of truth for protocol-specific properties.  Consumed by
    udev rule generation, factory backend selection, image encoding, and
    device type classification.
    """
    udev_subsystems: tuple[str, ...]     # subsystems for udev rules
    backend_key: str                     # 'sg_raw' or 'pyusb'
    fallback_backend: str | None      # 'hidapi' for HID/LED, None for others
    requires_reboot: bool                # SCSI needs reboot after udev quirk
    supports_jpeg: bool                  # bulk/ly use JPEG encoding
    is_led: bool                         # LED protocol — original (predates LCD)

    @property
    def is_lcd(self) -> bool:
        """LCD protocol — the newer device type (LED came first)."""
        return not self.is_led


PROTOCOL_TRAITS: dict[str, ProtocolTraits] = {
    'scsi': ProtocolTraits(
        udev_subsystems=('scsi_generic',), backend_key='sg_raw',
        fallback_backend=None, requires_reboot=True,
        supports_jpeg=False, is_led=False),
    'hid': ProtocolTraits(
        udev_subsystems=('hidraw', 'usb'), backend_key='pyusb',
        fallback_backend='hidapi', requires_reboot=False,
        supports_jpeg=False, is_led=False),
    'bulk': ProtocolTraits(
        udev_subsystems=('usb',), backend_key='pyusb',
        fallback_backend=None, requires_reboot=False,
        supports_jpeg=True, is_led=False),
    'ly': ProtocolTraits(
        udev_subsystems=('usb',), backend_key='pyusb',
        fallback_backend=None, requires_reboot=False,
        supports_jpeg=True, is_led=False),
    'led': ProtocolTraits(
        udev_subsystems=('hidraw', 'usb'), backend_key='pyusb',
        fallback_backend='hidapi', requires_reboot=False,
        supports_jpeg=False, is_led=True),
}


# =============================================================================
# Cloud / Mask URL maps (single source of truth — adapters import from here)
# =============================================================================

# Cloud theme download URL keys (resolution string → server path suffix).
# Source: C# v2.1.2 GifWebDir* constants.
CLOUD_THEME_URL_KEYS: dict[str, str] = {
    "240x240": "bj240240",
    "240x320": "bj240320",
    "320x240": "bj320240",
    "320x320": "bj320320",
    "360x360": "bj360360",
    "480x480": "bj480480",
    "640x172": "bj640172",
    "640x480": "bj640480",
    "800x480": "bj800480",
    "854x480": "bj854480",
    "960x320": "bj960320",
    "960x540": "bj960540",
    "1280x480": "bj1280480",
    "1600x720": "bj1600720",
    "1600x720u": "bj1600720u",
    "1600x720l": "bj1600720l",
    "1920x440": "bj1920440",
    "1920x462": "bj1920462",
    # Portrait variants
    "172x640": "bj172640",
    "320x960": "bj320960",
    "440x1920": "bj4401920",
    "462x1920": "bj4621920",
    "480x640": "bj480640",
    "480x800": "bj480800",
    "480x854": "bj480854",
    "480x1280": "bj4801280",
    "540x960": "bj540960",
    "720x1600": "bj7201600",
    "720x1600u": "bj7201600u",
    "720x1600l": "bj7201600l",
}

# Cloud mask server URLs by resolution string.
# Source: C# UCMask server endpoints.
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
    # Billboard/side-strip devices (cloud-only, no bundled archives)
    "640x172":   "http://www.czhorde.cc/tr/zt640172/",
    "172x640":   "http://www.czhorde.cc/tr/zt172640/",
    "960x320":   "http://www.czhorde.cc/tr/zt960320/",
    "320x960":   "http://www.czhorde.cc/tr/zt320960/",
    "1920x440":  "http://www.czhorde.cc/tr/zt1920440/",
    "440x1920":  "http://www.czhorde.cc/tr/zt4401920/",
}

# Cloud theme server base URL templates (resolution suffix appended at runtime).
# Order matters: `download_theme` iterates these in insertion order and
# returns the first success.  `china` (czhorde.com) is tried first because
# on typical US/international links it responds faster than `international`
# (czhorde.cc).  Contrary to the keys' names — preserved for back-compat
# with user settings.
CLOUD_SERVERS: dict[str, str] = {
    'china': 'http://www.czhorde.com/tr/bj{resolution}/',
    'international': 'http://www.czhorde.cc/tr/bj{resolution}/',
}


__all__ = [
    'BULK_RGB565_FBLS',
    'CLOUD_MASK_URLS',
    'CLOUD_SERVERS',
    'CLOUD_THEME_URL_KEYS',
    'DEVICE_TYPE_NAMES',
    'FBL_PROFILES',
    'FBL_TO_RESOLUTION',
    'FRAME_WRITE_TIMEOUT_MS',
    'HANDSHAKE_TIMEOUT_BULK_MS',
    'HANDSHAKE_TIMEOUT_HID_MS',
    'HANDSHAKE_TIMEOUT_LY_MS',
    'JPEG_MODE_FBLS',
    'LED_DEVICE_TYPE_NAME',
    'PANEL_ASSET_DIMS',
    'PROTOCOL_NAMES',
    'PROTOCOL_TRAITS',
    'RESOLUTION_TO_PM',
    'SPLIT_MODE_RESOLUTIONS',
    'SPLIT_OVERLAY_MAP',
    '_DEFAULT_PROFILE',
    'DeviceProfile',
    'HandshakeResult',
    'HidHandshakeInfo',
    'LCDDeviceConfig',
    'PlaybackState',
    'ProtocolTraits',
    'VideoState',
    'fbl_to_resolution',
    'get_encode_rotation',
    'get_profile',
    'panel_asset_dims',
    'pm_to_fbl',
]
