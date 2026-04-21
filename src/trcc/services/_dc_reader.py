"""Read-only DC-format theme config loader (legacy compatibility shim).

TRCC Windows + legacy Linux wrote themes as `config1.dc` — a binary
format with a magic byte (0xDC / 0xDD), version, enable flags, 13
font records, 13 element positions, and mask/rotation flags.

next/ writes theme configs as plain JSON going forward.  This reader
lets users load their existing DC-format themes; `ThemeService.load`
invokes it as a fallback, converts to our JSON-compatible dict, and
writes `config.json` alongside so the next load skips the binary path.

Scope: the 20% of fields the overlay actually renders.  We skip the
mask rectangle, UI mode, charsets, and style bytes that legacy surfaces
through its full 800-LOC parser.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import ThemeError

log = logging.getLogger(__name__)


_MAGIC_DC = 0xDC   # standard theme format
_MAGIC_DD = 0xDD   # cloud-theme variant (unsupported for now)
_FONT_SLOTS = 13
_ELEMENT_SLOTS = 13


# Legacy slot order → our normalized sensor keys.  `None` means a label
# slot (the "CPU" / "GPU" / "MHz" string that sits next to a value).
_SLOT_MAP: List[Tuple[str, Optional[str], str, str]] = [
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


def load_dc_as_theme_config(path: Path) -> Dict[str, Any]:
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
    if magic == _MAGIC_DD:
        raise ThemeError(
            "Cloud-theme (0xDD) DC format not yet supported; "
            "load the decompressed theme from its source."
        )

    try:
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


def _parse_dc(data: bytes, theme_name: str) -> Dict[str, Any]:
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
    fonts: List[Dict[str, Any]] = []
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
    positions: List[Tuple[int, int]] = []
    for _ in range(_ELEMENT_SLOTS):
        try:
            x = r.read_int32()
            y = r.read_int32()
        except (struct.error, IndexError):
            break
        positions.append((x, y))

    # Build element list
    elements: List[Dict[str, Any]] = []
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
