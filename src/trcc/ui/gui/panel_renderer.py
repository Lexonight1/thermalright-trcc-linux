"""Build a gui panel from a :class:`PanelSpec`.

The gui half of the split: the spec says *what* controls exist and where,
this says *how* this skin draws them.  It deliberately reuses the panel
primitives that already exist in ``gui/base.py`` — ``create_image_button``
and ``set_background_pixmap`` — rather than reimplementing widget
construction, so a spec-built panel goes through exactly the same code as
the hand-written one it replaces.  That is what makes a pixel-identical
comparison meaningful: if the bytes match, the only thing that changed is
where the description lives.

Asset NAMES are resolved here (``getattr(Assets, "SENSOR_BTN")``) because the
spec layer must not import ``assets.py`` — it pulls in Qt.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from ..presentation.panel_spec import (
    Background,
    IconButton,
    ImageButton,
    Label,
    PanelSpec,
)
from .assets import Assets
from .base import create_image_button, make_icon_button, set_background_pixmap

log = logging.getLogger(__name__)

# The exact stylesheet the hand-written panels emit for a spec Label.  Kept
# in one place and byte-identical to the strings it replaces — a stray space
# here is a changed render.
_LABEL_QSS = "color: {color}; font-size: {size}px; background: transparent;"


def _asset(name: str) -> str:
    """Resolve an ``Assets`` attribute name to its filename.

    A value that already looks like a filename passes straight through —
    the cut panels name their icons directly ("shared_close.png") rather
    than via an ``Assets`` attribute, and warning about those would be
    noise about correct code.
    """
    if "." in name:
        return name
    value = getattr(Assets, name, None)
    if value is None:
        log.warning("panel_renderer: no Assets.%s — control will fall back",
                    name)
        return name
    return value


def _noop() -> None:
    """Placeholder click handler.

    ``make_icon_button`` connects its handler unconditionally, and a spec
    carries no behaviour by design.  A control with no handler supplied gets
    this, so the panel can connect its own afterwards.
    """


def _logged(
    panel: str, control_id: str, handler: Callable[[], None],
) -> Callable[[], None]:
    """Wrap a handler so activating the control logs WHICH control it was.

    A spec control has a stable id, so the log line comes for free and reads
    the same in every skin — where today each panel hand-writes its own, and
    the ones nobody wrote are the blind spots CLAUDE.md's logging rules exist
    to prevent ("a silent panel is a debugging blind spot").  One line per
    user action, at INFO: these are clicks, not per-frame ticks.
    """
    def fire() -> None:
        log.info("%s.%s activated", panel, control_id)
        handler()
    return fire


class RenderedPanel:
    """The widgets a spec produced, looked up by control id.

    Typed accessors rather than a bare ``dict[str, QWidget]``: panels call
    ``setChecked`` / ``setText`` on what they get back, and a dict of the
    base class would force a cast at every call site.  A wrong id raises
    here instead of surfacing as an ``AttributeError`` three frames later.
    """

    def __init__(self, widgets: dict[str, QWidget]) -> None:
        self._widgets = widgets

    def __contains__(self, control_id: str) -> bool:
        return control_id in self._widgets

    def widget(self, control_id: str) -> QWidget:
        try:
            return self._widgets[control_id]
        except KeyError:
            raise KeyError(
                f"no control {control_id!r} was rendered "
                f"(have: {sorted(self._widgets)})",
            ) from None

    def button(self, control_id: str) -> QPushButton:
        widget = self.widget(control_id)
        if not isinstance(widget, QPushButton):
            raise TypeError(
                f"control {control_id!r} is a {type(widget).__name__}, "
                f"not a button",
            )
        return widget

    def label(self, control_id: str) -> QLabel:
        widget = self.widget(control_id)
        if not isinstance(widget, QLabel):
            raise TypeError(
                f"control {control_id!r} is a {type(widget).__name__}, "
                f"not a label",
            )
        return widget


def render(
    panel: QWidget,
    spec: PanelSpec,
    containers: dict[str, QWidget] | None = None,
    handlers: dict[str, Callable[[], None]] | None = None,
) -> RenderedPanel:
    """Realise *spec* onto *panel*; return the built widgets by control id.

    ``containers`` maps a container id to an already-built parent widget, for
    controls that live inside a structural widget the spec does not model
    (a scroll area's content widget, say).  A control naming an unknown
    container is parented to the panel, with a warning — a silent reparent
    would move it somewhere plausible and wrong.
    """
    containers = containers or {}
    handlers = handlers or {}
    log.info("render: %s — bg=%s controls=%d containers=%s",
             type(panel).__name__,
             spec.background.asset if spec.background else None,
             len(spec.controls), sorted(containers))

    if spec.background is not None:
        bg = spec.background
        width, height = bg.size if bg.size else (None, None)
        set_background_pixmap(
            panel, _asset(bg.asset), width, height,
            fallback_style=bg.fallback_style,
        )

    built: dict[str, QWidget] = {}
    for control in spec.controls:
        parent = panel
        if control.parent is not None:
            parent = containers.get(control.parent, panel)
            if control.parent not in containers:
                log.warning(
                    "render: control %r wants container %r which was not "
                    "supplied — parenting to the panel instead",
                    control.id, control.parent,
                )
        built[control.id] = _build(
            control, parent, handlers, type(panel).__name__,
        )
        log.debug("render: built %s %s at %s",
                  type(control).__name__, control.id, control.rect)
    return RenderedPanel(built)


def _build(control: Any, parent: QWidget,
           handlers: dict[str, Callable[[], None]],
           panel_name: str) -> QWidget:
    if isinstance(control, ImageButton):
        widget = create_image_button(
            parent, *control.rect,
            _asset(control.normal), _asset(control.active),
            checkable=control.checkable,
            fallback_text=control.fallback_text,
        )
        if control.tooltip:
            widget.setToolTip(control.tooltip)
        return widget

    if isinstance(control, IconButton):
        handler = handlers.get(control.id)
        return make_icon_button(
            parent, control.rect, _asset(control.image), control.fallback,
            _logged(panel_name, control.id, handler) if handler else _noop,
        )

    if isinstance(control, Label):
        widget = QLabel(control.text, parent)
        widget.setGeometry(*control.rect)
        widget.setStyleSheet(
            _LABEL_QSS.format(color=control.color, size=control.font_size),
        )
        if control.align_center:
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        widget.setWordWrap(control.word_wrap)
        return widget

    raise TypeError(f"panel_renderer cannot build {type(control).__name__}")


_ = Background   # re-exported for spec authors importing from here
