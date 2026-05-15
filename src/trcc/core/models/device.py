"""Device models — entries, registries, detection, device info, button images."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import _DEFAULT_PROFILE, BULK_RGB565_FBLS, DeviceProfile, get_profile

# Default sidebar button images — matches C# fallback for unknown PM.
LCD_DEFAULT_BUTTON = "A1CZTV"
LED_DEFAULT_BUTTON = "A1KVMALEDC6"

# DeviceInfo runtime-state defaults — named so the dataclass declarations
# read like prose instead of magic numbers.
_DEFAULT_RESOLUTION: tuple[int, int] = (0, 0)
_DEFAULT_BRIGHTNESS = 65    # 0–100% — C# default
_DEFAULT_ROTATION = 0       # 0 / 90 / 180 / 270


@dataclass(frozen=True, slots=True)
class DeviceEntry:
    """Registry entry describing a known USB device's capabilities."""
    vendor: str
    product: str
    implementation: str
    model: str = "CZTV"
    button_image: str = LCD_DEFAULT_BUTTON
    protocol: str = "scsi"
    device_type: int = 1  # 1=SCSI, 2=HID Type 2, 3=HID Type 3, 4=Raw USB Bulk
    fbl: int = 100         # FBL code (resolution identifier) — used by Windows SCSI poll fallback


@dataclass(frozen=True, slots=True)
class PanelCutout:
    """Rectangular cutout on the panel (e.g. smartphone-display camera notch).

    Hardware fact — registry data, not user preference. Populated on a
    ``VariantOverride`` when the panel has an obstructed region the render
    pipeline should mask (the 灵动岛 / Dynamic Island feature).
    """
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class VariantOverride:
    """Per-(PM, SUB) overrides applied after handshake.

    Resolves cases where one (VID, PID) ships as multiple physical products
    sharing a chipset — the handshake fingerprint distinguishes them.
    Currently carries the sidebar button image; ``panel_cutout`` and
    ``display_name`` are populated by registry rows that need them.
    """
    button_image: str = ""
    panel_cutout: PanelCutout | None = None
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class UsbAddress:
    """Bus + address — a unique physical USB device location.

    Two coolers with identical VID:PID still have distinct (bus, address)
    pairs, so threading this through the transport open path lets each
    protocol bind to its own physical device (issue #128).
    """
    bus: int
    address: int

    @classmethod
    def parse(cls, usb_path: str) -> UsbAddress | None:
        """Parse 'usb:5:2' into UsbAddress(5, 2). Returns None on bad input."""
        match usb_path.split(':'):
            case ['usb', bus, address]:
                try:
                    return cls(int(bus), int(address))
                except ValueError:
                    return None
        return None

    def matches(self, dev: Any) -> bool:
        """pyusb `custom_match` — bind only to this physical device."""
        return dev.bus == self.bus and dev.address == self.address

    def __str__(self) -> str:
        return f"usb:{self.bus}:{self.address}"


@dataclass(slots=True)
class DetectedDevice:
    """Raw OS-level discovery — what one of the per-OS detectors finds.

    Convert to ``DeviceInfo`` via ``DeviceInfo.from_detected`` (the single
    sanctioned conversion site, which guarantees ``usb_address`` is set).
    """
    vid: int                                    # Vendor ID
    pid: int                                    # Product ID
    vendor_name: str
    product_name: str
    usb_path: str                               # e.g., "usb:5:2"
    scsi_device: str | None = None              # e.g., "/dev/sg0"
    implementation: str = "generic"             # Device-specific implementation
    model: str = "CZTV"                         # Device model for button image lookup
    button_image: str = LCD_DEFAULT_BUTTON      # Sidebar image prefix
    protocol: str = "scsi"                      # "scsi" / "hid" / "bulk" / "ly" / "led"
    device_type: int = 1                        # 1=SCSI, 2=HID Type 2, 3=HID Type 3, 4=Bulk, 5=LY

    @property
    def path(self) -> str:
        """Device path for protocol factories (SCSI → /dev/sgN, else USB path)."""
        return self.scsi_device or self.usb_path

    @property
    def usb_address(self) -> UsbAddress | None:
        """Physical USB ``(bus, address)`` parsed from ``usb_path``; ``None`` for SCSI-only."""
        return UsbAddress.parse(self.usb_path)


