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
from ..core.logs import Blob
from ..core.models import DATE_FORMATS, METRICS, DisplaySource

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


# =========================================================================
# The ``config1.dc`` trailer — ONE declaration, walked by both readers and
# the writer.
#
# Order and meaning come from the 2.1.6 writer (``FormCZTV.cs:7156``) and its
# own readers (``case 221`` :6817 · ``case 220`` :6212).  Each entry is
# ``(config key, default)`` and the DEFAULT'S TYPE is the wire type: ``bool``
# → one byte, ``int`` → int32, ``tuple`` → that many int32s.  There is nothing
# else to say about a field, so there is nothing else to keep in sync — the
# three hand-spelled copies this replaces had already drifted (the writer knew
# ``ui_mode`` / ``display_mode`` / ``overlay_rect``; neither reader produced
# them, so every write round-tripped those five fields to zero).
#
# ``0xDD`` writes the two runs back to back.  ``0xDC`` splits them: the 13
# element positions, the custom-text string and the show-unit bool sit in
# between.  That split is the only reason there are two runs.
_TRAILER_HEAD: tuple[tuple[str, Any], ...] = (
    ("background_display", True),        # myBjxs      背景显示 — background
    ("screencast_display", False),       # myTpxs      投屏显示 — SCREENCAST
    ("rotation", 0),                     # directionB
    ("ui_mode", 0),                      # myUIMode
)
_TRAILER_TAIL: tuple[tuple[str, Any], ...] = (
    ("display_source", 0),               # myMode      → DisplaySource
    ("screencast_border", False),        # myYcbk      buttonXSBK — show border
    ("screencast_rect", (0, 0, 0, 0)),   # JpX JpY JpW JpH
    ("mask_visible", False),             # myMbxs      蒙版显示 — mask
    ("mask_position", (0, 0)),           # XvalMB YvalMB
)

#: Every flag a ``config1.dc`` carries, as this codec names it.  ``SaveTheme``
#: copies these into its manifest by this name, so the manifest cannot go on
#: naming a field the codec has renamed.
THEME_FLAG_KEYS: tuple[str, ...] = (
    "overlay_enabled",
    *(key for key, _ in _TRAILER_HEAD),
    *(key for key, _ in _TRAILER_TAIL),
)

# Names for the log line.  ``DisplaySource(n)`` raises on a value the C# never
# writes; a diagnostic must never be the thing that fails.
_SOURCE_NAMES: dict[int, str] = {s.value: s.name for s in DisplaySource}


# Metric VALUE slots carry their unit in the format so it renders DYNAMICALLY —
# matching the C# flag-template reader (case 220), which sets every value
# element's ``myModeSub = 1`` → ``UCXiTongXianShiSubTimer`` draws number + unit.
# The unit is our decorator (``_draw_metric`` swaps °C→°F on the global toggle
# and button0 can hide it), NOT a baked glyph: a baked "°C" is static and reads
# wrong the moment the user picks Fahrenheit, so the unit must be drawn.  (The
# earlier #150/#203 "render bare" was wrong for these masks — the art's unit
# glyph is decoration; the functional unit is drawn, per the C#.)
_SLOT_MAP: list[tuple[str, str | None, str, str]] = [
    ("custom_text",       None,               "",      ""),
    ("cpu_temp",          "cpu:temp",         "CPU",   "{value:.0f}°C"),
    ("cpu_temp_label",    None,               "CPU",   ""),
    ("cpu_freq",          "cpu:freq",         "CPU",   "{value:.0f} MHz"),
    ("cpu_freq_label",    None,               "CPU",   ""),
    ("cpu_usage",         "cpu:usage",        "CPU",   "{value:.0f}%"),
    ("cpu_usage_label",   None,               "CPU",   ""),
    ("gpu_temp",          "gpu:primary:temp", "GPU",   "{value:.0f}°C"),
    ("gpu_temp_label",    None,               "GPU",   ""),
    ("gpu_clock",         "gpu:primary:clock","GPU",   "{value:.0f} MHz"),
    ("gpu_clock_label",   None,               "GPU",   ""),
    ("gpu_usage",         "gpu:primary:usage","GPU",   "{value:.0f}%"),
    ("gpu_usage_label",   None,               "GPU",   ""),
]

