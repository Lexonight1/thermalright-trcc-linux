"""Toolkit-free description of a panel: a background, and controls at coordinates.

A gui panel *is* a background image plus controls at absolute rects — the
rects come from the Windows app's ``InitializeComponent()`` and already live
in ``gui/constants.py::Layout``.  What does not exist yet is the description
as data: today each panel's ``_setup_ui`` writes it out imperatively, and the
qtgui skin writes the same facts again, differently.  Two transcriptions of
one contract is how the skins drift.

This module is that description.  A skin *renders* a spec rather than
re-stating it: gui honours ``rect`` (it is a pixel-faithful port of the
Windows layout), a native skin can ignore ``rect`` and still learn which
controls exist and what each one is for.

**Qt-free by construction**, enforced by
``tests/test_architecture_boundaries.py::test_presentation_layer_is_qt_app_and_adapter_free``:

* assets are named by their ``Assets`` ATTRIBUTE (``"SENSOR_BTN"``), never
  loaded here — ``gui/assets.py`` imports ``QPixmap``, so importing it would
  drag Qt into this layer.  The renderer resolves the name.
* ``gui/constants.py`` imports nothing, so ``Layout`` rects, ``Colors`` and
  ``Sizes`` can be referenced directly by a spec author.

Deliberately NOT modelled: structural containers (scroll areas, inner content
widgets) and anything with bespoke Qt properties.  A spec that grows a field
per Qt setter is worse than the duplication it replaces — those stay as code,
and keeping them out is what stops this becoming a widget framework.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# (x, y, w, h) — the shape ``Layout`` already stores and ``setGeometry`` wants.
Rect = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Background:
    """The panel's backdrop image.

    ``fallback_style`` is the QSS used when the asset is missing — kept as an
    opaque string because it is pure presentation the renderer passes through
    untouched.
    """
    asset: str
    size: tuple[int, int] | None = None
    fallback_style: str = ""


@dataclass(frozen=True, slots=True)
class ImageButton:
    """A flat image button — the sidebar/tab control the Windows app uses."""
    id: str
    rect: Rect
    normal: str
    active: str
    checkable: bool = False
    fallback_text: str | None = None
    tooltip: str | None = None
    parent: str | None = None


@dataclass(frozen=True, slots=True)
class Label:
    """Static text at a rect.

    ``text`` is the ENGLISH source string, not a resolved one: panels re-run
    ``apply_language`` on a language change, so a spec holding translated text
    would freeze the panel in whatever locale built it.
    """
    id: str
    rect: Rect
    text: str = ""
    color: str = ""
    font_size: int = 10
    align_center: bool = True
    word_wrap: bool = False
    parent: str | None = None


Control = ImageButton | Label


@dataclass(frozen=True, slots=True)
class PanelSpec:
    """One panel: its backdrop and the controls sitting on it.

    ``controls`` is ordered — the renderer builds in sequence, so later
    controls stack above earlier ones, matching hand-written ``_setup_ui``.
    """
    background: Background | None = None
    controls: tuple[Control, ...] = field(default_factory=tuple)

    def control(self, control_id: str) -> Control:
        """Look a control up by id — for tests and for panels that need one."""
        for c in self.controls:
            if c.id == control_id:
                return c
        raise KeyError(f"no control {control_id!r} in spec")
