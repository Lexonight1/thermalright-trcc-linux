#!/usr/bin/env python3
"""Mock GUI — run the real next/ GUI rooted at ``dev/.trcc/``.

Mirrors ``src/trcc/ui/gui/__init__.py::launch`` step for step, with two
differences:

  * ``platform`` is a ``DevPlatform`` from ``_mock_bootstrap`` so paths
    point at ``dev/.trcc/`` / ``dev/.trcc-user/`` instead of ``~/.trcc``.
  * No ``SingleInstance`` lock + no IPC server — those collide with a
    real install running in parallel.

Real USB enumeration + real sensors run unchanged, so the GUI behaves
exactly like a packaged install would, just isolated to a throwaway
data root.  Bugs found here are real bugs.

Usage:
    PYTHONPATH=src python3 dev/mock_gui.py                # fleet from devices.json
    PYTHONPATH=src python3 dev/mock_gui.py --all          # simulate EVERY cooler
    PYTHONPATH=src python3 dev/mock_gui.py --list-devices # print the catalog + exit
    PYTHONPATH=src python3 dev/mock_gui.py --decorated    # native window chrome
    PYTHONPATH=src python3 dev/mock_gui.py -v             # DEBUG level (-vv = more)
    PYTHONPATH=src python3 dev/mock_gui.py --list         # available resolutions

  Reproduce a reported device (no hardware needed) — a trcc report records the
  vid:pid AND the handshake reply bytes (PM + sub_byte):
    PYTHONPATH=src python3 dev/mock_gui.py --report user_report.txt   # their exact fleet
    PYTHONPATH=src python3 dev/mock_gui.py device=0416:5302 pm=9 sub=2 # one device by hand
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, cast

# Bootstrap handles sys.path + paths + logging.  Import is intentionally
# before any trcc.* import so the dev paths override resolves first.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import (
    DEV_DATA,
    DEV_TRCC,
    DEVICES_JSON,
    bootstrap,
)

log = logging.getLogger("dev.mock_gui")


def _print_resolution_list() -> None:
    from trcc.core.protocol import FBL_PROFILES
    resolutions = sorted({p.resolution for p in FBL_PROFILES.values()},
                         key=lambda r: (r[0] * r[1], r[0]))
    print("Available resolutions:")
    for w, h in resolutions:
        print(f"  {w}x{h}")


def _print_device_catalog() -> None:
    """Every cooler model the app knows — copy a row into devices.json to sim it.

    Far more than the bare USB vid:pid count: one vid:pid fronts dozens of
    coolers told apart by the handshake PM/SUB.  ``pm`` (and ``sub`` when shown)
    are what you put in ``devices.json`` to pick that exact model.
    """
    from _mock_bootstrap import device_catalog
    rows = device_catalog()
    print(f"Device catalog — {len(rows)} cooler variants the app supports:\n")
    print(f"  {'MODEL':24} {'VID:PID':11} {'PM':>3} {'SUB':>4} RESOLUTION")
    for vids, pm, sub, model, (w, h) in sorted(rows, key=lambda r: r[3]):
        # Primary vid:pid is enough to simulate; bulk models alias across 4
        # (a '+N' marks them — see dev/README.md).
        vid, pid = vids[0]
        vid_str = f"{vid:04x}:{pid:04x}" + (f" +{len(vids) - 1}" if len(vids) > 1 else "")
        sub_str = "-" if sub is None else str(sub)
        print(f"  {model:24} {vid_str:11} {pm:>3} {sub_str:>4} {w}x{h}")
    print("\nTo simulate one: add {\"vid\":\"..\",\"pid\":\"..\",\"pm\":N} to "
          "dev/devices.json (copy dev/devices.json.example), then run "
          "`python dev/mock_gui.py`.  Or `--all` to load the whole catalog.")


def _print_help() -> None:
    print(__doc__ or "")


def _device_spec_from_token(token: str) -> dict:
    """``device=VID:PID`` → a one-device spec.  Reply bytes (pm/sub/fbl) are
    filled in by the following ``pm=`` / ``sub=`` / ``fbl=`` tokens."""
    if ':' not in token:
        print(f"Error: device={token} — expected VID:PID, e.g. device=0416:5302")
        sys.exit(1)
    vid, pid = token.split(':', 1)
    return {"vid": vid, "pid": pid, "name": f"reported device {vid}:{pid}"}


def _parse_args() -> tuple[bool, int, str | None, bool, dict | None]:
    decorated = False
    verbosity = 0
    report_path: str | None = None
    all_devices = False
    device_spec: dict | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith('-v'):
            verbosity = arg.count('v')
        elif arg in ('-h', '--help'):
            _print_help()
            sys.exit(0)
        elif arg == '--list':
            _print_resolution_list()
            sys.exit(0)
        elif arg == '--list-devices':
            _print_device_catalog()
            sys.exit(0)
        elif arg == '--all':
            all_devices = True
        elif arg == '--report':
            i += 1
            if i < len(args):
                report_path = args[i]
            else:
                print("Error: --report requires a file path")
                sys.exit(1)
        elif arg == '--decorated':
            decorated = True
        elif arg.startswith('device='):
            device_spec = _device_spec_from_token(arg.split('=', 1)[1])
        elif arg.startswith(('pm=', 'sub=', 'fbl=')):
            if device_spec is None:
                print(f"Error: {arg} needs a device first, "
                      f"e.g. device=0416:5302 {arg}")
                sys.exit(1)
            key, val = arg.split('=', 1)
            try:
                device_spec[key] = int(val, 0)   # accepts 9 or 0x09
            except ValueError:
                print(f"Error: {arg} — {val!r} is not a number")
                sys.exit(1)
        i += 1
    return decorated, verbosity, report_path, all_devices, device_spec


def main() -> None:
    decorated, verbosity, report_path, all_devices, device_spec = _parse_args()

    # Bootstrap: dev paths + rotating log at dev/.trcc/trcc.log.  This is the
    # ONLY thing the mock does differently — substitute the dev platform and
    # configure logging (the CLI root callback that does it for the shipping
    # app never runs here).  Everything else is the REAL composition so the
    # mock exercises real code paths and surfaces real bugs.
    platform = bootstrap(report_path=report_path, verbosity=verbosity,
                         all_devices=all_devices,
                         specs=[device_spec] if device_spec else None)

    print(f"\nConfig:  {DEV_TRCC / 'config.json'}")
    print(f"Data:    {DEV_DATA}")
    print(f"Devices: {DEVICES_JSON}")
    print("Close window or Ctrl+C to quit.\n")

    # Run the SAME composition root the shipping GUI runs, with the dev seams
    # off: no single-instance lock + no IPC server (would collide with a real
    # install), and return normally instead of os._exit.  on_ready mounts the
    # developer console once the window is built (guarded to a mock fleet).
    from trcc.ui.gui import run_gui

    def _on_ready(window: Any) -> None:
        _mount_dev_console(window)
        # A CLI ``device=VID:PID pm=… sub=…`` means "show me THIS device" —
        # auto-connect it (the dev mock boots blank otherwise; scan_devices
        # returns []).  Mirrors the dev variant panel's click path.
        if device_spec is not None:
            _auto_connect(window._app, device_spec)

    sys.exit(run_gui(
        cast(Any, platform), decorated=decorated,
        single_instance=False, ipc=False, force_exit=False,
        on_ready=_on_ready,
    ))


def _auto_connect(app: Any, spec: dict) -> None:
    """Connect a CLI ``device=`` spec immediately — the same way the dev
    variant panel does on a click: pin the handshake reply, then
    ``ConnectDevice`` (which self-attaches via ``find_product``).  So
    ``device=87ad:70db pm=11 sub=5`` presents that device instead of a blank
    boot."""
    platform = app.platform
    if not hasattr(platform, "set_active_reply"):
        log.warning("_auto_connect: platform has no set_active_reply — skip")
        return
    from trcc.core.commands import ConnectDevice
    from trcc.core.protocol import pm_to_fbl
    vid = int(str(spec["vid"]), 16)
    pid = int(str(spec["pid"]), 16)
    pm = int(spec.get("pm", 0))
    sub = int(spec.get("sub", 0))
    fbl = int(spec.get("fbl", pm_to_fbl(pm, sub)))
    key = f"{vid:04x}:{pid:04x}"
    platform.set_active_reply(vid, pid, pm=pm, sub=sub, fbl=fbl)
    result = app.dispatch(ConnectDevice(key=key))
    log.info("mock_gui._auto_connect: %s pm=%d sub=%d fbl=%d → ok=%s",
             key, pm, sub, fbl, getattr(result, "ok", None))


def _mount_dev_console(window: Any) -> None:
    """Build the dev variant panel + kick off the all-data prefetch.

    Dev behaves as if EVERY device is connected: all theme/mask/web data is
    downloaded once into dev/.trcc/data/ (bigger than the shipping app — normal
    for a dev build), and the variant panel lets you summon any of them.
    """
    import dev_console
    window._dev_console = dev_console.mount(window)
    dev_console.ensure_all_data(window._app)


if __name__ == '__main__':
    main()
