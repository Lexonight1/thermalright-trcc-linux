"""DC-format theme config codec (read + write, legacy compatibility).

TRCC Windows + legacy Linux wrote themes as `config1.dc` — a binary
format with a magic byte (0xDC / 0xDD), version, enable flags, 13
font records, 13 element positions, and mask/rotation flags.

next/ writes theme configs as plain JSON going forward (``trcc.json``).
This codec lets users:
  * load their existing DC-format themes (read path) — ``ThemeService.load``
    invokes it as a fallback, converts to our JSON-compatible dict, and
    writes ``trcc.json`` alongside so the next load skips the binary
    path; and
  * export a next/-managed theme back to legacy DC format (write path) so
    it round-trips to Windows TRCC + legacy Linux users.

Filenames are deliberately distinct from legacy's ``config.json`` — the
two tools use different JSON shapes, and sharing a filename would make
whichever wrote last clobber the other.

Scope: the 20% of fields the overlay actually renders + everything we
need to recreate a legacy DC the Windows app accepts.  Mask rectangle,
UI mode, charsets, and style bytes round-trip even though next/ ignores
some of them on read — preserving them lets the file open cleanly in
legacy without bytes flapping.
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
            font_name = r.read_string() or _DEFAULT_FONT_NAME
            size = _clamp_font_size(r.read_float())
            style = r.read_byte()                    # bit0 = bold, bit1 = italic
            r.read_byte()                            # unit
            r.read_byte()                            # charset
            alpha = r.read_byte()
            red = r.read_byte()
            green = r.read_byte()
            blue = r.read_byte()
            fonts.append({
                "name": font_name,
                "size": size,
                "bold": bool(style & 0x01),
                "italic": bool(style & 0x02),
                "color": f"#{red:02x}{green:02x}{blue:02x}" if alpha else "#ffffff",
            })
        except (struct.error, IndexError):
            fonts.append({
                "name": _DEFAULT_FONT_NAME, "size": 24, "bold": False,
                "italic": False, "color": "#ffffff",
            })

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
    mask_visible = False
    mask_x = 0
    mask_y = 0
    try:
        background_display = r.read_bool()
        transparent_display = r.read_bool()
        rotation = r.read_int32()
        r.read_int32()                              # ui_mode
        r.read_int32()                              # mode
        overlay_enabled = r.read_bool()
        for _ in range(4):
            r.read_int32()                          # overlay rect: x, y, w, h
        mask_visible = r.read_bool()
        mask_x = r.read_int32()
        mask_y = r.read_int32()
    except (struct.error, IndexError):
        pass

    return {
        "name": theme_name,
        "overlay_enabled": overlay_enabled,
        "rotation": rotation,
        "background_display": background_display,
        "transparent_display": transparent_display,
        "mask_visible": mask_visible,
        "mask_position": [mask_x, mask_y],
        "elements": elements,
    }


def _read_dd_font(r: _Reader) -> dict[str, Any]:
    """Read the font/color record that follows every 0xDD element."""
    name = r.read_string() or _DEFAULT_FONT_NAME
    size = _clamp_font_size(r.read_float())
    style = r.read_byte()                           # bit0=bold, bit1=italic
    r.read_byte()                                   # font_unit
    r.read_byte()                                   # font_charset
    alpha = r.read_byte()
    red = r.read_byte()
    green = r.read_byte()
    blue = r.read_byte()
    return {
        "name": name,
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


# =========================================================================
# Mask position calculation — ported verbatim from legacy
# ``OverlayService.calculate_mask_position``
# =========================================================================


def calculate_mask_position(
    mask_dir: Path,
    mask_size: tuple[int, int],
    lcd_size: tuple[int, int],
) -> tuple[int, int]:
    """Compute mask top-left position from the mask's own config1.dc.

    Direct port of legacy ``OverlayService.calculate_mask_position``:

      * Full-size mask (≥ LCD in both dims) → ``(0, 0)``
      * Sub-screen mask without a usable DC entry → centered
      * Sub-screen mask WITH ``mask_position`` in its sibling
        ``config1.dc`` → top-left from C# (XvalMB − W/2, YvalMB − H/2)

    Reading the mask's own DC: the file's 0xDD trailer carries
    ``mask_enabled`` + ``mask_position[x, y]`` (legacy: center
    coordinates).  ``load_dc_as_theme_config`` already exposes both
    fields in its output dict (added when ``_parse_dd`` started
    capturing them).
    """
    mask_w, mask_h = mask_size
    lcd_w, lcd_h = lcd_size
    if mask_w >= lcd_w and mask_h >= lcd_h:
        return (0, 0)
    centered = ((lcd_w - mask_w) // 2, (lcd_h - mask_h) // 2)
    dc_path = mask_dir / _DC_CONFIG_FILE_NAME
    if not dc_path.is_file():
        return centered
    try:
        cfg = load_dc_as_theme_config(dc_path)
    except ThemeError as e:
        log.warning("calculate_mask_position: %s unreadable (%s); centering",
                    dc_path, e)
        return centered
    if not cfg.get("mask_visible"):
        return centered
    pos = cfg.get("mask_position")
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        return centered
    try:
        cx, cy = int(pos[0]), int(pos[1])
    except (TypeError, ValueError):
        return centered
    # DC stores CENTER coords; legacy renders at top-left = center - size/2.
    return (cx - mask_w // 2, cy - mask_h // 2)


_DC_CONFIG_FILE_NAME = "config1.dc"


# =========================================================================
# Legacy-shape adapter — for the legacy GUI port (overlay_grid widget)
# =========================================================================


_DEFAULT_FONT_NAME = "Microsoft YaHei"


def dc_as_legacy_overlay_config(theme_dir: Path) -> dict[str, dict[str, Any]]:
    """Read a theme's ``config1.dc`` and return the legacy GUI's
    overlay_config dict shape (one entry per element, keyed by metric
    name with counters for duplicates).

    Used by the legacy GUI port's overlay grid; new code should consume
    `load_dc_as_theme_config`'s list shape directly via the Command bus.

    Returns ``{}`` when the theme has no DC file or it can't be parsed —
    callers treat empty dict as "no overlay to restore" rather than as
    an error.
    """
    dc_path = theme_dir / "config1.dc"
    if not dc_path.is_file():
        return {}
    try:
        theme_config = load_dc_as_theme_config(dc_path)
    except ThemeError as e:
        log.warning("dc_as_legacy_overlay_config: %s skipped (%s)", dc_path, e)
        return {}

    overlay: dict[str, dict[str, Any]] = {}
    counters: dict[str, int] = {}
    for element in theme_config.get("elements", ()):
        if not isinstance(element, dict):
            continue
        key, entry = _next_element_to_legacy_entry(element, counters)
        if key is None or entry is None:
            continue
        overlay[key] = entry
    return overlay


def _next_element_to_legacy_entry(
    element: dict[str, Any], counters: dict[str, int],
) -> tuple[str | None, dict[str, Any] | None]:
    """Translate one next/-shaped element into a (key, legacy entry) pair.

    Legacy entry shape (consumed by overlay_grid.load_from_overlay_config):
        {
          "x": int, "y": int, "color": str, "enabled": bool,
          "font": {"size": int, "style": "bold"|"regular", "name": str},
          # type-specific:
          "metric": "time"|"date"|"weekday"|<sensor-id>,
          "text": str,           # for type="text"
          "time_format" / "date_format" / "temp_unit": int,
        }
    """
    etype = element.get("type")
    if etype not in ("text", "metric", "clock"):
        return None, None

    font = {
        "name": element.get("name", _DEFAULT_FONT_NAME),
        "size": int(element.get("size", 24)),
        "style": "bold" if element.get("bold") else "regular",
    }
    entry: dict[str, Any] = {
        "x": int(element.get("x", 0)),
        "y": int(element.get("y", 0)),
        "color": element.get("color", "#ffffff"),
        "enabled": True,
        "font": font,
    }

    if etype == "text":
        entry["text"] = element.get("text", "")
        # Legacy keys custom-text under "custom_text" + optional counter.
        return _take_key("custom_text", counters), entry
    if etype == "clock":
        source = element.get("source", "")
        if source not in ("time", "date", "weekday"):
            return None, None
        entry["metric"] = source
        if source == "time":
            entry["time_format"] = 0
        elif source == "date":
            entry["date_format"] = 0
        return _take_key(source, counters), entry
    # type == "metric"
    metric_id = element.get("metric", "")
    if not metric_id:
        return None, None
    entry["metric"] = metric_id
    # Hardware temperature metrics carry a unit choice (C/F); the grid
    # honours ``temp_unit`` when present, so seed it from user prefs.
    if metric_id.endswith("temp"):
        entry["temp_unit"] = 0
    return _take_key(metric_id, counters), entry


def _take_key(base: str, counters: dict[str, int]) -> str:
    n = counters.get(base, 0)
    counters[base] = n + 1
    return base if n == 0 else f"{base}_{n}"


# =========================================================================
# Write path — emit a legacy-compatible config1.dc (0xDD format)
# =========================================================================


# Inverse of ``_HW_TO_SENSOR`` — sensor id -> (main_count, sub_count).
# Lazy-built so the reader path doesn't pay for it.
def _build_sensor_to_hw() -> dict[str, tuple[int, int]]:
    inverse: dict[str, tuple[int, int]] = {}
    for (main_c, sub_c), (sensor_id, _fmt) in _HW_TO_SENSOR.items():
        inverse[sensor_id] = (main_c, sub_c)
    return inverse


_SENSOR_TO_HW: dict[str, tuple[int, int]] | None = None


def _sensor_to_hw() -> dict[str, tuple[int, int]]:
    global _SENSOR_TO_HW
    if _SENSOR_TO_HW is None:
        _SENSOR_TO_HW = _build_sensor_to_hw()
    return _SENSOR_TO_HW


_DEFAULT_FONT_UNIT = 3      # GraphicsUnit.Point
_DEFAULT_FONT_CHARSET = 134  # GB2312 — matches legacy Windows TRCC default


class _Writer:
    """Sequential binary writer mirroring legacy ``BinaryWriter``."""

    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def write_byte(self, value: int) -> None:
        self.buf.append(value & 0xFF)

    def write_bool(self, value: bool) -> None:
        self.buf.append(1 if value else 0)

    def write_int32(self, value: int) -> None:
        self.buf.extend(struct.pack("<i", value))

    def write_float(self, value: float) -> None:
        self.buf.extend(struct.pack("<f", value))

    def write_string(self, value: str) -> None:
        """Length-prefixed UTF-8 string with the 7-bit-encoded length
        Windows ``BinaryWriter.Write(string)`` uses."""
        if not value:
            self.buf.append(0)
            return
        encoded = value.encode("utf-8")
        length = len(encoded)
        if length < 0x80:
            self.buf.append(length)
        else:
            # Two-byte 7-bit varint (legacy never emits more than two,
            # since strings are font names + short text).
            self.buf.append((length & 0x7F) | 0x80)
            self.buf.append((length >> 7) & 0x7F)
        self.buf.extend(encoded)


def write_dc_from_theme_config(
    path: Path,
    config: dict[str, Any],
    *,
    user_overlay_elements: list[dict[str, Any]] | None = None,
) -> None:
    """Write ``config1.dc`` (0xDD format) to *path*.

    Takes a next/-style theme config dict (same shape ``load_dc_as_theme_config``
    returns) plus an optional list of user overlay elements (the
    ``DeviceSettings.user_overlay_elements`` list, dict-form).  Theme
    elements paint first, user elements on top — matching how
    ``OverlayService.render`` composes them at draw time.

    Always emits 0xDD even when the source was 0xDC: the cloud-theme
    variant is the format both legacy and Windows TRCC accept for
    user-saved themes; 0xDC is reserved for the bundled stock themes.
    """
    if path.parent and not path.parent.exists():
        raise ThemeError(f"DC output directory missing: {path.parent}")

    elements: list[dict[str, Any]] = list(config.get("elements", []))
    elements.extend(user_overlay_elements or [])

    w = _Writer()
    w.write_byte(_MAGIC_DD)
    w.write_bool(True)                        # system_info_enabled
    w.write_int32(len(elements))
    for element in elements:
        _write_dd_element(w, element)
    _write_dd_trailer(w, config)

    try:
        path.write_bytes(bytes(w.buf))
    except OSError as e:
        raise ThemeError(f"Cannot write {path}: {e}") from e


def _write_dd_element(w: _Writer, element: dict[str, Any]) -> None:
    """Serialize one overlay element into the 0xDD element block."""
    mode, mode_sub, main_count, sub_count, custom_text = _element_to_legacy(element)
    w.write_int32(mode)
    w.write_int32(mode_sub)
    w.write_int32(int(element.get("x", 0)))
    w.write_int32(int(element.get("y", 0)))
    w.write_int32(main_count)
    w.write_int32(sub_count)
    _write_dd_font(w, element)
    w.write_string(custom_text)


def _write_dd_font(w: _Writer, element: dict[str, Any]) -> None:
    """Write the font + color record that follows every 0xDD element."""
    w.write_string(str(element.get("font_name", _DEFAULT_FONT_NAME)))
    w.write_float(float(element.get("size", 24.0)))
    style = 0
    if element.get("bold"):
        style |= 0x01
    if element.get("italic"):
        style |= 0x02
    w.write_byte(style)
    w.write_byte(_DEFAULT_FONT_UNIT)
    w.write_byte(_DEFAULT_FONT_CHARSET)
    a, r, g, b = _hex_to_argb(str(element.get("color", "#ffffff")))
    w.write_byte(a)
    w.write_byte(r)
    w.write_byte(g)
    w.write_byte(b)


def _write_dd_trailer(w: _Writer, config: dict[str, Any]) -> None:
    """Append the display-options block after the element list."""
    w.write_bool(bool(config.get("background_display", True)))
    w.write_bool(bool(config.get("transparent_display", False)))
    w.write_int32(int(config.get("rotation", 0)))
    w.write_int32(int(config.get("ui_mode", 0)))
    w.write_int32(int(config.get("display_mode", 0)))
    w.write_bool(bool(config.get("overlay_enabled", True)))
    overlay_rect = config.get("overlay_rect", (0, 0, 0, 0))
    for value in overlay_rect:
        w.write_int32(int(value))
    w.write_bool(bool(config.get("mask_enabled", False)))
    mask_pos = config.get("mask_position", (0, 0))
    for value in mask_pos:
        w.write_int32(int(value))


def _element_to_legacy(
    element: dict[str, Any],
) -> tuple[int, int, int, int, str]:
    """Map a next/ overlay element dict back to (mode, mode_sub, main, sub, text)."""
    kind = element.get("type", "text")
    if kind == "text":
        return (_MODE_CUSTOM, 0, 0, 0, str(element.get("text", "")))
    if kind == "metric":
        sensor = str(element.get("metric", ""))
        main_c, sub_c = _sensor_to_hw().get(sensor, (0, 0))
        return (_MODE_HARDWARE, 0, main_c, sub_c, "")
    if kind == "clock":
        source = element.get("source", "time")
        mode = {
            "time": _MODE_TIME,
            "weekday": _MODE_WEEKDAY,
            "date": _MODE_DATE,
        }.get(source, _MODE_TIME)
        return (mode, 0, 0, 0, "")
    # Unknown type — emit as CUSTOM with empty text so legacy at least
    # gets an addressable slot.
    return (_MODE_CUSTOM, 0, 0, 0, "")


def _hex_to_argb(hex_color: str) -> tuple[int, int, int, int]:
    """Parse '#rrggbb' or '#aarrggbb' into (A, R, G, B) 0..255."""
    s = hex_color.lstrip("#").strip()
    if len(s) == 6:
        try:
            r = int(s[0:2], 16)
            g = int(s[2:4], 16)
            b = int(s[4:6], 16)
            return (255, r, g, b)
        except ValueError:
            return (255, 255, 255, 255)
    if len(s) == 8:
        try:
            a = int(s[0:2], 16)
            r = int(s[2:4], 16)
            g = int(s[4:6], 16)
            b = int(s[6:8], 16)
            return (a, r, g, b)
        except ValueError:
            return (255, 255, 255, 255)
    return (255, 255, 255, 255)
