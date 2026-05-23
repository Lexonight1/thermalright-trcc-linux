"""DC file codec — ``config1.dc`` read/write.

This module is the namespace.  Import it as ``Dc``:

    from ...services import _dc as Dc
    cfg = Dc.File(path).read()
    Dc.File(path).write(cfg)

Three classes live here:

  * ``Reader`` — parses DC bytes (``0xDC`` / ``0xDD``) into a theme
    config dict.
  * ``Writer`` — serialises a theme config dict into ``0xDD`` bytes.
  * ``File``   — DI'd with a path; ``read()`` and ``write(config)``
    delegate to a ``Reader`` / ``Writer`` (injectable for tests).
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

from ..core.errors import ThemeError

log = logging.getLogger(__name__)


_MAGIC_DC = 0xDC
_MAGIC_DD = 0xDD
_FONT_SLOTS = 13
_ELEMENT_SLOTS = 13

_DEFAULT_FONT_NAME = "Microsoft YaHei"
_DEFAULT_FONT_UNIT = 3       # GraphicsUnit.Point
_DEFAULT_FONT_CHARSET = 134  # GB2312

# 0xDD element mode field (legacy OverlayMode IntEnum).
_MODE_HARDWARE = 0
_MODE_TIME = 1
_MODE_WEEKDAY = 2
_MODE_DATE = 3
_MODE_CUSTOM = 4


_SLOT_MAP: list[tuple[str, str | None, str, str]] = [
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

_HW_TO_SENSOR: dict[tuple[int, int], tuple[str, str]] = {
    (0, 1): ("cpu:temp",            "{value:.0f}°C"),
    (0, 2): ("cpu:usage",           "{value:.0f}%"),
    (0, 3): ("cpu:freq",            "{value:.0f} MHz"),
    (0, 4): ("cpu:power",           "{value:.0f} W"),
    (1, 1): ("gpu:primary:temp",    "{value:.0f}°C"),
    (1, 2): ("gpu:primary:usage",   "{value:.0f}%"),
    (1, 3): ("gpu:primary:clock",   "{value:.0f} MHz"),
    (1, 4): ("gpu:primary:power",   "{value:.0f} W"),
    (2, 1): ("memory:percent",      "{value:.0f}%"),
    (2, 2): ("memory:clock",        "{value:.0f} MHz"),
    (2, 3): ("memory:available",    "{value:.0f} MB"),
    (2, 4): ("memory:temp",         "{value:.0f}°C"),
    (3, 1): ("disk:0:read",         "{value:.0f} MB/s"),
    (3, 2): ("disk:0:write",        "{value:.0f} MB/s"),
    (3, 3): ("disk:0:activity",     "{value:.0f}%"),
    (3, 4): ("disk:0:temp",         "{value:.0f}°C"),
    (4, 1): ("net:down",            "{value:.0f} KB/s"),
    (4, 2): ("net:up",              "{value:.0f} KB/s"),
    (4, 3): ("net:total_down",      "{value:.0f} MB"),
    (4, 4): ("net:total_up",        "{value:.0f} MB"),
    (5, 1): ("fan:cpu",             "{value:.0f} RPM"),
    (5, 2): ("fan:gpu",             "{value:.0f} RPM"),
    (5, 3): ("fan:ssd",             "{value:.0f} RPM"),
    (5, 4): ("fan:sys2",            "{value:.0f} RPM"),
}


_SENSOR_TO_HW: dict[str, tuple[int, int]] | None = None


def _sensor_to_hw() -> dict[str, tuple[int, int]]:
    global _SENSOR_TO_HW
    if _SENSOR_TO_HW is None:
        _SENSOR_TO_HW = {
            sensor: pair for pair, (sensor, _fmt) in _HW_TO_SENSOR.items()
        }
    return _SENSOR_TO_HW


# =========================================================================
# Reader — bytes → dict
# =========================================================================


class Reader:
    """Parses ``config1.dc`` bytes (``0xDC`` or ``0xDD``) into a theme
    config dict."""

    __slots__ = ()

    def parse(self, data: bytes, theme_name: str) -> dict[str, Any]:
        """Return a next/-shape theme config dict.

        Raises ``ThemeError`` on empty buffer, unknown magic, or
        truncated binary.
        """
        if not data:
            raise ThemeError("empty DC buffer")
        magic = data[0]
        if magic not in (_MAGIC_DC, _MAGIC_DD):
            raise ThemeError(f"not a DC file (magic byte 0x{magic:02x})")
        try:
            if magic == _MAGIC_DD:
                return _parse_dd(data, theme_name)
            return _parse_dc(data, theme_name)
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            raise ThemeError(str(e)) from e


# =========================================================================
# Writer — dict → bytes
# =========================================================================


class Writer:
    """Serialises a theme config dict into ``0xDD`` DC bytes."""

    __slots__ = ()

    def serialize(
        self,
        config: dict[str, Any],
        *,
        user_overlay_elements: list[dict[str, Any]] | None = None,
    ) -> bytes:
        elements: list[dict[str, Any]] = list(config.get("elements", []))
        elements.extend(user_overlay_elements or [])
        w = _Writer()
        w.write_byte(_MAGIC_DD)
        w.write_bool(True)
        w.write_int32(len(elements))
        for element in elements:
            _write_dd_element(w, element)
        _write_dd_trailer(w, config)
        return bytes(w.buf)


# =========================================================================
# Dc — a DC file on disk.  Exposes .reader and .writer attributes.
# =========================================================================


class File:
    """A ``config1.dc`` on disk.

    Construct with the path.  ``dc.reader`` and ``dc.writer`` are the
    codec components (injectable for tests).  ``dc.read()`` and
    ``dc.write(config)`` are convenience methods that delegate to them.
    """

    __slots__ = ("path", "reader", "writer")

    def __init__(
        self,
        path: Path,
        *,
        reader: Reader | None = None,
        writer: Writer | None = None,
    ) -> None:
        self.path = path
        self.reader = reader or Reader()
        self.writer = writer or Writer()

    def read(self) -> dict[str, Any]:
        try:
            data = self.path.read_bytes()
        except OSError as e:
            raise ThemeError(f"Cannot read {self.path}: {e}") from e
        if not data:
            raise ThemeError(f"Empty DC file: {self.path}")
        try:
            return self.reader.parse(data, self.path.parent.name)
        except ThemeError as e:
            raise ThemeError(f"Invalid DC file {self.path}: {e}") from e

    def write(
        self,
        config: dict[str, Any],
        *,
        user_overlay_elements: list[dict[str, Any]] | None = None,
    ) -> None:
        if self.path.parent and not self.path.parent.exists():
            raise ThemeError(
                f"DC output directory missing: {self.path.parent}"
            )
        data = self.writer.serialize(
            config, user_overlay_elements=user_overlay_elements,
        )
        try:
            self.path.write_bytes(data)
        except OSError as e:
            raise ThemeError(f"Cannot write {self.path}: {e}") from e


# =========================================================================
# Internal: binary reader + writer
# =========================================================================


class _Reader:
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


class _Writer:
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
        if not value:
            self.buf.append(0)
            return
        encoded = value.encode("utf-8")
        length = len(encoded)
        if length < 0x80:
            self.buf.append(length)
        else:
            self.buf.append((length & 0x7F) | 0x80)
            self.buf.append((length >> 7) & 0x7F)
        self.buf.extend(encoded)


# =========================================================================
# Internal: parse paths (0xDC / 0xDD)
# =========================================================================


def _parse_dc(data: bytes, theme_name: str) -> dict[str, Any]:
    r = _Reader(data, start=1)
    r.read_int32()
    r.read_int32()

    flag_custom = r.read_bool()
    r.read_bool()
    flag_cpu_temp = r.read_bool()
    flag_cpu_freq = r.read_bool()
    flag_cpu_usage = r.read_bool()
    flag_gpu_temp = r.read_bool()
    flag_gpu_clock = r.read_bool()
    flag_gpu_usage = r.read_bool()
    r.read_int32()

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

    fonts: list[dict[str, Any]] = []
    custom_text = ""
    for idx in range(_FONT_SLOTS):
        try:
            if idx == 0:
                custom_text = r.read_string()
            font_name = r.read_string() or _DEFAULT_FONT_NAME
            size = _clamp_font_size(r.read_float())
            style = r.read_byte()
            r.read_byte()
            r.read_byte()
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

    try:
        background_display = r.read_bool()
        transparent_display = r.read_bool()
        rotation = r.read_int32()
        r.read_int32()
    except (struct.error, IndexError):
        background_display, transparent_display, rotation = True, False, 0

    positions: list[tuple[int, int]] = []
    for _ in range(_ELEMENT_SLOTS):
        try:
            x = r.read_int32()
            y = r.read_int32()
        except (struct.error, IndexError):
            break
        positions.append((x, y))

    elements: list[dict[str, Any]] = []
    for idx, (slot_name, metric_key, label, fmt) in enumerate(_SLOT_MAP):
        if idx >= len(positions):
            break
        if not slot_enabled.get(slot_name, True):
            continue
        x, y = positions[idx]
        font = fonts[idx] if idx < len(fonts) else {
            "size": 24, "bold": False, "italic": False, "color": "#ffffff",
        }
        if slot_name == "custom_text":
            if not custom_text:
                continue
            elements.append({
                "type": "text", "x": x, "y": y, "text": custom_text, **font,
            })
        elif metric_key is None:
            elements.append({
                "type": "text", "x": x, "y": y, "text": label, **font,
            })
        else:
            elements.append({
                "type": "metric", "x": x, "y": y,
                "metric": metric_key, "format": fmt, **font,
            })

    # Optional 0xDC trailer — carries overlay rect + mask flags + the
    # clock/date/weekday block.  Same fields legacy DcParser reads
    # after the 13 positions; ported verbatim to avoid silently losing
    # the time/date/weekday elements 0xDC themes carry.
    overlay_enabled = True
    mask_visible = False
    mask_x = 0
    mask_y = 0
    try:
        r.read_string()         # custom-text string (unused here)
        r.read_bool()            # num8 (unknown)
        r.read_int32()           # num5 (mode)
        overlay_enabled = r.read_bool()
        r.read_int32()           # overlay rect X
        r.read_int32()           # overlay rect Y
        r.read_int32()           # overlay rect W
        r.read_int32()           # overlay rect H
        mask_visible = r.read_bool()
        mask_x = r.read_int32()
        mask_y = r.read_int32()
    except (struct.error, IndexError):
        pass

    # Clock/date/weekday block.  Flag10 is the master enable; flag11
    # = date, flag12 = time, flag13 = weekday.  Each carries its own
    # font block + (x, y) coordinates.  Bare-minimum parse — emit
    # next/-shape clock elements so OverlayService renders them on
    # 0xDC themes the same as legacy did.
    try:
        flag_clock_master = r.read_bool()
        flag_date = r.read_bool()
        flag_time = r.read_bool()
        date_format = r.read_int32()  # noqa: F841 — per-element format stays user-pref
        time_format = r.read_int32()  # noqa: F841
        date_x = r.read_int32()
        date_y = r.read_int32()
        time_x = r.read_int32()
        time_y = r.read_int32()
        date_font = _read_dd_font(r)
        time_font = _read_dd_font(r)
        flag_weekday = r.read_bool()
        weekday_x = r.read_int32()
        weekday_y = r.read_int32()
        weekday_font = _read_dd_font(r)
        if flag_clock_master:
            if flag_date:
                elements.append({
                    "type": "clock", "source": "date",
                    "x": date_x, "y": date_y, **date_font,
                })
            if flag_time:
                elements.append({
                    "type": "clock", "source": "time",
                    "x": time_x, "y": time_y, **time_font,
                })
            if flag_weekday:
                elements.append({
                    "type": "clock", "source": "weekday",
                    "x": weekday_x, "y": weekday_y, **weekday_font,
                })
    except (struct.error, IndexError):
        # Trailer is optional — older 0xDC themes don't have it.
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


def _parse_dd(data: bytes, theme_name: str) -> dict[str, Any]:
    r = _Reader(data, start=1)
    r.read_bool()
    count = r.read_int32()
    if count < 0 or count > 100:
        raise ThemeError(f"0xDD element count out of range: {count}")

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
        if (el := _build_dd_element(
            mode, mode_sub, x, y, main_count, sub_count, font, custom_text,
        )) is not None:
            elements.append(el)

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
        r.read_int32()
        r.read_int32()
        overlay_enabled = r.read_bool()
        for _ in range(4):
            r.read_int32()
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
    name = r.read_string() or _DEFAULT_FONT_NAME
    size = _clamp_font_size(r.read_float())
    style = r.read_byte()
    r.read_byte()
    r.read_byte()
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
    base: dict[str, Any] = {"x": x, "y": y, **font}
    match mode:
        case 0:
            entry = _HW_TO_SENSOR.get((main_count, sub_count))
            if entry is None:
                log.debug(
                    "0xDD HARDWARE element (%d, %d) has no sensor mapping; skipping",
                    main_count, sub_count,
                )
                return None
            sensor_id, fmt = entry
            return {**base, "type": "metric", "metric": sensor_id, "format": fmt}
        case 4:
            if not custom_text:
                return None
            return {**base, "type": "text", "text": custom_text}
        case 1:
            return {**base, "type": "clock", "source": "time"}
        case 2:
            return {**base, "type": "clock", "source": "weekday"}
        case 3:
            return {**base, "type": "clock", "source": "date"}
        case _:
            log.debug("0xDD: unknown element mode %d; skipping", mode)
            return None


def _clamp_font_size(raw: float, default: float = 24.0) -> float:
    if 0 < raw < 100:
        return max(8.0, min(72.0, raw))
    return default


# =========================================================================
# Internal: write (0xDD)
# =========================================================================


def _write_dd_element(w: _Writer, element: dict[str, Any]) -> None:
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
    return (_MODE_CUSTOM, 0, 0, 0, "")


def _hex_to_argb(hex_color: str) -> tuple[int, int, int, int]:
    s = hex_color.lstrip("#").strip()
    if len(s) == 6:
        try:
            return (255, int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return (255, 255, 255, 255)
    if len(s) == 8:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16),
                    int(s[4:6], 16), int(s[6:8], 16))
        except ValueError:
            return (255, 255, 255, 255)
    return (255, 255, 255, 255)
