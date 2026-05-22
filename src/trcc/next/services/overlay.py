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
from typing import Any

from ..core.ports import Renderer

log = logging.getLogger(__name__)


class OverlayService:
    """Compose text/metric overlays onto a base surface."""

    def __init__(self, renderer: Renderer) -> None:
        self._r = renderer

    def render(
        self,
        base: Any,
        config: dict[str, Any],
        sensors: dict[str, float],
        clock: dict[str, str] | None = None,
        user_elements: list[dict[str, Any]] | None = None,
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
                { "type": "clock", "x": int, "y": int,
                  "source": "time" | "weekday" | "date",
                  "color": "#ffffff", "size": int },
                ...
              ]
            }

        ``clock`` is a pre-resolved ``{"time": "14:58", "date": ...,
        "weekday": ...}`` dict produced by DisplayService via
        ``services._clock.compute_clock``.  When ``None``, clock
        elements are skipped (e.g. test fixtures that don't care).

        ``user_elements`` is the user's edits on top of the theme's
        bundled elements; rendered after them so users layer on top.
        Same dict shape as ``config["elements"]`` (produced by
        ``OverlayElement.to_dict``).
        """
        if not config.get("overlay_enabled", True):
            return base

        # Start from a copy of the base surface
        width, height = self._r.surface_size(base)
        overlay = self._r.create_surface(width, height)

        elements: list[dict[str, Any]] = config.get("elements", [])
        for element in elements:
            self._draw_element(overlay, element, sensors, clock or {})
        # User edits paint on top.
        for element in user_elements or []:
            self._draw_element(overlay, element, sensors, clock or {})

        return self._r.composite(base, overlay, position=(0, 0))

    # ── per-element dispatch ──────────────────────────────────────────

    def _draw_element(
        self,
        surface: Any,
        element: dict[str, Any],
        sensors: dict[str, float],
        clock: dict[str, str],
    ) -> None:
        kind = element.get("type")
        if kind == "text":
            self._draw_text(surface, element)
        elif kind == "metric":
            self._draw_metric(surface, element, sensors)
        elif kind == "clock":
            self._draw_clock(surface, element, clock)
        else:
            log.debug("Skipping unknown overlay element type: %r", kind)

    def _draw_text(self, surface: Any, element: dict[str, Any]) -> None:
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
        element: dict[str, Any],
        sensors: dict[str, float],
    ) -> None:
        metric_id = str(element.get("metric", ""))
        value: float | None = sensors.get(metric_id)
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

    def _draw_clock(
        self,
        surface: Any,
        element: dict[str, Any],
        clock: dict[str, str],
    ) -> None:
        source = str(element.get("source", ""))
        text = clock.get(source, "")
        if not text:
            log.debug("Clock source %r unresolved; skipping", source)
            return
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
