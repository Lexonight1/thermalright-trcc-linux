"""Preview-geometry resolution — toolkit-free, for the LCD device View.

The preview bezel/label dimensions for a device's active theme are pure
geometry: the DisplayService's composed canvas (portrait composition + user
orientation, #136) when a theme and profile are resolved, otherwise the
device's cached canvas swapped for the user orientation.  That decision lived
inline in ``LCDHandler._composed_preview_size``, untestable without a QWidget.

Lifting it here leaves the handler a thin View (call → poke the preview widget)
and makes the compose-vs-fallback rule unit-testable with no Qt — and routes
the fallback swap through the single ``oriented_resolution`` helper instead of a
hand-rolled one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ...core.models import oriented_resolution

if TYPE_CHECKING:
    from ...core.models import DeviceProfile, ProductInfo, Theme


class CanvasComposer(Protocol):
    """The one DisplayService method this resolver needs (ISP).

    Typing against the narrow shape — not the whole ``DisplayService`` — is
    what lets the rule be exercised with a trivial fake and no Qt/renderer.
    """

    def composed_canvas_size(
        self, info: ProductInfo, theme: Theme, profile: DeviceProfile | None,
        orientation: int,
    ) -> tuple[int, int]: ...


def composed_preview_size(
    display: CanvasComposer,
    *,
    info: ProductInfo | None,
    theme: Theme | None,
    profile: DeviceProfile | None,
    orientation: int,
    canvas_size: tuple[int, int],
) -> tuple[int, int]:
    """Preview bezel/label dims for the active theme (#136).

    When the device is connected (``info`` + ``profile``) AND a theme is
    loaded, defer to the DisplayService's composed canvas — it folds portrait
    composition and the user orientation exactly as the wire frame does, so the
    preview frame asset + label match the panel.  Otherwise (pre-handshake or
    no theme) fall back to the cached canvas swapped for the user orientation.
    """
    if info is not None and theme is not None and profile is not None:
        return display.composed_canvas_size(info, theme, profile, orientation)
    return oriented_resolution(canvas_size, orientation)
