"""Cover ``dev/tools/diagnose.py::parse_report`` against real report layouts.

The report-replay loop (``dev/mock_gui.py --report`` →
``dev/_mock_bootstrap.py::_specs_from_report`` → ``parse_report``) is only as
good as this parser: if it can't recover a reporter's device from the text they
paste into a GitHub issue, the mock boots nothing.  These fixtures are the two
shapes that actually turn up in issues:

* **Current** (2026+) — a ``## Devices`` section + ``## Platform`` block, with
  the handshake bytes in the log tail (issue #203 / #176 layout).
* **Truncated current** — GitHub clipped the paste above ``## Devices`` (what
  #203 actually pasted); only the log tail survives, so the device id must be
  recovered from the ``found <vid:pid>`` scan line.
* **Legacy** — the old ``[N] vid:pid (PROTO) path=…`` layout, which must still
  parse so historical reports keep replaying.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DEV_TOOLS = Path(__file__).resolve().parent.parent / "dev" / "tools"


@pytest.fixture(scope="module")
def parse_report():
    sys.path.insert(0, str(_DEV_TOOLS))
    try:
        from diagnose import parse_report as fn  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(_DEV_TOOLS))
    return fn


@pytest.fixture(scope="module")
def parse_dispatch_sequence():
    sys.path.insert(0, str(_DEV_TOOLS))
    try:
        from diagnose import (  # type: ignore[import-not-found]
            parse_dispatch_sequence as fn,
        )
    finally:
        sys.path.remove(str(_DEV_TOOLS))
    return fn


# A log tail with the shapes App.dispatch actually emits: str/int/bool kwargs,
# a PosixPath arg, connection + network + per-frame + query commands (all
# skipped), and an unparseable enum repr (skipped without aborting the rest).
_DISPATCH_LOG = """\
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch ConnectDevice(key='87ad:70db')
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch CheckForUpdate()
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch ListThemes(resolution=(854, 480), directory=None)
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch SetBrightness(key='87ad:70db', percent=100)
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch SetOrientation(key='87ad:70db', degrees=180)
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch LoadTheme(key='87ad:70db', path=PosixPath('/home/x/.trcc/data/theme854480/Theme5'), reset_overrides=True)
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch SetSplitMode(key='87ad:70db', mode=0)
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch SendFrame(key='87ad:70db')
  2026-06-28 INFO trcc.app:App.dispatch:599: dispatch SetOrientation(key='87ad:70db', orient=<Orientation.PORTRAIT: 90>)
"""


# The current ``trcc report`` layout — device in its own section, handshake in
# the log tail (mirrors debug_report.py::_render_devices + _render_kv).
_CURRENT = """\
## Platform
  distro              Fedora Linux 44 (KDE Plasma Desktop Edition)
  install_method      pip
  python              3.14.5
  system              Linux
  release             7.0.12-201.fc44.x86_64

## Devices (1)
  87ad:70db       GrandVision 360 AIO             wire=bulk

## Log tail (1000 lines)
  2026-06-28T13:20:39 INFO    trcc.adapters.system.linux:LinuxPlatform.scan_devices:401:   found 87ad:70db serial='110702190133e003'
  2026-06-28T13:20:39 INFO    trcc.adapters.device.bulk_lcd:BulkLcd.connect:165: BulkLcd handshake OK: PM=11 SUB=5 resolution=(854, 480) (JPEG)
"""

# The same report clipped above "## Devices" — only the log tail is left.
_TRUNCATED = """\
  gpu:primary:temp                    27.00 °C  (temperature)

## Log tail (1000 lines)
  2026-06-28T13:20:39 INFO    trcc.adapters.system:PlatformFactory.current:63: PlatformFactory.current: building LinuxPlatform
  2026-06-28T13:20:39 INFO    trcc.adapters.system.linux:LinuxPlatform.scan_devices:401:   found 87ad:70db serial='110702190133e003'
  2026-06-28T13:20:39 INFO    trcc.adapters.device.bulk_lcd:BulkLcd.connect:165: BulkLcd handshake OK: PM=11 SUB=5 resolution=(854, 480) (JPEG)
"""

# The pre-2026 layout the parser must still accept.
_LEGACY = """\
trcc-linux:    9.6.5
OS:            Linux 6.8.0
Python:        3.12.3

Detected devices:
  [0] 87ad:70db  GrandVision 360 AIO  (BULK)  path=/dev/bus/usb/003/004

Handshakes:
  BulkLcd handshake OK: PM=11 SUB=5 resolution=(854, 480)
