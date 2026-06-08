"""Overlay (de)serialization — toolkit-free, shared by every presentation.

Three shapes meet here, all converted with plain functions (no Qt):

* ``OverlayElementConfig`` — the editor's per-element dataclass
  (:class:`trcc.core.models.OverlayElementConfig`), owned by
  :class:`trcc.ui.presentation.overlay_model.OverlayModel`.
* the **legacy renderer dict** — keyed by metric name, one entry per element
  (``{"cpu_temp": {"x":…, "metric":"cpu:temp", "font":{…}}, …}``); what the
  ported overlay editor reads/writes.
* the **next/ ``OverlayElement`` dict** — flat ``id``/``size``/``bold`` +
  ``type``/``metric``/``source``/``format``; what ``SetOverlayConfig`` consumes.

Lives in ``ui/presentation`` (not ``ui/gui``) so the Qt-free Presentation
Models can use it without a backwards dependency on a concrete GUI.  The
``(main, sub)`` ↔ ``(sensor_id, format)`` mapping is reused from the DC codec
(``services._dc``) so the editor never drifts from the reader.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...core._safe import load_json_or_default
from ...core.errors import ThemeError
from ...core.models import (
    DATE_FORMATS,
    HARDWARE_METRICS,
    TIME_FORMATS,
    OverlayElementConfig,
    OverlayMode,
)
from ...services import _dc as Dc

log = logging.getLogger(__name__)


_JSON_CONFIG_FILE = "trcc.json"
_DC_CONFIG_FILE = "config1.dc"
_LEGACY_CONFIG_FILE = "config.json"
_DEFAULT_FONT_NAME = "Microsoft YaHei"


# =========================================================================
# OverlayElementConfig  ↔  legacy renderer dict
# =========================================================================


def configs_to_overlay_config(
    configs: list[OverlayElementConfig], enabled: bool,
) -> dict[str, dict[str, Any]]:
    """Editor configs → legacy ``OverlayRenderer`` keyed dict.

    Returns ``{}`` when overlay is disabled (the renderer draws nothing).
    """
    if not enabled:
        return {}

    overlay_config: dict[str, dict[str, Any]] = {}
    for i, cfg in enumerate(configs):
        entry: dict[str, Any] = {
            "x": cfg.x,
            "y": cfg.y,
            "color": cfg.color,
            "font": {
                "size": cfg.font_size,
                "style": "bold" if cfg.font_style == 1 else "regular",
                "name": cfg.font_name,
            },
            "enabled": True,
        }

        if cfg.mode == OverlayMode.TIME:
            entry["metric"] = "time"
            entry["time_format"] = cfg.mode_sub
            key = f"time_{i}"
        elif cfg.mode == OverlayMode.DATE:
            entry["metric"] = "date"
            entry["date_format"] = cfg.mode_sub
            key = f"date_{i}"
        elif cfg.mode == OverlayMode.WEEKDAY:
            entry["metric"] = "weekday"
            key = f"weekday_{i}"
        elif cfg.mode == OverlayMode.CUSTOM:
            entry["text"] = cfg.text
            key = f"custom_{i}"
        elif cfg.mode == OverlayMode.HARDWARE:
            entry["metric"] = HARDWARE_METRICS.get(
                (cfg.main_count, cfg.sub_count),
                f"hw_{cfg.main_count}_{cfg.sub_count}",
            )
            entry["temp_unit"] = cfg.mode_sub
            key = f"hw_{cfg.main_count}_{cfg.sub_count}_{i}"
        else:
            continue

        overlay_config[key] = entry

    return overlay_config


def overlay_config_to_configs(
    overlay_config: dict[str, Any],
) -> list[OverlayElementConfig]:
    """Legacy ``OverlayRenderer`` keyed dict → editor configs.

    Skips disabled / malformed entries.  Hardware metrics arrive as next/
    ids ("cpu:temp"); ``Dc.metric_to_hardware`` maps them back to
    ``(main, sub)`` so every metric element re-enters the grid (without it
    they were silently dropped and could not be selected or dragged).
    """
    configs: list[OverlayElementConfig] = []
    for _key, cfg in overlay_config.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue

        font = cfg.get("font", {})
        is_dict = isinstance(font, dict)
        font_size = font.get("size", 36) if is_dict else 36
        font_style = (1 if font.get("style") == "bold" else 0) if is_dict else 0
        font_name = font.get("name", _DEFAULT_FONT_NAME) if is_dict else _DEFAULT_FONT_NAME

        elem = OverlayElementConfig(
            x=cfg.get("x", 100),
            y=cfg.get("y", 100),
            color=cfg.get("color", "#FFFFFF"),
            font_size=font_size,
            font_style=font_style,
            font_name=font_name,
        )

        metric = cfg.get("metric", "")
        if metric == "time":
            elem.mode = OverlayMode.TIME
            elem.mode_sub = cfg.get("time_format", 0)
        elif metric == "date":
            elem.mode = OverlayMode.DATE
            elem.mode_sub = cfg.get("date_format", 0)
        elif metric == "weekday":
            elem.mode = OverlayMode.WEEKDAY
        elif "text" in cfg:
            elem.mode = OverlayMode.CUSTOM
            elem.text = cfg["text"]
        elif (hw := Dc.metric_to_hardware(metric)) is not None:
            elem.main_count, elem.sub_count = hw
            elem.mode = OverlayMode.HARDWARE
            elem.mode_sub = cfg.get("temp_unit", 0)
        else:
            log.warning(
                "overlay_config_to_configs: unmapped metric %r — skipping element",
                metric,
            )
            continue
        configs.append(elem)

    return configs


# =========================================================================
# OverlayElementConfig  →  next/ OverlayElement dict (Command-bus shape)
# =========================================================================


def configs_to_next_elements(configs: list[Any]) -> list[dict[str, Any]]:
    """Editor configs → next/ ``OverlayElement`` dicts for ``SetOverlayConfig``.

    The shape ``OverlayElement.from_dict`` consumes: a stable ``id`` (grid
    order — the whole layout is dispatched as one replacement, so positional
    ids are sufficient and stable per dispatch), FLAT font fields
    (``size``/``bold``/``italic``), and ``type`` + ``metric``/``source``/
    ``format`` resolved per :class:`OverlayMode`.

    Without it the grid emitted the legacy keyed shape (nested ``font``,
    ``metric: "time"``, no ``id``), so ``SetOverlayConfig`` rejected every edit
    — colour, font and drag never persisted.  ``(main, sub)`` →
    ``(sensor_id, format)`` reuses the DC codec's table so the editor never
    drifts from the reader.
    """
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


# =========================================================================
# Theme directory  →  legacy renderer dict
# =========================================================================


def dc_as_legacy_overlay_config(theme_dir: Path) -> dict[str, dict[str, Any]]:
    """Read a theme's overlay config and return the legacy ``overlay_grid``
    dict shape.

    Source preference matches ``ThemeService._load_config``:

      1. ``trcc.json`` -- next/-native; its ``elements`` list is the overlay
         layout ``SaveTheme`` now writes (saved themes carry NO ``config1.dc``).
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
