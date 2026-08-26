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
from ..core.logs import per_frame
from ..core.models import OverlayElement, ThemeDir
from ..core.ports import Renderer
from . import _dc as Dc
from ._clock import is_default_date_pattern, resolve_clock

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)



# Unit suffixes stripped off a metric value before drawing — the Windows app
# (TRCC.cs) removes ℃/℉/MHz/%/RPM from every sensor value and draws the bare
# integer, because the unit glyph is baked into the theme artwork, not the
# overlay.  Longest-first so " MHz" is removed before a bare "MHz" fragment.
_METRIC_UNITS = ("°C", "°F", "℃", "℉", " MHz", "MHz", " RPM", "RPM", "%")


def _strip_metric_unit(text: str) -> str:
    """Return ``text`` with any trailing metric unit removed (bare number)."""
    for unit in _METRIC_UNITS:
        text = text.replace(unit, "")
    return text.strip()


def resolve_overlay_elements(
    theme_config: dict[str, Any],
    user_elements: list[OverlayElement] | None,
) -> list[dict[str, Any]]:
    """The device's overlay layout — its WORKING layer, or the theme's.

    The C# keeps ONE array (``UCXiTongXianShiSubArray``, 2.1.6 FormCZTV.cs):
    a theme load or a mask apply reads straight into it, the editor shows it,
    the renderer draws it, the save writes it back.  The cutover split that
    into three stored sources — user edits, an applied mask's layout, the
    theme's bundled elements — and STACKED them at render time, so every
    element drew twice.  That was corrected to a precedence, and the
    precedence is now gone too: ``LoadTheme`` / ``ApplyMask`` / ``SaveTheme``
    ADOPT their source into the working layer, so there is only ever one.

    * ``user_elements`` — the working layer — is the answer whenever it
      exists.  ``None`` means the device has none of its own; ``[]`` means it
      has one and the user emptied it, which draws nothing.  Collapsing those
      two is what made a deleted last element reappear (#276).
    * ``theme_config["elements"]`` is the fallback for a null layer.  It is
      the net under the config schema v1→v2 migration: an upgrading user's
      layer reads as ``None`` until the first reconnect seeds it.  Strictly it
      cannot fire — ``app.active_themes`` is written only by ``LoadTheme``,
      which seeds the layer a few lines later, so "a theme to fall back to"
      and "a null layer" cannot both be true — but a blank overlay is the
      wrong thing to be wrong about, and one branch is a cheap net.

    Returns flat dicts (``OverlayElement.to_dict`` shape, which the theme's
    own elements already use) so every consumer — render, the DC writer,
    theme save/export — shares one definition of "what is on screen".
    """
    if user_elements is not None:
        return [e.to_dict() for e in user_elements]
    return list(theme_config.get("elements") or [])


def overlay_source(user_elements: list[OverlayElement] | None) -> str:
    """Name the layer ``resolve_overlay_elements`` returns for these inputs.

    The same precedence, reported instead of applied, so a log line, a
    Result field and a save manifest all name the winning layer identically
    rather than each restating the ternary.
    """
    source = "user" if user_elements is not None else "theme"
    log.debug("overlay_source: %s", source)
    return source


def effective_overlay_layout(
    theme_config: dict[str, Any],
    user_elements: list[OverlayElement] | None,
) -> list[dict[str, Any]]:
    """The effective layout with every element addressable by ``id``.

    :func:`resolve_overlay_elements` answers what DRAWS — the renderer needs
    no ids and runs this per frame.  This answers what can be ADDRESSED:
    flash, select, edit.  The two differ because a theme's own elements come
    from a ``config1.dc`` parse and carry no ``id`` at all — every shipped
    theme is in that state — so a UI offering to highlight "element 3" had
    nothing to name it by, and the Command that looked the name up never
    matched it.  User and mask layers are :class:`OverlayElement` objects and
    always carry a real id, which is why the bug only ever showed on an
    untouched theme.

    Minted ids are POSITIONAL (``el_{i}``), never random: a caller resolves
    the layout, hands an id back in a Command, and that Command re-resolves
    through here — the two answers have to agree.
    ``OverlayElement.from_dict`` mints a uuid, which would differ on the
    second call.  A name a real id already owns is skipped, so a mint can
    never shadow a genuine element.
    """
    elements = resolve_overlay_elements(theme_config, user_elements)
    taken = {str(e["id"]) for e in elements if e.get("id")}
    out: list[dict[str, Any]] = []
    minted = 0
    for i, element in enumerate(elements):
        entry = dict(element)
        if not entry.get("id"):
            candidate = f"el_{i}"
            n = i
            while candidate in taken:
                n += 1
                candidate = f"el_{n}"
            entry["id"] = candidate
            taken.add(candidate)
            minted += 1
        out.append(entry)
    log.debug(
        "effective_overlay_layout: %d element(s), %d id(s) minted",
        len(out), minted,
    )
    return out


def _element_family(element: dict[str, Any]) -> str:
    """The element's own font family, or "" for the renderer's theme default.

    Both parsers have always written it under ``name`` (``_dc.py`` for DC
    themes, ``theme.py`` for the JSON/legacy shape) and nothing ever read it,
    so every overlay drew in the theme default no matter what the DC stored or
    the user picked.  ``name`` is the established key -- the serializer round-
    trips it (``ui/presentation/overlay_serialization.py``) -- so it stays, and
    this is the one place that reads it.
    """
    family = str(element.get("name", ""))
    frame_log.debug("_element_family: %r", family)
    return family


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
        dc_file = ThemeDir(dc_path).dc if dc_path.is_dir() else dc_path
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
        dc_path = ThemeDir(mask_dir).dc
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
            family=_element_family(element),
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
        # ``show_unit`` mirrors the Windows unit-switch (myModeSub == 1): when
        # set, the unit glyph (°C / % / MHz / RPM) is drawn after the number;
        # otherwise the bare number is drawn because the unit is baked into the
        # theme art and drawing it here would double-print it (#150/#203).  89%
        # of shipped masks show the unit; the 001-series (baked glyph) do not.
        if bool(element.get("show_unit", True)):
            # The value is already unit-converted upstream by
            # ``personalize_readings`` (°F picked → value is Fahrenheit); swap
            # the hard-coded °C glyph so the label matches the global unit.
            if temp_unit.upper() == "F":
                text = text.replace("°C", "°F").replace("℃", "℉")
        else:
            text = _strip_metric_unit(text)
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
            family=_element_family(element),
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
        # Date format reconciliation (universal — every UI renders through here):
        #   * A DELIBERATE non-default theme pattern (e.g. "%m/%d") is honoured —
        #     a theme designed for a specific date layout keeps it.
        #   * A default-equivalent pattern ("%Y/%m/%d" ≡ "yyyy/MM/dd") means the
        #     theme didn't customise the date, so the user's global date_format
        #     pref wins (it's already baked into the precomputed dict, per
        #     device).  Time/weekday always use the dict (localised weekday, 12h
        #     handling).  ``"%" in fmt`` excludes the metric default "{value}".
        # (#format-prefs)
        elem_fmt = str(element.get("format", ""))
        if (clock_source == "date" and "%" in elem_fmt
                and not is_default_date_pattern(elem_fmt)):
            text = resolve_clock("date", date_format=elem_fmt)
        else:
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
            family=_element_family(element),
        )
