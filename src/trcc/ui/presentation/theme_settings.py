"""ThemeSettings — what the device Settings panel SENDS, for every GUI.

Measured on the gui's panel (2026-11-10): each of its 34 controls is a picture
of an App fact (a Query field) and, when touched, one Command -- sometimes after
a UI-only step, a file dialog or the eyedropper, that only produces the value.
A GUI is only a skin (his rule, 2026-11-10), so the Command half lives here,
once, with the few rules that ride on it; the gui and hudgui each draw their
own controls and call these.  No toolkit: plain ``pytest`` tests it.

The rules that used to live in one window and not the other:

* an edit to an element of a switched-off overlay switches it on -- unless the
  edit leaves no element (``LCDHandler.on_overlay_edit``);
* a loaded mask or theme sets the switch from its layout: on with elements, off
  without (``LCDHandler._adopt_loaded_overlay``, #276);
* the sound choice changed during a running cast re-issues that device's own
  cast, from the App's live region (``TRCCApp._on_screencast_audio_toggled``);
* the border flag is sent with the App's region (``TRCCApp._on_screencast_border_toggled``);
* an element dragged on the preview, or nudged, stays on the canvas
  (``TRCCApp._on_drag_*`` / ``_on_nudge``, copied once into hudgui).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

from ...core.commands import (
    ApplyMask,
    ClearInset,
    EnableOverlay,
    LcdSnapshot,
    RecentColors,
    RememberColor,
    ResolveOverlay,
    SetBackground,
    SetInset,
    SetMaskPosition,
    SetMaskVisible,
    SetMediaPlayer,
    SetScreencastRegion,
    SetTempUnit,
    ShowBackground,
    StartScreencast,
    StopScreencast,
    UploadCustomMask,
)
from ...core.commands._base import Command
from ...core.logs import per_frame
from ...core.ports import CommandBus
from ...core.results import Result

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

R = TypeVar("R", bound=Result)
#: One colour, as the App keeps it.
Rgb = tuple[int, int, int]


class Edited(NamedTuple):
    """An element edit's answer, and whether the overlay is on after it."""
    result: Result
    overlay_on: bool