# DERIVED from ``core.models.METRICS`` — the one authority for what a DC
# ``(main_count, sub_count)`` pair means.  This used to be a second hand-kept
# table whose docstring claimed to be "the single source"; nine of its
# twenty-four ids differed from the other table's by no mechanical rule, and
# picking the wrong vocabulary silently DROPPED the overlay element.
_HW_TO_SENSOR: dict[tuple[int, int], tuple[str, str]] = {
    pair: (metric.sensor_id, metric.fmt)
    for pair, metric in METRICS.by_dc_pair.items()
}


_SENSOR_TO_HW: dict[str, tuple[int, int]] | None = None


def _sensor_to_hw() -> dict[str, tuple[int, int]]:
    log.debug("_sensor_to_hw")
    global _SENSOR_TO_HW
    if _SENSOR_TO_HW is None:
        _SENSOR_TO_HW = {
            sensor: pair for pair, (sensor, _fmt) in _HW_TO_SENSOR.items()
        }
    return _SENSOR_TO_HW


def hardware_metric(main: int, sub: int) -> tuple[str, str] | None:
    """``(main_count, sub_count)`` hardware code → ``(sensor_id, format)``.

    The single source the DC reader and the GUI overlay editor share for
    "what sensor + default format does this hardware element render", so
    the two never drift on metric ids (``cpu:temp``) or format strings.
    Returns ``None`` for an unmapped code.
    """
    log.debug("hardware_metric: main=%s sub=%s", main, sub)
    return _HW_TO_SENSOR.get((main, sub))


def metric_to_hardware(sensor: str) -> tuple[int, int] | None:
    """Inverse of :func:`hardware_metric`: ``sensor_id`` → ``(main, sub)``.

    Used when loading a next/ metric element back into the legacy-style
    overlay grid (which keys hardware elements by ``main``/``sub`` count).
    """
    log.debug("metric_to_hardware: sensor=%s", sensor)
    return _sensor_to_hw().get(sensor)


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
        log.info(
            "Reader.parse: %s — magic=0x%02x bytes=%d",
            theme_name, magic, len(data),
        )
        if magic not in (_MAGIC_DC, _MAGIC_DD):
            raise ThemeError(f"not a DC file (magic byte 0x{magic:02x})")
        try:
            if magic == _MAGIC_DD:
                result = _parse_dd(data, theme_name)
            else:
                result = _parse_dc(data, theme_name)
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            raise ThemeError(str(e)) from e
        source = result.get("display_source", 0)
        log.info(
            "Reader.parse: %s → %d elements, source=%s overlay_enabled=%s "
            "background=%s screencast=%s rect=%s border=%s mask_visible=%s "
            "mask_position=%s rotation=%s",
            theme_name, len(result.get("elements", [])),
            _SOURCE_NAMES.get(source, source), result.get("overlay_enabled"),
            result.get("background_display"), result.get("screencast_display"),
            result.get("screencast_rect"), result.get("screencast_border"),
            result.get("mask_visible"), result.get("mask_position"),
            result.get("rotation"),
        )
        return result


# =========================================================================
# Writer — dict → bytes
# =========================================================================


