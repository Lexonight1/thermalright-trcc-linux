#!/usr/bin/env python
"""Diagnose tool — drive protocol tests from a ``trcc report`` file.

Usage
-----
    python tools/diagnose.py path/to/report.txt
    python tools/diagnose.py -       # read from stdin (paste report)

What it does
------------
1. Parses the ``trcc report`` text (sections: Version, Detected devices,
   Handshakes, Recent log).
2. Extracts: trcc version, OS, each device's VID:PID, protocol, PM byte,
   SUB byte, and resolution.
3. For each detected device, sets TRCC_DIAGNOSE_* env vars and runs pytest
   against the matching test file(s).
4. Reports which tests pass / fail — pinpoints the broken layer without
   needing real hardware.

Supported protocols
-------------------
    scsi  → tests/adapters/device/test_scsi.py
    bulk  → tests/adapters/device/test_bulk.py
    ly    → tests/adapters/device/test_ly.py
    hid   → tests/adapters/device/test_hid.py
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DeviceProfile:
    protocol: str
    vid: int
    pid: int
    pm: int = 0
    sub: int = 0
    width: int = 0
    height: int = 0
    path: str = ""


@dataclass
class ParsedReport:
    trcc_version: str = ""
    os_name: str = ""
    python_version: str = ""
    devices: list[DeviceProfile] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    ebusy_in_log: bool = False


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_PROTO_MAP = {
    "SCSI": "scsi",
    "BULK": "bulk",
    "LY":   "ly",
    "HID":  "hid",
    "LED":  "hid",
}

# Current reports print the wire lowercase (``wire=bulk``); LED shares the HID
# test surface, everything else maps to its own protocol key.
_WIRE_TO_PROTOCOL = {
    "scsi": "scsi",
    "bulk": "bulk",
    "hid":  "hid",
    "ly":   "ly",
    "led":  "hid",
}

_SCSI_TEST = {
    "linux":   "tests/adapters/device/test_scsi.py",
    "windows": "tests/adapters/device/windows/test_scsi.py",
    "macos":   "tests/adapters/device/macos/test_scsi.py",
    "bsd":     "tests/adapters/device/bsd/test_scsi.py",
}

_TEST_MAP_BASE = {
    "bulk": "tests/adapters/device/test_bulk.py",
    "ly":   "tests/adapters/device/test_ly.py",
    "hid":  "tests/adapters/device/test_hid.py",
}


def _platform(os_name: str) -> str:
    """Derive platform key from OS string in trcc report."""
    s = os_name.lower()
    if "windows" in s:
        return "windows"
    if "darwin" in s or "macos" in s or "mac os" in s:
        return "macos"
    if "freebsd" in s or "openbsd" in s or "netbsd" in s or "bsd" in s:
        return "bsd"
    return "linux"


def _test_map(os_name: str) -> dict[str, str]:
    return {**_TEST_MAP_BASE, "scsi": _SCSI_TEST[_platform(os_name)]}


def parse_report(text: str) -> ParsedReport:
    """Extract device profile(s) from ``trcc report`` text output.

    Handles both report layouts:

    * **Current** (2026+) — a ``## Devices (N)`` section with
      ``  <vid>:<pid>  <product>  wire=<wire>`` rows and a ``## Platform``
      key/value block (``system`` / ``python``). The handshake bytes live in
      the log tail.
    * **Legacy** — a ``[N] vid:pid Name (PROTO) path=…`` detected-devices
      section with ``trcc-linux:`` / ``OS:`` / ``Python:`` headers.

    The handshake block (``PM=… SUB=… resolution=(w, h)``) sits in the log
    tail in both layouts and is matched the same way.
    """
    report = ParsedReport()

    # Version — only legacy reports carry an explicit line.
    m = re.search(r"trcc-linux:\s+(\S+)", text)
    if m:
        report.trcc_version = m.group(1)

    # Python — current "## Platform" row "  python  3.14.5", else legacy line.
    m = re.search(r"^\s*python\s+(\S+)", text, re.MULTILINE) or \
        re.search(r"Python:\s+(\S+)", text)
    if m:
        report.python_version = m.group(1)

    # OS — current "## Platform" row "  system  Linux", else legacy "OS: …",
    # else recover it from the log tail ("building LinuxPlatform") so a report
    # that got clipped above the Platform section still resolves the OS.
    m = re.search(r"^\s*system\s+(\S+)", text, re.MULTILINE) or \
        re.search(r"OS:\s+(.+)", text) or \
        re.search(r"building (\w+)Platform", text)
    if m:
        report.os_name = m.group(1).strip()

    # Current layout: "## Devices" rows — "  87ad:70db  Product  wire=bulk".
    for m in re.finditer(
        r"^\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s+.*?\bwire=(\w+)\s*$",
        text,
        re.MULTILINE,
    ):
        vid = int(m.group(1), 16)
        pid = int(m.group(2), 16)
        proto = _WIRE_TO_PROTOCOL.get(m.group(3).lower(), m.group(3).lower())
        report.devices.append(DeviceProfile(protocol=proto, vid=vid, pid=pid))

    # Legacy fallback: "[N] vid:pid  Name  (PROTO)  path=..." — only if the
    # current-format pass found nothing, so old pasted reports still replay.
    if not report.devices:
        for m in re.finditer(
            r"\[\d+\]\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})(?![0-9a-fA-F])\s+.*?\((\w+)\).*?path=(\S+)",
            text,
        ):
            vid = int(m.group(1), 16)
            pid = int(m.group(2), 16)
            proto = _PROTO_MAP.get(m.group(3).upper(), m.group(3).lower())
            report.devices.append(
                DeviceProfile(protocol=proto, vid=vid, pid=pid, path=m.group(4))
            )

    # Log-tail fallback: reporters often paste a report clipped above the
    # "## Devices" section (GitHub truncates), but the scan line
    # "found 87ad:70db serial=..." survives in the log tail. Recover the
    # device id from there and infer the wire from its "<Wire>Lcd handshake OK"
    # line (protocol only matters to the pytest runner — the mock derives the
    # wire from the vid/pid registry regardless).
    if not report.devices:
        protos = re.findall(r"\b(Scsi|Bulk|Hid|Ly)Lcd handshake OK", text)
        seen: set[tuple[int, int]] = set()
        for m in re.finditer(
            r"found\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\b", text
        ):
            vid, pid = int(m.group(1), 16), int(m.group(2), 16)
            if (vid, pid) in seen:
                continue
            seen.add((vid, pid))
            idx = len(report.devices)
            proto = protos[idx].lower() if idx < len(protos) else ""
            report.devices.append(DeviceProfile(protocol=proto, vid=vid, pid=pid))

    # Handshake values — one PM/SUB/resolution block per device (in order)
    handshake_blocks = re.findall(
        r"PM=(\d+).*?SUB=(\d+).*?resolution=\((\d+),\s*(\d+)\)", text
    )
    for i, (pm, sub, w, h) in enumerate(handshake_blocks):
        if i < len(report.devices):
            report.devices[i].pm = int(pm)
            report.devices[i].sub = int(sub)
            report.devices[i].width = int(w)
            report.devices[i].height = int(h)

    # EBUSY / claim_interface anywhere in the log
    if re.search(r"EBUSY|claim_interface", text):
        report.ebusy_in_log = True
        report.log_tail = re.findall(r".*(EBUSY|claim_interface).*", text)

    return report


# ---------------------------------------------------------------------------
# Action-sequence replay — reconstruct the user's Command sequence
# ---------------------------------------------------------------------------
#
# Every ``app.dispatch(cmd)`` logs one INFO line — ``dispatch <Name>(<args>)`` —
# so a report's log tail is a complete, ordered trail of the user's actions
# through the ONE universal Command bus (the same path GUI / CLI / API share).
# Replaying that sequence on the mock reproduces the reporter's on-screen state
# and pinpoints a bug to the Command + dispatch layer, not a GUI-only slot.

# Replay reproduces on-screen DISPLAY STATE, so drop three kinds of command:
#   * connection — the harness re-attaches the device itself;
#   * per-frame — DEBUG, never in a default report, pure animation noise;
#   * network / meta / read-only queries — no display effect, and some
#     (update check, report generation) have side effects we don't want to
#     trigger while replaying.
_REPLAY_SKIP = frozenset({
    "ConnectDevice", "DiscoverDevices", "AttachDevice",
    "SendFrame", "RenderAndSend", "ReadSensors",
    "CheckForUpdate", "GenerateDebugReport",
    "ListGpus", "ListThemes", "ListMasks", "ListDevices", "GetState",
})


def _literal(node: ast.expr) -> Any:
    """Evaluate a logged argument node to a JSON-form value.

    Handles the shapes an ``App.dispatch`` repr produces: constants,
    ``Path``/``PosixPath``/``WindowsPath('…')`` (→ the path string, which the
    command builder re-coerces back to ``Path``), signed numbers, and
    list/tuple containers.  Anything else raises, so the whole call is skipped
    (e.g. an enum repr like ``<Orientation.X: 90>`` isn't even valid syntax).
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(e) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"Path", "PosixPath", "WindowsPath"}
            and node.args and isinstance(node.args[0], ast.Constant)):
        return node.args[0].value
    raise ValueError(f"unsupported argument node: {type(node).__name__}")


