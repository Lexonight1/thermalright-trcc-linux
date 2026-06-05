"""Adapter — next/ theme config -> legacy GUI overlay_grid dict shape.

The legacy ``overlay_grid`` widget (ported into next/'s GUI) consumes a
dict keyed by metric name with one entry per element:

    {
      "cpu_temp": {"x": int, "y": int, "color": "#rrggbb",
                   "enabled": bool, "font": {...},
                   "metric": "cpu:temp", "temp_unit": 0},
      "custom_text": {... "text": "..."},
      "time":         {... "metric": "time", "time_format": 0},
      ...
    }

next/'s theme configs (whether read from DC or JSON) carry an
``elements: list[dict]`` instead.  This module translates between the
two shapes — read a theme dir (DC preferred, legacy ``config.json``
fallback), return the overlay_grid-shape dict, ``{}`` on miss.

Lives in ``ui/gui/`` because only the legacy overlay_grid widget
consumes the legacy-shape dict; pure services use the list shape.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...core._safe import load_json_or_default
from ...core.errors import ThemeError
from ...services import _dc as Dc

log = logging.getLogger(__name__)


_JSON_CONFIG_FILE = "trcc.json"
_DC_CONFIG_FILE = "config1.dc"
_LEGACY_CONFIG_FILE = "config.json"
_DEFAULT_FONT_NAME = "Microsoft YaHei"


def configs_to_next_elements(configs: list[Any]) -> list[dict[str, Any]]:
    """Grid ``OverlayElementConfig`` list → next/ ``OverlayElement`` dicts.

    The shape ``SetOverlayConfig`` / ``OverlayElement.from_dict`` consume: a
    stable ``id`` (grid order — the whole layout is dispatched as a single
    replacement, so positional ids are sufficient and stable per dispatch),
    FLAT font fields (``size``/``bold``/``italic``), and ``type`` +
    ``metric``/``source``/``format`` resolved per :class:`OverlayMode`.

    This is the edit/save direction.  Without it the grid emitted the legacy
    keyed shape (nested ``font``, ``metric: "time"``, and crucially no
    ``id``), so ``SetOverlayConfig`` rejected every edit — colour, font, and
    drag never persisted.  ``(main, sub)`` → ``(sensor_id, format)`` reuses
    the DC codec's table so the editor never drifts from the reader.
    """
    from ...core.models import DATE_FORMATS, TIME_FORMATS, OverlayMode

    out: list[dict[str, Any]] = []
    for i, cfg in enumerate(configs):
        base: dict[str, Any] = {
            "id": f"el_{i}",
            "x": cfg.x, "y": cfg.y,
            "color": cfg.color,
            "size": cfg.font_size,
            "bold": cfg.font_style == 1,
            "italic": cfg.font_style == 2,
        }
        match cfg.mode:
            case OverlayMode.CUSTOM:
                out.append({**base, "type": "text", "text": cfg.text})
            case OverlayMode.TIME:
                out.append({**base, "type": "clock", "source": "time",
                            "format": TIME_FORMATS.get(cfg.mode_sub,
                                                       TIME_FORMATS[0])})
            case OverlayMode.DATE:
                out.append({**base, "type": "clock", "source": "date",
                            "format": DATE_FORMATS.get(cfg.mode_sub,
                                                       DATE_FORMATS[0])})
            case OverlayMode.WEEKDAY:
                out.append({**base, "type": "clock", "source": "weekday"})
            case OverlayMode.HARDWARE:
                entry = Dc.hardware_metric(cfg.main_count, cfg.sub_count)
                if entry is None:
                    log.warning("configs_to_next_elements: unmapped hardware "
                                "(%s, %s) — skipping", cfg.main_count,
                                cfg.sub_count)
                    continue
                sensor, fmt = entry
                out.append({**base, "type": "metric",
                            "metric": sensor, "format": fmt})
            case _:
                log.warning("configs_to_next_elements: unknown mode %s — "
                            "skipping", cfg.mode)
    log.debug("configs_to_next_elements: %d config(s) → %d next/ element(s)",
              len(configs), len(out))
    return out


def dc_as_legacy_overlay_config(theme_dir: Path) -> dict[str, dict[str, Any]]:
    """Read a theme's overlay config and return the legacy GUI's
    ``overlay_grid`` dict shape.

    Source preference matches ``ThemeService._load_config``:

      1. ``trcc.json`` -- next/-native; its ``elements`` list is the
         overlay layout ``SaveTheme`` now writes (saved themes carry NO
         ``config1.dc`` — the layout lives here).
      2. ``config1.dc`` -- binary, parsed via ``Dc.File.read()``
      3. ``config.json`` (legacy) -- pass through the ``dc:`` sub-dict,
         filtered by ``enabled``

    Returns ``{}`` when none exist or all parse empty.
    """
    raw_json = load_json_or_default(theme_dir / _JSON_CONFIG_FILE, None)
    if isinstance(raw_json, dict):
        overlay = _theme_config_to_overlay_dict(raw_json)
        if overlay:
            return overlay

    dc_path = theme_dir / _DC_CONFIG_FILE
    if dc_path.is_file():
        try:
            theme_config = Dc.File(dc_path).read()
        except ThemeError as e:
            log.warning(
                "dc_as_legacy_overlay_config: %s skipped (%s)", dc_path, e,
            )
        else:
            overlay = _theme_config_to_overlay_dict(theme_config)
            if overlay:
                return overlay

    raw = load_json_or_default(theme_dir / _LEGACY_CONFIG_FILE, None)
    if isinstance(raw, dict):
        dc_dict = raw.get("dc")
        if isinstance(dc_dict, dict):
            return {
                k: v for k, v in dc_dict.items()
                if isinstance(v, dict) and v.get("enabled", True)
            }

    return {}


def _theme_config_to_overlay_dict(
    theme_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    overlay: dict[str, dict[str, Any]] = {}
    counters: dict[str, int] = {}
    for element in theme_config.get("elements", ()):
        if not isinstance(element, dict):
            continue
        key, entry = _element_to_legacy_entry(element, counters)
        if key is None or entry is None:
            continue
        overlay[key] = entry
    return overlay


def _element_to_legacy_entry(
    element: dict[str, Any], counters: dict[str, int],
) -> tuple[str | None, dict[str, Any] | None]:
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
    metric_id = element.get("metric", "")
    if not metric_id:
        return None, None
    entry["metric"] = metric_id
    if metric_id.endswith("temp"):
        entry["temp_unit"] = 0
    return _take_key(metric_id, counters), entry


def _take_key(base: str, counters: dict[str, int]) -> str:
    n = counters.get(base, 0)
    counters[base] = n + 1
    return base if n == 0 else f"{base}_{n}"