class Writer:
    """Serialises a theme config dict into ``0xDD`` DC bytes."""

    __slots__ = ()

    def serialize(self, config: dict[str, Any]) -> bytes:
        """Serialise ``config`` — its ``elements`` ARE the layout.

        There used to be a ``user_overlay_elements=`` parameter that this
        appended to ``config["elements"]``, which made the codec the third
        place that decided what an overlay contains.  Callers now resolve the
        one layout (``device_overlay_layout``) and hand it in as the config's
        elements; the codec decides nothing.
        """
        log.info("serialize: %d element(s)", len(config.get("elements", [])))
        elements: list[dict[str, Any]] = list(config.get("elements", []))
        w = _Writer()
        w.write_byte(_MAGIC_DD)
        # ``myXtxx`` — the overlay toggle.  Hardcoded ``True`` until
        # 2026-09-14, when the readers stopped looking for it in the
        # trailer; a write now preserves what the read found.
        w.write_bool(bool(config.get("overlay_enabled", True)))
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
        log.debug("__init__: path=%s", path)
        self.path = path
        self.reader = reader or Reader()
        self.writer = writer or Writer()

    def read(self) -> dict[str, Any]:
        log.info("File.read: %s", self.path)
        try:
            data = self.path.read_bytes()
        except OSError as e:
            log.warning("File.read: cannot read %s: %s: %s",
                        self.path, type(e).__name__, e)
            raise ThemeError(f"Cannot read {self.path}: {e}") from e
        if not data:
            log.warning("File.read: empty file %s", self.path)
            raise ThemeError(f"Empty DC file: {self.path}")
        try:
            return self.reader.parse(data, self.path.parent.name)
        except ThemeError as e:
            log.warning("File.read: parse failed for %s: %s", self.path, e)
            raise ThemeError(f"Invalid DC file {self.path}: {e}") from e

    def write(self, config: dict[str, Any]) -> None:
        elements = config.get("elements") or []
        log.info("File.write: %s — %d element(s)", self.path, len(elements))
        if self.path.parent and not self.path.parent.exists():
            log.warning("File.write: output dir missing %s — refusing",
                        self.path.parent)
            raise ThemeError(
                f"DC output directory missing: {self.path.parent}"
            )
        data = self.writer.serialize(config)
        try:
            self.path.write_bytes(data)
            log.info("File.write: %s — %d bytes written", self.path, len(data))
        except OSError as e:
            log.warning("File.write: write failed for %s: %s: %s",
                        self.path, type(e).__name__, e)
            raise ThemeError(f"Cannot write {self.path}: {e}") from e


# =========================================================================
# Internal: binary reader + writer
# =========================================================================


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, start: int) -> None:
        log.debug("__init__: data=%s start=%s", Blob(data), start)
        self.data = data
        self.pos = start

    def read_int32(self) -> int:
        log.debug("read_int32")
        val = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_bool(self) -> bool:
        log.debug("read_bool")
        val = self.data[self.pos] != 0
        self.pos += 1
        return val

    def read_byte(self) -> int:
        log.debug("read_byte")
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_float(self) -> float:
        log.debug("read_float")
        val = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_string(self) -> str:
        log.debug("read_string")
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
        log.debug("__init__")
        self.buf = bytearray()

    def write_byte(self, value: int) -> None:
        log.debug("write_byte: value=%s", value)
        self.buf.append(value & 0xFF)

    def write_bool(self, value: bool) -> None:
        log.debug("write_bool: value=%s", value)
        self.buf.append(1 if value else 0)

    def write_int32(self, value: int) -> None:
        log.debug("write_int32: value=%s", value)
        self.buf.extend(struct.pack("<i", value))

    def write_float(self, value: float) -> None:
        log.debug("write_float: value=%s", value)
        self.buf.extend(struct.pack("<f", value))

    def write_string(self, value: str) -> None:
        log.debug("write_string: value=%s", value)
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


def _trailer_defaults() -> dict[str, Any]:
    """Every trailer field at its default — what a truncated DC yields."""
    defaults = {key: list(default) if isinstance(default, tuple) else default
                for run in (_TRAILER_HEAD, _TRAILER_TAIL)
                for key, default in run}
    log.debug("_trailer_defaults: %d field(s) seeded — %s",
              len(defaults), defaults)
    return defaults


def _read_run(r: _Reader, run: tuple[tuple[str, Any], ...],
              into: dict[str, Any]) -> None:
    """Read one declared run, writing each field as it lands.

    Fields land one at a time rather than as a batch so a DC truncated
    mid-run keeps the fields that were actually there — the shape the
    hand-written readers had, preserved.
    """
    for key, default in run:
        if isinstance(default, bool):
            into[key] = r.read_bool()
        elif isinstance(default, tuple):
            into[key] = [r.read_int32() for _ in default]
        else:
            into[key] = r.read_int32()
    log.debug("_read_run: %s",
              {key: into[key] for key, _ in run})


def _write_run(w: _Writer, run: tuple[tuple[str, Any], ...],
               config: dict[str, Any]) -> None:
    """Write one declared run, padding a short tuple with its default.

    A field must occupy its full width or every byte after it shifts, so a
    config carrying a 3-int ``screencast_rect`` still writes four.
    """
    log.debug("_write_run: %s",
              {key: config.get(key, default) for key, default in run})
    for key, default in run:
        value = config.get(key, default)
        if isinstance(default, bool):
            w.write_bool(bool(value))
        elif isinstance(default, tuple):
            for item in (*value, *default)[:len(default)]:
                w.write_int32(int(item))
        else:
            w.write_int32(int(value))



