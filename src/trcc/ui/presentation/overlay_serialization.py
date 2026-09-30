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
from collections.abc import Iterable
from typing import Any

from ...core.models import (
    DATE_FORMATS,
    TIME_FORMATS,
    OverlayElementConfig,
    OverlayMode,
    format_index,
)
from ...core.results import OverlayElementEntry
from ...services import _dc as Dc

log = logging.getLogger(__name__)


_DEFAULT_FONT_NAME = "Microsoft YaHei"


def entries_to_configs(
    entries: Iterable[OverlayElementEntry],
) -> list[OverlayElementConfig]:
    """App elements → editor cells, in order, each keeping its id.

    A metric the DC table cannot name has no ``(main, sub)`` to show in a
    cell and is left out — safe, because the editor edits by id and so never
    touches an element it does not show.
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
            case "metric", _ if (hw := Dc.metric_to_hardware(e.metric)):
                cfg.mode = OverlayMode.HARDWARE
                cfg.main_count, cfg.sub_count = hw
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