# =========================================================================
# Device registries — single source of truth for all known USB devices
# =========================================================================

SCSI_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x87CD, 0x70DB): DeviceEntry(
        vendor="Thermalright", product="LCD Display",
        implementation="thermalright_lcd_v1",
    ),
    (0x0416, 0x5406): DeviceEntry(
        vendor="Winbond", product="LCD Display",
        implementation="ali_corp_lcd_v1",
    ),
    (0x0402, 0x3922): DeviceEntry(
        vendor="Thermalright", product="LCD Display",
        model="FROZEN_WARFRAME", button_image=LCD_DEFAULT_BUTTON,
        implementation="ali_corp_lcd_v1",
    ),
}

HID_LCD_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x0416, 0x5302): DeviceEntry(
        vendor="Winbond", product="USBDISPLAY",
        implementation="hid_type2", protocol="hid", device_type=2,
    ),
    (0x0418, 0x5303): DeviceEntry(
        vendor="ALi Corp", product="LCD Display",
        implementation="hid_type3", protocol="hid", device_type=3,
    ),
    (0x0418, 0x5304): DeviceEntry(
        vendor="ALi Corp", product="LCD Display",
        implementation="hid_type3", protocol="hid", device_type=3,
    ),
}

LED_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x0416, 0x8001): DeviceEntry(
        vendor="Winbond", product="LED Controller",
        model="LED_DIGITAL", implementation="hid_led",
        protocol="led", device_type=1,
        button_image=LED_DEFAULT_BUTTON,
    ),
}

BULK_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x87AD, 0x70DB): DeviceEntry(
        vendor="ChiZhu Tech", product="GrandVision 360 AIO",
        model="GRAND_VISION", button_image="A1GRAND VISION",
        implementation="bulk_usblcdnew",
        protocol="bulk", device_type=4,
    ),
}

LY_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x0416, 0x5408): DeviceEntry(
        vendor="Winbond", product="Trofeo Vision 9.16 LCD",
        implementation="ly_bulk", protocol="ly", device_type=5,
    ),
    (0x0416, 0x5409): DeviceEntry(
        vendor="Winbond", product="Trofeo Vision 9.16 LCD",
        implementation="ly_bulk", protocol="ly", device_type=5,
    ),
}

ALL_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    **SCSI_DEVICES, **HID_LCD_DEVICES, **LED_DEVICES, **BULK_DEVICES, **LY_DEVICES,
}


