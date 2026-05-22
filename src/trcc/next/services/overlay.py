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
from pathlib import Path
from typing import Any

from ..core.errors import ThemeError
from ..core.ports import Renderer
from . import _dc as Dc

log = logging.getLogger(__name__)


_DC_CONFIG_FILE = "config1.dc"


class OverlayService:
    """Compose text/metric overlays onto a base surface."""

    def __init__(self, renderer: Renderer) -> None:
        self._r = renderer

    @staticmethod
    def calculate_mask_position(
        mask_dir: Path,
        mask_size: tuple[int, int],
        lcd_size: tuple[int, int],
    ) -> tuple[int, int]:
        """Top-left position for a mask on the LCD canvas.

        Direct port of legacy ``OverlayService.calculate_mask_position``:

          * Full-size mask (>= LCD in both dims) -> ``(0, 0)``
          * Sub-screen mask without a usable DC entry -> centered
          * Sub-screen mask with ``mask_position`` in its sibling
            ``config1.dc`` -> top-left from C# (XvalMB - W/2, YvalMB - H/2)
        """
        mask_w, mask_h = mask_size
        lcd_w, lcd_h = lcd_size
        if mask_w >= lcd_w and mask_h >= lcd_h:
            return (0, 0)
        centered = ((lcd_w - mask_w) // 2, (lcd_h - mask_h) // 2)
        dc_path = mask_dir / _DC_CONFIG_FILE
        if not dc_path.is_file():
            return centered
        try:
            cfg = Dc.File(dc_path).read()
        except ThemeError as e:
            log.warning(
                "calculate_mask_position: %s unreadable (%s); centering",
                dc_path, e,
            )
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
        # DC stores CENTER coords; render at top-left = center - size/2.
        return (cx - mask_w // 2, cy - mask_h // 2)

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