def parse_dispatch_sequence(text: str) -> list[dict[str, Any]]:
    """Extract the ordered user-action Command sequence from a report log tail.

    Returns one ``{"command": name, "kwargs": {...}}`` envelope per replayable
    action — exactly the shape ``trcc.ipc.decode_command`` consumes — in the
    order they were dispatched.  Connection / per-frame commands and any line
    whose arguments don't parse as literals are dropped.
    """
    seq: list[dict[str, Any]] = []
    for m in re.finditer(r"dispatch\s+(\w+)\((.*)\)\s*$", text, re.MULTILINE):
        name, argstr = m.group(1), m.group(2)
        if name in _REPLAY_SKIP:
            continue
        try:
            call = ast.parse(f"_({argstr})", mode="eval").body
            if not isinstance(call, ast.Call):
                continue
            kwargs = {kw.arg: _literal(kw.value)
                      for kw in call.keywords if kw.arg is not None}
        except (SyntaxError, ValueError):
            continue
        seq.append({"command": name, "kwargs": kwargs})
    return seq


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _run_tests(device: DeviceProfile, extra_markers: list[str], os_name: str = "") -> int:
    """Run pytest for the given device profile. Returns exit code."""
    test_file = _test_map(os_name).get(device.protocol)
    if not test_file:
        print(f"  [SKIP] No test file for protocol '{device.protocol}'")
        return 0

    env = os.environ.copy()
    env["TRCC_DIAGNOSE_VID"] = f"{device.vid:04X}"
    env["TRCC_DIAGNOSE_PID"] = f"{device.pid:04X}"
    env["TRCC_DIAGNOSE_PM"] = str(device.pm)
    env["TRCC_DIAGNOSE_SUB"] = str(device.sub)
    env["TRCC_DIAGNOSE_PROTOCOL"] = device.protocol
    env["TRCC_DIAGNOSE_PATH"] = device.path
    env["TRCC_DIAGNOSE_WIDTH"] = str(device.width)
    env["TRCC_DIAGNOSE_HEIGHT"] = str(device.height)
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")

    # Run only the diagnose-aware profile tests for this protocol
    p = device.protocol
    k_filters = [f"test_{p}_handshake_profile", f"test_{p}_send_frame_profile"]
    if "EBUSY" in extra_markers or p == "bulk":
        k_filters.append("test_bulk_open_ebusy_no_reset")

    k_expr = " or ".join(k_filters)

    cmd = [
        sys.executable, "-m", "pytest",
        str(_REPO_ROOT / test_file),
        "-k", k_expr,
        "-v", "--tb=short", "--no-header",
    ]

    print(f"\n  Running: {' '.join(cmd[-4:])}")
    print(f"  VID={device.vid:04X} PID={device.pid:04X} PM={device.pm} "
          f"SUB={device.sub} protocol={device.protocol}")
    print()

    result = subprocess.run(cmd, env=env, cwd=_REPO_ROOT)
    return result.returncode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    if path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(errors="replace")

    report = parse_report(text)

    print("=" * 60)
    print("TRCC Diagnose Tool")
    print("=" * 60)
    print(f"  trcc-linux : {report.trcc_version or '(not found)'}")
    print(f"  OS         : {report.os_name or '(not found)'}")
    print(f"  Python     : {report.python_version or '(not found)'}")
    print(f"  Devices    : {len(report.devices)}")
    if report.ebusy_in_log:
        print("  !! EBUSY detected in log — running claim_interface test")

    if not report.devices:
        print("\n[ERROR] No devices found in report. Check the 'Detected devices' section.")
        sys.exit(2)

    extra = ["EBUSY"] if report.ebusy_in_log else []
    failed = 0
    for i, device in enumerate(report.devices, 1):
        print(f"\n{'─' * 60}")
        print(f"Device {i}: {device.vid:04X}:{device.pid:04X} ({device.protocol.upper()})")
        rc = _run_tests(device, extra, report.os_name)
        if rc != 0:
            failed += 1

    print(f"\n{'=' * 60}")
    if failed:
        print(f"RESULT: {failed}/{len(report.devices)} device(s) FAILED — see output above")
        sys.exit(1)
    else:
        print(f"RESULT: All {len(report.devices)} device(s) PASSED")


if __name__ == "__main__":
    main()