@dataclass(slots=True)
class DeviceInfo:
    """Runtime device info — derived from a ``DetectedDevice`` plus handshake state.

    Construct only via the three sanctioned classmethods:
      • ``DeviceInfo.from_detected``  — the conversion chokepoint
      • ``DeviceInfo.from_dict``      — legacy dict input
      • ``DeviceInfo.from_wire_dict`` — IPC reconstruction

    Each guarantees ``usb_address`` is parsed, so the factory's
    ``device_info.usb_address`` access never raises ``AttributeError`` and
    multi-device disambiguation (#128) always has the data it needs.
    """
    name: str
    path: str                                   # /dev/sgX (SCSI) or "usb:bus:addr" (USB)
    resolution: tuple[int, int] = _DEFAULT_RESOLUTION

    # Identity (mirrors DetectedDevice)
    vendor: str | None = None
    product: str | None = None
    model: str | None = None
    vid: int = 0
    pid: int = 0
    usb_address: UsbAddress | None = None       # physical USB (bus, address); None for SCSI
    device_index: int = 0                       # 0-based ordinal among detected devices
    fbl_code: int | None = None                 # Resolution identifier
    protocol: str = "scsi"                      # "scsi" / "hid" / "bulk" / "ly" / "led"
    device_type: int = 1                        # 1=SCSI, 2=HID T2, 3=HID T3
    implementation: str = "generic"             # "thermalright_lcd_v1", "hid_type2", "hid_led", ...
    button_image: str = LCD_DEFAULT_BUTTON

    # Handshake-derived
    pm_byte: int = 0                            # PM from handshake → button image lookup
    sub_byte: int = 0                           # SUB from handshake → encode rotation
    led_style_id: int | None = None             # LED style from probe
    led_style_sub: int = 0                      # LED style sub-variant

    # Runtime state
    connected: bool = True
    brightness: int = _DEFAULT_BRIGHTNESS
    rotation: int = _DEFAULT_ROTATION

    # ── Conversion chokepoints — the only sanctioned constructors ────────

    @classmethod
    def from_detected(cls, d: DetectedDevice, device_index: int = 0) -> DeviceInfo:
        """**THE** ``DetectedDevice → DeviceInfo`` conversion site.

        Guarantees ``usb_address`` is populated from the source's USB path.
        Every code path that turns raw discovery into runtime device info
        funnels through here — closes the bug class behind #130/#131.
        """
        return cls(
            name=f"{d.vendor_name} {d.product_name}",
            path=d.path,
            vendor=d.vendor_name,
            product=d.product_name,
            model=d.model,
            vid=d.vid,
            pid=d.pid,
            usb_address=d.usb_address,
            device_index=device_index,
            protocol=d.protocol,
            device_type=d.device_type,
            implementation=d.implementation,
            button_image=d.button_image,
        )

    @classmethod
    def from_dict(cls, d: dict) -> DeviceInfo:
        """Create DeviceInfo from a detection dict (find_lcd_devices output)."""
        return cls(
            name=d.get('name', 'LCD'),
            path=d.get('path', ''),
            resolution=d.get('resolution', _DEFAULT_RESOLUTION),
            vendor=d.get('vendor'),
            product=d.get('product'),
            model=d.get('model'),
            vid=d.get('vid', 0),
            pid=d.get('pid', 0),
            usb_address=UsbAddress.parse(d.get('path', '')),
            device_index=d.get('device_index', 0),
            protocol=d.get('protocol', 'scsi'),
            device_type=d.get('device_type', 1),
            implementation=d.get('implementation', 'generic'),
            led_style_id=d.get('led_style_id'),
            led_style_sub=d.get('led_style_sub', 0),
        )

    def to_wire_dict(self) -> dict:
        """Serialize for IPC transport.

        ``dataclasses.asdict`` recursively dictifies nested dataclasses
        (notably ``usb_address: UsbAddress``) and turns tuples into lists —
        both round-trip cleanly through JSON. Pair with ``from_wire_dict``.
        """
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_wire_dict(cls, d: dict) -> DeviceInfo:
        """Reconstruct from a ``to_wire_dict`` payload.

        Restores the nested ``UsbAddress`` dataclass and converts the
        ``resolution`` list back to a tuple — JSON has no tuple type.
        """
        addr_dict = d.get('usb_address')
        usb_address = UsbAddress(**addr_dict) if addr_dict else None
        res = d.get('resolution', _DEFAULT_RESOLUTION)
        return cls(
            name=d.get('name', ''),
            path=d.get('path', ''),
            resolution=(res[0], res[1]) if isinstance(res, list | tuple) else _DEFAULT_RESOLUTION,
            vendor=d.get('vendor'),
            product=d.get('product'),
            model=d.get('model'),
            vid=d.get('vid', 0),
            pid=d.get('pid', 0),
            usb_address=usb_address,
            device_index=d.get('device_index', 0),
            fbl_code=d.get('fbl_code'),
            protocol=d.get('protocol', 'scsi'),
            device_type=d.get('device_type', 1),
            implementation=d.get('implementation', 'generic'),
            button_image=d.get('button_image', LCD_DEFAULT_BUTTON),
            pm_byte=d.get('pm_byte', 0),
            sub_byte=d.get('sub_byte', 0),
            led_style_id=d.get('led_style_id'),
            led_style_sub=d.get('led_style_sub', 0),
            connected=d.get('connected', True),
            brightness=d.get('brightness', _DEFAULT_BRIGHTNESS),
            rotation=d.get('rotation', _DEFAULT_ROTATION),
        )

    @property
    def resolution_str(self) -> str:
        """Get resolution as string (e.g., '320x320')."""
        return f"{self.resolution[0]}x{self.resolution[1]}"

    @property
    def profile(self) -> DeviceProfile:
        """Device profile derived from FBL code."""
        return get_profile(self.fbl_code) if self.fbl_code is not None else _DEFAULT_PROFILE

    @property
    def use_jpeg(self) -> bool:
        """Whether this device uses JPEG encoding.

        Bulk/LY: JPEG unless FBL is RGB565-only (e.g. FBL 100).
        HID: JPEG if profile says so. SCSI: always RGB565.
        """
        if self.protocol in ('bulk', 'ly'):
            return self.fbl_code not in BULK_RGB565_FBLS
        return self.profile.jpeg if self.protocol == 'hid' else False

    @property
    def encoding_params(self) -> tuple:
        """Encoding params for ImageService.encode_for_device().

        Returns (protocol, resolution, fbl, use_jpeg).
        """
        res = self.resolution
        fbl = self.fbl_code
        if res == (0, 0):
            res = self.profile.resolution
        return (self.protocol, res, fbl, self.use_jpeg)

    # ── Derived geometry — pure functions of resolution + rotation ──────

    @property
    def is_rotated(self) -> bool:
        """True when content is rotated 90°/270° on a non-square device."""
        w, h = self.resolution
        return w != h and self.rotation in (90, 270)

    @property
    def canvas_size(self) -> tuple[int, int]:
        """Render-target size — native, swapped on 90°/270° non-square rotation.

        Single source of truth for the rotated canvas. The C# code carried
        ``is1600x720`` flags directly on the form because the data lived on
        one object; this is the same idea expressed as derived data on the
        DTO. No service mirror state — read straight from the DeviceInfo.
        """
        w, h = self.resolution
        return (h, w) if self.is_rotated else (w, h)

    @property
    def is_widescreen_split(self) -> bool:
        """True if this device's resolution supports split mode (Dynamic Island)."""
        from .protocol import SPLIT_MODE_RESOLUTIONS
        return self.resolution in SPLIT_MODE_RESOLUTIONS

    # ── Derived handshake-fingerprint helpers ───────────────────────────

    @property
    def effective_pm(self) -> int:
        """PM byte, falling back to FBL code when PM=0.

        Some bulk devices report PM=0 and rely on FBL alone to identify
        the panel; this captures the ``pm_byte or fbl_code or 0`` pattern
        that was previously inlined at every variant-resolution site.
        """
        return self.pm_byte or self.fbl_code or 0

    @property
    def encode_angle(self) -> int:
        """Device encode rotation angle (C# RotateImg in ImageToJpg)."""
        from .protocol import get_encode_rotation
        if self.fbl_code is None:
            return 0
        return get_encode_rotation(
            self.profile, self.sub_byte, self.rotation, pm_byte=self.pm_byte,
        )

    # ── Post-handshake enrichment chokepoint ─────────────────────────────

    def enrich_from_handshake(self, result: Any) -> None:
        """Apply a ``HandshakeResult`` to this DeviceInfo (LCD path).

        The single sanctioned site that copies handshake-derived fields onto
        a ``DeviceInfo`` and resolves the (PM, SUB) → ``VariantOverride``
        lookup (button image today; ``panel_cutout`` / ``display_name`` once
        Phase B fills those rows). Replaces the prior scattered field-by-field
        mutations across ``services/device.py`` and ``ui/gui/trcc_app.py``.

        Tolerant of partial results: each field is copied only if the
        handshake produced a meaningful value, so unknown protocols (or
        failed handshakes that returned ``HandshakeResult(raw_response=...)``)
        leave the prior state intact.
        """
        if result is None:
            return

        res = getattr(result, 'resolution', None)
        if isinstance(res, tuple) and len(res) == 2 and res != (0, 0):
            self.resolution = res

        fbl = getattr(result, 'fbl', None) or getattr(result, 'model_id', None)
        if fbl:
            self.fbl_code = fbl

        # PM=0 / SUB=0 are legitimate (some entries are keyed under PM=0
        # SUB=0). Always copy.
        self.pm_byte = getattr(result, 'pm_byte', 0)
        self.sub_byte = getattr(result, 'sub_byte', 0)

        # Resolve sidebar identity from the (PM, SUB) fingerprint. Falls back
        # to FBL when PM=0 (some bulk devices). Phase B will also use the
        # full VariantOverride to populate panel_cutout + display_name.
        if (pm := self.effective_pm):
            override = get_variant_override(pm, self.sub_byte)
            if override is not None and override.button_image:
                self.button_image = override.button_image

    def enrich_from_led_probe(self, probe: Any) -> None:
        """Apply an ``LedHandshakeInfo`` to this DeviceInfo (LED path).

        Sister to ``enrich_from_handshake``; consumes the LED probe shape
        (has ``pm``, ``sub_type``, ``style``, ``style_sub`` instead of the
        bulk handshake's ``pm_byte`` / ``sub_byte``). Resolves the LED
        button-image variant in the same single step.

        Tolerant of partial probes: ``probe.style`` may be ``None`` if the
        device doesn't match any known LED style — the method leaves prior
        state intact in that case.
        """
        if probe is None or getattr(probe, 'style', None) is None:
            return
        self.pm_byte = getattr(probe, 'pm', 0)
        self.sub_byte = getattr(probe, 'sub_type', 0)
        self.led_style_id = probe.style.style_id
        self.led_style_sub = getattr(probe, 'style_sub', 0)
        self.model = probe.style.model_name

        if self.pm_byte:
            override = get_variant_override(self.pm_byte, self.sub_byte,
                                             is_led=True)
            if override is not None and override.button_image:
                self.button_image = override.button_image


