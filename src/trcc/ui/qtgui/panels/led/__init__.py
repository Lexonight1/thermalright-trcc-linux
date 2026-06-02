"""LED control sub-panels.

The legacy 1390-line ``uc_led_control.py`` had every LED concern stacked
in one widget — colour wheel, mode picker, zone editor, segment editor,
sensor toggles, clock/format prefs, live metrics overlay.  Reading it
top-to-bottom never quite tells you what's interesting about a given
device.

Next/ splits that into a small QTabWidget host (:class:`LedPanel`) and
one focused sub-panel per concern:

* :class:`ColorTab`    — global colour wheel + presets + brightness
* :class:`ModeTab`     — six LED effect modes
* :class:`ZoneTab`     — per-zone colour + sync / carousel
* :class:`SegmentTab`  — per-segment visibility toggles
* :class:`AdvancedTab` — sensor linkage, test mode, clock format

Each sub-panel binds to ``LedDeviceSettings`` for the active key,
dispatches one or two LED Commands, and listens to ``LedSettingsChanged``
to refresh when another UI changes the same device.
"""
from .advanced_tab import AdvancedTab
from .color_tab import ColorTab
from .mode_tab import ModeTab
from .segment_tab import SegmentTab
from .zone_tab import ZoneTab

__all__ = (
    "AdvancedTab",
    "ColorTab",
    "ModeTab",
    "SegmentTab",
    "ZoneTab",
)
