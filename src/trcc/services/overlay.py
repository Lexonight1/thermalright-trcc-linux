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
from ..core.models import OverlayElement
from ..core.ports import Renderer
from . import _dc as Dc

log = logging.getLogger(__name__)


_DC_CONFIG_FILE = "config1.dc"


def resolve_overlay_elements(
    theme_config: dict[str, Any],
    mask_elements: list[OverlayElement] | None,
    user_elements: list[OverlayElement],
) -> list[dict[str, Any]]:
    """The ONE effective overlay layout for a device, by precedence.

    Legacy held a single ``self.config`` and ``set_config`` REPLACED it;
    ``_restore_mask_and_overlay`` resolved it as mask > saved-user > theme.
    The cutover split that one config into three persisted sources — the
    user's edits, an applied mask's layout, and the theme's bundled
    elements — that were wrongly STACKED at render time (every element
    drew twice).  This restores legacy's single-layout semantics: exactly
    one source wins and is drawn, never added on top of another.

    Precedence (each REPLACES, never adds):

    * ``user_elements`` (the device's live edits) win when present.  A
      source change — explicit theme switch (``LoadTheme``) or mask apply
      (``ApplyMask``) — clears them, so a stale layout never bleeds into a
      new theme.  Combined with that clear, "user first" is observably
      identical to legacy's "mask > saved-user": after applying a mask the
      user layer is empty, so the mask wins until the user edits again.
    * ``mask_elements`` (an applied mask's own layout, set by ``ApplyMask``
      from the mask's ``config1.dc``) override the theme so the mask's
      metric placement survives a theme swap.
    * ``theme_config["elements"]`` — the theme's bundled layout — is the
      default when neither override is present.

    Returns flat dicts (``OverlayElement.to_dict`` shape, which the theme's
    own elements already use) so every consumer — render, the DC writer,
    theme save/export — shares one definition of "what is on screen".
    """
    if user_elements:
        return [e.to_dict() for e in user_elements]
    if mask_elements is not None:
        return [e.to_dict() for e in mask_elements]
    return list(theme_config.get("elements") or [])