# Implementation key → display name (SCSI LCD devices)
IMPL_NAMES: dict[str, str] = {
    "thermalright_lcd_v1": "Thermalright LCD v1 (USBLCD)",
    "ali_corp_lcd_v1": "ALi Corp LCD v1 (USBLCD)",
    "generic": "Generic LCD",
}


# =============================================================================
# Device Button Image Map (from UCDevice.cs ADDUserButton)
# =============================================================================

# ──────────────────────────────────────────────────────────────────────────
# Variant override registry — keyed by (pm, sub) from handshake.
#
# Phase B.0: each entry carries `button_image`. Phase B fills `panel_cutout`
# and `display_name` on rows where the panel has a notch or a distinct
# physical product variant (e.g. Levita vs GrandVision 360 sharing 87ad:70db).
#
# These dicts replaced earlier `dict[int, dict[int|None, str]]` shapes —
# same nesting, richer leaf value (VariantOverride).
# ──────────────────────────────────────────────────────────────────────────

def _v(button_image: str) -> VariantOverride:
    """Shorthand for VariantOverride rows that carry only a button image."""
    return VariantOverride(button_image=button_image)


_LCD_BUTTON_IMAGE: dict[int, dict[int | None, VariantOverride]] = {
    1:   {0: _v('A1GRAND VISION'), 1: _v('A1GRAND VISION'),
          48: _v('A1LM22'), 49: _v('A1LF14'), None: _v('A1GRAND VISION')},
    3:   {None: _v('A1CORE VISION')},
    4:   {1: _v('A1HYPER VISION'), 2: _v('A1RP130 VISION'), 3: _v('A1LM16SE'),
          4: _v('A1LF10V'), 5: _v('A1LM19SE')},
    5:   {None: _v('A1Mjolnir VISION')},
    6:   {1: _v('frozen_warframe_ultra'), 2: _v('A1FROZEN VISION V2')},
    7:   {1: _v('A1Stream Vision'), 2: _v('A1Mjolnir VISION PRO')},
    9:   {0: _v('A1LC2JD'), 1: _v('A1LC2JD'), 2: _v('A1LC2JD'), 3: _v('A1LC2JD'),
          4: _v('A1LC2JD'), None: _v('A1LF19')},
    10:  {5: _v('A1LF16'), 6: _v('A1LF18'), 7: _v('A1LD6'), None: _v('A1LC3')},
    11:  {6: _v('A1LD8'), None: _v('A1LF19')},
    12:  {None: _v('A1LF167')},
    13:  {None: _v('A1PC1')},
    14:  {1: _v('A1Stream Vision'), 2: _v('A1Mjolnir VISION PRO')},
    15:  {2: _v('A1LC8'), None: _v('A1LC7')},
    16:  {None: _v('A1CZ2')},
    17:  {1: _v('A1PC1'), 2: _v('A1LC9'), 5: _v('A1PC1'), None: _v('A1PC1')},
    32:  {0: _v('A1ELITE VISION'), 1: _v('A1FROZEN WARFRAME PRO'),
          None: _v('A1ELITE VISION')},
    36:  {None: _v('A1AS120 VISION')},
    49:  {None: _v('A1FROZEN WARFRAME')},
    50:  {None: _v('A1FROZEN WARFRAME')},
    51:  {None: _v('A1FROZEN WARFRAME')},
    52:  {None: _v('A1BA120 VISION')},
    53:  {1: _v('A1LF21'), 2: _v('A1LF22'), None: _v('A1LF20')},
    54:  {None: _v('A1LC5')},
    58:  {0: _v('A1FROZEN WARFRAME SE'), None: _v('A1LM26')},
    63:  {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'), 2: _v('A1LM27'),
          3: _v('A1LM30')},
    64:  {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'), 2: _v('A1LM27'),
          3: _v('A1LM30')},
    65:  {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), 2: _v('A1LF14'), 3: _v('A1LD7'),
          4: _v('A1LD10'), 5: _v('A1LD7')},
    66:  {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), 2: _v('A1LF14'),
          3: _v('A1LD7'), 4: _v('A1LD7')},
    68:  {None: _v('A1LM24')},
    69:  {2: _v('A1LD9')},
    100: {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'),
          None: _v('A1FROZEN WARFRAME PRO')},
    101: {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), None: _v('A1ELITE VISION')},
    128: {None: _v('A1LM24')},
    129: {None: _v('A1GRAND VISION')},
}

