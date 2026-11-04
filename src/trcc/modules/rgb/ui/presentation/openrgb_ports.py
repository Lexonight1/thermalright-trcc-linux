"""What a window says about OpenRGB driving the coolers -- no toolkit.

One ``OpenRgbPorts`` answer becomes the lines the RGB page and the CLI show,
in the same words: the DDP device to add in OpenRGB for each cooler (#160).
OpenRGB cannot find a cooler by itself, so these are the values to type.
"""
from __future__ import annotations

import logging

from trcc.modules.rgb.core.models import DDP_HOST
from trcc.modules.rgb.core.results import OpenRgbPortsResult
from trcc.modules.rgb.ui.presentation.rgb_page import Tr, same

log = logging.getLogger(__name__)

INTRO = ("OpenRGB can light your coolers: in OpenRGB, add a DDP device for "
         "each, with these values.  Its colours show while OpenRGB sends them.")
#: DDP arrived in OpenRGB 1.0; 0.9, what most distros ship, has none
#: (measured on its release_0.9 tag, 2026-11-04).
NEEDS = "This needs OpenRGB 1.0 or newer."
NO_COOLER = "No cooler is connected -- OpenRGB has none to light."
COOLER = "{name}: IP {host}, port {port}, {leds} LEDs"
NO_PORT = "{name}: no port for OpenRGB -- {error}"


def openrgb_port_lines(result: OpenRgbPortsResult, tr: Tr = same) -> tuple[str, ...]:
    """*result* as lines: the intro and the version it needs, then one line
    per cooler -- or that no cooler is connected."""
    log.debug("openrgb_port_lines: %d cooler(s)", len(result.coolers))
    if not result.coolers:
        return (tr(NO_COOLER),)
    return (tr(INTRO), tr(NEEDS), *(
        tr(NO_PORT).format(name=c.name, error=c.error) if c.error
        else tr(COOLER).format(name=c.name, host=DDP_HOST, port=c.port,
                               leds=c.led_count)
        for c in result.coolers))
