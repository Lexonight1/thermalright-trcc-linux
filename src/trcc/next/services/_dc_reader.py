"""Read-only DC-format theme config loader (legacy compatibility shim).

TRCC Windows + legacy Linux wrote themes as `config1.dc` — a binary
format with a magic byte (0xDC / 0xDD), version, enable flags, 13
font records, 13 element positions, and mask/rotation flags.

next/ writes theme configs as plain JSON going forward.  This reader
lets users load their existing DC-format themes; `ThemeService.load`
invokes it as a fallback, converts to our JSON-compatible dict, and
writes `trcc-next.json` alongside so the next load skips the binary
path.  The filename is deliberately distinct from legacy's
`config.json` — the two tools use different JSON shapes, and sharing
a filename would make whichever wrote last clobber the other.

Scope: the 20% of fields the overlay actually renders.  We skip the
mask rectangle, UI mode, charsets, and style bytes that legacy surfaces
through its full 800-LOC parser.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

from ..core.errors import ThemeError

log = logging.getLogger(__name__)


_MAGIC_DC = 0xDC   # standard theme format
_MAGIC_DD = 0xDD   # cloud-theme variant (variable-length element list)
_FONT_SLOTS = 13
_ELEMENT_SLOTS = 13


# Legacy slot order → our normalized sensor keys.  `None` means a label
# slot (the "CPU" / "GPU" / "MHz" string that sits next to a value).
_SLOT_MAP: list[tuple[str, str | None, str, str]] = [
    # (slot_name, metric_key_or_None, label_text, format_string)
    ("custom_text",       None,               "",      ""),
    ("cpu_temp",          "cpu:temp",         "CPU",   "{value:.0f}°C"),
    ("cpu_temp_label",    None,               "CPU",   ""),
    ("cpu_freq",          "cpu:freq",         "CPU",   "{value:.0f} MHz"),
    ("cpu_freq_label",    None,               "MHz",   ""),
    ("cpu_usage",         "cpu:usage",        "CPU",   "{value:.0f}%"),
    ("cpu_usage_label",   None,               "%",     ""),
    ("gpu_temp",          "gpu:primary:temp", "GPU",   "{value:.0f}°C"),
    ("gpu_temp_label",    None,               "GPU",   ""),
    ("gpu_clock",         "gpu:primary:clock","GPU",   "{value:.0f} MHz"),
    ("gpu_clock_label",   None,               "MHz",   ""),
    ("gpu_usage",         "gpu:primary:usage","GPU",   "{value:.0f}%"),
    ("gpu_usage_label",   None,               "%",     ""),
]


# 0xDD HARDWARE element (main_count, sub_count) → (sensor_id, format).
# Mirrors legacy core/models/sensor.py::HARDWARE_METRICS but emits
# next/-shape sensor IDs directly.
_HW_TO_SENSOR: dict[tuple[int, int], tuple[str, str]] = {
    # CPU (main=0)
    (0, 1): ("cpu:temp",            "{value:.0f}°C"),
    (0, 2): ("cpu:usage",           "{value:.0f}%"),
    (0, 3): ("cpu:freq",            "{value:.0f} MHz"),
    (0, 4): ("cpu:power",           "{value:.0f} W"),
    # GPU (main=1)
    (1, 1): ("gpu:primary:temp",    "{value:.0f}°C"),
    (1, 2): ("gpu:primary:usage",   "{value:.0f}%"),
    (1, 3): ("gpu:primary:clock",   "{value:.0f} MHz"),
    (1, 4): ("gpu:primary:power",   "{value:.0f} W"),
    # MEM (main=2)
    (2, 1): ("memory:percent",      "{value:.0f}%"),
    (2, 2): ("memory:clock",        "{value:.0f} MHz"),
    (2, 3): ("memory:available",    "{value:.0f} MB"),
    (2, 4): ("memory:temp",         "{value:.0f}°C"),
    # HDD (main=3)
    (3, 1): ("disk:0:read",         "{value:.0f} MB/s"),
    (3, 2): ("disk:0:write",        "{value:.0f} MB/s"),
    (3, 3): ("disk:0:activity",     "{value:.0f}%"),
    (3, 4): ("disk:0:temp",         "{value:.0f}°C"),
    # NET (main=4)
    (4, 1): ("net:down",            "{value:.0f} KB/s"),
    (4, 2): ("net:up",              "{value:.0f} KB/s"),
    (4, 3): ("net:total_down",      "{value:.0f} MB"),
    (4, 4): ("net:total_up",        "{value:.0f} MB"),
    # FAN (main=5)
    (5, 1): ("fan:cpu",             "{value:.0f} RPM"),
    (5, 2): ("fan:gpu",             "{value:.0f} RPM"),
    (5, 3): ("fan:ssd",             "{value:.0f} RPM"),
    (5, 4): ("fan:sys2",            "{value:.0f} RPM"),
}

# 0xDD element mode field (matches legacy OverlayMode IntEnum).
_MODE_HARDWARE = 0
_MODE_TIME = 1
_MODE_WEEKDAY = 2
_MODE_DATE = 3
_MODE_CUSTOM = 4


def load_dc_as_theme_config(path: Path) -> dict[str, Any]:
    """Read a `config1.dc` and return a JSON-compatible dict for ThemeService.

    Raises ThemeError on any parse failure.  Output shape:
        {
          "name": ...,
          "overlay_enabled": True,
          "rotation": 0,
          "background_display": True,
          "transparent_display": False,
          "elements": [
            {"type": "metric" | "text", "x": int, "y": int, ...},
            ...
          ]
        }
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ThemeError(f"Cannot read {path}: {e}") from e

    if not data:
        raise ThemeError(f"Empty DC file: {path}")
    magic = data[0]
    if magic not in (_MAGIC_DC, _MAGIC_DD):
        raise ThemeError(
            f"Not a DC file (magic byte 0x{magic:02x}): {path}"
        )
    try:
        if magic == _MAGIC_DD:
            return _parse_dd(data, path.parent.name)
        return _parse_dc(data, path.parent.name)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise ThemeError(f"Invalid DC file {path}: {e}") from e