class ThemeSettings:
    """Every Command the device Settings panel sends, over the bus alone.

    Holds no device state: each method takes the device key and asks the App
    for anything else it needs, so it works the same in-process and through a
    daemon, and two windows never disagree about a cached copy.
    """

    def __init__(self, bus: CommandBus) -> None:
        log.debug("ThemeSettings: built")
        self._bus = bus

    def _send(self, command: Command[R]) -> R:
        """Dispatch *command*; a refusal is logged by ``App.dispatch``."""
        log.debug("ThemeSettings._send: %r", command)
        return self._bus.dispatch(command)

    # ── The overlay ────────────────────────────────────────────────────

    def set_overlay(self, key: str, on: bool) -> Result:
        """The overlay switch."""
        log.info("ThemeSettings.set_overlay: %s %s", key, on)
        return self._send(EnableOverlay(key=key, enabled=on))

    def edit_element(self, key: str, edit: Callable[..., Command[Any]],
                     overlay_on: bool) -> Edited:
        """One element added, changed or deleted -- *edit* is the Command
        waiting only for the key.  Editing an element means wanting to see it,
        so a switched-off overlay (*overlay_on* False) is switched on -- but not
        by an edit that leaves no element, which would answer "remove
        everything" by turning the overlay on."""
        command = edit(key=key)
        log.info("ThemeSettings.edit_element: %r (overlay on: %s)", command, overlay_on)
        result = self._send(command)
        if not result.ok or overlay_on:
            return Edited(result, overlay_on)
        if not self._send(ResolveOverlay(key=key)).elements:
            log.debug("ThemeSettings.edit_element: no element left -- stays off")
            return Edited(result, False)
        self._send(EnableOverlay(key=key, enabled=True))
        return Edited(result, True)

    def follow_loaded_layout(self, key: str) -> bool:
        """A USER load (a theme click, a mask applied) copied its layout in:
        the switch follows it -- on with elements, off without (#276).  A
        reconnect never comes here; it shows the saved switch.  Returns it."""
        on = bool(self._send(ResolveOverlay(key=key)).elements)
        log.info("ThemeSettings.follow_loaded_layout: %s -> %s", key, on)
        self._send(EnableOverlay(key=key, enabled=on))
        return on

    # ── Colours and units ──────────────────────────────────────────────

    def remember_colour(self, key: str, rgb: Rgb) -> tuple[Rgb, ...] | None:
        """An edit ended on *rgb*: the App remembers it.  The recent row
        after, or None if refused."""
        log.info("ThemeSettings.remember_colour: %s %s", key, rgb)
        result = self._send(RememberColor(key=key, color=rgb))
        return tuple(result.colors) if result.ok else None

    def recent_colours(self, key: str) -> tuple[Rgb, ...] | None:
        """The App's recent-colour row for *key*, or None."""
        result = self._send(RecentColors(key=key))
        log.debug("ThemeSettings.recent_colours: %s ok=%s", key, result.ok)
        return tuple(result.colors) if result.ok else None

    def set_temp_unit(self, unit: str) -> Result:
        """°C or °F -- the App's one unit, for every panel."""
        log.info("ThemeSettings.set_temp_unit: %s", unit)
        return self._send(SetTempUnit(unit=unit))

    # ── The layer mask ─────────────────────────────────────────────────

    def set_mask_visible(self, key: str, visible: bool) -> Result:
        """The mask switch (and its eye -- the same fact)."""
        log.info("ThemeSettings.set_mask_visible: %s %s", key, visible)
        return self._send(SetMaskVisible(key=key, visible=visible))

    def set_mask_position(self, key: str, x: int, y: int) -> Result:
        """Where the mask sits."""
        log.info("ThemeSettings.set_mask_position: %s (%d, %d)", key, x, y)
        return self._send(SetMaskPosition(key=key, x=x, y=y))

    def apply_mask(self, key: str, path: Path, *, custom: bool) -> Result:
        """A mask onto the panel: one of the user's own (*custom*, an upload)
        or a downloaded one.  Applied, the overlay switch follows its layout."""
        log.info("ThemeSettings.apply_mask: %s %s custom=%s", key, path, custom)
        result = self._send(UploadCustomMask(key=key, source=path) if custom
                            else ApplyMask(key=key, path=path))
        if result.ok:
            self.follow_loaded_layout(key)
        return result

    # ── Background, box, media player ──────────────────────────────────

    def show_background(self, key: str, shown: bool) -> Result:
        """The background switch: one Command that also ends a cast or the
        media player, or switches the background off."""
        log.info("ThemeSettings.show_background: %s %s", key, shown)
        return self._send(ShowBackground(key=key, shown=shown))

    def set_background(self, key: str, path: Path) -> Result:
        """A picture or a video becomes the background."""
        log.info("ThemeSettings.set_background: %s %s", key, path)
        return self._send(SetBackground(key=key, path=path))

    def set_inset(self, key: str, module: str, corner: str, size: str) -> Result:
        """A module's picture in a box over the theme -- or, with no module,
        the box gone."""
        log.info("ThemeSettings.set_inset: %s %r %s %s", key, module, corner, size)
        return self._send(SetInset(key=key, module=module, corner=corner, size=size)
                          if module else ClearInset(key=key))

    def play_media(self, key: str, uri: str) -> Result:
        """The media player plays a file or an address."""
        log.info("ThemeSettings.play_media: %s %s", key, uri)
        return self._send(SetMediaPlayer(key=key, uri=uri))

    def stop_media(self, key: str) -> Result:
        """The media player off: the theme's own picture again."""
        log.info("ThemeSettings.stop_media: %s", key)
        return self._send(SetMediaPlayer(key=key, uri=""))

    # ── Screencast ─────────────────────────────────────────────────────

    def start_cast(self, key: str, region: tuple[int, int, int, int],
                   audio: bool) -> Result:
        """Cast *region* (x, y, w, h), with the sound bars or not."""
        log.info("ThemeSettings.start_cast: %s %s audio=%s", key, region, audio)
        x, y, w, h = region
        return self._send(StartScreencast(key=key, x=x, y=y, w=w, h=h, audio=audio))

    def stop_cast(self, key: str) -> Result:
        """The cast ends."""
        log.info("ThemeSettings.stop_cast: %s", key)
        return self._send(StopScreencast(key=key))

    def set_cast_audio(self, key: str, on: bool) -> Result | None:
        """The sound bars on or off.  With *key*'s cast running it is that
        cast again with the new choice -- its own live region, from the App,
        never one another UI started elsewhere.  Without one, nothing is sent
        (None): the choice is the window's until the next start."""
        snapshot = self._send(LcdSnapshot(key=key))
        live = snapshot.screencast_region if snapshot.ok else None
        log.info("ThemeSettings.set_cast_audio: %s %s (live: %s)", key, on, live)
        if live is None:
            return None
        x, y, w, h, _audio = live
        return self.start_cast(key, (x, y, w, h), on)

    def set_region(self, key: str, region: tuple[int, int, int, int]) -> Result:
        """The region the App holds for *key*; a running cast follows it."""
        log.info("ThemeSettings.set_region: %s %s", key, region)
        x, y, w, h = region
        return self._send(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h))

    def set_hide_border(self, key: str, hide: bool) -> Result | None:
        """The capture border shown or hidden, sent with the App's region.
        None when the App holds no region yet."""
        snapshot = self._send(LcdSnapshot(key=key))
        rect = snapshot.screencast_rect if snapshot.ok else None
        log.info("ThemeSettings.set_hide_border: %s %s (region %s)", key, hide, rect)
        if rect is None:
            return None
        x, y, w, h = rect
        return self._send(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h,
                                              hide_border=hide))


# ── Moving an element on the preview ───────────────────────────────────


def _clamp(value: int, top: int) -> int:
    frame_log.debug("_clamp: %d to 0..%d", value, top)
    return max(0, min(value, top))


@dataclass(frozen=True, slots=True)
class ElementDrag:
    """An element held under the pointer: where the pointer went (from
    *origin*, where it was grabbed) moves it from *start*, kept on *canvas*."""
    origin: tuple[int, int]
    start: tuple[int, int]
    canvas: tuple[int, int]

    def to(self, x: int, y: int) -> tuple[int, int]:
        """Where the element goes with the pointer at (*x*, *y*)."""
        moved = (_clamp(self.start[0] + x - self.origin[0], self.canvas[0]),
                 _clamp(self.start[1] + y - self.origin[1], self.canvas[1]))
        frame_log.debug("ElementDrag.to: (%d, %d) -> %s", x, y, moved)
        return moved


def nudge(position: tuple[int, int], dx: int, dy: int,
          canvas: tuple[int, int]) -> tuple[int, int]:
    """*position* moved by (*dx*, *dy*), kept on *canvas* -- an arrow key."""
    moved = (_clamp(position[0] + dx, canvas[0]), _clamp(position[1] + dy, canvas[1]))
    log.debug("nudge: %s by (%d, %d) -> %s", position, dx, dy, moved)
    return moved