_LED_BUTTON_IMAGE: dict[int, dict[int | None, VariantOverride]] = {
    1:   {None: _v('A1FROZEN HORIZON PRO')},
    2:   {None: _v('A1FROZEN MAGIC PRO')},
    3:   {None: _v('A1AX120 DIGITAL')},
    16:  {None: _v('A1PA120 DIGITAL')},
    23:  {None: _v('A1RK120 DIGITAL')},
    32:  {None: _v('A1AK120 Digital')},
    48:  {None: _v('A1LF8')},
    49:  {None: _v('A1LF10')},
    80:  {None: _v('A1LF12')},
    96:  {None: _v('A1LF10')},
    112: {None: _v('A1LC2')},
    128: {None: _v('A1LC1')},
    129: {None: _v('A1LF11')},
    144: {None: _v('A1LF15')},
    160: {None: _v('A1LF13')},
    176: {None: _v('A1LF25')},
    208: {None: _v('A1CZ1')},
    **{pm: {None: _v('A1PA120 DIGITAL')} for pm in range(17, 32) if pm != 23},
}


def _resolve_variant(table: dict[int, dict[int | None, VariantOverride]],
                     key: int, sub: int) -> VariantOverride | None:
    """Look up a VariantOverride by (PM key, SUB), preferring exact match over None default."""
    match table.get(key):
        case None:
            return None
        case sub_map if sub in sub_map:
            return sub_map[sub]
        case sub_map:
            return sub_map.get(None)