# ── Internal: struct-based binary walker ─────────────────────────────


class _Reader:
    """Minimal sequential binary reader (subset of legacy BinaryReader)."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, start: int) -> None:
        self.data = data
        self.pos = start

    def read_int32(self) -> int:
        val = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_bool(self) -> bool:
        val = self.data[self.pos] != 0
        self.pos += 1
        return val

    def read_byte(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_float(self) -> float:
        val = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_string(self) -> str:
        if self.pos >= len(self.data):
            return ""
        length = self.data[self.pos]
        self.pos += 1
        if length <= 0 or self.pos + length > len(self.data):
            return ""
        try:
            s = self.data[self.pos:self.pos + length].decode("utf-8")
        except UnicodeDecodeError:
            s = ""
        self.pos += length
        return s


def _parse_dc(data: bytes, theme_name: str) -> dict[str, Any]:
    """Walk a 0xDC-format DC buffer; return our JSON-compatible dict."""
    r = _Reader(data, start=1)   # skip magic

    # header: version (i32) + reserved (i32)
    r.read_int32()
    r.read_int32()

    # 8 enable flags
    flag_custom = r.read_bool()
    r.read_bool()                                    # flag_sysinfo — unused in next/
    flag_cpu_temp = r.read_bool()
    flag_cpu_freq = r.read_bool()
    flag_cpu_usage = r.read_bool()
    flag_gpu_temp = r.read_bool()
    flag_gpu_clock = r.read_bool()
    flag_gpu_usage = r.read_bool()
    r.read_int32()                                   # reserved

    slot_enabled = {
        "custom_text": flag_custom,
        "cpu_temp": flag_cpu_temp,
        "cpu_temp_label": flag_cpu_temp,
        "cpu_freq": flag_cpu_freq,
        "cpu_freq_label": flag_cpu_freq,
        "cpu_usage": flag_cpu_usage,
        "cpu_usage_label": flag_cpu_usage,
        "gpu_temp": flag_gpu_temp,
        "gpu_temp_label": flag_gpu_temp,
        "gpu_clock": flag_gpu_clock,
        "gpu_clock_label": flag_gpu_clock,
        "gpu_usage": flag_gpu_usage,
        "gpu_usage_label": flag_gpu_usage,
    }

    # 13 font records.  Slot 0 carries the custom text string.
    fonts: list[dict[str, Any]] = []
    custom_text = ""
    for idx in range(_FONT_SLOTS):
        try:
            if idx == 0:
                custom_text = r.read_string()
            r.read_string()                          # font_name (unused; system default)
            size = _clamp_font_size(r.read_float())
            style = r.read_byte()                    # bit0 = bold, bit1 = italic
            r.read_byte()                            # unit
            r.read_byte()                            # charset
            alpha = r.read_byte()
            red = r.read_byte()
            green = r.read_byte()
            blue = r.read_byte()
            fonts.append({
                "size": size,
                "bold": bool(style & 0x01),
                "italic": bool(style & 0x02),
                "color": f"#{red:02x}{green:02x}{blue:02x}" if alpha else "#ffffff",
            })
        except (struct.error, IndexError):
            fonts.append({"size": 24, "bold": False, "italic": False, "color": "#ffffff"})

    # Display options
    try:
        background_display = r.read_bool()
        transparent_display = r.read_bool()
        rotation = r.read_int32()
        r.read_int32()                               # ui_mode (unused)
    except (struct.error, IndexError):
        background_display, transparent_display, rotation = True, False, 0

    # 13 (x, y) pairs — one per slot
    positions: list[tuple[int, int]] = []
    for _ in range(_ELEMENT_SLOTS):
        try:
            x = r.read_int32()
            y = r.read_int32()
        except (struct.error, IndexError):
            break
        positions.append((x, y))

    # Build element list
    elements: list[dict[str, Any]] = []
    for idx, (slot_name, metric_key, label, fmt) in enumerate(_SLOT_MAP):
        if idx >= len(positions):
            break
        if not slot_enabled.get(slot_name, True):
            continue
        x, y = positions[idx]
        font = fonts[idx] if idx < len(fonts) else {"size": 24, "bold": False,
                                                    "italic": False, "color": "#ffffff"}
        if slot_name == "custom_text":
            if not custom_text:
                continue
            elements.append({
                "type": "text",
                "x": x, "y": y, "text": custom_text,
                **font,
            })
        elif metric_key is None:
            elements.append({
                "type": "text",
                "x": x, "y": y, "text": label,
                **font,
            })
        else:
            elements.append({
                "type": "metric",
                "x": x, "y": y, "metric": metric_key, "format": fmt,
                **font,
            })

    return {
        "name": theme_name,
        "overlay_enabled": True,
        "rotation": rotation,
        "background_display": background_display,
        "transparent_display": transparent_display,
        "elements": elements,
    }


def _clamp_font_size(raw: float, default: float = 24.0) -> float:
    if 0 < raw < 100:
        return max(8.0, min(72.0, raw))
    return default


# ── 0xDD format (cloud themes) ───────────────────────────────────────


def _parse_dd(data: bytes, theme_name: str) -> dict[str, Any]:
    """Walk a 0xDD-format (cloud-theme) DC buffer.

    Layout differs from 0xDC: instead of fixed slots, 0xDD carries a
    variable-length list of typed elements (HARDWARE / TIME / WEEKDAY /
    DATE / CUSTOM).  Trailer block (display options + mask settings) is
    optional.

    Time / weekday / date elements emit ``type: "clock"`` with a
    ``source`` discriminator; OverlayService resolves them per-frame
    against the device's time/date format and the app language.
    Hardware and custom elements render correctly today.
    """
    r = _Reader(data, start=1)   # skip magic
    r.read_bool()                # system_info flag (unused in next/)

    count = r.read_int32()
    if count < 0 or count > 100:
        raise ThemeError(
            f"0xDD element count out of range: {count}",
        )

    elements: list[dict[str, Any]] = []
    for _ in range(count):
        mode = r.read_int32()
        mode_sub = r.read_int32()
        x = r.read_int32()
        y = r.read_int32()
        main_count = r.read_int32()
        sub_count = r.read_int32()
        font = _read_dd_font(r)
        custom_text = r.read_string()

        if (element := _build_dd_element(
            mode, mode_sub, x, y, main_count, sub_count, font, custom_text,
        )) is not None:
            elements.append(element)

    # Optional trailer — bail gracefully if file truncates here.
    background_display = True
    transparent_display = False
    rotation = 0
    overlay_enabled = True
    try:
        background_display = r.read_bool()
        transparent_display = r.read_bool()
        rotation = r.read_int32()
        r.read_int32()                              # ui_mode
        r.read_int32()                              # mode
        overlay_enabled = r.read_bool()
        for _ in range(4):
            r.read_int32()                          # overlay rect: x, y, w, h
        r.read_bool()                               # mask_enabled
        for _ in range(2):
            r.read_int32()                          # mask position: x, y
    except (struct.error, IndexError):
        pass

    return {
        "name": theme_name,
        "overlay_enabled": overlay_enabled,
        "rotation": rotation,
        "background_display": background_display,
        "transparent_display": transparent_display,
        "elements": elements,
    }


def _read_dd_font(r: _Reader) -> dict[str, Any]:
    """Read the font/color record that follows every 0xDD element."""
    r.read_string()                                 # font_name (unused)
    size = _clamp_font_size(r.read_float())
    style = r.read_byte()                           # bit0=bold, bit1=italic
    r.read_byte()                                   # font_unit
    r.read_byte()                                   # font_charset
    alpha = r.read_byte()
    red = r.read_byte()
    green = r.read_byte()
    blue = r.read_byte()
    return {
        "size": size,
        "bold": bool(style & 0x01),
        "italic": bool(style & 0x02),
        "color": f"#{red:02x}{green:02x}{blue:02x}" if alpha else "#ffffff",
    }


def _build_dd_element(
    mode: int,
    mode_sub: int,
    x: int,
    y: int,
    main_count: int,
    sub_count: int,
    font: dict[str, Any],
    custom_text: str,
) -> dict[str, Any] | None:
    """Translate one parsed 0xDD element into next/'s overlay-element dict."""
    base: dict[str, Any] = {"x": x, "y": y, **font}
    match mode:
        case 0:  # HARDWARE
            entry = _HW_TO_SENSOR.get((main_count, sub_count))
            if entry is None:
                log.debug(
                    "0xDD HARDWARE element (%d, %d) has no sensor mapping; skipping",
                    main_count, sub_count,
                )
                return None
            sensor_id, fmt = entry
            return {**base, "type": "metric", "metric": sensor_id, "format": fmt}
        case 4:  # CUSTOM
            if not custom_text:
                return None
            return {**base, "type": "text", "text": custom_text}
        case 1:  # TIME
            return {**base, "type": "clock", "source": "time"}
        case 2:  # WEEKDAY
            return {**base, "type": "clock", "source": "weekday"}
        case 3:  # DATE
            return {**base, "type": "clock", "source": "date"}
        case _:
            log.debug("0xDD: unknown element mode %d; skipping", mode)
            return None