"""


def test_current_layout_extracts_device_and_handshake(parse_report):
    report = parse_report(_CURRENT)
    assert report.os_name == "Linux"
    assert report.python_version == "3.14.5"
    assert len(report.devices) == 1
    dev = report.devices[0]
    assert (dev.vid, dev.pid) == (0x87AD, 0x70DB)
    assert dev.protocol == "bulk"
    assert (dev.pm, dev.sub) == (11, 5)
    assert (dev.width, dev.height) == (854, 480)


def test_truncated_report_recovers_device_from_log_tail(parse_report):
    # GitHub clipped the "## Devices" section — the scan line in the log tail is
    # the only surviving device id, and the handshake still supplies PM/SUB/res.
    report = parse_report(_TRUNCATED)
    assert report.os_name == "Linux"          # recovered from "building LinuxPlatform"
    assert len(report.devices) == 1
    dev = report.devices[0]
    assert (dev.vid, dev.pid) == (0x87AD, 0x70DB)
    assert dev.protocol == "bulk"             # inferred from "BulkLcd handshake OK"
    assert (dev.pm, dev.sub) == (11, 5)
    assert (dev.width, dev.height) == (854, 480)


_LED_REPORT = """\
## Platform
  system              Linux

## Devices (1)
  0416:8001       Assassin 120 Digital            wire=led

## Log tail (1000 lines)
  2026-06-30 INFO trcc.adapters.device.led:Led.connect:252:   found 0416:8001 serial='z'
  2026-06-30 INFO trcc.adapters.device.led:Led.connect:252: Led handshake OK: PM=16 SUB=16 style=pa120 model=PA120_DIGITAL
"""


def test_led_handshake_without_resolution_extracts_pm_sub(parse_report):
    # LED / segment coolers log "handshake OK: PM=N SUB=M style=… model=…" with
    # no "resolution=(w, h)" — PM/SUB must still be recovered (they are the
    # whole fingerprint; width/height stay 0 and the model resolves from PM).
    report = parse_report(_LED_REPORT)
    assert len(report.devices) == 1
    dev = report.devices[0]
    assert (dev.vid, dev.pid) == (0x0416, 0x8001)
    assert dev.protocol == "hid"          # led → hid test surface
    assert (dev.pm, dev.sub) == (16, 16)
    assert (dev.width, dev.height) == (0, 0)


def test_variant_override_line_is_not_mistaken_for_handshake(parse_report):
    # "variant override PM=… SUB=…" carries the same bytes but no resolution —
    # only the real "handshake OK:" line should set the device's PM/SUB.
    text = _CURRENT + (
        "  2026 INFO trcc.core.commands.device:ConnectDevice.execute:188: "
        "ConnectDevice 87ad:70db: variant override PM=99 SUB=99 → A1LF19\n"
    )
    report = parse_report(text)
    # PM/SUB come from the handshake line (11/5), not the override line (99/99).
    assert (report.devices[0].pm, report.devices[0].sub) == (11, 5)


def test_legacy_layout_still_parses(parse_report):
    report = parse_report(_LEGACY)
    assert report.trcc_version == "9.6.5"
    assert report.os_name == "Linux 6.8.0"
    assert report.python_version == "3.12.3"
    assert len(report.devices) == 1
    dev = report.devices[0]
    assert (dev.vid, dev.pid) == (0x87AD, 0x70DB)
    assert dev.protocol == "bulk"
    assert dev.path == "/dev/bus/usb/003/004"
    assert (dev.pm, dev.sub) == (11, 5)
    assert (dev.width, dev.height) == (854, 480)


def test_log_tail_fallback_dedupes_repeated_scan_lines(parse_report):
    # scan_devices runs several times per session, so "found 87ad:70db" repeats;
    # the device must appear exactly once, not once per scan.
    text = _TRUNCATED + (
        "  found 87ad:70db serial='x'\n"
        "  found 87ad:70db serial='x'\n"
    )
    report = parse_report(text)
    assert len(report.devices) == 1


def test_dispatch_sequence_reconstructs_display_actions(parse_dispatch_sequence):
    seq = parse_dispatch_sequence(_DISPATCH_LOG)
    # Connection, network, query, per-frame, and the enum-repr line are dropped;
    # the four display-state actions remain, in order.
    names = [e["command"] for e in seq]
    assert names == [
        "SetBrightness", "SetOrientation", "LoadTheme", "SetSplitMode",
    ]


def test_dispatch_sequence_preserves_kwargs_and_paths(parse_dispatch_sequence):
    seq = parse_dispatch_sequence(_DISPATCH_LOG)
    by_name = {e["command"]: e["kwargs"] for e in seq}
    assert by_name["SetOrientation"] == {"key": "87ad:70db", "degrees": 180}
    # PosixPath(...) collapses to the string the command builder re-coerces.
    assert by_name["LoadTheme"] == {
        "key": "87ad:70db",
        "path": "/home/x/.trcc/data/theme854480/Theme5",
        "reset_overrides": True,
    }


def test_dispatch_envelopes_decode_to_real_commands(parse_dispatch_sequence):
    # The envelopes must be exactly what trcc.ipc.decode_command consumes —
    # prove the whole replay contract, not just the parse.
    import trcc.ipc as ipc
    from trcc.core.commands import SetOrientation

    seq = parse_dispatch_sequence(_DISPATCH_LOG)
    orient_env = next(e for e in seq if e["command"] == "SetOrientation")
    cmd = ipc.decode_command(orient_env)
    assert isinstance(cmd, SetOrientation)
    assert cmd.key == "87ad:70db"
    assert cmd.degrees == 180