def get_variant_override(key: int, sub: int = 0, *,
                          is_led: bool = False) -> VariantOverride | None:
    """Resolve full VariantOverride record for a (PM, SUB) handshake fingerprint.

    Args:
        key: PM byte (HID/Bulk) or FBL fallback when PM=0.
        sub: SUB byte (HID/Bulk).
        is_led: True for LED devices, False for LCD.
    """
    return _resolve_variant(
        _LED_BUTTON_IMAGE if is_led else _LCD_BUTTON_IMAGE, key, sub)


def get_button_image(key: int, sub: int = 0, *, is_led: bool = False) -> str | None:
    """Resolve device button image from PM+SUB (HID) or VID+PID (SCSI).

    Args:
        key: PM byte (HID) or VID (SCSI).
        sub: SUB byte (HID) or PID (SCSI).
        is_led: True for LED devices (C# case 1), False for LCD (cases 2-4, 257).
    """
    override = get_variant_override(key, sub, is_led=is_led)
    return override.button_image if override is not None else None


__all__ = [
    'ALL_DEVICES',
    'BULK_DEVICES',
    'HID_LCD_DEVICES',
    'IMPL_NAMES',
    'LCD_DEFAULT_BUTTON',
    'LED_DEFAULT_BUTTON',
    'LED_DEVICES',
    'LY_DEVICES',
    'SCSI_DEVICES',
    'DetectedDevice',
    'DeviceEntry',
    'DeviceInfo',
    'PanelCutout',
    'UsbAddress',
    'VariantOverride',
    'get_button_image',
    'get_variant_override',
]