def _parse_dc(data: bytes, theme_name: str) -> dict[str, Any]:
    r = _Reader(data, start=1)
    r.read_int32()
    r.read_int32()

    flag_custom = r.read_bool()
    # ``myXtxx`` — the OVERLAY toggle, same field 0xDD carries in its header
    # and discarded here for the same reason.  See ``_parse_dd``.
    overlay_enabled = r.read_bool()
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
            # Preserve the raw RGB bytes regardless of alpha.  Legacy
            # `dc_parser` / `OverlayElement.color_hex` ignored alpha
            # and returned the colour straight from the bytes — themes
            # commonly persist with alpha=0 + a real RGB triplet
            # (#808080 is the most common), and forcing #ffffff there
            # silently repaints user themes white on first read.
            del alpha  # legacy quirk: not part of the colour key
            fonts.append({
                "name": font_name,
                "size": size,
                "bold": bool(style & 0x01),
                "italic": bool(style & 0x02),
                "color": f"#{red:02x}{green:02x}{blue:02x}",
            })
        except (struct.error, IndexError):
            fonts.append({
                "name": _DEFAULT_FONT_NAME, "size": 24, "bold": False,
                "italic": False, "color": "#ffffff",
            })

    trailer = _trailer_defaults()
    try:
        _read_run(r, _TRAILER_HEAD, trailer)
    except (struct.error, IndexError) as e:
        log.debug("0xDC head truncated (%s) — keeping defaults", e)

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
    show_unit = True
    try:
        r.read_string()         # custom-text string (unused here)
        # ``num8`` — the theme's SHOW-UNIT switch.  The C# copies this one
        # bool into every value element's ``myModeSub`` (``arrayList5..10[1]
        # = num8``), which is the same field 0xDD carries per element and
        # which ``_build_dd_element`` already translates to ``show_unit``.
        # 0xDC had no equivalent: its metric formats bake the glyph in and
        # the flag was read and dropped, so a mask whose ART already draws
        # "°C" got a second one from us.  MEASURED: 10 of 500 shipped 0xDC
        # files ask for it hidden (2.0%); on the 0xDD side 177 of 1274
        # (13.9%) do, and those have always worked.
        show_unit = r.read_bool()
        _read_run(r, _TRAILER_TAIL, trailer)
    except (struct.error, IndexError) as e:
        log.debug("0xDC tail absent/truncated (%s) — keeping defaults", e)

    # Clock/date/weekday block.  Flag10 is the master enable; flag11
    # = date, flag12 = time, flag13 = weekday.  Each carries its own
    # font block + (x, y) coordinates.  Bare-minimum parse — emit
    # next/-shape clock elements so OverlayService renders them on
    # 0xDC themes the same as legacy did.
    try:
        flag_clock_master = r.read_bool()
        flag_date = r.read_bool()
        flag_time = r.read_bool()
        # The DC stores the date/time format the theme was DESIGNED for —
        # honour it (legacy kept it as the element's mode_sub) instead of
        # forcing the global yyyy/MM/dd default.  Date maps cleanly to a
        # strftime pattern; time keeps the global path (its 12h handling is
        # not a bare strftime — see resolve_clock).
        date_format_idx = r.read_int32()
        time_format_idx = r.read_int32()  # noqa: F841 — time stays global
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
                    "format": DATE_FORMATS.get(date_format_idx,
                                               DATE_FORMATS[0]),
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

    # One flag for all six values, applied where 0xDD applies its per-element
    # one — so both formats hand the overlay the same vocabulary and
    # ``_draw_metric`` needs to know nothing about which file it came from.
    for element in elements:
        if element.get("type") == "metric":
            element["show_unit"] = show_unit

    return {
        "name": theme_name,
        "overlay_enabled": overlay_enabled,
        **trailer,
        "elements": elements,
    }


def _parse_dd(data: bytes, theme_name: str) -> dict[str, Any]:
    r = _Reader(data, start=1)
    # ``myXtxx`` — ``ucXiTongXianShi1``, 系统显示, the OVERLAY toggle.  Read and
    # DISCARDED until 2026-09-14, while ``overlay_enabled`` took the trailer's
    # ``myYcbk`` (the screencast show-border flag) instead.  Measured over 2622
    # shipped DCs the two disagree on 892 of them — 34.0%.
    overlay_enabled = r.read_bool()
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

    trailer = _trailer_defaults()
    try:
        _read_run(r, _TRAILER_HEAD, trailer)
        _read_run(r, _TRAILER_TAIL, trailer)
    except (struct.error, IndexError) as e:
        log.debug("0xDD trailer truncated (%s) — later fields keep defaults", e)

    return {
        "name": theme_name,
        "overlay_enabled": overlay_enabled,
        **trailer,
        "elements": elements,
    }