class OverlayService:
    """Compose text/metric overlays onto a base surface."""

    def __init__(self, renderer: Renderer) -> None:
        self._r = renderer

    @classmethod
    def render_dc_standalone(
        cls,
        *,
        renderer: Renderer,
        dc_path: Path,
        width: int,
        height: int,
        sensors: dict[str, float] | None = None,
        clock: dict[str, str] | None = None,
        temp_unit: str = "C",
    ) -> tuple[Any, int, dict[str, Any]]:
        """Render a DC config standalone — solid-black background.

        For CLI/API ``overlay`` previews of a DC file without disturbing
        an active device.  Reads ``config1.dc`` (or the directory's
        sibling file), creates a fresh black ``width × height`` surface,
        composites every parsed element onto it.

        Returns ``(image, element_count, parsed_dc)`` — the parsed DC
        dict is handed back so callers can inspect rotation /
        background_display / mask_position without re-parsing.

        ``sensors`` defaults to empty (metric elements render their
        format string with ``0.0``); ``clock`` defaults to ``None``
        (clock elements skipped — same behaviour as ``render`` when
        clock is missing).
        """
        dc_file = dc_path / _DC_CONFIG_FILE if dc_path.is_dir() else dc_path
        parsed = Dc.File(dc_file).read()
        config: dict[str, Any] = {
            "overlay_enabled": True,
            "elements": parsed.get("elements", []),
        }
        base = renderer.create_surface(width, height, color=(0, 0, 0))
        image = cls(renderer).render(
            base, config,
            sensors=sensors or {},
            clock=clock,
            temp_unit=temp_unit,
        )
        log.info(
            "render_dc_standalone: %s -> %dx%d, %d element(s)",
            dc_file, width, height, len(config["elements"]),
        )
        return image, len(config["elements"]), parsed

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
        log.info("calculate_mask_position: mask_dir=%s mask_size=%s lcd_size=%s",
                 mask_dir, mask_size, lcd_size)
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
        *,
        temp_unit: str = "C",
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

        ``config["elements"]`` is the ONE effective overlay layout the
        caller has already resolved (see ``resolve_overlay_elements``):
        exactly one source — the user's live edits, an applied mask, or the
        theme's bundled elements — REPLACES the others.  There is
        deliberately no separate user layer here; rendering theme + user as
        two stacked passes is what drew every edited element twice.

        ``clock`` is a pre-resolved ``{"time": "14:58", "date": ...,
        "weekday": ...}`` dict produced by DisplayService via
        ``services._clock.compute_clock``.  When ``None``, clock
        elements are skipped (e.g. test fixtures that don't care).

        ``temp_unit`` is "C" (default) or "F".  When "F",
        ``_draw_metric`` converts any temperature value
        (``metric_id.endswith(':temp')`` OR format-string contains
        ``°C``) from Celsius to Fahrenheit and swaps the unit symbol
        in the formatted output.  Sensor sources always deliver °C;
        the renderer is the single conversion site (SRP).
        """
        if not config.get("overlay_enabled", True):
            log.debug("render: overlay_enabled=False — returning base unchanged")
            return base

        # Start from a copy of the base surface
        width, height = self._r.surface_size(base)
        overlay = self._r.create_surface(width, height)

        elements: list[dict[str, Any]] = config.get("elements", [])
        clock_keys = list(clock.keys()) if clock else []
        log.debug(
            "render: %dx%d, elements=%d, sensors=%d, clock_sources=%s, "
            "temp_unit=%s",
            width, height, len(elements),
            len(sensors), clock_keys, temp_unit,
        )
        for idx, element in enumerate(elements):
            self._draw_element(overlay, element, sensors, clock or {},
                               source=f"element[{idx}]", temp_unit=temp_unit)

        return self._r.composite(base, overlay, position=(0, 0))

    # ── per-element dispatch ──────────────────────────────────────────

    def _draw_element(
        self,
        surface: Any,
        element: dict[str, Any],
        sensors: dict[str, float],
        clock: dict[str, str],
        *,
        source: str = "?",
        temp_unit: str = "C",
    ) -> None:
        kind = element.get("type")
        if kind == "text":
            self._draw_text(surface, element, source=source)
        elif kind == "metric":
            self._draw_metric(
                surface, element, sensors,
                source=source, temp_unit=temp_unit,
            )
        elif kind == "clock":
            self._draw_clock(surface, element, clock, source=source)
        else:
            log.warning("draw_element %s: unknown type %r — skipping (element=%r)",
                        source, kind, element)

    def _draw_text(
        self, surface: Any, element: dict[str, Any], *, source: str = "?",
    ) -> None:
        text = str(element.get("text", ""))
        if not text:
            log.debug("draw_text %s: empty text — skipping", source)
            return
        x = int(element.get("x", 0))
        y = int(element.get("y", 0))
        size = int(element.get("size", 16))
        log.debug("draw_text %s: %r at (%d, %d) size=%d", source, text, x, y, size)
        self._r.draw_text(
            surface,
            x=x, y=y, text=text,
            color=str(element.get("color", "#ffffff")),
            size=size,
            bold=bool(element.get("bold", False)),
            italic=bool(element.get("italic", False)),
        )

    def _draw_metric(
        self,
        surface: Any,
        element: dict[str, Any],
        sensors: dict[str, float],
        *,
        source: str = "?",
        temp_unit: str = "C",
    ) -> None:
        metric_id = str(element.get("metric", ""))
        value: float | None = sensors.get(metric_id)
        if value is None:
            log.warning(
                "draw_metric %s: metric %r has no sensor reading — skipping "
                "(available sensors: %d, sample keys=%s)",
                source, metric_id, len(sensors),
                list(sensors.keys())[:5],
            )
            return
        fmt = str(element.get("format", "{value}"))
        text = fmt.format(value=value)
        # Presentation-layer suffix swap only — the numeric value is
        # ALREADY converted by ``personalize_readings`` at the
        # broadcast / one-shot boundary (MetricsLoop / RenderAndSend /
        # LoadTheme / ReadSensors all route through it).  Renderer
        # just adjusts the unit symbol in the formatted text so the
        # theme's hardcoded ``"33°C"`` reads ``"33°F"`` when the
        # user picked °F.  No celsius_to_fahrenheit call here — that
        # would double-convert.
        if temp_unit == "F" and "°C" in text:
            text = text.replace("°C", "°F")
        x = int(element.get("x", 0))
        y = int(element.get("y", 0))
        log.debug("draw_metric %s: %s=%s at (%d, %d)",
                  source, metric_id, text, x, y)
        self._r.draw_text(
            surface,
            x=x, y=y, text=text,
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
        *,
        source: str = "?",
    ) -> None:
        clock_source = str(element.get("source", ""))
        text = clock.get(clock_source, "")
        if not text:
            log.warning(
                "draw_clock %s: source %r unresolved — skipping "
                "(clock dict keys: %s)",
                source, clock_source, list(clock.keys()),
            )
            return
        x = int(element.get("x", 0))
        y = int(element.get("y", 0))
        log.debug("draw_clock %s: %s=%r at (%d, %d)",
                  source, clock_source, text, x, y)
        self._r.draw_text(
            surface,
            x=x, y=y, text=text,
            color=str(element.get("color", "#ffffff")),
            size=int(element.get("size", 16)),
            bold=bool(element.get("bold", False)),
            italic=bool(element.get("italic", False)),
        )
