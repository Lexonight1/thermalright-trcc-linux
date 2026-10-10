"""Overlay editor cells ↔ App overlay elements — toolkit-free, no Qt.

Two shapes meet here:

* ``OverlayElementConfig`` — the gui editor's per-cell dataclass
  (:class:`trcc.core.models.OverlayElementConfig`), owned by
  :class:`trcc.ui.presentation.overlay_model.OverlayModel`: the Windows
  ``myMode`` / ``myModeSub`` / ``(main, sub)`` vocabulary.
* the App's element — read as :class:`~trcc.core.results.OverlayElementEntry`
  from ``ResolveOverlay``, written as the fields of ``AddOverlayElement`` /
  ``UpdateOverlayElement``: ``type`` + ``metric``/``source``/``format``.

The cell carries the element's ``id``, so an edit names the one element it
changes.  The editor once kept a third, keyed-dict shape read straight from
the theme's files; it lost every id and showed the file instead of what the
App holds, and is gone.

``(main, sub)`` ↔ ``(sensor_id, format)`` is the DC codec's table
(``services._dc``), so the editor never drifts from the reader.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ...core.models import (
    DATE_FORMATS,
    TIME_FORMATS,
    OverlayElementConfig,
    OverlayMode,
    default_metric_format,
    format_index,
)
from ...core.results import OverlayElementEntry
from ...services import _dc as Dc
from ...services.overlay import metric_text

log = logging.getLogger(__name__)


_DEFAULT_FONT_NAME = "Microsoft YaHei"


def entries_to_configs(
    entries: Iterable[OverlayElementEntry],
) -> list[OverlayElementConfig]:
    """App elements → editor cells, in order, each keeping its id.

    A metric the DC table cannot name (a board probe, a voltage, one DIMM)
    gets a cell too, by its sensor id -- it used to be left out, so the gui
    could neither show nor edit what qtgui, the CLI and the API placed (#223,
    #259, #310).
    """
    configs: list[OverlayElementConfig] = []
    for e in entries:
        cfg = OverlayElementConfig(
            id=e.id, x=e.x, y=e.y, color=e.color,
            font_name=e.font or _DEFAULT_FONT_NAME, font_size=e.size,
            font_style=1 if e.bold else 2 if e.italic else 0,
        )
        match e.type, e.source:
            case "text", _:
                cfg.mode, cfg.text = OverlayMode.CUSTOM, e.text
            case "clock", "time":
                cfg.mode = OverlayMode.TIME
                cfg.mode_sub = format_index(TIME_FORMATS, e.format)
            case "clock", "date":
                cfg.mode = OverlayMode.DATE
                cfg.mode_sub = format_index(DATE_FORMATS, e.format)
            case "clock", "weekday":
                cfg.mode = OverlayMode.WEEKDAY
            case "metric", _ if e.metric:
                cfg.mode = OverlayMode.HARDWARE
                if (hw := Dc.metric_to_hardware(e.metric)) is not None:
                    cfg.main_count, cfg.sub_count = hw
                else:
                    cfg.main_count, cfg.sub_count, cfg.metric = 0, 0, e.metric
                # button0, the C# unit-switch: 1 draws the unit glyph.
                cfg.mode_sub = 1 if e.show_unit else 0
            case _:
                log.warning("entries_to_configs: %s %s (metric %r) has no "
                            "editor cell — not shown", e.id, e.type, e.metric)
                continue
        configs.append(cfg)
    log.debug("entries_to_configs: %d cell(s)", len(configs))
    return configs


def config_fields(cfg: OverlayElementConfig) -> dict[str, Any] | None:
    """An editor cell → the element fields ``AddOverlayElement`` takes.

    ``UpdateOverlayElement`` takes the same names, so an edit sends the
    subset that differs before and after.  ``None`` for a hardware pair the
    DC table cannot name — there is no sensor to give the App.
    """
    base: dict[str, Any] = {
        "x": cfg.x, "y": cfg.y, "color": cfg.color,
        "size": cfg.font_size, "font": cfg.font_name,
        "bold": cfg.font_style == 1, "italic": cfg.font_style == 2,
    }
    match cfg.mode:
        case OverlayMode.CUSTOM:
            fields = {**base, "type": "text", "text": cfg.text}
        case OverlayMode.TIME:
            fields = {**base, "type": "clock", "source": "time",
                      "format": TIME_FORMATS.get(cfg.mode_sub, TIME_FORMATS[0])}
        case OverlayMode.DATE:
            fields = {**base, "type": "clock", "source": "date",
                      "format": DATE_FORMATS.get(cfg.mode_sub, DATE_FORMATS[0])}
        case OverlayMode.WEEKDAY:
            fields = {**base, "type": "clock", "source": "weekday"}
        case OverlayMode.HARDWARE if cfg.metric:
            fields = {**base, "type": "metric", "metric": cfg.metric,
                      "format": default_metric_format(cfg.metric),
                      "show_unit": cfg.mode_sub == 1}
        case OverlayMode.HARDWARE if (
                hw := Dc.hardware_metric(cfg.main_count, cfg.sub_count)):
            sensor, fmt = hw
            fields = {**base, "type": "metric", "metric": sensor,
                      "format": fmt, "show_unit": cfg.mode_sub == 1}
        case _:
            log.warning("config_fields: %s cell (%s, %s) maps to no element",
                        cfg.mode.name, cfg.main_count, cfg.sub_count)
            return None
    log.debug("config_fields: %s → %s", cfg.mode.name, fields["type"])
    return fields


def tile_reading(cfg: OverlayElementConfig, readings: Mapping[str, float],
                 temp_unit: str) -> tuple[str, str] | None:
    """A hardware tile's live ``(number, unit)``, or None when it has none.

    The C# grid tile shows the number in label2 and the unit in label3 --
    the unit always, whatever the element's unit switch says
    (``UCXiTongXianShiSub.UCXiTongXianShiSubTimer``, mode 0).  The text comes
    from the function the panel draws with, so a tile cannot disagree with
    the panel about °F or a duty-only fan (#301).
    """
    if cfg.mode != OverlayMode.HARDWARE or (fields := config_fields(cfg)) is None:
        return None
    text = metric_text({**fields, "show_unit": True}, readings, temp_unit)
    if text is None:
        return None
    number = re.match(r"-?[\d.]+", text)
    reading = ((number.group(), text[number.end():].strip()) if number
               else (text, ""))
    log.debug("tile_reading: %s -> %s", fields.get("metric"), reading)
    return reading

