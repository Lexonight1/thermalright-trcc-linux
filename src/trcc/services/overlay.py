"""OverlayService — text/metric overlays composited on a background.

Overlay rendering takes:
  * the base image (background.png from a Theme, already loaded)
  * a sensor reading dict
  * element layout from the theme's config.json

and returns a composite surface ready for orientation + encode.
Uses the Renderer port exclusively; knows nothing about Qt directly.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..core.ports import Renderer

log = logging.getLogger(__name__)


class OverlayService:
    """Compose text/metric overlays onto a base surface."""

    def __init__(self, renderer: Renderer) -> None:
        self._r = renderer

    def render(
        self,
        base: Any,
        config: Dict[str, Any],
        sensors: Dict[str, float],
    ) -> Any:
        """Render every overlay element from *config* onto *base*.

        config shape (TRCC theme config.json):
            {
              "overlay_enabled": bool,
              "elements": [
                { "type": "text", "x": int, "y": int, "text": str,
                  "color": "#ffffff", "size": int, "bold": bool, "italic": bool },
                { "type": "metric", "x": int, "y": int, "metric": "cpu_temp",
                  "format": "{value:.0f}°C", "color": "#ffffff", "size": int },
                ...
              ]
            }
        """
        if not config.get("overlay_enabled", True):
            return base

        # Start from a copy of the base surface
        width, height = self._r.surface_size(base)
        overlay = self._r.create_surface(width, height)

        elements: List[Dict[str, Any]] = config.get("elements", [])
        for element in elements:
            self._draw_element(overlay, element, sensors)

        return self._r.composite(base, overlay, position=(0, 0))

    # ── per-element dispatch ──────────────────────────────────────────

    def _draw_element(
        self,
        surface: Any,
        element: Dict[str, Any],
        sensors: Dict[str, float],
    ) -> None:
        kind = element.get("type")
        if kind == "text":
            self._draw_text(surface, element)
        elif kind == "metric":
            self._draw_metric(surface, element, sensors)
        else:
            log.debug("Skipping unknown overlay element type: %r", kind)

    def _draw_text(self, surface: Any, element: Dict[str, Any]) -> None:
        self._r.draw_text(
            surface,
            x=int(element.get("x", 0)),
            y=int(element.get("y", 0)),
            text=str(element.get("text", "")),
            color=str(element.get("color", "#ffffff")),
            size=int(element.get("size", 16)),
            bold=bool(element.get("bold", False)),
            italic=bool(element.get("italic", False)),
        )

    def _draw_metric(
        self,
        surface: Any,
        element: Dict[str, Any],
        sensors: Dict[str, float],
    ) -> None:
        metric_id = str(element.get("metric", ""))
        value: Optional[float] = sensors.get(metric_id)
        if value is None:
            log.debug("Metric %r has no sensor reading; skipping", metric_id)
            return
        fmt = str(element.get("format", "{value}"))
        text = fmt.format(value=value)
        self._r.draw_text(
            surface,
            x=int(element.get("x", 0)),
            y=int(element.get("y", 0)),
            text=text,
            color=str(element.get("color", "#ffffff")),
            size=int(element.get("size", 16)),
            bold=bool(element.get("bold", False)),
            italic=bool(element.get("italic", False)),
        )
