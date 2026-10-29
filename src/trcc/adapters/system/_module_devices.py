"""An installed module's USB devices, on Linux: letting this seat reach them.

A module names the USB devices it drives in its ``module.json``; the user
accepts them on its install screen.  ``trcc-module-devices`` (``assets/``,
packaged to /usr/bin) then writes one udev rule for the module -- built by the
helper from the ids alone -- and ``trcc-module-devices-off`` takes it back,
with no password, since it can only remove.
"""
from __future__ import annotations

from pathlib import Path

#: Must equal the helper's own constants (gated by a test: the helper may
#: import nothing from trcc).
RULES_DIR = Path("/etc/udev/rules.d")
RULE_GLOB = "70-trcc-module-*.rules"
HELPER = "/usr/bin/trcc-module-devices"
HELPER_OFF = "/usr/bin/trcc-module-devices-off"
#: The helpers as the package ships them -- what root runs when /usr/bin has none.
_ASSETS = Path(__file__).resolve().parents[2] / "assets"
HELPER_ASSET = _ASSETS / "trcc-module-devices"
HELPER_OFF_ASSET = _ASSETS / "trcc-module-devices-off"
