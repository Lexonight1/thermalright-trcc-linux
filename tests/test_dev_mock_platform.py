"""The dev mock fleet must survive a change to the transport-opener port.

``dev/_mock_bootstrap._build_dev_platform`` overrides ONLY the USB seam --
``scan_devices`` plus the scripted ``_open_scsi`` / ``_open_bulk`` -- and
inherits ``BaseOS.open_transport`` and its Wire->opener table from the real host
platform.  That inheritance is the whole value of the dev mock (it exercises the
production dispatch rather than a copy of it) and it is also the exposure:
``open_transport`` calls ``opener(vid, pid, serial, unit)``, so an opener whose
signature drifts from the port is a ``TypeError`` on **every** connect.

It drifted.  #287 step 4 (``10c383c1``) added ``unit`` to the openers, taught
``tests/mock_platform.py`` about it, and missed these two.  Every dev harness --
``mock_gui``, ``mock_cli``, ``mock_api``, ``mock`` -- died on ``ConnectDevice``
with ``_open_scsi() takes from 3 to 4 positional arguments but 5 were given``,
while the suite stayed green at 27,161, because **no test had ever built a
``DevMockPlatform``**.  The tests import helper FUNCTIONS from that module
(``variant_resolution``, ``NO_PANEL``, ``_specs_from_report``) and nothing else,
so the class the harnesses actually run on was unmeasured.

That matters more than a broken script: ``dev/mock_gui.py`` is the tool
``CLAUDE.md`` makes mandatory after every refactor ("run the real app and
``dev/mock_gui.py``"), and the multi-device mock is how #136 / #137 / widescreen
panels are verified without hardware.  A silently dead harness is a verification
step that reports success by not running.

These DRIVE the seam rather than inspect it -- ``open_transport`` is called the
way the app calls it, so any future parameter the port gains is caught by the
same TypeError, with no list here to keep in sync.

MUTATION CHECK -- drop ``unit`` from either scripted opener in
``dev/_mock_bootstrap.py`` and the matching test must fail with that TypeError.
Both were confirmed to fail before this file was committed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.models import Wire
from trcc.core.ports import Transport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
from _mock_bootstrap import _build_dev_platform

#: One SCSI panel and one bulk panel -- the two scripted openers.  Kept here
#: rather than read from ``dev/devices.json``, which is local and gitignored:
#: a gate that depends on an untracked file passes by skipping on CI.
_SCSI = {"type": "lcd", "name": "scsi panel", "vid": "0402", "pid": "3922",
         "pm": 32, "fbl": 100, "resolution": "320x320"}
_BULK = {"type": "lcd", "name": "bulk panel", "vid": "87ad", "pid": "70db",
         "pm": 11, "sub": 5, "resolution": "854x480"}


@pytest.mark.parametrize(
    ("spec", "wire"),
    [(_SCSI, Wire.SCSI), (_BULK, Wire.BULK)],
    ids=("scsi", "bulk"),
)
def test_the_dev_mock_opens_a_transport_the_way_the_app_does(
    spec: dict, wire: Wire,
) -> None:
    """``App.attach`` reaches the scripted opener through this exact call."""
    platform = _build_dev_platform([spec])
    vid, pid = int(spec["vid"], 16), int(spec["pid"], 16)

    transport = platform.open_transport(wire, vid, pid, None, "")

    assert isinstance(transport, Transport), (
        f"the dev mock's {wire.value} opener did not hand back a Transport — "
        f"every dev harness connects through this call"
    )


def test_the_dev_mock_opens_a_transport_with_the_ports_defaults() -> None:
    """The same call with the optional arguments omitted.

    ``App.attach`` passes all four today, but the port declares ``serial`` and
    ``unit`` optional, and a scripted opener that only works when they are
    supplied would be a mock that is stricter than the thing it stands in for.
    """
    platform = _build_dev_platform([_SCSI])

    transport = platform.open_transport(Wire.SCSI, 0x0402, 0x3922)

    assert isinstance(transport, Transport)
