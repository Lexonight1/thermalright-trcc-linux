"""``LedSnapshot`` must populate what the LED tabs now read.

The six tabs take a ``LedSnapshotResult`` instead of a live
``LedDeviceSettings`` — reaching ``app.settings.for_led`` raised under
``TRCC_DAEMON=1`` and handed a UI a mutable domain object.

**The widget-level tests live in ``test_gui_panels.py``** (seven of them, one
per tab, converted onto the Result in the same change).  They are NOT repeated
here.  What is here is the half nothing covered: that the Command actually
copies the four fields the tabs depend on.  A field the Command forgets is a
field the panel silently renders as its default, and the tabs can no longer
reach ``settings`` to notice the difference.

Worth knowing why the mode field is spelled as a NAME and not the enum: the
two consumers of it fail in different ways without the ``LEDMode[...]``
conversion — ``zone_tab.set_mode`` does ``findData(int(mode))`` and raises
``ValueError`` on ``"STATIC"``, while ``mode_tab`` does ``_radios.get(mode)``
on a ``dict[LEDMode, ...]`` and silently checks NOTHING.
``test_led_mode_tab_selects_radio_for_persisted_mode`` in ``test_gui_panels``
is the guard for the silent one; it was verified by mutation.
"""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import LedSnapshot


def test_the_four_added_fields_survive_the_round_trip(fake_platform) -> None:
    """Segment mask + the LC1/LF11/LC2 readout preferences reach the Result."""
    app = App(fake_platform)
    key = "0416:8001"
    settings = app.settings.for_led(key)
    settings.segment_on = [True, False, True]
    settings.clock_24h = False
    settings.week_sunday = True
    settings.memory_ratio = 4

    r = app.dispatch(LedSnapshot(key=key))

    assert r.segment_on == (True, False, True), "the mask, not a bool"
    assert r.clock_24h is False
    assert r.week_sunday is True
    assert r.memory_ratio == 4
    assert r.segment_count == 3, "segment_count must stay len(segment_on)"