def _read_dd_font(r: _Reader) -> dict[str, Any]:
    log.debug("_read_dd_font: r=%s", r)
    name = r.read_string() or _DEFAULT_FONT_NAME
    size = _clamp_font_size(r.read_float())
    style = r.read_byte()
    r.read_byte()
    r.read_byte()
    # Alpha byte is read+discarded — see ``_parse_dc`` for why we
    # preserve the RGB triplet regardless of alpha.
    r.read_byte()
    red = r.read_byte()
    green = r.read_byte()
    blue = r.read_byte()
    return {
        "name": name,
        "size": size,
        "bold": bool(style & 0x01),
        "italic": bool(style & 0x02),
        "color": f"#{red:02x}{green:02x}{blue:02x}",
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
            # mode_sub is the C# "unit-switch" (button0): 1 appends the unit
            # glyph to the number, otherwise the bare number is drawn (the unit
            # lives in the theme art).  Translate the wire int to the domain
            # ``show_unit`` here so the model/UI layers never see mode_sub.
            return {**base, "type": "metric", "metric": sensor_id,
                    "format": fmt, "show_unit": mode_sub == 1}
        case 4:
            if not custom_text:
                return None
            return {**base, "type": "text", "text": custom_text}
        case 1:
            return {**base, "type": "clock", "source": "time"}
        case 2:
            return {**base, "type": "clock", "source": "weekday"}
        case 3:
            return {**base, "type": "clock", "source": "date",
                    "format": DATE_FORMATS.get(mode_sub, DATE_FORMATS[0])}
        case _:
            log.debug("0xDD: unknown element mode %d; skipping", mode)
            return None


def _clamp_font_size(raw: float, default: float = 24.0) -> float:
    # Theme fonts span ~8 px labels up to the panel's hero number (the 001-series
    # temperature is authored at 128).  Only reject values a misaligned/garbage
    # read produces (NaN, negative, or absurdly large); everything else is real.
    log.debug("_clamp_font_size: raw=%s default=%s", raw, default)
    if 8.0 <= raw <= 512.0:
        return raw
    return default


# =========================================================================
# Internal: write (0xDD)
# =========================================================================


def _write_dd_element(w: _Writer, element: dict[str, Any]) -> None:
    log.debug("_write_dd_element: w=%s element=%s", w, element)
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
    log.debug("_write_dd_font: w=%s element=%s", w, element)
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
    """Write the trailer from the same declaration both readers walk.

    Every key here is a key a reader produces, because it is literally the
    same tuple — a field cannot be written under a name nothing reads back.
    That is not hypothetical: ``ui_mode``, ``display_mode`` and
    ``overlay_rect`` were spelled here and by neither reader, so all five
    of those ints round-tripped to zero.
    """
    log.debug("_write_dd_trailer: w=%s config=%s", w, config)
    _write_run(w, _TRAILER_HEAD, config)
    _write_run(w, _TRAILER_TAIL, config)


def _element_to_legacy(
    element: dict[str, Any],
) -> tuple[int, int, int, int, str]:
    log.debug("_element_to_legacy: element=%s", element)
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
    """Hex → ``(a, r, g, b)`` per DC's Windows-GDI ``#AARRGGBB`` convention.

    Cannot share ``core/_colors.parse_hex`` because the two interpret
    8-character hex strings differently:

    * ``parse_hex`` follows CSS ``#RRGGBBAA`` (alpha last) — the modern
      web standard most callers want.
    * DC stores colors per Windows GDI's ``#AARRGGBB`` (alpha first),
      because that's what Thermalright's Windows app writes.

    Round-tripping a parsed DC theme through a different convention
    silently mutates user themes (see test_color_with_alpha_round_trips).
    The 6-char form (no alpha) defaults to opaque; bad input returns
    opaque white, matching firmware's tolerance behaviour.
    """
    log.debug("_hex_to_argb: hex_color=%s", hex_color)
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
