"""LedZoneModel — toolkit-free zone/carousel interaction model for the LED panel.

The zone selection rules used to live in ``UCLedControl`` with the
``QPushButton`` checked-state AS the model: radio-select, carousel multi-select
with a "can't disable the last zone" guard, and the styles-2/7 "select all"
special case all read/wrote ``btn.isChecked()`` directly.

This lifts that logic into plain Python.  The model is the authority for
``enabled`` / ``selected`` / ``carousel``; the View mirrors ``enabled`` onto its
buttons after every mutation and emits the signal the model reports.  No Qt, so
the rules are unit-testable without a QApplication.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ZoneEmit:
    """What the View should emit after a model mutation.

    ``kind`` is ``"zone_selected"`` (radio pick → ``zone``),
    ``"carousel_zone"`` (multi-select toggle → ``zone`` + ``on``) or
    ``"carousel"`` (mode toggle → ``on``).  ``click_zone`` returns ``None``
    when nothing should be emitted (e.g. the last-zone guard fired).
    """
    kind: str
    zone: int = -1
    on: bool = False


class LedZoneModel:
    """Zone enabled-state + selection + carousel mode (no Qt)."""

    def __init__(self) -> None:
        self._zone_count = 1
        self._select_all_style = False
        self._enabled: list[bool] = [False]
        self._selected = 0
        self._carousel = False

    # ── Setup ─────────────────────────────────────────────────────────

    def configure(self, zone_count: int, select_all_style: bool) -> None:
        """Reset for a device: zone 0 selected, carousel off (matches the
        panel's ``initialize``: zone 0 checked, others off)."""
        self._zone_count = zone_count
        self._select_all_style = select_all_style
        self._selected = 0
        self._carousel = False
        self._enabled = [i == 0 for i in range(max(zone_count, 1))]

    def load_sync(self, carousel: bool, zones: list[bool]) -> None:
        """Restore carousel mode + per-zone enabled flags from saved config."""
        self._carousel = carousel
        for i in range(min(len(zones), self._zone_count)):
            self._enabled[i] = zones[i]

    # ── Queries ───────────────────────────────────────────────────────

    @property
    def zone_count(self) -> int:
        return self._zone_count

    @property
    def select_all_style(self) -> bool:
        return self._select_all_style

    @property
    def selected(self) -> int:
        return self._selected

    @property
    def carousel(self) -> bool:
        return self._carousel

    @property
    def enabled(self) -> list[bool]:
        """The persisted multi-select / carousel mask (one bool per zone)."""
        return list(self._enabled)

    @property
    def display_enabled(self) -> list[bool]:
        """What the View should show as checked.

        For a select-all style (PA120/LF10) the carousel toggle is an
        "edit every zone together" overlay, so every button reads active while
        it is on — without disturbing the persisted multi-select ``enabled``
        mask, so turning it off restores the zones the user had picked.
        """
        if self._select_all_style and self._carousel:
            return [True] * self._zone_count
        return list(self._enabled)

    # ── Interaction ───────────────────────────────────────────────────

    def click_zone(self, index: int) -> ZoneEmit | None:
        """Apply a zone-button click; return what the View should emit.

        - **select-all style (PA120/LF10)**: the zones are an independent
          multi-select — the colour applies to *every* selected zone (C#
          ``ucColor1Delegate`` gate ``nowLedStyle == 2 || 7``).  Toggle this
          zone in/out, refusing to disable the last.  While the "select all"
          overlay (carousel) is on, all zones are edited together so a click is
          ignored (turn it off to pick individually).
        - **page style + carousel on**: toggle this page in/out of the rotation.
        - **page style + carousel off**: radio-select this page.
        """
        if not 0 <= index < self._zone_count:
            log.debug("LedZoneModel.click_zone: index %s out of range", index)
            return None

        if self._select_all_style:
            if self._carousel:
                return None  # select-all overlay on — every zone edited together
            return self._toggle_zone(index)

        if self._carousel:
            return self._toggle_zone(index)

        # Page style, radio-select.
        self._selected = index
        self._enabled = [j == index for j in range(self._zone_count)]
        return ZoneEmit("zone_selected", index)

    def _toggle_zone(self, index: int) -> ZoneEmit | None:
        """Multi-select toggle with a can't-disable-the-last-zone guard."""
        if not self._enabled[index]:
            self._enabled[index] = True
            return ZoneEmit("carousel_zone", index, True)
        others = sum(1 for j in range(self._zone_count)
                     if j != index and self._enabled[j])
        if others > 0:
            self._enabled[index] = False
            return ZoneEmit("carousel_zone", index, False)
        # Last enabled zone — keep it on, emit nothing.
        self._enabled[index] = True
        return None

    def toggle_carousel(self, on: bool) -> ZoneEmit:
        """Toggle carousel / select-all mode."""
        self._carousel = on
        if self._select_all_style:
            # Select-all is a display/apply overlay (see ``display_enabled``):
            # it does NOT mutate the underlying multi-select mask, so turning it
            # off restores the zones the user had selected.
            pass
        elif not on:
            # Page style: circulate off → collapse to the single selected page.
            # (Circulate on leaves the current multi-select untouched.)
            self._enabled = [j == self._selected for j in range(self._zone_count)]
        return ZoneEmit("carousel", on=on)
